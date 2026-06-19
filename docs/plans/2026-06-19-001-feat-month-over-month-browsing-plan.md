---
title: "feat: Month-over-month browsing with a month picker"
type: feat
date: 2026-06-19
status: ready
depth: standard
---

# feat: Month-over-month browsing with a month picker

## Summary

Add a header **month picker** that switches the view between any month already synced into the local DB, defaulting to the newest. **Sync** keeps pulling the newest "Who is hiring?" thread and now switches the view to it — so pulling in July (in a few weeks) is one click, while June stays browsable. Browsing stays scoped to the selected month, with one deliberate exception: the picker flags any month that has follow-ups coming due, so an application that matures after a rollover stays visible from the default view.

---

## Problem Frame

The data layer *already* does month-over-month storage. `threads` holds one row per monthly thread, `jobs.thread_id` ties each post to its month, and sync only ever upserts (`db.upsert_jobs`) — so syncing July adds rows without touching June, and your favorites/notes/statuses (`user_state`, keyed by HN comment id) survive any re-sync.

The single gap is **exposure**: `GET /api/jobs` is hardwired to `db.latest_thread()` (`app.py`). The instant a newer thread is synced, `latest_thread()` rolls forward by timestamp and the previous month silently disappears from the UI — even though its posts and all your triage state remain in `data.db`. You triage ~270 posts/month into an application pipeline; you need next month's thread to arrive without losing access to this month's.

This is a read/exposure feature, not a storage or migration feature. No schema change, no data migration.

---

## Requirements

- **R1** — View any month already in the DB, chosen from a header control; default to the newest month. *(the month picker)*
- **R2** — Pull in a not-yet-synced month (e.g. July) on demand; after pulling, the view shows that month and the picker lists it alongside earlier months. *(pull in July)*
- **R3** — Tabs, filters, search, the ↻ Follow-up chip, and zen mode all scope to the selected month. *(strictly per-month — your decision)*
- **R4** — `user_state` (favorites / notes / statuses) on any post survives switching months and re-syncing. *(Invariant 1 — do not break)*
- **R5** — `GET /api/jobs` with no `thread_id` keeps returning the newest month, so the initial page load and any existing callers are unchanged.
- **R6** — Surface a cross-month follow-up cue that is visible on the default view *without* opening the picker: each picker option shows its follow-up-due count, and an always-visible indicator near the picker reports the total due in non-selected months. *(the one deliberate carve-out from strictly-per-month — keeps post-rollover follow-ups discoverable)*

---

## Key Technical Decisions

- **KTD1 — Months are a first-class resource: add `GET /api/threads`.** Returns all threads newest-first with a per-month `job_count`, rather than embedding the month list inside the `/api/jobs` payload. Keeps `/api/jobs` a focused "jobs for one thread" endpoint and makes the month list independently inspectable by curl. This raises the app from four endpoints to five (update `CLAUDE.md`). Alternative considered below.

- **KTD2 — `/api/jobs` gains an optional `thread_id` query param.** Absent → newest month (R5, backward compatible). Present and known → that month. Present and unknown → `404`. Reuses the existing `db.list_jobs(conn, thread_id)` filter, which already supports this — only a `db.get_thread(conn, id)` lookup is new.

- **KTD3 — Sync stays server-side unchanged; the frontend does the switch.** `POST /api/sync` already auto-resolves the newest thread via Algolia and returns its `thread_id`. "Pull in July" needs no new sync endpoint — it is the existing Sync button once July's thread is live. The frontend uses the returned `thread_id` to select the new month and refresh the picker.

- **KTD4 — Strictly per-month scoping (your decision).** The picker scopes everything, including Applied and Follow-up. No cross-thread aggregation *except the single follow-up-due cue in KTD6*. The follow-up/Swiss-friendly derivations stay frontend-only over `state.jobs` (now the selected month) and need no logic change — only the data they run on narrows to one month.

- **KTD5 — Selection is in-memory; default newest on load.** No persistence across page reloads (a reload returns to the newest month). Remembering the last-viewed month is deferred (see Scope Boundaries).

- **KTD6 — One cross-month signal: the follow-up-due cue.** Strict per-month scoping (KTD4) would strand follow-ups that mature *after* a rollover — the 7-day window (`FOLLOWUP_DAYS`) is crossed only after the next thread lands and the view auto-switches away, so the very first rollover hides June's just-due items behind the default July view. The minimal fix that preserves per-month browsing everywhere else: `GET /api/threads` returns a `followup_due` count per month (computed server-side with the *same predicate* as the frontend `followupDue`), the picker badges months that have any, and a small always-visible indicator reports the total due in non-selected months. This is the **only** read that aggregates across threads — the in-view chip, tabs, and lists stay per-month. Consequence to track: the 7-day window is now defined in two places (frontend `FOLLOWUP_DAYS` and the server query); they must stay equal (see Risks).

---

## High-Level Technical Design

The two flows this plan introduces. Authoritative alongside the prose; not a sketch.

```mermaid
sequenceDiagram
  actor E as Evan
  participant UI as Browser (static/index.html)
  participant API as FastAPI (app.py)
  participant DB as SQLite (db.py)

  Note over E,DB: Flow A — switch to an earlier month
  E->>UI: pick "May 2026" in the month picker
  UI->>API: GET /api/jobs?thread_id=<may>
  API->>DB: get_thread(may) + list_jobs(may)
  DB-->>API: thread + jobs (May)
  API-->>UI: { thread, jobs }
  UI-->>E: render May posts (tabs/filters/follow-up scoped to May)

  Note over E,DB: Flow B — pull in a new month (July)
  E->>UI: click Sync
  UI->>API: POST /api/sync
  API->>DB: upsert_jobs(July thread + posts)
  API-->>UI: { thread_id: <july>, ... }
  UI->>API: GET /api/threads (refresh picker)
  UI->>API: GET /api/jobs?thread_id=<july>
  API-->>UI: months list + July jobs
  UI-->>E: picker lists July (selected); June still selectable
```

The picker scopes the read side only. Every write path (`PATCH /api/jobs/{id}`, `POST /api/jobs/{id}/apply`) is per-post by HN comment id and is untouched — which is what keeps Invariant 1 intact across month switches. The `GET /api/threads` response carries `job_count` and `followup_due` per month; the latter powers the cross-month follow-up cue (KTD6) and is the one read that aggregates across threads.

---

## Implementation Units

### U1. Backend: thread listing + month-scoped jobs query

- **Goal** — Expose the months that exist and let `/api/jobs` return any one of them.
- **Requirements** — R1, R2, R5, and the backend half of R6 (`followup_due` per month); enables R3.
- **Dependencies** — none.
- **Files** — `db.py`, `app.py`.
- **Approach** —
  - `db.py`: add `list_threads(conn)` returning every thread newest-first (`ORDER BY time DESC`) with two per-month counts: `job_count`, and `followup_due` — the number of posts whose `user_state` matches the *same predicate* the frontend `followupDue` uses (`status='applied'`, `applied_via='email'`, `applied_at` not null, `reminded_at` null, and `applied_at` older than the 7-day window). Compute via a `LEFT JOIN jobs` (for `job_count`) plus a `LEFT JOIN user_state` aggregated with a `CASE`/`SUM`, with the cutoff (`now − FOLLOWUP_DAYS·86400`) computed in Python and bound as a parameter. LEFT JOINs so a freshly-created thread with zero kept posts still appears (`job_count` 0, `followup_due` 0). Add `get_thread(conn, thread_id)` mirroring `latest_thread` but keyed by id, returning `None` when absent.
  - `app.py`: add `GET /api/threads` returning `db.list_threads(...)`. Extend `get_jobs` with `thread_id: int | None = None`; when provided, resolve via `db.get_thread` and raise `HTTPException(404, "unknown thread id")` if missing; when absent, keep `db.latest_thread`. Pass the resolved thread's id into `db.list_jobs`. (Two boundary notes: FastAPI binds `thread_id` as an optional query param, so a *non-integer* value returns its standard `422`, not the `404` — unreachable from the UI, which only sends listed ids. And `db.list_jobs`' existing truthy `if thread_id` filter is safe here because HN story ids are always large positive integers; no real path passes `0`.)
- **Patterns to follow** — match the existing `connect()/try/finally/close()` shape in `app.py` and the `dict(row)` / `sqlite3.Row` conventions in `db.py`. Keep the new endpoint in the same minimal style as `get_jobs`.
- **Verification scenarios** —
  - With two months synced, `GET /api/threads` returns a 2-element array, newest first, each with `job_count` matching that month's post count.
  - `GET /api/jobs?thread_id=<older>` returns the older month's posts and `thread.id == <older>`; the post count matches that month.
  - `GET /api/jobs` (no param) still returns the newest month (R5).
  - `GET /api/jobs?thread_id=999999` (unknown) returns `404`.
  - A favorite set on a post via `PATCH /api/jobs/{id}` is still present in that post's row after the above reads (Invariant 1 spot-check).
  - With an email application in an older month whose `applied_at` is >7 days old and `reminded_at` is null, `GET /api/threads` reports `followup_due ≥ 1` for that month; after a reminder is sent (`reminded_at` set), the same call reports one fewer. The count matches what the frontend `followupDue` would derive for that month's posts.

### U2. Frontend: month picker + per-month loading

- **Goal** — A header control that lists synced months and loads the chosen one; default newest.
- **Requirements** — R1, R3, and the frontend half of R6 (picker badge + "due elsewhere" indicator).
- **Dependencies** — U1.
- **Files** — `static/index.html`.
- **Approach** —
  - Extend `state` with `threads: []` and `threadId: null`.
  - Add a `<select id="month-select">` to the header `.bar`, placed between `#thread-meta` and the `search-wrap` so DOM order reads h1 → `#thread-meta` → `#month-select` → search → Zen → Sync (context controls before action controls; the bar already wraps on narrow viewports). Populate from `state.threads`; label each option from the HN title month — `(title.match(/\(([^)]+)\)/) || [])[1] || title` (the same parse `applyTemplate` already uses) — suffixed with its `job_count`. Three count-states: **empty** (fresh DB) → hide the select, `#thread-meta` shows "no data yet — hit Sync"; **exactly one month** → render the select disabled (still shows that month, no false implication of choice — this is the common case for weeks after each sync); **two or more** → enabled.
  - **Follow-up cue (R6 / KTD6):** suffix each option whose `followup_due > 0` with a marker (e.g. `June 2026 · 270 · ↻3`). Because a closed `<select>` shows only the selected option, also render a small always-visible indicator next to the picker — `↻ N due in other months` — where N sums `followup_due` over threads other than `state.threadId`; show it only when N > 0, styled with the existing `.chip.due` / `.status-pill.due` warning color. Make it clickable to jump to the most recent other month with `followup_due > 0`, turning the cue into a one-click path to the stranded items.
  - Rework `load(threadId)` to fetch the month list and the selected month's jobs together (`Promise.all` of `GET /api/threads` and `GET /api/jobs[?thread_id=]`), then set `state.threads`, `state.jobs`, `state.thread`, and `state.threadId` from the response, repopulate the picker, update `#thread-meta`, and `render()`. Fetching the list on every load keeps the picker correct after a sync without extra bookkeeping. `threadId` is **optional**: absent/null → newest month, so the existing page-load `load();` call and the R5 backward-compat path are unchanged. The month switch reuses the current synchronous `#list` re-render in `render()` — no loading spinner (consistent with the rest of the app, which has none, and sub-perceptible against a localhost server).
  - On picker `change`, call `load(+value)` and reset `state.zenIdx = 0` (different posts); leave `state.filters` and `state.tab` as the user's persistent lens across months. Clear `state.closed` so collapse state doesn't carry between months.
  - Keep `applyTemplate`'s month reference reading from `state.thread` — it now correctly reflects the *viewed* month (e.g. applying from June's view says "for June").
- **Patterns to follow** — existing delegated-listener style (`$("#...").addEventListener`), the `esc()` helper for any title text rendered into markup, and the existing `#thread-meta` update inside `load()`.
- **Verification scenarios** —
  - With two months synced, the picker lists both, newest selected by default; `#thread-meta` shows the selected month's count.
  - Selecting the other month swaps the post list, the tab counts, and the follow-up chip count to that month; search and filter chips keep their state.
  - A favorite toggled in June is still shown when switching June → other month → June (Invariant 1, UI side).
  - Zen mode (`z`) after switching months operates over the newly selected month's filtered list starting at position 1.
  - Fresh DB (no threads): picker is hidden/disabled and `#thread-meta` reads "no data yet — hit Sync".
  - With an email application due for follow-up in June while July is selected (the default), the `↻ N due in other months` indicator is visible and June's option carries its `↻` marker; clicking the indicator switches to June, where the item shows in the in-card follow-up chip and the indicator clears.
  - `node --check` passes on the extracted `<script>` block.

### U3. Frontend: Sync switches to the newly-pulled month

- **Goal** — After Sync, show the month that was just pulled and surface it in the picker — the "pull in July" moment.
- **Requirements** — R2.
- **Dependencies** — U2.
- **Files** — `static/index.html`.
- **Approach** — In `sync()`, read `thread_id` from the `POST /api/sync` JSON response and call `load(thread_id)` instead of the bare `load()`. Read `thread_id` inside the existing success branch (after the `r.ok` check); the `catch` path is unchanged — it only alerts and never calls `load()`, so a failed sync leaves the current month and picker selection intact (this is what the error-path verification scenario below asserts). Because `load` now also refreshes `state.threads`, the new month appears in the picker and is selected. No change to the Sync button's "always pull newest" behavior (the stated default): syncing while viewing an older month pulls the newest thread and switches to it.
- **Patterns to follow** — keep the existing `sync()` button-state handling (`disabled` / "Syncing…" / restore) and the `try/catch` `alert` on failure.
- **Verification scenarios** —
  - Re-syncing while the newest month is current refreshes that month in place and stays on it.
  - Syncing with a newer thread available (simulate by syncing a newer `thread_id` via the API) switches the view to it and adds it to the picker as the selected, newest option.
  - Syncing while viewing an older month pulls the newest and switches to it (matches the documented Sync = "get the current month" behavior).
  - A failed sync leaves the current month and picker selection unchanged (error path).

### U4. Docs: README + CLAUDE.md

- **Goal** — Keep the re-entry docs and Claude context accurate; give a way to exercise two months before July is live.
- **Requirements** — supports R1, R2 (discoverability); keeps project docs truthful.
- **Dependencies** — U1–U3.
- **Files** — `README.md`, `CLAUDE.md`.
- **Approach** —
  - `README.md`: note in "Coming back" / the monthly routine that old months stay browsable via the new picker and that Sync switches to the newest thread. Add a one-liner: to get a second month locally for testing before July's thread exists, sync a prior month by id (`curl -X POST 'localhost:8137/api/sync?thread_id=<older story id>'`) — the existing per-month sync path.
  - `CLAUDE.md`: update the architecture bullet that enumerates the endpoints — now five, with `GET /api/jobs` taking an optional `thread_id` and a new `GET /api/threads`. Note that month selection is frontend state and browsing is strictly per-month, with one deliberate exception: `GET /api/threads` returns a server-computed `followup_due` count per month (the *only* cross-thread aggregation) that powers the picker's cross-month follow-up cue. Flag that the 7-day follow-up window is now mirrored in `FOLLOWUP_DAYS` (frontend) and the `list_threads` query — keep them equal.
- **Verification scenarios** — `Test expectation: none — documentation only.` Confirm the endpoint count and the curl example match the implemented behavior from U1–U3.

---

## Scope Boundaries

**In scope:** the month picker; per-month browsing of any synced month; Sync switching to the pulled month; `GET /api/threads`; the optional `thread_id` on `/api/jobs`; the cross-month follow-up cue (per-month `followup_due` count + picker badge + "due elsewhere" indicator); README/CLAUDE.md updates.

**Out of scope (deliberate):**
- **Full cross-month pipeline** — a combined surface that *lists or filters* Applied / Interviewing / Follow-up items across all months at once. Out of scope: browsing stays strictly per-month. The one concession is a discovery *cue* (R6 / KTD6) — the picker flags months with follow-ups due — not a cross-month list. The cue covers follow-ups only; later-stage items (Applied / Interviewing / Offer) get no cross-month signal — revisit if those start slipping across rollovers.

### Deferred to Follow-Up Work
- **Remember last-viewed month** across reloads (e.g. `localStorage`). Default-newest is the chosen behavior for now.
- **"Re-sync this month" affordance** for refreshing an older month from its own view (today: the `?thread_id=` curl path covers it).
- **Pruning / deleting** old months from `data.db`.
- **Cross-month analytics / comparison** (e.g. counts over time).
- **Scheduled / automatic** monthly sync — Sync stays manual.

---

## Alternatives Considered

- **Embed the month list in the `/api/jobs` response** (`{ thread, threads, jobs }`) instead of a dedicated `GET /api/threads`. Saves one small fetch and slightly simplifies the frontend, but overloads the jobs endpoint and couples "what months exist" to "give me this month's posts." Rejected for endpoint clarity; the extra fetch is negligible at this scale. If the double round-trip ever matters, this is the cheap fallback.
- **Strictly per-month vs. cross-month pipeline** — resolved to per-month by your choice; recorded in KTD4 and Scope Boundaries.

---

## Risks & Dependencies

- **Algolia recency** — `hn.resolve_latest_thread_id` only sees the most recent threads from Algolia, so the auto-Sync path reliably pulls the *current* month. Pulling an arbitrarily old month relies on the existing `?thread_id=` param (unchanged). Low risk; it's how testing a second month works today.
- **Title-format dependency** — the picker label parses `"... (June 2026)"`. If a thread title ever lacks the parenthetical, the label falls back to the full title. Low; cosmetic.
- **Backward compatibility** — `GET /api/jobs` with no param, and every write endpoint (`PATCH`, `apply`), are unchanged. The only new failure mode is an unknown `thread_id` → `404`, which the frontend never sends (it only offers months it listed).
- **Follow-up window defined twice** — the 7-day threshold now lives in both the frontend `FOLLOWUP_DAYS` and the server-side `list_threads` predicate (KTD6). If they drift, the picker's cross-month badge and the in-card chip will disagree about what's "due." Low risk (single constant, same machine's clock), but change them together. The cue mitigates the rollover-stranding that strict per-month would otherwise cause; by deliberate scope, later-stage pipeline items remain per-month (Scope Boundaries).

---

## Verification Approach

Per project convention (no test suite): `curl` against the API, `node --check` on the extracted inline script, and a manual UI pass.

- **Two-month setup without waiting for July:** sync the current month (Sync), then `curl -X POST 'localhost:8137/api/sync?thread_id=<a prior month's story id>'` to land a second month in `data.db`. The picker should then show both.
- **Server:** the dev server usually runs in the background with `--reload` (`.venv/bin/uvicorn app:app --reload --port 8137`) — check before starting another (port conflict).
- **Invariant 1 throughout:** set a favorite / note / status in one month, switch away and back, confirm it persists; confirm a re-sync of that month leaves it intact.

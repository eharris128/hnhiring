# hnhiring — Claude context

Personal, single-user tracker for HN "Who is hiring?" threads, used to triage
~270 posts/month down to an application pipeline. Filters: remote roles, US-based
roles, and a configurable "home-friendly" region (the author's is Switzerland).
Everything personal — name, signature, website, pitches, home region, resume path,
follow-up window — lives in `profile.json` (gitignored; `profile.json.example` is the
template). Never hardcode any of it in the Python modules or `index.html`.

## Run / verify

```sh
with-keys .venv/bin/uvicorn app:app --reload --port 8137   # http://127.0.0.1:8137
```

No test suite. Verify changes with curl against the API and `node --check` on
the inline JS (extract the `<script>` block from `static/index.html`).

## Architecture (deliberately small — keep it that way)

- `app.py` — FastAPI. Six endpoints: `POST /api/sync` (upserts `jobs`, then runs
  `db.auto_skip_reapplied` and returns its count as `auto_skipped` — see below),
  `GET /api/jobs` (optional `?thread_id=` — defaults to the newest month, `404` on
  an unknown id), `GET /api/threads` (every synced month, newest first, each with
  `job_count` and a `followup_due` count — feeds the month picker), `GET
  /api/profile` (`profile.public()` — the frontend's slice of profile.json:
  signature, website, pitches, followup_days, home; never address/resume), `PATCH
  /api/jobs/{id}` (favorite/status/notes/applied_via only), and `POST
  /api/jobs/{id}/apply`, keyed by `kind`: `'application'` (default) emails the
  application via `mailer.py` (resume attached), then sets status=applied,
  applied_via=email, and appends an audit line to notes; `'reminder'` sends a
  follow-up nudge, appends a "↻ reminder sent" audit line to notes, and stamps
  `reminded_at`, leaving status/channel/date alone.
  `test: true` redirects either kind to profile.json's `test_address` with no state
  change. `applied_via` ('email' = needs follow-up | 'portal' | NULL) is also set
  to 'portal' by the frontend when `+` bumps interested→applied.
- `profile.py` — loads `profile.json` merged over `DEFAULTS` (cached by mtime, so
  edits apply on the next request; falls back to the example file when absent so
  the UI works on a fresh clone). Single source for the mailer, the classifier's
  home places, the follow-up window, and the frontend.
- `mailer.py` — Gmail SMTP (app password) with the resume PDF attached. Sender
  name/address, resume path and `test_address` come from the profile
  (`load_config` = profile validated for sending). The app password itself is NOT
  in that file — it comes from `$GMAIL_APP_PASSWORD`, injected from Bitwarden
  (`api/google-app-password`) by `with-keys`, so launch the server as
  `with-keys .venv/bin/uvicorn ...`. Env wins over a password in profile.json.
  Every inbox/interested/later post gets an "✉ Apply" button (compose modal,
  prefilled template; `a` key in zen mode); the To field is prefilled when an
  email is found in the post text, else left blank for manual entry (detection
  misses obfuscated forms sometimes — that's fine, the button still opens).
  Email detection is frontend-only (`emailsIn()` in index.html), nothing stored.
- `hn.py` — resolves newest thread via Algolia (`author_whoishiring`), fetches
  story + all `kids` from Firebase (`/v0/item/<id>.json`), 50 concurrent.
  Fallback thread id is hardcoded; sync accepts `?thread_id=` for old months.
- `classify.py` — regex heuristics → tags: `remote onsite us europe worldwide`
  plus `home`, built at classify time from `profile.home.places` (plain words,
  `re.escape`d, whole-word — the `\b` boundaries matter: "baseline" once matched
  `basel`; lru-cached per place list). "Home-friendly" is derived in the FRONTEND
  from the profile, not stored: `home OR (remote AND any(home.friendly_remote_tags))`.
  Changing `home.places` only re-tags on the next sync. `extract_company` pulls a
  normalized company name from the "Company | Role | ..." title convention
  (lowercased, legal suffix stripped; `None` if there's no `|` or the first segment
  looks like a role, not a company) — feeds the auto-skip match in `db.py`.
- `db.py` — SQLite (`data.db`, gitignored — personal data, local only).
  `auto_skip_reapplied` (run every sync) sets status='skip' on any untriaged job
  (no `user_state` row yet) whose `company` matches one the user already
  `rejected` from a `portal` application in a past month — exact-string match on
  the normalized `company` column, so it only catches literal re-postings, not
  renamed roles or near-duplicates. It never touches a job that's already been
  triaged.
- `static/index.html` — entire frontend: vanilla JS, no build step, ~1 file.
  Month selection is frontend state (`state.threadId`); browsing is **strictly
  per-month** (tabs, filters, search, the ↻ chip, zen all scope to the selected
  month). The *only* cross-thread signal is `followup_due` (server-computed in
  `db.list_threads`), surfaced per option in the month picker as `· ↻N` so a
  follow-up maturing after a month rolls over stays discoverable. The follow-up
  window is `followup_days` in profile.json — `db.list_threads` reads it via
  `profile.load()`, the frontend via `/api/profile` — one source of truth. The
  frontend fetches the profile inside `load()` (with threads + jobs) and applies
  it in `applyProfile()`: pitch dropdown, home chip label/visibility, ↻ chip title.

## Invariants — do not break

1. **`user_state` is sacred.** Sync upserts `jobs` directly; it only ever touches
   `user_state` through `auto_skip_reapplied`, and only to *initialize* rows that
   don't exist yet (untriaged posts) — it never modifies an existing row.
   Favorites, notes, and statuses set by the user must survive any re-sync,
   re-classify, or schema change.
2. **Statuses**: inbox → interested → applied → interviewing → offer /
   rejected / archived, plus `later` (parking lot; `l` in zen mode, `+`
   re-enters at interested) and `skip` (auto-set by `db.auto_skip_reapplied` on
   sync for re-postings from companies that already rejected a portal
   application; also manually selectable from the status dropdown). The `+`
   action follows `NEXT_STAGE` in index.html (no entry for `skip`, so those
   cards get no + button); `×` toggles archived ↔ inbox.
3. Post bodies are raw HN-sanitized HTML rendered via innerHTML; links in them
   get `target="_blank" rel="noopener"` applied post-render in `render()`.
4. Cards are expanded by default (`state.closed` tracks collapsed ones).
5. Zen mode (`z`) = one card at a time over the CURRENT filtered list, with
   keyboard triage (`+`/`x` auto-advance via `zenStep`). Keep new features
   working in both list and zen views — they share `cardHTML()`.
6. Status-changing actions (bump/archive/later/reject/dropdown) go through
   `patchTracked`, which stashes the overwritten fields in `state.lastChange` so
   Ctrl/Cmd+Z can revert the single most recent one (cleared on month switch —
   undo never crosses months). Apply is the one exception: `sendApply` is
   optimistic (flip the card, deliver in the background, roll back on failure),
   so it builds `state.lastChange` by hand instead of calling `patchTracked` —
   don't "simplify" it onto `patchTracked`. Any new status-changing action
   should call `patchTracked`, not `patch`, to stay undoable.

## Workflow notes

- Server usually already running in background with `--reload`; check before
  starting another (port conflict).
- When bulk-editing user state (e.g. "mark these N companies interested"),
  match against titles case-insensitively, surface ambiguous/missing matches
  for confirmation BEFORE patching, and use the API rather than raw SQL.
- GitHub: private repo `eharris128/hnhiring`, pushed over SSH (needs sandbox
  disabled for `git push`).

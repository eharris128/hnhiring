# hnhiring — Claude context

Personal (single-user: Evan) tracker for HN "Who is hiring?" threads. He uses it
to triage ~270 posts/month down to an application pipeline. Filters that matter
to him: remote roles, US-basis roles, roles compatible with his Swiss work permit.

## Run / verify

```sh
.venv/bin/uvicorn app:app --reload --port 8137   # http://127.0.0.1:8137
```

No test suite. Verify changes with curl against the API and `node --check` on
the inline JS (extract the `<script>` block from `static/index.html`).

## Architecture (deliberately small — keep it that way)

- `app.py` — FastAPI. Five endpoints: `POST /api/sync`, `GET /api/jobs`
  (optional `?thread_id=` — defaults to the newest month, `404` on an unknown id),
  `GET /api/threads` (every synced month, newest first, each with `job_count` and a
  `followup_due` count — feeds the month picker), `PATCH /api/jobs/{id}`
  (favorite/status/notes/applied_via only), and `POST /api/jobs/{id}/apply` — emails
  an application via `mailer.py` (resume attached), then sets status=applied,
  applied_via=email, and appends an audit line to notes. `applied_via` ('email' =
  needs follow-up | 'portal' | NULL) is also set to 'portal' by the frontend when
  `+` bumps interested→applied.
- `mailer.py` — Gmail SMTP (app password) with the resume PDF attached.
  Credentials in `mail.json` (gitignored; see `mail.json.example`). Posts whose
  text contains an email get an "✉ Apply" button (compose modal, prefilled
  template; `a` key in zen mode). Email detection is frontend-only
  (`emailsIn()` in index.html), nothing stored.
- `hn.py` — resolves newest thread via Algolia (`author_whoishiring`), fetches
  story + all `kids` from Firebase (`/v0/item/<id>.json`), 50 concurrent.
  Fallback thread id is hardcoded; sync accepts `?thread_id=` for old months.
- `classify.py` — regex heuristics → tags: `remote onsite us europe switzerland
  worldwide`. City regexes need `\b` boundaries (learned: "baseline" matched
  `basel`). "Swiss-friendly" is derived in the FRONTEND, not stored:
  `switzerland OR (remote AND (europe OR worldwide))`.
- `db.py` — SQLite (`data.db`, gitignored — personal data, local only).
- `static/index.html` — entire frontend: vanilla JS, no build step, ~1 file.
  Month selection is frontend state (`state.threadId`); browsing is **strictly
  per-month** (tabs, filters, search, the ↻ chip, zen all scope to the selected
  month). The *only* cross-thread signal is `followup_due` (server-computed in
  `db.list_threads`), surfaced per option in the month picker as `· ↻N` so a
  follow-up maturing after a month rolls over stays discoverable. `FOLLOWUP_DAYS`
  is mirrored in `index.html` and `db.py` — keep them equal.

## Invariants — do not break

1. **`user_state` is sacred.** Sync upserts `jobs` only; favorites, notes, and
   statuses live in `user_state` keyed by HN comment id and must survive any
   re-sync, re-classify, or schema change.
2. **Statuses**: inbox → interested → applied → interviewing → offer /
   rejected / archived, plus `later` (parking lot; `l` in zen mode, `+`
   re-enters at interested). The `+` action follows `NEXT_STAGE` in
   index.html; `×` toggles archived ↔ inbox.
3. Post bodies are raw HN-sanitized HTML rendered via innerHTML; links in them
   get `target="_blank" rel="noopener"` applied post-render in `render()`.
4. Cards are expanded by default (`state.closed` tracks collapsed ones).
5. Zen mode (`z`) = one card at a time over the CURRENT filtered list, with
   keyboard triage (`+`/`x` auto-advance via `zenStep`). Keep new features
   working in both list and zen views — they share `cardHTML()`.

## Workflow notes

- Server usually already running in background with `--reload`; check before
  starting another (port conflict).
- When bulk-editing user state (e.g. "mark these N companies interested"),
  match against titles case-insensitively, surface ambiguous/missing matches
  for confirmation BEFORE patching, and use the API rather than raw SQL.
- GitHub: private repo `eharris128/hnhiring`, pushed over SSH (needs sandbox
  disabled for `git push`).

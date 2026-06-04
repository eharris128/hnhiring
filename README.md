# hnhiring

Personal tracker for HN "Who is hiring?" threads. Pulls every top-level job post
from the official HN Firebase API (no RSS 100-item cap), tags posts with location
heuristics, and provides search, favorites, notes, and an application pipeline.

## Coming back? Start here

```sh
cd ~/projects/hnhiring
.venv/bin/uvicorn app:app --reload
# open http://127.0.0.1:8000 and hit Sync
```

(First-time setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`)

- **Sync** auto-discovers the newest "Who is hiring?" thread — on the 1st of the
  month it picks up the new thread automatically. Old months stay in the DB.
- Your favorites/notes/statuses live in `data.db` (gitignored, this machine only)
  and are never touched by sync.
- To sync a specific month: `curl -X POST 'localhost:8000/api/sync?thread_id=<hn story id>'`

## Monthly triage routine

1. Hit **Sync**.
2. Press **`z`** for Zen mode (one post at a time), stay on the **All** tab.
3. Triage with the keyboard:

   | key | action |
   |-----|--------|
   | `+` / `i` | bump a stage (inbox → interested → applied → …) and advance |
   | `x` | archive and advance (on archived: restore to inbox) |
   | `→` / `j`, `←` / `k` | next / previous |
   | `f` | favorite |
   | `n` | jump to notes (`Esc` to jump back) |
   | `z` / `Esc` | toggle Zen |

4. Switch to the **Interested** tab for the second pass: read properly, take
   notes, apply, and advance statuses (`+` or the dropdown).

## Filters

- **Remote** / **US-based** / **Swiss-friendly** chips combine with the status
  tabs and search. Swiss-friendly = located in Switzerland OR remote and open
  to Europe/worldwide.
- Tagging is regex-heuristic (`classify.py`) and occasionally wrong — the HN ↗
  link on each card is ground truth.

## Pipeline statuses

inbox → interested → applied → interviewing → offer / rejected / archived

## How it works

- `hn.py` — finds the newest thread via Algolia (`author_whoishiring`), then
  fetches the story and all its top-level comments from
  `hacker-news.firebaseio.com/v0/item/<id>.json` (50 concurrent, skips dead/deleted).
- `classify.py` — keyword heuristics → tags (`remote`, `onsite`, `us`, `europe`,
  `switzerland`, `worldwide`).
- `db.py` — SQLite: `jobs` (refreshed on sync) + `user_state` (yours, preserved).
- `app.py` — FastAPI: `POST /api/sync`, `GET /api/jobs`, `PATCH /api/jobs/{id}`.
- `static/index.html` — the whole frontend; vanilla JS, no build step.

# hnhiring

Personal tracker for HN "Who is hiring?" threads. Pulls every top-level job post
from the official HN Firebase API (no RSS 100-item cap), tags posts with location
heuristics, and lets you search, favorite, take notes, and track application status.

## Run

```sh
.venv/bin/uvicorn app:app --reload
# open http://127.0.0.1:8000
```

(First time: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`)

Hit **Sync** in the UI to fetch the latest thread. Syncing is idempotent — it
refreshes post content and tags but never touches your favorites/notes/statuses,
which live in a separate `user_state` table in `data.db`.

## How it works

- `hn.py` — resolves the newest "Who is hiring?" thread via Algolia
  (`author_whoishiring`), then fetches the story and all its `kids` from
  `hacker-news.firebaseio.com/v0/item/<id>.json` (50 concurrent requests).
  Dead/deleted comments are skipped.
- `classify.py` — keyword heuristics tag each post: `remote`, `onsite`, `us`,
  `europe`, `switzerland`, `worldwide`. The UI's **Swiss-friendly** filter is
  `switzerland OR (remote AND (europe OR worldwide))`.
- `db.py` — SQLite. `jobs` is overwritten on sync; `user_state` is yours.
- `app.py` — FastAPI: `POST /api/sync`, `GET /api/jobs`, `PATCH /api/jobs/{id}`.
- `static/index.html` — the whole frontend, no build step.

## Lifecycle statuses

inbox → interested → applied → interviewing → offer / rejected / archived

## Notes

- Tagging is heuristic; posts are occasionally mislabeled. The HN link on each
  card opens the original comment for ground truth.
- To sync a specific month's thread: `POST /api/sync?thread_id=<hn story id>`.

# hnhiring

Personal tracker for HN "Who is hiring?" threads. Pulls every top-level job post
from the official HN Firebase API (no RSS 100-item cap), tags posts with location
heuristics, and provides search, favorites, notes, and an application pipeline.

## Coming back? Start here

```sh
cd ~/projects/hnhiring
with-keys .venv/bin/uvicorn app:app --reload --port 8137
# open http://127.0.0.1:8137 and hit Sync
```

(First-time setup: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`)

`with-keys` injects the Gmail app password from Bitwarden (`api/google-app-password`)
as `$GMAIL_APP_PASSWORD`; plain `uvicorn` works too, only ✉ Apply stops sending.

- **Sync** auto-discovers the newest "Who is hiring?" thread — on the 1st of the
  month it picks up the new thread automatically and switches the view to it.
  Old months stay in the DB and stay browsable any time from the **month picker**
  in the header (newest selected by default).
- Your favorites/notes/statuses live in `data.db` (gitignored, this machine only)
  and are never touched by sync — switching months never disturbs them.
- Browsing is **per-month**. If an emailed application goes quiet (past 7 days) in a
  month you're *not* viewing, that month's option in the picker carries a **`· ↻N`**
  marker — open the picker now and then to catch them.
- To pull a specific or older month: `curl -X POST 'localhost:8137/api/sync?thread_id=<hn story id>'`
  — it then shows up in the month picker alongside the others. (Handy for landing a
  second month locally to try the picker before the next month's thread is live.)
- Sync also auto-files any untriaged post under **skip** if its company already
  rejected a portal application from you in a past month (a toast reports how
  many). It never touches a post you've already triaged — check the **Skip** tab
  occasionally in case it misfires on a same-named company.

## Monthly triage routine

1. Hit **Sync**.
2. Press **`z`** for Zen mode (one post at a time), stay on the **All** tab.
3. Triage with the keyboard:

   | key | action |
   |-----|--------|
   | `+` | bump a stage (inbox → interested → applied → …) and advance |
   | `x` | archive and advance (on archived: restore to inbox) |
   | `l` | send to Later (parking lot) and advance |
   | `r` | reject and advance |
   | `a` | open the ✉ Apply compose modal (or, on an applied post past follow-up, a reminder nudge) |
   | `→` / `j`, `←` / `k` | next / previous |
   | `f` | favorite |
   | `n` | jump to notes (`Esc` to leave the notes field) |
   | `i` | jump to the Interested tab |
   | `z` / `Esc` | toggle Zen |
   | `Ctrl`/`Cmd`+`z` | undo the last pile move (single level) |

4. Switch to the **Interested** tab for the second pass: read properly, take
   notes, apply, and advance statuses (`+` or the dropdown).

## Filters

- **Remote** / **US-based** / **Swiss-friendly** chips combine with the status
  tabs and search. Swiss-friendly = located in Switzerland OR remote and open
  to Europe/worldwide.
- Tagging is regex-heuristic (`classify.py`) and occasionally wrong — the HN ↗
  link on each card is ground truth.

## Pipeline statuses

inbox → interested → applied → interviewing → offer / rejected / archived,
plus `later` (parking lot) and `skip` (auto-filed re-postings, see above).

## How it works

- `hn.py` — finds the newest thread via Algolia (`author_whoishiring`), then
  fetches the story and all its top-level comments from
  `hacker-news.firebaseio.com/v0/item/<id>.json` (50 concurrent, skips dead/deleted).
- `classify.py` — keyword heuristics → tags (`remote`, `onsite`, `us`, `europe`,
  `switzerland`, `worldwide`), plus `extract_company` (normalized company name from
  the post title, used for the auto-skip check below).
- `db.py` — SQLite: `jobs` (refreshed on sync) + `user_state` (yours, preserved).
  `auto_skip_reapplied` runs on every sync and pre-files untriaged posts as
  `skip` when their company already rejected a portal application in a past
  month.
- `mailer.py` — sends the ✉ Apply emails through Gmail SMTP (resume PDF
  attached). Sender/resume/test address in `mail.json`, gitignored — see
  `mail.json.example`; the app password comes from `$GMAIL_APP_PASSWORD`.
- `app.py` — FastAPI: `POST /api/sync`, `GET /api/jobs` (optional `?thread_id=`),
  `GET /api/threads` (the months, for the picker), `PATCH /api/jobs/{id}`,
  `POST /api/jobs/{id}/apply` (send an application, or with `kind: "reminder"`,
  a follow-up nudge).
- `static/index.html` — the whole frontend; vanilla JS, no build step.

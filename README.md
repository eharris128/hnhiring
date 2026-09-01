# hnhiring

A local-first job-search tracker that ingests HN "Who is hiring?" threads. Pulls
every top-level job post from the official HN Firebase API (no RSS 100-item cap),
tags posts with location heuristics, and gives you search, favorites, notes, a
keyboard-driven triage mode, an application pipeline with follow-up nudges, and an
apply-by-email button with your resume attached. Everything stays on your machine:
SQLite, no accounts, no build step, three pip dependencies.

## Coming back? Start here

```sh
cd ~/projects/hnhiring
with-keys .venv/bin/uvicorn app:app --reload --port 8137
# open http://127.0.0.1:8137 and hit Sync
```

First-time setup:

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp profile.json.example profile.json   # then edit; see "Make it yours" below
```

`with-keys` injects the Gmail app password from Bitwarden (`api/google-app-password`)
as `$GMAIL_APP_PASSWORD`; plain `uvicorn` works too, only ✉ Apply stops sending.

- **Sync** auto-discovers the newest "Who is hiring?" thread. On the 1st of the
  month it picks up the new thread automatically and switches the view to it.
  Old months stay in the DB and stay browsable any time from the **month picker**
  in the header (newest selected by default).
- Your favorites/notes/statuses live in `data.db` (gitignored, this machine only)
  and are never touched by sync, so switching months never disturbs them.
- Browsing is **per-month**. If an emailed application goes quiet (past 7 days) in a
  month you're *not* viewing, that month's option in the picker carries a **`· ↻N`**
  marker, so open the picker now and then to catch them.
- To pull a specific or older month: `curl -X POST 'localhost:8137/api/sync?thread_id=<hn story id>'`.
  It then shows up in the month picker alongside the others. (Handy for landing a
  second month locally to try the picker before the next month's thread is live.)
- Sync also auto-files any untriaged post under **skip** if its company already
  rejected a portal application from you in a past month (a toast reports how
  many). It never touches a post you've already triaged, so check the **Skip** tab
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

- **Remote** / **US-based** / **<home>-friendly** chips combine with the status
  tabs and search. The third chip is yours to define in `profile.json` (see
  below): a post is *friendly* when it mentions one of your home places, OR it's
  remote and open to a region you can work from. With the example profile that
  reads "Swiss-friendly = in Switzerland, or remote and open to Europe/worldwide".
- Tagging is regex-heuristic (`classify.py`) and occasionally wrong. The HN ↗
  link on each card is ground truth.

## Make it yours (`profile.json`)

Everything personal lives in one gitignored file, `profile.json`. Copy
`profile.json.example` to start. Edits take effect on the next request (no
restart); the frontend re-reads it on every load, so refresh the page.

| key | what it does |
|-----|--------------|
| `name`, `address` | the From header on outgoing mail (Gmail account) |
| `signature` | how the templates sign off ("Best, *signature*"); defaults to `name` |
| `resume` | path to the PDF attached to every application and reminder |
| `test_address` | where the **Test** button in the compose modal delivers instead |
| `website` | linked in the application template; leave empty to drop the line |
| `followup_days` | days an emailed application sits quiet before it's flagged ↻ (chip, cards, and the month picker all use this one number) |
| `home.places` | plain words (not regexes) that mean "my region": cities, country, demonym. List spelling variants separately. Matched whole-word, case-insensitive |
| `home.friendly_remote_tags` | which remote regions also count as friendly: any of `us`, `europe`, `worldwide` |
| `home.label`, `home.friendly_label` | badge text for a post *in* your region and for the derived filter/badge |
| `pitches` | the paragraphs offered in the compose modal's pitch dropdown, keyed however you like; `label` is the dropdown text, `text` goes into the email (markdown-style `[text](url)` links become hyperlinks) |

Changing `home.places` only affects posts tagged from then on. Hit **Sync** to
re-tag the current month (or `curl -X POST 'localhost:8137/api/sync?thread_id=<id>'`
for an older one). Leave `places` and `friendly_remote_tags` both empty to hide the
home chip and badges entirely.

## Pipeline statuses

inbox → interested → applied → interviewing → offer / rejected / archived,
plus `later` (parking lot) and `skip` (auto-filed re-postings, see above).

## How it works

- `hn.py`: finds the newest thread via Algolia (`author_whoishiring`), then
  fetches the story and all its top-level comments from
  `hacker-news.firebaseio.com/v0/item/<id>.json` (50 concurrent, skips dead/deleted).
- `classify.py`: keyword heuristics → tags (`remote`, `onsite`, `us`, `europe`,
  `worldwide`, and `home` from your profile's `home.places`), plus `extract_company`
  (normalized company name from the post title, used for the auto-skip check below).
- `profile.py`: loads `profile.json` (mtime-cached) for the mailer, the classifier,
  the follow-up window, and the frontend.
- `db.py`: SQLite, with `jobs` (refreshed on sync) + `user_state` (yours, preserved).
  `auto_skip_reapplied` runs on every sync and pre-files untriaged posts as
  `skip` when their company already rejected a portal application in a past
  month.
- `mailer.py`: sends the ✉ Apply emails through Gmail SMTP (resume PDF
  attached). Sender/resume/test address come from `profile.json`; the app
  password comes from `$GMAIL_APP_PASSWORD`.
- `app.py`: FastAPI with `POST /api/sync`, `GET /api/jobs` (optional `?thread_id=`),
  `GET /api/threads` (the months, for the picker), `GET /api/profile` (the
  frontend's slice of `profile.json`, never the address or resume path),
  `PATCH /api/jobs/{id}`, `POST /api/jobs/{id}/apply` (send an application, or
  with `kind: "reminder"`, a follow-up nudge).
- `static/index.html`: the whole frontend; vanilla JS, no build step.

## Roadmap

- **Company re-posting frequency.** Company names are already normalized for the
  auto-skip check; counting how many months a company has posted is one query
  away and would flag perpetual hiring / ghost jobs on the card.

"""SQLite storage: job posts (refreshed on sync) and user state (never clobbered)."""

import calendar
import json
import re
import sqlite3
import time
from pathlib import Path

import classify

DB_PATH = Path(__file__).parent / "data.db"

# Follow-up window, mirrored from the frontend's FOLLOWUP_DAYS (index.html). An emailed
# application that has sat in 'applied' this many days with no reminder is "due". Used by
# list_threads to surface a cross-month follow-up cue on the picker. KEEP THESE EQUAL.
FOLLOWUP_DAYS = 7

SCHEMA = """
CREATE TABLE IF NOT EXISTS threads (
    id          INTEGER PRIMARY KEY,
    title       TEXT,
    time        INTEGER,
    last_synced INTEGER
);

CREATE TABLE IF NOT EXISTS jobs (
    id        INTEGER PRIMARY KEY,   -- HN comment id
    thread_id INTEGER NOT NULL,
    author    TEXT,
    time      INTEGER,
    title     TEXT,                  -- first line of the post, plain text
    text      TEXT,                  -- full post, HN-sanitized HTML
    tags      TEXT NOT NULL DEFAULT '[]',  -- JSON array from classify.py
    company   TEXT                  -- normalized company name from classify.extract_company, or NULL
);

CREATE TABLE IF NOT EXISTS user_state (
    job_id      INTEGER PRIMARY KEY,
    favorite    INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'inbox',
    notes       TEXT NOT NULL DEFAULT '',
    applied_via TEXT,                -- 'email' (follow up) | 'portal' | NULL
    applied_at  INTEGER,             -- unix secs first entered 'applied' (drives follow-up timing)
    reminded_at INTEGER,             -- unix secs a follow-up reminder was sent (NULL = none yet)
    updated_at  INTEGER
);
"""

# Audit line the apply endpoint writes ("✉ applied 2026-06-08 → x@y.com"); used to
# backfill applied_at for applications made before that column existed.
_APPLIED_NOTE_RE = re.compile(r"✉ applied (\d{4})-(\d{2})-(\d{2})")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    # Migrate older databases (CREATE IF NOT EXISTS won't add columns).
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(user_state)")}
    if "applied_via" not in cols:
        conn.execute("ALTER TABLE user_state ADD COLUMN applied_via TEXT")
    if "reminded_at" not in cols:
        conn.execute("ALTER TABLE user_state ADD COLUMN reminded_at INTEGER")
    if "applied_at" not in cols:
        conn.execute("ALTER TABLE user_state ADD COLUMN applied_at INTEGER")
        _backfill_applied_at(conn)
    job_cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    if "company" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN company TEXT")
        _backfill_company(conn)
    conn.commit()
    return conn


def _backfill_applied_at(conn: sqlite3.Connection) -> None:
    """Seed applied_at for existing email applications from their notes audit line.
    Uses noon UTC of the recorded day to stay clear of timezone edges. Rows without
    an audit line (e.g. bulk-marked) stay NULL and simply won't surface for follow-up."""
    for r in conn.execute(
        "SELECT job_id, notes FROM user_state WHERE status = 'applied' AND applied_via = 'email'"
    ).fetchall():
        m = _APPLIED_NOTE_RE.search(r["notes"] or "")
        if not m:
            continue
        ts = calendar.timegm((int(m[1]), int(m[2]), int(m[3]), 12, 0, 0, 0, 0, 0))
        conn.execute("UPDATE user_state SET applied_at = ? WHERE job_id = ?", (ts, r["job_id"]))


def _backfill_company(conn: sqlite3.Connection) -> None:
    """Seed company for jobs synced before the column existed, so the auto-skip
    matcher (see auto_skip_reapplied) works against past months without a resync."""
    for r in conn.execute("SELECT id, title FROM jobs").fetchall():
        company = classify.extract_company(r["title"] or "")
        if company:
            conn.execute("UPDATE jobs SET company = ? WHERE id = ?", (company, r["id"]))


def upsert_jobs(conn: sqlite3.Connection, thread: dict, jobs: list[dict]) -> None:
    conn.execute(
        "INSERT INTO threads (id, title, time, last_synced) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, last_synced=excluded.last_synced",
        (thread["id"], thread.get("title"), thread.get("time"), int(time.time())),
    )
    conn.executemany(
        "INSERT INTO jobs (id, thread_id, author, time, title, text, tags, company) "
        "VALUES (:id, :thread_id, :author, :time, :title, :text, :tags, :company) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, text=excluded.text, "
        "tags=excluded.tags, company=excluded.company",
        jobs,
    )
    conn.commit()


def auto_skip_reapplied(conn: sqlite3.Connection) -> int:
    """Auto-bucket untriaged jobs into status='skip' when their company already
    rejected a portal application from a past post. Only ever initializes jobs
    with no user_state row yet, so it never touches anything already triaged —
    user_state stays sacred. Returns the number of jobs skipped."""
    rejected_companies = {
        r["company"] for r in conn.execute(
            "SELECT DISTINCT j.company FROM jobs j "
            "JOIN user_state u ON u.job_id = j.id "
            "WHERE u.status = 'rejected' AND u.applied_via = 'portal' AND j.company IS NOT NULL"
        ).fetchall()
    }
    if not rejected_companies:
        return 0
    candidates = conn.execute(
        "SELECT j.id, j.company FROM jobs j LEFT JOIN user_state u ON u.job_id = j.id "
        "WHERE u.job_id IS NULL AND j.company IS NOT NULL"
    ).fetchall()
    skipped = 0
    for r in candidates:
        if r["company"] in rejected_companies:
            update_user_state(conn, r["id"], {"status": "skip"})
            skipped += 1
    return skipped


def list_jobs(conn: sqlite3.Connection, thread_id: int | None = None) -> list[dict]:
    where = "WHERE j.thread_id = ?" if thread_id else ""
    params = (thread_id,) if thread_id else ()
    rows = conn.execute(
        f"""SELECT j.*, COALESCE(u.favorite, 0) AS favorite,
                   COALESCE(u.status, 'inbox') AS status,
                   COALESCE(u.notes, '') AS notes,
                   u.applied_via AS applied_via,
                   u.applied_at AS applied_at,
                   u.reminded_at AS reminded_at
            FROM jobs j LEFT JOIN user_state u ON u.job_id = j.id
            {where} ORDER BY j.time DESC""",
        params,
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d["tags"])
        d["favorite"] = bool(d["favorite"])
        out.append(d)
    return out


def update_user_state(conn: sqlite3.Connection, job_id: int, fields: dict) -> dict:
    allowed = {k: v for k, v in fields.items()
               if k in ("favorite", "status", "notes", "applied_via", "applied_at", "reminded_at")}
    if "favorite" in allowed:
        allowed["favorite"] = int(bool(allowed["favorite"]))
    conn.execute(
        "INSERT INTO user_state (job_id, updated_at) VALUES (?, ?) "
        "ON CONFLICT(job_id) DO NOTHING",
        (job_id, int(time.time())),
    )
    # Stamp the applied date the first time a post enters 'applied' (any path: apply
    # endpoint, + bump, or the status dropdown). This is what drives follow-up timing.
    if allowed.get("status") == "applied" and "applied_at" not in allowed:
        row = conn.execute("SELECT applied_at FROM user_state WHERE job_id = ?", (job_id,)).fetchone()
        if not row or row["applied_at"] is None:
            allowed["applied_at"] = int(time.time())
    if allowed:
        sets = ", ".join(f"{k} = ?" for k in allowed)
        conn.execute(
            f"UPDATE user_state SET {sets}, updated_at = ? WHERE job_id = ?",
            (*allowed.values(), int(time.time()), job_id),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM user_state WHERE job_id = ?", (job_id,)).fetchone()
    return dict(row)


def latest_thread(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT * FROM threads ORDER BY time DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def get_thread(conn: sqlite3.Connection, thread_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM threads WHERE id = ?", (thread_id,)).fetchone()
    return dict(row) if row else None


def list_threads(conn: sqlite3.Connection) -> list[dict]:
    """Every synced month, newest first, with two per-month counts: job_count, and
    followup_due — emailed applications past the follow-up window with no reminder yet
    (the same predicate the frontend's followupDue uses). followup_due drives the
    picker's cross-month cue so per-month browsing doesn't strand post-rollover nudges.
    LEFT JOINs so a thread with zero kept posts still appears (both counts 0)."""
    cutoff = int(time.time()) - FOLLOWUP_DAYS * 86400
    rows = conn.execute(
        """SELECT t.*, COUNT(j.id) AS job_count,
                  COALESCE(SUM(CASE WHEN u.status = 'applied' AND u.applied_via = 'email'
                                     AND u.applied_at IS NOT NULL AND u.reminded_at IS NULL
                                     AND u.applied_at <= ?
                                    THEN 1 ELSE 0 END), 0) AS followup_due
           FROM threads t
           LEFT JOIN jobs j ON j.thread_id = t.id
           LEFT JOIN user_state u ON u.job_id = j.id
           GROUP BY t.id ORDER BY t.time DESC""",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]

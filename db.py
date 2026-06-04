"""SQLite storage: job posts (refreshed on sync) and user state (never clobbered)."""

import json
import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "data.db"

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
    tags      TEXT NOT NULL DEFAULT '[]'  -- JSON array from classify.py
);

CREATE TABLE IF NOT EXISTS user_state (
    job_id     INTEGER PRIMARY KEY,
    favorite   INTEGER NOT NULL DEFAULT 0,
    status     TEXT NOT NULL DEFAULT 'inbox',
    notes      TEXT NOT NULL DEFAULT '',
    updated_at INTEGER
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def upsert_jobs(conn: sqlite3.Connection, thread: dict, jobs: list[dict]) -> None:
    conn.execute(
        "INSERT INTO threads (id, title, time, last_synced) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, last_synced=excluded.last_synced",
        (thread["id"], thread.get("title"), thread.get("time"), int(time.time())),
    )
    conn.executemany(
        "INSERT INTO jobs (id, thread_id, author, time, title, text, tags) "
        "VALUES (:id, :thread_id, :author, :time, :title, :text, :tags) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title, text=excluded.text, tags=excluded.tags",
        jobs,
    )
    conn.commit()


def list_jobs(conn: sqlite3.Connection, thread_id: int | None = None) -> list[dict]:
    where = "WHERE j.thread_id = ?" if thread_id else ""
    params = (thread_id,) if thread_id else ()
    rows = conn.execute(
        f"""SELECT j.*, COALESCE(u.favorite, 0) AS favorite,
                   COALESCE(u.status, 'inbox') AS status,
                   COALESCE(u.notes, '') AS notes
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
    allowed = {k: v for k, v in fields.items() if k in ("favorite", "status", "notes")}
    if "favorite" in allowed:
        allowed["favorite"] = int(bool(allowed["favorite"]))
    conn.execute(
        "INSERT INTO user_state (job_id, updated_at) VALUES (?, ?) "
        "ON CONFLICT(job_id) DO NOTHING",
        (job_id, int(time.time())),
    )
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

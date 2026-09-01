"""HN Who-is-Hiring tracker — personal job application lifecycle manager.

Run:  uvicorn app:app --reload
"""

import smtplib
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
import hn
import mailer
import profile

app = FastAPI(title="hnhiring")
STATIC = Path(__file__).parent / "static"


class UserStatePatch(BaseModel):
    favorite: bool | None = None
    status: str | None = None
    notes: str | None = None
    applied_via: str | None = None  # 'email' | 'portal'


class ApplyRequest(BaseModel):
    to: str
    subject: str
    body: str
    test: bool = False  # send to profile.json's test_address instead; no state change
    kind: str = "application"  # 'application' (mark applied) | 'reminder' (follow-up nudge)


STATUSES = {"inbox", "interested", "later", "applied", "interviewing", "offer", "rejected", "archived", "skip"}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/jobs")
def get_jobs(thread_id: int | None = None):
    """Jobs for one month. No thread_id → newest month (back-compat, drives initial load);
    a known id → that month; an unknown id → 404 (unreachable from the UI, which only
    offers months it listed via /api/threads)."""
    conn = db.connect()
    try:
        if thread_id is None:
            thread = db.latest_thread(conn)
        else:
            thread = db.get_thread(conn, thread_id)
            if thread is None:
                raise HTTPException(404, "unknown thread id")
        jobs = db.list_jobs(conn, thread["id"]) if thread else []
        return {"thread": thread, "jobs": jobs}
    finally:
        conn.close()


@app.get("/api/threads")
def get_threads():
    """Every synced month for the picker — newest first, each with job_count and a
    followup_due count (the cross-month follow-up cue)."""
    conn = db.connect()
    try:
        return db.list_threads(conn)
    finally:
        conn.close()


@app.get("/api/profile")
def get_profile():
    """The frontend's slice of profile.json: signature/website for the templates, the
    pitches, followup_days, and the home region. Never the address or resume path."""
    return profile.public()


@app.post("/api/sync")
async def sync(thread_id: int | None = None):
    thread, jobs = await hn.fetch_thread(thread_id)
    conn = db.connect()
    try:
        db.upsert_jobs(conn, thread, jobs)
        auto_skipped = db.auto_skip_reapplied(conn)
        return {
            "thread_id": thread["id"],
            "title": thread.get("title"),
            "jobs": len(jobs),
            "auto_skipped": auto_skipped,
        }
    finally:
        conn.close()


@app.post("/api/jobs/{job_id}/apply")
def apply(job_id: int, req: ApplyRequest):
    """Email an application (resume attached) and mark the post applied, or — when
    kind='reminder' — send a follow-up nudge and stamp reminded_at without touching
    the application's status/channel/date."""
    if req.kind not in ("application", "reminder"):
        raise HTTPException(422, "kind must be 'application' or 'reminder'")
    conn = db.connect()
    try:
        exists = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "unknown job id")
        to, subject = req.to, req.subject
        try:
            if req.test:
                to = mailer.load_config().get("test_address")
                if not to:
                    raise mailer.MailConfigError("add test_address to profile.json")
                subject = f"[TEST] {req.subject}"
            mailer.send_application(to, subject, req.body)
        except mailer.MailConfigError as e:
            raise HTTPException(503, str(e))
        except smtplib.SMTPAuthenticationError:
            raise HTTPException(
                502, "Gmail rejected the login — $GMAIL_APP_PASSWORD may be stale; "
                     "regenerate at myaccount.google.com/apppasswords")
        except (smtplib.SMTPException, OSError) as e:
            raise HTTPException(502, f"send failed: {e}")
        if req.test:
            return {"test": True, "to": to}
        row = conn.execute("SELECT notes FROM user_state WHERE job_id = ?", (job_id,)).fetchone()
        notes = (row["notes"] if row else "") or ""
        stamp = time.strftime("%Y-%m-%d")
        if req.kind == "reminder":
            # Record the nudge; clears it from the follow-up bucket, leaves status alone.
            notes = f"{notes.rstrip()}\n↻ reminder sent {stamp} → {req.to}".strip()
            return db.update_user_state(
                conn, job_id, {"notes": notes, "reminded_at": int(time.time())})
        # Application: audit trail in notes, then mark applied (applied_at auto-stamped).
        notes = f"{notes.rstrip()}\n✉ applied {stamp} → {req.to}".strip()
        return db.update_user_state(
            conn, job_id, {"status": "applied", "notes": notes, "applied_via": "email"})
    finally:
        conn.close()


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: int, patch: UserStatePatch):
    if patch.status is not None and patch.status not in STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(STATUSES)}")
    if patch.applied_via is not None and patch.applied_via not in ("email", "portal"):
        raise HTTPException(422, "applied_via must be 'email' or 'portal'")
    conn = db.connect()
    try:
        exists = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "unknown job id")
        # exclude_unset (not exclude_none): lets a caller explicitly send applied_via:
        # null to clear it (e.g. undoing a bump that set it) while still omitting fields
        # it didn't mention at all.
        fields = patch.model_dump(exclude_unset=True)
        return db.update_user_state(conn, job_id, fields)
    finally:
        conn.close()

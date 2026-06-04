"""HN Who-is-Hiring tracker — personal job application lifecycle manager.

Run:  uvicorn app:app --reload
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

import db
import hn

app = FastAPI(title="hnhiring")
STATIC = Path(__file__).parent / "static"


class UserStatePatch(BaseModel):
    favorite: bool | None = None
    status: str | None = None
    notes: str | None = None


STATUSES = {"inbox", "interested", "applied", "interviewing", "offer", "rejected", "archived"}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/jobs")
def get_jobs():
    conn = db.connect()
    try:
        thread = db.latest_thread(conn)
        jobs = db.list_jobs(conn, thread["id"] if thread else None)
        return {"thread": thread, "jobs": jobs}
    finally:
        conn.close()


@app.post("/api/sync")
async def sync(thread_id: int | None = None):
    thread, jobs = await hn.fetch_thread(thread_id)
    conn = db.connect()
    try:
        db.upsert_jobs(conn, thread, jobs)
        return {"thread_id": thread["id"], "title": thread.get("title"), "jobs": len(jobs)}
    finally:
        conn.close()


@app.patch("/api/jobs/{job_id}")
def patch_job(job_id: int, patch: UserStatePatch):
    if patch.status is not None and patch.status not in STATUSES:
        raise HTTPException(422, f"status must be one of {sorted(STATUSES)}")
    conn = db.connect()
    try:
        exists = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not exists:
            raise HTTPException(404, "unknown job id")
        fields = patch.model_dump(exclude_none=True)
        return db.update_user_state(conn, job_id, fields)
    finally:
        conn.close()

"""Fetch Who-is-Hiring data from the official HN Firebase API (+ Algolia for discovery)."""

import asyncio
import json

import httpx

import classify

FIREBASE_ITEM = "https://hacker-news.firebaseio.com/v0/item/{id}.json"
ALGOLIA_LATEST = (
    "https://hn.algolia.com/api/v1/search_by_date"
    "?tags=story,author_whoishiring&hitsPerPage=10"
)
FALLBACK_THREAD_ID = 48357725  # Ask HN: Who is hiring? (June 2026)

_CONCURRENCY = 50


async def resolve_latest_thread_id(client: httpx.AsyncClient) -> int:
    """Most recent 'Who is hiring?' story by the whoishiring bot."""
    try:
        resp = await client.get(ALGOLIA_LATEST)
        resp.raise_for_status()
        for hit in resp.json()["hits"]:
            if "who is hiring" in hit.get("title", "").lower():
                return int(hit["story_id"])
    except (httpx.HTTPError, KeyError, ValueError):
        pass
    return FALLBACK_THREAD_ID


async def _fetch_item(client: httpx.AsyncClient, sem: asyncio.Semaphore, item_id: int) -> dict | None:
    async with sem:
        try:
            resp = await client.get(FIREBASE_ITEM.format(id=item_id))
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError:
            return None


async def fetch_thread(thread_id: int | None = None) -> tuple[dict, list[dict]]:
    """Return (thread, job rows ready for db.upsert_jobs)."""
    async with httpx.AsyncClient(timeout=30) as client:
        if thread_id is None:
            thread_id = await resolve_latest_thread_id(client)

        thread = (await client.get(FIREBASE_ITEM.format(id=thread_id))).json()
        if not thread or thread.get("type") != "story":
            raise ValueError(f"HN item {thread_id} is not a story")

        sem = asyncio.Semaphore(_CONCURRENCY)
        kids = thread.get("kids", [])
        items = await asyncio.gather(*(_fetch_item(client, sem, k) for k in kids))

    jobs = []
    for item in items:
        if not item or item.get("deleted") or item.get("dead") or not item.get("text"):
            continue
        text = item["text"]
        jobs.append(
            {
                "id": item["id"],
                "thread_id": thread_id,
                "author": item.get("by", ""),
                "time": item.get("time", 0),
                "title": classify.first_line(classify.to_plain(text)),
                "text": text,
                "tags": json.dumps(classify.classify(text)),
            }
        )
    return thread, jobs

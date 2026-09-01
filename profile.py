"""The user's profile: who you are, how you apply, and where "home" is.

Lives in profile.json (gitignored — copy profile.json.example and edit). One file
feeds four consumers:

    mailer.py    sender name/address, resume path, test address
    classify.py  home.places → the `home` tag
    db.py        followup_days (the ↻ follow-up window)
    app.py       GET /api/profile — the frontend-facing subset (templates, labels,
                 pitches, followup_days, home); never the resume path or address

Reads are cached by file mtime, so editing profile.json takes effect on the next
request without a server restart. With no profile.json at all, the example file is
used so the UI works out of the box (sending mail then fails with a clear error).
"""

import json
from pathlib import Path

PATH = Path(__file__).parent / "profile.json"
EXAMPLE = Path(__file__).parent / "profile.json.example"

# Every key the code reads, with a safe default, so a partial profile.json can't
# crash a consumer. Nested `home` is merged one level deep.
DEFAULTS: dict = {
    "name": "",
    "signature": "",
    "address": "",
    "resume": "",
    "test_address": "",
    "website": "",
    "followup_days": 7,
    "home": {
        "label": "",
        "friendly_label": "",
        "places": [],
        "friendly_remote_tags": ["europe", "worldwide"],
    },
    "pitches": {},
}

_cache: tuple[Path, float, dict] | None = None


def exists() -> bool:
    return PATH.exists()


def load() -> dict:
    """profile.json (or the example when it's missing) merged over DEFAULTS."""
    global _cache
    path = PATH if PATH.exists() else EXAMPLE
    mtime = path.stat().st_mtime
    if _cache and _cache[0] == path and _cache[1] == mtime:
        return _cache[2]
    raw = json.loads(path.read_text())
    cfg = {**DEFAULTS, **raw}
    cfg["home"] = {**DEFAULTS["home"], **(raw.get("home") or {})}
    if not cfg["home"]["friendly_label"] and cfg["home"]["label"]:
        cfg["home"]["friendly_label"] = f"{cfg['home']['label']}-friendly"
    if not cfg["signature"]:
        cfg["signature"] = cfg["name"]
    _cache = (path, mtime, cfg)
    return cfg


def public() -> dict:
    """What the frontend needs — and nothing that identifies the mailbox."""
    cfg = load()
    return {k: cfg[k] for k in ("name", "signature", "website", "followup_days", "home", "pitches")}

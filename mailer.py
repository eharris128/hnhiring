"""Send job-application emails through Gmail SMTP, resume always attached.

Config lives in mail.json (gitignored — see mail.json.example). The app
password itself comes from $GMAIL_APP_PASSWORD, injected from Bitwarden by
`with-keys` (mail.json may also carry it, but env wins). Gmail requires an app
password (https://myaccount.google.com/apppasswords), not the account password.
Mail sent this way still lands in the Gmail Sent folder.
"""

import html
import json
import mimetypes
import os
import re
import smtplib
from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path

CONFIG_PATH = Path(__file__).parent / "mail.json"

# Markdown-style [text](url) in the compose body becomes a hyperlink in the
# HTML part and "text (url)" in the plain-text fallback.
LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")


def _to_plain(body: str) -> str:
    return LINK_RE.sub(r"\1 (\2)", body)


def _to_html(body: str) -> str:
    links: list[str] = []

    def stash(m: re.Match) -> str:
        links.append(f'<a href="{m.group(2)}">{html.escape(m.group(1))}</a>')
        return f"\x00{len(links) - 1}\x00"

    out = html.escape(LINK_RE.sub(stash, body))
    out = re.sub(r"https?://[^\s<>\x00]+",
                 lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', out)
    out = re.sub(r"\x00(\d+)\x00", lambda m: links[int(m.group(1))], out)
    return "<div>" + out.replace("\n", "<br>\n") + "</div>"


class MailConfigError(Exception):
    """Config missing/incomplete, or the resume file is gone."""


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise MailConfigError("mail.json not found — copy mail.json.example and fill it in")
    cfg = json.loads(CONFIG_PATH.read_text())
    if os.environ.get("GMAIL_APP_PASSWORD"):
        cfg["app_password"] = os.environ["GMAIL_APP_PASSWORD"]
    elif not cfg.get("app_password"):
        raise MailConfigError(
            "no app password — run under `with-keys` so $GMAIL_APP_PASSWORD is set")
    missing = [k for k in ("address", "resume") if not cfg.get(k)]
    if missing:
        raise MailConfigError(f"mail.json is missing: {', '.join(missing)}")
    cfg["resume"] = Path(cfg["resume"]).expanduser()
    if not cfg["resume"].exists():
        raise MailConfigError(f"resume not found at {cfg['resume']}")
    return cfg


def send_application(to: str, subject: str, body: str) -> None:
    cfg = load_config()
    msg = EmailMessage()
    msg["From"] = formataddr((cfg.get("name", "Evan Harris"), cfg["address"]))
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(_to_plain(body))
    msg.add_alternative(_to_html(body), subtype="html")

    resume: Path = cfg["resume"]
    ctype, _ = mimetypes.guess_type(resume.name)
    maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
    msg.add_attachment(resume.read_bytes(), maintype=maintype, subtype=subtype,
                       filename=resume.name)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        # Gmail shows app passwords in four spaced groups; strip for login.
        smtp.login(cfg["address"], cfg["app_password"].replace(" ", ""))
        smtp.send_message(msg)

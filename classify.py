"""Keyword heuristics for tagging Who-is-Hiring posts.

Tags emitted:
    remote       — remote work offered (possibly geo-restricted)
    onsite       — onsite/hybrid presence mentioned
    us           — US-located or US-restricted
    europe       — Europe/EU/EMEA-located or -eligible
    switzerland  — Swiss location mentioned
    worldwide    — explicitly global remote

The UI derives the user-facing filters:
    Remote          = remote
    US-based        = us
    Swiss-friendly  = switzerland OR (remote AND (europe OR worldwide))
"""

import html
import re

# Strip HN's HTML so heuristics see plain text. <p> becomes a newline so the
# first line of the post can serve as a title.
_TAG_RE = re.compile(r"<p>|<[^>]+>")


def to_plain(text_html: str) -> str:
    text = _TAG_RE.sub(lambda m: "\n" if m.group() == "<p>" else "", text_html)
    return html.unescape(text)


def first_line(plain: str, limit: int = 160) -> str:
    line = plain.strip().split("\n", 1)[0].strip()
    return line[:limit] + ("…" if len(line) > limit else "")


# Most posts follow "Company | Role | Location | ..."; the first segment is the
# company. Reject segments that look like a role/descriptor instead (posts that
# omit the company name) so an unstructured title never masquerades as a company
# and collides with an unrelated one under exact-match comparison.
_NOT_A_COMPANY = re.compile(
    r"\b(?:engineer|developer|programmer|scientist|manager|director|architect|"
    r"designer|analyst|specialist|intern|founder|president|remote|onsite|"
    r"on-site|hybrid|full-?time|part-?time|contract|freelance|senior|staff|"
    r"principal|junior|lead|founding|multiple|various|roles?|positions?)\b",
    re.I,
)
_LEGAL_SUFFIX = re.compile(
    r"\s+(?:inc|llc|ltd|gmbh|ag|corp|co|pbc|plc|sa|limited|company)$"
)


def extract_company(title: str) -> str | None:
    """Best-effort company name from a post's title, normalized for exact-match
    comparison (lowercased, legal suffix stripped). None when the title doesn't
    follow the "Company | ..." convention or its first segment isn't a company."""
    if "|" not in title:
        return None
    first = title.split("|", 1)[0]
    first = re.sub(r"\(.*?\)", "", first)
    first = re.sub(r"https?://\S+", "", first)
    first = first.strip()
    if not first or _NOT_A_COMPANY.search(first):
        return None
    normalized = re.sub(r"[^a-z0-9]+", " ", first.lower()).strip()
    while True:
        stripped = _LEGAL_SUFFIX.sub("", normalized).strip()
        if stripped == normalized:
            break
        normalized = stripped
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized if len(normalized) >= 2 else None


_REMOTE = re.compile(r"\bremote\b", re.I)
_NO_REMOTE = re.compile(r"\b(?:no|not|isn'?t)\s+remote\b|\bremote\s*:?\s*no\b", re.I)
_ONSITE = re.compile(r"\bon-?site\b|\bhybrid\b|\bin[- ]office\b", re.I)

_REMOTE_US = re.compile(
    r"remote\s*\(\s*(?:us|usa|u\.s\.?a?\.?|united states)[^)]*\)"
    r"|\bus[- ]only\b|\bus[- ]based\b|\bus\s+time\s?zones?\b"
    r"|remote (?:in|within) the (?:us|usa|united states)\b",
    re.I,
)
_US_PLACES = re.compile(
    r"\busa\b|\bu\.s\.\b|\bunited states\b"
    r"|\bnyc\b|new york|san francisco|\bsf\b|bay area|silicon valley"
    r"|palo alto|mountain view|menlo park|sunnyvale|san jose|oakland"
    r"|seattle|austin|boston|denver|chicago|los angeles|san diego"
    r"|washington,? d\.?c\.?|atlanta|miami|portland|philadelphia|pittsburgh"
    r"|salt lake city|raleigh|durham|nashville|minneapolis|houston|dallas",
    re.I,
)

_SWISS = re.compile(
    r"\bswitzerland\b|\bswiss\b|\bz[üu]rich\b|\bgeneva\b|\bgen[èe]ve\b|\blausanne\b|\bbasel\b|\bbern\b|\bzug\b",
    re.I,
)
_EUROPE = re.compile(
    r"\beurope(?:an)?\b|\bemea\b|\bcet\b|\bcest\b|\beu\b"
    r"|remote\s*\(\s*(?:eu|europe)[^)]*\)",
    re.I,
)
_WORLDWIDE = re.compile(
    r"\bworldwide\b|\banywhere\b|\bglobal(?:ly)?\b|remote\s*\(\s*(?:global|worldwide|anywhere)\s*\)",
    re.I,
)


def classify(text_html: str) -> list[str]:
    plain = to_plain(text_html)
    tags = []
    if _REMOTE.search(plain) and not _NO_REMOTE.search(plain):
        tags.append("remote")
    if _ONSITE.search(plain):
        tags.append("onsite")
    if _REMOTE_US.search(plain) or _US_PLACES.search(plain):
        tags.append("us")
    if _EUROPE.search(plain):
        tags.append("europe")
    if _SWISS.search(plain):
        tags.append("switzerland")
    if _WORLDWIDE.search(plain):
        tags.append("worldwide")
    return tags

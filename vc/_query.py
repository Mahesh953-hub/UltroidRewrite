"""Classify a ``.vplay`` query so the user sees an accurate status message.

The distinction matters because the three cases take visibly different time:

- a bare 11-char id skips search entirely — only metadata is fetched
- a URL is a direct reference — only metadata is fetched
- free text needs a search round-trip *and* then a metadata fetch

Reporting "Searching: never gonna give you up" for a bare video id is
technically harmless but reads as though something slow is happening, and
reporting "Fetching" for free text hides the search step that actually
dominates the wait.
"""

from __future__ import annotations

import re
from enum import Enum

__all__ = ["QueryKind", "classify", "status_head", "status_rows"]


class QueryKind(str, Enum):
    VIDEO_ID = "id"
    URL = "url"
    SEARCH = "search"


#: youtube.com/watch?v=ID, youtu.be/ID, /shorts/ID, /embed/ID, /live/ID
_URL_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/|v/)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11})",
    re.IGNORECASE,
)
#: a bare id with no surrounding characters
_BARE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
#: any URL at all (including non-YouTube, which the resolver may still try)
_ANY_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def classify(query: str) -> QueryKind:
    """Return the kind of a raw ``.vplay`` argument."""
    q = (query or "").strip()
    if not q:
        return QueryKind.SEARCH
    if _BARE_ID_RE.match(q):
        return QueryKind.VIDEO_ID
    if _URL_RE.search(q) or _ANY_URL_RE.match(q):
        return QueryKind.URL
    return QueryKind.SEARCH


def status_head(kind: QueryKind, query: str) -> str:
    """The one-line status shown while the query is being resolved."""
    q = (query or "").strip()
    if kind is QueryKind.VIDEO_ID:
        return f"Fetching track `{q}`"
    if kind is QueryKind.URL:
        return f"Fetching link `{q}`"
    return f"Searching `{q}`"


def status_rows(kind: QueryKind) -> dict:
    """Extra detail rows for the status message."""
    if kind is QueryKind.VIDEO_ID:
        return {"Stage": "direct id", "Next": "reading metadata"}
    if kind is QueryKind.URL:
        return {"Stage": "direct link", "Next": "reading metadata"}
    return {"Stage": "search", "Next": "picking the best match"}

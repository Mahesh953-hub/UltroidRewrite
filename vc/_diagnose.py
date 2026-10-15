"""Classify extraction failures into actionable, honest diagnostics.

The raw failure from yt-dlp is long, multi-line, and does not say what the
*user* can do. Worse, when several extractors fail for the same underlying
reason, the user sees the same wall of text repeated once per attempt.

Two things are wrong in practice:

1. ``UNEXPECTED_EOF_WHILE_READING`` / ``CERTIFICATE_VERIFY_FAILED`` mean the
   TLS layer is being intercepted or the network blocks the host. No amount
   of client rotation, format tweaking, or cookie removal helps, and the
   resolver would otherwise burn ~90s trying all of them.
2. pytubefix and yt-dlp can fail for the *same* network reason. Reporting
   eight near-identical stack traces hides that there is one real cause.
"""

from __future__ import annotations

from enum import Enum

__all__ = ["FailureKind", "classify_failure", "diagnose", "summarise_attempts"]


class FailureKind(str, Enum):
    TLS = "tls"
    NETWORK = "network"
    UNAVAILABLE = "unavailable"
    NO_FORMAT = "no_format"
    RATE_LIMITED = "rate_limited"
    GEO_BLOCKED = "geo_blocked"
    UNKNOWN = "unknown"


#: Substrings that identify a transport-layer failure. Checked against a
#: lowercased concatenation of every attempt's error text.
_TLS_MARKERS = (
    "unexpected_eof_while_reading",
    "eof occurred in violation of protocol",
    "certificate_verify_failed",
    "certificate verify failed",
    "sslerror",
    "ssl: wrong_version",
    "tlsv1",
    "handshake_failure",
    "sslv3",
)
_NETWORK_MARKERS = (
    "name or service not known",
    "temporary failure in name resolution",
    "getaddrinfo",
    "connection reset",
    "connection aborted",
    "connection refused",
    "timed out",
    "timeout",
    "network is unreachable",
    "max retries exceeded",
    "unable to download webpage",
    "unable to download api page",
)
_UNAVAILABLE_MARKERS = (
    "private video",
    "video unavailable",
    "removed by the uploader",
    "account associated with this video has been terminated",
    "this video is not available",
    "members-only",
    "join this channel",
    "does not exist",
)
_NO_FORMAT_MARKERS = (
    "no video formats found",
    "requested format is not available",
    "sabr",
    "no ffmpeg-readable audio",
    "no audio stream",
)
_RATE_MARKERS = (
    "http error 429",
    "too many requests",
    "rate-limit",
    "ratelimit",
)
_GEO_MARKERS = (
    "not available in your country",
    "geo restricted",
    "blocked it in your country",
    "geo_bypass",
)


def classify_failure(text: str) -> FailureKind:
    """Best-effort classification of a combined extractor error blob."""
    low = (text or "").lower()
    if not low:
        return FailureKind.UNKNOWN
    # TLS before NETWORK: the TLS markers are more specific, and the network
    # markers overlap with them ("unable to download api page" is a symptom).
    if any(m in low for m in _TLS_MARKERS):
        return FailureKind.TLS
    if any(m in low for m in _RATE_MARKERS):
        return FailureKind.RATE_LIMITED
    if any(m in low for m in _GEO_MARKERS):
        return FailureKind.GEO_BLOCKED
    if any(m in low for m in _UNAVAILABLE_MARKERS):
        return FailureKind.UNAVAILABLE
    if any(m in low for m in _NO_FORMAT_MARKERS):
        return FailureKind.NO_FORMAT
    if any(m in low for m in _NETWORK_MARKERS):
        return FailureKind.NETWORK
    return FailureKind.UNKNOWN


#: What the user can actually do about each kind.
_ADVICE = {
    FailureKind.TLS: (
        "The connection to YouTube is being intercepted or blocked at the TLS "
        "layer, so no extractor can work right now."
    ),
    FailureKind.NETWORK: "The host could not be reached from this machine.",
    FailureKind.RATE_LIMITED: "YouTube is rate-limiting this IP. Wait a few minutes.",
    FailureKind.GEO_BLOCKED: "This video is not available from this region.",
    FailureKind.UNAVAILABLE: "That video is private, removed, or region-locked.",
    FailureKind.NO_FORMAT: (
        "YouTube returned no stream ffmpeg can read (SABR-only or token-gated)."
    ),
    FailureKind.UNKNOWN: "",
}

_TLS_DETAIL = (
    "This is not a cookie or PO-token problem, so adding cookies or rotating "
    "clients will not help. Common causes: a TLS-intercepting proxy, a network "
    "that blocks youtube.com, or a firewall that resets the connection."
)


def diagnose(errors: list[str], url: str = "") -> tuple[str, str, FailureKind]:
    """Turn raw attempt errors into ``(headline, detail, kind)``.

    ``headline`` is one short line for the status message; ``detail`` is the
    collapsed evidence, for the expandable block.
    """
    blob = " | ".join(errors or [])
    kind = classify_failure(blob)

    if kind is FailureKind.TLS:
        # Only keep the first TLS error: all eight attempts produce the same
        # cause, and repeating it eight times is noise, not evidence.
        first = next(
            (e for e in errors if classify_failure(e) is FailureKind.TLS), ""
        )
        headline = "🔒 Blocked by TLS interception"
        detail = _ADVICE[FailureKind.TLS] + "\n\n" + _TLS_DETAIL
        if first:
            detail += f"\n\nError: {_trim(first)}"
        return headline, detail, kind

    if kind is FailureKind.UNKNOWN:
        headline = "Couldn't resolve that track"
        detail = _trim(blob) or "No extractor returned a usable audio stream."
        return headline, detail, kind

    headline = {
        FailureKind.NETWORK: "🌐 Network unreachable",
        FailureKind.RATE_LIMITED: "⏳ Rate limited",
        FailureKind.GEO_BLOCKED: "🌍 Not available here",
        FailureKind.UNAVAILABLE: "🚫 Track unavailable",
        FailureKind.NO_FORMAT: "🎵 No readable stream",
    }[kind]
    detail = _ADVICE[kind] + (f"\n\nError: {_trim(blob)}" if blob else "")
    return headline, detail, kind


def _trim(text: str, limit: int = 300) -> str:
    """Collapse whitespace and cap length - these go into a chat message."""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return flat
    return flat[: limit - 1] + "…"


def summarise_attempts(errors: list[str]) -> str:
    """One line per distinct failure kind, with how many attempts hit it."""
    counts: dict[FailureKind, int] = {}
    for e in errors or []:
        k = classify_failure(e)
        counts[k] = counts.get(k, 0) + 1
    if not counts:
        return ""
    return ", ".join(f"{k.value} ×{n}" for k, n in counts.items())

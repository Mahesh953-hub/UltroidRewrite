"""Cookieless, PO-token-free audio stream resolution for voice-chat playback.

Why this exists
---------------
``vc/_youtube.py`` hardcoded ``"cookiefile": "cookies.txt"`` in all four of its
download paths, and ``vc/_yt.py`` proxied extraction through a third-party
Vercel endpoint. Both are wrong for a cookieless deploy: the first demands a
user-supplied cookie jar, the second hands every request to an external
service with no uptime guarantee.

The viable cookieless clients are not a matter of taste - they are read
straight out of yt-dlp's own PO-token policy table. In yt-dlp 2026.08.19:

    NO GVS PO TOKEN   : web_embedded, visionos, tv, tv_downgraded
    PO TOKEN REQUIRED : web, web_safari, web_music, web_creator,
                        android, android_vr, ios, mweb, tv_simply

Note this contradicts the older widely-copied advice that ``android``/``ios``
are the cookieless clients. In this version they hard-require a PO token, so
selecting them produces extraction failures rather than success.

SABR
----
YouTube now serves some clients SABR-only (UMP/protobuf framed). pytgcalls
hands the stream URL straight to ``ffmpeg.build_command``, and ffmpeg cannot
read SABR. :func:`is_sabr` detects and rejects those, because a URL that
cannot be demuxed is worthless no matter how it was obtained.

Fallback chain
--------------
Two independent implementations are tried in turn, so an upstream break in one
does not take the other down with it:

1. yt-dlp with a PO-token-free client
2. pytubefix with a PO-token-free client

Neither uses cookies or a PO token. The client order is module-level and
config-driven so it can be reordered without touching call sites.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

__all__ = [
    "YTDLP_CLIENTS",
    "PYTUBEFIX_CLIENTS",
    "STREAM_HEADERS",
    "AudioStream",
    "ExtractionError",
    "is_sabr",
    "resolve_audio",
    "ytdlp_resolve",
    "pytubefix_resolve",
]

LOGGER = logging.getLogger("vc.resolver")

#: yt-dlp clients that do NOT require a GVS PO token, best first.
#: Order matters: try the most complete format support first.
YTDLP_CLIENTS = ("tv", "visionos", "web_embedded", "tv_downgraded")

#: pytubefix client equivalents (different naming, same idea).
PYTUBEFIX_CLIENTS = ("TV", "VISION_OS", "TV_EMBED", "TV_DOWNGRADED")

#: googlevideo URLs 403 without matching headers on several code paths, and
#: these must be passed through to pytgcalls MediaStream(headers=...).
STREAM_HEADERS = {
    "User-Agent": "com.google.android.youtube/19.09.37 (Linux; U; Android 11) gzip",
}

#: SABR protocol marker. See the module docstring.
_SABR_MARKERS = ("sabr", "ump", "/sb/")


class ExtractionError(RuntimeError):
    """Every resolver in the chain failed for this URL."""


class AudioStream:
    """A resolved, ffmpeg-consumable audio stream."""

    __slots__ = ("url", "headers", "title", "duration", "source", "ext")

    def __init__(
        self,
        url: str,
        headers: dict[str, str],
        title: str = "",
        duration: int = 0,
        source: str = "",
        ext: str = "m4a",
    ) -> None:
        self.url = url
        self.headers = headers
        self.title = title
        self.duration = duration
        self.source = source
        self.ext = ext

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AudioStream {self.source} {self.duration}s {self.title!r}>"


def is_sabr(url: str) -> bool:
    """True if *url* looks like a SABR/UMP stream ffmpeg cannot demux."""
    if not url:
        return True
    low = url.lower()
    return any(m in low for m in _SABR_MARKERS)


def _pick_audio(info: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Choose the best plain-HTTPS audio format from a yt-dlp info dict.

    SABR formats are skipped. Prefer a real audio codec, then bitrate.
    """
    formats = info.get("formats") or []
    audio = [
        f
        for f in formats
        if f.get("url")
        and f.get("acodec") not in (None, "none")
        and not is_sabr(f["url"])
        and (f.get("protocol") or "https") in ("https", "http", None)
    ]
    if not audio:
        return None
    # Prefer muxed https audio, then higher bitrate.
    audio.sort(key=lambda f: (f.get("protocol") != "https", -(f.get("abr") or 0)))
    return audio[0]


def ytdlp_resolve(url: str, client: str) -> AudioStream:
    """Resolve *url* with yt-dlp using a single PO-token-free *client*."""
    import yt_dlp

    opts = {
        "format": "bestaudio[protocol^=http]/bestaudio/b",
        "extractor_args": {"youtube": {"player_client": [client]}},
        # Cookieless by contract: never consult or emit a cookie jar.
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "http_headers": dict(STREAM_HEADERS),
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)

    if not info:
        raise ExtractionError(f"yt-dlp returned no info for {url} (client={client})")

    # A single chosen format short-circuits into info['url']; otherwise pick
    # from the format list.
    if info.get("url") and not is_sabr(info["url"]):
        chosen = info
        stream_url = info["url"]
    else:
        chosen = _pick_audio(info)
        if not chosen:
            raise ExtractionError(
                f"no ffmpeg-readable audio format for {url} (client={client}); "
                f"SABR-only or token-gated"
            )
        stream_url = chosen["url"]

    headers = dict(STREAM_HEADERS)
    if chosen.get("http_headers"):
        headers.update(chosen["http_headers"])

    return AudioStream(
        url=stream_url,
        headers=headers,
        title=info.get("title") or "",
        duration=int(info.get("duration") or 0),
        source=f"yt-dlp:{client}",
        ext=chosen.get("ext") or "m4a",
    )


def pytubefix_resolve(url: str, client: str) -> AudioStream:
    """Resolve *url* with pytubefix using a single PO-token-free *client*."""
    from pytubefix import YouTube

    yt = YouTube(url, client=client)
    streams = yt.streams.filter(only_audio=True).order_by("abr").desc()
    if not streams:
        raise ExtractionError(f"pytubefix found no audio for {url} (client={client})")

    for stream in streams:
        # pytubefix 11 marks SABR streams; they are not ffmpeg-readable.
        if getattr(stream, "is_sabr", False):
            continue
        return AudioStream(
            url=stream.url,
            headers=dict(STREAM_HEADERS),
            title=getattr(yt, "title", "") or "",
            duration=int(getattr(stream, "length", 0) or 0),
            source=f"pytubefix:{client}",
            ext="m4a",
        )

    raise ExtractionError(f"pytubefix: all audio for {url} is SABR (client={client})")


async def resolve_audio(url: str) -> AudioStream:
    """Resolve a direct audio stream, trying each tier in order.

    Runs the blocking extractors in a worker thread so the event loop is not
    blocked by yt-dlp's network I/O.

    A TLS failure aborts the chain immediately. When the connection is being
    intercepted or blocked, *every* client fails the same way, so continuing
    just multiplies the wait - eight yt-dlp clients plus four pytubefix
    clients is roughly 90s of a guaranteed-equal outcome. Rotation only helps
    against content-level blocks (rate limits, geo), which are not transport
    errors.
    """
    from ._diagnose import FailureKind, classify_failure, summarise_attempts

    errors: list[str] = []

    tiers = (
        ("yt-dlp", YTDLP_CLIENTS, ytdlp_resolve),
        ("pytubefix", PYTUBEFIX_CLIENTS, pytubefix_resolve),
    )
    for name, clients, fn in tiers:
        for client in clients:
            try:
                return await asyncio.to_thread(fn, url, client)
            except Exception as exc:  # noqa: BLE001 - chain must try everything
                LOGGER.debug("%s client %s failed for %s: %s", name, client, url, exc)
                errors.append(f"{name}/{client}: {exc}")

            kind = classify_failure(errors[-1])
            if kind is FailureKind.TLS:
                # Transport-level and client-independent: stop now.
                raise ExtractionError(
                    "no cookieless audio stream for "
                    f"{url} - TLS connection to YouTube is intercepted or "
                    f"blocked ({client} failed). Tried so far: "
                    f"{summarise_attempts(errors)}"
                ) from None

    raise ExtractionError(
        f"no cookieless audio stream for {url}. Tried: " + " | ".join(errors)
    )

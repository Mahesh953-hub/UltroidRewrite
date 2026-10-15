"""Tests for the cookieless, PO-token-free audio resolver."""

from __future__ import annotations

import asyncio

import pytest

from vc._resolver import (
    PYTUBEFIX_CLIENTS,
    STREAM_HEADERS,
    YTDLP_CLIENTS,
    AudioStream,
    ExtractionError,
    is_sabr,
    pytubefix_resolve,
    resolve_audio,
    ytdlp_resolve,
)

REAL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# ── the client lists must match yt-dlp's own PO-token policy table ─────────


def test_no_configured_client_requires_a_po_token():
    """Every client we use must be PO-token-free in the installed yt-dlp.

    This is the whole point of the module: `android`/`ios` are the commonly
    cited cookieless clients but they hard-require a token in current yt-dlp.
    """
    from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS

    for client in YTDLP_CLIENTS:
        pol = INNERTUBE_CLIENTS[client].get("GVS_PO_TOKEN_POLICY")
        assert pol is not None, f"{client} has no policy entry"
        assert not any(p.required for p in pol.values()), (
            f"{client} requires a PO token - not cookieless"
        )


def test_client_lists_exclude_token_gated_clients():
    for bad in ("web", "web_safari", "android", "ios", "mweb", "web_music"):
        assert bad not in YTDLP_CLIENTS


def test_pytubefix_clients_are_known_names():
    for c in PYTUBEFIX_CLIENTS:
        assert c in ("TV", "VISION_OS", "TV_EMBED", "TV_DOWNGRADED", "TV_SIMPLY")


# ── SABR rejection ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url, sabr",
    [
        ("https://rr1---sn-x.googlevideo.com/videoplayback?id=1", False),
        ("https://rr3---sn-abc.googlevideo.com/videoplayback?expire=1", False),
        ("https://manifest.googlevideo.com/ump/1", True),
        ("https://sabr.googlevideo.com/x", True),
        ("https://x.googlevideo.com/sb/stream", True),
        ("", True),   # empty is never usable
        (None, True),  # type: ignore[arg-type]
    ],
)
def test_is_sabr(url, sabr):
    assert is_sabr(url) is sabr


def test_pick_audio_skips_sabr_formats():
    from vc._resolver import _pick_audio

    info = {
        "formats": [
            {"url": "https://x.googlevideo.com/ump/1", "acodec": "mp4a", "abr": 320},
            {"url": "https://x.googlevideo.com/videoplayback?a=1", "acodec": "mp4a", "abr": 128},
        ]
    }
    got = _pick_audio(info)
    assert got["url"].endswith("videoplayback?a=1")


def test_pick_audio_prefers_https_and_bitrate():
    from vc._resolver import _pick_audio

    info = {
        "formats": [
            {"url": "mms://x/a", "acodec": "mp4a", "abr": 256, "protocol": "m3u8_native"},
            {"url": "https://x/low", "acodec": "mp4a", "abr": 64, "protocol": "https"},
            {"url": "https://x/high", "acodec": "mp4a", "abr": 256, "protocol": "https"},
        ]
    }
    got = _pick_audio(info)
    assert got["url"] == "https://x/high"


def test_pick_audio_ignores_video_only():
    from vc._resolver import _pick_audio

    info = {"formats": [{"url": "https://x/v", "acodec": "none", "vcodec": "h264"}]}
    assert _pick_audio(info) is None


# ── resolver behaviour, with the network stubbed ───────────────────────────


def test_ytdlp_resolve_builds_cookieless_opts(monkeypatch):
    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {
                "title": "Song",
                "duration": 212,
                "url": "https://x.googlevideo.com/videoplayback?a=1",
                "http_headers": {"Referer": "https://youtube.com"},
            }

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    got = ytdlp_resolve(REAL, "tv")

    assert got.source == "yt-dlp:tv"
    assert got.title == "Song"
    assert got.duration == 212
    # The decisive assertions: no cookie jar, and a PO-token-free client.
    assert seen["cookiefile"] is None
    assert seen["cookiesfrombrowser"] is None
    assert seen["extractor_args"]["youtube"]["player_client"] == ["tv"]
    assert seen["http_headers"] == STREAM_HEADERS
    # Headers are merged so googlevideo does not 403 on playback.
    assert got.headers["Referer"] == "https://youtube.com"


def test_ytdlp_resolve_raises_on_sabr_only(monkeypatch):
    class FakeYDL:
        def __init__(self, opts):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {
                "title": "t",
                "url": "https://x.googlevideo.com/ump/1",
                "formats": [
                    {"url": "https://x.googlevideo.com/ump/2", "acodec": "mp4a"}
                ],
            }

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    with pytest.raises(ExtractionError, match="SABR"):
        ytdlp_resolve(REAL, "tv")


def test_pytubefix_resolve_skips_sabr(monkeypatch):
    class FakeStream:
        def __init__(self, url, abr, sabr):
            self.url, self. abr, self.is_sabr = url, abr, sabr
            self.length = 100

    class FakeYT:
        title = "PT"

        def __init__(self, url, client="TV"):
            self.client = client

        class _S:
            def filter(self, only_audio=True):
                return self

            def order_by(self, k):
                return self

            def desc(self):
                return self

            def __iter__(self):
                return iter(
                    [
                        FakeStream("https://x/sabr", 320, True),
                        FakeStream("https://x/good", 128, False),
                    ]
                )

        streams = _S()

    import pytubefix

    monkeypatch.setattr(pytubefix, "YouTube", FakeYT)
    got = pytubefix_resolve(REAL, "TV")
    assert got.url == "https://x/good"
    assert got.source == "pytubefix:TV"


def test_pytubefix_resolve_raises_when_all_sabr(monkeypatch):
    class FakeStream:
        url = "https://x/sabr"
        is_sabr = True
        length = 10

    class FakeYT:
        title = "PT"

        def __init__(self, url, client="TV"):
            pass

        class _S:
            def filter(self, only_audio=True):
                return self

            def order_by(self, k):
                return self

            def desc(self):
                return self

            def __iter__(self):
                return iter([FakeStream()])

        streams = _S()

    import pytubefix

    monkeypatch.setattr(pytubefix, "YouTube", FakeYT)
    with pytest.raises(ExtractionError, match="SABR"):
        pytubefix_resolve(REAL, "TV")


# ── the fallback chain ─────────────────────────────────────────────────────


def test_resolve_falls_back_across_the_chain(monkeypatch):
    calls = []

    def fake_ytdlp(url, client):
        calls.append(("ytdlp", client))
        if client != YTDLP_CLIENTS[1]:
            raise RuntimeError("blocked")
        return AudioStream("https://ok/1", {}, source="yt-dlp")

    def fake_pt(url, client):
        calls.append(("pytubefix", client))
        raise RuntimeError("also blocked")

    monkeypatch.setattr("vc._resolver.ytdlp_resolve", fake_ytdlp)
    monkeypatch.setattr("vc._resolver.pytubefix_resolve", fake_pt)

    got = asyncio.run(resolve_audio(REAL))
    assert got.url == "https://ok/1"
    # yt-dlp tried two clients before succeeding; pytubefix was never reached.
    assert calls == [("ytdlp", YTDLP_CLIENTS[0]), ("ytdlp", YTDLP_CLIENTS[1])]


def test_resolve_falls_through_to_pytubefix(monkeypatch):
    def fake_ytdlp(url, client):
        raise RuntimeError("gvs token required")

    def fake_pt(url, client):
        return AudioStream("https://pt/1", {}, source="pytubefix")

    monkeypatch.setattr("vc._resolver.ytdlp_resolve", fake_ytdlp)
    monkeypatch.setattr("vc._resolver.pytubefix_resolve", fake_pt)

    got = asyncio.run(resolve_audio(REAL))
    assert got.source == "pytubefix"


def test_resolve_raises_after_exhausting_the_chain(monkeypatch):
    monkeypatch.setattr(
        "vc._resolver.ytdlp_resolve", lambda u, c: (_ for _ in ()).throw(RuntimeError("x"))
    )
    monkeypatch.setattr(
        "vc._resolver.pytubefix_resolve", lambda u, c: (_ for _ in ()).throw(RuntimeError("y"))
    )
    with pytest.raises(ExtractionError) as ei:
        asyncio.run(resolve_audio(REAL))
    # The error names every attempt, so a failure is diagnosable.
    assert "yt-dlp" in str(ei.value) and "pytubefix" in str(ei.value)


def test_resolve_does_not_block_the_event_loop(monkeypatch):
    """Extractors run in a worker thread; the loop must stay responsive."""
    import threading

    main = threading.current_thread().ident

    def fake_ytdlp(url, client):
        assert threading.current_thread().ident != main
        return AudioStream("https://ok/1", {})

    monkeypatch.setattr("vc._resolver.ytdlp_resolve", fake_ytdlp)
    got = asyncio.run(resolve_audio(REAL))
    assert got.url == "https://ok/1"


def test_tls_failure_aborts_the_client_chain(monkeypatch):
    """A transport-level failure must not be retried across every client.

    When TLS is intercepted every client fails identically, so continuing
    multiplies a ~90s wait for a guaranteed-equal result. The chain must
    stop after the first one.
    """
    calls = []

    def _tls_fail(u, c):
        calls.append(c)
        raise RuntimeError(
            "Unable to download API page: [SSL: "
            "UNEXPECTED_EOF_WHILE_READING]"
        )

    monkeypatch.setattr("vc._resolver.ytdlp_resolve", _tls_fail)
    monkeypatch.setattr("vc._resolver.pytubefix_resolve", _tls_fail)

    with pytest.raises(ExtractionError) as ei:
        asyncio.run(resolve_audio(REAL))

    assert len(calls) == 1, f"tried {len(calls)} clients: {calls}"
    assert "TLS" in str(ei.value) or "intercepted" in str(ei.value)


def test_content_level_failure_still_rotates(monkeypatch):
    """A non-transport failure must still try every client.

    Rate limits and geo blocks are client-dependent, so rotation remains
    the right strategy there; the fast-fail must not over-trigger.
    """
    calls = []

    def _throttled(u, c):
        calls.append(c)
        raise RuntimeError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr("vc._resolver.ytdlp_resolve", _throttled)
    monkeypatch.setattr("vc._resolver.pytubefix_resolve", _throttled)

    with pytest.raises(ExtractionError):
        asyncio.run(resolve_audio(REAL))

    from vc._resolver import PYTUBEFIX_CLIENTS, YTDLP_CLIENTS

    assert len(calls) == len(YTDLP_CLIENTS) + len(PYTUBEFIX_CLIENTS)

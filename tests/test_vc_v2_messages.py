"""Tests for the v2 message layer, query classification, and failure diagnosis.

The v2 escaping rules are verified against the *real* parser from the
installed Telethon fork rather than against a hand-written model, because
the details that matter here (which characters get dropped, whether
backslash-escaping survives) are implementation-specific and were not
obvious. A test that asserted our own escaper against our own expectations
would pass even if both were wrong.
"""

from __future__ import annotations

import pytest
from telethon.extensions.markdownv2 import Markdown as V2Parser

from vc._diagnose import (
    FailureKind,
    classify_failure,
    diagnose,
    summarise_attempts,
)
from vc._messages import (
    PARSEMODE,
    esc,
    fmt_duration,
    loop_msg,
    now_playing_msg,
    queue_list_msg,
    status_msg,
)
from vc._dataclass import CachedTrack, ChannelPlay
from vc._player import PlayerState, Track
from vc._query import QueryKind, classify, status_head, status_rows

PARSER = V2Parser()


def _rendered(text: str) -> str:
    """What Telegram will actually show for *text* under v2."""
    return PARSER.parse(text)[0]


# ── parse mode identity ────────────────────────────────────────────────────


def test_v2_maps_to_the_fork_parser():
    """'v2' must be the fork's markdownv2 parser, not upstream Telethon."""
    import telethon.utils as tu
    from telethon.extensions import markdownv2

    assert PARSEMODE == "v2"
    # Resolve through the real client path rather than asserting on a
    # constant, so this fails if the fork ever re-points 'v2' at another
    # parser (e.g. markdownv3) with different escaping rules.
    assert type(tu.sanitize_parse_mode(PARSEMODE)) is markdownv2.Markdown


# ── escaping ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "Tom & Jerry",
        "a <live> b",
        "back`tick",
        "**bold**",
        "_under_score_",
        "~strike~",
        "|spoiler|",
        "[link]",
        "> quote",
        "10 > 20 & 30 < 40",
    ],
)
def test_hostile_text_survives_the_parser(raw):
    """Every character the v2 parser eats must be entity-escaped.

    This is the regression that motivated the whole v2 migration: the old
    HTML path rendered ``Tom & Jerry "Theme" <live>`` as gibberish, and a
    naive port to v2 silently *deletes* the ``<live>`` instead.
    """
    assert _rendered(esc(raw)) == raw


def test_unescaped_text_would_be_lost():
    """Guard the premise: without esc(), the parser really does drop text.

    If a future fork changes this, esc() becomes merely harmless rather than
    necessary, and this test tells us the rules can be revisited.
    """
    assert _rendered("a <live> b") != "a <live> b"
    assert _rendered("back`tick") != "back`tick"


def test_esc_leaves_ordinary_text_alone():
    plain = "Never Gonna Give You Up"
    assert esc(plain) == plain


def test_esc_is_idempotent_in_effect():
    """Escaping already-escaped text must not double-encode the value."""
    once = esc("A & B")
    assert _rendered(once) == "A & B"
    # A literal '&amp;' in a title is data, not markup.
    assert _rendered(esc("A &amp; B")) == "A &amp; B"


def test_esc_handles_none_and_limits():
    assert esc(None) == ""
    assert len(esc("x" * 50, limit=10)) == 10
    assert esc("x" * 50, limit=10).endswith("…")


def test_markup_still_works_alongside_escaped_values():
    """Escaping must not disable the parser's own formatting."""
    out = _rendered(status_msg("Now Playing", {"Song": "A & B"}))
    assert "Now Playing" in out
    assert "A & B" in out


def test_user_data_cannot_inject_formatting():
    """A hostile title must not be able to open a bold span.

    The value sits inside a ``code`` span, so ``**pwned**`` correctly
    renders as literal asterisks. What must not happen is a Bold entity
    covering it, which is what an unescaped raw title in a non-code
    context would produce.
    """
    text, entities = PARSER.parse(now_playing_msg(_track(title="**pwned**")))
    assert "pwned" in text

    # Our own head is legitimately bold. What matters is that no Bold entity
    # overlaps the title's own span, which is what an unescaped raw title
    # would produce.
    start = text.index("pwned")
    end = start + len("**pwned**")
    for e in entities:
        if type(e).__name__ != "MessageEntityBold":
            continue
        assert e.offset >= end or e.offset + e.length <= start, (
            "hostile title opened a bold span over its own text"
        )


def test_leading_gt_does_not_open_a_blockquote():
    """A line-leading '>' must not become a quote delimiter."""
    text, entities = PARSER.parse(esc("> quote"))
    assert text == "> quote"
    assert not [e for e in entities if type(e).__name__ == "MessageEntityBlockquote"]


def test_multiline_leading_gt_is_handled_per_line():
    text, entities = PARSER.parse(esc("a\n> quoted line"))
    assert "> quoted line" in text
    assert not [e for e in entities if type(e).__name__ == "MessageEntityBlockquote"]


# ── durations ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "secs,expected",
    [
        (0, "LIVE"),
        (None, "LIVE"),
        (5, "0:05"),
        (65, "1:05"),
        (605, "10:05"),
        (3600, "1:00:00"),
        (3725, "1:02:05"),
    ],
)
def test_fmt_duration(secs, expected):
    assert fmt_duration(secs) == expected


def test_fmt_duration_tolerates_junk():
    assert fmt_duration("not a number") == "LIVE"


# ── query classification ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query,kind",
    [
        ("dQw4w9WgXcQ", QueryKind.VIDEO_ID),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", QueryKind.URL),
        ("https://youtu.be/dQw4w9WgXcQ", QueryKind.URL),
        ("https://youtube.com/shorts/dQw4w9WgXcQ", QueryKind.URL),
        ("http://soundcloud.com/x/y", QueryKind.URL),
        # Real-world variants that must not fall through to SEARCH.
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s", QueryKind.URL),
        ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", QueryKind.URL),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ", QueryKind.URL),
        ("https://www.youtube.com/watch?feature=x&v=dQw4w9WgXcQ", QueryKind.URL),
        ("https://www.youtube.com/@someartist", QueryKind.URL),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", QueryKind.URL),
        ("never gonna give you up", QueryKind.SEARCH),
        ("rihanna umbrella", QueryKind.SEARCH),
        ("", QueryKind.SEARCH),
        # Exactly 11 chars required; these must not be mistaken for an id.
        ("short", QueryKind.SEARCH),
        ("waaaaaaaaaaytoolong", QueryKind.SEARCH),
    ],
)
def test_classify(query, kind):
    assert classify(query) == kind


def test_status_head_reflects_the_query_kind():
    assert "Searching" in status_head(QueryKind.SEARCH, "some song")
    assert "Searching" in status_head(QueryKind.SEARCH, "some song")
    assert "Fetching track" in status_head(QueryKind.VIDEO_ID, "dQw4w9WgXcQ")
    assert "Fetching link" in status_head(
        QueryKind.URL, "https://youtu.be/dQw4w9WgXcQ"
    )


def test_status_rows_exist_for_every_kind():
    for kind in QueryKind:
        assert status_rows(kind)


# ── failure diagnosis ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,kind",
    [
        ("SSL: UNEXPECTED_EOF_WHILE_READING", FailureKind.TLS),
        ("SSLError(EOF occurred in violation of protocol)", FailureKind.TLS),
        ("CERTIFICATE_VERIFY_FAILED", FailureKind.TLS),
        ("HTTP Error 429: Too Many Requests", FailureKind.RATE_LIMITED),
        ("Video unavailable. This video is not available", FailureKind.UNAVAILABLE),
        ("No video formats found", FailureKind.NO_FORMAT),
        ("getaddrinfo failed: Name or service not known", FailureKind.NETWORK),
        ("", FailureKind.UNKNOWN),
    ],
)
def test_classify_failure(text, kind):
    assert classify_failure(text) is kind


def test_tls_beats_network_markers():
    """A TLS error also contains network-ish text; TLS must win."""
    text = (
        "Unable to download API page: [SSL: UNEXPECTED_EOF_WHILE_READING] "
        "EOF occurred in violation of protocol"
    )
    assert classify_failure(text) is FailureKind.TLS


def test_diagnose_tls_keeps_one_error_not_eight():
    """Repeated identical transport failures collapse to a single cause."""
    err = "yt-dlp/tv: [SSL: UNEXPECTED_EOF_WHILE_READING]"
    errors = [f"yt-dlp/{c}: [SSL: UNEXPECTED_EOF_WHILE_READING]" for c in
              ("tv", "visionos", "web_embedded", "tv_downgraded")]
    headline, detail, kind = diagnose(errors)
    assert kind is FailureKind.TLS
    assert "TLS" in headline
    # The advice must appear once, not four times.
    assert detail.count("intercepted or blocked") <= 1
    assert "cookie" in detail.lower()
    assert len(detail) < 900


def test_diagnose_unknown_still_returns_text():
    headline, detail, kind = diagnose(["something entirely novel happened"])
    assert kind is FailureKind.UNKNOWN
    assert headline and "novel" in detail


def test_summarise_attempts_counts_by_kind():
    s = summarise_attempts(
        ["[SSL: UNEXPECTED_EOF_WHILE_READING]", "HTTP Error 429"]
    )
    assert "tls" in s and "rate_limited" in s


# ── message shape ──────────────────────────────────────────────────────────


def _track(**kw) -> Track:
    """Build a real Track from a CachedTrack, as the player does."""
    base = dict(name="Song", artist="Artist", user="You", duration=200)
    if "title" in kw:
        base["name"] = kw.pop("title")
    if "requester" in kw:
        base["user"] = kw.pop("requester")
    assert not kw, f"_track got unknown kwargs: {sorted(kw)}"
    base.update(kw)
    return Track(
        CachedTrack(
            url="https://youtu.be/x",
            loop=0,
            file_path="",
            thumbnail="",
            track_id="x",
            is_video=False,
            platform="youtube",
            channel=ChannelPlay(chat_id=1, is_channel=False),
            **base,
        )
    )


def test_now_playing_reports_position():
    st = PlayerState()
    st.queue.add(_track())
    st.start()
    out = _rendered(now_playing_msg(st.current, st))
    assert "Now Playing" in out
    assert "Song" in out and "Artist" in out


def test_queue_list_survives_hostile_titles():
    st = PlayerState()
    st.queue.add(_track(title="A & B <live>"))
    st.queue.add(_track(title="Second"))
    out = _rendered(queue_list_msg(st))
    assert "A & B <live>" in out
    assert "Second" in out


def test_queue_list_empty_is_nothing_playing():
    out = _rendered(queue_list_msg(PlayerState()))
    assert "Nothing Playing" in out


def test_loop_msg_off_and_on():
    st = PlayerState()
    st.queue.add(_track())
    assert "Loop Off" in _rendered(loop_msg(st.current, 0))
    assert "Looping 3x" in _rendered(loop_msg(st.current, 3))


# ── single-escape contract ─────────────────────────────────────────────────


def test_values_must_be_escaped_exactly_once():
    """Escaping twice corrupts the value.

    The message layer escapes every value. Any caller that pre-escapes - as
    the requester display name briefly did - yields "Tom &amp; Jerry" in the
    final message.
    """
    assert _rendered(esc("Tom & Jerry")) == "Tom & Jerry"
    assert _rendered(esc(esc("Tom & Jerry"))) != "Tom & Jerry"


def test_now_playing_does_not_double_escape_the_requester():
    """A display name with an ampersand must survive into the notice."""
    t = _track(requester="Tom & Jerry")
    out = _rendered(now_playing_msg(t))
    assert "Tom & Jerry" in out
    assert "&amp;" not in out


# ── review findings ────────────────────────────────────────────────────────


def test_queue_artist_is_escaped_like_the_title():
    """Review finding: the artist was interpolated raw in the queue line.

    The title went through esc() but the artist did not, so a name with a
    backtick lost it while the title survived - the parser is lossy, so this
    was a silent corruption rather than a visible error.
    """
    from vc._dataclass import CachedTrack, ChannelPlay
    from vc._player import PlayerState, Track

    nasty = "Tom & Jerry <live> `x`"
    st = PlayerState()
    st.queue.add(
        Track(
            CachedTrack(
                url="u", name="Song", artist=nasty, loop=0, user="me",
                file_path="", thumbnail="", track_id="t", duration=100,
                is_video=False, platform="youtube",
                channel=ChannelPlay(chat_id=1, is_channel=False),
            )
        )
    )
    rendered = _rendered(queue_list_msg(st))
    assert nasty in rendered, "artist was mangled by the v2 parser"
    assert "&amp;" not in rendered


def test_ydl_extract_records_the_failure_reason():
    """Review finding: extraction errors were swallowed into None.

    With TLS interception the user was told 'No results' and invited to try a
    different query when the real cause was the network.
    """
    from vc._youtube import YouTubeData

    class _Boom:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            raise RuntimeError(
                "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation "
                "of protocol"
            )

    import yt_dlp

    original = yt_dlp.YoutubeDL
    yt_dlp.YoutubeDL = lambda *a, **k: _Boom()
    try:
        assert YouTubeData._ydl_extract("https://youtu.be/x") is None
    finally:
        yt_dlp.YoutubeDL = original

    assert "SSL" in YouTubeData.last_extract_error
    from vc._diagnose import FailureKind, classify_failure
    assert classify_failure(YouTubeData.last_extract_error) is FailureKind.TLS


def test_successful_extract_clears_the_recorded_error():
    from vc._youtube import YouTubeData

    class _OK:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"id": "x", "title": "T"}

    import yt_dlp

    YouTubeData.last_extract_error = "stale"
    original = yt_dlp.YoutubeDL
    yt_dlp.YoutubeDL = lambda *a, **k: _OK()
    try:
        assert YouTubeData._ydl_extract("https://youtu.be/x") is not None
    finally:
        yt_dlp.YoutubeDL = original
    assert YouTubeData.last_extract_error == ""

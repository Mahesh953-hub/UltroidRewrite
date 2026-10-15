"""End-to-end command coverage for the voice-chat player.

These drive the real handlers in ``plugins/vcmusic.py`` against a fake event
and the real :class:`vc._player` state, so they assert the wiring rather than
the formatter: enqueue, start-if-idle, skip, pause/resume, loop, clear.

The module is loaded as a stub package (like ``test_brain_sdk_commands.py``)
because ``plugins/__init__.py`` pulls in the whole bot runtime.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── loader ─────────────────────────────────────────────────────────────────


def load_vcmusic():
    """Import plugins/vcmusic.py with the bot runtime stubbed out."""
    plugins_pkg = types.ModuleType("plugins")
    plugins_pkg.__path__ = [str(ROOT / "plugins")]
    plugins_pkg.get_string = lambda key: key
    registered: list[tuple[str, object]] = []
    plugins_pkg.ultroid_cmd = lambda **kw: (
        registered.append((kw.get("pattern"), kw)) or (lambda f: f)
    )
    sys.modules["plugins"] = plugins_pkg

    spec = importlib.util.spec_from_file_location(
        "plugins.vcmusic", ROOT / "plugins" / "vcmusic.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugins.vcmusic"] = module
    spec.loader.exec_module(module)
    return module, registered


@pytest.fixture()
def vcm():
    mod, registered = load_vcmusic()
    return mod, registered


# ── fakes ──────────────────────────────────────────────────────────────────


class FakeGroup:
    def __init__(self, has_call=True):
        self.full_chat = SimpleNamespace(call=1 if has_call else None)


class FakeEvent:
    def __init__(self, query="", chat_id=-100123, has_call=True):
        self.pattern_match = SimpleNamespace(group=lambda i: query)
        self.text = f".vplay {query}".strip()
        self.chat_id = chat_id
        self.is_channel = False
        self.id = 4242  # the command message, replied to by the status notice
        self.sender_id = 42
        self.replies: list[str] = []
        self.parse_modes: list = []
        self.status_edits: list = []
        self._has_call = has_call

        class _Client:
            async def __call__(self, request, *a, **kw):
                # A GetFullChannel request is passed in; answer with a group
                # whose `call` field reflects whether a group call exists.
                return FakeGroup(has_call)

        class _Msg:
            """Records the edits vplay makes to its status message."""

            def __init__(self, owner):
                self._owner = owner
                self.edits = []

            async def edit(self, text, **kw):
                self.edits.append((text, kw.get("parse_mode")))
                return self

        self.client = _Client()
        self.status_edits = []
        self.sent_to: list = []

        async def _send(chat_id, text, **kw):
            msg = _Msg(self)
            msg.edits.append((text, kw.get("parse_mode")))
            self.status_edits.append(msg)
            self.replies.append(text)
            self.sent_to.append(
                {
                    "chat_id": chat_id,
                    "reply_to": kw.get("reply_to"),
                    "parse_mode": kw.get("parse_mode"),
                    "text": text,
                }
            )
            return msg

        self.client.send_message = _send

    def get_display_name(self):
        return "Tester & Co <admin>"

    async def eor(self, text, **kw):
        self.replies.append(text)
        self.parse_modes.append(kw.get("parse_mode"))
        return self

    async def eod(self, text, **kw):
        self.replies.append(text)
        self.parse_modes.append(kw.get("parse_mode"))
        return self

    @property
    def last(self):
        return self.replies[-1] if self.replies else ""

    @property
    def last_parsemode(self):
        return self.parse_modes[-1] if self.parse_modes else None


def make_cached(name="Song", artist="Artist", duration=210):
    from vc._dataclass import CachedTrack, ChannelPlay

    return CachedTrack(
        url="https://youtu.be/abcdefghijk",
        name=name,
        artist=artist,
        loop=0,
        user="Tester",
        file_path="",
        thumbnail="",
        track_id="abcdefghijk",
        duration=duration,
        is_video=False,
        platform="youtube",
        channel=ChannelPlay(chat_id=-100123, is_channel=False),
    )


@pytest.fixture(autouse=True)
def clean_players(vcm):
    """Every test starts with an empty registry."""
    mod, _ = vcm
    from vc._pytgcalls import call

    call.players.clear()
    yield
    call.players.clear()


# ── registration ───────────────────────────────────────────────────────────


def test_all_commands_register(vcm):
    _, registered = vcm
    patterns = [p for p, _ in registered]
    assert len(patterns) == 14
    for expected in (
        "vplay(?: |$)(.*)",
        "vskip$",
        "vpause$",
        "vresume$",
        "vqueue$",
        "vnow$",
        "vclear$",
        r"vloop(?: |$)(.*)",
        r"vcmute$",
        r"vcunmute$",
    ):
        assert expected in patterns


def test_group_call_commands_register(vcm):
    _, registered = vcm
    patterns = {p for p, _ in registered}
    assert {"startvc$", "stopvc$", "vctitle(?: |$)(.*)", "vcinvite$"} <= patterns


def test_old_vctools_no_longer_duplicates_commands():
    """vctools.py must not re-register the same handlers."""
    src = (ROOT / "plugins" / "vctools.py").read_text()
    assert "@ultroid_cmd" not in src


def test_playback_commands_are_groups_only(vcm):
    """VC commands make no sense in PM and must be gated."""
    _, registered = vcm
    by_pattern = dict(registered)
    for p in ("vplay(?: |$)(.*)", "vskip$", "vqueue$", "vclear$"):
        assert by_pattern[p].get("groups_only") is True


# ── vplay ──────────────────────────────────────────────────────────────────


def test_vplay_starts_playback_when_idle(vcm, monkeypatch):
    mod, _ = vcm
    from vc._pytgcalls import call

    played: list = []
    monkeypatch.setattr(mod, "_ensure_call", _ok_ensure)
    monkeypatch.setattr(mod, "_resolve_to_track", _ok_track)
    monkeypatch.setattr(
        type(call), "_play_song", _record(played), raising=False
    )

    e = FakeEvent("never gonna give you up")
    asyncio.run(_run(mod, e, "vplay"))

    player = call.players.get(e.chat_id)
    assert player.is_playing
    assert player.current.title == "Song"
    assert played, "playback was never started"


def test_vplay_queues_when_already_playing(vcm, monkeypatch):
    mod, _ = vcm
    from vc._pytgcalls import call

    played: list = []
    monkeypatch.setattr(mod, "_ensure_call", _ok_ensure)
    monkeypatch.setattr(mod, "_resolve_to_track", _ok_track)
    monkeypatch.setattr(type(call), "_play_song", _record(played), raising=False)

    e = FakeEvent("first")
    asyncio.run(_run(mod, e, "vplay"))
    played.clear()
    asyncio.run(_run(mod, FakeEvent("second"), "vplay"))

    player = call.players.get(e.chat_id)
    assert len(player.queue) == 2
    assert player.current.title == "Song"  # head unchanged
    assert not played, "must not restart playback when already playing"


def test_vplay_without_query_reports_usage(vcm, monkeypatch):
    mod, _ = vcm
    monkeypatch.setattr(mod, "_ensure_call", _ok_ensure)
    e = FakeEvent("")
    asyncio.run(_run(mod, e, "vplay"))
    assert "Usage" in e.last


def test_vplay_starts_a_call_when_none_exists(vcm, monkeypatch):
    mod, _ = vcm
    from vc._pytgcalls import call

    monkeypatch.setattr(mod, "_resolve_to_track", _ok_track)
    monkeypatch.setattr(type(call), "_play_song", _record([]), raising=False)

    e = FakeEvent("q", has_call=False)
    # getchat must be faked too; the real one hits the network.
    started: list = []

    def _fake_startvc(chat_id):
        started.append(chat_id)
        return SimpleNamespace()  # a request object the fake client accepts

    monkeypatch.setattr(mod, "startvc", _fake_startvc)
    asyncio.run(_run(mod, e, "vplay"))
    assert started, "a group call should be started when none exists"


def test_vplay_reports_resolution_failure(vcm, monkeypatch):
    mod, _ = vcm

    async def _fail(e, q, status=None):
        await e.eor("🔍 No results for x")
        return None

    monkeypatch.setattr(mod, "_ensure_call", _ok_ensure)
    monkeypatch.setattr(mod, "_resolve_to_track", _fail)
    e = FakeEvent("zzzz")
    asyncio.run(_run(mod, e, "vplay"))
    assert "No results" in e.last


# ── transport ──────────────────────────────────────────────────────────────


def test_vskip_advances_the_queue(vcm, monkeypatch):
    mod, _ = vcm
    from vc._pytgcalls import call

    monkeypatch.setattr(type(call), "_play_song", _record([]), raising=False)
    monkeypatch.setattr(type(call), "_handle_no_songs", _record([]), raising=False)

    chat_id = -100999
    from vc._player import track_from_cached

    p = call.players.get(chat_id)
    p.queue.add(track_from_cached(make_cached("A")))
    p.queue.add(track_from_cached(make_cached("B")))
    p.start()

    e = FakeEvent("", chat_id=chat_id)
    asyncio.run(_run(mod, e, "vskip"))
    assert p.current.title == "B"


def test_vskip_on_empty_queue_reports_nothing_playing(vcm):
    mod, _ = vcm
    e = FakeEvent("", chat_id=-100888)
    asyncio.run(_run(mod, e, "vskip"))
    assert "Nothing Playing" in e.last


def test_vpause_and_resume_flip_state(vcm, monkeypatch):
    mod, _ = vcm
    from vc._pytgcalls import call
    from vc._player import track_from_cached

    chat_id = -100777
    p = call.players.get(chat_id)
    p.queue.add(track_from_cached(make_cached()))
    p.start()

    monkeypatch.setattr(type(call), "pause", _ok_call, raising=False)
    monkeypatch.setattr(type(call), "resume", _ok_call, raising=False)

    asyncio.run(_run(mod, FakeEvent("", chat_id=chat_id), "vpause"))
    assert p.is_paused

    asyncio.run(_run(mod, FakeEvent("", chat_id=chat_id), "vresume"))
    assert p.is_playing


def test_vpause_when_not_playing_is_reported(vcm):
    mod, _ = vcm
    e = FakeEvent("", chat_id=-100666)
    asyncio.run(_run(mod, e, "vpause"))
    assert "Nothing Playing" in e.last


# ── queue control ──────────────────────────────────────────────────────────


def test_vqueue_lists_tracks(vcm):
    mod, _ = vcm
    from vc._pytgcalls import call
    from vc._player import track_from_cached

    chat_id = -100555
    p = call.players.get(chat_id)
    p.queue.add(track_from_cached(make_cached("A", duration=100)))
    p.queue.add(track_from_cached(make_cached("B", duration=200)))
    p.start()

    e = FakeEvent("", chat_id=chat_id)
    asyncio.run(_run(mod, e, "vqueue"))
    assert "A" in e.last and "B" in e.last


def test_vnow_shows_elapsed(vcm):
    mod, _ = vcm
    from vc._pytgcalls import call
    from vc._player import track_from_cached

    chat_id = -100444
    p = call.players.get(chat_id)
    p.queue.add(track_from_cached(make_cached("Now", duration=100)))
    p.start(now=100.0)

    e = FakeEvent("", chat_id=chat_id)
    asyncio.run(_run(mod, e, "vnow"))
    assert "Now" in e.last


def test_vloop_sets_and_clears(vcm):
    mod, _ = vcm
    from vc._pytgcalls import call
    from vc._player import track_from_cached

    chat_id = -100333
    p = call.players.get(chat_id)
    p.queue.add(track_from_cached(make_cached()))
    p.start()

    asyncio.run(_run(mod, FakeEvent("3", chat_id=chat_id), "vloop"))
    assert p.loop == 3

    asyncio.run(_run(mod, FakeEvent("off", chat_id=chat_id), "vloop"))
    assert p.loop == 0


def test_vloop_rejects_nonsense(vcm):
    mod, _ = vcm
    from vc._pytgcalls import call
    from vc._player import track_from_cached

    chat_id = -100222
    p = call.players.get(chat_id)
    p.queue.add(track_from_cached(make_cached()))
    p.start()
    e = FakeEvent("banana", chat_id=chat_id)
    asyncio.run(_run(mod, e, "vloop"))
    assert "Usage" in e.last
    assert p.loop == 0


def test_vclear_empties_and_ends_call(vcm, monkeypatch):
    mod, _ = vcm
    from vc._pytgcalls import call
    from vc._player import track_from_cached

    ended: list = []
    monkeypatch.setattr(type(call), "end", _record(ended), raising=False)

    chat_id = -100111
    p = call.players.get(chat_id)
    p.queue.add(track_from_cached(make_cached("A")))
    p.queue.add(track_from_cached(make_cached("B")))
    p.start()

    e = FakeEvent("", chat_id=chat_id)
    asyncio.run(_run(mod, e, "vclear"))
    assert len(p.queue) == 0
    assert p.is_stopped
    assert ended, "the group call should be torn down"


# ── escaping in real handler output ────────────────────────────────────────


def test_queued_reply_escapes_hostile_titles(vcm, monkeypatch):
    mod, _ = vcm
    from vc._pytgcalls import call
    from vc._player import track_from_cached

    NASTY = 'Tom & Jerry "Theme" <live>'
    monkeypatch.setattr(type(call), "_play_song", _record([]), raising=False)

    chat_id = -100000
    p = call.players.get(chat_id)
    p.queue.add(track_from_cached(make_cached(NASTY)))
    p.start()

    async def _track(e, q, status=None):
        return make_cached(NASTY)

    monkeypatch.setattr(mod, "_ensure_call", _ok_ensure)
    monkeypatch.setattr(mod, "_resolve_to_track", _track)
    e = FakeEvent("x", chat_id=chat_id)
    asyncio.run(_run(mod, e, "vplay"))

    # The queued reply is v2 markup carrying an entity-escaped title, and
    # the title survives the real parser intact.
    from telethon.extensions.markdownv2 import Markdown as V2

    assert e.last_parsemode == "v2"
    rendered, _ = V2().parse(e.last)
    assert NASTY in rendered


# ── helpers ────────────────────────────────────────────────────────────────


def _ok_ensure(e):
    async def _inner():
        return True

    return _inner()


def _ok_track(e, q, status=None):
    async def _inner():
        return make_cached()

    return _inner()


def _ok_call(*a, **kw):
    async def _inner(*a2, **kw2):
        from vc._pytgcalls import Ok

        return Ok()

    return _inner()


def _record(sink):
    async def _inner(*a, **kw):
        sink.append((a, kw))
        return None

    return _inner


def _run(mod, event, which):
    """Invoke the real handler for `which` (handlers are named, not `_`)."""
    return getattr(mod, which)(event)


# ── .vplay status message ──────────────────────────────────────────────────


def _prep(mod, monkeypatch, track=None):
    """Wire vplay for a successful resolve; returns the played-sink."""
    played = []
    monkeypatch.setattr(mod, "_ensure_call", _ok_ensure)
    monkeypatch.setattr(mod, "_resolve_to_track", track or _ok_track)
    monkeypatch.setattr(
        type(mod.call), "_play_song", _record(played), raising=False
    )
    return played


def test_vplay_announces_searching_for_free_text(vcm, monkeypatch):
    mod, _ = vcm
    _prep(mod, monkeypatch)
    e = FakeEvent("never gonna give you up")
    asyncio.run(_run(mod, e, "vplay"))
    assert "Searching" in e.replies[0]
    assert "never gonna give you up" in e.replies[0]


def test_vplay_announces_fetching_for_a_bare_id(vcm, monkeypatch):
    """A bare id skips the search step, so the wording must differ."""
    mod, _ = vcm
    _prep(mod, monkeypatch)
    e = FakeEvent("dQw4w9WgXcQ")
    asyncio.run(_run(mod, e, "vplay"))
    assert "Fetching" in e.replies[0]
    assert "Searching" not in e.replies[0]


def test_vplay_announces_fetching_for_a_link(vcm, monkeypatch):
    mod, _ = vcm
    _prep(mod, monkeypatch)
    e = FakeEvent("https://youtu.be/dQw4w9WgXcQ")
    asyncio.run(_run(mod, e, "vplay"))
    assert "Fetching" in e.replies[0]
    assert "link" in e.replies[0].lower()


def test_vplay_status_is_sent_before_resolution(vcm, monkeypatch):
    """The status must reach the user before the slow work starts.

    Asserted by ordering, not by timing: if the status were sent after
    resolution, it would not be the first reply.
    """
    mod, _ = vcm
    order = []

    async def _slow(e, q, status=None):
        order.append("resolved")
        return make_cached()

    async def _ensure(e):
        return True

    monkeypatch.setattr(mod, "_ensure_call", _ensure)
    monkeypatch.setattr(mod, "_resolve_to_track", _slow)
    monkeypatch.setattr(mod, "_start_current", _no_start)
    monkeypatch.setattr(
        type(mod.call), "_play_song", _record([]), raising=False
    )

    e = FakeEvent("some query")
    asyncio.run(_run(mod, e, "vplay"))
    assert e.replies, "no status message was sent"
    assert order == ["resolved"]
    assert "Searching" in e.replies[0]


async def _no_start(e, chat_id):
    return None


def test_vplay_status_uses_v2_parse_mode(vcm, monkeypatch):
    mod, _ = vcm
    _prep(mod, monkeypatch)
    e = FakeEvent("some query")
    asyncio.run(_run(mod, e, "vplay"))
    # Sent through the client (not eor) so it lands before resolution.
    assert e.sent_to[0]["parse_mode"] == "v2"


def test_vplay_replies_to_the_command(vcm, monkeypatch):
    mod, _ = vcm
    _prep(mod, monkeypatch)
    e = FakeEvent("some query")
    asyncio.run(_run(mod, e, "vplay"))
    assert e.sent_to[-1]["reply_to"] == e.id


def test_vplay_tls_failure_is_reported_not_swallowed(vcm, monkeypatch):
    """A TLS-interception failure must reach the user as a diagnosis."""
    mod, _ = vcm
    mod._resolve_to_track = mod._resolve_to_track  # real function
    monkeypatch.setattr(mod, "_ensure_call", _ok_ensure)

    class _Boom:
        async def get_track(self):
            raise RuntimeError(
                "Unable to download API page: [SSL: "
                "UNEXPECTED_EOF_WHILE_READING]"
            )

    import vc._youtube as ytm

    monkeypatch.setattr(ytm, "YouTubeData", lambda q: _Boom())
    e = FakeEvent("https://youtu.be/dQw4w9WgXcQ")
    asyncio.run(mod._resolve_to_track(e, "https://youtu.be/dQw4w9WgXcQ", None))

    out = e.last
    assert "TLS" in out
    assert "intercepted" in out.lower()
    # The advice must be there, so the user knows cookies will not help.
    assert "cookie" in out.lower()
    # And it must not paste the raw multi-line traceback into chat.
    assert len(out) < 1200

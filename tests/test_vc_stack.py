"""W1-A "vc-stack" regression tests (credential-free).

Covers:
- A6 compat shim: ``PyTgCallsClient`` re-roots ``__module__`` so the
  py-tgcalls client gate (``BridgedClient.package_name`` →
  ``MtProtoClient`` bridge binding, pytgcalls/mtproto/mtproto_client.py)
  accepts it, while ``UltroidClient`` itself stays truthful.
- ``vc/_youtube.py::YouTubeData``: the MusicService-conformant provider whose
  absence broke ``vc/_pytgcalls.py`` at import time (loader-known issue).
- ``pyUltroid/_core/cards.py``: the now-playing/queue geometry constants that
  were dropped in the caa96ab consolidation (silent NameError → None).
- ``vc/_pytgcalls.py::wire_call``: idempotent Call-singleton wiring used by
  the plugins/vctools.py command layer.

Everything here runs without network, Telegram credentials, or a database.
vc-engine tests self-skip when the optional VC stack (yt-dlp,
youtube-search-python) is not installed — same philosophy as the plugin-load
harness (tests/README.md).
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]

# pyUltroid.configs.Var reads sys.argv[1..] and mandatory env vars at import
# time, so imports of the real package must happen with a benign argv/env
# (same approach as tests/test_client_hygiene.py and smoke_test.py).
_SAFE_ENV = {
    "API_ID": "12345",
    "API_HASH": "0" * 32,
    "SESSION": "1" + "A" * 352,
    "BOT_TOKEN": "123:abc",
    "LOG_CHANNEL": "-10012345",
}


def _import_base_client():
    with patch.dict(os.environ, _SAFE_ENV), patch.object(sys, "argv", ["pytest"]):
        from pyUltroid.startup import BaseClient
    return BaseClient


def _import_cards():
    with patch.dict(os.environ, _SAFE_ENV), patch.object(sys, "argv", ["pytest"]):
        return importlib.import_module("pyUltroid._core.cards")


def _import_youtube():
    """Import vc._youtube, skipping when the optional VC stack is absent."""
    pytest.importorskip("yt_dlp", reason="yt-dlp not installed (vc extra)")
    pytest.importorskip(
        "youtubesearchpython", reason="youtube-search-python not installed"
    )
    return importlib.import_module("vc._youtube")


def _import_pytgcalls_engine():
    pytest.importorskip("yt_dlp", reason="yt-dlp not installed (vc extra)")
    pytest.importorskip(
        "youtubesearchpython", reason="youtube-search-python not installed"
    )
    pytest.importorskip("pytgcalls", reason="py-tgcalls not installed")
    return importlib.import_module("vc._pytgcalls")


# ---------------------------------------------------------------- A6 shim --


class TestPyTgCallsCompatClient:
    def test_compat_client_re_roots_module(self):
        BaseClient = _import_base_client()
        assert (
            BaseClient.PyTgCallsClient.__module__
            == "telethon.client.telegramclient"
        )

    def test_ultroid_client_stays_truthful(self):
        BaseClient = _import_base_client()
        assert BaseClient.UltroidClient.__module__ == "pyUltroid.startup.BaseClient"
        assert issubclass(BaseClient.PyTgCallsClient, BaseClient.UltroidClient)

    def test_package_name_gate_accepts_compat_client(self):
        """W1-A.2: package_name() on the compat client returns "telethon"."""
        pytest.importorskip("pytgcalls", reason="py-tgcalls not installed")
        from pytgcalls.mtproto.bridged_client import BridgedClient

        BaseClient = _import_base_client()
        compat = object.__new__(BaseClient.PyTgCallsClient)
        assert BridgedClient.package_name(compat) == "telethon"
        # …and documents why the plain client is rejected by the gate.
        plain = object.__new__(BaseClient.UltroidClient)
        assert BridgedClient.package_name(plain) == "pyUltroid"

    def test_mtproto_client_binds_telethon_bridge(self):
        """End-to-end gate check: MtProtoClient must bind TelethonClient."""
        pytest.importorskip("pytgcalls", reason="py-tgcalls not installed")
        from pytgcalls.mtproto.mtproto_client import MtProtoClient
        from pytgcalls.mtproto.telethon_client import TelethonClient

        BaseClient = _import_base_client()
        compat = object.__new__(BaseClient.PyTgCallsClient)
        # TelethonClient.__init__ registers a Raw-update handler via
        # client.on(...) and ClientCache probes client._no_updates; stub both —
        # they are instance state a bare (non-__init__) instance lacks.
        compat.on = lambda *a, **k: (lambda f: f)
        compat._no_updates = False
        bridge = MtProtoClient(60, compat)
        assert isinstance(bridge._bind_client, TelethonClient)


# --------------------------------------------------------------- cards -----


class TestCardGeometry:
    def test_geometry_constants_restored(self):
        cards = _import_cards()
        assert cards._NP_WIDTH == 800
        assert cards._NP_HEIGHT == 200
        assert cards._THUMB_SIZE == 160
        assert cards._THUMB_PAD == 20
        assert cards._TEXT_X_OFFSET == 160 + 2 * 20 + 10
        assert cards._PROGRESS_H == 8
        assert cards._PROGRESS_Y == 200 - 46
        assert cards._MAX_QUEUE == 10
        assert cards._OUT_DIR.name == "now_playing"
        assert cards._OUT_DIR.parent == cards._RESOURCE_ROOT

    @pytest.mark.skipif(
        importlib.util.find_spec("PIL") is None, reason="Pillow not installed"
    )
    def test_now_playing_card_renders(self):
        cards = _import_cards()
        path = cards.generate_now_playing("Track", "Artist", 180, 42)
        try:
            assert path is not None, "card silently returned None (regression)"
            assert path.endswith(".png")
            assert (ROOT / path).is_file()
        finally:
            if path:
                (ROOT / path).unlink(missing_ok=True)

    @pytest.mark.skipif(
        importlib.util.find_spec("PIL") is None, reason="Pillow not installed"
    )
    def test_queue_card_renders_objects(self):
        import types

        cards = _import_cards()
        queue = [
            types.SimpleNamespace(name="One", artist="A", duration=61),
            {"name": "Two", "artist": "B", "duration": 125},
        ]
        path = cards.generate_queue_card(queue)
        try:
            assert path is not None, "card silently returned None (regression)"
            assert (ROOT / path).is_file()
        finally:
            if path:
                (ROOT / path).unlink(missing_ok=True)


# ----------------------------------------------------------- YouTubeData ---


class TestYouTubeData:
    def test_class_exists_and_is_music_service(self):
        mod = _import_youtube()
        from vc._downloader import MusicService

        assert hasattr(mod, "YouTube"), "pre-existing low-level class removed?"
        assert issubclass(mod.YouTubeData, MusicService)
        # ABC completeness — instantiation must not raise TypeError.
        mod.YouTubeData()
        mod.YouTubeData("dQw4w9WgXcQ")

    def test_is_valid_url_contract(self):
        mod = _import_youtube()
        yt = mod.YouTubeData()
        assert yt.is_valid("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert yt.is_valid("https://youtu.be/dQw4w9WgXcQ")
        assert yt.is_valid("https://www.youtube.com/shorts/dQw4w9WgXcQ")
        assert not yt.is_valid("https://open.spotify.com/track/xyz")
        assert not yt.is_valid("some plain search text")
        assert not yt.is_valid("")
        assert not yt.is_valid(None)

    def test_extract_id(self):
        mod = _import_youtube()
        assert (
            mod.YouTubeData._extract_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        )
        assert mod.YouTubeData._extract_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"
        assert mod.YouTubeData._extract_id("lofi beats") is None


# ------------------------------------------------------------- wire_call ---


class _FakeApp:
    """Minimal stand-in for a PyTgCalls instance (decorator surface only)."""

    def __init__(self):
        self.handlers = []

    def on_update(self, filters=None):
        def deco(fn):
            self.handlers.append(fn)
            return fn

        return deco


class TestWireCall:
    def test_wire_call_populates_engine_once(self):
        engine_mod = _import_pytgcalls_engine()
        engine = engine_mod.Call()
        app, bot = _FakeApp(), object()

        assert engine_mod.wire_call(app, bot, name="t", engine=engine) is True
        assert engine.calls["t"] is app
        assert engine.available_clients == ["t"]
        assert engine.bot is bot
        assert len(app.handlers) == 1

    def test_wire_call_is_idempotent(self):
        engine_mod = _import_pytgcalls_engine()
        engine = engine_mod.Call()
        app = _FakeApp()

        assert engine_mod.wire_call(app, None, name="t", engine=engine) is True
        assert engine_mod.wire_call(app, None, name="t", engine=engine) is False
        assert engine.available_clients == ["t"]
        # No duplicate update handlers stacked on re-wire…
        assert len(app.handlers) == 1
        # …and not on the bulk re-registration path either.
        asyncio.run(engine.register_decorators())
        assert len(app.handlers) == 1

    def test_wire_call_keeps_existing_bot(self):
        engine_mod = _import_pytgcalls_engine()
        engine = engine_mod.Call()
        first_bot, second_bot = object(), object()

        assert engine_mod.wire_call(_FakeApp(), first_bot, name="a", engine=engine)
        assert (
            engine_mod.wire_call(_FakeApp(), second_bot, name="b", engine=engine)
            is True
        )
        assert engine.bot is first_bot  # first bot wins
        assert sorted(engine.available_clients) == ["a", "b"]


# ----------------------------------------------------- vc/_saavn helpers ---


def _import_saavn():
    """Import vc._saavn, skipping when the optional VC stack is absent."""
    pytest.importorskip("yt_dlp", reason="yt-dlp not installed (vc extra)")
    pytest.importorskip(
        "youtubesearchpython", reason="youtube-search-python not installed"
    )
    return importlib.import_module("vc._saavn")


class TestInlineMention:
    def test_falls_back_to_first_name(self):
        """Without an importable plugins package, prefer user.first_name."""
        saavn = _import_saavn()
        user = SimpleNamespace(first_name="Alice")
        assert saavn._inline_mention(user) == "Alice"

    def test_falls_back_to_str_without_first_name(self):
        saavn = _import_saavn()
        assert saavn._inline_mention(12345) == "12345"

    def test_uses_plugins_inline_mention_when_available(self, monkeypatch):
        saavn = _import_saavn()
        stub = types.ModuleType("plugins")
        stub.inline_mention = lambda user: f"MENTION:{user.first_name}"
        monkeypatch.setitem(sys.modules, "plugins", stub)
        user = SimpleNamespace(first_name="Alice")
        assert saavn._inline_mention(user) == "MENTION:Alice"


# --------------------------------------------- plugins/vctools.py layer ----


@pytest.fixture
def vctools_stubbed(module_isolation):
    """Import plugins/vcmusic.py with a stubbed ``plugins`` package.

    The real plugins package needs the full module-mode pyUltroid bootstrap
    (unavailable credential-free in-process), so we stub the two names
    vcmusic imports from it and record every registered command pattern.

    The VC commands used to live in vctools.py, which is now a pointer
    module; vcmusic.py is where they are defined and registered.
    """
    recorded = []

    plugins_stub = types.ModuleType("plugins")
    plugins_stub.__path__ = [str(ROOT / "plugins")]

    def _ultroid_cmd(pattern=None, **kwargs):
        recorded.append(pattern)
        return lambda fn: fn

    plugins_stub.get_string = lambda key: key
    plugins_stub.ultroid_cmd = _ultroid_cmd

    with module_isolation("plugins"):
        sys.modules["plugins"] = plugins_stub
        mod = importlib.import_module("plugins.vcmusic")
        yield mod, recorded


class TestVcToolsCommandRegistration:
    def test_legacy_patterns_kept(self, vctools_stubbed):
        _, recorded = vctools_stubbed
        for pat in ("startvc$", "stopvc$", "vcinvite$", "vctitle(?: |$)(.*)"):
            assert pat in recorded

    def test_music_patterns_registered(self, vctools_stubbed):
        _, recorded = vctools_stubbed
        # The actual player surface. This list previously asserted an
        # aspirational set (play/skip/queue/nowplaying/volume/seek...) that
        # was never implemented, so it could not pass against real code.
        for pat in (
            "vplay(?: |$)(.*)",
            "vskip$",
            "vpause$",
            "vresume$",
            "vqueue$",
            "vnow$",
            "vclear$",
            r"vloop(?: |$)(.*)",
        ):
            assert pat in recorded

    def test_no_collision_with_mute_plugin_patterns(self, vctools_stubbed):
        """plugins/mute.py owns `mute`/`unmute`; the VC layer must use the
        vcmute/vcunmute names instead."""
        _, recorded = vctools_stubbed
        assert "mute( (.*)|$)" not in recorded
        assert "unmute( (.*)|$)" not in recorded
        assert "vcmute$" in recorded
        assert "vcunmute$" in recorded


class TestSendCardOrText:
    """_send_card_or_text must always remove the generated card file."""

    @staticmethod
    def _event(send_side_effect=None):
        client = SimpleNamespace(send_file=AsyncMock(side_effect=send_side_effect))
        return SimpleNamespace(client=client, chat_id=123)

    @staticmethod
    def _msg():
        return SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())

    def test_send_success_removes_card(self, vctools_stubbed, tmp_path):
        mod, _ = vctools_stubbed
        card = tmp_path / "card.png"
        card.write_bytes(b"png")
        e, msg = self._event(), self._msg()

        asyncio.run(mod._send_card_or_text(e, msg, str(card), "hello"))

        assert e.client.send_file.await_count == 1
        assert msg.delete.await_count == 1
        assert msg.edit.await_count == 0
        assert not card.exists()

    def test_send_failure_falls_back_to_edit_and_still_removes(
        self, vctools_stubbed, tmp_path
    ):
        mod, _ = vctools_stubbed
        card = tmp_path / "card.png"
        card.write_bytes(b"png")
        e = self._event(send_side_effect=ValueError("send failed"))
        msg = self._msg()

        asyncio.run(mod._send_card_or_text(e, msg, str(card), "hello"))

        assert msg.edit.await_count == 1  # plain-text fallback happened
        assert not card.exists()  # finally-block removal (review regression)

    def test_edit_failure_still_removes_card(self, vctools_stubbed, tmp_path):
        mod, _ = vctools_stubbed
        card = tmp_path / "card.png"
        card.write_bytes(b"png")
        e = self._event(send_side_effect=ValueError("send failed"))
        msg = self._msg()
        msg.edit.side_effect = ValueError("edit failed")

        with pytest.raises(ValueError, match="edit failed"):
            asyncio.run(mod._send_card_or_text(e, msg, str(card), "hello"))
        assert not card.exists()

    def test_no_card_edits_message(self, vctools_stubbed):
        mod, _ = vctools_stubbed
        e, msg = self._event(), self._msg()

        asyncio.run(mod._send_card_or_text(e, msg, None, "hello"))

        msg.edit.assert_awaited_once_with("hello")
        assert e.client.send_file.await_count == 0

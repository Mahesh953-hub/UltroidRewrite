from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import TelegramClient
from telethon.tl import types as tltypes

from custom.fmt import fmtbar, fmtbyte, fmtmsg, fmtsec, rawhtml


@pytest.fixture
def routed_client(module_isolation, monkeypatch):
    """Load BaseClient against namespace stubs; never run the bot bootstrap."""
    with module_isolation("pyUltroid"):
        root_path = Path(__file__).resolve().parents[1] / "pyUltroid"
        root = types.ModuleType("pyUltroid")
        root.__path__ = [str(root_path)]
        root.LOGS = logging.getLogger("rich-dispatch-tests")
        root.HNDLR = "."
        root.udB = root.asst = root.ultroid_bot = None

        startup = types.ModuleType("pyUltroid.startup")
        startup.__path__ = [str(root_path / "startup")]
        logger = types.ModuleType("pyUltroid.startup.logger")
        logger.LOGS = root.LOGS
        loops = types.ModuleType("pyUltroid.startup._loops")
        loops.ensure_running_loop = asyncio.new_event_loop
        configs = types.ModuleType("pyUltroid.configs")
        configs.Var = SimpleNamespace(API_ID=1, API_HASH="offline-test")

        sys.modules.update(
            {
                "pyUltroid": root,
                "pyUltroid.startup": startup,
                "pyUltroid.startup.logger": logger,
                "pyUltroid.startup._loops": loops,
                "pyUltroid.configs": configs,
            }
        )
        baseclient = importlib.import_module("pyUltroid.startup.BaseClient")
        richmsg = importlib.import_module("pyUltroid._core.richmsg")
        client = baseclient.UltroidClient.__new__(baseclient.UltroidClient, None)
        client.parse_mode = "v2"
        client.me = SimpleNamespace(bot=True, premium=False)

        async def legacy_send(self, *args, **kwargs):
            return ("legacy-send", args, kwargs)

        async def legacy_edit(self, *args, **kwargs):
            return ("legacy-edit", args, kwargs)

        async def legacy_file(self, *args, **kwargs):
            return ("legacy-file", args, kwargs)

        monkeypatch.setattr(TelegramClient, "send_message", legacy_send)
        monkeypatch.setattr(TelegramClient, "edit_message", legacy_edit)
        monkeypatch.setattr(TelegramClient, "send_file", legacy_file)
        try:
            yield client, richmsg
        finally:
            baseclient.loop.close()


def test_send_message_routes_explicit_markup_to_rich_engine(routed_client, monkeypatch):
    client, richmsg = routed_client
    calls = []

    async def send_rich(*args, **kwargs):
        calls.append((args, kwargs))
        return "rich-result"

    monkeypatch.setattr(richmsg, "send_rich", send_rich)
    result = asyncio.run(
        client.send_message(
            "peer",
            richmsg.RichMarkup("<b>bold</b>", "html"),
            link_preview=False,
        )
    )
    assert result == "rich-result"
    assert calls[0][0][2].format == "html"
    assert calls[0][1]["format"] is None
    assert calls[0][1]["link_preview"] is False
    assert "is_bot" not in calls[0][1]

    ordinary = asyncio.run(
        client.send_message("peer", "<b>ordinary</b>", parse_mode="html")
    )
    assert ordinary[0] == "legacy-send"
    assert ordinary[1] == ("peer", "<b>ordinary</b>")


def test_fmtmsg_routes_assistant_sends_through_layer_230(routed_client, monkeypatch):
    client, richmsg = routed_client
    calls = []

    async def send_rich(*args, **kwargs):
        calls.append((args, kwargs))
        return "rich-result"

    monkeypatch.setattr(richmsg, "send_rich", send_rich)
    formatted = fmtmsg("Settings", data={"Layer": 230})
    assert isinstance(formatted, str)
    assert formatted.__rich_format__ == "html"
    assert asyncio.run(client.send_message("assistant-peer", formatted)) == "rich-result"
    assert calls[0][0][2].format == "html"
    assert "<b>Settings</b>" in calls[0][0][2].text


def test_fmtmsg_serializes_entities_from_custom_entities():
    message = fmtmsg(
        "Notice",
        msgs=("bold", [tltypes.MessageEntityBold(offset=0, length=4)]),
    )
    assert "<strong>bold</strong>" in message


def test_unsupported_options_never_downgrade_explicit_rich_markup(
    routed_client, monkeypatch
):
    client, richmsg = routed_client
    native_calls = []

    async def send_rich(*args, **kwargs):
        native_calls.append((args, kwargs))
        return "rich-result"

    monkeypatch.setattr(richmsg, "send_rich", send_rich)
    legacy = asyncio.run(client.send_message("peer", "hello", schedule=object()))
    assert legacy[0] == "legacy-send"
    assert legacy[2]["schedule"] is not None
    assert not native_calls

    with pytest.raises(TypeError, match="unsupported RichMessage send options"):
        asyncio.run(
            client.send_message(
                "peer",
                richmsg.RichMarkup("<b>rich</b>", "html"),
                schedule=object(),
            )
        )
    assert not native_calls


def test_edit_message_routes_html_and_media_caption_uses_entities(
    routed_client, monkeypatch
):
    client, richmsg = routed_client
    edits = []

    async def edit_rich(*args, **kwargs):
        edits.append((args, kwargs))
        return "edited-rich"

    monkeypatch.setattr(richmsg, "edit_rich", edit_rich)
    markup = richmsg.RichMarkup("<b>updated</b>", "html")
    assert asyncio.run(
        client.edit_message("peer", 17, text=markup)
    ) == "edited-rich"
    assert edits[0][0][3] is markup
    assert edits[0][1]["format"] is None

    file_result = asyncio.run(
        client.send_file("peer", "photo.png", caption="<b>caption</b>", parse_mode="html")
    )
    assert file_result[0] == "legacy-file"
    assert file_result[2]["parse_mode"] == "html"
    assert file_result[2]["caption"] == "<b>caption</b>"

    native_caption = richmsg.rich_message_html("<b>caption</b>")
    with pytest.raises(TypeError, match="RichMessage is not a media caption"):
        asyncio.run(client.send_file("peer", "photo.png", caption=native_caption))


def test_rich_edit_accepts_legacy_parse_mode_as_a_native_format_alias(
    routed_client, monkeypatch
):
    client, richmsg = routed_client
    edits = []

    async def edit_rich(*args, **kwargs):
        edits.append((args, kwargs))
        return "edited-rich"

    monkeypatch.setattr(richmsg, "edit_rich", edit_rich)
    markup = richmsg.RichMarkup("<b>updated</b>", "html")
    assert asyncio.run(
        client.edit_message("peer", 17, text=markup, parse_mode="html")
    ) == "edited-rich"
    assert edits[0][1]["format"] == "html"
    assert edits[0][1]["buttons"] is None


def test_message_with_file_bypasses_text_rich_route(routed_client, monkeypatch):
    client, richmsg = routed_client
    native_calls = []

    async def send_rich(*args, **kwargs):
        native_calls.append((args, kwargs))
        return "rich-result"

    monkeypatch.setattr(richmsg, "send_rich", send_rich)
    result = asyncio.run(client.send_message("peer", "caption", file="photo.png"))
    assert result[0] == "legacy-send"
    assert result[2]["file"] == "photo.png"
    assert not native_calls


def test_fmtbar_handles_zero_and_clamps_ratio_with_ascii_output():
    assert fmtbar(5, 10) == "[ #####----- ] 50%"
    assert fmtbar(2, 0) == "[ ---------- ] 0%"
    assert fmtbar(20, 10) == "[ ########## ] 100%"
    with pytest.raises(ValueError):
        fmtbar(1, 2, bars=0)


def test_fmtbyte_formats_zero_fractional_and_negative_values():
    assert fmtbyte(0) == "0 B"
    assert fmtbyte(0.5) == "0.5 B"
    assert fmtbyte(1024) == "1 KiB"
    assert fmtbyte(-1024) == "-1 KiB"


def test_fmtsec_handles_negative_and_subsecond_durations():
    assert fmtsec(0.5) == "500 ms"
    assert fmtsec(0.0005) == "500 µs"
    assert fmtsec(-1.5) == "-1 second, 500 ms"
    with pytest.raises(ValueError):
        fmtsec(1, part=0)


def test_fmtmsg_escapes_dynamic_html_and_keeps_explicit_rawhtml():
    safe = fmtmsg("<Title>", msgs="<script>alert(1)</script>")
    assert "&lt;Title&gt;" in safe
    assert "&lt;script&gt;" in safe
    assert "<script>" not in safe

    trusted = fmtmsg("Title", msgs=rawhtml("<b>trusted</b>"))
    assert "<b>trusted</b>" in trusted

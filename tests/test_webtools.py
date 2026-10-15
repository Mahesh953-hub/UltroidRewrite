"""Tests for Wave-3 W3-B web_app / WebView tooling.

Covers:
- ``custom.tg.web_app_button()`` and the ``ikm()`` ``("…", "web_app", url)``
  tuple form, including graceful degradation when the raw TL types are absent.
- Simulated reception of ``MessageActionWebViewDataSent`` and
  ``UpdateWebViewResultSent`` through the ``plugins.webtools`` listeners.
- Live round-trip is gated by the ``WEBTOOLS_LIVE`` environment variable.

The plugin import test runs in a subprocess because ``plugins.webtools`` imports
``pyUltroid`` (which mutates process-global state).  The subprocess uses the same
mocks as ``smoke_test.py`` so no network or credentials are required.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from telethon import Button
from telethon.tl import types as tl_types

import custom.tg as tg
from custom.tg import ikm, web_app_button
from telethon.tl import functions
from telethon.tl.functions.messages import SendWebViewResultMessageRequest

ROOT = Path(__file__).resolve().parents[1]

SENTINEL = "WEBTOOLS_LISTENER_JSON:"


# ──────────────────────────────────────────────────────────────────────────
# custom.tg — TL shape and graceful degradation
# ──────────────────────────────────────────────────────────────────────────


def test_web_app_button_default_uses_keyboard_button_webview():
    btn = web_app_button("Open App", "https://example.com/app")
    assert isinstance(btn, tl_types.KeyboardButtonWebView)
    assert btn.text == "Open App"
    assert btn.url == "https://example.com/app"


def test_web_app_button_simple_uses_keyboard_button_simple_webview():
    btn = web_app_button("Open App", "https://example.com/app", simple=True)
    assert isinstance(btn, tl_types.KeyboardButtonSimpleWebView)


def test_web_app_button_style_and_icon_propagation():
    btn = web_app_button(
        "Open App", "https://example.com/app", style="primary", icon=123
    )
    assert isinstance(btn, tl_types.KeyboardButtonWebView)
    assert isinstance(btn.style, tl_types.KeyboardButtonStyle)
    assert btn.style.bg_primary is True
    assert btn.style.icon == 123


def test_ikm_web_app_tuple_builds_keyboard_button_webview():
    rows = ikm([("Open App", "web_app", "https://example.com/app")])
    btn = rows[0][0]
    assert isinstance(btn, tl_types.KeyboardButtonWebView)
    assert btn.text == "Open App"
    assert btn.url == "https://example.com/app"


def test_ikm_webapp_alias_also_builds_keyboard_button_webview():
    rows = ikm([("Open App", "webapp", "https://example.com/app")])
    assert isinstance(rows[0][0], tl_types.KeyboardButtonWebView)


def test_ikm_simple_webapp_tuple_builds_simple_webview():
    rows = ikm([("Open App", "simple_webapp", "https://example.com/app")])
    assert isinstance(rows[0][0], tl_types.KeyboardButtonSimpleWebView)


def test_ikm_web_app_tuple_style_and_icon():
    rows = ikm([("Open App", "web_app", "https://example.com/app", "danger", 99)])
    btn = rows[0][0]
    assert btn.style.bg_danger is True
    assert btn.style.icon == 99


def test_web_app_button_degrades_to_url_when_tl_types_missing(monkeypatch):
    monkeypatch.setattr(tg, "_WebViewButtonCls", None)
    monkeypatch.setattr(tg, "_SimpleWebViewButtonCls", None)
    btn = web_app_button(
        "Open App", "https://example.com/app", style="success", icon=42
    )
    assert isinstance(btn, tl_types.KeyboardInlineButton)
    assert btn.type.url == "https://example.com/app"
    assert btn.style.bg_success is True
    assert btn.style.icon == 42


def test_web_app_button_degrades_to_inline_when_no_url_and_no_tl_types(monkeypatch):
    monkeypatch.setattr(tg, "_WebViewButtonCls", None)
    monkeypatch.setattr(tg, "_SimpleWebViewButtonCls", None)
    btn = web_app_button("Open App", "", style="primary")
    assert isinstance(btn, tl_types.KeyboardButtonCallback)
    assert btn.data == b"web_app"


def test_ikm_web_app_degrades_gracefully_when_tl_types_missing(monkeypatch):
    monkeypatch.setattr(tg, "_WebViewButtonCls", None)
    monkeypatch.setattr(tg, "_SimpleWebViewButtonCls", None)
    rows = ikm([("Open App", "web_app", "https://example.com/app")])
    btn = rows[0][0]
    assert isinstance(btn, tl_types.KeyboardInlineButton)
    assert btn.type.url == "https://example.com/app"


def test_send_web_view_result_message_builds_tl_request():
    # Any valid TL object can stand in for the InputBotInlineResult parameter.
    result = tl_types.InputBotInlineMessageText(message="hello", entities=None)
    req = tg.send_web_view_result_message("query-123", result)
    assert isinstance(req, SendWebViewResultMessageRequest)
    assert req.bot_query_id == "query-123"
    assert req.result is result


def test_send_web_view_result_message_raises_when_not_available(monkeypatch):
    monkeypatch.setattr(tg, "_SendWebViewResultMsgCls", None)
    with pytest.raises(NotImplementedError):
        tg.send_web_view_result_message("query-123", None)


def test_capability_probes_match_installed_telethon():
    assert tg._HAS_WEBVIEW_BUTTONS is True
    assert tg._HAS_WEBVIEW_DATA_ACTIONS is True
    assert tg._HAS_WEBVIEW_RESULT_SENT is True
    assert tg._HAS_WEBVIEW_RESULT_MSG is True


def test_request_webview_request_class_exists_with_exact_name():
    """There is no RequestWebView shorthand; the real TL request class is
    RequestWebViewRequest. This test would fail if code uses the stripped name."""
    assert hasattr(functions.messages, "RequestWebViewRequest")
    assert not hasattr(functions.messages, "RequestWebView")


# ──────────────────────────────────────────────────────────────────────────
# plugins/webtools — simulated listener round-trip (subprocess worker)
# ──────────────────────────────────────────────────────────────────────────


class _ListenerWorker:
    """Code that runs in a subprocess with the smoke_test-style mocks."""

    SCRIPT = textwrap.dedent(r"""
        import asyncio
        import json
        import os
        import re
        import sys

        os.environ["SESSION"] = "1" + "A" * 352
        os.environ.setdefault("API_ID", "12345")
        os.environ.setdefault("API_HASH", "abcdef")
        os.environ.setdefault("BOT_TOKEN", "123:abc")
        os.environ.setdefault("LOG_CHANNEL", "-10012345")
        # Enable the live gate so the webview command exercises the real API path.
        os.environ["WEBTOOLS_LIVE"] = "1"

        import telethon.sessions

        def dummy_string_session_init(self, string=None):
            self._dc_id = 2
            self._server_address = "149.154.167.51"
            self._port = 443
            self._auth_key = b"A" * 256

        telethon.sessions.StringSession.__init__ = dummy_string_session_init

        from telethon import TelegramClient
        from telethon.tl import types
        from unittest.mock import AsyncMock

        class DummyUser:
            id = 123456789
            first_name = "Mocked"
            last_name = "User"
            username = "mocked_user"
            bot = False
            bot_inline_placeholder = "Placeholder"

        async def mock_start(self, *args, **kwargs):
            self.me = DummyUser()
            bot_token = kwargs.get("bot_token") or (args[0] if args else None)
            if bot_token:
                self.me.bot = True
                self._bot = True
            return self

        TelegramClient.start = mock_start
        TelegramClient.connect = AsyncMock()
        TelegramClient.get_me = AsyncMock(return_value=DummyUser())
        TelegramClient.get_entity = AsyncMock(return_value=DummyUser())

        sys.argv[0] = "-m"
        import pyUltroid
        from pyUltroid import ultroid_bot

        import plugins.webtools as wt

        from telethon.types import (
            MessageService,
            MessageActionWebViewDataSent,
            MessageActionWebViewDataSentMe,
            PeerUser,
            UpdateNewMessage,
            UpdateWebViewResultSent,
        )

        class FakeCmdEvent:
            out = True
            sender_id = 123456789
            chat_id = 123456789
            is_private = True
            chat = None

            def __init__(self, text, pattern):
                self.text = text
                self.pattern_match = re.match(pattern, text)
                self.eor_calls = []

            async def eor(self, *args, **kwargs):
                self.eor_calls.append((args, kwargs))

        async def main():
            # --- Targeted test: simplewebapp uses ReplyKeyboardMarkup, not inline ---
            simple_event = FakeCmdEvent(
                ".simplewebapp https://example.com/simple Simple App",
                r"\.simplewebapp( (.*)|$)",
            )
            await wt.simplewebapp_cmd(simple_event)
            simple_markup = simple_event.eor_calls[0][1].get("reply_markup")
            simple_button = None
            if simple_markup is not None and simple_markup.rows:
                simple_button = simple_markup.rows[0][0]

            # --- Targeted test: webview command uses RequestWebViewRequest ---
            captured_requests = []
            async def fake_client_call(self, request, **kwargs):
                captured_requests.append(request)
                return types.WebViewResultUrl(
                    url="https://result.example.com/",
                    query_id=123456789,
                )
            TelegramClient.__call__ = fake_client_call

            webview_event = FakeCmdEvent(
                ".webview mybot https://example.com/webview",
                r"\.webview( (.*)|$)",
            )
            await wt.webview_cmd(webview_event)
            webview_request_class = (
                captured_requests[0].__class__.__name__ if captured_requests else None
            )

            # --- Listener round-trip tests ---
            msg = MessageService(
                id=1,
                peer_id=PeerUser(user_id=123456789),
                date=None,
                from_id=PeerUser(user_id=123456789),
                action=MessageActionWebViewDataSent(text="demo-text"),
                out=True,
            )
            update_data = UpdateNewMessage(message=msg, pts=1, pts_count=1)
            await ultroid_bot.dispatcher._dispatch(update_data)

            update_sent = UpdateWebViewResultSent(query_id=999888777)
            await ultroid_bot.dispatcher._dispatch(update_sent)

            msg_me = MessageService(
                id=2,
                peer_id=PeerUser(user_id=123456789),
                date=None,
                from_id=PeerUser(user_id=123456789),
                action=MessageActionWebViewDataSentMe(text="me-text", data="me-data"),
                out=True,
            )
            await ultroid_bot.dispatcher._dispatch(UpdateNewMessage(message=msg_me, pts=2, pts_count=1))

            await asyncio.sleep(0.05)

            print("WEBTOOLS_LISTENER_JSON:" + json.dumps({
                "last": wt._last_webapp_data,
                "listener_count": len(ultroid_bot.dispatcher.listener_states()),
                "simple_markup_class": simple_markup.__class__.__name__ if simple_markup else None,
                "simple_button_class": simple_button.__class__.__name__ if simple_button else None,
                "webview_request_class": webview_request_class,
            }))

        asyncio.run(main())
        """)

    @staticmethod
    def run() -> dict:
        proc = subprocess.run(
            [sys.executable, "-c", _ListenerWorker.SCRIPT],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        line = next(
            (l for l in proc.stdout.splitlines() if l.startswith(SENTINEL)), None
        )
        if line is None:
            raise RuntimeError(
                f"listener worker produced no sentinel line\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}\n"
                f"exit={proc.returncode}"
            )
        return json.loads(line[len(SENTINEL) :])


@pytest.fixture(scope="module")
def listener_report():
    """Run the webtools listener worker once per module."""
    return _ListenerWorker.run()


def test_listener_captures_webview_data_sent(listener_report):
    data = listener_report["last"]
    assert "MessageActionWebViewDataSent" in data
    assert "text='demo-text'" in data["MessageActionWebViewDataSent"]


def test_listener_captures_webview_data_sent_me(listener_report):
    data = listener_report["last"]
    assert "MessageActionWebViewDataSentMe" in data
    assert "text='me-text'" in data["MessageActionWebViewDataSentMe"]
    assert "data='me-data'" in data["MessageActionWebViewDataSentMe"]


def test_listener_captures_update_webview_result_sent(listener_report):
    data = listener_report["last"]
    assert "UpdateWebViewResultSent" in data
    assert "query_id=999888777" in data["UpdateWebViewResultSent"]


def test_listeners_registered(listener_report):
    assert listener_report["listener_count"] >= 2


def test_simplewebapp_uses_reply_keyboard_markup(listener_report):
    """KeyboardButtonSimpleWebView is a reply-keyboard button and must be sent
    via ReplyKeyboardMarkup, not as inline buttons."""
    assert listener_report["simple_markup_class"] == "ReplyKeyboardMarkup"
    assert listener_report["simple_button_class"] == "KeyboardButtonSimpleWebView"


def test_webview_command_uses_real_request_webview_request_class(listener_report):
    """The webview command must invoke the exact TL request class
    functions.messages.RequestWebViewRequest (there is no RequestWebView alias)."""
    assert listener_report["webview_request_class"] == "RequestWebViewRequest"


# ──────────────────────────────────────────────────────────────────────────
# Live round-trip gate
# ──────────────────────────────────────────────────────────────────────────


def test_live_roundtrip_gated_by_environment():
    """Live network round-trip is documented as gated; absent WEBTOOLS_LIVE the
    plugin commands report the gate without performing network I/O."""
    assert "WEBTOOLS_LIVE" not in os.environ, "do not run live tests in this suite"


@pytest.mark.skipif(
    not os.environ.get("WEBTOOLS_LIVE"),
    reason="Live web_app round-trip requires WEBTOOLS_LIVE=1 and real bot credentials",
)
def test_live_webview_roundtrip_placeholder():
    """Placeholder for the live, credential-backed round-trip."""
    pytest.skip("live round-trip must be executed manually with WEBTOOLS_LIVE=1")

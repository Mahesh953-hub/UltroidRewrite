"""W4-A business-scope tests.

Credential-free unit tests proving that the business-connection request wrapping
is scoped to a dedicated subclass used only for assistant/bot clients, and that
user-account clients are never affected by the business context variable.
"""

from __future__ import annotations

import asyncio
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from telethon import TelegramClient
from telethon.functions import InvokeWithBusinessConnectionRequest
from telethon.tl import TLRequest


@contextmanager
def _isolated_client_modules():
    """Import pyUltroid client modules without running the production bootstrap."""
    saved = {name: module for name, module in sys.modules.items()}
    for name in list(sys.modules):
        if name == "pyUltroid" or name.startswith("pyUltroid."):
            sys.modules.pop(name, None)
    try:
        root = str(Path(__file__).resolve().parents[1])
        if root not in sys.path:
            sys.path.insert(0, root)
        yield
    finally:
        for name in list(sys.modules):
            if name == "pyUltroid" or name.startswith("pyUltroid."):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


@pytest.fixture
def client_mods(monkeypatch):
    with _isolated_client_modules():
        original_argv = sys.argv[:]
        sys.argv = ["test_business_scope"]
        try:
            for key, value in {
                "API_ID": "12345",
                "API_HASH": "deadbeefcafe",
                "SESSION": "AQABBAABBAABBAABBAABBAABBAABBAABBAABBAABBAABBAABBAAB",
                "LOG_CHANNEL": "1",
                "BOT_TOKEN": "123456:dummy-token",
            }.items():
                monkeypatch.setenv(key, value)

            from pyUltroid.startup.BaseClient import (
                BusinessCapableClient,
                UltroidClient,
            )
            from pyUltroid.startup.business import (
                _BUSINESS_WRAPPABLE_REQUESTS,
                business_connection_id,
            )

            yield types.SimpleNamespace(
                BusinessCapableClient=BusinessCapableClient,
                UltroidClient=UltroidClient,
                business_connection_id=business_connection_id,
                wrappable=_BUSINESS_WRAPPABLE_REQUESTS,
            )
        finally:
            sys.argv[:] = original_argv


class DummyRequest(TLRequest):
    CONSTRUCTOR_ID = 0x1234ABCD
    SUBCLASS_OF_ID = 0x5678EF01

    def __init__(self):
        pass

    def to_dict(self):
        return {"_": "DummyRequest"}


class OtherRequest(TLRequest):
    CONSTRUCTOR_ID = 0x11111111
    SUBCLASS_OF_ID = 0x22222222

    def __init__(self):
        pass

    def to_dict(self):
        return {"_": "OtherRequest"}


@pytest.fixture
def capture_telegram_call(monkeypatch):
    """Patch TelegramClient.__call__ to record the query it receives."""
    captured = []

    async def fake_call(self, query, *args, **kwargs):
        captured.append(query)
        return "mocked"

    monkeypatch.setattr(TelegramClient, "__call__", fake_call)
    return captured


@pytest.fixture
def wrappable_with_dummy(monkeypatch, client_mods):
    """Make the production allow-list include the test DummyRequest class."""
    monkeypatch.setattr(
        "pyUltroid.startup.business._BUSINESS_WRAPPABLE_REQUESTS",
        client_mods.wrappable | {"DummyRequest"},
    )


class TestBusinessCapableClient:
    def test_wraps_request_when_business_context_active(
        self, client_mods, capture_telegram_call, wrappable_with_dummy
    ):
        client = object.__new__(client_mods.BusinessCapableClient)
        req = DummyRequest()
        token = client_mods.business_connection_id.set("conn-123")
        try:
            asyncio.run(client(req))
        finally:
            client_mods.business_connection_id.reset(token)

        assert len(capture_telegram_call) == 1
        wrapped = capture_telegram_call[0]
        assert isinstance(wrapped, InvokeWithBusinessConnectionRequest)
        assert wrapped.connection_id == "conn-123"
        assert wrapped.query is req

    def test_does_not_wrap_when_no_business_context(
        self, client_mods, capture_telegram_call, wrappable_with_dummy
    ):
        client = object.__new__(client_mods.BusinessCapableClient)
        req = DummyRequest()
        asyncio.run(client(req))

        assert len(capture_telegram_call) == 1
        assert capture_telegram_call[0] is req

    def test_non_wrappable_request_left_untouched(
        self, client_mods, capture_telegram_call, wrappable_with_dummy
    ):
        client = object.__new__(client_mods.BusinessCapableClient)
        req = OtherRequest()
        token = client_mods.business_connection_id.set("conn-123")
        try:
            asyncio.run(client(req))
        finally:
            client_mods.business_connection_id.reset(token)

        assert len(capture_telegram_call) == 1
        assert capture_telegram_call[0] is req


class TestUserClientUntouched:
    def test_ultroid_client_does_not_wrap_in_business_context(
        self, client_mods, capture_telegram_call, wrappable_with_dummy
    ):
        """A plain user UltroidClient must ignore the business context variable."""
        client = object.__new__(client_mods.UltroidClient)
        req = DummyRequest()
        token = client_mods.business_connection_id.set("conn-123")
        try:
            asyncio.run(client(req))
        finally:
            client_mods.business_connection_id.reset(token)

        assert len(capture_telegram_call) == 1
        assert capture_telegram_call[0] is req
        assert not isinstance(
            capture_telegram_call[0], InvokeWithBusinessConnectionRequest
        )

    def test_ultroid_client_has_no_business_call_override(self, client_mods):
        assert client_mods.UltroidClient.__call__ is TelegramClient.__call__


class TestClientFactory:
    def test_bot_token_uses_business_capable_client(self, client_mods, monkeypatch):
        # Bypass UltroidClient.__init__ to avoid any network setup.
        monkeypatch.setattr(client_mods.UltroidClient, "__init__", lambda *a, **k: None)
        client = client_mods.UltroidClient("asst", bot_token="123456:abc")
        assert isinstance(client, client_mods.BusinessCapableClient)
        assert isinstance(client, client_mods.UltroidClient)
        assert isinstance(client, TelegramClient)

    def test_no_bot_token_uses_plain_ultroid_client(self, client_mods, monkeypatch):
        monkeypatch.setattr(client_mods.UltroidClient, "__init__", lambda *a, **k: None)
        client = client_mods.UltroidClient("session")
        assert type(client) is client_mods.UltroidClient
        assert not isinstance(client, client_mods.BusinessCapableClient)

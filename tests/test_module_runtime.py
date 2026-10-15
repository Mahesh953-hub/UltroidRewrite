"""Focused runtime coverage for the module command/update changes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom.tg import _normalize_icon
from pyUltroid.modules import Modules
from pyUltroid.modules import execute as execute_module


def test_normalize_icon_converts_numeric_string_without_registry_lookup(monkeypatch):
    """Telegram document ids supplied as strings must remain literal ids."""
    called = False

    def registry_lookup(_):
        nonlocal called
        called = True
        raise AssertionError("numeric document ids must not use the icon registry")

    monkeypatch.setattr("custom.tg.document_id_for", registry_lookup)
    assert _normalize_icon("123456789012345") == 123456789012345
    assert called is False


class GuestUpdate:
    pass


class BusinessUpdate:
    pass


def _union() -> Modules:
    """A fresh union instance; the union is itself the executor and router."""
    return Modules()


def test_execute_guest_command_uses_me_username_and_public_app(monkeypatch):
    """Guest commands parse an optional @username prefix and expose .app."""
    client = SimpleNamespace(
        me=SimpleNamespace(username="SarahBot"),
        app="public-app",
        http=object(),
        _client="raw-client",
        edit_message=AsyncMock(),
        loop=asyncio.get_event_loop_policy().new_event_loop(),
    )
    update = GuestUpdate()
    update._client = client
    update.message = SimpleNamespace(message="@SarahBot   1 + 1")
    update.reference_messages = []
    msg_id = SimpleNamespace(access_hash=99)
    seen = {}

    async def fake_aexec(command, kwargs):
        seen["command"] = command
        seen["app"] = kwargs["app"]
        return "2"

    async def fake_deliver(*args):
        seen["deliver"] = args

    monkeypatch.setattr(execute_module, "UpdateBotGuestChatQuery", GuestUpdate)
    monkeypatch.setattr(execute_module, "UpdateBotNewBusinessMessage", BusinessUpdate)
    monkeypatch.setattr(execute_module, "aexec", fake_aexec)

    executor = _union()
    executor.deliver = fake_deliver
    try:
        asyncio.run(executor.execute(update, msg_id))
    finally:
        client.loop.close()

    assert seen["command"] == "1 + 1"
    assert seen["app"] == "public-app"


def test_executor_namespace_is_shared_not_mutated():
    """Each call copies the namespace, so callers cannot poison the next run."""
    executor = _union()
    first = dict(executor.namespace)
    first["injected"] = True

    assert "injected" not in executor.namespace
    assert executor.namespace["tl"] is not None


def test_fmtout_reports_utf16_length():
    """Entity offsets are UTF-16 units, so astral characters count double."""
    text, length = _union().fmtout("hi", 1)
    assert text == "h..."
    assert length == len("h...".encode("utf-16-le")) // 2


# The router dispatches through a per-type table on the union instance.
OWNER = 7


def _client():
    return SimpleNamespace(
        user=SimpleNamespace(id=OWNER), connection_id="c", user_id=OWNER
    )


@pytest.mark.parametrize("kind", ["guest", "business", "media", "callback", "inline"])
def test_updates_routes_supported_variants(monkeypatch, kind):
    from telethon.types import (
        InputBotInlineMessageID,
        Message,
        MessageMediaPhoto,
        PeerChat,
        PeerUser,
        UpdateBotGuestChatQuery,
        UpdateBotNewBusinessMessage,
        UpdateBusinessBotCallbackQuery,
        UpdateInlineBotCallbackQuery,
    )

    client = _client()
    modules = _union()
    execute = AsyncMock()
    modules.execute = execute

    if kind == "guest":
        update = object.__new__(UpdateBotGuestChatQuery)
        update.message = Message(
            id=1, message="run", peer_id=PeerChat(3), from_id=PeerUser(OWNER)
        )
        update.answer = AsyncMock(return_value="result")

    elif kind == "business":
        update = object.__new__(UpdateBotNewBusinessMessage)
        update.connection_id = "c"
        update.message = Message(
            id=4, message="code#", peer_id=PeerUser(1), from_id=PeerUser(OWNER)
        )
        update.respond = AsyncMock(
            return_value=SimpleNamespace(
                updates=[SimpleNamespace(message=SimpleNamespace(id=5))]
            )
        )

    elif kind == "media":
        update = object.__new__(UpdateBotNewBusinessMessage)
        update.connection_id = "c"
        update.message = Message(
            id=1, message="👀", peer_id=PeerUser(1), from_id=PeerUser(OWNER)
        )
        update.reply_to_message = Message(
            id=4, peer_id=PeerUser(42), media=MessageMediaPhoto(ttl_seconds=5)
        )
        client.download_media = AsyncMock(return_value="downloaded-file")
        client.send_file = AsyncMock()

    elif kind == "callback":
        update = object.__new__(UpdateBusinessBotCallbackQuery)
        update.user_id, update.connection_id, update.data = OWNER, "c", b"0"
        update.message = Message(id=4, peer_id=PeerUser(1))
        update.delete = AsyncMock()

    else:
        update = object.__new__(UpdateInlineBotCallbackQuery)
        update.user_id, update.data = OWNER, b"0"
        update.msg_id = InputBotInlineMessageID(id=1, access_hash=1, dc_id=0)
        client.edit_message = AsyncMock()
        client.get_input_entity = AsyncMock(side_effect=RuntimeError())

    update._client = client

    assert asyncio.run(modules.route(update)) is True

    if kind in ("guest", "business"):
        execute.assert_awaited_once()
    elif kind == "media":
        client.download_media.assert_awaited_once()
        client.send_file.assert_awaited_once()
        # Sends to the owner id, not to the raw peer.
        assert client.send_file.await_args.args[0] == OWNER
    elif kind == "callback":
        update.delete.assert_awaited_once()
    else:
        client.edit_message.assert_awaited_once()


def test_updates_ignores_non_owner_traffic():
    """An unresolved/foreign sender must be ignored, not silently executed."""
    from telethon.types import Message, PeerUser, UpdateBotNewBusinessMessage

    client = _client()
    modules = _union()
    execute = AsyncMock()
    modules.execute = execute

    update = object.__new__(UpdateBotNewBusinessMessage)
    update.connection_id = "c"
    update.message = Message(
        id=4, message="code#", peer_id=PeerUser(1), from_id=PeerUser(999)
    )
    update.respond = AsyncMock()
    update._client = client

    assert asyncio.run(modules.route(update)) is False
    execute.assert_not_awaited()


def test_updates_works_when_owner_id_is_unresolvable():
    """Regression: owner id used to resolve to None and killed all dispatch."""
    from telethon.types import Message, PeerUser, UpdateBotNewBusinessMessage

    client = SimpleNamespace()  # no user_id, no user, no me
    modules = _union()
    execute = AsyncMock()
    modules.execute = execute

    update = object.__new__(UpdateBotNewBusinessMessage)
    update.connection_id = "c"
    update.message = Message(
        id=4, message="code#", peer_id=PeerUser(1), from_id=PeerUser(7)
    )
    update.respond = AsyncMock(
        return_value=SimpleNamespace(
            updates=[SimpleNamespace(message=SimpleNamespace(id=5))]
        )
    )
    update._client = client

    assert asyncio.run(modules.route(update)) is True
    execute.assert_awaited_once()


def test_dispatch_cache_keeps_subclasses_routable():
    """A cached exact type must not stop a subclass resolving by isinstance."""
    from telethon.types import UpdateBotNewBusinessMessage

    class Derived(UpdateBotNewBusinessMessage):
        pass

    modules = _union()
    assert modules._handler_for(object.__new__(Derived)) == "business"
    # Second sighting hits the cache.
    assert modules._handler_for(object.__new__(Derived)) == "business"


def test_raw_handler_excludes_channel_participant_and_log_channel_side_effects():
    """The bootstrap intentionally limits this handler to bot updates."""
    from telethon import events
    from telethon.types import UpdateChannelParticipant

    handlers = events._get_handlers(_union().on_update)
    assert len(handlers) == 1
    assert UpdateChannelParticipant not in handlers[0].types
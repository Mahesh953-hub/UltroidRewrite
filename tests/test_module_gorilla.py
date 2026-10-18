"""Real-TL-object coverage for the forced business/guest addons and router.

The original port had zero coverage here, which is why a silently-dead
dispatch (owner id resolving to ``None``) went unnoticed.
"""

from __future__ import annotations

import asyncio

import pytest
from telethon import TelegramClient, events
from telethon.types import (
    InputBotInlineMessageID,
    Message,
    MessageEntityPre,
    PeerChat,
    PeerUser,
    UpdateBotGuestChatQuery,
    UpdateBotNewBusinessMessage,
    UpdateBusinessBotCallbackQuery,
    UpdateInlineBotCallbackQuery,
)

from pyUltroid.modules import Modules
from pyUltroid.modules import _gorilla

OWNER = 4242


@pytest.fixture(scope="module", autouse=True)
def _addons_installed():
    """The TL classes carry the addons for this module, as production does."""
    addons = Modules()
    addons.install(force=True)
    yield


class FakeClient(TelegramClient):
    """A TelegramClient shell that records calls instead of hitting Telegram."""

    def __init__(self, uid=OWNER, conn="CONN-1"):  # noqa: D107
        self.user_id = uid
        self.connection_id = conn
        self.sent = []
        self._parse_mode = None
        self._entity_parser = None

    async def __call__(self, request, *a, **k):
        self.sent.append(request)
        return "sent"

    def build_reply_markup(self, b):
        return f"markup:{b}"

    async def get_input_entity(self, e):
        return e

    async def get_input_entity_or_none(self, e):
        return e


# ── owner id resolution ────────────────────────────────────────────────────


def test_owner_id_prefers_explicit_user_id():
    assert Modules().owner_id(FakeClient(uid=7)) == 7


def test_owner_id_falls_back_to_user_id_field():
    """The fork exposes User.id, not User.uid — the bug that killed dispatch."""
    client = FakeClient()
    del client.user_id
    client.user = type("U", (), {"id": 99})()
    assert Modules().owner_id(client) == 99


def test_owner_id_reads_me_id():
    client = FakeClient()
    del client.user_id
    client.me = type("M", (), {"id": 123})()
    assert Modules().owner_id(client) == 123


def test_owner_id_returns_none_when_unresolvable():
    assert Modules().owner_id(object()) is None


def test_owner_id_ignores_uid_alias():
    """Regression: ``User.uid`` does not exist on this fork."""
    client = FakeClient()
    del client.user_id
    client.user = type("U", (), {"uid": 5})()
    assert Modules().owner_id(client) is None


# ── forced class patches ───────────────────────────────────────────────────


def test_addons_are_installed_on_tl_classes():
    Modules().install(force=True)
    assert UpdateBotGuestChatQuery.answer.__module__ == "pyUltroid.modules._gorilla"
    assert UpdateBotNewBusinessMessage.respond.__module__ == "pyUltroid.modules._gorilla"
    assert UpdateBotNewBusinessMessage.delete.__module__ == "pyUltroid.modules._gorilla"
    assert UpdateBusinessBotCallbackQuery.respond.__module__ == (
        "pyUltroid.modules._gorilla"
    )
    assert TelegramClient.invoke.__module__ == "pyUltroid.modules._gorilla"


def test_install_is_idempotent_but_force_reasserts():
    addons = Modules()
    assert addons.install() == 6
    assert addons.install() == 0
    # A forced reinstall re-asserts the addons over later patches.
    assert addons.install(force=True) == 6


def test_invoke_wraps_with_business_connection():
    client = FakeClient()
    from telethon.functions import InvokeWithBusinessConnectionRequest
    from telethon.functions import messages as _m
    SendMessageRequest = _m.SendMessageRequest

    asyncio.run(client.invoke(SendMessageRequest(peer=None, message="x")))
    sent = client.sent[-1]
    assert isinstance(sent, InvokeWithBusinessConnectionRequest)
    assert sent.connection_id == "CONN-1"


def test_invoke_does_not_wrap_inline_requests():
    from telethon.functions import messages as _m
    SetBotGuestChatResultRequest = _m.SetBotGuestChatResultRequest

    client = FakeClient()
    asyncio.run(
        client.invoke(SetBotGuestChatResultRequest(query_id="q", result=[]))
    )
    assert type(client.sent[-1]).__name__ == "SetBotGuestChatResultRequest"


def test_invoke_passes_through_without_connection():
    from telethon.functions import messages as _m
    SendMessageRequest = _m.SendMessageRequest

    client = FakeClient(conn=None)
    asyncio.run(client.invoke(SendMessageRequest(peer=None, message="x")))
    assert type(client.sent[-1]).__name__ == "SendMessageRequest"


# ── router dispatch ────────────────────────────────────────────────────────


def _guest_update(client, sender=OWNER):
    upd = object.__new__(UpdateBotGuestChatQuery)
    upd._client = client
    upd.query_id = "q1"
    upd.query = "print(1)"
    upd.message = Message(
        date=None,
        id=1, message="@bot print(1)", peer_id=PeerChat(5), from_id=PeerUser(sender)
    )
    return upd


def _business_update(client, text, sender=OWNER):
    upd = object.__new__(UpdateBotNewBusinessMessage)
    upd._client = client
    upd.connection_id = "CONN-1"
    upd.message = Message(
        date=None,
        id=77, message=text, peer_id=PeerUser(1), from_id=PeerUser(sender)
    )
    return upd


def test_guest_update_reaches_answer_and_execute(monkeypatch):
    client = FakeClient()
    answered = {}

    async def fake_answer(self, value, *a, **k):
        answered["value"] = value
        return "MSG-1"

    async def fake_execute(update, msg_id):
        answered["msg_id"] = msg_id

    monkeypatch.setattr(UpdateBotGuestChatQuery, "answer", fake_answer)
    modules = Modules()
    modules.execute = fake_execute

    assert asyncio.run(modules.route(_guest_update(client))) is True
    assert answered == {"value": "article", "msg_id": "MSG-1"}


def test_guest_update_from_stranger_is_ignored(monkeypatch):
    client = FakeClient()
    called = []

    async def fake_execute(update, msg_id):
        called.append(msg_id)

    modules = Modules()
    modules.execute = fake_execute
    assert asyncio.run(modules.route(_guest_update(client, sender=1))) is False
    assert called == []


def test_business_message_with_hash_executes(monkeypatch):
    client = FakeClient()
    posted = {}

    class _Inner:
        id = 99

    class _Msg:
        message = _Inner()

    class Result:
        updates = [_Msg()]

    async def fake_respond(self, *a, **k):
        posted["args"] = a
        return Result()

    async def fake_execute(update, msg_id):
        posted["exec"] = msg_id

    monkeypatch.setattr(UpdateBotNewBusinessMessage, "respond", fake_respond)
    modules = Modules()
    modules.execute = fake_execute

    upd = _business_update(client, "print(1)#")
    assert asyncio.run(modules.route(upd)) is True
    assert posted["exec"] == 99


def test_business_message_without_hash_is_ignored(monkeypatch):
    client = FakeClient()
    called = []

    async def fake_execute(update, msg_id):
        called.append(msg_id)

    modules = Modules()
    modules.execute = fake_execute
    assert asyncio.run(modules.route(_business_update(client, "hello"))) is False
    assert called == []


def test_business_callback_cancels_the_matching_exec_task():
    """The inline "Raise" button cancels the task named Exec-<message id>."""
    modules = Modules()
    client = FakeClient()
    upd = object.__new__(UpdateBusinessBotCallbackQuery)
    upd._client = client
    upd.user_id = OWNER
    upd.connection_id = "CONN-1"
    upd.data = b"0"
    upd.message = Message(id=123, date=None, message=None, peer_id=PeerUser(1))

    async def scenario():
        task = asyncio.ensure_future(asyncio.sleep(30))
        task.set_name("Exec-123")
        await asyncio.sleep(0)  # let it start
        assert not task.done()
        handled = await modules.route(upd)
        await asyncio.sleep(0)
        return handled, task

    handled, task = asyncio.run(scenario())
    assert handled is True
    assert task.cancelled() or task.cancelling() or task.cancelled()


def test_business_callback_ignores_foreign_connection():
    modules = Modules()
    client = FakeClient(conn="OTHER")
    upd = object.__new__(UpdateBusinessBotCallbackQuery)
    upd._client = client
    upd.user_id = OWNER
    upd.connection_id = "CONN-1"
    upd.data = b"0"
    upd.message = Message(id=5, date=None, message=None, peer_id=PeerUser(1))
    assert asyncio.run(modules.route(upd)) is False


def test_inline_callback_ignored_for_stranger():
    modules = Modules()
    client = FakeClient()
    upd = object.__new__(UpdateInlineBotCallbackQuery)
    upd._client = client
    upd.user_id = 1
    upd.data = b"0"
    upd.msg_id = InputBotInlineMessageID(id=1, access_hash=2, dc_id=0)
    assert asyncio.run(modules.route(upd)) is False


def test_unknown_update_returns_false():
    modules = Modules()
    assert asyncio.run(modules.route(object())) is False


# ── guest answer: the previous silent-drop regression ──────────────────────


def test_guest_answer_always_answers_when_no_matching_inline_handler(monkeypatch):
    """Regression: a non-matching InlineQuery pattern must not swallow the query.

    The old ``_answer`` returned ``None`` whenever *any* InlineQuery handler
    existed, so the guest executor silently stopped working.
    """
    client = FakeClient()
    upd = object.__new__(UpdateBotGuestChatQuery)
    upd._client = client
    upd.query_id = "q9"
    upd.query = "print(1)"

    import pyUltroid

    monkeypatch.setattr(
        pyUltroid,
        "asst",
        type(
            "A",
            (),
            {
                "list_event_handlers": lambda self: [
                    (None, events.InlineQuery(pattern="calc"))
                ]
            },
        )(),
        raising=False,
    )

    class StubInline:
        def __init__(self, _client):
            pass

        async def article(self, *a, **k):
            return "ARTICLE"

    monkeypatch.setattr(_gorilla, "InlineResults", StubInline)

    sent = asyncio.run(Modules().answer(upd, "article", "T", text="x"))
    assert sent == "sent"
    assert type(client.sent[-1]).__name__ == "SetBotGuestChatResultRequest"


def test_guest_answer_defers_only_on_a_real_pattern_match(monkeypatch):
    client = FakeClient()
    upd = object.__new__(UpdateBotGuestChatQuery)
    upd._client = client
    upd.query_id = "q9"
    upd.query = "calc 2+2"

    import pyUltroid

    monkeypatch.setattr(
        pyUltroid,
        "asst",
        type(
            "A",
            (),
            {
                "list_event_handlers": lambda self: [
                    (None, events.InlineQuery(pattern="calc"))
                ]
            },
        )(),
        raising=False,
    )

    assert asyncio.run(Modules().answer(upd, "article", "T", text="x")) is None
    assert client.sent == []

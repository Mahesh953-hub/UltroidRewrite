"""Credential-free unit tests for the W1-C dispatcher evolution.

Covers ``pyUltroid/startup/dispatcher.py``:

* A11  — ``CommandEntry.__eq__`` / ``ListenerEntry.__eq__`` return
  ``NotImplemented`` for foreign types (no raise); consistent prefix
  stripping between ``register_command`` / ``unregister_command``; the Raw
  handler is bound exactly once (no remove/re-add churn); service messages
  are skipped.
* E12  — listener enable/disable toggles persisted in udB
  (``LISTENER_TOGGLES``) and honored at dispatch time.
* W1-C.5 — ListenerManager concept port: one Raw handler per client,
  identity-set dedup, ``once=`` support.
* W1-C.6 — the ``register_listen`` helper behind the ``ultroid_cmd``
  ``listen=`` kwarg (dispatcher route + Raw fallback).

The module under test is import-safe in isolation (telethon + stdlib only at
top level); the owner gate is monkeypatched exactly the way ``smoke_test.py``
does it. Async tests use ``asyncio.run`` — the suite has no pytest-asyncio.
"""

from __future__ import annotations

import asyncio

import pytest
from telethon.events import Raw
from telethon.types import (
    Message,
    MessageActionEmpty,
    MessageService,
    PeerUser,
    UpdateNewMessage,
    UpdateUserName,
)

from pyUltroid.startup.dispatcher import (
    TOGGLES_KEY,
    CommandDispatcher,
    CommandEntry,
    ListenerEntry,
    register_listen,
)

OWNER_ID = 123456789


class MemoryDB:
    """udB-shaped in-memory store (get_key/set_key/del_key)."""

    def __init__(self):
        self.data = {}

    def get_key(self, key):
        return self.data.get(key)

    def set_key(self, key, value):
        self.data[key] = value

    def del_key(self, key):
        self.data.pop(key, None)


class FakeClient:
    """Telethon-client-shaped handler registry with Telethon remove semantics."""

    def __init__(self, db=None):
        self.handlers = []  # list of (callback, event-builder)
        self.udB = db
        self.sent = []

    def add_event_handler(self, callback, event=None):
        self.handlers.append((callback, event))

    def remove_event_handler(self, callback, event=None):
        found = 0
        for pair in list(self.handlers):
            cb, ev = pair
            if cb == callback and (event is None or isinstance(ev, type(event))):
                self.handlers.remove(pair)
                found += 1
        return found

    async def send_message(self, peer, *args, **kwargs):
        self.sent.append((peer, args, kwargs))


@pytest.fixture()
def client():
    return FakeClient()


@pytest.fixture()
def dispatcher(client):
    return CommandDispatcher(client, prefix=(".", "/"))


@pytest.fixture(autouse=True)
def owner_gate(monkeypatch):
    """Dispatcher imports owner_and_sudos from pyUltroid._misc at call time."""
    monkeypatch.setattr("pyUltroid._misc.owner_and_sudos", lambda: [OWNER_ID])


def make_update(text=".cmd args", user_id=OWNER_ID):
    msg = Message(
        id=123,
        peer_id=PeerUser(user_id=user_id),
        date=None,
        message=text,
        from_id=PeerUser(user_id=user_id),
    )
    update = UpdateNewMessage(message=msg, pts=1, pts_count=1)
    return update


class RespondRecorder:
    def __init__(self):
        self.calls = []

    async def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))


# ── W1-C.1: __eq__ returns NotImplemented ────────────────────────────────────


class TestEntryEquality:
    def test_command_entry_eq_foreign_type_returns_not_implemented(self):
        entry = CommandEntry(name="x", func=lambda: None)
        assert entry.__eq__(object()) is NotImplemented
        assert entry.__eq__(None) is NotImplemented

    def test_listener_entry_eq_foreign_type_returns_not_implemented(self):
        entry = ListenerEntry(update_type=UpdateNewMessage, func=lambda: None)
        assert entry.__eq__(object()) is NotImplemented
        assert entry.__eq__("listener") is NotImplemented

    def test_reflected_comparisons_do_not_raise(self):
        cmd = CommandEntry(name="x", func=lambda: None)
        lst = ListenerEntry(update_type=UpdateNewMessage, func=lambda: None)
        # The old `raise NotImplementedError` made each of these crash.
        assert (cmd == object()) is False
        assert (cmd != object()) is True
        assert (None in [cmd]) is False
        assert (object() in [lst]) is False
        assert (cmd == lst) is False  # cross-type: both return NotImplemented

    def test_eq_still_matches_by_func_identity(self):
        def handler():
            pass

        assert CommandEntry(name="a", func=handler) == CommandEntry(
            name="b", func=handler
        )
        assert ListenerEntry(
            update_type=UpdateNewMessage, func=handler
        ) == ListenerEntry(update_type=UpdateNewMessage, func=handler)


# ── W1-C.2: consistent prefix handling ───────────────────────────────────────


class TestPrefixConsistency:
    def test_register_and_unregister_share_normalization(self, dispatcher):
        def handler():
            pass

        dispatcher.register_command(".cmd", handler)
        assert list(dispatcher._commands) == ["cmd"]

        dispatcher.unregister_command(".cmd", handler)
        assert dispatcher._commands == {}

    def test_second_configured_prefix_strips_symmetrically(self, dispatcher):
        def handler():
            pass

        dispatcher.register_command("/cmd", handler)
        assert list(dispatcher._commands) == ["cmd"]
        # old code stripped "".join(prefix) as an lstrip char-set here
        dispatcher.unregister_command("/cmd", handler)
        assert dispatcher._commands == {}

    def test_at_most_one_prefix_is_stripped(self, dispatcher):
        def handler():
            pass

        dispatcher.register_command("..cmd", handler)
        assert list(dispatcher._commands) == [".cmd"]  # not "cmd" (old lstrip behavior)
        dispatcher.unregister_command("..cmd", handler)
        assert dispatcher._commands == {}

    def test_bare_name_roundtrip(self, dispatcher):
        def handler():
            pass

        dispatcher.register_command("Cmd", handler)
        assert list(dispatcher._commands) == ["cmd"]  # lowercased
        dispatcher.unregister_command("cmd", handler)
        assert dispatcher._commands == {}


# ── W1-C.3/.5: one Raw handler per client, bound once, no churn ──────────────


class TestBindOnce:
    def test_single_bare_raw_handler_bound_on_start(self, dispatcher, client):
        dispatcher.start()
        assert len(client.handlers) == 1
        callback, event = client.handlers[0]
        assert isinstance(event, Raw)
        assert event.types is None  # bare Raw: fan-out happens in _dispatch
        assert callback == dispatcher._dispatch

    def test_registration_churn_does_not_rebind(self, dispatcher, client):
        dispatcher.start()

        def cmd():
            pass

        def lst(update):
            pass

        for i in range(5):
            dispatcher.register_command(f"c{i}", cmd)
            dispatcher.register_listener(UpdateNewMessage, lst, name=f"l{i}")
        for i in range(5):
            dispatcher.unregister_command(f"c{i}", cmd)
        dispatcher.unregister_listener(UpdateNewMessage, lst)

        assert len(client.handlers) == 1  # never removed/re-added
        assert dispatcher._commands == {}
        assert dispatcher._listeners == {}

    def test_stop_unbinds_and_restart_rebinds(self, dispatcher, client):
        dispatcher.start()
        dispatcher.stop()
        assert client.handlers == []
        dispatcher.start()
        assert len(client.handlers) == 1

    def test_cmd_handler_alias_points_at_dispatch(self, dispatcher):
        # smoke_test.py drives routing through dispatcher._cmd_handler(update)
        assert dispatcher._cmd_handler == dispatcher._dispatch


# ── command dispatch (live state, owner gate, service skip) ──────────────────


class TestCommandDispatch:
    def test_command_routes_and_responds(self, dispatcher):
        seen = {}

        @dispatcher.command("hello")
        async def hello(update, args, reply):
            seen["args"] = args
            seen["reply"] = reply
            return "response_ok"

        update = make_update(".hello world")
        responder = RespondRecorder()
        update.respond = responder

        asyncio.run(dispatcher._cmd_handler(update))

        assert seen == {"args": "world", "reply": None}
        assert responder.calls == [(("response_ok",), {})]

    def test_tuple_result_passes_buttons(self, dispatcher):
        @dispatcher.command("btn")
        async def btn(update, args, reply):
            return ("text_out", "BUTTONS")

        update = make_update(".btn")
        responder = RespondRecorder()
        update.respond = responder

        asyncio.run(dispatcher._cmd_handler(update))
        assert responder.calls == [(("text_out",), {"buttons": "BUTTONS"})]

    def test_second_prefix_also_routes(self, dispatcher):
        called = []

        @dispatcher.command("dual")
        async def dual(update, args, reply):
            called.append(True)

        asyncio.run(dispatcher._cmd_handler(make_update("/dual")))
        assert called == [True]

    def test_non_owner_is_ignored(self, dispatcher):
        called = []

        @dispatcher.command("mine")
        async def mine(update, args, reply):
            called.append(True)

        asyncio.run(dispatcher._cmd_handler(make_update(".mine", user_id=999)))
        assert called == []

    def test_exact_duplicate_command_registration_is_ignored(self, dispatcher):
        def handler(update, args, reply):
            pass

        dispatcher.register_command("dup", handler)
        dispatcher.register_command("dup", handler)
        assert len(dispatcher._commands["dup"]) == 1

    def test_unregister_takes_effect_without_rebind(self, dispatcher, client):
        called = []

        @dispatcher.command("gone")
        async def gone(update, args, reply):
            called.append(True)

        dispatcher.unregister_command("gone", gone)
        asyncio.run(dispatcher._cmd_handler(make_update(".gone")))
        assert called == []
        assert len(client.handlers) == 1


class TestServiceMessageSkip:
    def test_service_message_is_skipped_before_patching(self, dispatcher):
        called = []

        @dispatcher.command("svc")
        async def svc(update, args, reply):
            called.append(True)

        service_msg = MessageService(
            id=1,
            peer_id=PeerUser(user_id=OWNER_ID),
            from_id=PeerUser(user_id=OWNER_ID),  # would pass the owner gate
            date=None,
            action=MessageActionEmpty(),
        )
        update = UpdateNewMessage(message=service_msg, pts=1, pts_count=1)

        # must not raise (pre-skip code reached `msg.message` -> AttributeError
        # on MessageService) and must not dispatch or patch helpers
        asyncio.run(dispatcher._cmd_handler(update))
        assert called == []
        assert not hasattr(update, "respond")
        assert not hasattr(update, "reply")
        assert not hasattr(update, "get_reply_message")


# ── W1-C.5: listener dedup + once ────────────────────────────────────────────


class TestListenerDedup:
    def test_same_func_object_registers_once(self, dispatcher):
        def listener(update):
            pass

        dispatcher.register_listener(UpdateNewMessage, listener)
        dispatcher.register_listener(UpdateNewMessage, listener)
        assert len(dispatcher._listeners[UpdateNewMessage]) == 1

    def test_distinct_funcs_both_register(self, dispatcher):
        def l1(update):
            pass

        def l2(update):
            pass

        dispatcher.register_listener(UpdateNewMessage, l1)
        dispatcher.register_listener(UpdateNewMessage, l2)
        assert len(dispatcher._listeners[UpdateNewMessage]) == 2

    def test_unregister_reenables_registration(self, dispatcher):
        def listener(update):
            pass

        dispatcher.register_listener(UpdateNewMessage, listener)
        dispatcher.unregister_listener(UpdateNewMessage, listener)
        assert dispatcher._listeners == {}

        dispatcher.register_listener(UpdateNewMessage, listener)
        assert len(dispatcher._listeners[UpdateNewMessage]) == 1


class TestOnceSemantics:
    def test_once_listener_fires_once_then_unregisters(self, dispatcher):
        calls = []

        @dispatcher.listen(UpdateNewMessage, once=True)
        async def one_shot(update):
            calls.append(update)

        update = make_update("no prefix here")
        asyncio.run(dispatcher._dispatch(update))
        asyncio.run(dispatcher._dispatch(update))

        assert len(calls) == 1
        assert dispatcher._listeners == {}  # auto-unregistered

    def test_match_miss_does_not_consume_once(self, dispatcher):
        calls = []

        @dispatcher.listen(UpdateNewMessage, once=True, match=lambda u: u.pts == 42)
        async def picky(update):
            calls.append(update)

        asyncio.run(dispatcher._dispatch(make_update("x")))  # pts=1 -> filtered out
        assert calls == []
        assert UpdateNewMessage in dispatcher._listeners  # still registered

        update = make_update("x")
        update.pts = 42
        asyncio.run(dispatcher._dispatch(update))
        assert len(calls) == 1
        assert dispatcher._listeners == {}

    def test_exception_still_unregisters_once(self, dispatcher):
        calls = []

        @dispatcher.listen(UpdateNewMessage, once=True)
        async def broken(update):
            calls.append(update)
            raise RuntimeError("boom")

        asyncio.run(dispatcher._dispatch(make_update("x")))  # logged, swallowed
        assert len(calls) == 1
        assert dispatcher._listeners == {}

    def test_regular_listener_is_not_unregistered(self, dispatcher):
        calls = []

        @dispatcher.listen(UpdateNewMessage)
        async def persistent(update):
            calls.append(update)

        for _ in range(3):
            asyncio.run(dispatcher._dispatch(make_update("x")))
        assert len(calls) == 3
        assert UpdateNewMessage in dispatcher._listeners


# ── E12: persisted toggles ───────────────────────────────────────────────────


class TestPersistedToggles:
    def test_listener_enabled_by_default(self, dispatcher):
        @dispatcher.listen(UpdateNewMessage, name="feat.x")
        async def listener(update):
            pass

        assert dispatcher.is_listener_enabled("feat.x") is True

    def test_disable_skips_dispatch_and_persists_to_udb(self, client):
        dispatcher = CommandDispatcher(client, prefix=".")
        client.udB = MemoryDB()
        calls = []

        @dispatcher.listen(UpdateNewMessage, name="feat.y")
        async def listener(update):
            calls.append(update)

        dispatcher.set_listener_enabled("feat.y", False)
        assert client.udB.get_key(TOGGLES_KEY) == {"feat.y": False}

        asyncio.run(dispatcher._dispatch(make_update("x")))
        assert calls == []

        dispatcher.set_listener_enabled("feat.y", True)
        asyncio.run(dispatcher._dispatch(make_update("x")))
        assert len(calls) == 1

    def test_toggles_shared_across_dispatchers_via_udb(self, client):
        """A second dispatcher on the same client (restart proxy) sees the
        persisted state — proving the toggle is udB-backed, not in-memory."""
        client.udB = MemoryDB()
        first = CommandDispatcher(client, prefix=".")
        first.set_listener_enabled("feat.z", False)

        second = CommandDispatcher(client, prefix=".")
        assert second.is_listener_enabled("feat.z") is False

    def test_toggle_key_defaults_to_module_qualname(self, dispatcher):
        @dispatcher.listen(UpdateUserName)
        async def uname_listener(update):
            pass

        entry = dispatcher._listeners[UpdateUserName][0]
        assert (
            entry.key
            == f"{__name__}.TestPersistedToggles.test_toggle_key_defaults_to_module_qualname.<locals>.uname_listener"
        )

    def test_explicit_name_wins(self, dispatcher):
        @dispatcher.listen(UpdateUserName, name="custom.key")
        async def listener(update):
            pass

        assert dispatcher._listeners[UpdateUserName][0].key == "custom.key"

    def test_listener_states_snapshot(self, client):
        client.udB = MemoryDB()
        dispatcher = CommandDispatcher(client, prefix=".")

        @dispatcher.listen(UpdateUserName, name="feat.state", priority=10)
        async def listener(update):
            pass

        dispatcher.set_listener_enabled("feat.state", False)
        states = dispatcher.listener_states()
        assert states == [
            {
                "key": "feat.state",
                "type": "UpdateUserName",
                "enabled": False,
                "once": False,
                "priority": 10,
            }
        ]

    def test_db_less_client_uses_in_memory_fallback(self, dispatcher):
        assert dispatcher._db() is None
        dispatcher.set_listener_enabled("k", False)
        assert dispatcher.is_listener_enabled("k") is False


# ── W1-C.6: register_listen (the ultroid_cmd `listen=` implementation) ───────


class TestRegisterListen:
    def test_registers_on_dispatcher_when_available(self, dispatcher, client):
        client.dispatcher = dispatcher

        async def handler(update):
            pass

        out = register_listen(
            handler, client=client, listen=UpdateUserName, name="kw.x"
        )
        assert out is handler
        entries = dispatcher._listeners[UpdateUserName]
        assert len(entries) == 1
        assert entries[0].func is handler
        assert entries[0].key == "kw.x"

    def test_listen_accepts_type_lists(self, dispatcher, client):
        client.dispatcher = dispatcher

        async def handler(update):
            pass

        register_listen(
            handler, client=client, listen=[UpdateUserName, UpdateNewMessage]
        )
        assert len(dispatcher._listeners[UpdateUserName]) == 1
        assert len(dispatcher._listeners[UpdateNewMessage]) == 1

    def test_listen_dedupes_double_import(self, dispatcher, client):
        client.dispatcher = dispatcher

        async def handler(update):
            pass

        register_listen(handler, client=client, listen=UpdateUserName)
        register_listen(handler, client=client, listen=UpdateUserName)
        assert len(dispatcher._listeners[UpdateUserName]) == 1

    def test_fallback_registers_raw_when_no_dispatcher(self, client):
        async def handler(update):
            pass

        register_listen(handler, client=client, listen=UpdateUserName)
        assert len(client.handlers) == 1
        _, event = client.handlers[0]
        assert isinstance(event, Raw)

    def test_fallback_match_and_once_semantics(self, client):
        calls = []

        async def handler(update):
            calls.append(update)

        register_listen(
            handler,
            client=client,
            listen=UpdateUserName,
            match=lambda u: u.user_id == 7,
            once=True,
        )
        wrapper = client.handlers[0][0]

        # non-matching type -> ignored
        asyncio.run(wrapper(make_update("x")))
        # matching type, predicate False -> ignored, once NOT consumed
        asyncio.run(
            wrapper(
                UpdateUserName(user_id=1, first_name="a", last_name="", usernames=[])
            )
        )
        assert calls == []
        assert len(client.handlers) == 1
        # matching -> fires once and self-removes from the client registry
        # (Telethon would not deliver again after removal, so we assert the
        # registry state instead of re-invoking the wrapper directly)
        asyncio.run(
            wrapper(
                UpdateUserName(user_id=7, first_name="a", last_name="", usernames=[])
            )
        )
        assert len(calls) == 1
        assert client.handlers == []

    def test_none_client_is_a_noop(self):
        async def handler(update):
            pass

        assert register_listen(handler, client=None, listen=UpdateUserName) is handler

    def test_fallback_swallows_and_logs_listener_exception(self, client, caplog):
        """A raising fallback listener must not propagate (same semantics as
        CommandDispatcher._run_listener_entry)."""

        async def broken(update):
            raise RuntimeError("fallback boom")

        register_listen(broken, client=client, listen=UpdateUserName)
        wrapper = client.handlers[0][0]

        update = UpdateUserName(user_id=1, first_name="a", last_name="", usernames=[])
        with caplog.at_level("ERROR", logger="pyUltroid.startup.dispatcher"):
            asyncio.run(wrapper(update))  # must not raise

        assert any(
            "fallback boom" in str(r.exc_info[1]) for r in caplog.records if r.exc_info
        )
        assert len(client.handlers) == 1  # non-once listener stays registered

    def test_fallback_once_consumed_even_when_callback_raises(self, client, caplog):
        """The once is consumed by the attempt: a raising once-listener still
        self-removes from the client registry."""

        async def broken_once(update):
            raise RuntimeError("once boom")

        register_listen(broken_once, client=client, listen=UpdateUserName, once=True)
        wrapper = client.handlers[0][0]

        update = UpdateUserName(user_id=1, first_name="a", last_name="", usernames=[])
        with caplog.at_level("ERROR", logger="pyUltroid.startup.dispatcher"):
            asyncio.run(wrapper(update))  # logged, swallowed, once consumed

        assert client.handlers == []

"""Offline tests for the bounded bot-to-bot conversation feature."""

from __future__ import annotations

import ast
from pathlib import Path

from pyUltroid.b2b import B2BConversationManager, is_bot_to_bot_disabled

ROOT = Path(__file__).resolve().parents[1]


def _manager(config=None):
    clock_value = [1000.0]
    saved = []
    manager = B2BConversationManager(
        config,
        save=lambda value: saved.append(value.copy()),
        clock=lambda: clock_value[0],
    )
    manager._test_clock = clock_value
    manager._test_saved = saved
    return manager


def _private_session(manager, bot_id=101):
    manager.set_enabled(True)
    manager.allow_bot(bot_id)
    return manager.start(bot_id, bot_id, "peerbot")


def _accept(manager, session, message_id, *, reply_to=None, is_private=True):
    return manager.accept_incoming(
        chat_id=session.chat_id,
        bot_id=session.bot_id,
        message_id=message_id,
        reply_to_id=reply_to,
        is_private=is_private,
        sender_is_bot=True,
    )


def test_feature_is_off_by_default_and_only_config_is_persisted():
    manager = _manager()

    assert manager.enabled is False
    assert manager.active_count() == 0
    manager.set_enabled(True)
    manager.allow_bot(101)

    assert manager._test_saved[-1] == {
        "enabled": True,
        "allowed_bots": [101],
        "allowed_groups": {},
    }


def test_private_pair_requires_feature_enabled_and_allowlisted_bot():
    manager = _manager()
    try:
        manager.start(101, 101)
    except RuntimeError as exc:
        assert "disabled" in str(exc)
    else:
        raise AssertionError("disabled feature started a session")

    manager.set_enabled(True)
    try:
        manager.start(101, 101)
    except PermissionError:
        pass
    else:
        raise AssertionError("unallowlisted bot started a session")

    manager.allow_bot(101)
    session = manager.start(101, 101)
    assert manager.is_active(session)


def test_group_scope_is_explicit_and_does_not_grant_private_access():
    manager = _manager()
    manager.set_enabled(True)
    manager.allow_bot(101, -10055)
    assert manager.is_allowed(101, -10055)
    assert not manager.is_allowed(101, 101)

    session = manager.start(101, -10055, "peerbot")
    manager.record_outbound(session, 700)

    unrelated, reason = _accept(
        manager, session, 800, reply_to=999, is_private=False
    )
    assert unrelated is None
    assert reason == "not_in_reply_chain"

    accepted, reason = _accept(
        manager, session, 800, reply_to=700, is_private=False
    )
    assert accepted is session
    assert reason == "accepted"


def test_incoming_requires_bot_sender_and_deduplicates_message_ids():
    manager = _manager()
    session = _private_session(manager)
    rejected, reason = manager.accept_incoming(
        chat_id=101,
        bot_id=101,
        message_id=1,
        reply_to_id=None,
        is_private=True,
        sender_is_bot=False,
    )
    assert rejected is None
    assert reason == "not_bot"

    accepted, reason = _accept(manager, session, 1)
    assert accepted is session
    assert reason == "accepted"
    duplicate, reason = _accept(manager, session, 1)
    assert duplicate is None
    assert reason == "duplicate"


def test_group_session_cannot_be_treated_as_a_private_bot_chat():
    manager = _manager()
    manager.set_enabled(True)
    manager.allow_bot(101, -10055)
    session = manager.start(101, -10055, "peerbot")

    rejected, reason = manager.accept_incoming(
        chat_id=-10055,
        bot_id=101,
        message_id=1,
        reply_to_id=None,
        is_private=True,
        sender_is_bot=True,
    )
    assert rejected is None
    assert reason == "private_peer_mismatch"
    assert manager.is_active(session)


def test_chat_rate_limit_is_enforced():
    manager = _manager()
    session = _private_session(manager)
    manager.MAX_CHAT_MESSAGES_PER_MINUTE = 1

    accepted, reason = _accept(manager, session, 1)
    assert accepted is session
    assert reason == "accepted"
    limited, reason = _accept(manager, session, 2)
    assert limited is None
    assert reason == "rate_limited"


def test_peer_bot_rate_limit_spans_its_private_and_group_sessions():
    manager = _manager()
    manager.set_enabled(True)
    manager.allow_bot(101)
    manager.allow_bot(101, -10055)
    private = manager.start(101, 101, "peerbot")
    group = manager.start(101, -10055, "peerbot")
    manager.record_outbound(group, 700)
    manager.MAX_CHAT_MESSAGES_PER_MINUTE = 10
    manager.MAX_BOT_MESSAGES_PER_MINUTE = 1

    first, reason = _accept(manager, private, 1)
    assert first is private
    assert reason == "accepted"
    limited, reason = _accept(
        manager, group, 2, reply_to=700, is_private=False
    )
    assert limited is None
    assert reason == "rate_limited"


def test_idle_timeout_ends_session():
    manager = _manager()
    session = _private_session(manager)
    manager._test_clock[0] += manager.IDLE_TIMEOUT_SECONDS + 1

    expired, reason = _accept(manager, session, 2)
    assert expired is None
    assert reason == "expired"
    assert manager.active_count() == 0


def test_total_session_timeout_applies_even_if_idle_timeout_does_not():
    manager = _manager()
    session = _private_session(manager)
    manager.MAX_SESSION_SECONDS = 5
    manager._test_clock[0] += 3
    first, reason = _accept(manager, session, 1)
    assert first is session
    assert reason == "accepted"

    manager._test_clock[0] += 3
    expired, reason = _accept(manager, session, 2)
    assert expired is None
    assert reason == "expired"


def test_turn_limit_stops_a_repeated_reply_loop():
    manager = _manager()
    session = _private_session(manager)
    manager.MAX_TURNS = 2

    first, _ = _accept(manager, session, 1)
    second, _ = _accept(manager, session, 2)
    third, reason = _accept(manager, session, 3)

    assert first is session
    assert second is session
    assert third is None
    assert reason == "turn_limit"
    assert manager.active_count() == 0


def test_disable_and_revoke_stop_sessions_immediately():
    manager = _manager()
    session = _private_session(manager)
    assert manager.revoke_bot(session.bot_id)
    assert not manager.is_active(session)

    session = _private_session(manager, bot_id=202)
    manager.set_enabled(False)
    assert not manager.is_active(session)
    assert manager.active_count() == 0


def test_disabled_rpc_detection_handles_tl_code_and_camel_case_class():
    class RpcError(Exception):
        message = "USER_BOT_TO_BOT_DISABLED"

    class UserBotToBotDisabledError(Exception):
        pass

    assert is_bot_to_bot_disabled(RpcError())
    assert is_bot_to_bot_disabled(UserBotToBotDisabledError())
    assert not is_bot_to_bot_disabled(RuntimeError("unrelated Telegram failure"))


def test_plugin_registers_four_commands_and_one_incoming_bot_handler():
    path = ROOT / "plugins" / "bot2bot.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    command_functions = [
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and any(
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Name)
            and decorator.func.id == "ultroid_cmd"
            for decorator in node.decorator_list
        )
    ]
    assert len(command_functions) == 4
    assert any(
        isinstance(node, ast.If)
        and any(
            isinstance(call, ast.Call)
            and isinstance(call.func, ast.Attribute)
            and call.func.attr == "add_event_handler"
            for call in ast.walk(node)
        )
        for node in tree.body
    )

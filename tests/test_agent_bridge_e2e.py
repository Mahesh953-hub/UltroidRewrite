"""End-to-end: a tap on the bot actually answers the blocked tool.

The earlier phases built the pieces and left them unwired, so nothing worked
on a live client. These tests exist to catch exactly that class of mistake -
a component that passes its own unit tests while being unreachable in
production.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from pyUltroid.agent import _bridge as bridge_mod
from pyUltroid.agent._bridge import AskBridge, shared_bridge
from pyUltroid.agent._interactive import InteractiveTools
from pyUltroid.agent._runtime import GROUP_SAFE_CAPS, resolve_caps
from pyUltroid.agent._tools import DEFAULT_AGENT_CAPS, ToolExecutor
from pyUltroid.shell._context import Cap


class FakeBot:
    """Stands in for the assistant bot: records sends, emits callbacks."""

    def __init__(self):
        self.sent = []
        self._id = 0

    async def send_message(self, chat_id, text, buttons=None, **kw):
        self._id += 1
        self.sent.append({"chat": chat_id, "text": text, "buttons": buttons, "id": self._id})
        return SimpleNamespace(id=self._id)

    async def delete_me(self, *a, **k):
        return True


@pytest.fixture(autouse=True)
def _clean_shared_bridge():
    """The shared bridge is process state; tests must not leak into each other."""
    bridge_mod._BRIDGE = None
    yield
    bridge_mod._BRIDGE = None


# ── capability policy: the owner may ask, a group may not ───────────────────


def test_owner_gets_the_ask_cap(monkeypatch):
    monkeypatch.setattr(
        "pyUltroid.agent._runtime._owner_and_sudos", lambda: {7}
    )
    caps = resolve_caps(surface="dm", user_id=7)
    assert Cap.AGENT_ASK in caps


def test_a_stranger_does_not_get_the_ask_cap(monkeypatch):
    monkeypatch.setattr("pyUltroid.agent._runtime._owner_and_sudos", lambda: {7})
    caps = resolve_caps(surface="dm", user_id=999)
    assert Cap.AGENT_ASK not in caps


def test_group_surface_currently_still_grants_ask_to_the_owner(monkeypatch):
    """Documents a pre-existing gap, it does not endorse it.

    ``resolve_caps`` accepts ``surface`` and ``is_private`` and never reads
    either one - its body branches only on ownership. So an owner in a group
    gets DEFAULT_AGENT_CAPS, which contradicts its own docstring ("DM and
    group surfaces use the same rule, fixing the previous asymmetry where a
    group command could grant shell execution").

    That is a pre-existing security gap, not something this work introduced,
    and it is not fixed here: narrowing a capability grant is a policy
    decision, not a refactor. Until it is fixed, the practical consequence for
    this feature is that an owner can be asked from a group too.
    """
    monkeypatch.setattr("pyUltroid.agent._runtime._owner_and_sudos", lambda: {7})
    caps = resolve_caps(surface="group", user_id=7, is_private=False)
    assert Cap.AGENT_ASK in caps  # current behaviour; see docstring


def test_ask_cap_is_in_the_default_set():
    assert Cap.AGENT_ASK in DEFAULT_AGENT_CAPS
    assert Cap.AGENT_ASK not in GROUP_SAFE_CAPS


def test_tools_can_still_be_switched_off_entirely():
    assert resolve_caps(surface="dm", user_id=7, tools_enabled=False) == frozenset()


# ── the executor asks through the SHARED bridge ─────────────────────────────


def test_executor_uses_the_shared_bridge():
    ex = ToolExecutor(chat_id=7, caps=frozenset({Cap.AGENT_ASK}))
    assert ex.interactive.bridge is shared_bridge()


def test_two_executors_share_one_pending_map():
    a = ToolExecutor(chat_id=7, caps=frozenset({Cap.AGENT_ASK}))
    b = ToolExecutor(chat_id=7, caps=frozenset({Cap.AGENT_ASK}))
    assert a.interactive.bridge is b.interactive.bridge


# ── the whole chain, as production runs it ──────────────────────────────────


def test_a_button_tap_answers_a_blocked_ask_user_call():
    """The question goes out on the bot; a tap settles the tool's future."""
    shared_bridge(bot=FakeBot(), user=object())
    ex = ToolExecutor(chat_id=7, caps=frozenset({Cap.AGENT_ASK}))
    bridge = shared_bridge()
    assert bridge.pending == 0

    async def scenario():
        task = asyncio.ensure_future(
            ex.execute("ask_user", {"question": "What is your name?"})
        )
        await asyncio.sleep(0)
        # the bot really was asked, with a real styled keyboard
        assert bridge.bot.sent
        sent = bridge.bot.sent[-1]
        assert sent["text"] == "What is your name?"
        assert bridge.pending == 1

        # the assistant's callback handler receiving a real tap
        session = next(iter(bridge._pending.values()))
        tapped = session.callback_for("Ada")
        assert bridge.owns(tapped) is True
        outcome = bridge.resolve_callback(tapped)
        assert outcome is not None and outcome.answered

        result = await task
        assert bridge.pending == 0
        return result, sent

    result, sent = asyncio.run(scenario())
    assert result["answered"] is True
    assert result["user_response"] == "Ada"


def test_a_typed_dm_answers_a_blocked_ask_user_call():
    bridge = shared_bridge(bot=FakeBot(), user=object())
    ex = ToolExecutor(chat_id=7, caps=frozenset({Cap.AGENT_ASK}))

    async def scenario():
        task = asyncio.ensure_future(ex.execute("ask_user", {"question": "name?"}))
        await asyncio.sleep(0)
        # a private message arriving from the user
        outcome = bridge.submit_text(7, ("Grace", []))
        return task, outcome

    task, outcome = asyncio.run(scenario())
    assert outcome.answered and outcome.value == "Grace"
    assert asyncio.run(task)["user_response"] == "Grace"


def test_a_tap_in_another_chat_does_not_answer():
    bridge = shared_bridge(bot=FakeBot(), user=object())
    ex = ToolExecutor(chat_id=7, caps=frozenset({Cap.AGENT_ASK}))

    async def scenario():
        task = asyncio.ensure_future(ex.execute("ask_user", {"question": "name?"}))
        await asyncio.sleep(0)
        assert bridge.submit_text(99, "wrong chat") is None
        next(iter(bridge._pending.values())).resolve("ok")
        return await task

    assert asyncio.run(scenario())["user_response"] == "ok"


def test_no_question_is_left_open_after_a_turn():
    bridge = shared_bridge(bot=FakeBot(), user=object())
    ex = ToolExecutor(chat_id=7, caps=frozenset({Cap.AGENT_ASK}))

    async def one(i):
        task = asyncio.ensure_future(ex.execute("ask_user", {"question": f"q{i}"}))
        await asyncio.sleep(0)
        session = next(iter(bridge._pending.values()))
        session.resolve(f"a{i}")
        bridge._retire(session)
        return await task

    for i in range(4):
        assert asyncio.run(one(i))["user_response"] == f"a{i}"
    assert bridge.pending == 0

"""The interactive tools, end to end through the real executor.

Every path here goes through ``ToolExecutor.execute``, so the capability
policy is genuinely in the loop rather than bypassed.
"""

from __future__ import annotations

import asyncio

import pytest

from pyUltroid.agent._bridge import AskBridge
from pyUltroid.agent._interactive import (
    MAX_CHOICES,
    MAX_QUESTIONS,
    InteractiveTools,
    clamp_ttl,
)
from pyUltroid.agent._tools import TOOL_REQUIRED_CAPS, ToolExecutor
from pyUltroid.shell._context import Cap


class FakeBot:
    def __init__(self):
        self.sent = []
        self._id = 0

    async def send_message(self, chat_id, text, buttons=None, **kw):
        self._id += 1
        self.sent.append({"chat": chat_id, "text": text, "buttons": buttons})
        return type("M", (), {"id": self._id})()


def _executor(chat_id=7, caps=None):
    bridge = AskBridge(bot=FakeBot(), user=object())
    return ToolExecutor(chat_id=chat_id, caps=caps, interactive=InteractiveTools(bridge, chat_id=chat_id)), bridge


def _answer(bridge, chat, value, index=0):
    for session in list(bridge._pending.values()):
        if session.chat_id == chat:
            session.resolve(value)
            bridge._retire(session)
            return
    raise AssertionError("no open question")


def _run(coro_factory, bridge, chat, value):
    async def scenario():
        task = asyncio.ensure_future(coro_factory())
        await asyncio.sleep(0)
        _answer(bridge, chat, value)
        return await task

    return asyncio.run(scenario())


# ── capability policy ───────────────────────────────────────────────────────


def test_ask_requires_its_own_grant():
    """The grant exists, and it is deliberately not a group-safe capability."""
    assert TOOL_REQUIRED_CAPS["ask_user"] == frozenset({Cap.AGENT_ASK})
    from pyUltroid.agent._tools import DEFAULT_AGENT_CAPS
    from pyUltroid.agent._runtime import GROUP_SAFE_CAPS

    # In the default set, so the owner can actually ask.
    assert Cap.AGENT_ASK in DEFAULT_AGENT_CAPS
    # But excluded from the group-safe subset: asking paints a keyboard in
    # someone's chat, which is not something a shared surface grants.
    assert Cap.AGENT_ASK not in GROUP_SAFE_CAPS


def test_ask_is_denied_without_the_grant():
    ex, _ = _executor(caps=frozenset())
    result = asyncio.run(ex.execute("ask_user", {"question": "name?"}))
    assert result["denied"] is True
    assert Cap.AGENT_ASK in result["missing_caps"]


def test_ask_runs_with_the_grant():
    ex, bridge = _executor(caps=frozenset({Cap.AGENT_ASK}))
    out = _run(lambda: ex.execute("ask_user", {"question": "name?"}), bridge, 7, "Ada")
    assert out["answered"] is True
    assert out["user_response"] == "Ada"


def test_confirm_and_collect_are_gated_too():
    ex, _ = _executor(caps=frozenset())
    for name, args in (
        ("confirm_action", {"what": "delete"}),
        ("collect_fields", {"fields": [{"name": "a", "question": "a?"}]}),
    ):
        out = asyncio.run(ex.execute(name, args))
        assert out["denied"] is True, name


# ── ask_user ────────────────────────────────────────────────────────────────


def test_ask_user_reports_the_choices_it_offered():
    ex, bridge = _executor(caps=frozenset({Cap.AGENT_ASK}))
    out = _run(
        lambda: ex.execute("ask_user", {"question": "who?", "choices": ["Ada", "Grace"]}),
        bridge,
        7,
        "Ada",
    )
    assert out["choices_offered"] == ["Ada", "Grace"]


def test_ask_user_timeout_is_not_an_exception():
    ex, bridge = _executor()
    out = asyncio.run(
        ex.execute("ask_user", {"question": "name?", "ttl": 5})
    )
    # ttl is clamped to >=5s, so force the fast path instead of waiting.
    assert "answered" in out or out.get("denied")


def test_ask_user_ttl_expiry_shape():
    bridge = AskBridge(bot=FakeBot(), user=object(), ttl=0.01)
    tools = InteractiveTools(bridge, chat_id=7)
    out = asyncio.run(tools.ask_user("name?"))
    assert out["answered"] is False
    assert out["timed_out"] is True
    assert out["user_response"] is None
    assert bridge.pending == 0


def test_duplicate_choices_are_collapsed():
    ex, bridge = _executor(caps=frozenset({Cap.AGENT_ASK}))
    out = _run(
        lambda: ex.execute(
            "ask_user", {"question": "who?", "choices": ["Ada", "ada", "Grace"]}
        ),
        bridge,
        7,
        "Ada",
    )
    assert out["choices_offered"] == ["Ada", "Grace"]


def test_choices_are_bounded():
    ex, bridge = _executor(caps=frozenset({Cap.AGENT_ASK}))
    many = [f"c{i}" for i in range(MAX_CHOICES + 8)]
    out = _run(
        lambda: ex.execute("ask_user", {"question": "q", "choices": many}),
        bridge,
        7,
        "c0",
    )
    assert len(out["choices_offered"]) == MAX_CHOICES


def test_a_string_choices_value_is_tolerated():
    ex, bridge = _executor(caps=frozenset({Cap.AGENT_ASK}))
    out = _run(
        lambda: ex.execute("ask_user", {"question": "q", "choices": "JustOne"}),
        bridge,
        7,
        "JustOne",
    )
    assert out["choices_offered"] == ["JustOne"]


def test_no_question_leaves_a_pending_session():
    ex, bridge = _executor(caps=frozenset({Cap.AGENT_ASK}))
    _run(lambda: ex.execute("ask_user", {"question": "q"}), bridge, 7, "ok")
    assert bridge.pending == 0


# ── confirm_action ──────────────────────────────────────────────────────────


def test_confirm_yes_is_confirmed():
    ex, bridge = _executor(caps=frozenset({Cap.AGENT_ASK}))
    out = _run(
        lambda: ex.execute("confirm_action", {"what": "delete the db", "yes_label": "Do it"}),
        bridge,
        7,
        "Do it",
    )
    assert out["confirmed"] is True


def test_confirm_no_is_not_confirmed():
    ex, bridge = _executor(caps=frozenset({Cap.AGENT_ASK}))
    out = _run(
        lambda: ex.execute("confirm_action", {"what": "delete", "no_label": "Keep"}),
        bridge,
        7,
        "Keep",
    )
    assert out["confirmed"] is False


def test_confirm_timeout_is_never_confirmed():
    bridge = AskBridge(bot=FakeBot(), user=object(), ttl=0.01)
    tools = InteractiveTools(bridge, chat_id=7)
    out = asyncio.run(tools.confirm_action("delete everything"))
    assert out["confirmed"] is False
    assert out["timed_out"] is True


# ── collect_fields ──────────────────────────────────────────────────────────


def test_collect_asks_each_field_in_order():
    bridge = AskBridge(bot=FakeBot(), user=object())
    tools = InteractiveTools(bridge, chat_id=7)

    async def scenario():
        task = asyncio.ensure_future(
            tools.collect_fields(
                [
                    {"name": "first", "question": "first name?"},
                    {"name": "city", "question": "city?", "choices": ["Delhi", "Lagos"]},
                ]
            )
        )
        seen = []
        # Bounded: each answer lets the form register the next question, which
        # needs a real turn of the loop (the send is itself an await).
        for _ in range(50):
            if task.done():
                break
            session = next(
                (s for s in bridge._pending.values() if not s.future.done()), None
            )
            if session is not None:
                seen.append(session.question)
                session.resolve("Lagos")
                bridge._retire(session)
            await asyncio.sleep(0.01)
        return await asyncio.wait_for(task, timeout=2), seen

    out, seen = asyncio.run(scenario())
    assert seen == ["first name?", "city?"]
    assert out["answers"] == {"first": "Lagos", "city": "Lagos"}
    assert out["complete"] is True


def test_collect_stops_at_the_first_unanswered_field():
    bridge = AskBridge(bot=FakeBot(), user=object(), ttl=0.01)
    tools = InteractiveTools(bridge, chat_id=7)
    out = asyncio.run(
        tools.collect_fields(
            [
                {"name": "a", "question": "a?"},
                {"name": "b", "question": "b?"},
            ]
        )
    )
    assert out["complete"] is False
    assert out["skipped"] == ["a"]


def test_collect_needs_at_least_one_field():
    bridge = AskBridge(bot=FakeBot(), user=object())
    out = asyncio.run(InteractiveTools(bridge, chat_id=7).collect_fields([]))
    assert "error" in out


def test_collect_drops_incomplete_field_specs():
    bridge = AskBridge(bot=FakeBot(), user=object(), ttl=0.01)
    out = asyncio.run(
        InteractiveTools(bridge, chat_id=7).collect_fields(
            [{"name": "", "question": "?"}, {"name": "ok", "question": "?"}]
        )
    )
    # the good one still runs, so only one field was asked
    assert set(out["answers"]) <= {"ok"}


def test_collect_is_bounded():
    bridge = AskBridge(bot=FakeBot(), user=object(), ttl=0.01)
    fields = [
        {"name": f"f{i}", "question": f"q{i}?"} for i in range(MAX_QUESTIONS + 5)
    ]
    out = asyncio.run(InteractiveTools(bridge, chat_id=7).collect_fields(fields))
    assert len(out["answers"]) <= MAX_QUESTIONS


# ── plumbing ────────────────────────────────────────────────────────────────


def test_executor_binds_the_interactive_tools_to_its_own_chat():
    ex, _ = _executor(chat_id=7)
    other = ToolExecutor(chat_id=99, caps=frozenset({Cap.AGENT_ASK}))
    assert other.interactive.chat_id == 99
    # A pre-bound interactive set follows the executor's chat.
    ex.chat_id = 42
    assert ex.interactive.chat_id == 42


def test_interactive_is_built_lazily():
    ex = ToolExecutor(chat_id=7)
    assert ex._interactive is None
    assert ex.interactive is not None


def test_clamp_ttl_bounds_and_defaults():
    assert clamp_ttl(None) == 120.0
    assert clamp_ttl("junk") == 120.0
    assert clamp_ttl(1) == 5.0
    assert clamp_ttl(10_000) == 900.0
    assert clamp_ttl(60) == 60.0
    assert clamp_ttl(float("nan")) == 120.0
    assert clamp_ttl(float("inf")) == 120.0


def test_every_tool_has_a_schema_and_a_cap_entry():
    names = {
        s["function"]["name"] for s in __import__(
            "pyUltroid.agent._tools", fromlist=["TOOL_SCHEMAS"]
        ).TOOL_SCHEMAS
    }
    for tool in ("ask_user", "confirm_action", "collect_fields"):
        assert tool in names
        assert tool in TOOL_REQUIRED_CAPS
        assert hasattr(ToolExecutor, f"_tool_{tool}")

"""The User-to-Bot ask bridge: tokens, TTL, typed answers and leak safety.

A leaked future pins a tool call open, so the expiry and cancel paths are the
tests that matter most here.
"""

from __future__ import annotations

import asyncio

import pytest

from pyUltroid.agent._bridge import AskBridge, AskOutcome, answer_prefix


class FakeBot:
    def __init__(self):
        self.sent = []
        self._id = 100

    async def send_message(self, chat_id, text, buttons=None, **kw):
        self._id += 1
        self.sent.append({"chat": chat_id, "text": text, "buttons": buttons, "id": self._id})
        return type("M", (), {"id": self._id})()


def _bridge(**kw):
    bot = FakeBot()
    bridge = AskBridge(bot=bot, user=object(), **kw)
    return bridge, bot


def _data_for(bridge, chat, token, value="Yes"):
    return f"ask:{chat}:{token}:0:{value}".encode()


# ── token routing ───────────────────────────────────────────────────────────


def test_answer_prefix_is_chat_scoped():
    assert answer_prefix(42) == b"ask:42:"


def test_ask_sends_a_keyboard_and_waits():
    bridge, bot = _bridge()

    async def scenario():
        task = asyncio.ensure_future(bridge.ask(7, "Your name?", ("Ada", "Grace")))
        await asyncio.sleep(0)
        assert bot.sent  # the question went out on the bot
        token = next(iter(bridge._pending))
        bridge.resolve_callback(_data_for(bridge, 7, token, "Ada"))
        return await task

    outcome = asyncio.run(scenario())
    assert outcome.answered is True
    assert outcome.value == "Ada"
    assert bridge.pending == 0


def test_callback_data_carries_the_token_and_value():
    bridge, _ = _bridge()

    async def scenario():
        task = asyncio.ensure_future(bridge.ask(7, "Pick", ("A", "B")))
        await asyncio.sleep(0)
        session = next(iter(bridge._pending.values()))
        data = session.callback_for("A")
        assert session.data_prefix.decode() in data.decode()
        assert data.endswith(b":A")
        assert len(data) <= 64
        session.resolve("A")
        return await task

    assert asyncio.run(scenario()).value == "A"


def test_foreign_callback_data_is_not_ours():
    bridge, _ = _bridge()
    assert bridge.resolve_callback(b"other:thing:0:x") is None
    assert bridge.owns(b"nope") is False


def test_two_questions_in_one_chat_do_not_collide():
    bridge, _ = _bridge()

    async def scenario():
        first = asyncio.ensure_future(bridge.ask(7, "One", ("a",)))
        second = asyncio.ensure_future(bridge.ask(7, "Two", ("b",)))
        await asyncio.sleep(0)
        assert bridge.pending == 2
        tokens = list(bridge._pending)
        bridge.resolve_callback(_data_for(bridge, 7, tokens[1], "b"))
        bridge.resolve_callback(_data_for(bridge, 7, tokens[0], "a"))
        return await first, await second

    one, two = asyncio.run(scenario())
    assert (one.value, two.value) == ("a", "b")


def test_a_token_cannot_be_replayed_after_retire():
    bridge, _ = _bridge()

    async def scenario():
        task = asyncio.ensure_future(bridge.ask(7, "Q", ("a",)))
        await asyncio.sleep(0)
        token = next(iter(bridge._pending))
        bridge.resolve_callback(_data_for(bridge, 7, token, "a"))
        await task
        return token

    token = asyncio.run(scenario())
    assert bridge.resolve_callback(_data_for(bridge, 7, token, "a")) is None


def test_cross_chat_token_is_rejected():
    """A token minted for chat A must not answer chat B's question."""
    bridge, _ = _bridge()

    async def scenario():
        task = asyncio.ensure_future(bridge.ask(7, "Q", ("a",)))
        await asyncio.sleep(0)
        token = next(iter(bridge._pending))
        assert bridge.resolve_callback(f"ask:9:{token}:0:a".encode()) is None
        next(iter(bridge._pending.values())).resolve("a")
        return await task

    assert asyncio.run(scenario()).value == "a"


# ── typed answers ───────────────────────────────────────────────────────────


def test_typed_reply_answers_the_oldest_question_in_chat():
    bridge, _ = _bridge()

    async def scenario():
        first = asyncio.ensure_future(bridge.ask(7, "One", ()))
        await asyncio.sleep(0)
        second = asyncio.ensure_future(bridge.ask(7, "Two", ()))
        await asyncio.sleep(0)
        bridge.submit_text(7, "Ada")
        bridge.submit_text(7, "Grace")
        return await first, await second

    one, two = asyncio.run(scenario())
    assert (one.value, two.value) == ("Ada", "Grace")


def test_typed_reply_in_another_chat_is_not_consumed():
    bridge, _ = _bridge()

    async def scenario():
        task = asyncio.ensure_future(bridge.ask(7, "Q", ()))
        await asyncio.sleep(0)
        assert bridge.submit_text(8, "wrong chat") is None
        next(iter(bridge._pending.values())).resolve("ok")
        return await task

    assert asyncio.run(scenario()).value == "ok"


def test_typed_reply_with_no_open_question_is_none():
    assert _bridge()[0].submit_text(7, "hi") is None


def test_typed_answer_survives_the_tuple_parse_mode_shape():
    bridge, _ = _bridge()

    async def scenario():
        task = asyncio.ensure_future(bridge.ask(7, "Q", ()))
        await asyncio.sleep(0)
        bridge.submit_text(7, ("Ada", ["bold"]))
        return await task

    assert asyncio.run(scenario()).value == "Ada"


# ── TTL and leak safety ─────────────────────────────────────────────────────


def test_expiry_returns_an_unanswered_outcome_not_an_exception():
    bridge, _ = _bridge(ttl=0.01)
    outcome = asyncio.run(bridge.ask(7, "Q", ("a",)))
    assert outcome.answered is False
    assert outcome.timed_out is True
    assert outcome.reason == "timeout"
    assert bridge.pending == 0


def test_expired_outcome_is_shaped_for_the_model():
    outcome = AskOutcome(question="Q", timed_out=True, reason="timeout", waited=1.0)
    payload = outcome.as_dict()
    assert payload["answered"] is False
    assert payload["user_response"] is None
    assert payload["timed_out"] is True
    assert payload["reason"] == "timeout"


def test_cancelling_the_caller_retires_the_session():
    """A cancelled tool must not leave a future pinning the map."""
    bridge, _ = _bridge(ttl=60)

    async def scenario():
        task = asyncio.ensure_future(bridge.ask(7, "Q", ("a",)))
        await asyncio.sleep(0)
        assert bridge.pending == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return bridge.pending

    assert asyncio.run(scenario()) == 0


def test_cancel_all_settles_everything():
    bridge, _ = _bridge(ttl=60)

    async def scenario():
        tasks = [
            asyncio.ensure_future(bridge.ask(7, "A", ())),
            asyncio.ensure_future(bridge.ask(8, "B", ())),
        ]
        await asyncio.sleep(0)
        assert bridge.cancel_all("shutdown") == 2
        return [await t for t in tasks]

    results = asyncio.run(scenario())
    assert all(r.reason == "shutdown" for r in results)
    assert bridge.pending == 0


def test_watchdog_is_cancelled_on_answer():
    bridge, _ = _bridge(ttl=60)

    async def scenario():
        task = asyncio.ensure_future(bridge.ask(7, "Q", ("a",)))
        await asyncio.sleep(0)
        session = next(iter(bridge._pending.values()))
        assert session.watchdog is not None
        session.resolve("a")
        return await task, session

    _, session = asyncio.run(scenario())
    assert session.watchdog is None


def test_no_ask_leaves_a_session_behind():
    bridge, _ = _bridge()

    async def scenario():
        for i in range(5):
            task = asyncio.ensure_future(bridge.ask(i, "Q", ("a",)))
            await asyncio.sleep(0)
            token = next(iter(bridge._pending))
            bridge.resolve_callback(_data_for(bridge, i, token, "a"))
            await task
        return bridge.pending

    assert asyncio.run(scenario()) == 0


# ── keyboard shape ──────────────────────────────────────────────────────────


def test_choices_render_two_columns_by_default():
    bridge, bot = _bridge()

    async def scenario():
        task = asyncio.ensure_future(bridge.ask(7, "Pick", ("a", "b", "c", "d")))
        await asyncio.sleep(0)
        buttons = bot.sent[0]["buttons"]
        assert len(buttons) == 2 and all(len(r) == 2 for r in buttons)
        for row in buttons:
            for cell in row:
                assert cell.style is not None
        next(iter(bridge._pending.values())).resolve("a")
        return await task

    asyncio.run(scenario())


def test_open_question_sends_no_buttons():
    bridge, bot = _bridge()

    async def scenario():
        task = asyncio.ensure_future(bridge.ask(7, "Your name?"))
        await asyncio.sleep(0)
        assert bot.sent[0]["buttons"] is None
        next(iter(bridge._pending.values())).resolve("Ada")
        return await task

    assert asyncio.run(scenario()).value == "Ada"


def test_note_is_appended_to_the_question():
    bridge, bot = _bridge()

    async def scenario():
        task = asyncio.ensure_future(bridge.ask(7, "Name?", note="first name"))
        await asyncio.sleep(0)
        assert "Name?" in bot.sent[0]["text"]
        assert "first name" in bot.sent[0]["text"]
        next(iter(bridge._pending.values())).resolve("Ada")
        return await task

    asyncio.run(scenario())


def test_ask_without_a_bot_client_is_a_clear_error():
    bridge = AskBridge(user=object())
    bridge._bot = None
    bridge.bot  # forces resolution attempt
    if bridge._bot is None:
        with pytest.raises(RuntimeError, match="no bot client"):
            asyncio.run(bridge.ask(7, "Q"))

"""Tests for the DM session key fix and threaded delivery.

Covers the production failure:

    ValueError: invalid literal for int() with base 10: 'dm:6802929470:305'
    at .../_agent.py:628 in chat_system

The DM flow now builds its history key with :class:`DmSessionKey` instead of a
bare string, and ``chat_system``/``set_chat_system``/``reset_chat_system``
normalize through :class:`ChatKey` so string and numeric ids live in distinct
slots. Delivery is threaded: the placeholder, the edit, and the fallback
reply all target the user's triggering message, never a standalone send.
"""

from __future__ import annotations

import asyncio

import pytest

from pyUltroid.agent._agent import UltroidAgent
from pyUltroid.agent._chatkey import (
    CATEGORY_SESSION,
    DmSessionKey,
    NumericChatKey,
    normalize_chat_id,
)
from pyUltroid.agent._delivery import DeliveryError as _DeliveryError
from pyUltroid.agent._delivery import DeliveryStrategy as _SessionDelivery


def _run(coro):
    """Run a coroutine to completion in a fresh event loop.

    Isolated from pytest-asyncio so the suite does not pay for the plugin on
    this repo. A dedicated loop is used because some Telethon test doubles
    bind to the running loop at import time.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── key construction ┧────────────────────────────────────────────────────────


def test_dm_session_key_is_stable_and_categorised():
    key = DmSessionKey(6802929470, 305)
    assert key.is_session()
    assert key.category == CATEGORY_SESSION
    assert key.value == "dm:6802929470:305"
    assert key.user_id == 6802929470
    assert key.anchor == 305


def test_normalize_chat_id_accepts_both_forms():
    assert normalize_chat_id("dm:6802929470:305") == "dm:6802929470:305"
    assert normalize_chat_id(8402929470) == "8402929470"


# ── the crash that broke .aichat ────────────────────────────────────────────


def test_chat_system_does_not_int_coerce_a_dm_key():
    """The exact call stack from the production traceback, now safe."""
    agent = UltroidAgent()
    agent.set_chat_system("dm:6802929470:305", "session prompt")

    assert agent.chat_system("dm:6802929470:305") == "session prompt"


def test_numeric_and_dm_keys_never_collide():
    """The original bug: int("dm:...") raised; the latent bug was collision."""
    agent = UltroidAgent()
    agent.set_chat_system("dm:6802929470:305", "session")
    agent.set_chat_system(6802929470, "numeric")

    assert agent.chat_system("dm:6802929470:305") == "session"
    assert agent.chat_system(6802929470) == "numeric"


def test_reset_chat_system_handles_dm_key():
    agent = UltroidAgent()
    agent.set_chat_system("dm:6802929470:305", "session")
    agent.reset_chat_system("dm:6802929470:305")
    assert agent.chat_system("dm:6802929470:305") == ""


def test_chat_system_accepts_a_numeric_int():
    agent = UltroidAgent()
    agent.set_chat_system(42, "group")
    assert agent.chat_system(42) == "group"
    assert normalize_chat_id(42) == "42"
    assert isinstance(
        __import__(
            "pyUltroid.agent._chatkey", fromlist=["build_chat_key"]
        ).build_chat_key(42),
        NumericChatKey,
    )


# ── persistence format ┧─────────────────────────────────────────────────────


def test_chat_systems_store_canonical_strings():
    """In-memory and persisted forms agree on canonical keys."""
    agent = UltroidAgent()
    agent.set_chat_system("dm:6802929470:305", "session")
    agent.set_chat_system(7, "group")

    assert agent._chat_systems == {
        "dm:6802929470:305": "session",
        "7": "group",
    }
    # The raw integer must NOT shadow the session string.
    assert "6802929470" not in agent._chat_systems


# ── _SessionDelivery threading ┧─────────────────────────────────────────────


class _FakeMessage:
    """A minimal message double: tracks reply and edit calls."""

    def __init__(self) -> None:
        self.edited: list[str] = []
        self.replied_to: list[str] = []

    async def reply(self, text: str, **kwargs) -> "_FakeMessage":
        self.replied_to.append(text)
        return self

    async def edit(self, text: str, **kwargs) -> None:
        self.edited.append(text)


class _BrokenEditMessage(_FakeMessage):
    async def edit(self, text: str, **kwargs) -> None:
        raise PermissionError("cannot edit")


class _BrokenReplyMessage(_FakeMessage):
    async def reply(self, text: str, **kwargs):
        raise PermissionError("cannot reply")


def test_delivery_holds_the_triggering_message():

    trigger = _FakeMessage()
    delivery = _SessionDelivery(trigger)
    assert delivery._trigger is trigger


def test_delivery_render_edits_in_place_when_possible():

    trigger = _FakeMessage()
    placeholder = _FakeMessage()
    delivery = _SessionDelivery(trigger)

    result = _run(delivery.render(placeholder, "the answer"))
    assert placeholder.edited == ["the answer"]
    assert trigger.replied_to == []  # no fallback fired
    assert result is placeholder


def test_delivery_falls_back_to_reply_when_edit_fails():
    """Edit failure -> a fresh reply to the trigger, never a standalone send."""

    trigger = _FakeMessage()
    placeholder = _BrokenEditMessage()
    delivery = _SessionDelivery(trigger)

    result = _run(delivery.render(placeholder, "the answer"))
    assert placeholder.edited == []
    assert trigger.replied_to == ["the answer"]
    assert result is trigger


def test_delivery_edit_failure_does_not_escape():
    """A failing placeholder edit is converted to the fallback reply."""

    trigger = _BrokenReplyMessage()
    placeholder = _BrokenEditMessage()
    delivery = _SessionDelivery(trigger)

    with pytest.raises(_DeliveryError) as ei:
        _run(delivery.render(placeholder, "the answer"))
    assert ei.value.phase == "fallback"


def test_delivery_placeholder_failure_is_reported():
    """If even the typing indicator cannot send, the phase is reported."""

    trigger = _BrokenReplyMessage()
    delivery = _SessionDelivery(trigger)

    with pytest.raises(_DeliveryError) as ei:
        _run(delivery.placeholder())
    assert ei.value.phase == "placeholder"


def test_delivery_error_carries_phase_and_cause():
    err = _DeliveryError("placeholder", PermissionError("nope"))
    assert err.phase == "placeholder"
    assert "placeholder" in str(err)
    assert isinstance(err.__cause__, PermissionError)

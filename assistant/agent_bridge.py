"""Routes the user's answer back to whatever the agent is waiting on.

This is the half of the ask bridge that was missing: the tool half lives in
:mod:pyUltroid.agent._interactive and puts a styled keyboard on the Bot, but
a tap on that keyboard and a typed reply both have to arrive back here to
settle the future the tool is blocked on.

Two handlers, both deliberately unfiltered:

* every CallbackQuery is offered to the bridge, which claims only the
  data it minted and ignores the rest - the assistant's own menu buttons share
  the same dispatcher, so this must be a filter and not a pattern;
* every private inbound text message is offered as a typed answer to the
  oldest open question in that chat, and is otherwise left completely alone,
  so the PM bot and the session threads keep owning their own flows.

The client note matters: Telegram does not deliver a callback query for a
message the *User* account sent itself. That is why the question goes out on
the Bot in the first place, and why this file binds bot=asst.
"""

from __future__ import annotations

from typing import Any

from telethon import events

from pyUltroid import asst
from pyUltroid.startup.logger import LOGS
from pyUltroid.agent._bridge import AskBridge, shared_bridge
from pyUltroid.agent._telegram import text_of

__res_tags__ = ["ai", "agent", "bridge"]
_REGISTERED = False


def _bridge() -> AskBridge:
    """The process-wide bridge, bound to the bot that paints the keyboards."""
    bridge = shared_bridge()
    bridge.attach(bot=asst)
    return bridge


async def on_ask_callback(event: Any) -> None:
    """Settle the question whose button was tapped."""
    data = getattr(event, "data", None)
    if not data:
        return
    bridge = _bridge()
    if not bridge.owns(data):
        return
    try:
        outcome = bridge.resolve_callback(data)
    except Exception as exc:  # noqa: BLE001 - one bad tap must not kill the bot
        LOGS.exception("[agent/bridge] callback resolve failed: %s", exc)
        return
    if outcome is None:
        return
    # The keyboard has served its purpose; take it off the message so a second
    # tap cannot re-answer a question that is already settled.
    try:
        await event.delete()
    except Exception:  # noqa: BLE001 - deleting is cosmetic
        pass
    LOGS.info(
        "[agent/bridge] answered %r via button after %.1fs",
        outcome.question[:60],
        outcome.waited,
    )
    raise events.StopPropagation


async def on_ask_reply(event: Any) -> None:
    """Settle the oldest open question in this chat with typed text."""
    bridge = _bridge()
    if bridge.pending == 0:
        return
    text = text_of(getattr(event, "message", event), "").strip()
    if not text:
        return
    try:
        outcome = bridge.submit_text(event.chat_id, text)
    except Exception as exc:  # noqa: BLE001
        LOGS.exception("[agent/bridge] text resolve failed: %s", exc)
        return
    if outcome is not None:
        LOGS.info(
            "[agent/bridge] answered %r by text after %.1fs",
            outcome.question[:60],
            outcome.waited,
        )
        raise events.StopPropagation


def register() -> None:
    """Bind the clients and attach both handlers to the bot."""
    global _REGISTERED
    if _REGISTERED or asst is None:
        return
    _bridge()
    asst.add_event_handler(on_ask_callback, events.CallbackQuery())
    asst.add_event_handler(
        on_ask_reply,
        events.NewMessage(incoming=True, func=lambda e: bool(e.is_private)),
    )
    _REGISTERED = True
    LOGS.info("[agent/bridge] ask bridge wired to the assistant bot")


register()

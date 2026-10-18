"""Agent sessions in the assistant bot's DM, one reply thread per session.

A session-start card anchors a reply thread. Replies to that anchor continue
only that session; ordinary private messages and commands remain available to
the PM bot and other assistant handlers.

Session metadata is stored under ``AGENT_DM_SESSIONS_<user_id>`` and each
conversation uses a typed ``dm:<user_id>:<anchor>`` key, keeping sessions
separate across users and restarts.
"""

from __future__ import annotations

import re
import time
from typing import Any

from telethon import events

from pyUltroid import asst
from pyUltroid._core.richmsg import (
    RichBlocks,
    client_can_send_rich_message,
    send_rich,
)
from pyUltroid._misc._assistant import asst_cmd
from pyUltroid.agent import AgentRequest, agent
from pyUltroid.agent import controls as ai_controls
from pyUltroid.agent._chatkey import DmSessionKey
from pyUltroid.agent._delivery import DeliveryError as _DeliveryError
from pyUltroid.agent._delivery import DeliveryStrategy as _SessionDelivery
from pyUltroid.agent._reporting import report_run
from pyUltroid.agent._sessions import SessionStoreError
from pyUltroid.startup.logger import LOGS as _LOGS

__res_tags__ = ["ai", "agent", "sessions"]

# Only strip a command at the beginning of the message. Never remove text that
# merely happens to contain "/ai" later in the user's prompt.
_AI_PREFIX = re.compile(r"^/ai(?:@\w+)?(?:\s+|$)", re.IGNORECASE)


async def send_session_card(user_id: Any, title: str) -> Any:
    """Send the session-start card; its message is the session anchor."""
    blocks = (
        RichBlocks()
        .header("AI session", level=3)
        .header(str(title), level=4)
        .paragraph(
            "Reply to this message to continue this session. "
            "Each session has its own conversation history."
        )
    )
    return await send_rich(asst, user_id, blocks)


async def _send_session_list(event: Any, sessions: list[dict]) -> Any:
    """Render a readable, consistently indented session index."""
    items = []
    for session in sessions:
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(session.get("created", 0)))
        title = str(session.get("title") or "Untitled session")
        anchor = session.get("anchor")
        items.append(f"{title} — {when} (reply to message {anchor})")

    blocks = RichBlocks().header("Your AI sessions", level=2).list(items)
    return await send_rich(
        event.client,
        event.chat_id,
        blocks,
        reply_to=event,
        link_preview=False,
    )


@asst_cmd("ai")
async def dm_ai_cmd(event):
    """Start an AI session or continue the session card being replied to."""
    if not event.is_private:
        return
    user_id = event.sender_id
    prompt = _AI_PREFIX.sub("", (event.raw_text or "").strip(), count=1).strip()

    reply = await event.get_reply_message()
    session = _session_for_reply(user_id, reply) if reply is not None else None

    if session is None:
        title = (prompt or "New session")[:60]
        card = await send_session_card(user_id, title)
        try:
            agent().session_create(user_id, card.id, title)
        except SessionStoreError:
            return await event.reply(
                "This session could not be saved, so it cannot be continued later. "
                "Please try again."
            )
        anchor = card.id
    else:
        anchor = session["anchor"]

    if not prompt:
        return await event.reply(
            "Reply to the session card with a prompt, or send /ai <prompt> "
            "to start a new session."
        )

    await _run_session(event, user_id, anchor, prompt)


@asst_cmd("sessions")
async def dm_sessions_cmd(event):
    """List this user's AI sessions in the assistant bot's private chat."""
    if not event.is_private:
        return
    sessions = agent().session_list(event.sender_id)
    if not sessions:
        return await event.reply("No sessions yet. Start one with /ai <prompt>.")
    return await _send_session_list(event, sessions)


@asst.on(
    events.NewMessage(
        incoming=True,
        func=lambda event: (
            event.is_private
            and event.is_reply
            and bool((event.raw_text or "").strip())
            and not (event.raw_text or "").lstrip().startswith("/")
        ),
    )
)
async def dm_thread_handler(event):
    """Continue only the session associated with the replied-to anchor."""
    user_id = event.sender_id
    session = agent().session_find(user_id, event.reply_to_msg_id)
    if session is None:
        session = _session_for_reply(user_id, await event.get_reply_message())
    if session is None:
        return
    anchor = session["anchor"]
    try:
        await _run_session(event, user_id, anchor, (event.raw_text or "").strip())
    except Exception:  # noqa: BLE001 - outer Telegram handler boundary
        _LOGS.error("[agent/dm] session reply failed")
        try:
            await event.reply(
                "This AI session could not process that reply. "
                "Try again or start a new session."
            )
        except Exception:  # noqa: BLE001 - failure notice is best-effort
            _LOGS.error("[agent/dm] session error notice could not be sent")
    raise events.StopPropagation


async def _run_session(event: Any, user_id: Any, anchor: int, prompt: str) -> None:
    """Run one agent turn and deliver it in the session's reply thread."""
    key = DmSessionKey(user_id, anchor).value
    controls = ai_controls.get_controls(f"dm:{user_id}")
    started = time.time()
    delivery = _SessionDelivery(event, anchor=anchor)
    rich_allowed = await client_can_send_rich_message(event.client)
    try:
        status = await delivery.placeholder()
    except _DeliveryError as exc:
        return await event.reply(
            f"Could not open session {anchor}: delivery failed at {exc.phase}. "
            "Start a new session with /ai <prompt>."
        )

    try:
        response = await agent().runtime_run(
            AgentRequest(
                prompt=prompt,
                chat_id=key,
                origin="dm-session",
                user_id=user_id,
                is_private=True,
                use_tools=ai_controls.dm_tools_enabled(user_id, controls),
                max_rounds=controls["rounds"],
                model=controls["model"],
                temperature=controls["temp"],
                use_cache=True,
                rich_messages_allowed=rich_allowed,
                timeout_seconds=105.0,
            )
        )
    except Exception:  # noqa: BLE001 - report unexpected runtime/provider failures
        _LOGS.error("[agent/dm] runtime failed")
        try:
            await delivery.render(
                status,
                "This session could not complete the request. Please try again.",
            )
        except _DeliveryError:
            _LOGS.error("[agent/dm] runtime failure notice could not be delivered")
        return

    report_run(
        origin="dm-session",
        chat_id=event.chat_id,
        user=getattr(getattr(event, "sender", None), "username", None),
        provider=response.provider,
        model=response.model,
        prompt=prompt,
        tools_used=list(getattr(response, "tools_used", None) or []),
        duration=time.time() - started,
        ok=response.ok,
        error=getattr(response, "error", None),
    )
    try:
        await delivery.render(status, response)
    except _DeliveryError as exc:
        await event.reply(
            f"Could not deliver response {anchor}: delivery failed at {exc.phase}."
        )


def _session_for_reply(user_id: Any, reply: Any) -> dict | None:
    """Resolve replies to either a session anchor or a result in its thread."""
    store = agent()
    message_id = getattr(reply, "id", None)
    session = store.session_find(user_id, message_id)
    if session is not None:
        return session
    header = getattr(reply, "reply_to", None)
    # Telethon retains thread root metadata on replies even when the directly
    # replied-to result has scrolled out of cache.
    for candidate in (
        getattr(reply, "reply_to_top_id", None),
        getattr(header, "reply_to_top_id", None),
        getattr(reply, "reply_to_msg_id", None),
        getattr(header, "reply_to_msg_id", None),
    ):
        if candidate:
            session = store.session_find(user_id, candidate)
            if session is not None:
                return session
    return None

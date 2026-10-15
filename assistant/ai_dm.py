"""Agent sessions in the assistant bot's DM, one reply thread per session.

Telegram bots have no forum topics in private chats at the layer this fork
speaks, so a "session" is a reply thread: the session-start card anchors it and
every message that replies into the thread continues it. Replying to a
different session's anchor switches threads without losing either history,
which is the same affordance topics give a forum.

Only replies into a session thread trigger the agent. Plain DM text is left to
the surfaces that already own it — the PM bot forwards it, and ``/ai`` starts
something new — so a session can never hijack another DM flow.

Session state lives in the database under ``AGENT_DM_SESSIONS_<user_id>``, one
entry per session, so sessions survive restarts. Each session's conversation
history is the standard agent history, keyed by ``dm:<user_id>:<anchor>``
instead of a chat id, so the two never collide.
"""

from __future__ import annotations

import re
import time
from typing import Any

from telethon import events

from pyUltroid import asst
from pyUltroid._misc._assistant import asst_cmd
from pyUltroid._core.richmsg import RichBlocks, send_rich
from pyUltroid.startup.logger import LOGS as _LOGS
from pyUltroid.agent import agent, controls as ai_controls
from pyUltroid.agent import AgentRequest, resolve_caps
from pyUltroid.agent._sessions import SessionStoreError
from pyUltroid.agent._chatkey import DmSessionKey
from pyUltroid.agent._delivery import DeliveryError as _DeliveryError
from pyUltroid.agent._delivery import DeliveryStrategy as _SessionDelivery
from pyUltroid.agent._reporting import report_run

__res_tags__ = ["ai", "agent", "sessions"]

#: ``/ai[@botname] <prompt>``
_AI_PREFIX = re.compile(r"^/ai(?:@\w+)?\s*", re.IGNORECASE)


# ── session card ──────────────────────────────────────────────────────────


async def send_session_card(user_id: Any, title: str) -> Any:
    """Send the session-start card; its message is the session anchor."""
    blocks = (
        RichBlocks(is_bot=True)
        .header("AI session", level=3)
        .paragraph(f"**{title}**")
        .paragraph(
            "Reply to this message to keep the conversation in this thread. "
            "Each reply thread is a separate session with its own history."
        )
    )
    try:
        message = await send_rich(asst, user_id, blocks)
    except Exception:
        message = None
    if message is None:
        message = await asst.send_message(
            user_id,
            f"**AI session — {title}**\n\nReply to this message to continue in this thread.",
        )
    return message


# ── commands ──────────────────────────────────────────────────────────────


@asst_cmd("ai")
async def dm_ai_cmd(event):
    """
    /ai <prompt> — start or continue an agent session in the bot's DM.

    Replying to a session card with ``/ai`` continues that session. Without a
    reply, a new session starts and its card anchors the thread.
    """
    if not event.is_private:
        return
    user_id = event.sender_id
    prompt = _AI_PREFIX.sub("", (event.raw_text or "").strip()).strip()

    reply = await event.get_reply_message()
    session = None
    if reply is not None:
        session = agent().session_find(user_id, reply.id)

    if session is None:
        title = (prompt or "New session")[:60]
        card = await send_session_card(user_id, title)
        try:
            agent().session_create(user_id, card.id, title)
        except SessionStoreError:
            return await event.reply(
                "⚠️ Could not save this session, so it cannot be continued later. "
                "Try again in a moment."
            )
        anchor = card.id
    else:
        anchor = session["anchor"]

    if not prompt:
        return await event.reply(
            "Reply to the session card with your prompt text to continue it, "
            "or send `/ai <prompt>` to start a new session."
        )

    await _run_session(event, user_id, anchor, prompt)


@asst_cmd("sessions")
async def dm_sessions_cmd(event):
    """
    /sessions — list this user's agent sessions in the bot's DM.
    """
    if not event.is_private:
        return
    sessions = agent().session_list(event.sender_id)
    if not sessions:
        return await event.reply("No sessions yet. Start one with `/ai <prompt>`.")
    lines = []
    for session in sessions:
        when = time.strftime("%Y-%m-%d %H:%M", time.gmtime(session.get("created", 0)))
        lines.append(
            f"• **{session['title']}** — {when} (reply to message `{session['anchor']}`)"
        )
    await event.reply("**Your AI sessions**\n\n" + "\n".join(lines))


# ── reply-thread continuation ─────────────────────────────────────────────


@asst.on(
    events.NewMessage(
        incoming=True,
        func=lambda e: e.is_private
        and e.is_reply
        and bool((e.raw_text or "").strip())
        and not (e.raw_text or "").lstrip().startswith("/"),
    )
)
async def dm_thread_handler(event):
    """Continue the session whose thread a DM reply lands in.

    Anything else — a reply to some other message, plain text, a command —
    passes through untouched, so the PM bot and every other DM flow keep
    working exactly as before.
    """
    user_id = event.sender_id
    anchor = event.reply_to_msg_id
    if agent().session_find(user_id, anchor) is None:
        return
    await _run_session(event, user_id, anchor, (event.raw_text or "").strip())


# ── the session loop ──────────────────────────────────────────────────────


async def _run_session(event: Any, user_id: Any, anchor: int, prompt: str) -> None:
    """Drive one DM session turn to completion.

    The session is anchored to *anchor* (the message the user replied to) and
    its history lives under the typed key ``dm:<user>:<anchor>``. The key is
    built with :class:`DmSessionKey` so it never reaches the agent as a bare
    string that the old ``int()`` coercion could mis-normalise.
    """
    key = DmSessionKey(user_id, anchor).value
    controls = ai_controls.get_controls(f"dm:{user_id}")
    # A DM session is a normal runtime request; the runtime applies the
    # owner/sudo capability boundary instead of this handler re-implementing it.
    t0 = time.time()
    delivery = _SessionDelivery(event)
    try:
        status = await delivery.placeholder()
    except _DeliveryError as exc:
        # Cannot even send a placeholder - the thread or peer is broken.
        # Report rather than crashing the handler, mirroring how the agent
        # surfaces run errors to chat.
        return await event.reply(
            f"⚠️ Could not open session {anchor}: delivery failed at "
            f"{exc.phase}. Open a new session with `/ai <prompt>`."
        )

    response = await agent().runtime_run(
        AgentRequest(
            prompt=prompt,
            chat_id=key,
            origin="dm-session",
            user_id=user_id,
            is_private=True,
            use_tools=ai_controls.dm_tools_enabled(user_id, controls),
            use_cache=True,
        )
    )
    report_run(
        origin="dm-session",
        chat_id=event.chat_id,
        user=getattr(getattr(event, "sender", None), "username", None),
        provider=response.provider,
        model=response.model,
        prompt=prompt,
        tools_used=list(getattr(response, "tools_used", None) or []),
        duration=time.time() - t0,
        ok=response.ok,
        error=getattr(response, "error", None),
    )
    text = response.content or response.error or "…"
    if len(text) > 4000:
        text = text[:4000] + "…"
    try:
        await delivery.render(status, text)
    except _DeliveryError as exc:
        await event.reply(
            f"⚠️ Could not deliver response {anchor}: "
            f"delivery failed at {exc.phase}."
        )

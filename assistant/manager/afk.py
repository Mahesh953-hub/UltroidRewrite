# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from datetime import datetime as dt

from telethon.errors import RPCError
from telethon.events import NewMessage
from telethon.tl.types import (
    Message,
    MessageEntityMention,
    MessageEntityMentionName,
    User,
)
from telethon.utils import get_display_name

from . import HNDLR, LOGS, asst, asst_cmd
from pyUltroid._core.richmsg import RichBlocks, send_rich
from pyUltroid.fns import fns

AFK = {}


def _afk_duration(started):
    elapsed = max(0, int((dt.now() - started).total_seconds() * 1000))
    return fns.time_formatter(elapsed)


def _is_afk_command(raw_text):
    parts = str(raw_text or "").strip().split(maxsplit=1)
    if not parts:
        return False
    command = parts[0]
    for prefix in {"/", str(HNDLR or "")}:
        if prefix and command.startswith(prefix):
            name = command[len(prefix) :].split("@", 1)[0]
            if name.casefold() == "afk":
                return True
    return False


async def _send_status(event, title, *details):
    blocks = RichBlocks().header(str(title), level=3)
    for detail in details:
        if detail:
            blocks.paragraph(str(detail))
    return await send_rich(
        event.client,
        event.chat_id,
        blocks,
        reply_to=event,
        link_preview=False,
    )


async def _forward_reason_if_needed(event, reason):
    if not isinstance(reason, Message):
        return
    try:
        await event.client.forward_messages(
            event.chat_id,
            reason,
            reply_to=event,
        )
    except RPCError as exc:
        LOGS.warning(
            "assistant AFK reason media could not be forwarded (%s)",
            exc.__class__.__name__,
        )


@asst_cmd(pattern="afk", func=lambda event: not event.is_private)
async def go_afk(event):
    sender = await event.get_sender()
    if not isinstance(sender, User) or sender.bot:
        return

    parts = (event.raw_text or event.text or "").split(maxsplit=1)
    reason = parts[1].strip() if len(parts) > 1 else None
    if event.is_reply and not reason:
        replied = await event.get_reply_message()
        if replied is not None:
            if replied.text and not replied.media:
                reason = replied.text
            elif replied.media:
                reason = replied

    AFK.setdefault(event.chat_id, {})[event.sender_id] = {
        "reason": reason,
        "time": dt.now(),
    }
    if isinstance(reason, str) and reason:
        await _send_status(
            event,
            f"{get_display_name(sender)} went AFK",
            f"Reason: {reason}",
        )
    else:
        await _forward_reason_if_needed(event, reason)
        await _send_status(event, f"{get_display_name(sender)} went AFK")


@asst.on(NewMessage(func=lambda event: AFK.get(event.chat_id) and not event.is_private))
async def make_change(event):
    if _is_afk_command(event.raw_text):
        return
    sender = await event.get_sender()
    if not isinstance(sender, User) or sender.bot:
        return

    chat_afk = AFK.get(event.chat_id, {})
    if event.sender_id in chat_afk:
        entry = chat_afk.pop(event.sender_id)
        await _send_status(
            event,
            f"{get_display_name(sender)} is no longer AFK",
            f"Away for {_afk_duration(entry['time'])}",
        )
        if not chat_afk:
            AFK.pop(event.chat_id, None)
            return

    notified = set()
    replied = await event.get_reply_message()
    if replied is not None and replied.sender_id in chat_afk:
        user_id = replied.sender_id
        notified.add(user_id)
        entry = chat_afk[user_id]
        display = get_display_name(replied.sender) if replied.sender else str(user_id)
        details = [f"Away for {_afk_duration(entry['time'])}"]
        reason = entry.get("reason")
        if isinstance(reason, str) and reason:
            details.append(f"Reason: {reason}")
        else:
            await _forward_reason_if_needed(event, reason)
        await _send_status(event, f"{display} is currently AFK", *details)

    for entity, text in event.get_entities_text():
        if isinstance(entity, MessageEntityMentionName):
            user_id = entity.user_id
        elif isinstance(entity, MessageEntityMention):
            user_id = text
        else:
            continue
        if not user_id:
            continue
        try:
            mentioned = await event.client.get_entity(user_id)
        except (RPCError, ValueError) as exc:
            LOGS.debug(
                "assistant AFK mention lookup failed (%s)", exc.__class__.__name__
            )
            continue
        if mentioned.id not in chat_afk or mentioned.id in notified:
            continue
        notified.add(mentioned.id)
        entry = chat_afk[mentioned.id]
        details = [f"Away for {_afk_duration(entry['time'])}"]
        reason = entry.get("reason")
        if isinstance(reason, str) and reason:
            details.append(f"Reason: {reason}")
        else:
            await _forward_reason_if_needed(event, reason)
        await _send_status(
            event,
            f"{get_display_name(mentioned)} is currently AFK",
            *details,
        )

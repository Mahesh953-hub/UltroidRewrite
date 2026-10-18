# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

# https://github.com/xditya/TeleBot/blob/master/telebot/plugins/mybot/pmbot/incoming.py

# --------------------------------------- Imports -------------------------------------------- #

import ast
import html
import os

from telethon.errors.rpcerrorlist import MessageIdInvalidError, UserNotParticipantError
from telethon.tl.custom import Button
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import Channel, Chat
from telethon.utils import get_display_name

from pyUltroid.dB.base import KeyManager
from pyUltroid.dB import db
from custom.tg import ikm
from custom.fmt import fmtmsg, rawhtml
from . import *
from pyUltroid.fns import fns

botb = KeyManager("BOTBLS", cast=list)
CACHE = {}


def _force_sub_chats(value):
    """Read both native and legacy stringified PMBOT_FSUB values safely."""
    if value is None or value is False or value == "":
        return ()
    if isinstance(value, str):
        try:
            value = ast.literal_eval(value)
        except (SyntaxError, ValueError):
            value = [part.strip() for part in value.split(",") if part.strip()]
    if value is None or value is False:
        return ()
    if isinstance(value, (str, int)):
        value = (value,)
    if not isinstance(value, (list, tuple, set)):
        return ()
    return tuple(value)


# --------------------------------------- Incoming -------------------------------------------- #


@asst_cmd(
    load=AST_PLUGINS,
    incoming=True,
    func=lambda e: e.is_private and not botb.contains(e.sender_id),
)
async def on_new_mssg(event):
    who = event.sender_id
    message_text = (
        getattr(event, "raw_text", None) or getattr(event, "text", None) or ""
    )
    # doesn't reply to that user anymore
    if message_text.startswith("/") or who == OWNER_ID:
        return
    force_sub_chats = _force_sub_chats(udB.get_key("PMBOT_FSUB"))
    if force_sub_chats:
        MSG = ""
        BTTS = []
        for chat in force_sub_chats:
            try:
                await event.client.get_permissions(chat, event.sender_id)
            except UserNotParticipantError:
                if not MSG:
                    MSG += get_string("pmbot_1")
                try:
                    uri = ""
                    TAHC_ = await event.client.get_entity(chat)
                    if hasattr(TAHC_, "username") and TAHC_.username:
                        uri = f"https://t.me/{TAHC_.username}"
                    elif CACHE.get(chat):
                        uri = CACHE[chat]
                    else:
                        if isinstance(TAHC_, Channel):
                            FUGB = await event.client(GetFullChannelRequest(chat))
                        elif isinstance(TAHC_, Chat):
                            FUGB = await event.client(GetFullChatRequest(chat))
                        else:
                            return
                        if FUGB.full_chat.exported_invite:
                            CACHE[chat] = FUGB.full_chat.exported_invite.link
                            uri = CACHE[chat]
                    BTTS.append((get_display_name(TAHC_), "url", uri))
                except Exception as er:
                    LOGS.exception(f"Error On PmBot Force Sub!\n - {chat} \n{er}")
        if MSG:
            return await event.reply(
                fmtmsg("Subscription Required ", msgs=MSG),
                buttons=ikm(BTTS) if BTTS else None,
                parse_mode="html",
            )
    try:
        xx = await event.forward_to(OWNER_ID)
    except MessageIdInvalidError:
        if not getattr(event, "message", None) and not getattr(event, "media", None):
            return
        try:
            xx = await event.client.send_message(
                OWNER_ID, event.message or "", file=event.media
            )
        except Exception:
            LOGS.exception("PmBot fallback copy failed")
            return
    if event.fwd_from:
        await xx.reply(f"From {fns.inline_mention(event.sender)} [{event.sender_id}]")
    db.botchat.add(xx.id, who)


# --------------------------------------- Outgoing -------------------------------------------- #


@asst_cmd(
    load=AST_PLUGINS,
    from_users=[OWNER_ID],
    incoming=True,
    func=lambda e: e.is_private and e.is_reply,
)
async def on_out_mssg(event):
    x = event.reply_to_msg_id
    to_user = db.botchat.get_who(x)
    message_text = (
        getattr(event, "raw_text", None) or getattr(event, "text", None) or ""
    )
    if message_text.startswith("/who"):
        try:
            if not to_user:
                return await event.reply(
                    "Could not identify the user for this message."
                )
            k = await asst.get_entity(to_user)
            photu = await event.client.download_profile_photo(k.id)
            text_out = fmtmsg(
                "User Information ",
                {
                    "Name": get_display_name(k),
                    "ID": str(k.id),
                    "Profile Link": fns.inline_mention(k, html=True),
                },
            )
            await event.reply(
                text_out,
                file=photu,
                parse_mode="html",
            )
            if photu:
                os.remove(photu)
            return
        except Exception as er:
            return await event.reply(
                fmtmsg("Error ", msgs=str(er)),
                parse_mode="html",
            )
    elif message_text.startswith("/"):
        return
    if to_user:
        await asst.send_message(to_user, event.message)


# --------------------------------------- Ban/Unban -------------------------------------------- #


@asst_cmd(
    pattern="ban",
    load=AST_PLUGINS,
    from_users=[OWNER_ID],
    func=lambda x: x.is_private,
)
async def banhammer(event):
    if not event.is_reply:
        return await event.reply(
            fmtmsg("Ban target not found", msgs=get_string("pmbot_2")),
            parse_mode="html",
        )
    target = db.botchat.get_who(event.reply_to_msg_id)
    if not target:
        return await event.reply("Could not identify the user for this message.")
    if botb.contains(target):
        return await event.reply(
            fmtmsg("Already Banned", msgs=get_string("pmbot_3")),
            parse_mode="html",
        )

    botb.add(target)
    await event.reply(
        fmtmsg(
            "User Banned",
            msgs=rawhtml(f"User ID: <code>{html.escape(str(target))}</code>"),
        ),
        parse_mode="html",
    )
    await asst.send_message(target, get_string("pmbot_4"))


@asst_cmd(
    pattern="unban",
    load=AST_PLUGINS,
    from_users=[OWNER_ID],
    func=lambda x: x.is_private,
)
async def unbanhammer(event):
    if not event.is_reply:
        return await event.reply(
            fmtmsg("Unban target not found", msgs=get_string("pmbot_5")),
            parse_mode="html",
        )
    target = db.botchat.get_who(event.reply_to_msg_id)
    if not target:
        return await event.reply("Could not identify the user for this message.")
    if not botb.contains(target):
        return await event.reply(
            fmtmsg("Not Banned", msgs=get_string("pmbot_6")),
            parse_mode="html",
        )

    botb.remove(target)
    await event.reply(
        fmtmsg(
            "User Unbanned",
            msgs=rawhtml(f"User ID: <code>{html.escape(str(target))}</code>"),
        ),
        parse_mode="html",
    )
    await asst.send_message(target, get_string("pmbot_7"))


# --------------------------------------- END -------------------------------------------- #

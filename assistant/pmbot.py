# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

# https://github.com/xditya/TeleBot/blob/master/telebot/plugins/mybot/pmbot/incoming.py

# --------------------------------------- Imports -------------------------------------------- #

import os

from telethon.errors.rpcerrorlist import MessageIdInvalidError, UserNotParticipantError
from telethon.tl.custom import Button
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import Channel, Chat
from telethon.utils import get_display_name

from pyUltroid.dB.base import KeyManager
from pyUltroid.dB.botchat_db import *
from custom.tg import ikm
from custom.fmt import fmtmsg

from . import *
from pyUltroid.fns import fns

botb = KeyManager("BOTBLS", cast=list)
FSUB = udB.get_key("PMBOT_FSUB")
CACHE = {}
# --------------------------------------- Incoming -------------------------------------------- #


@asst_cmd(
    load=AST_PLUGINS,
    incoming=True,
    func=lambda e: e.is_private and not botb.contains(e.sender_id),
)
async def on_new_mssg(event):
    who = event.sender_id
    # doesn't reply to that user anymore
    if event.text.startswith("/") or who == OWNER_ID:
        return
    if FSUB:
        MSG = ""
        BTTS = []
        for chat in FSUB:
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
        if MSG and BTTS:
            return await event.reply(
                fmtmsg("Subscription Required 📢", msgs=MSG),
                buttons=ikm(BTTS),
                parse_mode="html",
            )
    try:
        xx = await event.forward_to(OWNER_ID)
    except MessageIdInvalidError:
        if not event.message and not event.media:
            return
        try:
            xx = await event.client.send_message(
                OWNER_ID, event.message or "", file=event.media
            )
        except Exception:
            LOGS.exception("PmBot fallback copy failed")
            return
    if event.fwd_from:
        await xx.reply(
            f"From {fns.inline_mention(event.sender)} [`{event.sender_id}`]"
        )
    add_stuff(xx.id, who)


# --------------------------------------- Outgoing -------------------------------------------- #


@asst_cmd(
    load=AST_PLUGINS,
    from_users=[OWNER_ID],
    incoming=True,
    func=lambda e: e.is_private and e.is_reply,
)
async def on_out_mssg(event):
    x = event.reply_to_msg_id
    to_user = get_who(x)
    if event.text.startswith("/who"):
        try:
            k = await asst.get_entity(to_user)
            photu = await event.client.download_profile_photo(k.id)
            text_out = fmtmsg(
                "User Information 👤",
                {
                    "Name": get_display_name(k),
                    "ID": str(k.id),
                    "Profile Link": helper.inline_mention(k),
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
        except BaseException as er:
            return await event.reply(
                fmtmsg("Error ❌", msgs=str(er)),
                parse_mode="html",
            )
    elif event.text.startswith("/"):
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
    target = get_who(event.reply_to_msg_id)
    if botb.contains(target):
        return await event.reply(
            fmtmsg("Already Banned", msgs=get_string("pmbot_3")),
            parse_mode="html",
        )

    botb.add(target)
    await event.reply(
        fmtmsg("User Banned 🚫", msgs=f"User ID: <code>{target}</code>"),
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
    target = get_who(event.reply_to_msg_id)
    if not botb.contains(target):
        return await event.reply(
            fmtmsg("Not Banned", msgs=get_string("pmbot_6")),
            parse_mode="html",
        )

    botb.remove(target)
    await event.reply(
        fmtmsg("User Unbanned ✅", msgs=f"User ID: <code>{target}</code>"),
        parse_mode="html",
    )
    await asst.send_message(target, get_string("pmbot_7"))


# --------------------------------------- END -------------------------------------------- #

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from . import get_help

__doc__ = get_help("help_admintools")

import asyncio

from telethon.errors import BadRequestError
from telethon.errors.rpcerrorlist import ChatNotModifiedError, UserIdInvalidError
from telethon.tl import functions as tl_functions
from telethon.tl.functions.channels import EditAdminRequest, GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest, SetHistoryTTLRequest
from telethon.tl.types import InputMessagesFilterPinned
from telethon.utils import get_display_name

from pyUltroid.dB import DEVLIST
from pyUltroid.fns import fns
from pyUltroid.fns import fns

from . import HNDLR, LOGS, eod, eor, get_string, types, ultroid_cmd


@ultroid_cmd(
    pattern="promote( (.*)|$)",
    admins_only=True,
    manager=True,
    require="add_admins",
    fullsudo=True,
)
async def prmte(ult):
    xx = await ult.eor(get_string("com_1"))
    user, rank = await fns.get_uinfo(ult)
    rank = rank or "Admin"
    FullRight = False
    if not user:
        return await xx.edit(get_string("pro_1"))
    if rank.split()[0] == "-f":
        try:
            rank = rank.split(maxsplit=1)[1]
        except IndexError:
            rank = "Admin"
        FullRight = True
    try:
        if FullRight:
            await ult.client(
                EditAdminRequest(ult.chat_id, user.id, ult.chat.admin_rights, rank)
            )
        else:
            await ult.client.edit_admin(
                ult.chat_id,
                user.id,
                invite_users=True,
                ban_users=True,
                delete_messages=True,
                pin_messages=True,
                manage_call=True,
                title=rank,
            )
        await eod(
            xx, get_string("pro_2").format(fns.inline_mention(user), ult.chat.title, rank)
        )
    except Exception as ex:
        return await xx.edit(f"`{ex}`")


@ultroid_cmd(
    pattern="demote( (.*)|$)",
    admins_only=True,
    manager=True,
    require="add_admins",
    fullsudo=True,
)
async def dmote(ult):
    xx = await ult.eor(get_string("com_1"))
    user, rank = await fns.get_uinfo(ult)
    if not rank:
        rank = "Not Admin"
    if not user:
        return await xx.edit(get_string("de_1"))
    try:
        await ult.client.edit_admin(
            ult.chat_id,
            user.id,
            invite_users=None,
            ban_users=None,
            delete_messages=None,
            pin_messages=None,
            manage_call=None,
            title=rank,
        )
        await eod(xx, get_string("de_2").format(fns.inline_mention(user), ult.chat.title))
    except Exception as ex:
        return await xx.edit(f"`{ex}`")


@ultroid_cmd(
    pattern="ban( (.*)|$)",
    admins_only=True,
    manager=True,
    require="ban_users",
    fullsudo=True,
)
async def bban(ult):
    something = await fns.get_uinfo(ult)
    if not something:
        return
    user, reason = something
    if not user:
        return await eod(ult, get_string("ban_1"))
    if user.id in DEVLIST:
        return await eod(ult, get_string("ban_2"))
    try:
        await ult.client.edit_permissions(ult.chat_id, user.id, view_messages=False)
    except UserIdInvalidError:
        return await eod(ult, get_string("adm_1"))
    except BadRequestError:
        return await eod(ult, get_string("ban_3"))
    senderme = fns.inline_mention(await ult.get_sender())
    userme = fns.inline_mention(user)
    text = get_string("ban_4").format(userme, senderme, ult.chat.title)
    if reason:
        text += get_string("ban_5").format(reason)
    await eod(ult, text)


@ultroid_cmd(
    pattern="unban( (.*)|$)",
    admins_only=True,
    manager=True,
    require="ban_users",
    fullsudo=True,
)
async def uunban(ult):
    xx = await ult.eor(get_string("com_1"))
    if ult.text[1:].startswith("unbanall"):
        return
    something = await fns.get_uinfo(ult)
    if not something:
        return
    user, reason = something
    if not user:
        return await xx.edit(get_string("unban_1"))
    try:
        await ult.client.edit_permissions(ult.chat_id, user.id, view_messages=True)
    except UserIdInvalidError:
        return await eod(ult, get_string("adm_1"))
    except BadRequestError:
        return await xx.edit(get_string("adm_2"))
    sender = fns.inline_mention(await ult.get_sender())
    text = get_string("unban_3").format(fns.inline_mention(user), sender, ult.chat.title)
    if reason:
        text += get_string("ban_5").format(reason)
    await xx.edit(text)


@ultroid_cmd(
    pattern="kick( (.*)|$)",
    manager=True,
    require="ban_users",
    fullsudo=True,
)
async def kck(ult):
    if "kickme" in ult.text:
        return
    if ult.is_private:
        return await ult.eor("`Use this in Group/Channel.`", time=5)
    ml = ult.text.split(" ", maxsplit=1)[0]
    xx = await ult.eor(get_string("com_1"))
    something = await fns.get_uinfo(ult)
    if not something:
        return
    user, reason = something
    if not user:
        return await xx.edit(get_string("adm_1"))
    if user.id in DEVLIST:
        return await xx.edit(get_string("kick_2"))
    if getattr(user, "is_self", False):
        return await xx.edit(get_string("kick_3"))
    try:
        await ult.client.kick_participant(ult.chat_id, user.id)
    except BadRequestError as er:
        LOGS.info(er)
        return await xx.edit(get_string("kick_1"))
    except Exception as e:
        LOGS.exception(e)
        return
    text = get_string("kick_4").format(
        fns.inline_mention(user), fns.inline_mention(await ult.get_sender()), ult.chat.title
    )
    if reason:
        text += get_string("ban_5").format(reason)
    await xx.edit(text)


@ultroid_cmd(
    pattern="tban( (.*)|$)",
    admins_only=True,
    manager=True,
    require="ban_users",
    fullsudo=True,
)
async def tkicki(e):
    huh = e.text.split()
    inputt = None
    try:
        tme = huh[1]
    except IndexError:
        return await e.eor(get_string("adm_3"), time=15)
    try:
        inputt = huh[2]
    except IndexError:
        if e.reply_to_msg_id:
            inputt = (await e.get_reply_message()).sender_id
    if not inputt:
        return await e.eor(get_string("tban_1"))
    userid = await e.client.parse_id(inputt)
    try:
        user = await e.client.get_entity(userid)
    except Exception as ex:
        return await eor(e, f"`{ex}`")
    try:
        bun = fns.ban_time(tme)
        await e.client.edit_permissions(
            e.chat_id, user.id, until_date=bun, view_messages=False
        )
        await eod(
            e,
            get_string("tban_2").format(fns.inline_mention(user), e.chat.title, tme),
            time=15,
        )
    except Exception as m:
        return await e.eor(str(m))


@ultroid_cmd(pattern="pin$", manager=True, require="pin_messages", fullsudo=True)
async def pin(msg):
    if not msg.is_reply:
        return await eor(msg, get_string("pin_1"))
    me = await msg.get_reply_message()
    if me.is_private:
        text = "`Pinned.`"
    else:
        text = f"Pinned [This Message]({me.message_link}) !"
    try:
        await msg.client.pin_message(msg.chat_id, me.id, notify=False)
    except BadRequestError:
        return await eor(msg, get_string("adm_2"))
    except Exception as e:
        return await eor(msg, f"**ERROR:**`{e}`")
    await eor(msg, text)


@ultroid_cmd(
    pattern="unpin($| (.*))",
    manager=True,
    require="pin_messages",
    fullsudo=True,
)
async def unp(ult):
    xx = await ult.eor(get_string("com_1"))
    ch = (ult.pattern_match.group(1).strip()).strip()
    msg = None
    if ult.is_reply:
        msg = ult.reply_to_msg_id
    elif ch != "all":
        return await xx.edit(get_string("unpin_1").format(HNDLR))
    try:
        await ult.client.unpin_message(ult.chat_id, msg)
    except BadRequestError:
        return await xx.edit(get_string("adm_2"))
    except Exception as e:
        return await xx.edit(f"**ERROR:**`{e}`")
    await xx.edit("`Unpinned!`")


@ultroid_cmd(
    pattern="tpin( (.*)|$)",
    admins_only=True,
    manager=True,
    require="pin_messages",
    fullsudo=True,
)
async def pin_message(ult):
    match = ult.pattern_match.group(1).strip()
    if not ult.is_reply:
        return await ult.eor("`Reply to message..`", time=6)
    if not match:
        return await ult.eor("`Please provide time..`", time=8)
    msg = await ult.eor(get_string("com_1"))
    msg_id = ult.reply_to_msg_id
    try:
        time = fns.ban_time(match)
        await ult.client.pin_message(ult.chat_id, msg_id)
        await msg.eor(f"`pinned for time` `{time}`", time=8)
    except Exception as er:
        return await msg.edit(str(er))
    await asyncio.sleep(time)
    try:
        await ult.client.unpin_message(ult.chat_id, msg_id)
    except Exception as er:
        LOGS.exception(er)


@ultroid_cmd(pattern="purge( (.*)|$)", manager=True, require="delete_messages")
async def fastpurger(purg):
    match = purg.pattern_match.group(1).strip()
    try:
        ABC = purg.text[6]
    except IndexError:
        ABC = None
    if ABC and purg.text[6] in ["m", "a"]:
        return
    if not purg._client._bot and (
        (match)
        or (purg.is_reply and (purg.is_private or isinstance(purg.chat, types.Chat)))
    ):
        p = 0
        async for msg in purg.client.iter_messages(
            purg.chat_id,
            limit=int(match) if match else None,
            min_id=purg.reply_to_msg_id if purg.is_reply else None,
        ):
            await msg.delete()
            p += 0
        return await eor(purg, f"Purged {p} Messages! ", time=5)
    if not purg.reply_to_msg_id:
        return await eor(purg, get_string("purge_1"), time=10)
    try:
        await purg.client.delete_messages(
            purg.chat_id, list(range(purg.reply_to_msg_id, purg.id))
        )

    except Exception as er:
        LOGS.info(er)
    await purg.eor("__Fast purge complete!__", time=5)


@ultroid_cmd(
    pattern="purgeme( (.*)|$)",
)
async def fastpurgerme(purg):
    if num := purg.pattern_match.group(1).strip():
        try:
            nnt = int(num)
        except Exception:
            await eor(purg, get_string("com_3"), time=5)
            return
        mp = 0
        async for mm in purg.client.iter_messages(
            purg.chat_id, limit=nnt, from_user="me"
        ):
            await mm.delete()
            mp += 1
        await eor(purg, f"Purged {mp} Messages!", time=5)
        return
    elif not purg.reply_to_msg_id:
        return await eod(
            purg,
            "`Reply to a message to purge from or use it like ``purgeme <num>`",
            time=10,
        )
    chat = await purg.get_input_chat()
    msgs = []
    async for msg in purg.client.iter_messages(
        chat,
        from_user="me",
        min_id=purg.reply_to_msg_id,
    ):
        msgs.append(msg)
    if msgs:
        await purg.client.delete_messages(chat, msgs)
    await purg.eor(
        "__Fast purge complete!__\n**Purged** `" + str(len(msgs)) + "` **messages.**",
        time=5,
    )


@ultroid_cmd(
    pattern="purgeall$",
)
async def _(e):
    if not e.is_reply:
        return await eod(
            e,
            get_string("purgeall_1"),
        )

    msg = await e.get_reply_message()
    name = msg.sender
    try:
        await e.client.delete_messages(e.chat_id, from_user=msg.sender_id)
        await e.eor(get_string("purgeall_2").format(name.first_name), time=5)
    except Exception as er:
        return await e.eor(str(er), time=5)


@ultroid_cmd(pattern="pinned", manager=True, groups_only=True)
async def djshsh(event):
    chat = await event.get_chat()
    if isinstance(chat, types.Chat):
        FChat = await event.client(GetFullChatRequest(chat.id))
    elif isinstance(chat, types.Channel):
        FChat = await event.client(GetFullChannelRequest(chat.id))
    else:
        return
    msg_id = FChat.full_chat.pinned_msg_id
    if not msg_id:
        return await event.eor(get_string("pinned_1"))
    msg = await event.client.get_messages(chat.id, ids=msg_id)
    if msg:
        await event.eor(get_string("pinned_2").format(msg.message_link))


@ultroid_cmd(
    pattern="listpinned$",
)
async def get_all_pinned(event):
    x = await event.eor(get_string("com_1"))
    chat_id = (str(event.chat_id)).replace("-100", "")
    chat_name = get_display_name(event.chat)
    a = ""
    c = 1
    async for i in event.client.iter_messages(
        event.chat_id, filter=InputMessagesFilterPinned
    ):
        if i.message:
            t = " ".join(i.message.split()[:4])
            txt = f"{t}...."
        else:
            txt = "Go to message."
        a += f"{c}. <a href=https://t.me/c/{chat_id}/{i.id}>{txt}</a>\n"
        c += 1

    if c == 1:
        m = f"<b>The pinned message in {chat_name}:</b>\n\n"
    else:
        m = f"<b>List of pinned message(s) in {chat_name}:</b>\n\n"

    if not a:
        return await eor(x, get_string("listpin_1"), time=5)

    await x.edit(m + a, parse_mode="html")


@ultroid_cmd(
    pattern="autodelete( (.*)|$)",
    admins_only=True,
)
async def autodelte(ult):
    match = ult.pattern_match.group(1).strip()
    if not match or match not in ["24h", "7d", "1m", "off"]:
        return await ult.eor("`Please Use in Proper Format..`", time=5)
    if match == "24h":
        tt = 3600 * 24
    elif match == "7d":
        tt = 3600 * 24 * 7
    elif match == "1m":
        tt = 3600 * 24 * 31
    else:
        tt = 0
    try:
        await ult.client(SetHistoryTTLRequest(ult.chat_id, period=tt))
    except ChatNotModifiedError:
        return await ult.eor(
            f"Auto Delete Setting is Already same to `{match}`", time=5
        )
    await ult.eor(f"Auto Delete Status Changed to `{match}` !")


# ── Invite-request manager (B6) ────────────────────────────────────────────
#
# `.invreq`                          — list pending join requests (compact).
# `.invreq approve|decline <user>`   — approve/decline one requester
#                                       (`<user>` id/username, or reply to
#                                       their message).
# `.invreq approve|decline all`      — approve/decline every pending request.
#
# Raw requests (`messages.GetChatInviteImportersRequest(requested=True)`,
# `messages.HideChatJoinRequestRequest`, `messages.HideAllChatJoinRequestsRequest`)
# are looked up via `getattr` on `telethon.tl.functions.messages` so a
# Telethon build without this fork's schema degrades to a clear message
# instead of an `AttributeError`.


def _friendly_invreq_error(exc):
    """Turn any join-request RPCError/generic exception into a short,
    chat-friendly message — never a traceback."""
    message = str(getattr(exc, "message", "") or exc).upper()
    if "ADMIN" in message:
        return f"🚫 **Missing rights to manage join requests.**\n`{exc}`"
    if "MISSING" in message or "USER_NOT_PARTICIPANT" in message:
        return f"❌ **No pending join request found for that user.**\n`{exc}`"
    return f"❌ **Error.**\n`{exc}`"


async def _invreq_list(event, chat):
    req_cls = getattr(tl_functions.messages, "GetChatInviteImportersRequest", None)
    if req_cls is None:
        return await event.eor(
            "`messages.GetChatInviteImportersRequest isn't available in "
            "this Telethon build (fork layer mismatch?)`",
            time=8,
        )
    ok = await event.eor(get_string("com_1"))
    try:
        result = await event.client(
            req_cls(
                peer=chat,
                offset_date=None,
                offset_user=types.InputUserEmpty(),
                limit=50,
                requested=True,
            )
        )
    except Exception as exc:
        return await eod(ok, _friendly_invreq_error(exc))

    if not result.importers:
        return await eod(ok, "`No pending join requests.`")

    users = {u.id: u for u in result.users}
    lines = [f"**Pending join requests** (`{result.count}` total):\n"]
    for i, imp in enumerate(result.importers, 1):
        user = users.get(imp.user_id)
        name = get_display_name(user) if user else f"id {imp.user_id}"
        date = imp.date.strftime("%Y-%m-%d %H:%M") if imp.date else "?"
        line = f"{i}. **{name}** (`{imp.user_id}`) — `{date}`"
        if imp.about:
            line += f"\n   _{imp.about}_"
        lines.append(line)
    await eod(ok, "\n".join(lines))


async def _invreq_hide_all(event, chat, approved):
    req_cls = getattr(tl_functions.messages, "HideAllChatJoinRequestsRequest", None)
    if req_cls is None:
        return await event.eor(
            "`messages.HideAllChatJoinRequestsRequest isn't available in "
            "this Telethon build (fork layer mismatch?)`",
            time=8,
        )
    ok = await event.eor(get_string("com_1"))
    try:
        await event.client(req_cls(peer=chat, approved=approved))
    except Exception as exc:
        return await eod(ok, _friendly_invreq_error(exc))
    verb = "Approved" if approved else "Declined"
    await eod(ok, f"✅ **{verb} all pending join requests.**")


async def _invreq_hide_one(event, chat, user_id, approved):
    req_cls = getattr(tl_functions.messages, "HideChatJoinRequestRequest", None)
    if req_cls is None:
        return await event.eor(
            "`messages.HideChatJoinRequestRequest isn't available in this "
            "Telethon build (fork layer mismatch?)`",
            time=8,
        )
    ok = await event.eor(get_string("com_1"))
    try:
        await event.client(req_cls(peer=chat, user_id=user_id, approved=approved))
    except Exception as exc:
        return await eod(ok, _friendly_invreq_error(exc))
    verb = "Approved" if approved else "Declined"
    await eod(ok, f"✅ **{verb}** join request from `{user_id}`.")


@ultroid_cmd(
    pattern="invreq( (.*)|$)",
    admins_only=True,
    manager=True,
    require="invite_users",
    fullsudo=True,
)
async def _invite_request_manager(event):
    raw = event.pattern_match.group(1).strip()
    tokens = raw.split()

    try:
        chat = await event.get_input_chat()
    except Exception as exc:
        return await event.eor(f"`Couldn't resolve this chat: {exc}`", time=6)

    if not tokens:
        return await _invreq_list(event, chat)

    action = tokens[0].lower()
    if action not in ("approve", "decline"):
        return await event.eor(
            "`Usage: .invreq [approve|decline] [all|<user>]`", time=8
        )
    approved = action == "approve"

    target = tokens[1] if len(tokens) > 1 else None
    if target and target.lower() == "all":
        return await _invreq_hide_all(event, chat, approved)

    user_id = None
    if target:
        try:
            user_id = await event.client.parse_id(target)
        except Exception as exc:
            return await event.eor(f"`Couldn't resolve user: {exc}`", time=6)
    elif event.is_reply:
        reply = await event.get_reply_message()
        user_id = reply.sender_id
    if user_id is None:
        return await event.eor(
            "`Usage: .invreq approve|decline <user>|all` (or reply to the "
            "requester's message).`",
            time=8,
        )

    await _invreq_hide_one(event, chat, user_id, approved)

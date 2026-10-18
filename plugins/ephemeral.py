# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""
✘ User commands (routed on the user account):

* ``{i}eph <text>`` — broadcast ephemeral message to the current chat.
* ``{i}whisper <@user|id> <text>`` (alias ``{i}whspr``) — ephemeral message
  visible only to that user. Reply to someone and pass only ``<text>`` to
  whisper back to the person you replied to.
* ``{i}ephdel <id>`` — delete a broadcast ephemeral message you sent.
* ``{i}welcome on <text>`` — enable auto-welcome for new joiners (ephemeral).
* ``{i}welcome off`` — disable auto-welcome.
* ``{i}welcome text <text>`` — change welcome message text.
* ``{i}welcome list`` — list current welcome messages.
* ``{i}welcome del`` — delete the welcome message.
* ``{i}welcome reset`` — reset welcome to default.

✘ Assistant-bot commands (owner-only, delivered from the user account):

* ``/eph <text>`` and ``/whisper <@user|id> <text>`` — same surface as above.

✘ Inline mode (selection via assistant, delivery via user account):

``@<assistant> eph <text>`` or ``@<assistant> whisper <@user|id> <text>`` —
the assistant returns one article with a coloured Send button. Tapping it
sends the ephemeral message from your user account into that chat. The message
text is kept in-memory only and never rendered into the inline result, so the
inline preview never leaks the whisper content.
"""

import time

from telethon import utils as _tl_utils
from telethon.tl import types
from telethon.tl.functions import ephemeral as _ephemeral
from telethon.tl.types import (
    InputRichMessageHTML,
    MessageActionChatAddUser,
    MessageActionChatJoinedByLink,
    MessageActionChatJoinedByRequest,
    MessageActionChatCreate,
    UpdateNewMessage,
    UpdateNewChannelMessage,
)

DeleteMessageRequest = _ephemeral.DeleteMessageRequest
SendMessageRequest = _ephemeral.SendMessageRequest
ReportMessageRequest = _ephemeral.ReportMessageRequest
MessageActionChatJoinedViaCommunity = getattr(
    types, "MessageActionChatJoinedViaCommunity", None
)
GetWelcomeMessagesRequest = getattr(_ephemeral, "GetWelcomeMessagesRequest", None)
DeleteWelcomeMessageRequest = getattr(_ephemeral, "DeleteWelcomeMessageRequest", None)
DeleteAllWelcomeMessagesRequest = getattr(
    _ephemeral, "DeleteAllWelcomeMessagesRequest", None
)
EditMessageRequest = getattr(_ephemeral, "EditMessageRequest", None)

from pyUltroid._core._cbs import cb, cb_pattern
from pyUltroid._core.events import InlineResultBuilder

from custom.fmt import fmtmsg
from custom.entities import parse_rich
from custom.tg import ikm

from . import HNDLR, LOGS, callback, eor, in_pattern, ultroid_bot, ultroid_cmd, asst_cmd


def _rich_message_html(html):
    """Build the native layer-230 HTML payload; markup lives on the request."""
    return InputRichMessageHTML(html=html)


# --------------------------------------------------------------------------- #
#  Delivery primitives                                                        #
# --------------------------------------------------------------------------- #


class _EphemeralSendRequest(SendMessageRequest):
    """``ephemeral.sendMessage`` whose inputs are pre-resolved by
    :func:`_send_ephemeral`.

    The generated ``resolve()`` runs ``get_input_entity`` on ``receiver_id``;
    that cannot cast the broadcast sentinel ``InputUserEmpty`` (it raises
    ``TypeError`` out of the uncaught ``utils.get_peer`` path). We resolve
    ``peer`` and ``receiver_id`` ourselves and no-op the default resolver so a
    broadcast and a whisper share one code path.
    """

    async def resolve(self, client, utils):
        return None


class _EphemeralDeleteRequest(DeleteMessageRequest):
    """``ephemeral.deleteMessage`` with pre-resolved inputs (same reasoning)."""

    async def resolve(self, client, utils):
        return None


def _reply_to(event):
    """Build an ``InputReplyToMessage`` when the command is a reply, else None."""
    try:
        rid = event.reply_to_msg_id
    except Exception:
        return None
    return types.InputReplyToMessage(reply_to_msg_id=rid) if rid else None


async def _send_ephemeral(
    client,
    peer,
    text,
    entities=None,
    *,
    receiver=None,
    reply_to=None,
    noforwards=False,
    welcome=False,
    rich=None,
):
    """Deliver one ephemeral message.

    ``receiver=None`` → broadcast (``InputUserEmpty``, everyone in ``peer``
    sees it); pass a real user → whisper (only they see it). ``peer`` is the
    chat the message lands in. Both inputs are pre-resolved here.

    ``noforwards=True`` prevents the recipient from forwarding the message.
    ``welcome=True`` marks the message as a Telegram welcome message.
    ``rich`` is an ``InputRichMessage`` subclass for rich formatting.
    """
    peer_input = await client.get_input_entity(peer)
    if receiver is None:
        receiver_input = types.InputUserEmpty()
    else:
        receiver_input = _tl_utils.get_input_user(
            await client.get_input_entity(receiver)
        )
    request = _EphemeralSendRequest(
        peer=peer_input,
        receiver_id=receiver_input,
        message=text,
        entities=entities or None,
        reply_to=reply_to,
        noforwards=noforwards,
        welcome=welcome,
        rich_message=rich,
    )
    return await client(request)


async def _send_strong_whisper(
    client,
    peer,
    text,
    receiver,
    entities=None,
    reply_to=None,
):
    """Deliver a cryptographically-private ephemeral whisper.

    Uses ``noforwards=True`` so the recipient cannot forward, ``rich_message``
    (HTML) for strong formatting, and a properly resolved ``receiver_id`` so
    that logically only the sender (userbot) and the target user can see the
    handshake content. The message never enters the normal message pool
    for other participants.
    """
    clean, entities = parse_rich(text, "mdv3") if entities is None else (text, entities)
    rich = _rich_message_html(
        html=(
            f"<blockquote expandable>"
            f"<b>🔒 Private Whisper</b>"
            f"</blockquote>\n\n"
            f"{clean}"
        ),
    )
    return await _send_ephemeral(
        client,
        peer,
        clean,
        entities,
        receiver=receiver,
        reply_to=reply_to,
        noforwards=True,
        rich=rich,
    )


async def _send_welcome(
    client,
    peer,
    text,
    new_user_entity,
):
    """Deliver an ephemeral welcome message to a newly-joined user.

    Uses ``welcome=True`` so Telegram treats it as an official welcome
    message, with ``noforwards=True`` to keep it contained. Only the
    new joiner and the userbot can see it.
    """
    clean, _ = parse_rich(text, "mdv3")
    rich = _rich_message_html(
        html=(
            f"<blockquote expandable>"
            f'<b>👋 Welcome to {getattr(peer, "title", "this chat")}</b>'
            f"</blockquote>\n\n"
            f"{clean}"
        ),
    )
    return await _send_ephemeral(
        client,
        peer,
        clean,
        receiver=new_user_entity,
        welcome=True,
        noforwards=True,
        rich=rich,
    )


async def _delete_ephemeral(client, peer, message_id):
    """Delete a broadcast ephemeral message by id."""
    peer_input = await client.get_input_entity(peer)
    request = _EphemeralDeleteRequest(
        peer=peer_input,
        receiver_id=types.InputUserEmpty(),
        id=int(message_id),
    )
    return await client(request)


async def _resolve_receiver(client, raw, event=None):
    """Resolve a whisper receiver to a user entity, or ``None``.

    ``raw`` may be an @username, phone, numeric id or ``me``/``self``. When
    ``raw`` is empty and ``event`` is a reply, the replied-to sender is used.
    Unresolvable values return ``None`` (caller reports the usage error).
    """
    raw = (raw or "").strip()
    if raw:
        try:
            return await client.get_entity(raw)
        except (ValueError, TypeError):
            return None
    if event is not None:
        try:
            reply = await event.get_reply_message()
        except Exception:
            reply = None
        if reply is not None and getattr(reply, "sender_id", None):
            try:
                return await client.get_entity(reply.sender_id)
            except (ValueError, TypeError):
                return None
    return None


def _display_receiver(entity):
    """Safe human label for a resolved user entity."""
    uname = getattr(entity, "username", None)
    if uname:
        return f"@{uname}"
    first = getattr(entity, "first_name", None) or getattr(entity, "title", None)
    if first:
        return str(first)
    return str(getattr(entity, "id", entity))


def _looks_like_receiver(token):
    """True when a leading token is unambiguously a receiver, not message text."""
    token = token or ""
    if token.startswith(("@", "+")):
        return True
    if token.lstrip("+-").isdigit():
        return True
    return token.lower() in ("me", "self")


def _usage(pattern):
    return f"{HNDLR}{pattern}"


def _is_join_action(message):
    """Return the joining user's ID if *message* is a join action, else None."""
    action = getattr(message, "action", None)
    if action is None:
        return None
    if isinstance(action, MessageActionChatCreate):
        # creator joined — the creator's id is in the message
        return getattr(message, "from_id", None)
    join_action_types = (
        MessageActionChatAddUser,
        MessageActionChatJoinedByLink,
        MessageActionChatJoinedByRequest,
    )
    if MessageActionChatJoinedViaCommunity is not None:
        join_action_types += (MessageActionChatJoinedViaCommunity,)
    if isinstance(action, join_action_types):
        users = getattr(action, "users", None)
        if users:
            return users[0]
    return None


async def _get_welcome_messages(client, peer):
    """Fetch current welcome messages for *peer*."""
    if GetWelcomeMessagesRequest is None:
        return NotImplemented
    try:
        peer_input = await client.get_input_entity(peer)
        req = GetWelcomeMessagesRequest(peer=peer_input, hash=0)
        result = await client(req)
        if hasattr(result, "messages"):
            return result.messages
        return None
    except Exception:
        LOGS.exception("welcome: get failed")
        return None


async def _delete_welcome_message(client, peer, msg_id):
    """Delete a specific welcome message."""
    if DeleteWelcomeMessageRequest is None:
        raise NotImplementedError("Deleting welcome messages is unsupported by this Telegram API")
    try:
        peer_input = await client.get_input_entity(peer)
        req = DeleteWelcomeMessageRequest(peer=peer_input, id=msg_id)
        return await client(req)
    except Exception as er:
        LOGS.exception("welcome: delete failed")
        raise er


async def _delete_all_welcome_messages(client, peer):
    """Delete all welcome messages for *peer*."""
    if DeleteAllWelcomeMessagesRequest is None:
        raise NotImplementedError("Deleting welcome messages is unsupported by this Telegram API")
    try:
        peer_input = await client.get_input_entity(peer)
        req = DeleteAllWelcomeMessagesRequest(peer=peer_input)
        return await client(req)
    except Exception as er:
        LOGS.exception("welcome: delete all failed")
        raise er


async def _edit_welcome_message(client, peer, msg_id, text):
    """Edit an existing welcome message."""
    if EditMessageRequest is None:
        raise NotImplementedError("Editing welcome messages is unsupported by this Telegram API")
    try:
        clean, _ = parse_rich(text, "mdv3")
        rich = _rich_message_html(
            html=(f"<blockquote expandable><b>👋 Welcome</b></blockquote>\n\n{clean}"),
            color=0x2ECC71,
            icon="👋",
        )
        peer_input = await client.get_input_entity(peer)
        req = EditMessageRequest(
            receiver_id=types.InputUserEmpty(),
            id=msg_id,
            peer=peer_input,
            message=clean,
            rich_message=rich,
            welcome=True,
            noforwards=True,
        )
        return await client(req)
    except Exception as er:
        LOGS.exception("welcome: edit failed")
        raise er


# --------------------------------------------------------------------------- #
#  Inline pending registry (message text is kept in-memory, never previewed)  #
# --------------------------------------------------------------------------- #

_PENDING = {}
_PENDING_TTL = 300
_SEQ = 0
_WELCOME_DATA = {}  # chat_id -> welcome text (or None to disable)


def _prune_pending():
    now = time.time()
    for key in [k for k, v in _PENDING.items() if now > v.get("exp", 0)]:
        _PENDING.pop(key, None)


def _store_pending(payload):
    """Store an inline delivery payload; return its monotonic key."""
    global _SEQ
    _prune_pending()
    _SEQ += 1
    key = str(_SEQ)
    payload["exp"] = time.time() + _PENDING_TTL
    _PENDING[key] = payload
    return key


def _pending_tail(event):
    tail = event.data_match.group(1)
    return tail.decode("utf-8") if isinstance(tail, bytes) else str(tail)


# --------------------------------------------------------------------------- #
#  User commands                                                               #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"eph(?: |$)(.*)")
async def eph_cmd(event):
    text = (event.pattern_match.group(1) or "").strip()
    if not text:
        return await eor(
            event,
            fmtmsg(
                "Ephemeral Message",
                {
                    "Usage": _usage("eph <text>"),
                    "Sends": "a disappearing message to this chat",
                },
                foot=f"{_usage('whisper <user> <text>')} sends to one person",
            ),
            parse_mode="html",
            time=12,
        )

    clean, entities = parse_rich(text, "mdv3")
    try:
        await _send_ephemeral(
            event.client, event.chat_id, clean, entities, reply_to=_reply_to(event)
        )
    except Exception as er:
        LOGS.exception("eph: send failed")
        return await eor(
            event,
            fmtmsg("Ephemeral Message", {"Error": str(er)}),
            parse_mode="html",
            time=10,
        )

    await eor(
        event,
        fmtmsg(
            "Ephemeral Message",
            "Delivered to this chat",
            foot="visible to everyone — auto-disappears",
        ),
        parse_mode="html",
        time=8,
    )


@ultroid_cmd(pattern=r"(?:whisper|whspr)(?: |$)(.*)")
async def whisper_cmd(event):
    raw = (event.pattern_match.group(1) or "").strip()
    if not raw:
        return await eor(
            event,
            fmtmsg(
                "Whisper",
                {"Usage": _usage("whisper <@user|id> <text>")},
                foot=f"Ephemeral message only that user can see.\nAlias: {_usage('whspr')}",
            ),
            parse_mode="html",
            time=12,
        )

    parts = raw.split(None, 1)
    first = parts[0]
    text = parts[1].strip() if len(parts) > 1 else ""

    receiver = None
    if _looks_like_receiver(first):
        receiver = await _resolve_receiver(event.client, first)
    else:
        receiver = await _resolve_receiver(event.client, None, event)
        text = raw

    if receiver is None:
        return await eor(
            event,
            fmtmsg(
                "Whisper",
                {"Error": "unable to resolve a receiver"},
                foot=f"Usage: {_usage('whisper <@user|id> <text>')} — or reply to someone.",
            ),
            parse_mode="html",
            time=10,
        )

    if not text:
        return await eor(
            event,
            fmtmsg(
                "Whisper",
                {"Error": "no message text provided"},
                foot=f"Usage: {_usage('whisper <@user|id> <text>')}",
            ),
            parse_mode="html",
            time=10,
        )

    try:
        await _send_strong_whisper(
            event.client,
            event.chat_id,
            text,
            receiver,
            reply_to=_reply_to(event),
        )
    except Exception as er:
        LOGS.exception("whisper: send failed")
        return await eor(
            event, fmtmsg("Whisper", {"Error": str(er)}), parse_mode="html", time=10
        )

    await eor(
        event,
        fmtmsg(
            "Whisper",
            {"Receiver": _display_receiver(receiver)},
            foot="🔒 Private — only you and the recipient can see it",
        ),
        parse_mode="html",
        time=8,
    )


@ultroid_cmd(pattern=r"ephdel(?: |$)(.*)")
async def ephdel_cmd(event):
    raw = (event.pattern_match.group(1) or "").strip()
    try:
        mid = int(raw)
    except (TypeError, ValueError):
        return await eor(
            event,
            fmtmsg(
                "Ephemeral · Delete",
                {"Usage": _usage("ephdel <message-id>")},
            ),
            parse_mode="html",
            time=10,
        )

    try:
        await _delete_ephemeral(event.client, event.chat_id, mid)
    except Exception as er:
        LOGS.exception("ephdel: delete failed")
        return await eor(
            event,
            fmtmsg("Ephemeral · Delete", {"Error": str(er)}),
            parse_mode="html",
            time=10,
        )

    await eor(
        event,
        fmtmsg("Ephemeral · Delete", {"Deleted id": mid}),
        parse_mode="html",
        time=8,
    )


# --------------------------------------------------------------------------- #
#  Welcome commands                                                            #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"welcome(?: |$)(.*)")
async def welcome_cmd(event):
    """Manage ephemeral welcome messages for new joiners."""
    raw = (event.pattern_match.group(1) or "").strip()
    chat_id = event.chat_id

    if not raw:
        current = _WELCOME_DATA.get(chat_id)
        status = "ON" if current else "OFF"
        return await eor(
            event,
            fmtmsg(
                "Welcome Status",
                {"Status": status, "Text": current or "Not set"},
            ),
            parse_mode="html",
            time=10,
        )

    parts = raw.split(None, 1)
    cmd = parts[0].lower()

    if cmd == "on":
        text = parts[1].strip() if len(parts) > 1 else ""
        if not text:
            return await eor(
                event,
                fmtmsg("Welcome", {"Error": "provide text after 'on'"}),
                parse_mode="html",
                time=10,
            )
        _WELCOME_DATA[chat_id] = text
        await eor(
            event,
            fmtmsg("Welcome", {"Status": "ON", "Text": text}),
            parse_mode="html",
            time=8,
        )
    elif cmd == "off":
        _WELCOME_DATA.pop(chat_id, None)
        await eor(
            event,
            fmtmsg("Welcome", {"Status": "OFF"}),
            parse_mode="html",
            time=8,
        )
    elif cmd == "text":
        text = parts[1].strip() if len(parts) > 1 else ""
        if not text:
            return await eor(
                event,
                fmtmsg("Welcome", {"Error": "provide new text after 'text'"}),
                parse_mode="html",
                time=10,
            )
        _WELCOME_DATA[chat_id] = text
        await eor(
            event,
            fmtmsg("Welcome", {"Status": "text updated", "Text": text}),
            parse_mode="html",
            time=8,
        )
    elif cmd == "list":
        msgs = await _get_welcome_messages(event.client, event.chat_id)
        if msgs is NotImplemented:
            await eor(
                event,
                fmtmsg(
                    "Welcome",
                    {"Info": "Listing saved welcome messages is unsupported by this Telegram API"},
                ),
                parse_mode="html",
                time=10,
            )
        elif msgs:
            lines = [f"**Welcome messages ({len(msgs)}):**"]
            for m in msgs:
                mid = getattr(m, "id", "?")
                lines.append(f"  • ID {mid}")
            await eor(
                event,
                "\n".join(lines),
                parse_mode="html",
                time=12,
            )
        else:
            await eor(
                event,
                fmtmsg("Welcome", {"Info": "No welcome messages set"}),
                parse_mode="html",
                time=8,
            )
    elif cmd == "del":
        try:
            await _delete_all_welcome_messages(event.client, event.chat_id)
            await eor(
                event,
                fmtmsg("Welcome", {"Status": "All welcome messages deleted"}),
                parse_mode="html",
                time=8,
            )
        except Exception as er:
            await eor(
                event,
                fmtmsg("Welcome", {"Error": str(er)}),
                parse_mode="html",
                time=10,
            )
    elif cmd == "reset":
        _WELCOME_DATA.pop(chat_id, None)
        try:
            await _delete_all_welcome_messages(event.client, event.chat_id)
        except Exception:
            pass
        await eor(
            event,
            fmtmsg("Welcome", {"Status": "Reset to default"}),
            parse_mode="html",
            time=8,
        )
    else:
        await eor(
            event,
            fmtmsg(
                "Welcome",
                {
                    "Usage": f"{_usage('welcome on <text>')} | {_usage('welcome off')} | {_usage('welcome text <text>')} | {_usage('welcome list')} | {_usage('welcome del')} | {_usage('welcome reset')}",
                },
            ),
            parse_mode="html",
            time=10,
        )


# --------------------------------------------------------------------------- #
#  Assistant-bot commands (owner-only, delivered from the user account)       #
# --------------------------------------------------------------------------- #


@asst_cmd(pattern=r"eph(?: |$)(.*)", owner=True)
async def asst_eph_cmd(event):
    text = (event.pattern_match.group(1) or "").strip()
    if not text:
        return await event.reply(
            fmtmsg("Ephemeral Message", {"Usage": "/eph <text>"}), parse_mode="html"
        )
    clean, entities = parse_rich(text, "mdv3")
    try:
        await _send_ephemeral(ultroid_bot, event.chat_id, clean, entities)
    except Exception as er:
        LOGS.exception("asst /eph: send failed")
        return await event.reply(
            fmtmsg("Ephemeral Message", {"Error": str(er)}), parse_mode="html"
        )
    await event.reply(
        fmtmsg("Ephemeral Message", "Delivered", foot="auto-disappears"),
        parse_mode="html",
    )


@asst_cmd(pattern=r"(?:whisper|whspr)(?: |$)(.*)", owner=True)
async def asst_whisper_cmd(event):
    raw = (event.pattern_match.group(1) or "").strip()
    if not raw:
        return await event.reply(
            fmtmsg("Whisper", {"Usage": "/whisper <@user|id> <text>"}),
            parse_mode="html",
        )

    parts = raw.split(None, 1)
    first = parts[0]
    text = parts[1].strip() if len(parts) > 1 else ""

    receiver = None
    if _looks_like_receiver(first):
        receiver = await _resolve_receiver(ultroid_bot, first)
    else:
        receiver = await _resolve_receiver(ultroid_bot, None, event)
        text = raw

    if receiver is None:
        return await event.reply(
            fmtmsg("Whisper", {"Error": "unable to resolve a receiver"}),
            parse_mode="html",
        )
    if not text:
        return await event.reply(
            fmtmsg("Whisper", {"Error": "no message text provided"}),
            parse_mode="html",
        )

    try:
        await _send_strong_whisper(ultroid_bot, event.chat_id, text, receiver)
    except Exception as er:
        LOGS.exception("asst /whisper: send failed")
        return await event.reply(
            fmtmsg("Whisper", {"Error": str(er)}), parse_mode="html"
        )
    await event.reply(
        fmtmsg(
            "Whisper",
            {"Receiver": _display_receiver(receiver)},
            foot="🔒 Private — only you and the recipient can see it",
        ),
        parse_mode="html",
    )


# --------------------------------------------------------------------------- #
#  Inline mode (selection via assistant, send via user account)               #
# --------------------------------------------------------------------------- #


@in_pattern("eph(.*)", owner=True)
async def eph_inline(event):
    query = (event.pattern_match.group(1) or "").strip()
    builder = InlineResultBuilder(event)

    if not query:
        article = await builder.article(
            title="Ephemeral message",
            text="Usage: @bot eph <text> — send a disappearing message to this chat.",
            description="Send an ephemeral message",
        )
        return await event.answer([article] if article is not None else [])

    clean, entities = parse_rich(query, "mdv3")
    key = _store_pending({"text": clean, "entities": entities, "receiver": None})
    buttons = ikm([[("Send", cb("assistant", "eph", key), "primary")]])

    article = await builder.article(
        title="Send ephemeral message",
        text="Tap Send to deliver from your user account.",
        description="Coloured-button delivery",
        buttons=buttons,
    )
    await event.answer([article] if article is not None else [])


@in_pattern("whisper(.*)", owner=True)
async def whisper_inline(event):
    query = (event.pattern_match.group(1) or "").strip()
    builder = InlineResultBuilder(event)

    if not query:
        article = await builder.article(
            title="Whisper",
            text="Usage: @bot whisper <@user|id> <text> — ephemeral message to one person.",
            description="Whisper to a user",
        )
        return await event.answer([article] if article is not None else [])

    parts = query.split(None, 1)
    receiver = parts[0]
    text = parts[1].strip() if len(parts) > 1 else ""
    if not text:
        article = await builder.article(
            title="Whisper needs text",
            text="Usage: @bot whisper <@user|id> <text>.",
            description="Add message text",
        )
        return await event.answer([article] if article is not None else [])

    clean, entities = parse_rich(text, "mdv3")
    key = _store_pending({"text": clean, "entities": entities, "receiver": receiver})
    buttons = ikm([[("Whisper", cb("assistant", "whisper", key), "danger")]])

    article = await builder.article(
        title=f"Whisper to {receiver}",
        text="Tap Whisper to deliver from your user account.",
        description="Single-recipient ephemeral message",
        buttons=buttons,
    )
    await event.answer([article] if article is not None else [])


@callback(data=cb_pattern("assistant", "eph"), owner=True)
async def eph_inline_send(event):
    await _inline_deliver(event, whisper=False)


@callback(data=cb_pattern("assistant", "whisper"), owner=True)
async def whisper_inline_send(event):
    await _inline_deliver(event, whisper=True)


async def _inline_deliver(event, *, whisper):
    key = _pending_tail(event)
    pending = _PENDING.pop(key, None)
    if not pending or time.time() > pending.get("exp", 0):
        return await event.answer("Expired — send the inline query again.", alert=True)

    receiver = None
    if whisper:
        raw = pending.get("receiver")
        if not raw:
            return await event.answer("No receiver.", alert=True)
        try:
            receiver = await _resolve_receiver(ultroid_bot, raw)
        except Exception as er:
            LOGS.exception("whisper inline: resolve receiver")
            return await event.answer(f"Could not resolve receiver: {er}", alert=True)
        if receiver is None:
            return await event.answer("Could not resolve receiver.", alert=True)

    try:
        await _send_strong_whisper(
            ultroid_bot,
            event.chat_id,
            pending["text"],
            receiver,
        )
    except Exception as er:
        LOGS.exception("ephemeral inline: send failed")
        return await event.answer(f"Send failed: {er}", alert=True)

    await event.answer("Sent.", alert=True)


# --------------------------------------------------------------------------- #
#  Join listener (auto-welcome)                                               #
# --------------------------------------------------------------------------- #


async def _on_join_update(update):
    """Detect new joiners and send ephemeral welcome messages."""
    message = getattr(update, "message", None)
    if message is None:
        return

    join_user_id = _is_join_action(message)
    if join_user_id is None:
        return

    chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        return

    welcome_text = _WELCOME_DATA.get(chat_id)
    if not welcome_text:
        return

    try:
        new_user = await ultroid_bot.get_entity(join_user_id)
        await _send_welcome(ultroid_bot, chat_id, welcome_text, new_user)
        LOGS.debug("welcome sent to %s in %s", join_user_id, chat_id)
    except Exception:
        LOGS.exception("welcome: send failed for %s", join_user_id)


def _register_listeners():
    from pyUltroid.startup.dispatcher import register_listen

    # Observe raw updates to detect joins
    targets = (
        UpdateNewMessage,
        UpdateNewChannelMessage,
        types.UpdateNewEphemeralMessage,
        types.UpdateDeleteEphemeralMessages,
        types.UpdateEditEphemeralMessage,
    )
    register_listen(
        _on_join_update,
        client=ultroid_bot,
        listen=targets,
        once=False,
        name="ephemeral_join_welcome",
    )

    # Also observe existing ephemeral updates
    register_listen(
        lambda u: LOGS.debug("ephemeral update: %s", u.stringify()),
        client=ultroid_bot,
        listen=(
            types.UpdateNewEphemeralMessage,
            types.UpdateDeleteEphemeralMessages,
            types.UpdateEditEphemeralMessage,
        ),
        once=False,
        name="ephemeral_updates",
    )


_register_listeners()

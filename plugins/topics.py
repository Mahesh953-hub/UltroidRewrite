# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}topics`
    List forum topics of the current group (only works in forum-enabled
    supergroups).

• `{i}topic <name>`
    Create a new forum topic titled `<name>` in the current group.
    Needs "Manage Topics" admin rights.

• `{i}topicedit <id> <name>`
    Rename forum topic `<id>` to `<name>`.

• `{i}sendtopic <id> <text>`
    Send `<text>` into forum topic `<id>` of the current group.
"""

from telethon import helpers
from telethon.errors.rpcbaseerrors import RPCError
from telethon.errors.rpcerrorlist import (
    ChannelForumMissingError,
    ChatAdminRequiredError,
)
from telethon.tl import functions

from . import LOGS, eod, get_string, ultroid_cmd

_NOT_FORUM = (
    "`This chat isn't a forum-enabled supergroup — topics aren't available " "here.`"
)
_NEED_ADMIN = '`I need "Manage Topics" admin rights in this chat for that.`'


def _friendly_topic_error(exc):
    """Turn any RPC/library failure into a short chat-friendly message."""
    if isinstance(exc, ChannelForumMissingError):
        return _NOT_FORUM
    if isinstance(exc, ChatAdminRequiredError):
        return _NEED_ADMIN
    return f"❌ **Error.**\n`{exc}`"


async def _require_forum_chat(ult):
    """Fetch the current chat and make sure it's a forum; returns the chat
    (`types.Channel`) on success, or ``None`` after replying with an error."""
    chat = await ult.get_chat()
    if not getattr(chat, "forum", False):
        await ult.eor(_NOT_FORUM, time=8)
        return None
    return chat


async def _list_forum_topics(client, peer, limit=100):
    """Direct ``messages.GetForumTopicsRequest`` call.

    Bypasses the fork's own `TelegramClient.get_forum_topics()` helper,
    which (at the pinned layer) looks up
    ``functions.channels.GetForumTopicsRequest`` — a request that lives
    under ``functions.messages`` instead, so the helper always raises.
    """
    req_cls = getattr(functions.messages, "GetForumTopicsRequest", None)
    if req_cls is None:
        raise RuntimeError(
            "messages.GetForumTopicsRequest isn't available in this "
            "Telethon build (fork layer mismatch?)"
        )
    input_peer = await client.get_input_entity(peer)
    result = await client(
        req_cls(
            peer=input_peer,
            offset_date=None,
            offset_id=0,
            offset_topic=0,
            limit=limit,
            q=None,
        )
    )
    return list(getattr(result, "topics", None) or [])


@ultroid_cmd(pattern="topics$", groups_only=True, fullsudo=True)
async def _list_topics(ult):
    chat = await _require_forum_chat(ult)
    if chat is None:
        return
    ok = await ult.eor(get_string("com_1"))
    try:
        topics = await _list_forum_topics(ult.client, ult.chat_id)
    except Exception as exc:  # noqa: BLE001 - never leak a traceback to chat
        LOGS.debug("topics: list failed: %s", exc)
        return await eod(ok, _friendly_topic_error(exc))

    if not topics:
        return await eod(ok, "`No forum topics found in this chat.`")

    lines = [f"**Forum topics** ({len(topics)}):"]
    for topic in topics:
        flags = []
        if getattr(topic, "pinned", False):
            flags.append("📌")
        if getattr(topic, "closed", False):
            flags.append("🔒")
        marker = " ".join(flags)
        title = getattr(topic, "title", "?")
        lines.append(f"`{topic.id}` — **{title}** {marker}".rstrip())
    await eod(ok, "\n".join(lines))


@ultroid_cmd(pattern="topic( (.*)|$)", groups_only=True, fullsudo=True)
async def _create_topic(ult):
    title = ult.pattern_match.group(1).strip()
    if not title:
        return await ult.eor("`Usage: .topic <name>`", time=6)
    chat = await _require_forum_chat(ult)
    if chat is None:
        return

    ok = await ult.eor(get_string("com_1"))
    req_cls = getattr(functions.messages, "CreateForumTopicRequest", None)
    if req_cls is None:
        return await eod(
            ok,
            "`messages.CreateForumTopicRequest isn't available in this "
            "Telethon build (fork layer mismatch?)`",
        )
    try:
        input_peer = await ult.client.get_input_entity(ult.chat_id)
        await ult.client(
            req_cls(
                peer=input_peer,
                title=title,
                random_id=helpers.generate_random_long(),
            )
        )
    except RPCError as exc:
        LOGS.debug("topics: create failed: %s", exc)
        return await eod(ok, _friendly_topic_error(exc))
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("topics: create failed: %s", exc)
        return await eod(ok, f"❌ **Error.**\n`{exc}`")
    await eod(ok, f"✅ **Topic created:** `{title}`")


@ultroid_cmd(pattern="topicedit( (.*)|$)", groups_only=True, fullsudo=True)
async def _edit_topic(ult):
    raw = ult.pattern_match.group(1).strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        return await ult.eor("`Usage: .topicedit <id> <name>`", time=6)
    topic_id, new_title = int(parts[0]), parts[1].strip()
    if not new_title:
        return await ult.eor("`Usage: .topicedit <id> <name>`", time=6)
    chat = await _require_forum_chat(ult)
    if chat is None:
        return

    ok = await ult.eor(get_string("com_1"))
    req_cls = getattr(functions.messages, "EditForumTopicRequest", None)
    if req_cls is None:
        return await eod(
            ok,
            "`messages.EditForumTopicRequest isn't available in this "
            "Telethon build (fork layer mismatch?)`",
        )
    try:
        input_peer = await ult.client.get_input_entity(ult.chat_id)
        await ult.client(req_cls(peer=input_peer, topic_id=topic_id, title=new_title))
    except RPCError as exc:
        LOGS.debug("topics: edit failed: %s", exc)
        return await eod(ok, _friendly_topic_error(exc))
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("topics: edit failed: %s", exc)
        return await eod(ok, f"❌ **Error.**\n`{exc}`")
    await eod(ok, f"✅ **Topic `{topic_id}` renamed to:** `{new_title}`")


@ultroid_cmd(pattern="sendtopic( (.*)|$)", groups_only=True, fullsudo=True)
async def _send_to_topic(ult):
    raw = ult.pattern_match.group(1).strip()
    parts = raw.split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        return await ult.eor("`Usage: .sendtopic <id> <text>`", time=6)
    topic_id, text = int(parts[0]), parts[1]
    chat = await _require_forum_chat(ult)
    if chat is None:
        return

    try:
        await ult.client.send_message(ult.chat_id, text, reply_to=topic_id)
    except RPCError as exc:
        LOGS.debug("topics: sendtopic failed: %s", exc)
        return await ult.eor(_friendly_topic_error(exc), time=8)
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("topics: sendtopic failed: %s", exc)
        return await ult.eor(f"❌ **Error.**\n`{exc}`", time=8)
    await ult.eor(f"✅ **Sent to topic `{topic_id}`.**", time=5)

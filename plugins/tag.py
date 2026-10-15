# Ultroid - UserBot
# Copyright (C) 2021-2026 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""
✘ Commands Available

• `{i}tagall [-e] [-a] [-h] [-c <msg>] <query>` — Tag members with robust modes.
    `-e` — Ephemeral: only tagged users can see their mention.
    `-a` — Tag only admins.
    `-h` — Hidden tag using invisible Unicode characters.
    `-c <msg>` — Custom message for each tagged user.
    `-c <session_id>` — Cancel an active tagging session.
    `<query>` — filter: on, off, all, bots, rec, admins, owner.

• `{i}tagcancel` — Cancel the current tagging session.
"""

import time
import asyncio
import unicodedata

from telethon.tl.types import (
    ChannelParticipantAdmin,
    ChannelParticipantCreator,
    UserStatusOffline,
    UserStatusOnline,
    UserStatusRecently,
    UpdateNewMessage,
    UpdateNewChannelMessage,
)
from telethon.tl.functions.ephemeral import SendMessageRequest

from pyUltroid._core._cbs import cb, cb_pattern
from pyUltroid._core.events import InlineResultBuilder

from custom.fmt import fmtmsg
from custom.tg import ikm
from pyUltroid.fns import fns

from . import (
    HNDLR,
    eor,
    get_string,
    ultroid_bot,
    ultroid_cmd,
    asst_cmd,
    in_pattern,
    callback,
    LOGS,
)

_SESSIONS = {}


def _generate_session_id():
    return f"tag_{int(time.time())}"


async def _get_session(session_id):
    return _SESSIONS.get(session_id)


def _set_session(session_id, data):
    _SESSIONS[session_id] = data


def _del_session(session_id):
    _SESSIONS.pop(session_id, None)


def _is_hidden_tag(text):
    """Check if text uses invisible Unicode characters for hidden tagging."""
    if not text:
        return False
    for ch in text:
        if unicodedata.category(ch) == "Cf":
            return True
    return False


def _build_hidden_text(text):
    """Wrap text in invisible Unicode characters for hidden tagging."""
    invis = "\u200b"
    result = ""
    for ch in text:
        if ch == " ":
            result += invis + " " + invis
        else:
            result += ch + invis
    return result


def _normalize_mention_text(text, hidden=False):
    """Normalize mention text for display."""
    if hidden:
        return _build_hidden_text(text)
    return text


async def _send_ephemeral_tag(client, chat_id, receiver, text):
    """Send an ephemeral message visible only to the receiver."""
    try:
        peer = await client.get_input_entity(chat_id)
        receiver_input = await client.get_input_entity(receiver)
        request = SendMessageRequest(
            peer=peer,
            receiver_id=receiver_input,
            message=text,
            noforwards=True,
        )
        await client(request)
        return True
    except Exception as er:
        return False


async def _send_regular_tag(client, chat_id, text):
    """Send a regular message to the chat."""
    try:
        await client.send_message(chat_id, text)
        return True
    except Exception:
        return False


async def _get_filtered_participants(
    client, chat_id, filter_type="all", admins_only=False
):
    """Get participants filtered by type."""
    participants = await client.get_participants(chat_id, limit=200)
    filtered = []

    for p in participants:
        user = p.user
        participant = p.participant
        status = p.status
        deleted = getattr(user, "deleted", False)
        is_bot = user.bot

        if deleted:
            continue

        if admins_only and not isinstance(
            participant, (ChannelParticipantAdmin, ChannelParticipantCreator)
        ):
            continue

        if filter_type == "on" and not isinstance(status, UserStatusOnline):
            continue
        elif filter_type == "off" and not isinstance(status, UserStatusOffline):
            continue
        elif filter_type == "rec" and not isinstance(status, UserStatusRecently):
            continue
        elif filter_type == "admin" and not isinstance(
            participant, ChannelParticipantAdmin
        ):
            continue
        elif filter_type == "owner" and not isinstance(
            participant, ChannelParticipantCreator
        ):
            continue
        elif filter_type == "bots" and not is_bot:
            continue

        filtered.append(p)

    return filtered


async def _run_tag_session(
    session_id, event, participants, mode, extra, custom_msg, hidden
):
    """Execute a tagging session."""
    chat_id = event.chat_id
    sent_count = 0
    failed_count = 0
    total = len(participants)
    progress_msg = None

    try:
        progress_msg = await eor(
            event,
            fmtmsg(
                "Tag Session",
                {
                    "Status": "🔄 In Progress",
                    "Total": total,
                    "Mode": mode,
                },
            ),
            parse_mode="v2",
            time=8,
        )

        for idx, p in enumerate(participants):
            user = p.user
            mention = fns.inline_mention(user)

            # Build tag message
            if custom_msg:
                tag_text = custom_msg.replace("{name}", mention).replace(
                    "{user}", str(user.username or user.id)
                )
            elif mode == "hidden":
                tag_text = f"🗿 {_normalize_mention_text(mention, hidden=True)}"
            else:
                tag_text = f"🗿 {mention}"

            # Send tag
            if mode == "ephemeral" or extra == "e":
                ok = await _send_ephemeral_tag(ultroid_bot, chat_id, user.id, tag_text)
            else:
                ok = await _send_regular_tag(ultroid_bot, chat_id, tag_text)

            if ok:
                sent_count += 1
            else:
                failed_count += 1

            # Update progress every 5 users
            if (idx + 1) % 5 == 0 or (idx + 1) == total:
                percent = (idx + 1) / total * 100
                try:
                    await progress_msg.edit(
                        fmtmsg(
                            "Tag Session",
                            {
                                "Status": f"🔄 {percent:.0f}%",
                                "Tagged": f"{sent_count}/{total}",
                                "Failed": failed_count,
                            },
                        ),
                        parse_mode="v2",
                    )
                except Exception:
                    pass

            # Check if session was cancelled
            if _get_session(session_id) and _get_session(session_id).get("cancelled"):
                await progress_msg.edit(
                    fmtmsg(
                        "Tag Session",
                        {
                            "Status": "❌ Cancelled",
                            "Tagged": f"{sent_count}/{total}",
                        },
                    ),
                    parse_mode="v2",
                )
                _del_session(session_id)
                return

            # Small delay to avoid flood
            await asyncio.sleep(0.05)

        # Session complete
        await progress_msg.edit(
            fmtmsg(
                "Tag Session",
                {
                    "Status": "✅ Complete",
                    "Tagged": f"{sent_count}/{total}",
                    "Failed": failed_count,
                },
            ),
            parse_mode="v2",
        )
        _del_session(session_id)

    except Exception as er:
        import logging

        logging.getLogger("tag").exception("tag session error")
        try:
            await progress_msg.edit(
                fmtmsg("Tag Session", {"Error": str(er)}),
                parse_mode="v2",
            )
        except Exception:
            pass
        _del_session(session_id)


@ultroid_cmd(pattern=r"tagall(?:\s+(.*))?(?:\s+(.*))?$", groups_only=True)
async def tagall(e):
    """Tag members of a chat with robust filtering and ephemeral support."""
    args = (e.pattern_match.group(1) or "").strip() if e.pattern_match.group(1) else ""
    extra_args = (
        (e.pattern_match.group(2) or "").strip() if e.pattern_match.group(2) else ""
    )

    # Parse flags
    mode = "regular"
    filter_type = "all"
    admins_only = False
    hidden = False
    custom_msg = None
    cancel_session_id = None

    parts = args.split()
    if not parts:
        return await eor(
            e,
            fmtmsg(
                "Tag",
                {
                    "Usage": f"{HNDLR}tagall [-e] [-a] [-h] [-c <msg>] <on/off/all/bots/rec/admins/owner>"
                },
            ),
            parse_mode="v2",
            time=10,
        )

    flag_parts = []
    filter_candidates = []
    i = 0
    while i < len(parts):
        p = parts[i]
        if p == "-e":
            mode = "ephemeral"
        elif p == "-a":
            admins_only = True
        elif p == "-h":
            hidden = True
        elif p == "-c" and i + 1 < len(parts):
            custom_msg = parts[i + 1]
            i += 1
        elif p.startswith("-c"):
            custom_msg = p[2:].strip()
        elif p.startswith("-"):
            pass
        elif p in ("on", "off", "all", "bots", "rec", "admins", "owner"):
            filter_type = p.lower()
        else:
            filter_candidates.append(p)
        i += 1

    if filter_candidates and not filter_type:
        filter_type = filter_candidates[0].lower()

    valid_filters = ["on", "off", "all", "bots", "rec", "admins", "owner"]
    if filter_type not in valid_filters:
        return await eor(
            e,
            fmtmsg(
                "Tag",
                {
                    "Usage": f"{HNDLR}tagall {' '.join(valid_filters)} [-e] [-a] [-h] [-c <msg>]"
                },
            ),
            parse_mode="v2",
            time=10,
        )

    # Check existing sessions
    active_sessions = [sid for sid, s in _SESSIONS.items() if not s.get("cancelled")]
    if active_sessions and not cancel_session_id:
        return await eor(
            e,
            fmtmsg(
                "Tag",
                {
                    "Warning": f"Active session(s): {', '.join(active_sessions[:3])}",
                    "Use": f"{HNDLR}tagcancel <id> to cancel",
                },
            ),
            parse_mode="v2",
            time=10,
        )

    msg = await e.eor(get_string("com_1"))

    # Get participants
    await msg.edit(fmtmsg("Tag", {"Status": "Fetching participants..."}))
    try:
        participants = await _get_filtered_participants(
            ultroid_bot, e.chat_id, filter_type=filter_type, admins_only=admins_only
        )
    except Exception as er:
        return await eor(
            e,
            fmtmsg("Tag", {"Error": str(er)}),
            parse_mode="v2",
            time=10,
        )

    if not participants:
        return await eor(
            e,
            fmtmsg("Tag", {"Error": "No participants found"}),
            parse_mode="v2",
            time=5,
        )

    # Build tag message
    if hidden:
        tag_display = f"🗿 {_normalize_mention_text(fns.inline_mention(participants[0].user), hidden=True)}"
    elif custom_msg:
        tag_display = f"📝 {custom_msg}"
    elif mode == "ephemeral":
        tag_display = "🔒 Ephemeral tag session"
    else:
        tag_display = f"🗿 Tagging {filter_type} ({len(participants)} users)"

    # Create session
    session_id = _generate_session_id()
    await _set_session(
        session_id,
        {
            "filter": filter_type,
            "mode": mode,
            "admins": admins_only,
            "hidden": hidden,
            "custom_msg": custom_msg,
            "total": len(participants),
            "cancelled": False,
            "started": time.time(),
        },
    )

    # Start tagging session
    await eor(
        e,
        fmtmsg(
            "Tag",
            {
                "Status": "✅ Session started",
                "ID": f"`{session_id}`",
                "Total": len(participants),
                "Mode": mode,
                "Filter": filter_type,
                "Hidden": "✅" if hidden else "❌",
                "Custom": custom_msg or "N/A",
            },
        ),
        parse_mode="v2",
        time=5,
    )

    # Run the session in background
    asyncio.create_task(
        _run_tag_session(
            session_id,
            e,
            participants,
            mode,
            "-e" if mode == "ephemeral" else "",
            custom_msg,
            hidden,
        )
    )


@ultroid_cmd(pattern=r"tagcancel(?:\s+(.*))?$")
async def tagcancel(e):
    """Cancel an active tagging session."""
    session_id = (
        (e.pattern_match.group(1) or "").strip() if e.pattern_match.group(1) else ""
    )

    if session_id:
        session = await _get_session(session_id)
        if session:
            session["cancelled"] = True
            return await eor(
                e,
                fmtmsg("Tag", {"Status": "🔴 Cancelled", "ID": session_id}),
                parse_mode="v2",
                time=5,
            )
        return await eor(
            e,
            fmtmsg("Tag", {"Error": "Session not found"}),
            parse_mode="v2",
            time=5,
        )

    # Cancel all active sessions
    cancelled = 0
    for sid, s in _SESSIONS.items():
        if not s.get("cancelled"):
            s["cancelled"] = True
            cancelled += 1

    if cancelled:
        return await eor(
            e,
            fmtmsg("Tag", {"Status": f"🔴 Cancelled {cancelled} session(s)"}),
            parse_mode="v2",
            time=5,
        )

    return await eor(
        e,
        fmtmsg("Tag", {"Info": "No active sessions"}),
        parse_mode="v2",
        time=5,
    )


@ultroid_cmd(pattern="tagowner$", groups_only=True)
async def tagowner(e):
    """Tag the owner of the chat."""
    try:
        participants = await ultroid_bot.get_participants(e.chat_id, limit=200)
        for p in participants:
            if isinstance(p.participant, ChannelParticipantCreator):
                mention = fns.inline_mention(p.user)
                if hasattr(p, "status") and isinstance(p.status, UserStatusOnline):
                    await _send_regular_tag(ultroid_bot, e.chat_id, f"👑 {mention}")
                else:
                    await _send_regular_tag(ultroid_bot, e.chat_id, f"👑 {mention}")
                return await eor(
                    e,
                    fmtmsg("Tag", {"Status": "Tagged owner", "Owner": mention}),
                    parse_mode="v2",
                    time=5,
                )
    except Exception as er:
        return await eor(
            e,
            fmtmsg("Tag", {"Error": str(er)}),
            parse_mode="v2",
            time=5,
        )

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from pyUltroid.dB import db as dbs
import asyncio
import re

from telethon import events

from custom.tg import ikm
from pyUltroid._core.events import render_farewell, render_welcome
from pyUltroid.dB import stickers
from pyUltroid.fns import fns
from pyUltroid.fns import fns

from . import LOGS, asst, callback, udB, ultroid_bot, ultroid_cmd
from ._inline import something

# ---------------------------------------------------------------------------
# Relocated / removed code (Phase 2 — plugin restructure, Task 2.2)
# ---------------------------------------------------------------------------
# The following handlers lived here previously and have been **moved to
# ``chatbot.py``** (absorbed by its unified auto-reply engine — Task 2.3):
#   • CHATBOT: the ``chatBot_replies`` handler was moved to chatbot.py
#   • CHATBOT: the ``oracleBot_replies`` handler was moved to chatbot.py
#   • CHATBOT: the ``uname_stuff`` / ``uname_change`` (UpdateUserName) username
#     logging handlers were moved to chatbot.py
# The ``CHATBOT_USERS`` / ``ORACLE_USERS`` udB initialisation blocks were
# removed together with them — that state is now owned by ``chatbot.py``.
#
# Removed ban / moderation code (no longer present in this file):
#   • gban check block (``ULTROID_BANS`` + ``is_gbanned``)
#   • force-subscribe mute/ban block (``GetParticipantRequest``)
#   • ``ProfanityDetector`` + ``nsfw_db`` profanity delete block
# and with them the ``nsfw_db`` / ``gban_mute_db`` / ``forcesub_db`` imports.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Per-group event-notification settings (``.actions`` menu)
# ---------------------------------------------------------------------------
# Event types exposed in the ``.actions`` menu:
#   • welcome — fires on joins/adds, renders the per-chat welcome note
#   • goodbye — fires on voluntary leaves, renders the per-chat goodbye note
#   • kick    — NEW: fires when a member is kicked (see ``_notify_kick``)
#   • leave   — lightweight generic "X left" notice for voluntary leaves
# Defaults preserve the legacy behaviour: welcome/goodbye were always on
# (they fired whenever a note was set), while the new kick/leave notices are
# opt-in — so with defaults the listener behaves exactly as before.
ACTION_EVENTS = ("welcome", "goodbye", "kick", "leave")
ACTION_DEFAULTS = {"welcome": True, "goodbye": True, "kick": False, "leave": False}
_ACTION_LABELS = {
    "welcome": "Welcome",
    "goodbye": "Goodbye",
    "kick": "Kick",
    "leave": "Leave",
}


def _actions_key(chat_id):
    return f"CHAT_ACTIONS_{chat_id}"


def _get_actions(chat_id):
    data = udB.get_key(_actions_key(chat_id))
    if not isinstance(data, dict):
        return dict(ACTION_DEFAULTS)
    # backfill any missing keys against the defaults
    return {
        event: bool(data.get(event, ACTION_DEFAULTS[event])) for event in ACTION_EVENTS
    }


def _set_actions(chat_id, settings):
    udB.set_key(
        _actions_key(chat_id), {event: bool(val) for event, val in settings.items()}
    )


def _action_enabled(chat_id, event_name):
    return _get_actions(chat_id).get(event_name, ACTION_DEFAULTS[event_name])


def _actions_menu_text(chat_id):
    settings = _get_actions(chat_id)
    lines = ["**Chat Action Settings**", ""]
    for event in ACTION_EVENTS:
        status = "ON" if settings.get(event) else "OFF"
        lines.append(f"• **{_ACTION_LABELS[event]}**: `{status}`")
    lines += ["", "Tap a button to toggle, or `.actions <event> on|off`."]
    return "\n".join(lines)


def _actions_buttons(chat_id):
    settings = _get_actions(chat_id)
    rows = []
    for event in ACTION_EVENTS:
        status = "ON" if settings.get(event) else "OFF"
        rows.append([(f"{_ACTION_LABELS[event]}: {status}", f"ca_{event}_{chat_id}")])
    rows.append([("⚙ Disable All", f"ca_dis_{chat_id}", "danger")])
    return ikm(rows)


async def _send_actions_menu(event, chat_id):
    """Deliver the actions menu: bot-first → inline fallback → text fallback."""
    text = _actions_menu_text(chat_id)
    buttons = _actions_buttons(chat_id)
    # 1) bot-first — the assistant bot can post inline buttons directly
    if asst is not None:
        try:
            await asst.send_message(chat_id, text, buttons=buttons)
            try:
                await event.delete()
            except Exception:
                pass
            return
        except Exception as er:
            LOGS.debug(f"_chatactions: bot-first menu delivery failed: {er}")
    # 2) inline fallback — userbot inline-queries the assistant and clicks
    try:
        await something(event, text, None, buttons)
        return
    except Exception as er:
        LOGS.debug(f"_chatactions: inline menu delivery failed: {er}")
    # 3) text fallback — no buttons, point at the text commands
    await event.eor(
        text + "\n\n_⚠️ Interactive buttons unavailable here. "
        "Use `.actions <event> on|off` to toggle._"
    )


# ---------------------------------------------------------------------------
# Chat-action listener (joins / leaves / kicks)
# ---------------------------------------------------------------------------


@ultroid_bot.on(events.ChatAction())
async def Function(event):
    try:
        await DummyHandler(event)
    except Exception as er:
        LOGS.exception(er)


async def DummyHandler(ult):
    # clean chat actions
    key = udB.get_key("CLEANCHAT") or []
    if ult.chat_id in key:
        try:
            await ult.delete()
        except Exception:
            pass

    # thank members
    if dbs.greetings.must_thank(ult.chat_id):
        chat_count = (await ult.client.get_participants(ult.chat_id, limit=0)).total
        if chat_count % 100 == 0:
            stik_id = chat_count / 100 - 1
            sticker = stickers[stik_id]
            await ult.respond(file=sticker)

    # welcome on join / add
    if (ult.user_joined or ult.added_by) and _action_enabled(ult.chat_id, "welcome"):
        await _deliver_welcome(ult)

    # kicked — kick notification takes precedence over the goodbye farewell
    elif ult.user_kicked:
        if _action_enabled(ult.chat_id, "kick"):
            await _notify_kick(ult)
        elif _action_enabled(ult.chat_id, "goodbye") and dbs.greetings.get_goodbye(ult.chat_id):
            await _deliver_farewell(ult)

    # voluntary leave — goodbye farewell takes precedence over the leave notice
    elif ult.user_left:
        if _action_enabled(ult.chat_id, "goodbye") and dbs.greetings.get_goodbye(ult.chat_id):
            await _deliver_farewell(ult)
        elif _action_enabled(ult.chat_id, "leave"):
            await _notify_leave(ult)


async def _deliver_welcome(ult):
    wel = dbs.greetings.get_welcome(ult.chat_id)
    if not wel:
        return
    user = await ult.get_user()
    chat = await ult.get_chat()
    count = (
        chat.participants_count
        or (await ult.client.get_participants(chat, limit=0)).total
    )
    msgg = wel["welcome"]
    med = wel["media"] or None
    msg = None
    if msgg:
        # W2-D (B1): single rendering engine — pyUltroid._core.events
        # supports the full legacy placeholder set.
        msg = render_welcome(msgg, user, chat, count)
    if wel.get("button"):
        btn = fns.create_tl_btn(wel["button"])
        await something(ult, msg, med, btn)
    elif msg:
        send = await ult.reply(msg, file=med)
        await asyncio.sleep(150)
        await send.delete()
    else:
        await ult.reply(file=med)


async def _deliver_farewell(ult):
    wel = dbs.greetings.get_goodbye(ult.chat_id)
    if not wel:
        return
    user = await ult.get_user()
    chat = await ult.get_chat()
    count = (
        chat.participants_count
        or (await ult.client.get_participants(chat, limit=0)).total
    )
    msgg = wel["goodbye"]
    med = wel["media"]
    msg = None
    if msgg:
        # W2-D (B1): single rendering engine — pyUltroid._core.events
        # supports the full legacy placeholder set.
        msg = render_farewell(msgg, user, chat, count)
    if wel.get("button"):
        btn = fns.create_tl_btn(wel["button"])
        await something(ult, msg, med, btn)
    elif msg:
        send = await ult.reply(msg, file=med)
        await asyncio.sleep(150)
        await send.delete()
    else:
        await ult.reply(file=med)


async def _notify_kick(ult):
    """Send a notification when a member is kicked from the chat."""
    try:
        user = await ult.get_user()
        chat = await ult.get_chat()
        title = getattr(chat, "title", None) or "the group"
        mention = fns.inline_mention(user)
        kicker = None
        try:
            kicker = await ult.get_kicked_by()
        except Exception:
            kicker = ult.kicked_by
        if kicker is not None:
            text = f"👢 {fns.inline_mention(kicker)} kicked {mention} from **{title}**."
        else:
            text = f"👢 {mention} was kicked from **{title}**."
        await ult.reply(text)
    except Exception as er:
        LOGS.exception(er)


async def _notify_leave(ult):
    """Send a lightweight notice when a member leaves the chat voluntarily."""
    try:
        user = await ult.get_user()
        chat = await ult.get_chat()
        title = getattr(chat, "title", None) or "the group"
        await ult.reply(f"👋 {fns.inline_mention(user)} left **{title}**.")
    except Exception as er:
        LOGS.exception(er)


# ---------------------------------------------------------------------------
# .actions command + callback menu
# ---------------------------------------------------------------------------


@ultroid_cmd(pattern="actions(?: |$)", groups_only=True)
async def actions_cmd(event):
    """Manage per-group event notifications (welcome/goodbye/kick/leave)."""
    chat_id = event.chat_id
    parts = event.text.split(maxsplit=2)
    # parts[0] == ".actions"

    # .actions → interactive menu
    if len(parts) == 1:
        return await _send_actions_menu(event, chat_id)

    sub = parts[1].lower()

    # .actions disable → turn everything off at once
    if sub == "disable":
        _set_actions(chat_id, {event: False for event in ACTION_EVENTS})
        return await event.eor(
            "**All chat action notifications disabled for this chat.**", time=5
        )

    # .actions <event> on|off → toggle an individual event
    if sub in ACTION_EVENTS:
        if len(parts) == 3 and parts[2].lower() in ("on", "off"):
            value = parts[2].lower() == "on"
            settings = _get_actions(chat_id)
            settings[sub] = value
            _set_actions(chat_id, settings)
            return await event.eor(
                f"**{_ACTION_LABELS[sub]}** notifications turned "
                f"**{'ON' if value else 'OFF'}**.",
                time=5,
            )
        return await event.eor(f"Usage: `.actions {sub} on|off`", time=8)

    await event.eor(
        "Usage:\n• `.actions` — open the settings menu\n"
        "• `.actions disable` — disable all notifications\n"
        "• `.actions <event> on|off` — toggle (welcome/goodbye/kick/leave)",
        time=10,
    )


@callback(
    data=re.compile(rb"^ca_(dis|welcome|goodbye|kick|leave)_(-?\d+)$"), owner=True
)
async def actions_callback(event):
    """Handle taps on the ``.actions`` menu buttons — toggle and refresh."""
    op = event.data_match.group(1).decode("utf-8")
    chat_id = int(event.data_match.group(2).decode("utf-8"))

    settings = _get_actions(chat_id)
    if op == "dis":
        settings = {event_name: False for event_name in ACTION_EVENTS}
    elif op in ACTION_EVENTS:
        settings[op] = not settings.get(op, False)
    else:
        return  # unknown op — ignore silently

    _set_actions(chat_id, settings)
    await event.edit(_actions_menu_text(chat_id), buttons=_actions_buttons(chat_id))

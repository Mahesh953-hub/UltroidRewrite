# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

import os
from datetime import datetime as _dt

from pytz import timezone as tz
from telethon import Button, events
from telethon.errors.rpcerrorlist import MessageDeleteForbiddenError
from telethon.tl import functions, types as tl_types
from telethon.utils import get_display_name

from pyUltroid._misc import SUDO_M, owner_and_sudos
from pyUltroid.dB.base import KeyManager
from strings import get_string
from custom.tg import ikm
from custom.fmt import fmtmsg, rawhtml

from . import *
from ._cbs import cb, cb_exact_pattern
from pyUltroid.fns import fns

SETTER = cb("assistant", "start", "setter")
MAIN_MENU = cb("assistant", "start", "mainmenu")


def _cbstuffs_menu(key):
    return cb("assistant", "callbacks", "menu", key)


_MDV3_ESCAPE_CHARS = "_*[]()~`>-|"


def _mdv3_escape(text):
    """Backslash-escape MarkdownV3 entity delimiters in *text* (owner names)."""
    return "".join("\\" + ch if ch in _MDV3_ESCAPE_CHARS else ch for ch in str(text))


def _rich_menu(head, body):
    try:
        from pyUltroid._core.entities import RICH_MD

        rich = bool(getattr(RICH_MD, "available", False))
    except Exception:
        rich = False
        RICH_MD = None
    if rich:
        return (
            f"**{_mdv3_escape(head)}**\n\n**> {_mdv3_escape(body)}",
            RICH_MD,
        )
    return fmtmsg(head, msgs=body), "v2"


def _settings_configured(key):
    if key == "apiset":
        return any(
            udB.get_key(k) for k in ("RMBG_API", "DEEP_AI", "OCR_API", "SERPAPI")
        )
    if key == "chatbot":
        return bool(udB.get_key("PMBOT"))
    if key == "alive_customisation":
        return bool(udB.get_key("ALIVE_TEXT") or udB.get_key("ALIVE_PIC"))
    if key == "pm_settings":
        return bool(udB.get_key("PMSETTING"))
    if key == "othervars":
        return any(
            udB.get_key(k)
            for k in (
                "TAG_LOG",
                "SUDO",
                "EMOJI_IN_HELP",
                "DUAL_HNDLR",
                "ADDONS",
                "INLINE_PIC",
                "PLUGIN_CHANNEL",
                "SUDO_HNDLR",
            )
        )
    if key == "vc_menu":
        return bool(udB.get_key("VC_SESSION"))
    return False


Owner_info_msg = udB.get_key("BOT_INFO_START")
custom_info = True
if Owner_info_msg is None:
    custom_info = False
    Owner_info_msg = f"""
**Owner** - {OWNER_NAME}
**OwnerID** - `{OWNER_ID}`

**Message Forwards** - {udB.get_key("PMBOT")}

**Ultroid [v{ultroid_version}](https://github.com/TeamUltroid/Ultroid), powered by @TeamUltroid**
"""


def _settings_keyboard():
    return ikm(
        [
            [
                (
                    "API Kᴇʏs",
                    _cbstuffs_menu("apiset"),
                    "success" if _settings_configured("apiset") else "primary",
                ),
                (
                    "Pᴍ Bᴏᴛ",
                    _cbstuffs_menu("chatbot"),
                    "success" if _settings_configured("chatbot") else "primary",
                ),
            ],
            [
                (
                    "Aʟɪᴠᴇ",
                    _cbstuffs_menu("alive_customisation"),
                    (
                        "success"
                        if _settings_configured("alive_customisation")
                        else "primary"
                    ),
                ),
                (
                    "PᴍPᴇʀᴍɪᴛ",
                    _cbstuffs_menu("pm_settings"),
                    "success" if _settings_configured("pm_settings") else "primary",
                ),
            ],
            [
                (
                    "Fᴇᴀᴛᴜʀᴇs",
                    _cbstuffs_menu("othervars"),
                    "success" if _settings_configured("othervars") else "primary",
                ),
                (
                    "VC Sᴏɴɢ Bᴏᴛ",
                    _cbstuffs_menu("vc_menu"),
                    "success" if _settings_configured("vc_menu") else "primary",
                ),
            ],
            [("« Bᴀᴄᴋ", MAIN_MENU, "danger")],
        ],
        icon=True,
    )


_start = ikm(
    [
        [
            ("Lᴀɴɢᴜᴀɢᴇ 🌐", cb("assistant", "lang", "menu"), "primary"),
            ("SᴇᴛᴛɪNGs ⚙️", SETTER, "primary"),
        ],
        [
            ("Sᴛᴀᴛs ✨", "states", "primary"),
            ("Bʀᴏᴀᴅᴄᴀsᴛ 📻", "bcast", "primary"),
        ],
        [("TɪᴍᴇZᴏɴᴇ 🌎", "tz", "primary")],
    ],
    icon=True,
)


async def _set_bot_profile():
    if not asst:
        return
    description = udB.get_key("BOT_INFO_START") or (
        "Manage your Ultroid userbot from chat, or open the Mini App "
        "settings dashboard below."
    )
    stripped = description.strip()
    about = stripped.splitlines()[0][:120] if stripped else "Ultroid Assistant"
    try:
        await asst(
            functions.bots.SetBotInfoRequest(
                lang_code="",
                about=about,
                description=description[:512],
            )
        )
        LOGS.info("assistant.start: bot profile description updated.")
    except Exception as exc:
        LOGS.warning(
            "assistant.start: SetBotInfoRequest failed (%s) -- skipping.",
            exc.__class__.__name__,
        )


async def _configure_miniapp():
    if not asst:
        return
    try:
        from pyUltroid.webserver.miniapp import (
            MINIAPP_URL_KEY,
            SUPERVISOR,
            MiniAppApplication,
            MiniAppStore,
        )
    except Exception:
        LOGS.exception("assistant.start: failed to import the Mini App module.")
        return

    base_url = (udB.get_key(MINIAPP_URL_KEY) or "").strip()
    if not base_url:
        LOGS.info(
            "assistant.start: %s not set -- Mini App settings dashboard disabled.",
            MINIAPP_URL_KEY,
        )
        return

    store = MiniAppStore(udB)
    try:
        token = store.ensure_token()
    except Exception:
        LOGS.exception("assistant.start: failed to prepare the Mini App token.")
        return

    host = os.environ.get("MINIAPP_BIND", "0.0.0.0")
    try:
        port = int(os.environ.get("MINIAPP_PORT", "7861"))
    except ValueError:
        port = 7861

    try:
        miniapp = MiniAppApplication(token=token, udb=store.db).build()
        server = SUPERVISOR.ensure(miniapp, host=host, port=port)
    except Exception:
        LOGS.exception("assistant.start: failed to start the Mini App server.")
        return
    if server is None:
        LOGS.warning(
            "assistant.start: Mini App port %s:%s unavailable -- dashboard may "
            "already be running elsewhere.",
            host,
            port,
        )
    else:
        LOGS.info(
            "assistant.start: Mini App settings dashboard listening on %s:%s.",
            host,
            port,
        )

    settings_url = f"{base_url.rstrip('/')}/settings?token={token}"

    try:
        owner_entity = await asst.get_input_entity(OWNER_ID)
    except Exception as exc:
        LOGS.warning(
            "assistant.start: could not resolve the owner's InputUser (%s) -- "
            "skipping the Mini App menu button. The owner likely needs to "
            "message this assistant bot at least once so it can resolve "
            "the entity.",
            exc.__class__.__name__,
        )
        return

    try:
        await asst(
            functions.bots.SetBotMenuButtonRequest(
                user_id=owner_entity,
                button=tl_types.BotMenuButton(text="Settings", url=settings_url),
            )
        )
        LOGS.info("assistant.start: Mini App menu button configured for the owner.")
    except Exception as exc:
        LOGS.warning(
            "assistant.start: SetBotMenuButtonRequest failed (%s) -- skipping.",
            exc.__class__.__name__,
        )


async def _configure_assistant_startup():
    try:
        await _set_bot_profile()
    except Exception:
        LOGS.exception("assistant.start: bot profile setup crashed.")
    try:
        await _configure_miniapp()
    except Exception:
        LOGS.exception("assistant.start: Mini App setup crashed.")


if asst:
    try:
        asst.loop.create_task(_configure_assistant_startup())
    except Exception:
        LOGS.exception(
            "assistant.start: could not schedule startup configuration task."
        )


@callback("ownerinfo")
async def own(event):
    msg = Owner_info_msg.format(
        mention=fns.inline_mention(event.sender),
        me=fns.inline_mention(ultroid_bot.me),
    )
    if custom_info:
        msg += "\n\n• Powered by **@TeamUltroid**"
    await event.edit(
        fmtmsg("Owner Information 👤", msgs=msg),
        buttons=ikm([("Close ❌", "closeit", "danger")], icon=True),
        link_preview=False,
        parse_mode="v2",
    )


@callback("closeit")
async def closet(lol):
    try:
        await lol.delete()
    except MessageDeleteForbiddenError:
        await lol.answer("MESSAGE_TOO_OLD", alert=True)


@asst_cmd(pattern="start( (.*)|$)", forwards=False, func=lambda x: not x.is_group)
async def ultroid(event):
    args = event.pattern_match.group(1).strip()
    keym = KeyManager("BOT_USERS", cast=list)
    if not keym.contains(event.sender_id) and event.sender_id not in owner_and_sudos():
        keym.add(event.sender_id)
        kak_uiw = udB.get_key("OFF_START_LOG")
        if not kak_uiw or kak_uiw != True:
            msg = f"{fns.inline_mention(event.sender)} `[{event.sender_id}]` started your [Assistant bot](@{asst.me.username})."
            btn_list = [("Info 👤", "info_stub", "primary")]
            if event.sender.username:
                btn_list.append(
                    ("User 🔗", "url", f"tg://user?id={event.sender_id}", "green")
                )
            await event.client.send_message(
                udB.get_key("LOG_CHANNEL"), msg, buttons=ikm([btn_list], icon=True)
            )
    if event.sender_id not in SUDO_M.fullsudos:
        ok = ""
        me = fns.inline_mention(ultroid_bot.me)
        mention = fns.inline_mention(event.sender)
        if args and args != "set":
            await fns.get_stored_file(event, args)
        if not udB.get_key("STARTMSG"):
            if udB.get_key("PMBOT"):
                ok = "You can contact my master using this bot!!\n\nSend your Message, I will Deliver it To Master."
            await event.reply(
                fmtmsg(
                    "✨ Ultroid Assistant ✨",
                    msgs=f"Hey there {mention}, this is Ultroid Assistant of {me}!\n\n{ok}",
                ),
                file=udB.get_key("STARTMEDIA"),
                buttons=(
                    ikm([("Info. 👤", "ownerinfo", "primary")], icon=True)
                    if Owner_info_msg
                    else None
                ),
                parse_mode="v2",
            )
        else:
            custom_msg = udB.get_key("STARTMSG").format(me=me, mention=mention)
            await event.reply(
                fmtmsg("✨ Ultroid Assistant ✨", msgs=custom_msg),
                file=udB.get_key("STARTMEDIA"),
                buttons=(
                    ikm([("Info. 👤", "ownerinfo", "primary")], icon=True)
                    if Owner_info_msg
                    else None
                ),
                parse_mode="v2",
            )
    else:
        name = get_display_name(event.sender)
        if args == "set":
            _text, _pm = _rich_menu("Settings ⚙️", "Choose from the below options:")
            await event.reply(
                _text,
                buttons=_settings_keyboard(),
                parse_mode=_pm,
            )
        elif args:
            await fns.get_stored_file(event, args)
        else:
            _text, _pm = _rich_menu(
                f"Welcome, {name}! ✨", get_string("ast_3").format(name)
            )
            await event.reply(
                _text,
                buttons=_start,
                parse_mode=_pm,
            )


@callback("info_stub", owner=True)
async def ekekdhdb(e):
    text = f"When New Visitor will visit your Assistant Bot. You will get this log message!\n\nTo Disable : {HNDLR}setdb OFF_START_LOG True"
    await e.answer(text, alert=True)


@callback(
    cb_exact_pattern("assistant", "start", "mainmenu"),
    owner=True,
    func=lambda x: not x.is_group,
)
async def ultroid_menu(event):
    _text, _pm = _rich_menu(
        f"Welcome back, {OWNER_NAME}! ✨", get_string("ast_3").format(OWNER_NAME)
    )
    await event.edit(
        _text,
        buttons=_start,
        parse_mode=_pm,
    )


@callback("states", owner=True)
async def botstat(event):
    ok = len(udB.get_key("BOT_USERS") or [])
    msg = """Ultroid Assistant - Stats
Total Users - {}""".format(
        ok,
    )
    await event.edit(
        fmtmsg("Stats 📊", data={"Total Users": ok}),
        buttons=ikm([("« Bᴀᴄᴋ", MAIN_MENU, "danger")], icon=True),
        parse_mode="v2",
    )


@callback("bcast", owner=True)
async def bdcast(event):
    keym = KeyManager("BOT_USERS", cast=list)
    total = keym.count()
    await event.edit(
        fmtmsg("Broadcast 📻", msgs=f"• Initiating broadcast to {total} users."),
        parse_mode="v2",
    )
    async with event.client.conversation(OWNER_ID) as conv:
        await conv.send_message(
            fmtmsg(
                "Broadcast 📻",
                msgs="Enter your broadcast message.\nUse /cancel to stop the broadcast.",
            ),
            parse_mode="v2",
        )
        response = await conv.get_response()
        if response.message == "/cancel":
            return await conv.send_message(
                fmtmsg("Cancelled ❌", msgs="Nothing was changed."),
                parse_mode="v2",
            )
        success = 0
        fail = 0
        await conv.send_message(
            fmtmsg("Broadcast 📻", msgs=f"Starting a broadcast to {total} users..."),
            parse_mode="v2",
        )
        start = _dt.now()
        for i in keym.get():
            try:
                await asst.send_message(int(i), response)
                success += 1
            except BaseException:
                fail += 1
        end = _dt.now()
        time_taken = (end - start).seconds
        await conv.send_message(
            fmtmsg(
                "Broadcast Completed 📻",
                {
                    "Time Taken": f"{time_taken} seconds",
                    "Total Users": str(total),
                    "Successful": f"{success} users",
                    "Failed": f"{fail} users",
                },
            ),
            parse_mode="v2",
        )


@callback(cb_exact_pattern("assistant", "start", "setter"), owner=True)
async def setting(event):
    _text, _pm = _rich_menu("Settings ⚙️", "Choose from the below options:")
    await event.edit(
        _text,
        buttons=_settings_keyboard(),
        parse_mode=_pm,
    )


@callback("tz", owner=True)
async def timezone_(event):
    await event.delete()
    pru = event.sender_id
    var = "TIMEZONE"
    name = "Timezone"
    async with event.client.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs="Send your timezone from the list below.",
                data={
                    "Check From Here": rawhtml(
                        '<a href="http://www.timezoneconverter.com/cgi-bin/findzone.tzc">timezoneconverter.com</a>'
                    )
                },
            ),
            parse_mode="v2",
        )
        response = conv.wait_event(events.NewMessage(chats=pru))
        response = await response
        themssg = response.message.message
        if themssg == "/cancel":
            return await conv.send_message(
                fmtmsg("Cancelled ❌", msgs="Nothing was changed."),
                buttons=ikm([("« Bᴀᴄᴋ", MAIN_MENU, "danger")], icon=True),
                parse_mode="v2",
            )
        try:
            tz(themssg)
            await setit(event, var, themssg)
            await conv.send_message(
                fmtmsg("Timezone Changed ✅", msgs=f"{name} changed to {themssg}"),
                buttons=ikm([("« Bᴀᴄᴋ", MAIN_MENU, "danger")], icon=True),
                parse_mode="v2",
            )
        except BaseException:
            await conv.send_message(
                fmtmsg("Error ❌", msgs="Wrong TimeZone, Try again"),
                buttons=ikm([("« Bᴀᴄᴋ", MAIN_MENU, "danger")], icon=True),
                parse_mode="v2",
            )

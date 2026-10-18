# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

import html
import os
from collections import OrderedDict
from datetime import datetime as _dt

from pytz import UnknownTimeZoneError
from pytz import timezone as tz
from telethon import events
from telethon.errors.rpcerrorlist import MessageDeleteForbiddenError
from telethon.tl import functions
from telethon.tl import types as tl_types
from telethon.utils import get_display_name, resolve_bot_file_id

from custom.tg import ikm
from pyUltroid import HNDLR, asst, udB, ultroid_bot
from pyUltroid._core.richmsg import coerce_to_rich_markup
from pyUltroid._core.richmsg import send_rich
from pyUltroid._misc import SUDO_M, owner_and_sudos
from pyUltroid._misc._assistant import asst_cmd, callback
from pyUltroid.dB.base import KeyManager
from pyUltroid.fns import fns
from pyUltroid.startup.logger import LOGS
from pyUltroid.version import ultroid_version
from strings import get_string

from . import OWNER_ID, OWNER_NAME, setit
from ._cbs import cb, cb_exact_pattern
from .rich_pages import body_page, edit_callback_page, rich_page

SETTER = cb("assistant", "start", "setter")
MAIN_MENU = cb("assistant", "start", "mainmenu")
_START_EVENTS = OrderedDict()
_START_EVENT_LIMIT = 512


def _claim_start_event(event):
    """Suppress a repeated delivery of the same Telegram /start update."""
    message = getattr(event, "message", None)
    message_id = getattr(message, "id", None)
    if message_id is None:
        return True
    key = (
        getattr(event, "chat_id", None),
        getattr(event, "sender_id", None),
        message_id,
    )
    if key in _START_EVENTS:
        return False
    _START_EVENTS[key] = None
    while len(_START_EVENTS) > _START_EVENT_LIMIT:
        _START_EVENTS.popitem(last=False)
    return True


def _valid_timezone(value):
    """Return a trimmed IANA timezone name, or ``None`` for invalid input."""
    name = str(value or "").strip()
    if not name:
        return None
    try:
        tz(name)
    except UnknownTimeZoneError:
        return None
    return name


def _cbstuffs_menu(key):
    return cb("assistant", "callbacks", "menu", key)


def _html_body(body):
    markup = coerce_to_rich_markup(str(body), parse_mode="mdv3")
    return markup.text if markup else html.escape(str(body))


def _rich_menu(head, body, *, media=None):
    return body_page(head, _html_body(body), media=media)


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


def _settings_sections():
    return (
        ("API Keys", "Configure image, AI, OCR, and search service credentials."),
        ("Chat Bot", "Toggle the bot and edit its welcome, info, media, and subscription settings."),
        ("Alive", "Customize the assistant's alive message and media."),
        ("PM Permit", "Control the private-message guard and its options."),
        ("Features", "Configure logging, sudo, handlers, plugins, and helper options."),
        ("VC Song Bot", "Set the voice-chat bot session."),
    )


def _settings_page():
    return rich_page(
        "Assistant Settings",
        _settings_sections(),
        description="Choose a section below. Credentials and session values stay hidden.",
        columns=("Section", "What it does"),
        total=6,
    )


def _owner_home_page(name):
    actions = (
        ("Language", "Choose the assistant's display language."),
        ("Settings", "Configure assistant features and services."),
        ("Stats", "View the number of registered assistant users."),
        ("Broadcast", "Send a message to registered assistant users."),
        ("Timezone", "Set the timezone used by the assistant."),
    )
    return rich_page(
        f"Welcome, {name}",
        actions,
        description=_plain_text(get_string("ast_3").format(name)),
        columns=("Action", "What it does"),
        total=len(actions),
    )


def _plain_text(value):
    """Remove simple Markdown markers before inserting a plain table cell."""
    text = str(value)
    for marker in ("**", "__", "~~", "`", "*"):
        text = text.replace(marker, "")
    return text


def _start_media_reference():
    """Resolve the saved Telegram file ID to an already-uploaded RichFile source."""
    file_id = udB.get_key("STARTMEDIA")
    if not file_id:
        return None
    try:
        media = resolve_bot_file_id(file_id)
    except (TypeError, ValueError):
        return None
    if isinstance(media, tl_types.Photo):
        return ("photo", media, None)
    if isinstance(media, tl_types.Document):
        return ("document", media, None)
    return None


def _default_owner_info():
    return f"""
**Owner** - {OWNER_NAME}
**OwnerID** - {OWNER_ID}

**Message Forwards** - {udB.get_key("PMBOT")}

**Ultroid [v{ultroid_version}](https://github.com/TeamUltroid/Ultroid), powered by @TeamUltroid**
"""


def _owner_info_state():
    """Return (text, custom, visible), reading the setting at use time."""
    value = udB.get_key("BOT_INFO_START")
    if value is None:
        return _default_owner_info(), False, True
    if value is False or (isinstance(value, str) and value.strip().lower() == "false"):
        return "", True, False
    text = str(value)
    return text, True, bool(text.strip())


class _SafeFormat(dict):
    def __missing__(self, key):
        return "{" + str(key) + "}"


def _safe_format(text, **values):
    try:
        return str(text).format_map(_SafeFormat(values))
    except (TypeError, ValueError):
        return str(text)


# Keep historical module-level names, while callbacks read the current setting.
Owner_info_msg, custom_info, _owner_info_visible = _owner_info_state()


def _settings_keyboard():
    return ikm(
        [
            [
                (
                    "API Keys",
                    _cbstuffs_menu("apiset"),
                    "success" if _settings_configured("apiset") else None,
                ),
                (
                    "Chat Bot",
                    _cbstuffs_menu("chatbot"),
                    "success" if _settings_configured("chatbot") else None,
                ),
            ],
            [
                (
                    "Alive",
                    _cbstuffs_menu("alive_customisation"),
                    (
                        "success"
                        if _settings_configured("alive_customisation")
                        else None
                    ),
                ),
                (
                    "PM Permit",
                    _cbstuffs_menu("pm_settings"),
                    "success" if _settings_configured("pm_settings") else None,
                ),
            ],
            [
                (
                    "Features",
                    _cbstuffs_menu("othervars"),
                    "success" if _settings_configured("othervars") else None,
                ),
                (
                    "VC Song Bot",
                    _cbstuffs_menu("vc_menu"),
                    "success" if _settings_configured("vc_menu") else None,
                ),
            ],
            [("Back", MAIN_MENU, "primary")],
        ],
        icon=False,
    )


_start = ikm(
    [
        [
            ("Language", cb("assistant", "lang", "menu"), "primary"),
            ("Settings", SETTER, "primary"),
        ],
        [
            ("Stats", "states", "primary"),
            ("Broadcast", "bcast", "primary"),
        ],
        [("Timezone", "tz", "primary")],
    ],
    icon=False,
)


async def _set_bot_profile():
    if not asst:
        return
    description, _custom, visible = _owner_info_state()
    if not visible:
        description = ""
    description = description or (
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
    info_text, is_custom, visible = _owner_info_state()
    if not visible:
        return await event.answer("The bot info message has been disabled.", alert=True)
    msg = _safe_format(
        info_text,
        mention=fns.inline_mention(event.sender),
        me=fns.inline_mention(ultroid_bot.me),
    )
    if is_custom:
        msg += "\n\nPowered by [@TeamUltroid](https://t.me/TeamUltroid)"
    await edit_callback_page(
        event,
        _rich_menu("Owner Information", msg),
        buttons=ikm([("Close", "closeit", None)], icon=False),
    )


@callback("closeit")
async def closet(lol):
    try:
        await lol.delete()
    except MessageDeleteForbiddenError:
        await lol.answer("MESSAGE_TOO_OLD", alert=True)


@asst_cmd(pattern="start( (.*)|$)", forwards=False, func=lambda x: not x.is_group)
async def ultroid(event):
    if not _claim_start_event(event):
        return
    args = event.pattern_match.group(1).strip()
    keym = KeyManager("BOT_USERS", cast=list)
    if not keym.contains(event.sender_id) and event.sender_id not in owner_and_sudos():
        keym.add(event.sender_id)
        kak_uiw = udB.get_key("OFF_START_LOG")
        if kak_uiw != True:  # noqa: E712 - preserve legacy bool-like config semantics
            msg = f"{fns.inline_mention(event.sender)} [{event.sender_id}] started your [Assistant bot](@{asst.me.username})."
            btn_list = [("Info", "info_stub", "primary")]
            if event.sender.username:
                btn_list.append(("User", "url", f"tg://user?id={event.sender_id}"))
            log_channel = udB.get_key("LOG_CHANNEL")
            if log_channel:
                try:
                    await event.client.send_message(
                        log_channel, msg, buttons=ikm([btn_list], icon=False)
                    )
                except Exception as exc:
                    LOGS.warning(
                        "assistant.start: visitor log could not be sent (%s).",
                        exc.__class__.__name__,
                    )
    if event.sender_id not in SUDO_M.fullsudos:
        ok = ""
        me = fns.inline_mention(ultroid_bot.me)
        mention = fns.inline_mention(event.sender)
        _info, _custom, show_info = _owner_info_state()
        info_button = (
            ikm([("Info", "ownerinfo", "primary")], icon=False) if show_info else None
        )
        if args and args != "set":
            stored = await fns.get_stored_file(event, args)
            if stored is not None:
                return stored
        if not udB.get_key("STARTMSG"):
            if udB.get_key("PMBOT"):
                ok = "You can contact my master using this bot!!\n\nSend your Message, I will Deliver it To Master."
            content = _rich_menu(
                "Ultroid Assistant",
                f"Hey there {mention}, this is Ultroid Assistant of {me}!\n\n{ok}",
                media=_start_media_reference(),
            )
            await send_rich(
                event.client,
                event.chat_id,
                content,
                reply_to=event,
                buttons=info_button,
                link_preview=False,
            )
        else:
            custom_msg = _safe_format(udB.get_key("STARTMSG"), me=me, mention=mention)
            content = _rich_menu(
                "Ultroid Assistant", custom_msg, media=_start_media_reference()
            )
            await send_rich(
                event.client,
                event.chat_id,
                content,
                reply_to=event,
                buttons=info_button,
                link_preview=False,
            )
    else:
        name = get_display_name(event.sender)
        if args == "set":
            await send_rich(
                event.client,
                event.chat_id,
                _settings_page(),
                reply_to=event,
                buttons=_settings_keyboard(),
                link_preview=False,
            )
        elif args:
            stored = await fns.get_stored_file(event, args)
            if stored is not None:
                return stored
            # Unrecognized start payloads (including the public ``start``
            # help deep link) should land on the normal owner welcome menu.
            await send_rich(
                event.client,
                event.chat_id,
                _owner_home_page(name),
                reply_to=event,
                buttons=_start,
                link_preview=False,
            )
        else:
            await send_rich(
                event.client,
                event.chat_id,
                _owner_home_page(name),
                reply_to=event,
                buttons=_start,
                link_preview=False,
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
    await edit_callback_page(
        event,
        _owner_home_page(OWNER_NAME),
        buttons=_start,
    )


@callback("states", owner=True)
async def botstat(event):
    ok = len(udB.get_key("BOT_USERS") or [])
    await edit_callback_page(
        event,
        rich_page(
            "Assistant Stats",
            [("Registered users", str(ok))],
            columns=("Metric", "Total"),
            total=1,
        ),
        buttons=ikm([("Back", MAIN_MENU, None)], icon=False),
    )


@callback("bcast", owner=True)
async def bdcast(event):
    keym = KeyManager("BOT_USERS", cast=list)
    total = keym.count()
    await edit_callback_page(
        event,
        body_page("Broadcast", f"<p>Initiating broadcast to {total} users.</p>", total=1),
    )
    async with event.client.conversation(OWNER_ID) as conv:
        prompt = await send_rich(
            event.client,
            conv.chat_id,
            body_page(
                "Broadcast",
                "<p>Enter your broadcast message.</p><p>Use /cancel to stop.</p>",
            ),
            link_preview=False,
        )
        response = await conv.get_response(prompt)
        cancel_command = str(response.message or "").strip().split(maxsplit=1)[0]
        if cancel_command.split("@", 1)[0].casefold() == "/cancel":
            return await send_rich(
                event.client,
                conv.chat_id,
                body_page("Broadcast cancelled", "<p>Nothing was changed.</p>"),
                buttons=ikm([("Back", MAIN_MENU, "primary")], icon=False),
                link_preview=False,
            )
        success = 0
        fail = 0
        await send_rich(
            event.client,
            conv.chat_id,
            body_page("Broadcast", f"<p>Starting a broadcast to {total} users.</p>"),
            link_preview=False,
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
        await send_rich(
            event.client,
            conv.chat_id,
            rich_page(
                "Broadcast Complete",
                [
                    ("Time taken", f"{time_taken} seconds"),
                    ("Total users", str(total)),
                    ("Successful", f"{success} users"),
                    ("Failed", str(fail)),
                ],
                columns=("Result", "Count"),
                total=4,
            ),
            link_preview=False,
        )


@callback(cb_exact_pattern("assistant", "start", "setter"), owner=True)
async def setting(event):
    await edit_callback_page(
        event,
        _settings_page(),
        buttons=_settings_keyboard(),
    )


@callback("tz", owner=True)
async def timezone_(event):
    await event.delete()
    pru = event.sender_id
    var = "TIMEZONE"
    name = "Timezone"
    async with event.client.conversation(pru) as conv:
        await send_rich(
            event.client,
            conv.chat_id,
            body_page(
                name,
                '<p>Send an IANA timezone name. Examples: <code>Asia/Kolkata</code>, '
                '<code>Europe/London</code>, <code>America/New_York</code>, or '
                '<code>UTC</code>.</p><p><a href="https://www.timezoneconverter.com/cgi-bin/findzone.tzc">Browse timezone names</a>. Send <code>/cancel</code> to stop.</p>',
                total=4,
            ),
            link_preview=False,
        )
        while True:
            response = await conv.wait_event(events.NewMessage(chats=pru))
            themssg = str(response.message.message or "").strip()
            cancel_command = (
                themssg.split(maxsplit=1)[0].split("@", 1)[0] if themssg else ""
            )
            if cancel_command.casefold() == "/cancel":
                return await send_rich(
                    event.client,
                    conv.chat_id,
                    body_page(
                        "Timezone update cancelled", "<p>Nothing was changed.</p>"
                    ),
                    buttons=ikm([("Back", MAIN_MENU, None)], icon=False),
                    link_preview=False,
                )

            timezone_name = _valid_timezone(themssg)
            if timezone_name is None:
                await send_rich(
                    event.client,
                    conv.chat_id,
                    body_page(
                        "Timezone not recognized",
                        "<p>Use an IANA name such as <code>Asia/Kolkata</code>, "
                        "<code>Europe/London</code>, <code>America/New_York</code>, "
                        "or <code>UTC</code>.</p><p>Try another value, or send "
                        "<code>/cancel</code> to stop.</p>",
                        total=4,
                    ),
                    link_preview=False,
                )
                continue

            await setit(event, var, timezone_name)
            return await send_rich(
                event.client,
                conv.chat_id,
                body_page(
                    "Timezone updated",
                    f"<p>{html.escape(name)} changed to "
                    f"<code>{html.escape(timezone_name)}</code>.</p>",
                    total=1,
                ),
                buttons=ikm([("Back", MAIN_MENU, None)], icon=False),
                link_preview=False,
            )

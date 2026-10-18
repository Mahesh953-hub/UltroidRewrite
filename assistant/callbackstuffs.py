# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

import ast
import asyncio
import time
import unicodedata
from asyncio.exceptions import TimeoutError as AsyncTimeOut
from os import remove
from pathlib import Path
from random import choice

from bs4 import BeautifulSoup as bs

try:
    from pyUltroid.fns.gDrive import GDriveManager
except ImportError:
    GDriveManager = None
from telegraph import upload_file as upl
from telethon import events
from telethon.tl import types
from telethon.tl.types import MessageMediaWebPage
from telethon.utils import get_peer_id

from custom.fmt import fmtmsg, rawhtml
from pyUltroid._core.loader import AsyncPluginLoader
from pyUltroid._core.richmsg import send_rich
from pyUltroid.fns import fns
from pyUltroid.ultroid import core

from . import *
from ._cbs import cb, cb_exact_pattern, cb_pattern
from .rich_pages import edit_callback_page, rich_page
telegraph = fns.telegraph_client()

try:
    GDrive = GDriveManager() if GDriveManager else None
except Exception:
    GDrive = None


def _tok(*parts):
    return cb("assistant", "callbacks", *parts)


def _handler(*parts):
    return cb_exact_pattern("assistant", "callbacks", *parts)


def _menu(key):
    return cb("assistant", "callbacks", "menu", key)


def _conv(key):
    return cb("assistant", "callbacks", "conv", key)


def _message_text(message):
    if isinstance(message, str):
        return message
    value = getattr(message, "raw_text", None)
    if value is None:
        value = getattr(message, "message", None)
    if not isinstance(value, str):
        value = getattr(value, "message", None)
    return str(value or "")


def _is_cancel(message):
    text = _message_text(message).strip()
    command = text.split(maxsplit=1)[0].split("@", 1)[0] if text else ""
    return command.casefold() == "/cancel"


def _row(*btns):
    return ikm([list(btns)], icon=False)[0]


def _toggle(key):
    value = udB.get_key(key)
    if value is True or (isinstance(value, str) and value.lower() == "true"):
        return "On"
    return "Off"


def _val(key):
    value = udB.get_key(key)
    if value is None or value == "":
        return "Not set"
    return str(value)


def _mask_val(key):
    value = udB.get_key(key)
    if value is None or value == "":
        return "Not set"
    return "Set (hidden)"


def _plain(text):
    """Remove common Markdown delimiters before showing text in a plain field."""
    value = str(text)
    for delimiter in ("**", "__", "~~", "`"):
        value = value.replace(delimiter, "")
    return value


_BUTTON_VALUE_KEYS = {
    "taglogger": "TAG_LOG",
    "help_emoji": "EMOJI_IN_HELP",
    "plugins": "PLUGIN_CHANNEL",
    "handler": "HNDLR",
    "sudo_handler": "SUDO_HNDLR",
    "addons": "ADDONS",
    "edon": "ADDONS",
    "edof": "ADDONS",
    "sudo": "SUDO",
    "onsudo": "SUDO",
    "ofsudo": "SUDO",
    "fbangroup": "FBAN_GROUP_ID",
    "excludefeds": "EXCLUDE_FED",
    "alive_text": "ALIVE_TEXT",
    "alive_media": "ALIVE_PIC",
    "pm_txt": "PM_TEXT",
    "set_pm_warns": "PMWARNS",
    "pm_media": "PMPIC",
    "pm_logger": "PMLOG",
    "pm_logger_group": "PMLOGGROUP",
    "pm_inline": "INLINE_PM",
    "pm_normal": "INLINE_PM",
    "auto_ap_on": "AUTOAPPROVE",
    "auto_ap_off": "AUTOAPPROVE",
    "pm_permit_on": "PMSETTING",
    "pm_permit_off": "PMSETTING",
    "bot_welcome": "STARTMSG",
    "bot_welcome_media": "STARTMEDIA",
    "bot_info_text": "BOT_INFO_START",
    "bot_fsub": "PMBOT_FSUB",
    "chatbot_on": "PMBOT",
    "chatbot_off": "PMBOT",
    "vc_session": "VC_SESSION",
    "dual_mode": "DUAL_MODE",
    "dual_mode_on": "DUAL_MODE",
    "dual_mode_off": "DUAL_MODE",
    "dual_mode_handler": "DUAL_HNDLR",
    "rmbg_key_set": "RMBG_API",
    "deep_key_set": "DEEP_AI",
    "ocr_key_set": "OCR_API",
    "serp_key_set": "SERPAPI",
}
_BUTTON_GROUP_KEYS = {
    "othervars": (
        "TAG_LOG",
        "SUDO",
        "HNDLR",
        "PLUGIN_CHANNEL",
        "ADDONS",
        "EMOJI_IN_HELP",
        "INLINE_PIC",
        "SUDO_HNDLR",
        "DUAL_MODE",
    ),
    "superfban": ("FBAN_GROUP_ID", "EXCLUDE_FED"),
    "alive_customisation": ("ALIVE_TEXT", "ALIVE_PIC"),
    "pm_customisation": ("PM_TEXT", "PMPIC", "PMLOG", "PMWARNS", "INLINE_PM"),
    "pm_type": ("INLINE_PM",),
    "apauto": ("AUTOAPPROVE",),
    "pm_settings": ("PMSETTING",),
    "chatbot": ("PMBOT", "STARTMSG", "STARTMEDIA", "BOT_INFO_START", "PMBOT_FSUB"),
    "vc_menu": ("VC_SESSION",),
    "dual_mode": ("DUAL_MODE", "DUAL_HNDLR"),
    "apiset": ("RMBG_API", "DEEP_AI", "OCR_API", "SERPAPI"),
}
_TOGGLE_BUTTONS = {
    "edon": ("ADDONS", True),
    "edof": ("ADDONS", False),
    "onsudo": ("SUDO", True),
    "ofsudo": ("SUDO", False),
    "auto_ap_on": ("AUTOAPPROVE", True),
    "auto_ap_off": ("AUTOAPPROVE", False),
    "pm_permit_on": ("PMSETTING", True),
    "pm_permit_off": ("PMSETTING", False),
    "chatbot_on": ("PMBOT", True),
    "chatbot_off": ("PMBOT", False),
    "pm_inline": ("INLINE_PM", True),
    "pm_normal": ("INLINE_PM", False),
    "dual_mode_on": ("DUAL_MODE", True),
    "dual_mode_off": ("DUAL_MODE", False),
    "pm_logger_on": ("PMLOG", True),
    "pm_logger_off": ("PMLOG", False),
}
_DESTRUCTIVE_BUTTONS = {"deltag", "del_alive_media", "del_pm_media"}


def _has_saved_value(value):
    return value is not None and value != "" and value != []


def _enabled(value):
    return value is True or (
        isinstance(value, str) and value.strip().lower() in {"true", "on", "1", "yes"}
    )


def _button_action(data):
    if isinstance(data, bytes):
        data = data.decode("utf-8", "ignore")
    return str(data or "").rstrip(":").split(":")[-1].lower()


def _menu_label(text):
    out = []
    for char in str(text or ""):
        if ord(char) in {
            0x00AB,
            0x00BB,
            0x2039,
            0x203A,
            0x25C0,
            0x25B6,
            0x2190,
            0x2192,
            0x2022,
            0x00B7,
            0xFE0E,
            0xFE0F,
            0x200D,
            0x20E3,
        }:
            out.append(" ")
            continue
        try:
            name = unicodedata.name(char)
        except ValueError:
            name = ""
        if name.startswith("LATIN LETTER SMALL CAPITAL "):
            out.append(name.rsplit(" ", 1)[-1].lower())
        elif unicodedata.category(char) in {"So", "Sk"}:
            out.append(" ")
        else:
            out.append(char)
    return " ".join("".join(out).split())


def _menu_button_style(data, label=None):
    action = _button_action(data)
    if _menu_label(label).lower() == "back":
        return "primary"
    if action in _DESTRUCTIVE_BUTTONS:
        return "danger"
    if action in _TOGGLE_BUTTONS:
        key, desired = _TOGGLE_BUTTONS[action]
        value = udB.get_key(key)
        return (
            "success"
            if _has_saved_value(value) and _enabled(value) is desired
            else None
        )
    key = _BUTTON_VALUE_KEYS.get(action)
    if key and _has_saved_value(udB.get_key(key)):
        return "success"
    keys = _BUTTON_GROUP_KEYS.get(action, ())
    return "success" if any(_has_saved_value(udB.get_key(k)) for k in keys) else None


def _refresh_menu_buttons(buttons):
    rows = []
    for row in buttons or []:
        converted = []
        for btn in row:
            legacy_callback = getattr(types, "KeyboardButtonCallback", None)
            inline_button = getattr(types, "KeyboardInlineButton", None)
            callback_type = getattr(types, "InlineButtonTypeCallback", None)
            button_type = getattr(btn, "type", None)
            is_callback = legacy_callback is not None and isinstance(
                btn, legacy_callback
            )
            is_callback = is_callback or (
                inline_button is not None
                and callback_type is not None
                and isinstance(btn, inline_button)
                and isinstance(button_type, callback_type)
            )
            if is_callback:
                data = getattr(btn, "data", None) or getattr(button_type, "data", None)
                converted.append(
                    (
                        _menu_label(btn.text),
                        data,
                        _menu_button_style(data, btn.text),
                    )
                )
            else:
                converted.append(btn)
        rows.append(converted)
    return ikm(rows, icon=False)


START_SETTER = cb("assistant", "start", "setter")

MENU_PATTERN = cb_pattern("assistant", "callbacks", "menu")
CONV_PATTERN = cb_pattern("assistant", "callbacks", "conv")
CHANGES_PATTERN = cb_pattern("assistant", "callbacks", "changes")
WRNS_PATTERN = cb_pattern("assistant", "callbacks", "pm_warns")
PASTA_PATTERN = cb_pattern("assistant", "callbacks", "paste")
FDROID_PATTERN = cb_pattern("assistant", "fdroid")


def text_to_url(event):
    """function to get media url (with|without) Webpage"""
    if isinstance(event.media, MessageMediaWebPage):
        webpage = event.media.webpage
        if not isinstance(webpage, types.WebPageEmpty) and webpage.type in ["photo"]:
            return webpage.display_url
    return event.text


_buttons = {
    "othervars": {
        "head": "Other Variables ",
        "body": "Configure the remaining variables for your Ultroid assistant.",
        "buttons": ikm(
            [
                [
                    ("Tag Logger", _tok("taglogger"), "person", "blue"),
                    ("SuperFban", _menu("superfban"), "blue"),
                ],
                [
                    ("Sudo Mode", _tok("sudo"), "blue"),
                    ("Handler", _tok("handler"), "blue"),
                ],
                [
                    ("Extra Plugins", _tok("plugins"), "blue"),
                    ("Addons", _tok("addons"), "blue"),
                ],
                [
                    ("Emoji in Help", _tok("help_emoji"), "blue"),
                    ("Set gDrive", _tok("gdrive"), "blue"),
                ],
                [
                    ("Inline Pic", _tok("inline_pic"), "blue"),
                    ("Sudo HNDLR", _tok("sudo_handler"), "blue"),
                ],
                [("Dual Mode", _menu("dual_mode"), "blue")],
                [("Back", START_SETTER, None)],
            ],
            icon=False,
        ),
    },
    "superfban": {
        "head": "SuperFban Settings ",
        "body": "SuperFban bans spammers across federations for your channel.",
        "foot": lambda: f"Currently: FBan group {_val('FBAN_GROUP_ID')}",
        "buttons": ikm(
            [
                [("FBan Group", _tok("fbangroup"), "blue")],
                [("Exclude Feds", _conv("excludefeds"), "blue")],
                [("Back", _menu("othervars"), None)],
            ],
            icon=False,
        ),
    },
    "apauto": {
        "text": "This'll auto approve on outgoing messages",
        "head": "Auto Approve ",
        "body": "Automatically approve incoming private messages.",
        "foot": lambda: f"Currently: {_toggle('AUTOAPPROVE')}",
        "buttons": ikm(
            [
                [("Auto Approve ON", _tok("auto_ap_on"), "green")],
                [("Auto Approve OFF", _tok("auto_ap_off"), "danger")],
                [("Back", _menu("pm_customisation"), None)],
            ],
            icon=False,
        ),
    },
    "alive_customisation": {
        "head": "Alive Customisation ",
        "body": f"Customise your {HNDLR}alive. Choose from the options below.",
        "foot": lambda: f"Currently: alive text {_val('ALIVE_TEXT')}",
        "buttons": ikm(
            [
                [("Alive Text", _conv("alive_text"), "blue")],
                [("Alive media", _tok("alive_media"), "blue")],
                [("Delete Alive Media", _tok("del_alive_media"), "danger")],
                [("Back", START_SETTER, None)],
            ],
            icon=False,
        ),
    },
    "pm_customisation": {
        "head": "PM Permit Settings",
        "body": "Customise your PMPERMIT settings below.",
        "buttons": ikm(
            [
                [
                    ("PM Text", _tok("pm_txt"), "blue"),
                    ("PM Media", _tok("pm_media"), "blue"),
                ],
                [
                    ("Auto Approve", _menu("apauto"), "green"),
                    ("PM Logger", _tok("pm_logger"), "blue"),
                ],
                [
                    ("Set Warns", _tok("set_pm_warns"), "green"),
                    ("Delete PM Media", _tok("del_pm_media"), "danger"),
                ],
                [("PM Permit Type", _menu("pm_type"), "blue")],
                [("Back", _menu("pm_settings"), None)],
            ],
            icon=False,
        ),
    },
    "pm_type": {
        "head": "PM Permit Type",
        "body": "Choose how the PM Permit guard handles incoming private messages.",
        "foot": lambda: (
            f"Currently: {'Inline' if udB.get_key('INLINE_PM') in (True, 'True') else 'Normal'}"
        ),
        "buttons": ikm(
            [
                [("Inline", _tok("pm_inline"), "green")],
                [("Normal", _tok("pm_normal"), "blue")],
                [("Back", _menu("pm_customisation"), None)],
            ],
            icon=False,
        ),
    },
    "pm_settings": {
        "head": "PM Permit",
        "body": "Control the PM Permit guard for incoming private messages.",
        "foot": lambda: f"Currently: {_toggle('PMSETTING')}",
        "buttons": ikm(
            [
                [("Turn PM Permit On", _tok("pm_permit_on"), "success")],
                [("Turn PM Permit Off", _tok("pm_permit_off"), "danger")],
                [("Customize PM Permit", _menu("pm_customisation"))],
                [("Back", START_SETTER, None)],
            ],
            icon=False,
        ),
    },
    "chatbot": {
        "head": "Chat Bot ",
        "body": "Chat with people through your assistant bot.",
        "data": {
            "More info": rawhtml(
                '<a href="https://t.me/UltroidUpdates/2">t.me/UltroidUpdates/2</a>'
            )
        },
        "foot": lambda: f"Currently: {_toggle('PMBOT')}",
        "buttons": ikm(
            [
                [
                    ("Chat Bot On", _tok("chatbot_on"), "success"),
                    ("Chat Bot Off", _tok("chatbot_off"), "danger"),
                ],
                [
                    ("Bot Welcome", _tok("bot_welcome"), "blue"),
                    ("Bot Welcome Media", _tok("bot_welcome_media"), "blue"),
                ],
                [("Bot Info Text", _tok("bot_info_text"), "blue")],
                [("Force Subscribe", _tok("bot_fsub"), "blue")],
                [("Back", START_SETTER, None)],
            ],
            icon=False,
        ),
    },
    "vc_menu": {
        "head": "VC Song Bot ",
        "body": "Play songs in group voice chat.",
        "data": {
            "More info": rawhtml(
                '<a href="https://t.me/UltroidUpdates/4">t.me/UltroidUpdates/4</a>'
            )
        },
        "foot": lambda: f"Currently: session {_mask_val('VC_SESSION')}",
        "buttons": ikm(
            [
                [("VC Session", _conv("vc_session"), "blue")],
                [("Back", START_SETTER, None)],
            ],
            icon=False,
        ),
    },
    "dual_mode": {
        "head": "Dual Mode ",
        "data": {
            "About": rawhtml(
                '<a href="https://t.me/UltroidUpdates/18">t.me/UltroidUpdates/18</a>'
            )
        },
        "foot": lambda: f"Currently: {_toggle('DUAL_MODE')}",
        "buttons": ikm(
            [
                [
                    ("Dual Mode On", _tok("dual_mode_on"), "success"),
                    ("Dual Mode Off", _tok("dual_mode_off"), "danger"),
                ],
                [("Dual Mode Handler", _tok("dual_mode_handler"), "blue")],
                [("Back", _menu("othervars"), None)],
            ],
            icon=False,
        ),
    },
    "apiset": {
        "head": "API Keys ",
        "body": get_string("ast_1"),
        "foot": lambda: (
            f"Keys set: "
            f"{sum(1 for k in ('RMBG_API', 'DEEP_AI', 'OCR_API', 'SERPAPI') if udB.get_key(k))}/4"
        ),
        "buttons": ikm(
            [
                [("Remove.bg API", _conv("rmbg_key_set"), "blue")],
                [("DEEP API", _conv("deep_key_set"), "blue")],
                [("OCR API", _conv("ocr_key_set"), "blue")],
                [("SERP API", _conv("serp_key_set"), "blue")],
                [("Back", START_SETTER, None)],
            ],
            icon=False,
        ),
    },
}

_MENU_BUTTON_DESCRIPTIONS = {
    "remove.bg api": "Set or replace the API key; its value stays hidden.",
    "deep api": "Set or replace the DeepAI key; its value stays hidden.",
    "ocr api": "Set or replace the OCR key; its value stays hidden.",
    "serp api": "Set or replace the search key; its value stays hidden.",
    "chat bot on": "Enable the private-message chat bot.",
    "chat bot off": "Disable the private-message chat bot.",
    "bot welcome": "Edit the welcome message shown by the bot.",
    "bot welcome media": "Set the media shown with the welcome message.",
    "bot info text": "Edit the information shown from the Info button.",
    "force subscribe": "Set the channel or group membership requirement.",
    "alive text": "Edit the assistant's alive message.",
    "alive media": "Set media for the alive message.",
    "delete alive media": "Remove the saved alive media.",
    "turn pm permit on": "Enable the incoming private-message guard.",
    "turn pm permit off": "Disable the incoming private-message guard.",
    "customize pm permit": "Open text, media, logging, and approval options.",
    "tag logger": "Choose the chat used for tag logs.",
    "superfban": "Configure federated-ban group settings.",
    "sudo mode": "Manage sudo access.",
    "handler": "Change the assistant command prefix.",
    "extra plugins": "Choose the source for additional plugins.",
    "addons": "Enable or disable add-on plugins.",
    "emoji in help": "Configure emoji labels in the help menu.",
    "set gdrive": "Configure Google Drive access.",
    "inline pic": "Set or clear the inline result picture.",
    "sudo hndlr": "Change the sudo command prefix.",
    "dual mode": "Configure dual-client routing and its handler.",
    "vc session": "Set the voice-chat bot session; its value stays hidden.",
    "fban group": "Set the group used by federated-ban actions.",
    "exclude feds": "Set the federations excluded from these actions.",
    "auto approve": "Choose whether outgoing messages are auto-approved.",
    "pm logger": "Enable or configure private-message logging.",
    "set warns": "Set the private-message warning limit.",
    "delete pm media": "Remove the saved PM Permit media.",
    "pm permit type": "Choose normal or inline PM Permit handling.",
    "inline": "Use inline PM Permit responses.",
    "normal": "Use normal PM Permit responses.",
    "dual mode on": "Enable dual-client mode.",
    "dual mode off": "Disable dual-client mode.",
    "dual mode handler": "Set the dual-client command handler.",
}


def _menu_table_rows(menu):
    """Describe visible submenu buttons without rendering stored values."""
    rows = []
    for button_row in menu.get("buttons", ()):
        for button in button_row:
            label = getattr(button, "text", None)
            if label is None:
                try:
                    label = button[0]
                except (IndexError, TypeError):
                    continue
            label = _menu_label(label)
            if label.casefold() == "back":
                continue
            purpose = _MENU_BUTTON_DESCRIPTIONS.get(
                label.casefold(), "Open or update this setting."
            )
            rows.append((label, purpose))
    return rows


_convo = {
    "rmbg_key_set": {
        "var": "RMBG_API",
        "name": "Remove.bg API Key",
        "text": get_string("ast_2"),
        "back": _menu("apiset"),
    },
    "deep_key_set": {
        "var": "DEEP_AI",
        "name": "Deep AI Api Key",
        "text": "Get Your Deep Api from deepai.org and send here.",
        "back": _menu("apiset"),
    },
    "ocr_key_set": {
        "var": "OCR_API",
        "name": "Ocr Api Key",
        "text": "Get Your OCR api from ocr.space and send that Here.",
        "back": _menu("apiset"),
    },
    "serp_key_set": {
        "var": "SERPAPI",
        "name": "Serp Api Key",
        "text": "Get Your SERP api from https://serpapi.com/users/sign_in and send that Here.",
        "back": _menu("apiset"),
    },
    "pm_logger_group": {
        "var": "PMLOGGROUP",
        "name": "Pm Log Group",
        "text": "Send chat id of chat which you want to save as Pm log Group.",
        "back": _tok("pm_logger"),
    },
    "vc_session": {
        "var": "VC_SESSION",
        "name": "Vc Session",
        "text": "**Vc session**\nEnter the New session u generated for vc bot.\n\nUse /cancel to terminate the operation.",
        "back": _menu("vc_menu"),
    },
    "set_pm_logger": {
        "var": "TAG_LOG",
        "name": "Tag Log Group",
        "text": f"Make a group, add your assistant and make it admin.\nGet the {HNDLR}id of that group and send it here for tag logs.\n\nUse /cancel to cancel.",
        "back": _tok("taglogger"),
    },
    "alive_text": {
        "var": "ALIVE_TEXT",
        "name": "Alive Text",
        "text": "**Alive Text**\nEnter the new alive text.\n\nUse /cancel to terminate the operation.",
        "back": _menu("alive_customisation"),
    },
    "excludefeds": {
        "var": "EXCLUDE_FED",
        "name": "Excluded Fed",
        "text": "Send the Fed IDs you want to exclude in the ban. Split by a space.\negid1 id2 id3\nSet is as None if you dont want any.\nUse /cancel to go back.",
        "back": _menu("superfban"),
    },
}


TOKEN_FILE = "resources/auths/auth_token.txt"


def _plugin_source_path(key, name, *, repo_root=None, registry=None):
    """Resolve a plugin source file inside its allowed repository directory."""
    repo_root = (
        Path(repo_root).resolve()
        if repo_root is not None
        else Path(__file__).resolve().parents[1]
    )
    roots = {
        "Official": ("plugins",),
        "Addons": ("addons",),
        "VCBot": ("vc", "plugins"),
    }.get(key, ())
    if not roots:
        return None

    if registry is None:
        registry = getattr(core, "registry", None)
    manifest = registry.get(name) if registry is not None else None
    manifest_path = getattr(manifest, "path", None)
    if manifest_path:
        candidate = Path(manifest_path)
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        candidate = candidate.resolve()
        if (
            candidate.suffix == ".py"
            and candidate.is_file()
            and any(candidate.is_relative_to(repo_root / folder) for folder in roots)
        ):
            return candidate

    normalized = str(name or "").replace("\\", "/").strip("/").removesuffix(".py")
    candidates = []
    for folder in roots:
        prefix = f"{folder}/"
        dotted_prefix = f"{folder}."
        if normalized.startswith(prefix):
            relative = normalized[len(prefix) :]
        elif normalized.startswith(dotted_prefix):
            relative = normalized[len(dotted_prefix) :].replace(".", "/")
        else:
            relative = normalized
            relative = relative.replace(".", "/")
        if not relative.endswith(".py"):
            relative += ".py"
        candidate = (repo_root / folder / relative).resolve()
        if (
            candidate.suffix == ".py"
            and candidate.is_file()
            and candidate.is_relative_to(repo_root / folder)
        ):
            candidates.append(candidate)
    return candidates[0] if candidates else None


@callback(cb_pattern("assistant", "inline", "send_plugin"), owner=True)
async def send(eve):
    key, _, rest = (eve.data_match.group(1)).decode("UTF-8").partition(":")
    name, index = rest.rsplit(":", 1) if ":" in rest else (rest, None)
    plugin = _plugin_source_path(key, name)
    if plugin is None:
        return await eve.answer("The plugin source file is unavailable.", alert=True)
    user_id = getattr(eve, "sender_id", None) or getattr(
        getattr(eve, "query", None), "user_id", None
    )
    if user_id is None:
        return await eve.answer(
            "I could not identify the requesting account.", alert=True
        )
    paste_ref = plugin.relative_to(Path(__file__).resolve().parents[1]).as_posix()
    if index is not None:
        paste_ref = f"{paste_ref}|{index}"
    buttons = ikm(
        [[("Paste", cb("assistant", "callbacks", "paste", paste_ref), "primary")]],
        icon=False,
    )
    try:
        await eve.client.send_file(
            user_id,
            file=str(plugin),
            caption=f"{name} source",
            buttons=buttons,
        )
    except Exception:
        LOGS.exception("Unable to deliver plugin source")
        return await eve.answer(
            "I could not send the source in a private message. Open the assistant bot and try again.",
            alert=True,
        )
    return await eve.answer("Source sent in your private chat.", alert=True)


heroku_api, app_name = Var.HEROKU_API, Var.HEROKU_APP_NAME


@callback(_handler("updatenow"), owner=True)
async def update(eve):
    from pyUltroid.updater import up as update_engine

    await eve.edit(
        fmtmsg("Updating ", msgs=_plain(get_string("clst_1"))),
        parse_mode="html",
    )
    msg_id = getattr(eve, "message_id", None) or getattr(
        getattr(eve, "message", None), "id", 0
    )

    state = await update_engine.engine.apply(
        restart_notice=("bot", eve.chat_id, msg_id),
    )
    text = _plain(state.summary())
    foot = (
        "Core files changed   restarting in 3s"
        if (state.status == "done" and state.needs_restart)
        else ""
    )
    await eve.edit(
        fmtmsg("Update", msgs=text, foot=foot),
        parse_mode="html",
    )


@callback(CHANGES_PATTERN, owner=True)
async def changes(event):
    from pyUltroid.updater import up as update_engine

    match = event.data_match.group(1).decode("utf-8")
    await event.answer(get_string("clst_3"))
    diff = await update_engine.engine.check()
    button = [_row(("Update Now", _tok("updatenow"), "green"))]
    if not (diff and diff.has_changes):
        return await event.edit(
            fmtmsg("Updates ", msgs="Already up-to-date."),
            buttons=button,
            parse_mode="html",
        )
    tl_chnglog = diff.changelog_text(max_entries=len(diff.commits))
    head = f"Ultroid {ultroid_version} updates for {diff.branch}"
    if not match:
        try:
            if len(tl_chnglog) > 700:
                tl_chnglog = f"{tl_chnglog[:700]}..."
                button.append(
                    _row(
                        (
                            "View Complete",
                            cb("assistant", "callbacks", "changes", "all"),
                            "primary",
                        )
                    )
                )

            await event.edit(
                fmtmsg("Updates ", msgs="Writing changelogs"),
                parse_mode="html",
            )
            img = await fns.Carbon(
                file_name="changelog",
                code=tl_chnglog,
                backgroundColor=choice(ATRA_COL),
                language="md",
            )
            return await event.edit(
                fmtmsg("  Ultroid Userbot  "),
                file=img,
                buttons=button,
                parse_mode="html",
            )
        except Exception as er:
            LOGS.exception(er)
    changelog_str = (
        f"{head}:\n\n{_plain(diff.changelog_text(10))}"
        "\n\nClick the below button to update!"
    )
    if len(changelog_str) > 1024:
        await event.edit(
            fmtmsg("Updates ", msgs=_plain(get_string("upd_4"))),
            parse_mode="html",
        )
        await asyncio.sleep(2)
        with open("ultroid_updates.txt", "w+") as file:
            file.write(tl_chnglog)
        await event.edit(
            fmtmsg("Updates ", msgs=_plain(get_string("upd_5"))),
            file="ultroid_updates.txt",
            buttons=button,
        )
        remove("ultroid_updates.txt")
        return
    await event.edit(
        fmtmsg(
            head,
            msgs=_plain(diff.changelog_text(10)),
            foot="Click the below button to update!",
        ),
        buttons=button,
        parse_mode="html",
    )


@callback(PASTA_PATTERN, owner=True)
async def _(e):

    ok = (e.data_match.group(1)).decode("UTF-8")
    index = None
    if "|" in ok:
        ok, index = ok.split("|")
    with open(ok) as hmm:
        _, key = await fns.get_paste(hmm.read())
    link = f"https://spaceb.in/{key}"
    raw = f"https://spaceb.in/api/v1/documents/{key}/raw"
    if not _:
        return await e.answer(key[:30], alert=True)
    if ok.startswith("addons"):
        key = "Addons"
    elif ok.startswith(("vcbot", "vc/")):
        key = "VCBot"
    else:
        key = "Official"

    data = cb(
        "assistant", "inline", "help_menu", key, index if index is not None else "0"
    )
    await e.edit(
        "",
        buttons=ikm(
            [
                [
                    ("Link", "url", link, "green", "link"),
                    ("Raw", "url", raw, "green", "play"),
                ],
                [("Back", data, None)],
            ],
            icon=False,
        ),
    )


@callback(MENU_PATTERN, owner=True)
async def _edit_to(event):
    match = event.data_match.group(1).decode("utf-8")
    menu = _buttons.get(match)
    if not menu:
        return
    rows = _menu_table_rows(menu)
    await edit_callback_page(
        event,
        rich_page(
            _menu_label(menu["head"]),
            rows,
            description=_plain(menu.get("body", "")),
            columns=("Button", "What it does"),
            total=len(rows),
        ),
        buttons=_refresh_menu_buttons(menu["buttons"]),
    )


@callback(CONV_PATTERN, owner=True)
async def convo_handler(event: events.CallbackQuery):
    match = event.data_match.group(1).decode("utf-8")
    if not _convo.get(match):
        return
    await event.delete()
    get_ = _convo[match]
    back = get_["back"]
    async with event.client.conversation(event.sender_id) as conv:
        prompt = await send_rich(
            event.client,
            conv.chat_id,
            rich_page(
                get_["name"],
                [("Instructions", _plain(get_["text"]))],
                columns=("Step", "What to do"),
                total=1,
            ),
            link_preview=False,
        )
        response = await conv.get_response(prompt)
        themssg = _message_text(response)
        try:
            themssg = ast.literal_eval(themssg)
        except Exception:
            pass
        if _is_cancel(response):
            return await send_rich(
                event.client,
                conv.chat_id,
                rich_page(
                    "Settings cancelled",
                    [("Result", "Nothing was changed.")],
                    columns=("Status", "Details"),
                    total=1,
                ),
                buttons=get_back_button(back),
                link_preview=False,
            )
        await setit(event, get_["var"], themssg)
        secret_setting = get_["var"] in {
            "RMBG_API",
            "DEEP_AI",
            "OCR_API",
            "SERPAPI",
            "VC_SESSION",
        }
        shown_value = "Set (hidden)" if secret_setting else _plain(themssg)
        await send_rich(
            event.client,
            conv.chat_id,
            rich_page(
                f"{get_['name']} updated",
                [("Result", shown_value)],
                columns=("Setting", "Status"),
                total=1,
            ),
            buttons=get_back_button(back),
            link_preview=False,
        )


@callback(_handler("authorise"), owner=True)
async def _(e):
    if not e.is_private:
        return
    if not GDrive:
        return await e.edit(
            fmtmsg("Error ", msgs="Google Drive is not available right now."),
            parse_mode="html",
        )
    url = GDrive._create_token_file()
    await e.edit(
        fmtmsg("Google Drive ", msgs="Go to the below link and send the code!"),
        parse_mode="html",
    )
    async with asst.conversation(e.sender_id) as conv:
        await conv.send_message(url)
        code = await conv.get_response()
        if GDrive._create_token_file(code=code.text):
            await conv.send_message(
                fmtmsg(
                    "Success ",
                    msgs="You are all set to use Google Drive with Ultroid Userbot.",
                ),
                buttons=_row(("Main Menu", START_SETTER, "primary")),
                parse_mode="html",
            )
        else:
            await conv.send_message(
                fmtmsg("Error ", msgs="Wrong code! Click authorise again."),
                parse_mode="html",
            )


@callback(_handler("folderid"), owner=True, func=lambda x: x.is_private)
async def _(e):
    if not e.is_private:
        return
    msg = (
        "Send your FOLDER ID\n\n"
        + "For FOLDER ID:\n"
        + "1. Open Google Drive App.\n"
        + "2. Create Folder.\n"
        + "3. Make that folder public.\n"
        + "4. Send link of that folder."
    )
    await e.delete()
    async with asst.conversation(e.sender_id, timeout=150) as conv:
        await conv.send_message(
            fmtmsg("Google Drive Folder ", msgs=msg),
            parse_mode="html",
        )
        repl = await conv.get_response()
        id = repl.text
        if id.startswith("https"):
            id = id.split("?id=")[-1]
        udB.set_key("GDRIVE_FOLDER_ID", id)
        await repl.reply(
            fmtmsg("Success ", msgs="Folder ID saved."),
            buttons=get_back_button(_tok("gdrive")),
            parse_mode="html",
        )


@callback(_handler("gdrive"), owner=True)
async def _(e):
    if not e.is_private:
        return
    await e.edit(
        fmtmsg(
            "Google Drive ",
            msgs="Click Authorise and send the code.\n\nYou can use your own CLIENT ID and SECRET.",
            data={
                "More info": rawhtml(
                    '<a href="https://t.me/UltroidUpdates/37">t.me/UltroidUpdates/37</a>'
                )
            },
        ),
        buttons=ikm(
            [
                [
                    ("Folder ID", _tok("folderid"), "blue"),
                    ("Authorise", _tok("authorise"), "green"),
                ],
                [("Back", _menu("othervars"), None)],
            ],
            icon=False,
        ),
        link_preview=False,
    )


@callback(_handler("dual_mode_on"), owner=True)
async def rhwhe(e):
    udB.set_key("DUAL_MODE", "True")
    await e.edit(
        fmtmsg(
            "Dual Mode ", msgs="Dual Mode has been turned on.", foot="Currently: On"
        ),
        buttons=get_back_button(_menu("othervars")),
        parse_mode="html",
    )


@callback(_handler("dual_mode_off"), owner=True)
async def rhwheoff(e):
    udB.del_key("DUAL_MODE")
    await e.edit(
        fmtmsg(
            "Dual Mode ", msgs="Dual Mode has been turned off.", foot="Currently: Off"
        ),
        buttons=get_back_button(_menu("othervars")),
        parse_mode="html",
    )


@callback(_handler("dual_mode_handler"), owner=True)
async def hndlrr(event):
    await event.delete()
    pru = event.sender_id
    var = "DUAL_HNDLR"
    name = "Dual Handler"
    CH = udB.get_key(var) or "/"
    async with event.client.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs=f"Send the symbol you want as Handler/Trigger for your assistant bot.\nCurrent Handler is {CH}.\n\nUse /cancel to cancel.",
            ),
            parse_mode="html",
        )
        response = conv.wait_event(events.NewMessage(chats=pru))
        response = await response
        themssg = response.message.message
        if _is_cancel(response):
            await conv.send_message(
                fmtmsg("Cancelled ", msgs="Nothing was changed."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        elif len(themssg) > 1:
            await conv.send_message(
                fmtmsg("Error ", msgs="Incorrect Handler."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        else:
            await setit(event, var, themssg)
            await conv.send_message(
                fmtmsg(f"{name} Changed ", msgs=f"New value: {themssg}"),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )


@callback(_handler("help_emoji"), owner=True)
async def help_emoji(event):
    await event.delete()
    pru = event.sender_id
    var = "EMOJI_IN_HELP"
    name = f"Emoji in {HNDLR}help menu"
    async with event.client.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs="Send the emoji you want to set .\n\nUse /cancel to cancel.",
            ),
            parse_mode="html",
        )
        response = conv.wait_event(events.NewMessage(chats=pru))
        response = await response
        themssg = response.message.message
        if _is_cancel(response):
            await conv.send_message(
                fmtmsg("Cancelled ", msgs="Nothing was changed."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        elif themssg.startswith(("/", HNDLR)):
            await conv.send_message(
                fmtmsg("Error ", msgs="Incorrect Emoji."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        else:
            await setit(event, var, themssg)
            await conv.send_message(
                fmtmsg(f"{name} Changed ", msgs=f"New value: {themssg}"),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )


@callback(_handler("plugins"), owner=True)
async def pluginch(event):
    await event.delete()
    pru = event.sender_id
    var = "PLUGIN_CHANNEL"
    name = "Plugin Channel"
    async with event.client.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs="Send the id or username of the channel to install plugins from.\n\nOur Channel: @ultroidplugins\n\nUse /cancel to cancel.",
            ),
            parse_mode="html",
        )
        response = conv.wait_event(events.NewMessage(chats=pru))
        response = await response
        themssg = response.message.message
        if _is_cancel(response):
            await conv.send_message(
                fmtmsg("Cancelled ", msgs="Nothing was changed."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        elif themssg.startswith(("/", HNDLR)):
            await conv.send_message(
                fmtmsg("Error ", msgs="Incorrect channel."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        else:
            await setit(event, var, themssg)
            await conv.send_message(
                fmtmsg(
                    f"{name} Changed ",
                    msgs=f"New value: {themssg}",
                    foot="Restart after setting all things.",
                ),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )


@callback(_handler("handler"), owner=True)
async def hndlrr(event):
    await event.delete()
    pru = event.sender_id
    var = "HNDLR"
    name = "Handler/Trigger"
    async with event.client.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs=f"Send the symbol you want as Handler/Trigger to use the bot.\nUse /cancel to cancel.",
                foot=lambda: f"Current Handler is {_val('HNDLR')}",
            ),
            parse_mode="html",
        )
        response = conv.wait_event(events.NewMessage(chats=pru))
        response = await response
        themssg = response.message.message
        if _is_cancel(response):
            await conv.send_message(
                fmtmsg("Cancelled ", msgs="Nothing was changed."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        elif len(themssg) > 1:
            await conv.send_message(
                fmtmsg("Error ", msgs="Incorrect Handler."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        elif themssg.startswith(("/", "#", "@")):
            await conv.send_message(
                fmtmsg("Error ", msgs="This cannot be used as handler."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        else:
            await setit(event, var, themssg)
            await conv.send_message(
                fmtmsg(f"{name} Changed ", msgs=f"New value: {themssg}"),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )


@callback(_handler("sudo_handler"), owner=True)
async def hndlrr(event):
    await event.delete()
    pru = event.sender_id
    var = "SUDO_HNDLR"
    name = "Sudo Handler"
    async with event.client.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs="Send the symbol you want as Sudo Handler/Trigger to use the bot.\n\nUse /cancel to cancel.",
                foot=f" Prohibited Values: /, #, @.",
            ),
            parse_mode="html",
        )

        response = conv.wait_event(events.NewMessage(chats=pru))
        response = await response
        themssg = response.message.message
        if _is_cancel(response):
            await conv.send_message(
                fmtmsg("Cancelled ", msgs="Nothing was changed."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        elif len(themssg) > 1:
            await conv.send_message(
                fmtmsg("Error ", msgs="Incorrect Handler."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        elif themssg.startswith(("/", "#", "@")):
            await conv.send_message(
                fmtmsg("Error ", msgs="This cannot be used as handler."),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )
        else:
            await setit(event, var, themssg)
            await conv.send_message(
                fmtmsg(f"{name} Changed ", msgs=f"New value: {themssg}"),
                buttons=get_back_button(_menu("othervars")),
                parse_mode="html",
            )


@callback(_handler("taglogger"), owner=True)
async def tagloggergrr(e):
    BUTTON = ikm(
        [
            [("SET TAG LOG", _conv("set_pm_logger"))],
            [("DELETE TAG LOG", _tok("deltag"), "danger")],
        ],
        icon=False,
    )
    BUTTON.append(get_back_button(_menu("othervars")))
    await e.edit(
        fmtmsg(
            "Tag Logger ",
            msgs="Choose whether to set or delete the tag log group.",
            foot=f"Currently: {_val('TAG_LOG')}",
        ),
        buttons=BUTTON,
        parse_mode="html",
    )


@callback(_handler("deltag"), owner=True)
async def _(e):
    udB.del_key("TAG_LOG")
    await e.edit(
        fmtmsg(
            "Tag Logger ",
            msgs="Tag Logger has been turned off.",
            foot="Currently: Off",
        ),
        buttons=get_back_button(_menu("othervars")),
        parse_mode="html",
    )


@callback(_handler("addons"), owner=True)
async def pmset(event):
    BT = (
        _row(("Addons Off", _tok("edof"), "danger"))
        if udB.get_key("ADDONS")
        else _row(("Addons On", _tok("edon"), "success"))
    )

    await event.edit(
        fmtmsg(
            "Addons ",
            msgs="Extra plugins can be enabled or disabled here.",
            foot=f"Currently: {_toggle('ADDONS')}",
        ),
        buttons=[
            BT,
            get_back_button(_menu("othervars")),
        ],
        parse_mode="html",
    )


@callback(_handler("edon"), owner=True)
async def eddon(event):
    var = "ADDONS"
    await setit(event, var, "True")
    await event.edit(
        fmtmsg(
            "Addons ",
            msgs="ADDONS has been turned on.\nAfter setting all things, restart.",
            foot="Currently: On",
        ),
        buttons=get_back_button(_tok("addons")),
        parse_mode="html",
    )


@callback(_handler("edof"), owner=True)
async def eddof(event):
    udB.set_key("ADDONS", "False")
    await event.edit(
        fmtmsg(
            "Addons ",
            msgs="ADDONS has been turned off.\nAfter setting all things, restart.",
            foot="Currently: Off",
        ),
        buttons=get_back_button(_tok("addons")),
        parse_mode="html",
    )


@callback(_handler("sudo"), owner=True)
async def pmset(event):
    BT = (
        _row(("Sudo Mode Off", _tok("ofsudo"), "danger"))
        if udB.get_key("SUDO")
        else _row(("Sudo Mode On", _tok("onsudo"), "success"))
    )

    await event.edit(
        fmtmsg(
            "Sudo Mode ",
            msgs=f"Some people you select can use your bot.\nUse {HNDLR}help sudo to know more.",
            foot=f"Currently: {_toggle('SUDO')}",
        ),
        buttons=[
            BT,
            get_back_button(_menu("othervars")),
        ],
        parse_mode="html",
    )


@callback(_handler("onsudo"), owner=True)
async def eddon(event):
    var = "SUDO"
    await setit(event, var, "True")
    await event.edit(
        fmtmsg(
            "Sudo Mode ",
            msgs="SUDO MODE has been turned on.\nAfter setting all things, restart.",
            foot="Currently: On",
        ),
        buttons=get_back_button(_tok("sudo")),
        parse_mode="html",
    )


@callback(_handler("ofsudo"), owner=True)
async def eddof(event):
    var = "SUDO"
    await setit(event, var, "False")
    await event.edit(
        fmtmsg(
            "Sudo Mode ",
            msgs="SUDO MODE has been turned off.\nAfter setting all things, restart.",
            foot="Currently: Off",
        ),
        buttons=get_back_button(_tok("sudo")),
        parse_mode="html",
    )


@callback(_handler("fbangroup"), owner=True)
async def fbangroup(event):
    await event.delete()
    name = "FBan Group ID"
    var = "FBAN_GROUP_ID"
    pru = event.sender_id
    async with asst.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs=f"Make a group, add @MissRose_Bot, send {HNDLR}id, copy that and send it here.\nUse /cancel to go back.",
                foot=f"Current value: {_val('FBAN_GROUP_ID')}",
            ),
            parse_mode="html",
        )
        response = conv.wait_event(events.NewMessage(chats=pru))
        response = await response
        themssg = response.message.message
        if _is_cancel(response):
            return await conv.send_message(
                fmtmsg("Cancelled ", msgs="Nothing was changed."),
                buttons=get_back_button(_menu("superfban")),
                parse_mode="html",
            )
        await setit(event, var, themssg)
        await conv.send_message(
            fmtmsg(f"{name} Changed ", msgs=f"New value: {themssg}"),
            buttons=get_back_button(_menu("superfban")),
            parse_mode="html",
        )


@callback(_handler("alive_media"), owner=True)
async def media(event):
    await event.delete()
    pru = event.sender_id
    var = "ALIVE_PIC"
    name = "Alive Media"
    async with event.client.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs="Send me a pic/gif/media to set as alive media.\n\nUse /cancel to terminate the operation.",
            ),
            parse_mode="html",
        )
        response = await conv.get_response()
        try:
            themssg = response.message
            if _is_cancel(response):
                return await conv.send_message(
                    fmtmsg("Cancelled ", msgs="Nothing was changed."),
                    buttons=get_back_button(_menu("alive_customisation")),
                    parse_mode="html",
                )
        except BaseException as er:
            LOGS.exception(er)
        if (
            not (response.text).startswith("/")
            and response.text != ""
            and (not response.media or isinstance(response.media, MessageMediaWebPage))
        ):
            url = text_to_url(response)
        elif response.sticker:
            url = response.file.id
        else:
            media = await event.client.download_media(response, "alvpc")
            try:
                x = upl(media)
                url = f"https://graph.org/{x[0]}"
                remove(media)
            except BaseException as er:
                LOGS.exception(er)
                return await conv.send_message(
                    fmtmsg("Terminated ", msgs="The operation was terminated."),
                    buttons=get_back_button(_menu("alive_customisation")),
                    parse_mode="html",
                )
        await setit(event, var, url)
        await conv.send_message(
            fmtmsg(f"{name} Changed ", msgs=f"New value: {url}"),
            buttons=get_back_button(_menu("alive_customisation")),
            parse_mode="html",
        )


@callback(_handler("del_alive_media"), owner=True)
async def dell(event):
    try:
        udB.del_key("ALIVE_PIC")
        return await event.edit(
            fmtmsg(
                "Alive Media ",
                msgs="Alive media has been deleted.",
                foot="Currently: Not set",
            ),
            buttons=get_back_button(_menu("alive_customisation")),
            parse_mode="html",
        )
    except BaseException as er:
        LOGS.exception(er)
        return await event.edit(
            fmtmsg("Error ", msgs="Something went wrong."),
            buttons=get_back_button(_menu("alive_customisation")),
            parse_mode="html",
        )


@callback(_handler("pm_inline"), owner=True)
async def inl_on(event):
    var = "INLINE_PM"
    await setit(event, var, "True")
    await event.edit(
        fmtmsg(
            "PM Permit Type",
            msgs="PM Permit type has been set to inline.",
            foot="Currently: Inline",
        ),
        buttons=get_back_button(_menu("pm_type")),
        parse_mode="html",
    )


@callback(_handler("pm_normal"), owner=True)
async def inl_on(event):
    var = "INLINE_PM"
    await setit(event, var, "False")
    await event.edit(
        fmtmsg(
            "PM Permit Type",
            msgs="PM Permit type has been set to normal.",
            foot="Currently: Normal",
        ),
        buttons=get_back_button(_menu("pm_type")),
        parse_mode="html",
    )


@callback(_handler("pm_txt"), owner=True)
async def name(event):
    await event.delete()
    pru = event.sender_id
    var = "PM_TEXT"
    name = "PM Text"
    async with event.client.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs="Enter the new PM Permit message.\n\nYou can use {name}, {fullname}, {count}, {mention}, and {username} placeholders.\n\nSend /cancel to stop.",
            ),
            parse_mode="html",
        )
        response = conv.wait_event(events.NewMessage(chats=pru))
        response = await response
        themssg = response.message.message
        if _is_cancel(response):
            return await conv.send_message(
                fmtmsg("Cancelled ", msgs="Nothing was changed."),
                buttons=get_back_button(_menu("pm_customisation")),
                parse_mode="html",
            )
        if len(themssg) > 4090:
            return await conv.send_message(
                fmtmsg(
                    "Error ", msgs="Message too long!\nGive a shorter message please."
                ),
                buttons=get_back_button(_menu("pm_customisation")),
                parse_mode="html",
            )
        await setit(event, var, themssg)
        await conv.send_message(
            fmtmsg(
                f"{name} Changed ",
                msgs=f"New value: {themssg}\n\nAfter setting all things, restart.",
            ),
            buttons=get_back_button(_menu("pm_customisation")),
            parse_mode="html",
        )


@callback(_handler("set_pm_warns"), owner=True)
async def name(event):
    btns = [
        (f"{x}", cb("assistant", "callbacks", "pm_warns", x), "primary")
        for x in range(1, 10)
    ]
    lst = ikm([btns[i : i + 3] for i in range(0, len(btns), 3)], icon=False)
    lst.append(get_back_button(_menu("pm_customisation")))
    await event.edit(
        fmtmsg(
            "PM Warnings ",
            msgs="Select the number of warnings for a user before getting blocked in PMs.",
            foot=f"Currently: {_val('PMWARNS')}",
        ),
        buttons=lst,
        parse_mode="html",
    )


@callback(WRNS_PATTERN, owner=True)
async def set_wrns(event):
    value = int(event.data_match.group(1).decode("UTF-8"))
    if dn := udB.set_key("PMWARNS", value):
        await event.edit(
            fmtmsg(
                "PM Warnings ",
                msgs=f"New users will have {value} chances in PMs before getting banned.",
                foot=f"Currently: {value}",
            ),
            buttons=get_back_button(_menu("pm_customisation")),
            parse_mode="html",
        )
    else:
        await event.edit(
            fmtmsg(
                "Error ", msgs=f"Something went wrong, please check your {HNDLR}logs."
            ),
            buttons=get_back_button(_menu("pm_customisation")),
            parse_mode="html",
        )


@callback(_handler("pm_media"), owner=True)
async def media(event):
    await event.delete()
    pru = event.sender_id
    var = "PMPIC"
    name = "PM Media"
    async with event.client.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs="Send a photo, GIF, sticker, or link for the PM Permit media.\n\nSend /cancel to stop.",
            ),
            parse_mode="html",
        )
        response = await conv.get_response()
        try:
            themssg = response.message
            if _is_cancel(response):
                return await conv.send_message(
                    fmtmsg("Cancelled ", msgs="Nothing was changed."),
                    buttons=get_back_button(_menu("pm_customisation")),
                    parse_mode="html",
                )
        except BaseException as er:
            LOGS.exception(er)
        media = await event.client.download_media(response, "pmpc")
        if (
            not (response.text).startswith("/")
            and response.text != ""
            and (not response.media or isinstance(response.media, MessageMediaWebPage))
        ):
            url = text_to_url(response)
        elif response.sticker:
            url = response.file.id
        else:
            try:
                x = upl(media)
                url = f"https://graph.org/{x[0]}"
                remove(media)
            except BaseException as er:
                LOGS.exception(er)
                return await conv.send_message(
                    fmtmsg("Terminated ", msgs="The operation was terminated."),
                    buttons=get_back_button(_menu("pm_customisation")),
                    parse_mode="html",
                )
        await setit(event, var, url)
        await conv.send_message(
            fmtmsg(f"{name} Changed ", msgs=f"New value: {url}"),
            buttons=get_back_button(_menu("pm_customisation")),
            parse_mode="html",
        )


@callback(_handler("del_pm_media"), owner=True)
async def dell(event):
    try:
        udB.del_key("PMPIC")
        return await event.edit(
            fmtmsg(
                "PM Media ",
                msgs="PM media has been deleted.",
                foot="Currently: Not set",
            ),
            buttons=get_back_button(_menu("pm_customisation")),
            parse_mode="html",
        )
    except BaseException as er:
        LOGS.exception(er)
        return await event.edit(
            fmtmsg("Error ", msgs="Something went wrong."),
            buttons=get_back_button(START_SETTER, " Settings"),
            parse_mode="html",
        )


@callback(_handler("auto_ap_on"), owner=True)
async def auto_ap_on(event):
    var = "AUTOAPPROVE"
    await setit(event, var, "True")
    await event.edit(
        fmtmsg(
            "Auto Approve ",
            msgs="AUTOAPPROVE has been started.",
            foot="Currently: On",
        ),
        buttons=get_back_button(_menu("apauto")),
        parse_mode="html",
    )


@callback(_handler("auto_ap_off"), owner=True)
async def auto_ap_off(event):
    try:
        udB.set_key("AUTOAPPROVE", "False")
        return await event.edit(
            fmtmsg(
                "Auto Approve ",
                msgs="AUTOAPPROVE has been stopped.",
                foot="Currently: Off",
            ),
            buttons=get_back_button(_menu("apauto")),
            parse_mode="html",
        )
    except BaseException as er:
        LOGS.exception(er)
        return await event.edit(
            fmtmsg("Error ", msgs="Something went wrong."),
            buttons=get_back_button(START_SETTER, " Settings"),
            parse_mode="html",
        )


@callback(_handler("pm_logger"), owner=True)
async def l_vcs(event):
    BT = (
        _row(("PM Logger Off", _tok("pm_logger_off"), "danger"))
        if udB.get_key("PMLOG")
        else _row(("PM Logger On", _tok("pm_logger_on"), "success"))
    )

    await event.edit(
        fmtmsg(
            "PM Logger",
            msgs="This will forward your PMs to your private group.",
            foot=f"Currently: {_toggle('PMLOG')}",
        ),
        buttons=[
            BT,
            _row(("PM Logger Group", _conv("pm_logger_group"), "primary")),
            get_back_button(_menu("pm_customisation")),
        ],
    )


@callback(_handler("pm_logger_on"), owner=True)
async def pmlog(event):
    await setit(event, "PMLOG", "True")
    await event.edit(
        fmtmsg("PM Logger", msgs="PM Logger is on.", foot="Currently: On"),
        buttons=get_back_button(_tok("pm_logger")),
        parse_mode="html",
    )


@callback(_handler("pm_logger_off"), owner=True)
async def pmlogof(event):
    try:
        udB.del_key("PMLOG")
        return await event.edit(
            fmtmsg("PM Logger", msgs="PM Logger is off.", foot="Currently: Off"),
            buttons=get_back_button(_tok("pm_logger")),
            parse_mode="html",
        )
    except BaseException as er:
        LOGS.exception(er)
        return await event.edit(
            fmtmsg("Error ", msgs="Something went wrong."),
            buttons=get_back_button(START_SETTER, " Settings"),
            parse_mode="html",
        )


@callback(_handler("pm_permit_on"), owner=True)
async def pmonn(event):
    var = "PMSETTING"
    await setit(event, var, "True")
    await event.edit(
        fmtmsg("PM Permit", msgs="PM Permit is on.", foot="Currently: On"),
        buttons=get_back_button(_menu("pm_settings")),
        parse_mode="html",
    )


@callback(_handler("pm_permit_off"), owner=True)
async def pmofff(event):
    var = "PMSETTING"
    await setit(event, var, "False")
    await event.edit(
        fmtmsg("PM Permit", msgs="PM Permit is off.", foot="Currently: Off"),
        buttons=get_back_button(_menu("pm_settings")),
        parse_mode="html",
    )


@callback(_handler("bot_welcome_media"), owner=True)
async def hhh(e):
    async with e.client.conversation(e.chat_id) as conv:
        await conv.send_message(
            fmtmsg(
                "Bot Welcome Media ",
                msgs="Send any media to keep at your bot's welcome.",
            ),
            parse_mode="html",
        )
        msg = await conv.get_response()
        if not msg.media or msg.text.startswith("/"):
            return await conv.send_message(
                fmtmsg("Terminated ", msgs="The operation was terminated."),
                buttons=get_back_button(_menu("chatbot")),
                parse_mode="html",
            )
        udB.set_key("STARTMEDIA", msg.file.id)
        await conv.send_message(
            fmtmsg("Bot Welcome Media ", msgs="Media has been saved."),
            buttons=get_back_button(_menu("chatbot")),
            parse_mode="html",
        )


@callback(_handler("bot_info_text"), owner=True)
async def hhh(e):
    async with e.client.conversation(e.chat_id) as conv:
        await conv.send_message(
            fmtmsg(
                "Bot Info Text ℹ",
                msgs="Send the message to display when a user presses the Info button in Bot Welcome.\n\nSend False to completely remove that button.",
            ),
            parse_mode="html",
        )
        msg = await conv.get_response()
        if msg.media or msg.text.startswith("/"):
            return await conv.send_message(
                fmtmsg("Terminated ", msgs="The operation was terminated."),
                buttons=get_back_button(_menu("chatbot")),
                parse_mode="html",
            )
        udB.set_key("BOT_INFO_START", msg.text)
        await conv.send_message(
            fmtmsg("Bot Info Text ", msgs="Text has been saved."),
            buttons=get_back_button(_menu("chatbot")),
            parse_mode="html",
        )


@callback(_handler("bot_fsub"), owner=True)
async def heheh(event):
    Ll = []
    err = ""
    async with event.client.conversation(event.chat_id) as conv:
        await conv.send_message(
            fmtmsg(
                "Force Subscribe ",
                msgs="Send the chat ID(s) the user must join before using Chat/PM Bot.\n\nSend /clear to disable PMBot Force Subscribe.\nSend /cancel to stop this process.",
            ),
            parse_mode="html",
        )
        await conv.send_message(
            fmtmsg("Example", msgs="-1001234567\n-100778888\n\nFor multiple chats."),
            parse_mode="html",
        )
        try:
            msg = await conv.get_response()
        except AsyncTimeOut:
            return await conv.send_message(
                fmtmsg("Time Up ", msgs="Start from /start back."),
                parse_mode="html",
            )
        if not msg.text or msg.text.startswith("/"):
            if msg.text == "/clear":
                udB.del_key("PMBOT_FSUB")
                return await conv.send_message(
                    fmtmsg(
                        "Force Subscribe ",
                        msgs="Force Subscribe has been stopped and applies to the next incoming message.",
                    ),
                    buttons=get_back_button(_menu("chatbot")),
                    parse_mode="html",
                )
            return await conv.send_message(
                fmtmsg("Cancelled ", msgs="Nothing was changed."),
                buttons=get_back_button(_menu("chatbot")),
                parse_mode="html",
            )
        for chat in msg.message.split("\n"):
            if chat.startswith("-") or chat.isdigit():
                chat = int(chat)
            try:
                CHSJSHS = await event.client.get_entity(chat)
                Ll.append(get_peer_id(CHSJSHS))
            except Exception as er:
                err += f"**{chat}** : {er}\n"
        if err:
            return await conv.send_message(
                fmtmsg("Error ", msgs=err),
                parse_mode="html",
            )
        # Keep the existing database representation; pmbot now parses this
        # legacy literal safely and reads it fresh for each incoming message.
        udB.set_key("PMBOT_FSUB", str(Ll))
        await conv.send_message(
            fmtmsg(
                "Force Subscribe ",
                msgs="Done. The setting applies to the next incoming message.",
            ),
            buttons=get_back_button(_menu("chatbot")),
            parse_mode="html",
        )


@callback(_handler("bot_welcome"), owner=True)
async def name(event):
    await event.delete()
    pru = event.sender_id
    var = "STARTMSG"
    name = "Bot Welcome Message:"
    async with event.client.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs="Enter the message to show when someone starts your assistant bot.\nYou can use {me} and {mention} parameters too.\nUse /cancel to terminate the operation.",
            ),
            parse_mode="html",
        )
        response = conv.wait_event(events.NewMessage(chats=pru))
        response = await response
        themssg = response.message.message
        if _is_cancel(response):
            return await conv.send_message(
                fmtmsg("Cancelled ", msgs="Nothing was changed."),
                buttons=get_back_button(_menu("chatbot")),
                parse_mode="html",
            )
        await setit(event, var, themssg)
        await conv.send_message(
            fmtmsg(f"{name} Changed ", msgs=f"New value: {themssg}"),
            buttons=get_back_button(_menu("chatbot")),
            parse_mode="html",
        )


@callback(_handler("chatbot_on"), owner=True)
async def chon(event):
    var = "PMBOT"
    await setit(event, var, "True")
    await AsyncPluginLoader(path="assistant/pmbot.py", key="PM Bot").load_all(log=False)
    if AST_PLUGINS.get("pmbot"):
        for i, e in AST_PLUGINS["pmbot"]:
            event.client.remove_event_handler(i)
        for i, e in AST_PLUGINS["pmbot"]:
            event.client.add_event_handler(i, events.NewMessage(**e))
    await event.edit(
        fmtmsg(
            "Chat Bot ",
            msgs="Now you can chat with people via this bot.",
            foot="Currently: On",
        ),
        buttons=get_back_button(_menu("chatbot")),
        parse_mode="html",
    )


@callback(_handler("chatbot_off"), owner=True)
async def chon(event):
    var = "PMBOT"
    await setit(event, var, "False")
    if AST_PLUGINS.get("pmbot"):
        for i, e in AST_PLUGINS["pmbot"]:
            event.client.remove_event_handler(i)
    await event.edit(
        fmtmsg(
            "Chat Bot ",
            msgs="Chat with people via this bot has been stopped.",
            foot="Currently: Off",
        ),
        buttons=get_back_button(_menu("chatbot")),
        parse_mode="html",
    )


@callback(_handler("inline_pic"), owner=True)
async def media(event):
    await event.delete()
    pru = event.sender_id
    var = "INLINE_PIC"
    name = "Inline Media"
    async with event.client.conversation(pru) as conv:
        await conv.send_message(
            fmtmsg(
                name,
                msgs="Send me a pic/gif/link to set as inline media.\n\nUse /cancel to terminate the operation.",
            ),
            parse_mode="html",
        )
        response = await conv.get_response()
        try:
            themssg = response.message
            if _is_cancel(response):
                return await conv.send_message(
                    fmtmsg("Cancelled ", msgs="Nothing was changed."),
                    buttons=get_back_button(START_SETTER, " Settings"),
                    parse_mode="html",
                )
        except BaseException as er:
            LOGS.exception(er)
        media = await event.client.download_media(response, "inlpic")
        if (
            not (response.text).startswith("/")
            and response.text != ""
            and (not response.media or isinstance(response.media, MessageMediaWebPage))
        ):
            url = text_to_url(response)
        else:
            try:
                x = upl(media)
                url = f"https://graph.org/{x[0]}"
                remove(media)
            except BaseException as er:
                LOGS.exception(er)
                return await conv.send_message(
                    fmtmsg("Terminated ", msgs="The operation was terminated."),
                    buttons=get_back_button(START_SETTER, " Settings"),
                    parse_mode="html",
                )
        await setit(event, var, url)
        await conv.send_message(
            fmtmsg(f"{name} Changed ", msgs=f"New value: {url}"),
            buttons=get_back_button(START_SETTER, " Settings"),
            parse_mode="html",
        )


FD_MEDIA = {}


@callback(FDROID_PATTERN, owner=True)
async def fdroid_dler(event):
    uri = event.data_match.group(1).decode("utf-8")
    if FD_MEDIA.get(uri):
        return await event.edit(file=FD_MEDIA[uri])
    await event.answer("  Starting Download  ", alert=True)
    await event.edit(
        fmtmsg("Downloading "),
        parse_mode="html",
    )
    URL = f"https://f-droid.org/packages/{uri}"
    conte = await fns.async_searcher(URL, re_content=True)
    BSC = bs(conte, "html.parser", from_encoding="utf-8")
    dl_ = BSC.find("p", "package-version-download").find("a")["href"]
    title = BSC.find("h3", "package-name").text.strip()
    thumb = BSC.find("img", "package-icon")["src"]
    if thumb.startswith("/"):
        thumb = f"https://f-droid.org{thumb}"
    thumb, _ = await fns.fast_download(thumb, filename=f"{uri}.png")
    s_time = time.time()
    file, _ = await fns.fast_download(
        dl_,
        filename=f"{title}.apk",
        progress_callback=lambda d, t: asyncio.get_running_loop().create_task(
            fns.progress(
                d,
                t,
                event,
                s_time,
                "Downloading...",
            )
        ),
    )

    time.time()
    n_file = await event.client.fast_uploader(
        file, show_progress=True, event=event, message="Uploading...", to_delete=True
    )
    buttons = ikm([("Search Again", "switch_inline", "fdroid", True, "green")])
    caption = fmtmsg(
        f"  {title}  ", data={"APK": rawhtml(f'<a href="{URL}">{title}</a>')}
    )
    try:
        msg = await event.edit(
            f"**  [{title}]({URL})  **", file=n_file, thumb=thumb, buttons=buttons
        )
    except Exception as er:
        LOGS.exception(er)
        try:
            msg = await event.client.edit_message(
                await event.get_input_chat(),
                event.message_id,
                f"**  [{title}]({URL})  **",
                buttons=buttons,
                thumb=thumb,
                file=n_file,
            )
        except Exception as er:
            remove(thumb)
            LOGS.exception(er)
            return await event.edit(
                fmtmsg("Error ", msgs=str(er)),
                buttons=buttons,
                parse_mode="html",
            )
    if msg and hasattr(msg, "media"):
        FD_MEDIA.update({uri: msg.media})
    remove(thumb)


@callback("noop")
async def _noop(event):
    await event.answer()

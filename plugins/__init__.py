# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from __future__ import annotations

import asyncio
import os
import time
from random import choice

from telethon import Button, events
from telethon.tl import functions, types  # pylint:ignore
from telethon.tl.custom.message import CustomMarkdown

# -- Markdown v3
from telethon.extensions.markdownv3 import MarkdownV3

from pyUltroid import *
from pyUltroid._misc._assistant import asst_cmd, callback, in_pattern
from pyUltroid._misc._decorators import ultroid_cmd
from pyUltroid._misc._wrappers import eod, eor
from pyUltroid.dB import DEVLIST, ULTROID_IMAGES
from pyUltroid.dB._core import HELP
from pyUltroid.fns import fns
from pyUltroid.fns.misc import CatboxUploader, Quotly
from pyUltroid.fns.tools import ChatBot, LogoHelper, TgConverter
from pyUltroid.startup._database import _BaseDatabase as Database
from pyUltroid.version import __version__, ultroid_version
from PIL import Image
from strings import get_help, get_string

async_searcher = fns.async_searcher
def_logs = fns.def_logs
download_file = fns.download_file
heroku_logs = fns.heroku_logs
safeinstall = fns.safeinstall

udB: Database

Redis = udB.get_key
con = TgConverter
quotly = Quotly()
OWNER_NAME = ultroid_bot.full_name
OWNER_ID = ultroid_bot.uid

ultroid_bot: UltroidClient
asst: UltroidClient

LOG_CHANNEL = udB.get_key("LOG_CHANNEL")

ultroid_bot.parse_mode = CustomMarkdown()


def inline_pic():
    INLINE_PIC = udB.get_key("INLINE_PIC")
    if INLINE_PIC is None:
        INLINE_PIC = choice(ULTROID_IMAGES)
    elif INLINE_PIC == False:
        INLINE_PIC = None
    return INLINE_PIC


Telegraph = fns.telegraph_client()

List = []
Dict = {}
InlinePlugin = {}
N = 0
cmd = ultroid_cmd
STUFF = {}

# Chats, which needs to be ignore for some cases
# Considerably, there can be many
# Feel Free to Add Any other...

NOSPAM_CHAT = [
    -1001361294038,  # UltroidSupportChat
    -1001387666944,  # PyrogramChat
    -1001109500936,  # TelethonChat
    -1001050982793,  # Python
    -1001256902287,  # DurovsChat
    -1001473548283,  # SharingUserbot
]

KANGING_STR = [
    "Using Witchery to kang this sticker...",
    "Plagiarising hehe...",
    "Inviting this sticker over to my pack...",
    "Kanging this sticker...",
    "Hey that's a nice sticker!\nMind if I kang?!..",
    "Hehe me stel ur stiker...",
    "Ay look over there (☉｡☉)!→\nWhile I kang this...",
    "Roses are red violets are blue, kanging this sticker so my pack looks cool",
    "Imprisoning this sticker...",
    "Mr.Steal-Your-Sticker is stealing this sticker... ",
]

ATRA_COL = [
    "DarkCyan",
    "DeepSkyBlue",
    "DarkTurquoise",
    "Cyan",
    "LightSkyBlue",
    "Turquoise",
    "MediumVioletRed",
    "Aquamarine",
    "Lightcyan",
    "Azure",
    "Moccasin",
    "PowderBlue",
]


# Plugin modules import these names from their parent package. Keep that
# surface backed by the unified fns object instead of recreating 80 wrappers.
_FNS_COMPAT_EXPORTS = frozenset(
    {
        "Carbon",
        "ReTrieveFile",
        "allcmds",
        "async_searcher",
        "bash",
        "check_filename",
        "def_logs",
        "downloader",
        "duration_s",
        "fast_download",
        "genss",
        "get_all_files",
        "get_paste",
        "get_random_user_data",
        "heroku_logs",
        "humanbytes",
        "inline_mention",
        "is_url_ok",
        "json_parser",
        "mediainfo",
        "progress",
        "restart",
        "run_async",
        "set_attributes",
        "shutdown",
        "split_list",
        "stdr",
        "text_set",
        "time_formatter",
        "un_plug",
        "uploader",
    }
)


def __getattr__(name):
    """Resolve legacy plugin helper names from their unified owner."""
    if name == "def_logs":
        from pyUltroid.fns.helper import def_logs

        value = def_logs
    elif name == "heroku_logs":
        from pyUltroid.fns.helper import heroku_logs

        value = heroku_logs
    elif name in _FNS_COMPAT_EXPORTS:
        from pyUltroid.fns import fns

        value = getattr(fns, name)
    elif name == "Image":
        from PIL import Image

        value = Image
    elif name == "safeinstall":
        from pyUltroid.fns.helper import safeinstall

        value = safeinstall
    elif name in {"HELP", "LIST"}:
        from pyUltroid.dB import _core

        value = getattr(_core, name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


# Preserve the package's previous star-import behavior and include the lazy
# helper names for addons that import the shared plugin surface.
__all__ = [name for name in globals() if not name.startswith("_")]
__all__.extend(sorted(_FNS_COMPAT_EXPORTS | {"Image", "HELP", "LIST"}))

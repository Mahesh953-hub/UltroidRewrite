# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from telethon import Button, custom
from telethon.errors.rpcerrorlist import ChatSendStickersForbiddenError
from telethon.utils import get_display_name

from custom.tg import ikm
from plugins import ATRA_COL, InlinePlugin
from pyUltroid import HNDLR, LOGS, ULTConfig, asst, udB, ultroid_bot, _ult_cache
from pyUltroid._misc import owner_and_sudos
from pyUltroid._misc._decorators import ultroid_cmd
from pyUltroid._misc._wrappers import eod
from pyUltroid._misc._assistant import asst_cmd, callback, in_pattern
from pyUltroid.configs import Var
from pyUltroid.fns.tools import _webupload_cache
from pyUltroid.fns import fns
from pyUltroid.fns.tools import _webupload_cache
from pyUltroid.version import ultroid_version
from strings import get_languages, get_string
from pyUltroid.dB import db as dbs

# The twelve submodules reach these through `from . import *`, so this module
# is the facade. Two rules keep that safe, and both are pinned by
# tests/test_assistant_facade.py:
#   1. nothing below imports a submodule of `assistant` - a package __init__
#      always finishes before any submodule body runs, which is what makes
#      the star import deterministic rather than lucky;
#   2. `pyUltroid` is reached by explicit import, never by star, so the
#      names the facade re-exports are provable instead of accidental.
OWNER_NAME = ultroid_bot.full_name
OWNER_ID = ultroid_bot.uid

AST_PLUGINS = {}


async def setit(event, name, value):
    try:
        udB.set_key(name, value)
    except BaseException as er:
        LOGS.exception(er)
        return await event.edit("`Something Went Wrong`")


def get_back_button(name, label="« Bᴀᴄᴋ", style="danger"):
    """Build a styled "« Back" row targeting ``name`` — a destination's own
    callback data (from ``assistant._cbs.cb``, or a legacy bare token).
    Reusing the destination's own data *is* the back-navigation mechanism:
    the click is a real callback-query round trip dispatched straight to
    that destination's handler (see ``assistant/_cbs.py``).
    """
    return ikm([(label, name, style, "back")])[0]

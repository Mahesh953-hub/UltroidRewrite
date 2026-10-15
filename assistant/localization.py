# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from . import (
    ULTConfig,
    callback,
    get_back_button,
    get_languages,
    get_string,
    udB,
)
from ._cbs import cb, cb_exact_pattern, cb_pattern
from custom.tg import ikm

LANG_MENU = cb_exact_pattern("assistant", "lang", "menu")
LANG_SET_PATTERN = cb_pattern("assistant", "lang", "set")

MAIN_MENU = cb("assistant", "start", "mainmenu")


@callback(LANG_MENU, owner=True)
async def setlang(event):
    languages = get_languages()
    btt = [
        (
            f"{languages[ult]['natively']} [{ult.lower()}]",
            cb("assistant", "lang", "set", ult),
            "primary",
        )
        for ult in languages
    ]
    buttons_data = [list(pair) for pair in zip(btt[::2], btt[1::2])]
    if len(btt) % 2 == 1:
        buttons_data.append([btt[-1]])
    buttons_data.append([("« Back", MAIN_MENU, "danger")])
    await event.edit(get_string("ast_4"), buttons=ikm(buttons_data, icon=True))


@callback(LANG_SET_PATTERN, owner=True)
async def settt(event):
    lang = event.data_match.group(1).decode("utf-8")
    languages = get_languages()
    ULTConfig.lang = lang
    udB.del_key("language") if lang == "en" else udB.set_key("language", lang)
    await event.edit(
        f"Your language has been set to {languages[lang]['natively']} [{lang}].",
        buttons=get_back_button(cb("assistant", "lang", "menu")),
    )

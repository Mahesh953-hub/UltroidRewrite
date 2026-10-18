# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE/>.

from custom.tg import ikm
from pyUltroid._core.richmsg import edit_rich

from . import (
    ULTConfig,
    callback,
    get_back_button,
    get_languages,
    udB,
)
from ._cbs import cb, cb_exact_pattern, cb_pattern
from .rich_pages import callback_edit_target, rich_page

LANG_MENU = cb_exact_pattern("assistant", "lang", "menu")
LANG_SET_PATTERN = cb_pattern("assistant", "lang", "set")
MAIN_MENU = cb("assistant", "start", "mainmenu")


def _language_key(raw: str, languages: dict) -> str | None:
    """Resolve a callback token against the currently supported locale keys."""
    key = str(raw or "").strip()
    if key in languages:
        return key
    return next(
        (
            candidate
            for candidate in languages
            if candidate.casefold() == key.casefold()
        ),
        None,
    )


async def _edit_language(event, blocks, buttons):
    """Edit this callback's message through the native RichMessage path."""
    peer, message = callback_edit_target(event)
    return await edit_rich(
        event.client,
        peer,
        message,
        blocks,
        format="html",
        buttons=buttons,
        link_preview=False,
    )


@callback(LANG_MENU, owner=True)
async def setlang(event):
    languages = get_languages()
    current = str(getattr(ULTConfig, "lang", "en") or "en").casefold()
    entries = [
        (
            f"{languages[code]['natively']} [{code.casefold()}]",
            cb("assistant", "lang", "set", code),
            "success" if code.casefold() == current else None,
        )
        for code in languages
    ]
    rows = [list(pair) for pair in zip(entries[::2], entries[1::2])]
    if len(entries) % 2:
        rows.append([entries[-1]])
    rows.append([("Back", MAIN_MENU, None)])
    await _edit_language(
        event,
        rich_page(
            "Language",
            [
                (
                    f"{languages[code]['natively']} [{code.casefold()}]",
                    "Currently selected" if code.casefold() == current else "Choose this language",
                )
                for code in languages
            ],
            description="Choose the language used by the assistant.",
            columns=("Language", "Status"),
            total=len(languages),
        ),
        ikm(rows, icon=False),
    )


@callback(LANG_SET_PATTERN, owner=True)
async def settt(event):
    try:
        raw = event.data_match.group(1).decode("utf-8")
    except (AttributeError, UnicodeDecodeError, IndexError):
        return await event.answer("That language selection is invalid.", alert=True)

    languages = get_languages()
    lang = _language_key(raw, languages)
    if lang is None:
        return await event.answer("That language is no longer available.", alert=True)

    ULTConfig.lang = lang
    udB.del_key("language") if lang.casefold() == "en" else udB.set_key(
        "language", lang
    )
    name = str(languages[lang].get("natively") or lang)
    await _edit_language(
        event,
        rich_page(
            "Language updated",
            [("Language", f"{name} [{lang}]")],
            description="The assistant language has been updated.",
            columns=("Setting", "Current value"),
            total=1,
        ),
        get_back_button(cb("assistant", "lang", "menu")),
    )

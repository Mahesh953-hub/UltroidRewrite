# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from telethon.errors.rpcerrorlist import (
    BotInlineDisabledError,
    BotMethodInvalidError,
    BotResponseTimeoutError,
)
from custom.tg import ikm
from telethon.tl.custom import Button

from pyUltroid._core._cbs import cb
from pyUltroid.dB._core import HELP, LIST
from pyUltroid._core.richmsg import RichBlocks
from pyUltroid.fns import fns

from . import HNDLR, LOGS, OWNER_NAME, asst, get_string, ultroid_cmd

from telethon.tl.types import (
    ReplyInlineMarkup,
    KeyboardButtonRow,
)

# ── MarkdownV3 + small-caps UI helpers for the .help menu ──────────────
#
# The .help menu body and button labels render under the fork's MarkdownV3
# (``custom.rich.RICH_MD`` / the ``.available``-guarded
# ``pyUltroid._core.entities.RICH_MD`` wrapper around it) when available,
# falling back to the classic ``inline_*`` strings under the client's default
# parse mode. ``parse_mode`` is *never* passed as ``None`` — the classic
# branch omits it (see :func:`_parse_mode_kwargs`) so the default applies
# instead of parsing being disabled. Button labels are folded to the
# small-caps font this UI uses throughout.

#: lower-case letter -> its Unicode small-cap glyph. ``f``/``q``/``x`` have
#: no small-cap code point and are deliberately absent (left untouched),
#: matching the mixed small-caps style the existing assistant captions use.
_SMALL_CAPS = {
    "a": "ᴀ",
    "b": "ʙ",
    "c": "ᴄ",
    "d": "ᴅ",
    "e": "ᴇ",
    "g": "ɢ",
    "h": "ʜ",
    "i": "ɪ",
    "j": "ᴊ",
    "k": "ᴋ",
    "l": "ʟ",
    "m": "ᴍ",
    "n": "ɴ",
    "o": "ᴏ",
    "p": "ᴘ",
    "r": "ʀ",
    "s": "ꜱ",
    "t": "ᴛ",
    "u": "ᴜ",
    "v": "ᴠ",
    "w": "ᴡ",
    "y": "ʏ",
    "z": "ᴢ",
}


def _to_smallcaps(text):
    """Render *text* in the small-caps font used across this UI.

    Lower-case letters with a small-cap glyph are swapped for it; upper-case
    letters, digits, punctuation and the few lower-case letters with no
    small-cap glyph (``f``/``q``/``x``) are left untouched.
    """
    return "".join(_SMALL_CAPS.get(ch, ch) for ch in str(text))


#: MarkdownV3 entity-delimiter chars that must be backslash-escaped when they
#: appear in *dynamic* values (owner name, counts) substituted into a template
#: that already carries intentional ``**bold**`` / ``[text](url)`` / `` `code` ``
#: markup — so user input can never open a stray entity.
_MDv3_ESCAPE_CHARS = "_*[]()~`>-|"


def _mdv3_escape(text):
    """Backslash-escape MarkdownV3 entity delimiters in *text*."""
    return "".join("\\" + ch if ch in _MDv3_ESCAPE_CHARS else ch for ch in str(text))


def _mdv3_text(template, *args):
    """``(text, parse_mode)`` for a help body built from *template*.

    MarkdownV3 (``RICH_MD``) when available: the template's plain ``~``
    separators are escaped (they are separators here, not strikethrough) and
    the dynamic *args* are delimiter-escaped, then everything is formatted and
    parsed as MarkdownV3. Classic fallback: the template is formatted as-is and
    returned with ``parse_mode=None`` — meaning the caller *omits*
    ``parse_mode`` (see :func:`_parse_mode_kwargs`) so the client's default
    parse mode applies; parsing is never disabled with an explicit ``None``.
    """
    try:
        from pyUltroid._core.entities import RICH_MD

        rich = bool(getattr(RICH_MD, "available", False))
    except Exception:
        RICH_MD = None
        rich = False
    if rich:
        return (
            template.replace("~", "\\~").format(*(_mdv3_escape(a) for a in args)),
            RICH_MD,
        )
    return template.format(*args), None


def _help_blocks():
    """Build the main help card for a failed inline-help request."""
    blocks = (
        RichBlocks(is_bot=True)
        .header("Bot Help", level=2)
        .paragraph(
            f"Welcome, {OWNER_NAME}. Choose a section to browse commands and tools."
        )
    )
    blocks.table(
        [
            ["Section", "Contents"],
            ["Plugins", "Core commands"],
            ["Add-ons", "Optional modules"],
            ["Voice Chat", "Voice and video chat tools"],
            ["Inline Plugins", "Searchable inline tools"],
        ],
        caption="Help menu",
        is_compact=True,
    )
    blocks.paragraph(
        "For plugin commands, use .help <plugin>; use .cmds for the complete command list."
    )
    return blocks


def _help_body(total_cmds):
    """``(text, parse_mode)`` for the main ``.help`` menu body (``inline_4``)."""
    return _mdv3_text(
        get_string("inline_4"),
        OWNER_NAME,
        len(HELP.get("Official", [])),
        len(HELP.get("Addons", [])),
        total_cmds,
    )


def _help_body_html(total_cmds):
    """Return the main ``.help`` menu body as an HTML string for ``InputRichMessageHTML``."""
    try:
        owner = _mdv3_escape(str(OWNER_NAME))
    except Exception:
        owner = str(OWNER_NAME)
    n_official = len(HELP.get("Official", []))
    n_addons = len(HELP.get("Addons", []))
    n_cmds = total_cmds
    return (
        f"<blockquote expandable><b>Bᴏᴛ Oғ {owner}</b></blockquote>\n\n"
        f"<b>Mᴀɪɴ Mᴇɴᴜ</b>\n\n"
        f"<b>Pʟᴜɢɪɴs</b> ~ {n_official}\n"
        f"<b>Aᴅᴅᴏɴs</b> ~ {n_addons}\n"
        f"<b>Tᴏᴛᴀʟ Cᴏᴍᴍᴀɴᴅs</b> ~ {n_cmds}"
    )


def _parse_mode_kwargs(parse_mode):
    """``{"parse_mode": parse_mode}`` when set, else ``{}`` (omit -> default).

    The classic fallback deliberately *omits* ``parse_mode`` (so the client's
    default parse mode applies) rather than passing ``parse_mode=None`` —
    passing ``None`` would disable parsing and dump raw markup, which the spec
    forbids ("never ``parse_mode=None``").
    """
    return {"parse_mode": parse_mode} if parse_mode is not None else {}


def _build_help_keyboard():
    """Build a ``ReplyInlineMarkup`` for the main ``.help`` menu.

    Uses the same layout as ``_main_help_menu`` but constructs
    ``KeyboardButtonRow`` / ``KeyboardInlineButton`` / styled
    ``KeyboardInlineButton`` objects for the rich-message path.
    """
    rows = [
        KeyboardButtonRow(
            buttons=[
                Button.inline(
                    _to_smallcaps(get_string("help_4")),
                    data=cb("assistant", "inline", "help_menu", "Official", "0"),
                    style="success",
                ),
                Button.inline(
                    _to_smallcaps(get_string("help_5")),
                    data=cb("assistant", "inline", "help_menu", "Addons", "0"),
                    style="primary",
                ),
            ]
        ),
        KeyboardButtonRow(
            buttons=[
                Button.inline(
                    _to_smallcaps(get_string("help_6")),
                    data=cb("assistant", "inline", "help_menu", "VCBot", "0"),
                    style="success",
                ),
                Button.inline(
                    _to_smallcaps(get_string("help_7")),
                    data="inlone",
                    style="success",
                ),
            ]
        ),
        KeyboardButtonRow(
            buttons=[
                Button.inline(
                    _to_smallcaps(get_string("help_8")),
                    data="ownr",
                    style="primary",
                ),
                Button.url(
                    _to_smallcaps(get_string("help_9")),
                    url=f"https://t.me/{asst.me.username}?start=set",
                    style="primary",
                ),
            ]
        ),
        KeyboardButtonRow(
            buttons=[
                Button.inline(
                    _to_smallcaps(get_string("help_10")),
                    data="close",
                    style="danger",
                ),
            ]
        ),
    ]
    return ReplyInlineMarkup(rows=rows)


_main_help_menu = ikm(
    [
        [
            (
                _to_smallcaps(get_string("help_4")),
                cb("assistant", "inline", "help_menu", "Official", "0"),
            ),
            (
                _to_smallcaps(get_string("help_5")),
                cb("assistant", "inline", "help_menu", "Addons", "0"),
            ),
        ],
        [
            (
                _to_smallcaps(get_string("help_6")),
                cb("assistant", "inline", "help_menu", "VCBot", "0"),
            ),
            (_to_smallcaps(get_string("help_7")), "inlone"),
        ],
        [
            (_to_smallcaps(get_string("help_8")), "ownr"),
            (
                _to_smallcaps(get_string("help_9")),
                "url",
                f"https://t.me/{asst.me.username}?start=set",
            ),
        ],
        [(_to_smallcaps(get_string("help_10")), "close")],
    ]
)


def _styled_main_help_menu():
    """Rebuild ``_main_help_menu`` through the styled button kit.

    Same rows, labels, callback-data and URL as ``_main_help_menu`` — only
    the rendering (``preset``-based bg colouring + the small-caps
    small-caps labels) differs. Built fresh on every call so it never
    shares (or mutates) the classic list's underlying row objects. Raises on
    import failure so the caller (``main_help_menu_buttons``) can fall back
    to the classic menu.

    Colour scheme (styled path only — the classic path has no colours):
      * Official / VCBot / Inline-Plugins category buttons -> GREEN (``success``)
      * Add-ons category button                      -> BLUE  (``primary``)
      * Owner Tools / Settings (URL)                 -> BLUE  (``primary``)
      * the main-menu close/back button              -> RED   (``danger``)
    """
    from pyUltroid._core.buttonkit import button as _sbtn

    return [
        [
            _sbtn(
                _to_smallcaps(get_string("help_4")),
                data=cb("assistant", "inline", "help_menu", "Official", "0"),
                preset="success",
            ),
            _sbtn(
                _to_smallcaps(get_string("help_5")),
                data=cb("assistant", "inline", "help_menu", "Addons", "0"),
                preset="primary",
            ),
        ],
        [
            _sbtn(
                _to_smallcaps(get_string("help_6")),
                data=cb("assistant", "inline", "help_menu", "VCBot", "0"),
                preset="success",
            ),
            _sbtn(_to_smallcaps(get_string("help_7")), data="inlone", preset="success"),
        ],
        [
            _sbtn(_to_smallcaps(get_string("help_8")), data="ownr", preset="primary"),
            _sbtn(
                _to_smallcaps(get_string("help_9")),
                url=f"https://t.me/{asst.me.username}?start=set",
                preset="primary",
            ),
        ],
        [_sbtn(_to_smallcaps(get_string("help_10")), data="close", preset="danger")],
    ]


def main_help_menu_buttons():
    """Return the main help menu keyboard for the current ``.ui buttons`` style.

    Defaults (``UI_BUTTONS=classic``) return ``_main_help_menu`` itself —
    the exact same list object, preserving the in-place row-2 mutation the
    ``_help`` command applies for MANAGER/DUAL_HNDLR setups (see below).
    ``styled`` mode returns a freshly-built styled keyboard, falling back to
    the classic list on any error (missing buttonkit, bad preset, etc.) so
    this never breaks the help menu.
    """
    try:
        from ._rebrand import ui_buttons_style

        styled = ui_buttons_style() == "styled"
    except Exception:
        styled = False
    if styled:
        try:
            return _styled_main_help_menu()
        except Exception:
            return _main_help_menu
    return _main_help_menu


@ultroid_cmd(pattern="help( (.*)|$)")
async def _help(ult):
    plug = ult.pattern_match.group(1).strip()
    chat = await ult.get_chat()
    if plug:
        try:
            if plug in HELP["Official"]:
                output = f"**Plugin** - `{plug}`\n"
                for i in HELP["Official"][plug]:
                    output += i
                output += "\n© @TeamUltroid"
                await ult.eor(output)
            elif HELP.get("Addons") and plug in HELP["Addons"]:
                output = f"**Plugin** - `{plug}`\n"
                for i in HELP["Addons"][plug]:
                    output += i
                output += "\n© @TeamUltroid"
                await ult.eor(output)
            elif HELP.get("VCBot") and plug in HELP["VCBot"]:
                output = f"**Plugin** - `{plug}`\n"
                for i in HELP["VCBot"][plug]:
                    output += i
                output += "\n© @TeamUltroid"
                await ult.eor(output)
            else:
                try:
                    x = get_string("help_11").format(plug)
                    for d in LIST[plug]:
                        x += HNDLR + d
                        x += "\n"
                    x += "\n© @TeamUltroid"
                    await ult.eor(x)
                except Exception:
                    file = None
                    compare_strings = []
                    for file_name in LIST:
                        compare_strings.append(file_name)
                        value = LIST[file_name]
                        for j in value:
                            j = fns.cmd_regex_replace(j)
                            compare_strings.append(j)
                            if j.strip() == plug:
                                file = file_name
                                break
                    if not file:
                        text = f"`{plug}` is not a valid plugin!"
                        best_match = None
                        for _ in compare_strings:
                            if plug in _ and not _.startswith("_"):
                                best_match = _
                                break
                        if best_match:
                            text += f"\nDid you mean `{best_match}`?"
                        return await ult.eor(text)
                    output = f"**Command** `{plug}` **found in plugin** - `{file}`\n"
                    if file in HELP["Official"]:
                        for i in HELP["Official"][file]:
                            output += i
                    elif HELP.get("Addons") and file in HELP["Addons"]:
                        for i in HELP["Addons"][file]:
                            output += i
                    elif HELP.get("VCBot") and file in HELP["VCBot"]:
                        for i in HELP["VCBot"][file]:
                            output += i
                    output += "\n© @TeamUltroid"
                    await ult.eor(output)
        except Exception as er:
            LOGS.exception(er)
            await ult.eor("Error 🤔 occured.")
    else:
        inline_failed = False
        inline_reason = ""
        try:
            results = await ult.client.inline_query(asst.me.username, "ultd")
            if results:
                await results[0].click(
                    chat.id, reply_to=ult.reply_to_msg_id, hide_via=False
                )
                await ult.delete()
                return
            else:
                inline_failed = True
                inline_reason = "Inline query returned no results — inline cache cold or @BotFather inline mode not enabled."
        except BotMethodInvalidError:
            inline_failed = True
            inline_reason = "BotMethodInvalidError — assistant bot @{} does not support inline mode.".format(
                asst.me.username
            )
        except BotResponseTimeoutError:
            inline_failed = True
            inline_reason = "BotResponseTimeoutError — inline query timed out."
        except BotInlineDisabledError:
            inline_failed = True
            inline_reason = (
                "BotInlineDisabledError — inline mode is disabled for @{}.".format(
                    asst.me.username
                )
            )
        except Exception as ex:
            inline_failed = True
            inline_reason = f"{type(ex).__name__}: {ex}"

        if inline_failed:
            LOGS.warning(
                "Inline help unavailable (%s); no standalone fallback will be sent.",
                inline_reason,
            )
        # Never post a standalone help card from the user account as fallback.
        # The only successful delivery is the inline result selected above.
        return

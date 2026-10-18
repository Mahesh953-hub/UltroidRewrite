# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

import html
import re

from telethon.errors.rpcerrorlist import (
    BotInlineDisabledError,
    BotMethodInvalidError,
    BotResponseTimeoutError,
)

from custom.tg import ikm
from pyUltroid._core._cbs import cb
from pyUltroid._core.richmsg import (
    RichBlocks,
    RichMarkup,
    coerce_to_rich_markup,
    edit_rich,
    normalize_blocks,
    rich_markup_from_blocks,
)
from pyUltroid.dB._core import HELP, LIST
from pyUltroid.fns.command_help import (
    command_aliases,
    command_matches,
    command_patterns,
    command_syntax,
)

from . import HNDLR, LOGS, InlinePlugin, asst, get_string, udB, ultroid_cmd


def _plugin_handler_count(name):
    """Count registered commands and plugin-control handlers."""
    count = len(command_patterns(name, LIST))
    try:
        from pyUltroid.ultroid import core

        count += len(core.manifest.get_entries(name))
    except (ImportError, AttributeError, KeyError, TypeError, ValueError):
        pass
    return count


def _help_overview_rows():
    """Build live handler totals for each main Help section."""
    rows = []
    for key, label, contains in (
        ("Official", "Plugins", "Core plugin commands and controls"),
        ("Addons", "Add-ons", "Optional plugin commands and controls"),
        ("VCBot", "Voice Chat", "Voice chat commands and controls"),
    ):
        plugins = HELP.get(key) or {}
        names = plugins.keys() if hasattr(plugins, "keys") else ()
        total = sum(_plugin_handler_count(name) for name in names)
        rows.append([label, str(total), contains])
    rows.append(["Inline Plugins", str(len(InlinePlugin)), "Searchable inline tools"])
    return rows


def _page_footer(page, pages, total):
    """Format the consistent, lower-case Help pagination footer."""
    return f"page {int(page)} of {int(pages)} • total {int(total)}"


_MARKDOWN_CODE_SPANS = re.compile(r"(`+)(.*?)\1", re.DOTALL)


def _markdown_docs_markup(text):
    """Convert help Markdown while preserving angle-bracket code placeholders."""
    source = str(text or "")

    def escape_code_angles(match):
        delimiter, code = match.groups()
        code = re.sub(r"(?<!\\)([<>])", r"\\\1", code)
        return f"{delimiter}{code}{delimiter}"

    source = _MARKDOWN_CODE_SPANS.sub(escape_code_angles, source)
    return coerce_to_rich_markup(source, parse_mode="mdv3")


def _help_blocks():
    """Build the overview-first Help Menu as native RichMessage HTML."""
    intro = RichBlocks(format="html")
    intro.header("Help Menu", level=2)
    intro.paragraph("Browse plugin commands, inline tools, and account settings from here.")
    overview = RichBlocks(format="html")
    overview.header("Overview", level=2)
    rows = _help_overview_rows()
    overview.table(
        [["Section name", "Total handlers", "Contains"], *rows],
        caption="Help sections",
        is_bordered=True,
        is_striped=True,
    )
    markup = rich_markup_from_blocks(
        normalize_blocks(intro) + normalize_blocks(overview), format="html"
    )
    total_handlers = sum(int(row[1]) for row in rows)
    return RichMarkup(
        f"{markup.text}<hr>"
        f"<p><i>{_page_footer(1, 1, total_handlers)}</i></p>",
        "html",
        markup.files,
    )


def _help_body(total_cmds=None):
    """Return Help Menu markup for callback edits."""
    return _help_blocks().text, "html"


def _help_body_html(total_cmds=None):
    """Return native RichMessage HTML for the complete Help Menu."""
    return _help_blocks().text


_MD_ESCAPE = "_*[]()~`>-|"


def _md_escape(value):
    return "".join("\\" + char if char in _MD_ESCAPE else char for char in str(value))


def _parse_mode_kwargs(parse_mode):
    """``{"parse_mode": parse_mode}`` when set, else ``{}`` (omit -> default).

    The classic fallback deliberately *omits* ``parse_mode`` (so the client's
    default parse mode applies) rather than passing ``parse_mode=None`` —
    passing ``None`` would disable parsing and dump raw markup, which the spec
    forbids ("never ``parse_mode=None``").
    """
    return {"parse_mode": parse_mode} if parse_mode is not None else {}


def _build_help_keyboard():
    """Return the clean, semantically colored help-button rows."""
    return main_help_menu_buttons()


def _assistant_settings_url():
    username = getattr(getattr(asst, "me", None), "username", None)
    if username:
        return f"https://t.me/{username}?start=set"
    return "https://t.me/UltroidSupportChat"


def _loaded_category_style(category):
    """Color loaded plugin libraries green and unavailable ones red."""
    return "success" if HELP.get(category) else "danger"


def _fallback_help_buttons():
    """Return only buttons that work on a user-client fallback message.

    Callback data in the rich menu is handled by the assistant bot. If inline
    mode is unavailable and this command sends through the user client, those
    callbacks have no receiver; a private-chat URL remains functional.
    """
    username = getattr(getattr(asst, "me", None), "username", None)
    if not username:
        return None
    return ikm(
        [
            [
                (
                    "Open assistant",
                    "url",
                    f"https://t.me/{username}?start=start",
                    "primary",
                )
            ]
        ],
        icon=False,
    )


_main_help_menu = ikm(
    [
        [
            (
                get_string("help_4"),
                cb("assistant", "inline", "help_menu", "Official", "0"),
                _loaded_category_style("Official"),
            ),
            (
                get_string("help_5"),
                cb("assistant", "inline", "help_menu", "Addons", "0"),
                _loaded_category_style("Addons"),
            ),
        ],
        [
            (
                get_string("help_6"),
                cb("assistant", "inline", "help_menu", "VCBot", "0"),
            ),
            (get_string("help_7"), "inlone"),
        ],
        [
            (get_string("help_8"), "ownr"),
            (
                get_string("help_9"),
                "url",
                _assistant_settings_url(),
                "primary",
            ),
        ],
        [(get_string("help_10"), "close", "danger")],
    ],
    icon=False,
)


def _styled_main_help_menu():
    """Rebuild the same menu through the styled button kit."""
    from pyUltroid._core.buttonkit import button as _sbtn

    return [
        [
            _sbtn(
                get_string("help_4"),
                data=cb("assistant", "inline", "help_menu", "Official", "0"),
                preset=_loaded_category_style("Official"),
            ),
            _sbtn(
                get_string("help_5"),
                data=cb("assistant", "inline", "help_menu", "Addons", "0"),
                preset=_loaded_category_style("Addons"),
            ),
        ],
        [
            _sbtn(
                get_string("help_6"),
                data=cb("assistant", "inline", "help_menu", "VCBot", "0"),
            ),
            _sbtn(get_string("help_7"), data="inlone"),
        ],
        [
            _sbtn(get_string("help_8"), data="ownr"),
            _sbtn(
                get_string("help_9"),
                url=_assistant_settings_url(),
                preset="primary",
            ),
        ],
        [_sbtn(get_string("help_10"), data="close", preset="danger")],
    ]


def main_help_menu_buttons():
    """Return the main help menu keyboard for the current ``.ui buttons`` style.

    Both button modes return fresh rows. The manager callback is included only
    on assistant-owned inline help messages; user-client fallback messages use
    a private-chat URL instead.
    """
    try:
        from ._rebrand import ui_buttons_style

        styled = ui_buttons_style() == "styled"
    except Exception:
        styled = False
    if styled:
        try:
            buttons = _styled_main_help_menu()
        except Exception:
            buttons = [list(row) for row in _main_help_menu]
    else:
        buttons = [list(row) for row in _main_help_menu]
    if udB.get_key("MANAGER") and udB.get_key("DUAL_HNDLR") == "/":
        manager_row = ikm(
            [[("Manager Help", cb("help", "manager", "mngbtn"), "primary")]],
            icon=False,
        )
        buttons[2:2] = manager_row
    return buttons


async def _edit_help(event, text):
    if isinstance(text, RichMarkup):
        markup = text
    else:
        markup = coerce_to_rich_markup(str(text), parse_mode="mdv3")
        if markup is None:
            markup = RichMarkup(html.escape(str(text)), "html")
    return await edit_rich(
        event.client,
        event.chat_id,
        event,
        markup,
        format="html",
        link_preview=False,
    )


def _plugin_title(name):
    return str(name).replace("_", " ").strip().title() or str(name)


def _pmf_guide_text(entries):
    """Describe the live PMF controls and what each one does."""
    lines = [
        "Plugin Manifest Framework (PMF) controls appear as buttons below.",
        "Toggles switch and persist a setting; actions run once; custom buttons run plugin-defined handlers.",
    ]
    for entry in entries or ():
        kind = entry.get("type")
        label = entry.get("label") or entry.get("key") or "Control"
        if kind == "toggle":
            state = "On" if entry.get("state") else "Off"
            detail = f"toggle, currently {state}"
        elif kind == "action":
            detail = "one-shot action"
        elif kind == "manifest_button":
            detail = "custom plugin action"
        else:
            continue
        description = str(entry.get("description") or "").strip()
        line = f"{label}: {detail}"
        if description:
            line += f" — {description}"
        lines.append(line)
    lines.append("Action results are shown in the status button beneath the controls.")
    return "\n".join(lines)


def _plugin_pmf_entries(name):
    """Return PMF help metadata with the live state of each toggle."""
    try:
        from pyUltroid.ultroid import core

        entries = core.manifest.get_entries(name)
        for entry in entries:
            if entry.get("type") == "toggle":
                entry["state"] = core.manifest.get_state(entry.get("toggle_key", ""))
        return entries
    except (ImportError, AttributeError, KeyError, TypeError, ValueError):
        return []


def _plugin_help_markup(
    name, entries=(), patterns=(), category="Plugins", pmf_entries=()
):
    """Build one plugin reference with collapsible rich Docs and PMF guidance."""
    commands = list(
        dict.fromkeys(command_syntax(pattern, HNDLR) for pattern in patterns or ())
    )
    pmf_entries = list(pmf_entries or ())
    total_handlers = len(commands) + len(pmf_entries)
    body = "".join(str(entry) for entry in entries or ())
    blocks = (
        RichBlocks(format="html")
        .header(_plugin_title(name), level=2)
        .paragraph("Browse this plugin's command handlers, documentation, and controls.")
    )
    overview = RichBlocks(format="html")
    overview.header("Overview", level=2)
    contains = f"{len(commands)} command(s)"
    if pmf_entries:
        contains += f"; {len(pmf_entries)} PMF control(s)"
    overview.table(
        [
            ["Section name", "Total handlers", "Contains"],
            [str(category), str(total_handlers), contains],
        ],
        caption="Plugin overview",
        is_bordered=True,
        is_striped=True,
    )
    detail_blocks = RichBlocks(format="html")
    if commands:
        detail_blocks.expandable("Commands", "\n".join(commands))
    if pmf_entries:
        detail_blocks.expandable("PMF guide", _pmf_guide_text(pmf_entries))
    if body.strip():
        converted = _markdown_docs_markup(body)
        body_html = converted.text if converted else html.escape(body)
    else:
        body_html = "No documentation is registered for this plugin."
    documentation = f"<blockquote expandable><b>Docs</b>\n{body_html}</blockquote>"
    if not commands:
        documentation += "<p>No commands are registered for this plugin.</p>"
    footer_total = total_handlers
    markup = rich_markup_from_blocks(
        normalize_blocks(blocks)
        + normalize_blocks(overview)
        + normalize_blocks(detail_blocks),
        format="html",
    )
    return RichMarkup(
        f"{markup.text}<hr>{documentation}"
        f"<hr><p><i>{_page_footer(1, 1, footer_total)}</i></p>",
        "html",
        markup.files,
    )


@ultroid_cmd(pattern="help( (.*)|$)")
async def _help(ult):
    plug = ult.pattern_match.group(1).strip()
    if plug:
        official_help = HELP.get("Official") or {}
        addons_help = HELP.get("Addons") or {}
        vcbot_help = HELP.get("VCBot") or {}
        try:
            help_maps = (official_help, addons_help, vcbot_help)
            plugin_name = next(
                (
                    name
                    for help_map in help_maps
                    for name in help_map
                    if name.casefold() == plug.casefold()
                ),
                next(
                    (name for name in LIST if name.casefold() == plug.casefold()), None
                ),
            )
            if plugin_name:
                category = next(
                    (
                        label
                        for label, help_map in (
                            ("Plugins", official_help),
                            ("Add-ons", addons_help),
                            ("Voice Chat", vcbot_help),
                        )
                        if plugin_name in help_map
                    ),
                    "Plugins",
                )
                entries = next(
                    (
                        help_map[plugin_name]
                        for help_map in help_maps
                        if plugin_name in help_map
                    ),
                    (),
                )
                output = _plugin_help_markup(
                    plugin_name,
                    entries=entries,
                    patterns=command_patterns(plugin_name, LIST),
                    category=category,
                    pmf_entries=_plugin_pmf_entries(plugin_name),
                )
                return await _edit_help(ult, output)

            file = next(
                (
                    file_name
                    for file_name, patterns in LIST.items()
                    if any(command_matches(pattern, plug) for pattern in patterns)
                ),
                None,
            )
            if file:
                category = next(
                    (
                        label
                        for label, help_map in (
                            ("Plugins", official_help),
                            ("Add-ons", addons_help),
                            ("Voice Chat", vcbot_help),
                        )
                        if file in help_map
                    ),
                    "Plugins",
                )
                entries = next(
                    (help_map[file] for help_map in help_maps if file in help_map),
                    (),
                )
                output = _plugin_help_markup(
                    file,
                    entries=entries,
                    patterns=command_patterns(file, LIST),
                    category=category,
                    pmf_entries=_plugin_pmf_entries(file),
                )
                return await _edit_help(ult, output)

            suggestions = sorted(
                {
                    name
                    for name in [
                        *LIST,
                        *(
                            alias
                            for patterns in LIST.values()
                            for p in patterns
                            for alias in command_aliases(p)
                        ),
                    ]
                    if plug.casefold() in name.casefold() and not name.startswith("_")
                },
                key=str.casefold,
            )
            output = f"<code>{html.escape(plug)}</code> is not a plugin or command."
            if suggestions:
                output += "\n\nDid you mean: " + ", ".join(
                    f"<code>{html.escape(item)}</code>" for item in suggestions[:5]
                )
            return await _edit_help(ult, output)
        except Exception as er:
            LOGS.exception(er)
            await _edit_help(ult, "<b>Help could not be opened.</b> Please try again.")
    else:
        inline_failed = False
        inline_reason = ""
        try:
            results = await ult.client.inline_query(asst.me.username, "ultd")
            if results:
                await results[0].click(
                    ult.chat_id, reply_to=ult.reply_to_msg_id, hide_via=False
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

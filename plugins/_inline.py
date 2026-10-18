# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

import asyncio
import html
import re
import time
from datetime import datetime
from math import ceil
from os import remove

from telethon import Button
from telethon.errors import BadRequestError, RPCError
from telethon.tl.types import (
    InputBotInlineMessageID,
    InputBotInlineMessageID64,
    InputWebDocument,
    Message,
)
from telethon.utils import get_display_name, resolve_bot_file_id

from pyUltroid._core import (
    InlineResultBuilder,
    build_menu_grid,
)
from pyUltroid._core._cbs import cb, cb_exact_pattern, cb_pattern
from pyUltroid._core.richmsg import (
    RichBlocks,
    RichMarkup,
    blocks_to_plain_text,
    edit_inline_plain,
    edit_rich,
    normalize_blocks,
    rich_markup_from_blocks,
)
from pyUltroid.dB._core import HELP, LIST
from pyUltroid.fns import fns
from pyUltroid.fns.command_help import command_patterns, command_syntax
from pyUltroid.ultroid import core

from . import (
    HNDLR,
    LOGS,
    OWNER_NAME,
    InlinePlugin,
    asst,
    callback,
    get_string,
    in_pattern,
    start_time,
    udB,
)
from ._help import (
    _help_blocks,
    _markdown_docs_markup,
    _page_footer,
    _plugin_pmf_entries,
    _pmf_guide_text,
    main_help_menu_buttons,
)

# ================================================#

helps = get_string("inline_1")

add_ons = udB.get_key("ADDONS")

zhelps = get_string("inline_3") if add_ons is False else get_string("inline_2")
PLUGINS = HELP.get("Official", [])
ADDONS = HELP.get("Addons", [])
upage = 0
INLINE_ALIVE_RESULT_ID = "alive-deploy"
INLINE_DEPLOY_SEND_DELAY_SECONDS = 3.5
# ============================================#

# --------------------BUTTONS--------------------#

SUP_BUTTONS = [
    [
        Button.url(
            get_string("btn_repo"), url="https://github.com/TeamUltroid/Ultroid"
        ),
        Button.url(get_string("btn_support"), url="t.me/UltroidSupportChat"),
    ],
]


def _ui_buttons_styled() -> bool:
    """Return ``True`` when ``.ui buttons`` is set to ``styled``.

    Isolated helper (rather than importing ``ui_buttons_style`` at every
    call site) so every styled/classic branch below degrades to classic
    the same way on any import/config error.
    """
    try:
        from ._rebrand import ui_buttons_style

        return ui_buttons_style() == "styled"
    except Exception:
        return False


def _btn(text: str, *, data=None, url=None, preset=None):
    """One inline button, styled or classic per ``.ui buttons`` — the single
    shared guard every button call site in this module goes through.

    Tries ``pyUltroid._core.buttonkit.button(text, data=data, url=url,
    preset=preset)`` when ``.ui buttons`` is ``styled``, falling back to
    the classic ``Button.url``/``Button.inline`` on any import/build error
    (or unconditionally when ``.ui buttons`` is ``classic``). Exactly one
    of *data*/*url* is expected, mirroring the underlying constructors;
    callback-data/URLs never change between the two branches — only the
    rendered style does.
    """
    if _ui_buttons_styled():
        try:
            from pyUltroid._core.buttonkit import button as _sbtn

            return _sbtn(text, data=data, url=url, preset=preset)
        except Exception:
            pass
    if url is not None:
        return Button.url(text, url=url)
    return Button.inline(text, data=data)


def _as_html(text):
    markup = _markdown_docs_markup(str(text))
    return markup.text if markup else html.escape(str(text))


def _catalog_help_blocks(key, index=0):
    """Build an overview-first catalog page with live totals and pagination."""
    catalogs = HELP.get(key) or {}
    title = {
        "Official": "Core plugin library",
        "Addons": "Add-on library",
        "VCBot": "Voice chat library",
    }.get(key, "Plugin library")
    handlers = sum(
        len(command_patterns(name, LIST)) + len(_plugin_pmf_entries(name))
        for name in catalogs
    )
    try:
        rows = max(1, int(udB.get_key("HELP_ROWS") or 5))
        cols = max(1, int(udB.get_key("HELP_COLUMNS") or 2))
    except (TypeError, ValueError):
        rows, cols = 5, 2
    page_size = rows * cols
    page_count = max(1, ceil(len(catalogs) / page_size))
    try:
        page = max(0, min(int(index), page_count - 1))
    except (TypeError, ValueError):
        page = 0
    header = RichBlocks(format="html")
    header.header(title, level=2)
    header.paragraph(
        "Browse this library to open a plugin's handlers, documentation, and controls."
    )
    overview = RichBlocks(format="html")
    overview.header("Overview", level=2)
    overview.table(
        [
            ["Section name", "Total handlers", "Contains"],
            [title, str(handlers), f"{len(catalogs)} plugin(s)"],
        ],
        caption="Library overview",
        is_bordered=True,
        is_striped=True,
    )
    if not catalogs:
        overview.paragraph("No modules are currently available in this library.")
    markup = rich_markup_from_blocks(
        normalize_blocks(header) + normalize_blocks(overview), format="html"
    )
    return RichMarkup(
        f"{markup.text}<hr><p><i>{_page_footer(page + 1, page_count, len(catalogs))}</i></p>",
        "html",
        markup.files,
    )


def _plugin_has_pmf_or_module(name):
    """Whether a plugin has PMF controls or a class on the Module layer."""
    manifest = getattr(core, "manifest", None)
    if manifest is not None and manifest.get_entries(name):
        return True
    module_registry = getattr(core, "modules", None)
    modules = getattr(module_registry, "modules", {}) or {}
    module_path = str(name).replace("\\", "/").removesuffix(".py").replace("/", ".")
    return any(
        type(module).__module__ == module_path
        or type(module).__module__.startswith(f"{module_path}.")
        for module in modules.values()
    )


def _plugin_card_markup(file, key, body, patterns, pmf_entries=()):
    """Assemble a native rich plugin reference with collapsible Docs and PMF."""
    commands = list(
        dict.fromkeys(command_syntax(pattern, HNDLR) for pattern in patterns or ())
    )
    pmf_entries = list(pmf_entries or ())
    total_handlers = len(commands) + len(pmf_entries)
    category = {
        "Official": "Core plugins",
        "Addons": "Add-ons",
        "VCBot": "Voice chat",
    }.get(key, str(key))
    header = RichBlocks(format="html")
    header.header(_plugin_label(file), level=2)
    header.paragraph(
        "Browse this plugin's command handlers, documentation, and controls."
    )
    overview = RichBlocks(format="html")
    overview.header("Overview", level=2)
    contains = f"{len(commands)} command(s)"
    if pmf_entries:
        contains += f"; {len(pmf_entries)} PMF control(s)"
    overview.table(
        [
            ["Section name", "Total handlers", "Contains"],
            [category, str(total_handlers), contains],
        ],
        caption="Plugin overview",
        is_bordered=True,
        is_striped=True,
    )
    details = RichBlocks(format="html")
    if commands:
        details.expandable("Commands", "\n".join(commands))
    if pmf_entries:
        details.expandable("PMF guide", _pmf_guide_text(pmf_entries))
    markup = rich_markup_from_blocks(
        normalize_blocks(header)
        + normalize_blocks(overview)
        + normalize_blocks(details),
        format="html",
    )
    documentation = _as_html(body) if body else "No documentation is registered."
    docs_html = f"<blockquote expandable><b>Docs</b>\n{documentation}</blockquote>"
    return RichMarkup(
        f"{markup.text}<hr>{docs_html}<hr><p><i>{_page_footer(1, 1, total_handlers)}</i></p>",
        "html",
        markup.files,
    )


async def _edit_rich_callback(event, content, buttons):
    """Edit a normal or inline callback message with a native RichMessage."""
    query = getattr(event, "query", None)
    inline_id = getattr(query, "msg_id", None)
    inline_types = {"InputBotInlineMessageID", "InputBotInlineMessageID64"}
    message = getattr(event, "message_id", None)
    if type(message).__name__ in inline_types:
        peer, message = message, None
    elif type(inline_id).__name__ in inline_types:
        peer, message = inline_id, None
    else:
        if message is None:
            message = inline_id
        peer = getattr(event, "chat_id", None)
    return await edit_rich(
        event.client,
        peer,
        message,
        content,
        format="html",
        buttons=buttons,
        link_preview=False,
    )


def _styled_sup_buttons():
    """Styled variant of :data:`SUP_BUTTONS` — same links, ``primary`` chips."""
    return [
        [
            _btn(
                get_string("btn_repo"),
                url="https://github.com/TeamUltroid/Ultroid",
                preset="primary",
            ),
            _btn(
                get_string("btn_support"),
                url="t.me/UltroidSupportChat",
                preset="primary",
            ),
        ],
    ]


def _sup_buttons():
    """Return :data:`SUP_BUTTONS`, styled per ``.ui buttons`` when possible."""
    if _ui_buttons_styled():
        try:
            return _styled_sup_buttons()
        except Exception:
            return SUP_BUTTONS
    return SUP_BUTTONS


# --------------------BUTTONS--------------------#


def _alive_rich_markup(invoker_name=None):
    """Build the Ultroid status/deploy card directly as RichMessage HTML."""
    uptime = fns.time_formatter((time.time() - start_time) * 1000)
    official_count = len(HELP.get("Official") or {})
    addon_count = len(HELP.get("Addons") or {})
    rows = (
        ("Owner", OWNER_NAME),
        ("Uptime", uptime),
        ("Plugins", str(official_count)),
        ("Addons", str(addon_count)),
    )
    parts = ["<h1>Ultroid · Alive</h1>", "<hr>"]
    if invoker_name:
        parts.append(
            f"<p>Hey {html.escape(str(invoker_name))}, want to deploy your own? "
            "Tap <b>Hi</b> to continue.</p>"
        )
    parts.append('<table class="bordered compact"><tr><th>Item</th><th>Details</th></tr>')
    parts.extend(
        f"<tr><td>{html.escape(label)}</td><td>{html.escape(value)}</td></tr>"
        for label, value in rows
    )
    parts.append(f"</table><hr><p><i>page 1 of 1 • total {len(rows)}</i></p>")
    return RichMarkup("".join(parts), "html")


def _deploy_guide_blocks(invoker_name):
    """Build real nested Layer-230 details, code, tables and in-body buttons.

    PageBlockButtonRow is a PageBlock, so it belongs in Details.blocks, not
    in the message's separate reply_markup. All copy payloads are examples.
    """
    from telethon.tl import types

    repo = "https://github.com/Mahesh953-hub/SarahUB"
    docs = repo + "/blob/main/docs/SETUP.md"

    def text(value):
        return types.TextPlain(str(value))

    def paragraph(value):
        return types.PageBlockParagraph(text(value))

    def url(label, target):
        return types.PageButton(text(label), types.InlineButtonTypeUrl(target))

    def buttons(*items):
        return types.PageBlockButtonRow(list(items))

    def code(value, *, language="bash", label="Copy command"):
        return [
            types.PageBlockPreformatted(text(value), language),
            buttons(types.PageButton(text(label), types.InlineButtonTypeCopy(value))),
        ]

    def details(title, children, *, opened=False):
        return types.PageBlockDetails(children, text(title), open=opened)

    def linked(label, target):
        return types.TextUrl(text(label), target, 0)

    env = (
        "API_ID=YOUR_API_ID\n"
        "API_HASH=YOUR_API_HASH\n"
        "SESSION=\n"
        "#-- OPTIONAL\n"
        "LOG_CHANNEL=\n"
        "BOT_TOKEN=\n"
        "MONGO_URI="
    )
    sources = [
        ("API_ID", "Create an app in Telegram API development tools.",
         "Telegram apps", "https://my.telegram.org/apps"),
        ("API_HASH", "Use the hash from that same Telegram app; keep it private.",
         "Telegram apps", "https://my.telegram.org/apps"),
        ("SESSION", "Leave blank for first-run private OTP/QR setup; it saves your session locally.",
         "Private setup guide", docs + "#web-setup-server"),
        ("LOG_CHANNEL", "Optional numeric ID of a group/channel you control; allow the bot to post.",
         "Telegram IDs", "https://core.telegram.org/api/peers#peer-id"),
        ("BOT_TOKEN", "Optional assistant token: create your own bot with BotFather.",
         "BotFather", "https://t.me/BotFather"),
        ("MONGO_URI", "Optional connection URI from your own MongoDB deployment; includes credentials.",
         "MongoDB Atlas", "https://www.mongodb.com/docs/atlas/driver-connection/"),
    ]
    rows = [types.PageTableRow([
        types.PageTableCell(text=text(label), header=True)
        for label in ("Variable", "Where to obtain it", "Open")
    ])]
    rows.extend(types.PageTableRow([
        types.PageTableCell(text=types.TextFixed(text(name))),
        types.PageTableCell(text=text(description)),
        types.PageTableCell(text=linked(label, target)),
    ]) for name, description, label, target in sources)
    obtain = [types.PageBlockTable(
        text("Where each value comes from"), rows, bordered=True, striped=True, compact=True,
    )]
    obtain.extend([
        buttons(url("API ID / hash", "https://my.telegram.org/apps"),
                url("Session setup", docs + "#web-setup-server")),
        buttons(url("Channel IDs", "https://core.telegram.org/api/peers#peer-id"),
                url("BotFather", "https://t.me/BotFather")),
        buttons(url("MongoDB URI", "https://www.mongodb.com/docs/atlas/driver-connection/")),
    ])

    steps = [
        details("Step 1 · Clone the repository", [
            paragraph("Use Git and Python 3.10+ (3.12 recommended). Private repositories require "
                      "GitHub access; authenticate securely, never paste a token into the clone URL."),
            *code("git clone https://github.com/Mahesh953-hub/SarahUB.git Ultroid\ncd Ultroid",
                  label="Copy clone commands"),
            buttons(url("Repository", repo), url("Setup guide", docs)),
        ], opened=True),
        details("Step 2 · Install requirements", [
            paragraph("Choose pip OR uv, not both. Run commands from the Ultroid folder on Linux/macOS."),
            details("2a · pip", [
                *code("python3 -m venv .venv\nsource .venv/bin/activate\n"
                      "python -m pip install --upgrade pip\npython -m pip install -r requirements.txt",
                      label="Copy pip install"),
            ]),
            details("2b · uv", [
                paragraph("Install uv first if needed, then create the environment and install the project."),
                *code("uv venv --python 3.12\nuv pip install -e .", label="Copy uv install"),
                buttons(url("Install uv", "https://docs.astral.sh/uv/getting-started/installation/")),
            ]),
        ]),
        details("Step 3 · Fill in your variables", [
            paragraph("Create a private .env file in the repository root, then replace YOUR_API_ID and "
                      "YOUR_API_HASH. SESSION may stay blank for first-run setup. Optional values may stay blank."),
            *code("umask 077\ntouch .env\nchmod 600 .env\nnano .env", label="Copy .env preparation"),
            *code(env, language="dotenv", label="Copy variable template"),
            details("3a · Where to obtain every value", obtain),
        ]),
        details("Step 4 · Start Ultroid", [
            paragraph("Choose the launch method matching your install. Only run one instance per session."),
            details("4a · Run as a Python module", [
                *code("source .venv/bin/activate\npython3 -m pyUltroid", label="Copy module start"),
            ], opened=True),
            details("4b · Run with uv", [
                *code("uv run --no-sync python3 -m pyUltroid", label="Copy uv start"),
            ], opened=True),
        ]),
        details("Step 5 · Complete private first-run login", [
            paragraph("If SESSION is blank, open the private setup URL printed in your terminal. "
                      "Use the setup token shown there and complete phone OTP or QR login. "
                      "The setup saves the session in your local .env. Keep the URL/token private; "
                      "do not expose an unprotected setup server to the Internet."),
            paragraph("Wait for startup to finish, then test your configured commands in Saved Messages. "
                      "If switching to a background launch, stop this foreground instance first with Ctrl+C."),
            buttons(url("First-run setup", docs + "#web-setup-server")),
        ]),
        details("Step 6 · Keep it running", [
            paragraph("Choose ONE method after successful setup and after stopping the foreground worker. "
                      "These keep it alive after SSH logout, not after a server reboot or crash; "
                      "use a properly configured systemd service for automatic restart. "
                      "Protect log files because they can contain private setup details."),
            details("6a · nohup", [
                *code("umask 077\nnohup .venv/bin/python -m pyUltroid > ultroid.log 2>&1 < /dev/null &",
                      label="Copy nohup start"),
            ]),
            details("6b · screen", [
                paragraph("Requires screen installed. Detach with Ctrl+A, then D; reattach with the command below. "
                          "Do not launch a second worker when reconnecting."),
                *code("screen -S ultroid .venv/bin/python -m pyUltroid", label="Copy screen start"),
                *code("screen -r ultroid", label="Copy screen reattach"),
            ]),
        ]),
        details("Step 7 · Use custom addons", [
            paragraph("Only load code you trust: addons execute with your account's permissions. "
                      "Telegram command examples use the default dot prefix; replace it if you changed yours."),
            details("7a · Enable addons", [
                paragraph("Add this setting to .env, then restart your existing worker once. "
                          "An existing database ADDONS setting can override the environment."),
                *code("ADDONS=true", language="dotenv", label="Copy addon setting"),
                buttons(url("Default addon repository", "https://github.com/Mahesh953-hub/MYADDONS")),
            ], opened=True),
            details("7b · Use a channel", [
                paragraph("Join your trusted channel with the user account and upload addon .py files there. "
                          "Run this in Telegram, replacing the example channel. "
                          "PLUGIN_CHANNEL is a database list, not an .env variable. "
                          "Channel loading requires a user account; it is unavailable in bot-only mode."),
                *code('.setdb PLUGIN_CHANNEL ["@your_addon_channel"]\n.restart',
                      language="text", label="Copy channel commands"),
                buttons(url("Channel loader source", repo + "/blob/main/pyUltroid/startup/funcs.py")),
            ], opened=True),
            details("7c · Use a repository", [
                paragraph("Before the first addon clone, set ADDONS_URL in Telegram to a trusted repository. "
                          "Replace OWNER/ADDONS_REPO. This is a database key, not an .env variable. "
                          "If addons/ already exists, this setting does not replace it: preserve local work "
                          "and migrate deliberately. For private access configure GIT_TOKEN securely in your "
                          "environment, never in a Telegram command or repository URL."),
                *code(".setdb ADDONS_URL https://github.com/OWNER/ADDONS_REPO.git\n.restart",
                      language="text", label="Copy repository commands"),
                buttons(url("Addon loader source", repo + "/blob/main/pyUltroid/startup/loader.py")),
            ], opened=True),
        ]),
    ]
    return types.InputRichMessage(blocks=[
        types.PageBlockParagraph(types.TextBold(text("Deploy your own Ultroid"))),
        paragraph(f"Hey {invoker_name}! Browse these steps to deploy your own Ultroid."),
        types.PageBlockDivider(),
        *steps,
        types.PageBlockDivider(),
        types.PageBlockParagraph(types.TextMarked(types.TextBold(text(
            "NEVER share your SESSION string or MongoDB MONGO_URI. They can grant access to your "
            "account and database. Also keep API_HASH, BOT_TOKEN, OTPs and .env private; "
            "never commit them to Git."
        )))),
        types.PageBlockParagraph(types.TextItalic(text("page 1 of 1 • total 7"))),
    ])


def _is_deploy_inline_send(update):
    """Accept only the empty-query send of the alive/deploy inline result."""
    inline_id = getattr(update, "msg_id", None)
    return (
        not (getattr(update, "query", "") or "").strip()
        and getattr(update, "id", None) == INLINE_ALIVE_RESULT_ID
        and type(inline_id).__name__
        in {"InputBotInlineMessageID", "InputBotInlineMessageID64"}
    )


async def _handle_deploy_inline_send(update, client=None):
    """Wait for Telegram's inline-send update, then personalize its exact message."""
    if not _is_deploy_inline_send(update):
        return None
    client = client or asst
    await asyncio.sleep(INLINE_DEPLOY_SEND_DELAY_SECONDS)
    full_name = "there"
    user_id = getattr(update, "user_id", None)
    if user_id:
        try:
            sender = await client.get_entity(int(user_id))
            full_name = get_display_name(sender) or getattr(sender, "username", None) or "there"
        except Exception as exc:
            LOGS.debug("[inline_deploy] invoker lookup failed (%s)", type(exc).__name__)
    return await edit_rich(
        client,
        update.msg_id,
        None,
        _alive_rich_markup(full_name),
        format="html",
        buttons=[[_btn("Hi", data="alive_deploy_hi", preset="success")]],
        link_preview=False,
    )


@in_pattern(owner=True, func=lambda x: not x.text)
async def inline_alive(o):
    builder = InlineResultBuilder(o)
    # Uptime, plugin totals and controls are live: neither the process nor
    # Telegram may reuse an earlier snapshot. Keep the chosen-result routing ID.
    article = await builder.article_rich(
        title="Ultroid Userbot",
        payload=_alive_rich_markup(),
        format="html",
        id=INLINE_ALIVE_RESULT_ID,
        description="Userbot | Telethon",
        buttons=_sup_buttons(),
    )
    await builder.answer(
        [article] if article is not None else [],
        private=True,
        cache_time=0,
        switch_pm="Ultroid Portal",
        switch_pm_param="start",
    )


@in_pattern("ultd", owner=True)
async def inline_handler(event):
    builder = InlineResultBuilder(event)
    # Counts alone cannot invalidate renamed commands, new help text or settings.
    # Rebuild this small live menu; leave unrelated inline/media caches intact.
    result = await builder.article_rich(
        title="Bot Help",
        payload=_help_blocks(),
        format="html",
        buttons=main_help_menu_buttons(),
    )
    await builder.answer(
        [result] if result is not None else [],
        private=True,
        cache_time=0,
        gallery=True,
    )


@in_pattern("pasta", owner=True)
async def _(event):
    ok = event.text.split("-")[1]
    link = f"https://spaceb.in/{ok}"
    raw = f"https://spaceb.in/api/{ok}/raw"
    result = await InlineResultBuilder(event).article(
        title="Paste",
        text="Pasted to Spacebin.",
        buttons=[
            [
                Button.url("SpaceBin", url=link),
                Button.url("Raw", url=raw),
            ],
        ],
    )
    await event.answer([result])


@callback("ownr", owner=True)
async def setting(event):
    await _edit_rich_callback(
        event,
        _help_blocks(),
        [
            [
                _btn("Ping", data="pkng"),
                _btn("Uptime", data="upp"),
            ],
            [
                _btn("Stats", data="alive"),
                _btn("Update", data="doupdate"),
            ],
            [_btn(get_string("btn_back"), data=cb("assistant", "inline", "main"))],
        ],
    )


_strings = {"Official": helps, "Addons": zhelps, "VCBot": get_string("inline_6")}


@callback(cb_pattern("assistant", "inline", "help_menu"), owner=True)
async def help_func(ult):
    key, count = ult.data_match.group(1).decode("utf-8").split(":", 1)
    if key == "VCBot" and HELP.get("VCBot") is None:
        return await ult.answer(get_string("help_12"), alert=True)
    elif key == "Addons" and HELP.get("Addons") is None:
        return await ult.answer(get_string("help_13").format(HNDLR), alert=True)
    count = int(count) if count else 0
    await _edit_rich_callback(
        ult,
        _catalog_help_blocks(key, count),
        page_num(count, key),
    )


@callback(cb_pattern("assistant", "inline", "plugin"), owner=True)
async def uptd_plugin(event):
    rem = event.data_match.group(1).decode("utf-8")
    key, _, rest = rem.partition(":")
    file, _, index = rest.partition(":")
    await _show_plugin(event, key, file, index or None)


async def _show_plugin(event, key, file, index):
    key_ = HELP.get(key, [])
    help_ = ""
    try:
        for i in key_[file]:
            help_ += i
    except Exception:
        help_ = ""
    patterns = command_patterns(file, LIST)
    rich_help_ = None
    try:
        from pyUltroid.ultroid import core
    except Exception:
        core = None
    if core is not None:
        try:
            candidate = core.plugin_help.get_rich(file)
            if candidate and candidate != core.plugin_help.get(file):
                rich_help_ = candidate
        except Exception:
            rich_help_ = None
    buttons = []
    pmf_entries = _plugin_pmf_entries(file) if core is not None else []
    data = cb("assistant", "inline", "send_plugin", key, file, index)
    buttons.append(
        [
            _btn(
                get_string("btn_send_plugin"),
                data=data,
                preset="primary",
            )
        ]
    )
    # --- Plugin manifest buttons ---
    styled = _ui_buttons_styled()
    if core is not None:
        try:
            entries = pmf_entries
            for ent in entries:
                etype = ent.get("type")
                tkey = ent.get("toggle_key", "")
                label = ent.get("label", "")

                if etype == "toggle":
                    state = core.manifest.get_state(tkey)
                    btn_label = f"{label}: {'On' if state else 'Off'}"
                    buttons.append(
                        [
                            _btn(
                                btn_label,
                                data=f"pltog_{tkey}",
                                preset="success" if state and styled else None,
                            )
                        ]
                    )

                elif etype == "action":
                    buttons.append([_btn(label, data=f"plact_{tkey}")])

                elif etype == "manifest_button":
                    color = ent.get("color", "blue")
                    preset = {
                        "green": "success",
                        "red": "danger",
                        "blue": "primary",
                        "grey": None,
                    }.get(str(color).lower())
                    if not _ui_buttons_styled():
                        preset = None
                    buttons.append([_btn(label, data=f"plman_{tkey}", preset=preset)])

            ph = core.manifest.get_placeholder(file)
            if entries:
                buttons.append([_placeholder_button(ph)])
        except Exception:
            pass
    # --- End manifest buttons ---
    data = cb(
        "assistant",
        "inline",
        "help_menu",
        key,
        str(index) if index is not None else "0",
    )
    buttons.append(
        [
            _btn(
                f"Back to { {'Official': 'Plugins', 'Addons': 'Add-ons', 'VCBot': 'Voice Chat'}.get(key, key) }",
                data=data,
                preset="primary",
            ),
        ]
    )
    try:
        await _edit_rich_callback(
            event,
            _plugin_card_markup(
                file,
                key,
                rich_help_ or help_,
                patterns,
                pmf_entries=pmf_entries,
            ),
            buttons,
        )
    except Exception as er:
        LOGS.exception(er)
        fallback = (
            RichBlocks(format="html")
            .header("Plugin guide unavailable", level=2)
            .paragraph(
                "The guide could not be rendered in place. Use this command instead:"
            )
            .code(f"{HNDLR}help {key}")
        )
        await _edit_rich_callback(event, fallback, buttons)


# Manifest callbacks


def _button_data(button):
    """Read callback data from both legacy and layer-229 inline buttons."""
    data = getattr(button, "data", None)
    if data is not None:
        return data
    return getattr(getattr(button, "type", None), "data", None)


def _rebuild_buttons(buttons, data_str: str, replacement):
    """Replace the button whose ``data`` matches *data_str* with *replacement*."""
    if not buttons:
        return None
    new_buttons = []
    for row in buttons:
        new_row = []
        for btn in row:
            data = _button_data(btn)
            if data and data.decode("utf-8") == data_str:
                new_row.append(replacement)
            else:
                new_row.append(btn)
        new_buttons.append(new_row)
    return new_buttons


def _placeholder_button(ph: dict):
    """Build the placeholder button for the current ``ph`` state.

    Mirrors ``core.manifest.set_placeholder``'s precedence: error
    (``{^}`` + truncated text) > URL button > copy-text button >
    idle "ready" button — styled via :func:`_btn` when ``.ui buttons`` is
    ``styled`` (falls back to classic on any error), otherwise always the
    classic button. Callback-data (``plph_info``/``plph_copy``) and URLs
    never change either way.
    """
    ph_text = ph.get("text") or ph.get("url")
    preset = ph.get("preset")
    if ph.get("is_error"):
        label = f"{{^}} {ph_text[:40]}" if ph_text else "{^}"
        return _btn(label, data="plph_info", preset=preset or "danger")
    if ph.get("url"):
        return _btn(ph_text[:60] or "Open", url=ph["url"], preset=preset or "primary")
    if ph.get("text"):
        return _btn(ph_text[:40], data="plph_copy", preset=preset or "default")
    return _btn(get_string("btn_ready"), data="plph_info", preset=preset or "success")


@callback(re.compile(rb"^pltog_(.*)"), owner=True)
async def _cb_toggle(event):
    """Flip a toggle, call handler, update button label and placeholder."""
    # Bugfix (pre-existing, unrelated to this session's C1/C2/C3 work):
    # `^pltog_` has no capturing group, so `.group(0)` was only ever the
    # literal 6-char prefix "pltog_" itself (never the rest of the data) —
    # `toggle_key` was always "". Capturing the remainder and reading
    # `.group(1)` fixes extraction without changing the `pltog_` callback
    # data prefix any button emits.
    toggle_key = event.data_match.group(1).decode("utf-8")
    new_state, alert_msg = await core.manifest.flip_toggle(toggle_key)
    if alert_msg:
        await event.answer(alert_msg, alert=True)
    # Update button label.
    msg = await event.get_message()
    buttons = msg.buttons if msg else None
    entry = core.manifest.get_entry(toggle_key)
    if entry:
        label = entry.get("label", "")
        new_label = f"{label}: {'On' if new_state else 'Off'}"
        replacement = _btn(
            new_label,
            data=f"pltog_{toggle_key}",
            preset="success" if new_state and _ui_buttons_styled() else None,
        )
        rebuilt = _rebuild_buttons(buttons, f"pltog_{toggle_key}", replacement)
        if rebuilt:
            # Update placeholder too.
            core.manifest.set_placeholder(
                entry.get("plugin", ""),
                text=new_label,
            )
            if buttons:
                ph_btn = _placeholder_button(
                    core.manifest.get_placeholder(entry.get("plugin", ""))
                )
                for row in rebuilt:
                    for i, btn in enumerate(row):
                        data = _button_data(btn)
                        if data and data.decode("utf-8") == "plph_copy":
                            row[i] = ph_btn
                            break
            await event.edit(buttons=rebuilt)


@callback(re.compile(rb"^plact_(.*)"), owner=True)
async def _cb_action(event):
    """Run a one-shot action, update placeholder with result."""
    # Bugfix — see the matching comment in `_cb_toggle` above.
    toggle_key = event.data_match.group(1).decode("utf-8")
    success, output = await core.manifest.run_action(toggle_key, event)
    entry = core.manifest.get_entry(toggle_key)
    plugin = entry.get("plugin", "") if entry else ""
    if success and output:
        core.manifest.set_placeholder(plugin, text=output)
    else:
        core.manifest.set_placeholder(plugin, text="", is_error=not success)
        if not success:
            await event.answer("Action failed — see logs.", alert=True)
        else:
            await event.answer("Done.", alert=False)
    # Rebuild placeholder button in the message.
    ph = core.manifest.get_placeholder(plugin)
    msg = await event.get_message()
    buttons = msg.buttons if msg else None
    if buttons:
        ph_btn = _placeholder_button(ph)
        rebuilt = []
        for row in buttons:
            new_row = []
            for btn in row:
                data = _button_data(btn)
                if data and data.decode("utf-8") in ("plph_info", "plph_copy"):
                    new_row.append(ph_btn)
                else:
                    new_row.append(btn)
            rebuilt.append(new_row)
        await event.edit(buttons=rebuilt)


@callback(re.compile(rb"^plman_(.*)"), owner=True)
async def _cb_manifest(event):
    """Dispatch a manifest button click to its @handle_manifest handler."""
    # Bugfix — see the matching comment in `_cb_toggle` above.
    toggle_key = event.data_match.group(1).decode("utf-8")
    success, output = await core.manifest.run_action(toggle_key, event)
    entry = core.manifest.get_entry(toggle_key)
    plugin = entry.get("plugin", "") if entry else ""
    if success and output:
        core.manifest.set_placeholder(plugin, text=output)
    else:
        core.manifest.set_placeholder(plugin, text="", is_error=not success)
        if not success:
            await event.answer("Action failed — see logs.", alert=True)
        elif output is None:
            await event.answer("Done.", alert=False)
    # Rebuild placeholder.
    ph = core.manifest.get_placeholder(plugin)
    msg = await event.get_message()
    buttons = msg.buttons if msg else None
    if buttons:
        ph_btn = _placeholder_button(ph)
        rebuilt = []
        for row in buttons:
            new_row = []
            for btn in row:
                data = _button_data(btn)
                if data and data.decode("utf-8") in ("plph_info", "plph_copy"):
                    new_row.append(ph_btn)
                else:
                    new_row.append(btn)
            rebuilt.append(new_row)
        await event.edit(buttons=rebuilt)


@callback(data="plph_copy")
async def _cb_ph_copy(event):
    """Copy placeholder text (no-op — Telegram doesn't support clipboard)."""
    await event.answer("This is a placeholder button!", alert=True)


@callback(data="plph_info")
async def _cb_ph_info(event):
    """Placeholder info click."""
    await event.answer("This is a placeholder button!", alert=True)


@callback(data="doupdate", owner=True)
async def _(event):
    # HSU (W1-B): the UpdateEngine check is the only update path.
    from pyUltroid.updater import up as update_engine

    diff = await update_engine.engine.check()
    if not (diff and diff.has_changes):
        return await event.answer(get_string("inline_9"), cache_time=0, alert=True)
    changelog_str = (
        f"**Ultroid updates for [{diff.branch}]:**\n\n"
        f"{diff.changelog_text(10)}\n\n{diff.impact_summary()}\n\n"
        + get_string("inline_8")
    )
    buttons = [
        [_btn(get_string("btn_update_now"), data="upd_apply")],
        [_btn(get_string("btn_back"), data="ownr")],
    ]
    if len(changelog_str) > 1024:
        # Too long for a message with buttons — attach the full log as a file.
        await event.edit(get_string("upd_4"))
        with open("ultroid_updates.txt", "w+") as file:
            file.write(diff.changelog_text(max_entries=len(diff.commits)))
        await event.edit(
            get_string("upd_5"),
            file="ultroid_updates.txt",
            buttons=buttons,
        )
        remove("ultroid_updates.txt")
    else:
        await event.edit(changelog_str, buttons=buttons)


@callback(data="pkng", owner=True)
async def _(event):
    start = datetime.now()
    end = datetime.now()
    ms = (end - start).microseconds
    pin = f"Ping: {ms} microseconds"
    await event.answer(pin, cache_time=0, alert=True)


@callback(data="upp", owner=True)
async def _(event):
    uptime = fns.time_formatter((time.time() - start_time) * 1000)
    pin = f"Uptime: {uptime}"
    await event.answer(pin, cache_time=0, alert=True)


@callback(data="inlone", owner=True)
async def _(e):
    _InButtons = [
        Button.switch_inline(_, query=InlinePlugin[_], same_peer=True)
        for _ in list(InlinePlugin.keys())
    ]
    InButtons = fns.split_list(_InButtons, 2)

    button = InButtons.copy()
    button.append(
        [
            _btn(get_string("btn_back"), data=cb("assistant", "inline", "main")),
        ],
    )
    await e.edit(buttons=button, link_preview=False)


@callback(cb_exact_pattern("assistant", "inline", "main"), owner=True)
async def opner(event):
    await _edit_rich_callback(
        event,
        _help_blocks(),
        main_help_menu_buttons(),
    )


@callback(data="close", owner=True)
async def on_plug_in_callback_query_handler(event):
    await event.edit(
        get_string("inline_5"),
        buttons=_btn(
            get_string("btn_open_again"), data=cb("assistant", "inline", "main")
        ),
    )


def _page_num_back_button(key=None):
    """The neutral Back button when there is only one plugin page."""
    return _btn(
        get_string("btn_back_page"),
        data=cb("assistant", "inline", "main"),
        preset=None,
    )


def _page_num_nav_row(key, index):
    """Prev / Back / Next nav row with neutral Back and primary paging controls."""
    return [
        _btn(
            get_string("btn_previous"),
            data=cb("assistant", "inline", "help_menu", key, index - 1),
        ),
        _btn(
            get_string("btn_back_page"),
            data=cb("assistant", "inline", "main"),
            preset=None,
        ),
        _btn(
            get_string("btn_next"),
            data=cb("assistant", "inline", "help_menu", key, index + 1),
        ),
    ]


def _plugin_label(key):
    """Display name for a help-registry key: ``plugins.afk`` -> ``Afk``."""
    stem = key.split(".", 1)[-1].replace("_", " ").strip()
    return stem.title() or key


def page_num(index, key):
    rows = udB.get_key("HELP_ROWS") or 5
    cols = udB.get_key("HELP_COLUMNS") or 2
    loaded = HELP.get(key, [])
    List = [
        _btn(
            _plugin_label(x),
            data=cb("assistant", "inline", "plugin", key, x, index),
            preset="success" if _plugin_has_pmf_or_module(x) else None,
        )
        for x in sorted(loaded)
    ]
    # W2-D: shared grid builder (equivalent to split_list(List, cols)).
    all_ = build_menu_grid(List, cols)
    fl_ = fns.split_list(all_, rows)
    try:
        new_ = fl_[index]
    except IndexError:
        new_ = fl_[0] if fl_ else []
        index = 0
    if index == 0 and len(fl_) == 1:
        new_.append([_page_num_back_button(key)])
    else:
        new_.append(_page_num_nav_row(key, index))
    return new_


# --------------------------------------------------------------------------------- #


STUFF = {}


@in_pattern("stf(.*)", owner=True)
async def ibuild(e):
    n = e.pattern_match.group(1).strip()
    builder = InlineResultBuilder(e)
    if not (n and n.isdigit()):
        return
    ok = STUFF.get(int(n))
    txt = ok.get("msg")
    pic = ok.get("media")
    btn = ok.get("button")
    if not (pic or txt):
        txt = "Hey!"
    if pic:
        try:
            include_media = True
            mime_type, _pic = None, None
            cont, results = None, None
            try:
                ext = str(pic).split(".")[-1].lower()
            except Exception:
                ext = None
            if ext in ["img", "jpg", "png"]:
                _type = "photo"
                mime_type = "image/jpg"
            elif ext in ["mp4", "mkv", "gif"]:
                mime_type = "video/mp4"
                _type = "gif"
            else:
                try:
                    if "telethon.tl.types" in str(type(pic)):
                        _pic = pic
                    else:
                        _pic = resolve_bot_file_id(pic)
                except Exception:
                    pass
                if _pic:
                    # InlineResultBuilder returns None on build failure;
                    # fall through to the article branch like a missing id.
                    document = await builder.document(
                        file=_pic,
                        title="Ultroid Op",
                        text=txt,
                        description="@TeamUltroid",
                        buttons=btn,
                        link_preview=False,
                    )
                    if document is not None:
                        results = [document]
                if not results:
                    _type = "article"
                    include_media = False
            if not results:
                if include_media:
                    cont = InputWebDocument(pic, 0, mime_type, [])
                article = await builder.article(
                    title="Ultroid Op",
                    type=_type,
                    text=txt,
                    description="@TeamUltroid",
                    include_media=include_media,
                    buttons=btn,
                    thumb=cont,
                    content=cont,
                    link_preview=False,
                )
                if article is not None:
                    results = [article]
            if results:
                return await e.answer(results)
        except Exception as er:
            LOGS.exception(er)
    result = await builder.article(
        title="Ultroid Op", text=txt, link_preview=False, buttons=btn
    )
    await e.answer([result] if result is not None else [])


async def something(e, msg, media, button, reply=True, chat=None):
    if e.client._bot:
        return await e.reply(msg, file=media, buttons=button)
    num = len(STUFF) + 1
    STUFF.update({num: {"msg": msg, "media": media, "button": button}})
    try:
        res = await e.client.inline_query(asst.me.username, f"stf{num}")
        return await res[0].click(
            chat or e.chat_id,
            reply_to=bool(isinstance(e, Message) and reply),
            hide_via=True,
            silent=True,
        )

    except Exception as er:
        LOGS.exception(er)


# Creative deploy inline-send flow


def _deploy_rpc_diagnostic(exc):
    """Return bounded protocol labels; never stringify a Telegram error."""
    from pyUltroid._core.richdebug import rpc_identifier

    identifier = rpc_identifier(exc)
    category = "BadRequestError" if isinstance(exc, BadRequestError) else "RPCError"
    return identifier, category


def _log_deploy_rpc(exc, *, inline_target, message_target, attempted=False, succeeded=False):
    identifier, category = _deploy_rpc_diagnostic(exc)
    LOGS.warning(
        "[inline_deploy] rpc_id=%s rpc_type=%s message_target_recognized=%s "
        "inline_target_recognized=%s fallback_attempted=%s fallback_succeeded=%s",
        identifier,
        category,
        bool(message_target),
        bool(inline_target),
        bool(attempted),
        bool(succeeded),
    )


async def _show_deploy_guide(event, content, buttons=None):
    """Prefer RichMessage; diagnose RPC failures without logging private data."""
    def plain_fallback():
        # Native PageBlocks are not RichBlocks dictionaries. Keep the commands
        # readable if an inline destination rejects RichMessage altogether.
        from telethon.tl.types import InputRichMessage

        if not isinstance(content, InputRichMessage):
            return blocks_to_plain_text(content)

        def flatten(node):
            kind = type(node).__name__
            if kind == "PageBlockDetails":
                return [node.title.text, *[line for child in node.blocks for line in flatten(child)]]
            if kind == "PageBlockPreformatted":
                return [node.text.text]
            return []

        sections = [line for block in content.blocks for line in flatten(block)]
        return (
            "Plain-text guide: collapsibles, tables and in-section copy/URL buttons "
            "are unavailable in this inline destination. The commands below are examples; "
            "choose one install/start method, replace placeholders, and do not start duplicate workers. "
            "If SESSION is blank, complete OTP/QR login using the private setup URL/token in your terminal.\n\n"
            + "\n\n".join(sections)
            + "\n\nFor full explanations and value sources, open Repository → docs/SETUP.md. "
            "Never share SESSION, MONGO_URI, API_HASH, BOT_TOKEN, OTPs or .env."
        )

    try:
        result = await _edit_rich_callback(event, content, buttons)
    except RPCError as exc:
        inline_types = (InputBotInlineMessageID, InputBotInlineMessageID64)
        query_id = getattr(getattr(event, "query", None), "msg_id", None)
        message_id = getattr(event, "message_id", None)
        inline_id = next(
            (candidate for candidate in (message_id, query_id) if isinstance(candidate, inline_types)),
            None,
        )
        message_target = inline_id is not None or (
            getattr(event, "chat_id", None) is not None
            and type(message_id if message_id is not None else query_id) is int
        )
        diagnostic = {"inline_target": inline_id is not None, "message_target": message_target}
        diagnostic["attempted"] = (
            _deploy_rpc_diagnostic(exc)[0] == "RICH_MESSAGE_UNSUPPORTED"
            and inline_id is not None
        )
        _log_deploy_rpc(exc, **diagnostic)
        if diagnostic["attempted"]:
            try:
                result = await edit_inline_plain(
                    event.client,
                    inline_id,
                    plain_fallback(),
                    buttons=buttons,
                    link_preview=False,
                )
            except RPCError as fallback_exc:
                _log_deploy_rpc(fallback_exc, **diagnostic)
            else:
                diagnostic["succeeded"] = True
                _log_deploy_rpc(exc, **diagnostic)
                await event.answer()
                return result
        try:
            await event.answer(
                "The guide could not be updated. Please try again.", alert=True
            )
        except RPCError as answer_exc:
            # Only Telegram RPC failures (e.g. expired query) are expected here.
            _log_deploy_rpc(answer_exc, **diagnostic)
        return None
    await event.answer()
    return result


@callback(data="alive_deploy_hi")
async def _cb_alive_deploy_hi(event):
    """Hi button on the inline-sent deploy article — greet + point to deploy.

    Fires on the assistant bot when someone taps the ``Hi`` button appended to
    the freshly-selected ``inline_alive`` article. Answers with a real,
    actionable deploy prompt (never a no-op).
    """
    who = "there"
    try:
        sender = await event.get_sender()
    except Exception:
        sender = None
    if sender is not None:
        who = get_display_name(sender) or getattr(sender, "username", None) or "there"
    buttons = [[
        _btn(
            "Repository",
            url="https://github.com/Mahesh953-hub/SarahUB",
            preset="primary",
        ),
        _btn("Support", url="https://t.me/UltroidSupportChat", preset="primary"),
    ]]
    return await _show_deploy_guide(
        event,
        _deploy_guide_blocks(who),
        buttons,
    )


def _register_deploy_inline_send():
    """Register the targeted ``UpdateBotInlineSend`` deploy flow on ``asst``.

    The bot-name article (``inline_alive``, empty query) is the one creative
    deploy entrypoint: when a user selects it, Telegram raises
    ``UpdateBotInlineSend`` with an empty ``query``. This handler edits that
    freshly-sent article **in place**, keeping all the Ultroid details and
    appending a "Hey {user}, wanna deploy your Ultroid? Tap Hi" call-to-action
    with a real ``Hi`` callback button.

    ``UpdateBotInlineSend`` also fires for programmatic clicks
    (``.help`` → ``ultd``, ``something()`` → ``stf*``), so the handler requires
    both an empty query and the exact ``alive-deploy`` article ID. It waits a
    short settle interval before editing only that chosen inline message.
    """
    if asst is None:
        return
    try:
        from telethon.tl.types import UpdateBotInlineSend
    except (ImportError, AttributeError):
        return
    from telethon.events import Raw

    async def _on_alive_send(update):
        try:
            await _handle_deploy_inline_send(update, asst)
        except Exception as e:
            LOGS.exception(f"[inline_deploy] inline-send edit failed: {e}")

    try:
        asst.add_event_handler(_on_alive_send, Raw(UpdateBotInlineSend))
    except Exception as e:
        LOGS.error(f"[inline_deploy] could not register inline-send handler: {e}")


_register_deploy_inline_send()

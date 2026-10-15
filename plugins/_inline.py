# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

import re
import time
from datetime import datetime
from os import remove

from telethon import Button
from telethon.tl.types import InputWebDocument, Message
from telethon.utils import resolve_bot_file_id

from pyUltroid._core._cbs import cb, cb_exact_pattern, cb_pattern
from pyUltroid._core import InlineResultBuilder, build_menu_grid
from pyUltroid.ultroid import core
from pyUltroid._core import PLUGIN_MANIFEST_RESULT_BUDGET
from pyUltroid.dB._core import HELP, LIST
from pyUltroid.fns import fns
from pyUltroid.fns import fns

from . import (
    HNDLR,
    LOGS,
    OWNER_NAME,
    InlinePlugin,
    asst,
    get_string,
    inline_pic,
    start_time,
    udB,
    callback,
    in_pattern,
)
from ._help import (
    _help_body,
    _mdv3_text,
    _parse_mode_kwargs,
    main_help_menu_buttons,
)

# ================================================#

helps = get_string("inline_1")

add_ons = udB.get_key("ADDONS")

zhelps = get_string("inline_3") if add_ons is False else get_string("inline_2")
PLUGINS = HELP.get("Official", [])
ADDONS = HELP.get("Addons", [])
upage = 0
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


def _buttons_style_tag() -> str:
    """Short cache-key tag so a `.ui buttons` flip busts cached inline results."""
    return "styled" if _ui_buttons_styled() else "classic"


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


def _alive_text() -> str:
    """The bot-name article body (Ultroid details + config).

    Shared by :func:`inline_alive` (to build the cached article) and the
    empty-query inline-send deploy handler (to *keep everything* while
    appending the "wanna deploy?" call-to-action).
    """
    _bot = getattr(asst, "me", None)
    _bot_name = getattr(_bot, "first_name", None) or (
        getattr(_bot, "username", None) or "Ultroid"
    )
    _up = fns.time_formatter((time.time() - start_time) * 1000)
    return (
        f"• **{_bot_name} — Ultroid Userbot**\n"
        f"• **Owner:** {OWNER_NAME}\n"
        f"• **Uptime:** `{_up}`\n"
        f"• **Plugins:** {len(PLUGINS)} official / {len(ADDONS)} addons\n"
        "• **Support:** @TeamUltroid"
    )


@in_pattern(owner=True, func=lambda x: not x.text)
async def inline_alive(o):
    TLINK = inline_pic()
    builder = InlineResultBuilder(o)
    # W2-D: reuse the built result for the Telegram cache_time window.
    # C2: key on the button style so a `.ui buttons` flip busts the cache.
    cache_key = f"inline:alive:{TLINK or 'none'}:{_buttons_style_tag()}"
    RES = core.inline_cache.get(cache_key)
    if RES is None:
        MSG = _alive_text()
        # Use a plain photo result when a picture is configured; otherwise a
        # media-free article. Building a *text* article with `include_media`
        # + a hard-coded graph.org thumb/content is what produced
        # ``SendMessageMediaInvalidError`` from SetInlineBotResultsRequest.
        if TLINK:
            article = await builder.photo(
                file=TLINK,
                link_preview=False,
                text=MSG,
                buttons=_sup_buttons(),
            )
        else:
            article = await builder.article(
                title="Ultroid Userbot",
                description="Userbot | Telethon",
                text=MSG,
                buttons=_sup_buttons(),
            )
        RES = [article] if article is not None else []
        if RES:
            core.inline_cache.set(cache_key, RES, ttl=300)
    await builder.answer(
        RES,
        private=True,
        cache_time=300,
        switch_pm="👥 ULTROID PORTAL",
        switch_pm_param="start",
    )


@in_pattern("ultd", owner=True)
async def inline_handler(event):
    z = []
    for x in LIST.values():
        z.extend(x)
    pic = inline_pic()
    # W2-D: cache the built help result; key tracks pic + plugin counts so
    # a load/unload or pic change invalidates naturally. TTL mirrors cache_time.
    # C2: also key on the button style so a `.ui buttons` flip doesn't keep
    # serving a stale classic/styled result until the cache_time window ends.
    cache_key = (
        f"inline:ultd:{pic}:{len(HELP.get('Official', []))}:"
        f"{len(HELP.get('Addons', []))}:{len(z)}:{_buttons_style_tag()}"
    )
    builder = InlineResultBuilder(event)
    result = core.inline_cache.get(cache_key)
    if result is None:
        text, _pm = _help_body(len(z))
        _pm_kw = _parse_mode_kwargs(_pm)
        if pic:
            result = await builder.photo(
                file=pic,
                link_preview=False,
                text=text,
                buttons=main_help_menu_buttons(),
                **_pm_kw,
            )
        else:
            result = await builder.article(
                title="Ultroid Help Menu",
                text=text,
                buttons=main_help_menu_buttons(),
                **_pm_kw,
            )
        if result is not None:
            core.inline_cache.set(cache_key, result, ttl=300)
    await builder.answer(
        [result] if result is not None else [],
        private=True,
        cache_time=300,
        gallery=True,
    )


@in_pattern("pasta", owner=True)
async def _(event):
    ok = event.text.split("-")[1]
    link = f"https://spaceb.in/{ok}"
    raw = f"https://spaceb.in/api/{ok}/raw"
    result = await event.builder.article(
        title="Paste",
        text="Pasted to Spacebin 🌌",
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
    z = []
    for x in LIST.values():
        z.extend(x)
    text, _pm = _help_body(len(z))
    await event.edit(
        text,
        file=inline_pic(),
        link_preview=False,
        buttons=[
            [
                _btn("•Pɪɴɢ•", data="pkng"),
                _btn("•Uᴘᴛɪᴍᴇ•", data="upp"),
            ],
            [
                _btn("•Stats•", data="alive"),
                _btn("•Uᴘᴅᴀᴛᴇ•", data="doupdate"),
            ],
            [_btn(get_string("btn_back"), data=cb("assistant", "inline", "main"))],
        ],
        **_parse_mode_kwargs(_pm),
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
    text, _pm = _mdv3_text(_strings.get(key, ""), OWNER_NAME, len(HELP.get(key)))
    await ult.edit(
        text,
        buttons=page_num(count, key),
        link_preview=False,
        **_parse_mode_kwargs(_pm),
    )


@callback(cb_pattern("assistant", "inline", "plugin"), owner=True)
async def uptd_plugin(event):
    rem = event.data_match.group(1).decode("utf-8")
    key, _, rest = rem.partition(":")
    file, _, index = rest.partition(":")
    await _show_plugin(event, key, file, index or None)


async def _show_plugin(event, key, file, index):
    key_ = HELP.get(key, [])
    hel_p = f"Plugin Name - `{file}`\n"
    help_ = ""
    from_registered_help = False
    try:
        for i in key_[file]:
            help_ += i
        from_registered_help = True
    except Exception:
        if file in LIST:
            help_ = get_string("help_11").format(file)
            for d in LIST[file]:
                help_ += HNDLR + d
                help_ += "\n"
    if not help_:
        help_ = f"{file} has no Detailed Help!"
    help_ += "\n© @TeamUltroid"
    # C1: an expandable-blockquote (MarkdownV3) variant of the same body,
    # built only when the text actually came from the registered help
    # cache (never for the LIST-command-list fallback above, which has a
    # different shape than what `plugin_help` cached). `rich_help_`/
    # `rich_parse_mode` stay `None` on any gating/import failure so the
    # classic path below is always the one sent — this never changes the
    # classic text or the callback-data on the buttons.
    rich_help_ = None
    rich_parse_mode = None
    if from_registered_help:
        try:
            from ._rebrand import ui_cards_enabled

            if ui_cards_enabled():
                from pyUltroid._core.entities import RICH_MD
                from pyUltroid.ultroid import core

                if RICH_MD.available:
                    candidate = core.plugin_help.get_rich(file)
                    if candidate and candidate != core.plugin_help.get(file):
                        rich_help_ = candidate + "\n© @TeamUltroid"
                        rich_parse_mode = RICH_MD
        except Exception:
            rich_help_ = None
            rich_parse_mode = None
    buttons = []
    if inline_pic():
        data = cb("assistant", "inline", "send_plugin", key, file, index)
        buttons.append(
            [
                _btn(
                    get_string("btn_send_plugin"),
                    data=data,
                )
            ]
        )
    # --- Plugin manifest buttons ---
    styled = _ui_buttons_styled()
    try:
        entries = core.manifest.get_entries(file)
        for ent in entries:
            etype = ent.get("type")
            tkey = ent.get("toggle_key", "")
            icon = ent.get("icon", "")
            label = ent.get("label", "")

            styled_row = None
            if styled:
                try:
                    from pyUltroid._core.buttonkit import plugin_status_buttons

                    state = (
                        core.manifest.get_state(tkey) if etype == "toggle" else None
                    )
                    built = plugin_status_buttons(ent, state=state)
                    if built:
                        styled_row = built
                except Exception:
                    styled_row = None
            if styled_row is not None:
                buttons.append(styled_row)
                continue

            if etype == "toggle":
                state = core.manifest.get_state(tkey)
                btn_label = (
                    f"{icon} {label}: {'✅' if state else '❌'}"
                    if icon
                    else f"{label}: {'✅' if state else '❌'}"
                )
                buttons.append([_btn(btn_label.strip(), data=f"pltog_{tkey}")])

            elif etype == "action":
                btn_label = f"{icon} {label}" if icon else label
                buttons.append([_btn(btn_label.strip(), data=f"plact_{tkey}")])

            elif etype == "manifest_button":
                color = ent.get("color", "blue")
                prefix = {"green": "🟢 ", "red": "🔴 ", "grey": "⚪️ "}.get(
                    color, "🔵 "
                )
                btn_label = f"{prefix}{label}"
                buttons.append([_btn(btn_label.strip(), data=f"plman_{tkey}")])

        # --- Placeholder button ---
        # Delegates the on/error/url/idle decision to `_placeholder_button`
        # (shared with the pltog_/plact_/plman_ callbacks below); the
        # no-entries case keeps its own static "{^}" — it never reflects
        # `ph` state, unlike the has-entries case.
        ph = core.manifest.get_placeholder(file)
        if entries:
            buttons.append([_placeholder_button(ph)])
        else:
            buttons.append([_btn("{^}", data="plph_info", preset="default")])
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
            _btn(get_string("btn_back"), data=data),
        ]
    )
    try:
        if rich_help_ is not None and rich_parse_mode is not None:
            await event.edit(rich_help_, buttons=buttons, parse_mode=rich_parse_mode)
        else:
            await event.edit(help_, buttons=buttons)
    except Exception as er:
        LOGS.exception(er)
        help = f"Do `{HNDLR}help {key}` to get list of commands."
        await event.edit(help, buttons=buttons)


# ── manifest callbacks ────────────────────────────────────────────────


def _rebuild_buttons(buttons, data_str: str, replacement):
    """Replace the button whose ``data`` matches *data_str* with *replacement*."""
    if not buttons:
        return None
    new_buttons = []
    for row in buttons:
        new_row = []
        for btn in row:
            if btn.data and btn.data.decode("utf-8") == data_str:
                new_row.append(replacement)
            else:
                new_row.append(btn)
        new_buttons.append(new_row)
    return new_buttons


def _placeholder_button(ph: dict):
    """Build the placeholder button for the current ``ph`` state.

    Mirrors ``core.manifest.set_placeholder``'s precedence — is_error
    (``{^}`` + truncated text) > url (URL button) > text (📋 copy button) >
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
        return _btn(f"📋 {ph_text[:40]}", data="plph_copy", preset=preset or "default")
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
        icon = entry.get("icon", "")
        label = entry.get("label", "")
        new_label = (
            f"{icon} {label}: {'✅' if new_state else '❌'}"
            if icon
            else f"{label}: {'✅' if new_state else '❌'}"
        )
        replacement = None
        if _ui_buttons_styled():
            try:
                from pyUltroid._core.buttonkit import state_chip

                replacement = state_chip(
                    toggle_key, bool(new_state), icon=icon, label=label
                )
            except Exception:
                replacement = None
        if replacement is None:
            replacement = _btn(new_label.strip(), data=f"pltog_{toggle_key}")
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
                        if btn.data and btn.data.decode("utf-8") == "plph_copy":
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
                if btn.data and btn.data.decode("utf-8") in ("plph_info", "plph_copy"):
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
                if btn.data and btn.data.decode("utf-8") in ("plph_info", "plph_copy"):
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
    pin = f"🌋Pɪɴɢ = {ms} microseconds"
    await event.answer(pin, cache_time=0, alert=True)


@callback(data="upp", owner=True)
async def _(event):
    uptime = fns.time_formatter((time.time() - start_time) * 1000)
    pin = f"🙋Uᴘᴛɪᴍᴇ = {uptime}"
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
    z = []
    for x in LIST.values():
        z.extend(x)
    text, _pm = _help_body(len(z))
    await event.edit(
        text,
        buttons=main_help_menu_buttons(),
        link_preview=False,
        **_parse_mode_kwargs(_pm),
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
    """The single-row 'Back' button when there's only one plugin page.

    GREEN (``success``) on the add-ons page, RED (``danger``) everywhere
    else — the add-ons page's back button is the one exception to the
    "back to main menu = danger" rule.
    """
    return _btn(
        get_string("btn_back_page"),
        data=cb("assistant", "inline", "main"),
        preset="success" if key == "Addons" else "danger",
    )


def _page_num_nav_row(key, index):
    """Prev / Back / Next nav row — styled chips when ``.ui buttons`` is styled.

    Prev/Next are BLUE (``primary``); Back-to-main is RED (``danger``) except
    on the add-ons page, where it is GREEN (``success``).
    """
    return [
        _btn(
            get_string("btn_previous"),
            data=cb("assistant", "inline", "help_menu", key, index - 1),
            preset="primary",
        ),
        _btn(
            get_string("btn_back_page"),
            data=cb("assistant", "inline", "main"),
            preset="success" if key == "Addons" else "danger",
        ),
        _btn(
            get_string("btn_next"),
            data=cb("assistant", "inline", "help_menu", key, index + 1),
            preset="primary",
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
    emoji = udB.get_key("EMOJI_IN_HELP") or "✘"
    List = [
        _btn(
            f"{emoji} {_plugin_label(x)} {emoji}",
            data=cb("assistant", "inline", "plugin", key, x, index),
            preset="primary",
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


# ─── Creative "deploy your Ultroid" inline_send flow ────────────────────


@callback(data="alive_deploy_hi")
async def _cb_alive_deploy_hi(event):
    """Hi button on the inline-sent deploy article — greet + point to deploy.

    Fires on the assistant bot when someone taps the ``Hi`` button appended to
    the freshly-selected ``inline_alive`` article. Answers with a real,
    actionable deploy prompt (never a no-op).
    """
    who = OWNER_NAME
    try:
        sender = await event.get_sender()
    except Exception:
        sender = None
    if sender is not None:
        who = fns.inline_mention(sender) or OWNER_NAME
    await event.answer("🚀 Let's get you deployed!", alert=True)
    await event.edit(
        f"Hey {who}! 🚀\n\n"
        "Deploy your own Ultroid in a few steps:\n"
        "`1.` `git clone https://github.com/TeamUltroid/Ultroid && cd Ultroid`\n"
        "`2.` `bash installer.sh`\n"
        "`3.` Fill `.env` with `API_ID` / `API_HASH`\n"
        "`4.` `bash startup`\n\n"
        "Stuck? Join @TeamUltroid.",
        link_preview=False,
    )


def _register_deploy_inline_send():
    """Register the empty-query ``UpdateBotInlineSend`` deploy flow on ``asst``.

    The bot-name article (``inline_alive``, empty query) is the one creative
    deploy entrypoint: when a user selects it, Telegram raises
    ``UpdateBotInlineSend`` with an empty ``query``. This handler edits that
    freshly-sent article **in place**, keeping all the Ultroid details and
    appending a "Hey {user}, wanna deploy your Ultroid? Tap Hi" call-to-action
    with a real ``Hi`` callback button.

    ``UpdateBotInlineSend`` also fires for programmatic clicks
    (``.help`` → ``ultd``, ``something()`` → ``stf*``), so the handler gates on
    an empty query only.
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
            query = getattr(update, "query", "") or ""
            if query.strip():
                return
            msg_id = getattr(update, "msg_id", None)
            if msg_id is None:
                return
            user_id = getattr(update, "user_id", None)
            who = OWNER_NAME
            if user_id:
                try:
                    who = (
                        fns.inline_mention(await asst.get_entity(int(user_id)))
                        or OWNER_NAME
                    )
                except Exception as e:
                    LOGS.debug(f"[inline_deploy] get_entity failed: {e}")
            text = (
                f"{_alive_text()}\n\n"
                f"Hey {who}, wanna deploy your Ultroid?\n"
                "Tap **Hi** to get started 🚀"
            )
            await asst.edit_message(
                msg_id,
                text=text,
                buttons=[[_btn("Hi 👋", data="alive_deploy_hi", preset="success")]],
                link_preview=False,
            )
        except Exception as e:
            LOGS.exception(f"[inline_deploy] inline-send edit failed: {e}")

    try:
        asst.add_event_handler(_on_alive_send, Raw(UpdateBotInlineSend))
    except Exception as e:
        LOGS.error(f"[inline_deploy] could not register inline-send handler: {e}")


_register_deploy_inline_send()

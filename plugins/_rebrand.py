# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""UI config router plugin (Phase D5) — the ``.ui`` command group.

This is the single switch every other rebrand component (rich messages,
styled buttons, PIL cards, themed help pages, …) is meant to read before it
renders anything. It owns three udB keys and nothing else:

``UI_THEME``
    ``"dark"`` or ``"default"`` (default: ``"default"``).
``UI_CARDS``
    ``True``/``False`` — PIL-rendered/rich cards vs plain legacy text
    (default: ``False``, i.e. today's legacy behaviour — unset means
    legacy). ``.ui cards on`` is the explicit opt-in to rich cards.
``UI_BUTTONS``
    ``"classic"`` or ``"styled"`` — legacy inline buttons vs the fork's
    ``style=``/``icon=`` button kit (default: ``"styled"``).

Commands
--------

``.ui``
    Show the current theme/cards/buttons configuration.
``.ui theme <dark|default>``
    Set the UI theme.
``.ui cards <on|off>``
    Opt in (``on``) to PIL/rich cards, or opt out (``off``, the default)
    back to plain legacy text.
``.ui buttons <classic|styled>``
    Switch between legacy and styled inline buttons.

Public API
----------

Other components should import the cheap getters below instead of poking at
udB directly — they centralize the default-value fallback:

* :func:`ui_theme` -> ``str``
* :func:`ui_cards_enabled` -> ``bool``
* :func:`ui_buttons_style` -> ``str``

The plugin also self-documents through the Plugin Manifest Framework
(``pyUltroid._core.manifest``): a ``@toggle`` for cards, an ``@action`` that
prints the current settings, and a ``@manifest``/``@handle_manifest`` pair
that cycles the theme/button-style enums from inline buttons in the help
page.
"""

from pyUltroid._core.manifest import action, handle_manifest, manifest, toggle

from . import udB, ultroid_cmd

# ── constants ────────────────────────────────────────────────────────────

_THEMES = ("default", "dark")
_DEFAULT_THEME = "default"

_BUTTON_STYLES = ("classic", "styled")
_DEFAULT_BUTTON_STYLE = "styled"

_DEFAULT_CARDS = False


# ── cheap module-level getters (the public API other components import) ──


def ui_theme() -> str:
    """Return the configured UI theme, ``"default"`` or ``"dark"``."""
    value = udB.get_key("UI_THEME")
    return value if value in _THEMES else _DEFAULT_THEME


def ui_cards_enabled() -> bool:
    """Return whether PIL/rich cards are enabled (opt-in; default off = legacy plain text)."""
    value = udB.get_key("UI_CARDS")
    if isinstance(value, bool):
        return value
    return _DEFAULT_CARDS


def ui_buttons_style() -> str:
    """Return the configured inline-button style, ``"classic"`` or ``"styled"``."""
    value = udB.get_key("UI_BUTTONS")
    return value if value in _BUTTON_STYLES else _DEFAULT_BUTTON_STYLE


def _status_text() -> str:
    cards = "on" if ui_cards_enabled() else "off"
    return (
        "**UI Configuration**\n\n"
        f"• theme: `{ui_theme()}`\n"
        f"• cards: `{cards}`\n"
        f"• buttons: `{ui_buttons_style()}`\n\n"
        "`.ui theme <dark|default>` · `.ui cards <on|off>` · "
        "`.ui buttons <classic|styled>`"
    )


# ── .ui command group ─────────────────────────────────────────────────────


@ultroid_cmd(pattern=r"ui(?:\s+(\S+)(?:\s+(\S+))?)?$", owner_only=True)
async def ui_router(event):
    sub = (event.pattern_match.group(1) or "").lower()
    value = (event.pattern_match.group(2) or "").lower()

    if not sub:
        return await event.eor(_status_text())

    if sub == "theme":
        if value not in _THEMES:
            return await event.eor(f"Usage: `.ui theme <{'|'.join(_THEMES)}>`", time=6)
        udB.set_key("UI_THEME", value)
        return await event.eor(f"UI theme set to **{value}**.", time=4)

    if sub == "cards":
        if value not in ("on", "off"):
            return await event.eor("Usage: `.ui cards <on|off>`", time=6)
        enabled = value == "on"
        udB.set_key("UI_CARDS", enabled)
        return await event.eor(
            (
                "PIL cards enabled."
                if enabled
                else "PIL cards disabled — plain rich messages."
            ),
            time=4,
        )

    if sub == "buttons":
        if value not in _BUTTON_STYLES:
            return await event.eor(
                f"Usage: `.ui buttons <{'|'.join(_BUTTON_STYLES)}>`", time=6
            )
        udB.set_key("UI_BUTTONS", value)
        return await event.eor(f"Button style set to **{value}**.", time=4)

    return await event.eor(_status_text())


# ── Plugin Manifest Framework — self-documentation ────────────────────────


@toggle(
    label="Rich Cards",
    key="cards",
    description="PIL/rich cards (on, opt-in) vs plain legacy text (off, default).",
    icon="🎴",
)
async def toggle_ui_cards(event, enabled: bool) -> str:
    """Mirror the toggle framework's own flip into the UI_CARDS udB key."""
    try:
        udB.set_key("UI_CARDS", enabled)
        return "PIL cards enabled." if enabled else "PIL cards disabled."
    except Exception:
        udB.set_key("UI_CARDS", not enabled)
        raise


@action(
    label="Show UI Settings",
    key="show_settings",
    description="Print the current .ui theme/cards/buttons configuration.",
    icon="⚙️",
)
async def action_show_ui_settings(event) -> str:
    cards = "on" if ui_cards_enabled() else "off"
    return f"theme={ui_theme()} | cards={cards} | buttons={ui_buttons_style()}"


@manifest(
    buttons=[
        {"label": "Cycle Theme", "key": "cycle_theme", "color": "blue"},
        {"label": "Cycle Buttons", "key": "cycle_buttons", "color": "grey"},
    ]
)
async def _ui_manifest_buttons(event) -> None:
    """Declares the manifest button row; the framework never calls this body."""
    return None


@handle_manifest(key="cycle_theme")
async def handle_cycle_theme(event) -> str:
    current = ui_theme()
    nxt = _THEMES[(_THEMES.index(current) + 1) % len(_THEMES)]
    udB.set_key("UI_THEME", nxt)
    return f"UI theme -> {nxt}"


@handle_manifest(key="cycle_buttons")
async def handle_cycle_buttons(event) -> str:
    current = ui_buttons_style()
    nxt = _BUTTON_STYLES[(_BUTTON_STYLES.index(current) + 1) % len(_BUTTON_STYLES)]
    udB.set_key("UI_BUTTONS", nxt)
    return f"Button style -> {nxt}"

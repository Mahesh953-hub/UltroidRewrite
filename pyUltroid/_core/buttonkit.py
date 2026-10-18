# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Styled Button Kit..

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Union
from collections.abc import Iterable
from uuid import uuid4

from custom.rich import STYLE_PRESETS, button
from custom.tg import _is_built_button, ikm

__all__ = (
    "STYLE_PRESETS",
    "button",
    "keyboard",
    "row",
    "menu_buttons",
    "confirm_cancel",
    "pager",
    "state_chip",
    "plugin_status_buttons",
    "PendingConfirmations",
)


# Keyboard-only mode  -  parity with custom.tg.ikm
def keyboard(rows: Any):
    """Build a ``buttons=`` keyboard matrix from rows built by this module."""
    return ikm(rows)


# Coercion helpers
def _coerce_button(spec: Any, preset: str | None = None):
    """Turn one button *spec* into a built Telethon button."""
    if _is_built_button(spec):
        return spec
    if isinstance(spec, dict):
        kwargs = dict(spec)
        if preset is not None and "preset" not in kwargs and "style" not in kwargs:
            kwargs["preset"] = preset
        return button(**kwargs)
    if isinstance(spec, tuple):
        built = ikm([spec])
        return built[0][0] if built else None
    return spec


def row(*btns: Any) -> list[Any]:
    """Combine buttons/specs into a single keyboard row."""
    out: list[Any] = []
    for spec in btns:
        if isinstance(spec, list) and not _is_built_button(spec):
            out.extend(_coerce_button(s) for s in spec)
        else:
            out.append(_coerce_button(spec))
    return [b for b in out if b is not None]


def menu_buttons(
    actions: Iterable[Any],
    *,
    columns: int = 1,
    preset: str | None = None,
) -> list[list[Any]]:
    """Build a button grid (keyboard matrix) from a flat list of action specs.

    Each action is anything :func:`_coerce_button` accepts. ``columns``
    groups the flattened buttons into rows of at most that many buttons
    (default: one button per row, matching the plugin help page's
    single-column manifest button list). ``preset`` is applied to any
    dict action that doesn't already specify its own ``style``/``preset``.
    """
    built = [
        b for b in (_coerce_button(a, preset) for a in actions or []) if b is not None
    ]
    cols = max(1, int(columns))
    return [built[i : i + cols] for i in range(0, len(built), cols)]


# Common patterns
def confirm_cancel(
    confirm_data: str | bytes = "confirm",
    cancel_data: str | bytes = "cancel",
    *,
    confirm_label: str = "Confirm",
    cancel_label: str = "Cancel",
    confirm_preset: str = "confirm",
    cancel_preset: str = "cancel",
) -> list[list[Any]]:
    """Build a ready-to-send ``[[Confirm, Cancel]]`` keyboard.

    Uses the ``"confirm"``/``"cancel"`` entries already in
    :data:`custom.rich.STYLE_PRESETS` (bg-success / bg-danger) unless
    overridden via ``confirm_preset``/``cancel_preset``.
    """
    return [
        [
            button(confirm_label, data=confirm_data, preset=confirm_preset),
            button(cancel_label, data=cancel_data, preset=cancel_preset),
        ]
    ]


class PendingConfirmations:
    """In-memory nonce store gating a real-money text-based confirm flow.

    Inline confirm/cancel *buttons* cannot be attached to messages sent by
    the **user** account  -  Telegram rejects a keyboard on a non-bot send
    (``BOT_METHOD_INVALID``), and a callback registered on the assistant
    **bot** can never fire for it anyway. Money-spending commands
    (``plugins/stars.py``'s ``.pdon <stars> <text>``, ``plugins/gifts.py``,
    ``plugins/giveaways.py``) instead preview the action, stash it here
    under a short nonce, and execute it once the owner either re-runs the
    same command with a trailing ``confirm`` token or re-runs with just
    the nonce (``pop`` looks it up without needing the original arguments
    retyped).

    ``stash(sender_id, kind, **data)`` returns a fresh nonce; ``pop(nonce,
    sender_id)`` returns the ``{"sender_id":, "kind":, "data":,
    "expires":}`` entry once  -  only to the same ``sender_id`` that stashed
    it, and only before ``ttl`` seconds elapse  -  else ``None``. Instantiate
    one instance per plugin module so independent money-spending flows
    never share pending state.
    """

    def __init__(self, ttl: float = 300.0) -> None:
        self._ttl = float(ttl)
        self._pending: dict[str, dict] = {}

    def _prune(self) -> None:
        now = time.time()
        for key in [k for k, v in self._pending.items() if v["expires"] < now]:
            self._pending.pop(key, None)

    def stash(self, sender_id: int, kind: str, **data: Any) -> str:
        """Stash a pending action for ``sender_id`` and return its nonce."""
        self._prune()
        nonce = uuid4().hex[:12]
        self._pending[nonce] = {
            "sender_id": sender_id,
            "kind": kind,
            "data": data,
            "expires": time.time() + self._ttl,
        }
        return nonce

    def pop(self, nonce: str, sender_id: int) -> dict | None:
        """Consume and return the pending entry for ``nonce``, or ``None``.

        Returns ``None`` (without raising) when the nonce is unknown,
        expired, or belongs to a different ``sender_id``  -  callers should
        treat any of those the same way (nothing to confirm).
        """
        self._prune()
        entry = self._pending.get(nonce)
        if not entry:
            return None
        if entry["sender_id"] != sender_id:
            return None
        return self._pending.pop(nonce, None)


def pager(
    prev_cb: str | bytes | None,
    next_cb: str | bytes | None,
    page: int,
    total: int,
    *,
    preset: str | None = None,
    prev_label: str = "« Prev",
    next_label: str = "Next »",
    page_label: str | None = None,
    show_page: bool = True,
) -> list[list[Any]]:
    """Build a ``Prev · page/total · Next`` pager row (a full keyboard).

    ``page``/``total`` are 1-based. The previous/next button is omitted at
    the corresponding boundary (``page <= 1`` / ``page >= total``) instead
    of being sent disabled  -  Telegram inline buttons have no disabled
    state. Pass ``prev_cb``/``next_cb`` as ``None`` to omit a side
    unconditionally (e.g. a single-page listing). Returns ``[]`` if there
    is nothing to show at all (single page, ``show_page=False``).
    """
    page = max(1, int(page))
    total = max(1, int(total))
    cells: list[Any] = []
    if prev_cb is not None and page > 1:
        cells.append(button(prev_label, data=prev_cb, preset=preset))
    if show_page:
        label = page_label if page_label is not None else f"{page}/{total}"
        cells.append(button(label, data="noop", preset=preset))
    if next_cb is not None and page < total:
        cells.append(button(next_label, data=next_cb, preset=preset))
    return [cells] if cells else []


# Manifest / toggle chips
#: Maps the manifest ``manifest_button`` ``color`` field (``blue``/``green``/
#: ``red``/``grey``  -  see ``pyUltroid/_core/manifest.py``) onto
#: ``custom.rich.STYLE_PRESETS`` names.
_MANIFEST_COLOR_PRESET: dict[str, str] = {
    "blue": "primary",
    "green": "success",
    "red": "danger",
    "grey": "default",
}


def state_chip(
    toggle_key: str,
    state: bool,
    *,
    icon: str = "",
    label: str = "",
    preset: str | None = None,
    icon_doc: str | None = None,
    on_preset: str = "confirm",
    off_preset: str = "cancel",
    on_label: str = "ON",
    off_label: str = "OFF",
) -> Any:
    """Build a single styled ON/OFF toggle chip button.

    Callback data is ``pltog_<toggle_key>``  -  the exact prefix
    ``plugins/_inline.py``'s ``_cb_toggle`` callback matches. Renders the
    bg-success/bg-danger colouring via ``preset``/``on_preset``/
    ``off_preset`` *in addition to* the existing text convention: pass
    ``label=""`` (default) for an icon/state-only chip, or ``label=`` to
    reproduce the ``"{icon} {label}: ON/OFF"`` layout
    ``plugins/_inline.py`` already builds  -  just styled instead of relying
    on ✅/❌ alone. ``icon_doc`` is a custom-emoji
    document id forwarded to :func:`custom.rich.button`'s ``icon=``  -  the
    fork's real button icon, distinct from the plain-text ``icon`` prefix
    baked into ``text`` above.
    """
    active_preset = (
        preset if preset is not None else (on_preset if state else off_preset)
    )
    state_word = on_label if state else off_label
    icon_part = f"{icon} " if icon else ""
    label_part = f"{label}: " if label else ""
    text = f"{icon_part}{label_part}{state_word}".strip()
    return button(text, data=f"pltog_{toggle_key}", preset=active_preset, icon=icon_doc)


def _get_manifest_registry():
    """Resolve the shared PMF registry lazily; ``None`` outside a running bot."""
    try:
        from pyUltroid._core.manifest import manifest_registry  # noqa: PLC0415

        return manifest_registry
    except Exception:
        return None


def plugin_status_buttons(
    manifest_entry: dict | None, *, state: bool | None = None
) -> list[Any]:
    """Build the button(s) for one Plugin Manifest Framework entry.

    ``manifest_entry`` is a dict shaped like ``ManifestRegistry.get_entries()``
    /``get_entry()`` results (keys ``type``, ``label``, ``key``, ``icon``,
    ``description``, ``toggle_key``, ``plugin``, and the optional Phase C2
    styling hints ``preset``/``icon_doc``  -  see ``pyUltroid/_core/manifest.py``,
    read-only from here). Dispatches on ``entry["type"]``:

    * ``"toggle"``          → one styled :func:`state_chip`. ``state``
      overrides the live value; when omitted it is read from
      :data:`pyUltroid._core.manifest.manifest_registry` (``False`` when
      the registry can't be resolved, e.g. outside a running bot).
      ``entry["preset"]``/``entry["icon_doc"]`` override :func:`state_chip`'s
      own on/off preset choice and icon when present.
    * ``"action"``          → one button, ``plact_<toggle_key>``, styled
      from ``entry["preset"]``/``entry["icon_doc"]`` when present (plain,
      unstyled otherwise  -  unchanged default).
    * ``"manifest_button"`` → one button, ``plman_<toggle_key>``. Coloured
      from ``entry["preset"]`` when set, else from the entry's ``color``
      field (mapped onto ``STYLE_PRESETS`` via :data:`_MANIFEST_COLOR_PRESET`);
      ``entry["icon_doc"]`` is forwarded as the button icon when present.

    Returns a flat list of buttons (``[]`` for an unknown/missing entry or
    type)  -  feed the result into :func:`row` (or wrap in ``[...]``) to
    place it in one keyboard row.
    """
    from ..ultroid import core
    if not manifest_entry:
        return []
    etype = manifest_entry.get("type")
    toggle_key = manifest_entry.get("toggle_key", "")
    icon = manifest_entry.get("icon", "") or ""
    label = manifest_entry.get("label", "") or ""
    preset = manifest_entry.get("preset")
    icon_doc = manifest_entry.get("icon_doc")
    if etype == "toggle":
        live = state
        if live is None:
            registry = _get_manifest_registry()
            live = core.registry.get_state(toggle_key) if registry is not None else False
        return [
            state_chip(
                toggle_key,
                bool(live),
                icon=icon,
                label=label,
                preset=preset,
                icon_doc=icon_doc,
            )
        ]
    if etype == "action":
        text = f"{icon} {label}".strip() if icon else label
        return [button(text, data=f"plact_{toggle_key}", preset=preset, icon=icon_doc)]
    if etype == "manifest_button":
        color = manifest_entry.get("color", "blue")
        preset = preset or _MANIFEST_COLOR_PRESET.get(color, "primary")
        return [button(label, data=f"plman_{toggle_key}", preset=preset, icon=icon_doc)]
    return []

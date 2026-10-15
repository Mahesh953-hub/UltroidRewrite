# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Plugin toggle system  -  decorator + registry for inline settings per plugin."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Dict, List, Optional, Tuple
from collections.abc import Callable

from ..startup.logger import LOGS

PLUGIN_TOGGLES = "PLUGIN_TOGGLES"
_HANDLER_TIMEOUT = 10.0


# Decorator
def plugin_toggle(
    label: str,
    key: str,
    *,
    description: str = "",
    icon: str = "",
    preset: str | None = None,
    icon_doc: str | None = None,
) -> Callable:
    """Mark an async function as a toggle-able plugin setting.

    The decorated function must be async and accept a single ``bool`` argument
    (the new state after the toggle).  It should return a ``str`` confirmation
    message or ``None`` for a silent success.

    The decorator only *attaches* metadata  -  it does **not** wrap the function
    and performs **no** signature validation here.  Validation is deferred to
    :meth:`ToggleRegistry.register`, so a mis-decorated function never breaks
    module import.

    Args:
        label: Button label, e.g. ``"Auto Update"``.
        key: Unique identifier within the plugin, e.g. ``"auto_update"``.
            Scoped per-plugin automatically as ``"{plugin}.{key}"``.
        description: Short help line shown below the toggle button.
        icon: Optional emoji prefix for the button, e.g. ``"🔄"``.
        preset: Optional (Phase C2) ``pyUltroid._core.buttonkit.STYLE_PRESETS``
            name the styled-button renderer may use for this toggle instead
            of its own on/off preset choice. Defaults to ``None`` and is only
            attached to the stamped metadata when explicitly given, so
            existing call sites / exact-equality checks on
            ``func._plugin_toggle_`` are unaffected.
        icon_doc: Optional (Phase C2) custom emoji document id string for
            the button icon. Same additive-only, ``None``-default contract
            as ``preset``.
    """

    def decorator(func: Callable) -> Callable:
        meta = {
            "label": label,
            "key": key,
            "description": description,
            "icon": icon,
        }
        if preset is not None:
            meta["preset"] = preset
        if icon_doc is not None:
            meta["icon_doc"] = icon_doc
        func._plugin_toggle_ = meta
        return func

    return decorator


# Registry
class ToggleRegistry:
    """Singleton registry for plugin toggle switches.

    Holds toggle metadata(``_toggles``) keyed by the namespaced
    ``"{plugin}.{key}"`` id and the live boolean states (``_states``).  All
    state mutations are mirrored to ``udB`` when it is available; otherwise the
    registry operates purely in-memory.
    """

    def __init__(self) -> None:
        self._toggles: dict[str, dict] = {}  # toggle_key -> metadata
        self._states: dict[str, bool] = {}  # toggle_key -> bool

    # ── registration
    def register(self, plugin_name: str, module: Any) -> int:
        """Scan *module* for ``@plugin_toggle`` decorated functions.

        Each discovered async coroutine function is recorded under the
        namespaced key ``"{plugin_name}.{key}"``.  Non-callables, non-async
        callables, and handlers whose signature accepts no argument are
        skipped with a warning.  Duplicate keys within the same plugin are
        skipped with a warning.  Persisted states are loaded first so a toggle
        that was previously flipped keeps its value; any toggle without an
        existing state defaults to ``False``.

        Returns the count of toggles registered.
        """
        self._load_states()
        count = 0
        for name in dir(module):
            try:
                obj = getattr(module, name)
            except Exception:
                continue
            if not callable(obj):
                continue
            meta = getattr(obj, "_plugin_toggle_", None)
            if not isinstance(meta, dict):
                continue
            if not asyncio.iscoroutinefunction(obj):
                LOGS.warning(
                    "plugin_toggle: %s.%s is not async  -  skipping toggle %r",
                    plugin_name,
                    name,
                    meta.get("key"),
                )
                continue
            if not _accepts_state_arg(obj):
                LOGS.warning(
                    "plugin_toggle: %s.%s accepts no state argument  -  skipping"
                    " toggle %r",
                    plugin_name,
                    name,
                    meta.get("key"),
                )
                continue
            toggle_key = f"{plugin_name}.{meta['key']}"
            if toggle_key in self._toggles:
                LOGS.warning(
                    "plugin_toggle: duplicate key %r in plugin %r  -  skipping",
                    toggle_key,
                    plugin_name,
                )
                continue
            self._toggles[toggle_key] = {
                "plugin": plugin_name,
                "label": meta.get("label", ""),
                "key": meta.get("key", ""),
                "description": meta.get("description", ""),
                "icon": meta.get("icon", ""),
                "preset": meta.get("preset"),
                "icon_doc": meta.get("icon_doc"),
                "handler": obj,
            }
            # Default missing states to OFF (do not clobber persisted values).
            self._states.setdefault(toggle_key, False)
            count += 1
        return count

    def unregister(self, plugin_name: str) -> int:
        """Remove all toggles (and their state) for *plugin_name*.

        Returns the count of toggles removed.
        """
        keys = [k for k, m in self._toggles.items() if m["plugin"] == plugin_name]
        for key in keys:
            del self._toggles[key]
            self._states.pop(key, None)
        if keys:
            self._save_states()
        return len(keys)

    # ── read access
    def get_toggles(self, plugin_name: str) -> list[dict]:
        """Return toggle metadata list for *plugin_name*, sorted by key.

        The handler callable is intentionally excluded so the returned dicts
        are safe to feed straight into the help-page UI. ``preset``/
        ``icon_doc`` are optional styled-button hints  -  ``None``
        when the toggle was declared without them.
        """
        result = [
            {
                "toggle_key": toggle_key,
                "plugin": meta["plugin"],
                "label": meta["label"],
                "key": meta["key"],
                "description": meta["description"],
                "icon": meta["icon"],
                "preset": meta.get("preset"),
                "icon_doc": meta.get("icon_doc"),
            }
            for toggle_key, meta in self._toggles.items()
            if meta["plugin"] == plugin_name
        ]
        result.sort(key=lambda m: m["key"])
        return result

    def get_state(self, toggle_key: str) -> bool:
        """Get current state (default ``False`` if not set)."""
        return self._states.get(toggle_key, False)

    def get_toggle(self, toggle_key: str) -> dict | None:
        """Return the full metadata dict for *toggle_key*, or ``None``."""
        meta = self._toggles.get(toggle_key)
        if meta is None:
            return None
        return {
            "toggle_key": toggle_key,
            "plugin": meta["plugin"],
            "label": meta["label"],
            "key": meta["key"],
            "description": meta["description"],
            "icon": meta["icon"],
            "preset": meta.get("preset"),
            "icon_doc": meta.get("icon_doc"),
        }

    # ── state mutation

    async def flip(self, toggle_key: str, clicker_id: int) -> tuple[bool, str]:
        """Flip the toggle state, call the handler, return ``(state, msg)``.

        The current state is inverted, persisted, then the plugin handler is
        awaited inside a 10-second timeout.  On timeout or any handler
        exception the state is reverted and re-persisted, and
        ``(False, error)`` is returned.  On success ``(new_state, message)``
        is returned, where *message* is the handler's return string or a
        generated ``"{icon} {label}: ON/OFF"`` fallback.
        """
        if toggle_key not in self._toggles:
            return (False, f"Toggle {toggle_key!r} is not registered")
        handler = self._toggles[toggle_key]["handler"]
        previous = self._states.get(toggle_key, False)
        new_state = not previous
        # Optimistically apply + persist, then let the handler confirm.
        self._states[toggle_key] = new_state
        self._save_states()
        try:
            result = await asyncio.wait_for(
                handler(new_state), timeout=_HANDLER_TIMEOUT
            )
        except TimeoutError:
            self._states[toggle_key] = previous
            self._save_states()
            return (False, "Toggle handler timed out; state reverted.")
        except Exception as exc:  # noqa: BLE001  -  handler is plugin code
            self._states[toggle_key] = previous
            self._save_states()
            return (False, f"Toggle handler error: {exc}")
        message = (
            result
            if isinstance(result, str) and result
            else _format_state_message(self._toggles[toggle_key], new_state)
        )
        return (new_state, message)

    # ── persistence
    def _load_states(self) -> None:
        """Load states from ``udB`` :data:`PLUGIN_TOGGLES` key.

        Merges persisted values into the in-memory dict without dropping
        defaults already set this session.  No-op when ``udB`` is unavailable.
        """
        from pyUltroid import udB
        if udB is None:
            return
        try:
            stored = udB.get_key(PLUGIN_TOGGLES)
        except Exception:
            return
        if isinstance(stored, dict):
            for raw_key, raw_val in stored.items():
                self._states[str(raw_key)] = bool(raw_val)

    def _save_states(self) -> None:
        """Persist states to ``udB`` :data:`PLUGIN_TOGGLES` key.

        No-op (in-memory only) when ``udB`` is unavailable.
        """
        from pyUltroid import udB
        if udB is None:
            return
        try:
            udB.set_key(PLUGIN_TOGGLES, dict(self._states))
        except Exception:
            pass


# Helpers
def _accepts_state_arg(obj: Callable) -> bool:
    """Return ``True`` if *obj*'s signature accepts at least one argument.

    Lenient: any introspection failure (builtins, C-implemented callables) is
    treated as accepting an argument so genuine plugin handlers are never
    rejected for being hard to introspect.
    """
    try:
        params = list(inspect.signature(obj).parameters.values())
    except (TypeError, ValueError):
        return True
    kinds = {
        inspect.Parameter.POSITIONAL_ONLY,
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        inspect.Parameter.VAR_POSITIONAL,
    }
    return any(p.kind in kinds for p in params)


def _format_state_message(meta: dict, state: bool) -> str:
    """Build the default ON/OFF confirmation message for a toggle."""
    prefix = f"{meta.get('icon', '')} ".strip() if meta.get("icon") else ""
    return f"{prefix}{meta.get('label', '')}: {'ON' if state else 'OFF'}"



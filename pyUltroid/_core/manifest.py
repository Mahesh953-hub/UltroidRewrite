# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Plugin Manifest Framework  -  decorator-based inline settings and actions per plugin.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import asyncio
import inspect
import sys
from typing import Any, Dict, List, Optional, Tuple
from collections.abc import Callable, Iterable

from ..startup.logger import LOGS

__all__ = (
    "PLUGIN_MANIFEST_RESULT_BUDGET",
    "toggle",
    "action",
    "manifest",
    "handle_manifest",
    "ManifestRegistry",
    "scan_module",
    "install_missing",
    "get_help_chars",
)

# ── constants ──────────────────────────────────────────────────────────

PLUGIN_MANIFEST_RESULT_BUDGET: int = 300
"""Maximum characters the framework may append to the help message for
action / manifest results."""

_DB_KEY_TOGGLES: str = "PMF_TOGGLES"
"""udB key storing ``{toggle_key: bool}``."""

_HANDLER_TIMEOUT_TOGGLE: float = 10.0
_HANDLER_TIMEOUT_ACTION: float = 30.0


def toggle(
    *,
    label: str,
    key: str,
    icon: str = "",
    description: str = "",
    preset: str | None = None,
    icon_doc: str | None = None,
) -> Callable:
    """Declare a boolean toggle switch for a plugin."""

    def _wrap(fn: Callable) -> Callable:
        meta = {
            "type": "toggle",
            "label": label,
            "key": key,
            "icon": icon,
            "description": description,
        }
        if preset is not None:
            meta["preset"] = preset
        if icon_doc is not None:
            meta["icon_doc"] = icon_doc
        fn._pmf_ = meta
        return fn

    return _wrap


def action(
    *,
    label: str,
    key: str,
    icon: str = "",
    description: str = "",
    preset: str | None = None,
    icon_doc: str | None = None,
) -> Callable:
    """Declare a one-shot action button for a plugin.

    The decorated handler receives ``(event)`` and must return a
    non-empty ``str`` (written into the result area) or ``False``
    (short alert shown instead).

    See :func:`toggle` for the optional ``preset``/``icon_doc`` styling
    metadata  -  same additive-only, ``None``-default contract.
    """

    def _wrap(fn: Callable) -> Callable:
        meta = {
            "type": "action",
            "label": label,
            "key": key,
            "icon": icon,
            "description": description,
        }
        if preset is not None:
            meta["preset"] = preset
        if icon_doc is not None:
            meta["icon_doc"] = icon_doc
        fn._pmf_ = meta
        return fn

    return _wrap


def manifest(*, buttons: list[dict[str, str]]) -> Callable:
    """Declare a custom inline button menu inside the plugin's help page.

    *buttons* is a list of dicts, each with keys ``label``, ``key``,
    and optionally ``color`` (one of ``blue``, ``green``, ``red``,
    ``grey``), plus the optional Phase C2 styling hints ``preset``
    (a ``buttonkit.STYLE_PRESETS`` name, overriding the ``color``-derived
    default) and ``icon_doc`` (custom emoji document id string). Both
    default to ``None``/absent and only affect the styled-button renderer.
    The framework renders the corresponding inline buttons and dispatches
    clicks to ``@handle_manifest``-decorated handlers whose *key* matches
    the button's key.

    ``@manifest`` and ``@handle_manifest`` work together: ``@manifest``
    declares the button layout; ``@handle_manifest`` provides the
    callback logic for each key.
    """

    def _wrap(fn: Callable) -> Callable:
        # Attach to the *module* indirectly through the function.
        fn._pmf_ = {
            "type": "manifest",
            "buttons": buttons,
        }
        return fn

    return _wrap


def handle_manifest(*, key: str) -> Callable:
    """Register a callback handler for a ``@manifest`` button key.

    The decorated function receives ``(event)`` and follows the same
    return contract as ``@action``: ``str`` → result area, ``False``
    → short alert.
    """

    def _wrap(fn: Callable) -> Callable:
        fn._pmf_ = {
            "type": "handle_manifest",
            "key": key,
        }
        fn._pmf_key_ = key
        return fn

    return _wrap


# ── verification helpers ───────────────────────────────────────────────


def _check_async(obj: Any) -> bool:
    """Return ``True`` if *obj* is an async callable."""
    return callable(obj) and (
        asyncio.iscoroutinefunction(obj)
        or (
            callable(obj)
            and asyncio.iscoroutinefunction(getattr(obj, "__wrapped__", None))
        )
    )


def _signature_ok(obj: Callable, entry_type: str) -> bool:
    """Rough signature check  -  lenient to avoid false negatives from
    decorated functions, ``functools.partial``, and C extensions."""
    try:
        sig = inspect.signature(obj)
        params = list(sig.parameters.values())
    except (ValueError, TypeError):
        # Can't inspect; trust the author.
        return True
    if entry_type in ("toggle",):
        # Needs at least 2 positional params: (event, enabled).
        positional = sum(
            1 for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        )
        return positional >= 2
    if entry_type in ("action", "handle_manifest"):
        positional = sum(
            1 for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        )
        return positional >= 1
    return True


# ── registry
class ManifestRegistry:
    """Singleton registry for all plugin manifest entries.

    Scanned by :func:`scan_module` and consumed by the help inline UI
    in ``plugins/_inline.py``.
    """

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}  # toggle_key → full entry
        self._states: dict[str, bool] = {}  # toggle_key → bool
        self._manifest_handlers: dict[str, Callable] = {}  # key → handler
        self._placeholder: dict[str, dict] = {}  # plugin_name → {text, url}
        #: Everything the database held, including toggles for plugins that are
        #: not loaded right now. Kept apart from ``_states`` so a save can carry
        #: an absent plugin's state through instead of dropping it.
        self._stored: dict[str, bool] | None = None
        #: Toggles that registered before the states could be read, so their
        #: stored choice still has to be applied.
        self._unrestored: set = set()

    # ── registration ────────────────────────────────────────────────

    def register(self, plugin_name: str, entry: dict) -> bool:
        """Register a single manifest entry discovered by the scan."""
        etype = entry.get("type")
        key = entry.get("key", "")
        toggle_key = f"{plugin_name}.{key}" if key else ""

        if etype in ("toggle", "action") and not key:
            LOGS.warning(
                "manifest: %s entry missing key, skipping",
                plugin_name,
            )
            return False

        if etype in ("toggle", "action"):
            if toggle_key in self._entries:
                LOGS.warning(
                    "manifest: duplicate key %r in %s, skipping",
                    toggle_key,
                    plugin_name,
                )
                return False
            handler = entry.pop("handler", None)
            if handler is None or not _check_async(handler):
                LOGS.warning(
                    "manifest: handler for %r is not async, skipping",
                    toggle_key,
                )
                return False
            if not _signature_ok(handler, etype):
                LOGS.warning(
                    "manifest: handler for %r has wrong signature, skipping",
                    toggle_key,
                )
                return False
            entry["toggle_key"] = toggle_key
            entry["plugin"] = plugin_name
            entry["handler"] = handler
            self._entries[toggle_key] = entry
            if etype == "toggle" and toggle_key not in self._states:
                # Restore what the user last chose. The registry is built during
                # the plugin load pass, long before anything reads a state, so
                # this is the point where a stored choice has to come back.
                self._states[toggle_key] = self._restore_state(toggle_key)
            return True

        if etype == "manifest":
            buttons = entry.get("buttons", [])
            for btn in buttons:
                btn_key = btn.get("key", "")
                if not btn_key:
                    continue
                full_key = f"{plugin_name}.{btn_key}"
                if full_key in self._manifest_handlers:
                    LOGS.warning(
                        "manifest: duplicate key %r, skipping",
                        full_key,
                    )
                    continue
                # Handler is attached later via handle_manifest
                self._manifest_handlers[full_key] = _missing_handler
                # Store button metadata for the UI
                self._entries[full_key] = {
                    "type": "manifest_button",
                    "toggle_key": full_key,
                    "plugin": plugin_name,
                    "key": btn_key,
                    "label": btn.get("label", btn_key),
                    "color": btn.get("color", "blue"),
                    "preset": btn.get("preset"),
                    "icon_doc": btn.get("icon_doc"),
                }
            return True

        if etype == "handle_manifest":
            hkey = entry.get("key", "")
            full_key = f"{plugin_name}.{hkey}" if "." not in hkey else hkey
            handler = entry.get("handler")
            if handler is None or not _check_async(
                getattr(handler, "__wrapped__", handler)
            ):
                LOGS.warning(
                    "manifest: handle_manifest for %r not async, skipping",
                    full_key,
                )
                return False
            self._manifest_handlers[full_key] = handler
            return True

        return False

    def unregister(self, plugin_name: str) -> int:
        """Remove all entries and state for *plugin_name*. Returns count."""
        removed = 0
        for key in list(self._entries):
            if self._entries[key].get("plugin") == plugin_name:
                del self._entries[key]
                removed += 1
        for key in list(self._states):
            if key.startswith(f"{plugin_name}."):
                del self._states[key]
                removed += 1
        for key in list(self._manifest_handlers):
            if key.startswith(f"{plugin_name}."):
                del self._manifest_handlers[key]
        self._placeholder.pop(plugin_name, None)
        return removed

    # ── queries
    def get_entries(self, plugin_name: str) -> list[dict]:
        """Return handler-free metadata for every entry under *plugin_name*,
        sorted by key.  Safe to feed into the help UI."""
        result: list[dict] = []
        for toggle_key, entry in self._entries.items():
            if entry.get("plugin") != plugin_name:
                continue
            result.append({k: v for k, v in entry.items() if k != "handler"})
        result.sort(key=lambda e: e.get("key", ""))
        return result

    def get_entry(self, toggle_key: str) -> dict | None:
        """Return the full metadata dict for *toggle_key*, or ``None``."""
        entry = self._entries.get(toggle_key)
        if entry is None:
            return None
        return dict(entry)

    def get_handler(self, toggle_key: str) -> Callable | None:
        """Return the handler callable for *toggle_key*."""
        entry = self._entries.get(toggle_key)
        if entry is not None:
            return entry.get("handler")
        return self._manifest_handlers.get(toggle_key)

    def get_state(self, toggle_key: str) -> bool:
        """Current toggle state (``False`` by default).

        Reading gives :meth:`_reconcile` its chance to run, so a toggle that
        registered before the database was reachable still reports the user's
        stored choice rather than the default it was given.
        """
        if self._unrestored:
            self._stored_states()  # gives _reconcile its chance to run
        return self._states.get(toggle_key, False)

    # ── placeholder
    def set_placeholder(
        self,
        plugin_name: str,
        text: str = "",
        url: str = "",
        is_error: bool = False,
        preset: str | None = None,
    ) -> None:
        """Set the placeholder state for *plugin_name*.

        - *text* non-empty, *is_error* False → green text button.
        - *url* non-empty               → green URL button.
        - *is_error* True               → red text button.
        - all empty                     → idle (red ``{^}``).

        ``preset`` is a forward-compatible styling
        hint (``buttonkit.STYLE_PRESETS`` name) the styled-button renderer
        may use instead of deriving its own preset from *is_error*/*url*.
        Defaults to ``None`` (renderer picks its own default).
        """
        self._placeholder[plugin_name] = {
            "text": text[:PLUGIN_MANIFEST_RESULT_BUDGET],
            "url": url,
            "is_error": is_error,
            "preset": preset,
        }

    def get_placeholder(self, plugin_name: str) -> dict:
        """Return the placeholder state for *plugin_name*."""
        return self._placeholder.get(
            plugin_name,
            {
                "text": "",
                "url": "",
                "is_error": False,
                "preset": None,
            },
        )

    # ── execution
    async def flip_toggle(self, toggle_key: str) -> tuple[bool, str | None]:
        """Flip the toggle state, persist, call handler, return ``(new_state, alert_or_none)``.

        On handler error: state reverts, error logged, returns ``(False, "...")``.
        """
        entry = self._entries.get(toggle_key)
        if entry is None or entry.get("type") != "toggle":
            return (False, "Toggle not found.")

        # Go through get_state so a toggle registered before the database was
        # reachable flips from the user's stored choice, not from the default.
        current = self.get_state(toggle_key)
        new_state = not current
        # Optimistic persist.
        self._states[toggle_key] = new_state
        self._save_toggle_states()

        handler = entry.get("handler")
        if handler is None:
            return (new_state, None)

        try:
            result = await asyncio.wait_for(
                handler(None, new_state),
                timeout=_HANDLER_TIMEOUT_TOGGLE,
            )
        except TimeoutError:
            self._states[toggle_key] = current
            self._save_toggle_states()
            LOGS.error("manifest: toggle %r handler timed out", toggle_key)
            return (current, None)
        except Exception:
            self._states[toggle_key] = current
            self._save_toggle_states()
            LOGS.exception("manifest: toggle %r handler crashed", toggle_key)
            return (current, None)

        alert = None
        if isinstance(result, str) and result:
            alert = result
        elif result is not False and result is not None:
            alert = str(result) if str(result) else None
        return (new_state, alert)

    async def run_action(
        self, toggle_key: str, event: Any = None
    ) -> tuple[bool, str | None]:
        """Run an action / handle_manifest handler, return ``(success, output_or_none)``.

        Output is ``None`` when the handler returns ``False`` (alert mode)
        or when an error occurs.
        """
        handler = self.get_handler(toggle_key)
        if handler is None:
            handler = self._manifest_handlers.get(toggle_key)
        if handler is None:
            return (False, None)

        try:
            result = await asyncio.wait_for(
                handler(event),
                timeout=_HANDLER_TIMEOUT_ACTION,
            )
        except TimeoutError:
            LOGS.error("manifest: action %r timed out", toggle_key)
            return (False, None)
        except Exception:
            LOGS.exception("manifest: action %r crashed", toggle_key)
            return (False, None)

        if result is False:
            return (True, None)  # alert mode  -  caller shows short popup
        if isinstance(result, str) and result:
            return (True, result)
        if result is not None:
            return (True, str(result))
        return (True, None)

    # ── persistence ─────────────────────────────────────────────────

    def _stored_states(self, *, read: bool = False) -> dict[str, bool] | None:
        """The persisted toggle states, read once and then cached.

        The read is deferred rather than done in ``__init__`` because this module
        is imported long before ``udB`` exists; at construction time there is no
        database to read.

        With *read* set, a failed read returns ``None`` instead of an empty
        mapping, so a caller that has to tell the two apart can do so without
        reading a second time and risking a different answer.
        """
        if self._stored is None:
            stored = _load_toggle_states()
            if stored is None:
                # No database yet, or the read failed. Do not cache that: the
                # next call must be able to read the real states. A cached miss
                # would be indistinguishable from "nothing stored" and the next
                # save would write defaults over the user's choices.
                return None if read else {}
            self._stored = stored
            self._reconcile()
        return self._stored

    def _reconcile(self) -> None:
        """Apply the stored states to toggles registered before the read worked.

        A toggle can register while the database is still unavailable, which
        leaves its default in ``_states``. Once a read succeeds, those toggles
        have to pick up the user's stored choice; otherwise the registration that
        happened to run first would keep the toggle off forever.
        """
        stored = self._stored or {}
        for toggle_key in list(self._unrestored):
            if toggle_key in self._states:
                self._states[toggle_key] = bool(stored.get(toggle_key, False))
        self._unrestored.clear()

    def _restore_state(self, toggle_key: str) -> bool:
        """The stored state for *toggle_key*, defaulting to off.

        When the states cannot be read yet, the toggle is remembered so
        :meth:`_reconcile` can correct it as soon as a read succeeds.
        """
        stored = self._stored_states(read=True)
        if stored is None:
            self._unrestored.add(toggle_key)
            return False
        return bool(stored.get(toggle_key, False))

    def _save_toggle_states(self) -> None:
        """Persist the live states, keeping the ones this process cannot see.

        A plugin that is unloaded has its state dropped from ``_states``, so
        writing that dict alone would erase the user's choice for every plugin
        that happens not to be loaded. The stored states are merged underneath,
        with the live ones winning.
        """
        # Re-read rather than trusting the cache. A save happens only when the
        # user flips a toggle, so the extra read is cheap, and it keeps a stored
        # state written since the cache was filled from being overwritten.
        stored = _load_toggle_states()
        if stored is None:
            # The states could not be read, so they cannot be merged either.
            # Writing the live states alone here would erase the choices this
            # process cannot see. Skip the write and keep the live state.
            LOGS.warning(
                "manifest: cannot read %s, so the toggle change is not persisted",
                _DB_KEY_TOGGLES,
            )
            return
        merged = {**stored, **self._states}
        if _save_toggle_states(merged):
            self._stored = merged

    def prune_stored_states(self, known_plugins: Iterable[str]) -> int:
        """Forget stored toggles whose plugin is not in *known_plugins*.

        Not called automatically. An absent plugin usually means "not loaded
        yet" or "failed to load", and discarding its state on that basis would
        quietly reset the user's choice; pruning is therefore an explicit
        request. Returns the number of states dropped.
        """
        # Match keys the same way ``unregister`` does  -  by plugin prefix, not by
        # splitting on the last dot. A key holding a dot of its own, or a plugin
        # name that does, would otherwise be attributed to a plugin that does not
        # exist and silently pruned.
        prefixes = tuple(f"{name}." for name in known_plugins)
        current = self._stored_states(read=True)
        if current is None:
            # The states could not be read, so there is nothing to prune from.
            return 0
        stored = dict(current)
        dropped = 0
        for key in list(stored):
            if not key.startswith(prefixes):
                del stored[key]
                dropped += 1
        if not dropped:
            return 0
        if not _save_toggle_states({**stored, **self._states}):
            # The prune did not reach the database, so the states it meant to
            # drop are still there. Leave the cache alone and report nothing
            # dropped, rather than hiding states this process would still find.
            return 0
        self._stored = stored
        return dropped


# ── helpers
async def _missing_handler(event) -> bool:
    """Default handler for manifest buttons not yet wired."""
    return False


def get_help_chars(plugin_name: str) -> int:
    """Return the stored help text character count for *plugin_name*.

    Used by the manifest UI to calculate remaining space for action
    results within the help message caption limit.
    """
    try:
        from pyUltroid.dB._core import HELP_CHARS  # noqa: PLC0415

        for bucket in HELP_CHARS.values():
            if isinstance(bucket, dict) and plugin_name in bucket:
                return bucket[plugin_name]
    except Exception:
        pass
    return 0


def _load_toggle_states() -> dict[str, bool] | None:
    """Read the persisted toggle states.

    Returns ``None`` when the read could not be made  -  no database yet, or the
    read raised  -  and a mapping when it succeeded, which may legitimately be
    empty. The two cases must stay distinct: treating a failed read as "no states
    stored" would let the next save overwrite the stored mapping with defaults.
    """
    from pyUltroid import udB
    if udB is None:
        return None
    try:
        stored = udB.get_key(_DB_KEY_TOGGLES) or {}
    except Exception:
        LOGS.exception("manifest: could not read %s", _DB_KEY_TOGGLES)
        return None
    if not isinstance(stored, dict):
        LOGS.warning(
            "manifest: %s holds %s, not a mapping; ignoring it",
            _DB_KEY_TOGGLES,
            type(stored).__name__,
        )
        return {}
    return {str(k): bool(v) for k, v in stored.items()}


def _save_toggle_states(states: dict[str, bool]) -> bool:
    """Write *states*, reporting whether the write reached the database.

    The caller caches what it believes is stored, so a failed write has to be
    visible; otherwise the cache would claim a state the database does not hold.
    """
    from pyUltroid import udB
    if udB is None:
        return False
    try:
        udB.set_key(_DB_KEY_TOGGLES, dict(states))
    except Exception:
        LOGS.exception("manifest: could not write %s", _DB_KEY_TOGGLES)
        return False
    return True


# ── module scanner (called by the loader)
def scan_module(plugin_name: str, module) -> int:
    """Scan a loaded plugin module for ``_pmf_``-decorated functions.

    Called by :meth:`PluginHelp.register` during the loader pass.
    Returns the number of entries successfully registered.
    """
    from ..ultroid import core
    count = 0
    for attr_name in dir(module):
        obj = getattr(module, attr_name, None)
        if obj is None:
            continue
        meta = getattr(obj, "_pmf_", None)
        if meta is None or not callable(obj):
            continue
        entry = dict(meta)
        entry["handler"] = obj
        if core.manifest.register(plugin_name, entry):
            count += 1
    return count


# ── install-on-missing (loader helper)
#: Modules whose pip install already failed in this process. Retrying
#: in-process never succeeds (same interpreter, same index), so skip
#: straight to ``False`` instead of burning another pip timeout per plugin.
_FAILED_INSTALLS: set[str] = set()


def install_missing(module_name: str, triggered_by: str, reason: str = "") -> bool:
    """Attempt to pip-install *module_name* when an import fails.

    Called from the loader's import guard.  Returns ``True`` if the
    install succeeded and a retry import is likely to work.
    """
    if module_name in _FAILED_INSTALLS:
        return False
    logs = LOGS
    logs.info(
        "manifest: installing %r (triggered by %r, reason: %s)",
        module_name,
        triggered_by,
        reason or "manifest dependency",
    )
    try:
        import subprocess

        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "-q",
                "--break-system-packages",
                module_name,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            logs.info("manifest: installed %r successfully", module_name)
            return True
        logs.warning(
            "manifest: pip install %r failed: %s",
            module_name,
            result.stderr.strip()[-200:],
        )
    except Exception:
        logs.exception("manifest: pip install %r crashed", module_name)
    _FAILED_INSTALLS.add(module_name)
    return False


# ── module-level singleton
"""Module-level singleton  -  one registry per process."""

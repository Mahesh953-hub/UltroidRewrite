# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""Modern asynchronous plugin / addon loader.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

import asyncio
import contextlib
import glob
import json
import os
import sys
import time
from dataclasses import dataclass, field
from importlib import import_module
from importlib.util import cache_from_source
from logging import Logger
from traceback import format_exc
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from ..startup.logger import LOGS
from ..extender._manifest import DepSpec

# Status constants
STATUS_ACTIVE = "active"
STATUS_INACTIVE = "inactive"
STATUS_ERRORED = "errored"


# PluginManifest
@dataclass
class PluginManifest:
    """Metadata describing one loaded (or failed) plugin."""

    name: str
    version: str = "1.0"
    author: str = "Unknown"
    description: str = ""
    path: str = ""
    dependencies: tuple = ()
    handler_count: int = 0
    loaded_at: float = 0.0
    status: str = STATUS_INACTIVE  # active | inactive | errored
    error_info: str | None = None
    # Exact event registrations observed while importing this plugin.
    handlers: list[tuple[Any, Any, Any]] = field(default_factory=list, repr=False)
    # Dispatcher command/listener registrations owned by this plugin.
    dispatcher_entries: list[tuple[Any, str, Any, Any]] = field(
        default_factory=list, repr=False
    )

    @property
    def is_active(self) -> bool:
        return self.status == STATUS_ACTIVE

    @property
    def is_errored(self) -> bool:
        return self.status == STATUS_ERRORED


# PluginRegistry
class PluginRegistry:
    """Canonical-ID registry with immutable snapshots and legacy stem aliases."""

    def __init__(self) -> None:
        self._plugins: dict[str, PluginManifest] = {}
        self._names: frozenset[str] = frozenset()
        self._aliases: dict[str, set[str]] = {}

    @property
    def names(self) -> frozenset[str]:
        return self._names

    @property
    def ALL_PLUGINS(self) -> set:
        return {name for name, manifest in self._plugins.items() if manifest.is_active}

    def get(self, name: str) -> PluginManifest | None:
        """Return a canonical id directly, or an unambiguous legacy stem."""
        if name in self._plugins:
            return self._plugins[name]
        candidates = self.candidates(name)
        return self._plugins[candidates[0]] if len(candidates) == 1 else None

    def candidates(self, name: str) -> tuple[str, ...]:
        return tuple(sorted(self._aliases.get(name, ())))

    def resolve(self, name: str) -> tuple[str | None, tuple[str, ...]]:
        if name in self._plugins:
            return name, ()
        candidates = self.candidates(name)
        return (candidates[0], ()) if len(candidates) == 1 else (None, candidates)

    def get_all(self) -> list[PluginManifest]:
        return list(self._plugins.values())

    def get_by_status(self, status: str) -> list[PluginManifest]:
        return [
            manifest for manifest in self._plugins.values() if manifest.status == status
        ]

    def get_active(self) -> list[PluginManifest]:
        return self.get_by_status(STATUS_ACTIVE)

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None

    def __len__(self) -> int:
        return len(self._plugins)

    def register(self, manifest: PluginManifest) -> bool:
        """Insert a new canonical id; collisions never replace an existing plugin."""
        if manifest.name in self._plugins:
            return False
        self._plugins[manifest.name] = manifest
        stem = _plugin_base_name(manifest.path or manifest.name)
        self._aliases.setdefault(stem, set()).add(manifest.name)
        self._names = frozenset(self._plugins)
        if manifest.is_active:
            ALL_PLUGINS.add(manifest.name)
        return True

    def unregister(self, name: str) -> PluginManifest | None:
        canonical, candidates = self.resolve(name)
        if canonical is None:
            return None
        manifest = self._plugins.pop(canonical, None)
        if manifest is None:
            return None
        stem = _plugin_base_name(manifest.path or canonical)
        aliases = self._aliases.get(stem)
        if aliases is not None:
            aliases.discard(canonical)
            if not aliases:
                self._aliases.pop(stem, None)
        self._names = frozenset(self._plugins)
        ALL_PLUGINS.discard(canonical)
        return manifest



ALL_PLUGINS: set = set()


# Module-level helpers
def _plugin_base_name(path: str) -> str:
    """Extract the plugin base name (``plugins/foo.py`` → ``foo``)."""
    return path.split("/")[-1].split("\\")[-1].replace(".py", "")


def _plugin_id(path: str) -> str:
    """Return a stable import-style id preserving package/path identity."""
    normalized = os.path.normpath(path).replace("\\", "/")
    if normalized.endswith(".py"):
        normalized = normalized[:-3]
    return normalized.strip("/").replace("/", ".")


def _locate_plugin(name: str) -> str | None:
    """Locate a canonical id or a legacy filename stem without guessing collisions."""
    if "." in name:
        candidate = name.replace(".", os.sep) + ".py"
        if os.path.exists(candidate):
            return candidate
    for folder in ("plugins", "assistant", "addons"):
        candidate = f"{folder}/{name}.py"
        if os.path.exists(candidate):
            return candidate
    return None


def _func_for_path(path: str):
    """Return the import callable appropriate for *path*."""
    if "addons" in path:
        from ..startup.utils import load_addons

        return load_addons
    return import_module


def _key_for_path(path: str, default: str = "Official") -> str:
    if "assistant" in path:
        return "Assistant"
    if "addons" in path:
        return "Addons"
    return default


def _bytecode_fresh(path: str) -> bool:
    """
    Return True when a valid ``__pycache__`` .pyc exists whose mtime is not
    older than the source file  -  i.e. re-compilation can be skipped.
    """
    try:
        pyc = cache_from_source(path)
        if not os.path.exists(pyc):
            return False
        return os.path.getmtime(pyc) >= os.path.getmtime(path)
    except Exception:
        return False


def _compile_check(path: str) -> tuple[bool, str]:
    """
    Compile the source with :func:`compile` to validate syntax BEFORE import.

    Returns ``(ok, error_message)``.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        compile(src, path, "exec")
        return True, ""
    except SyntaxError as exc:
        return False, f"SyntaxError: {exc}"
    except Exception as exc:  # noqa: BLE001  -  any read/compile failure
        return False, f"{exc.__class__.__name__}: {exc}"


def _run_coro_sync(coro):
    """
    Run *coro* to completion from a synchronous context.

    Prefers the Ultroid client's loop (``run_in_loop``); falls back to the
    current event loop or a fresh one. Best-effort  -  never raises.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        coro.close()
        raise RuntimeError(
            "Loader.load() cannot run inside an active event loop; "
            "await AsyncPluginLoader.load_all() or load_plugin() instead."
        )
    try:
        from pyUltroid import ultroid_bot

        if ultroid_bot is not None and callable(
            getattr(ultroid_bot, "run_in_loop", None)
        ):
            return ultroid_bot.run_in_loop(coro)
    except Exception:
        pass
    try:
        return asyncio.run(coro)
    except Exception as exc:
        LOGS.error(f"[loader] sync coroutine run failed: {exc}")
        return None


# HRE (extender) lifecycle bridge
async def _hre_pre_load(name: str, path: str) -> tuple[bool, str]:
    """
    Async pre-load gate. Returns ``(allowed, reason)``. Never raises.
    """
    try:
        from ..extender import hre

        result = await hre.on_plugin_load(name, path)
    except Exception as exc:  # noqa: BLE001  -  HRE must never break a load
        LOGS.error(f"[loader] HRE pre-load hook failed for '{name}': {exc}")
        return True, ""
    if result.can_load:
        if result.block_reason:
            # Soft case: an install failed but the plugin may cope itself.
            LOGS.warning(f"[loader] HRE: {name}: {result.block_reason}")
        return True, ""
    return False, result.block_reason or "blocked by HRE resource policy"


def _hre_pre_load_sync(name: str, path: str) -> tuple[bool, str]:
    """
    Synchronous variant for the legacy sync facades. Never raises.

    Inside a live event loop the sync facade cannot await pip installs, so
    it degrades to the manifest + conflict part of the gate
    (``check_loadable_sync``); the async loader is the supported path inside
    the client loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        running = False
    else:
        running = True
    if running:
        try:
            from ..extender import hre

            result = hre.check_loadable_sync(name, path)
            if not result.can_load:
                return False, result.block_reason or "blocked by HRE resource policy"
        except Exception as exc:  # noqa: BLE001
            LOGS.error(f"[loader] HRE sync pre-load failed for '{name}': {exc}")
        return True, ""
    try:
        return asyncio.run(_hre_pre_load(name, path))
    except Exception as exc:  # noqa: BLE001
        LOGS.error(f"[loader] HRE pre-load dispatch failed for '{name}': {exc}")
        return True, ""


def _hre_post_unload(name: str) -> None:
    """Detach the plugin's HRE manifest after unload. Never raises."""
    try:
        from ..extender import hre

        hre.on_plugin_unload_sync(name)
    except Exception as exc:  # noqa: BLE001
        LOGS.debug(f"[loader] HRE unload hook failed for '{name}': {exc}")


# ══════════════════════════════════════════════════════════════════════════
# Error notice send site (C5)
# ══════════════════════════════════════════════════════════════════════════
#
# A failed plugin load already renders an error card (see
# ``_register_failure``/``_register_failure_async``/``load_plugin`` below).
# The functions here are the *actual send site* for that card plus a text
# notice (:func:`pyUltroid._core.cards.build_error_notice`) to the owner's
# ``LOG_CHANNEL``: best-effort only, since the loader routinely runs before
# any client is connected (module import time, ``--worker`` harnesses,
# offline plugin scans, ...). Never raises and never blocks plugin loading
#  -  no usable connected client / no configured ``LOG_CHANNEL`` silently
# degrades to the pre-existing render-to-disk + log behaviour.

#: Strong refs for fire-and-forget notice tasks scheduled from a sync call
#: site (:func:`_dispatch_error_notice_sync`)  -  without this, nothing holds
#: the task alive and the event loop is free to garbage-collect it before it
#: runs (a classic ``asyncio.create_task`` footgun).
_PENDING_NOTICE_TASKS: set = set()


def _log_channel_clients():
    """Connected clients (``ultroid_bot``/``asst``, de-duplicated) or ``[]``.

    Best-effort: any import/attribute error degrades to "no client
    available" rather than raising. A client that exists but isn't
    connected yet (very early startup) is excluded  -  sending to it would
    just raise inside Telethon anyway.
    """
    try:
        from pyUltroid import asst, ultroid_bot
    except Exception:
        return []
    clients = []
    for client in (ultroid_bot, asst):
        if client is None or client in clients:
            continue
        try:
            if client.is_connected():
                clients.append(client)
        except Exception:
            continue
    return clients


async def _send_error_notice_async(
    name: str, error: str, tb: str, image_path: str | None = None
) -> None:
    """Best-effort: post the rendered error card + text notice to ``LOG_CHANNEL``.

    Silently does nothing (never raises) when: ``pyUltroid.udB`` isn't
    bootstrapped yet, no ``LOG_CHANNEL`` is configured, or no client is
    connected yet  -  exactly the "loader can run before any client exists"
    case this is guarded against. ``image_path`` is the already-rendered
    error card (``generate_error_card``'s return value); ``None`` sends the
    text notice alone.
    """
    try:
        try:
            from pyUltroid import udB
        except Exception:
            return
        if udB is None:
            return
        try:
            chat_id = udB.get_key("LOG_CHANNEL")
        except Exception:
            chat_id = None
        if not chat_id:
            return
        clients = _log_channel_clients()
        if not clients:
            return
        try:
            from .cards import build_error_notice

            notice = build_error_notice(name, error_traceback=tb, error_message=error)
        except Exception as exc:  # noqa: BLE001  -  best-effort, never raise
            LOGS.debug("[loader] error notice build failed for %r: %s", name, exc)
            return
        last_exc = None
        for client in clients:
            try:
                await client.send_message(
                    chat_id,
                    notice.text,
                    file=image_path,
                    formatting_entities=notice.entities or None,
                    parse_mode=notice.parse_mode,
                )
                return
            except Exception as exc:  # noqa: BLE001  -  try the next client
                last_exc = exc
                continue
        if last_exc is not None:
            LOGS.debug("[loader] error notice send failed for %r: %s", name, last_exc)
    except Exception as exc:  # noqa: BLE001  -  a notifier must never raise
        LOGS.debug("[loader] error notice dispatch failed for %r: %s", name, exc)


def _dispatch_error_notice_sync(
    name: str, error: str, tb: str, image_path: str | None = None
) -> None:
    """Fire-and-forget :func:`_send_error_notice_async` from a sync call site.

    Only schedules the send when an event loop is already running  -  the
    synchronous loader entry points (``_load_sync``/legacy ``Loader``) can
    run before any loop starts, in which case this is a silent no-op
    (matching the async call sites' own "no client yet" guard). Never
    raises.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        task = loop.create_task(_send_error_notice_async(name, error, tb, image_path))
        _PENDING_NOTICE_TASKS.add(task)
        task.add_done_callback(_PENDING_NOTICE_TASKS.discard)
    except Exception:  # noqa: BLE001  -  never let scheduling break the loader
        pass


# ══════════════════════════════════════════════════════════════════════════
# Plugin help cache
FALLBACK_TEMPLATE = "{name}\nNo help available"


def _hndlr() -> str:
    try:
        from pyUltroid import HNDLR

        return HNDLR or "."
    except Exception:
        return "."


class PluginHelp:
    """Frozenset-backed cache that keeps legacy HELP buckets synchronized."""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._names: frozenset[str] = frozenset()
        self._rich_cache: dict[str, str] = {}

    @property
    def cached_names(self) -> frozenset[str]:
        return self._names

    @property
    def cache(self) -> dict[str, str]:
        return dict(self._cache)

    @staticmethod
    def format_help(text: str, hndlr: str | None = None) -> str:
        """Format ``{i}`` without losing help containing malformed braces."""
        if not text:
            return ""
        try:
            from pyUltroid import udB

            return text.format(
                i=_hndlr() if hndlr is None else hndlr,
                ASST_USERNAME=udB.get_key("ASST_USERNAME") if udB else "Bot",
            )
        except Exception:
            return text.replace("{i}", _hndlr() if hndlr is None else hndlr)

    def _resolve(self, name: str, module=None) -> str:
        text = None
        try:
            from strings import get_help

            text = get_help(name)
        except Exception:
            pass
        if not text and module is not None:
            text = getattr(module, "__doc__", None)
        return self.format_help(text) if text else FALLBACK_TEMPLATE.format(name=name)

    @staticmethod
    def _resolve_rich(text: str) -> str:
        """Best-effort MarkdownV3-blockquote variant of *text* (Phase C1).

        Keeps the first line (usually the plugin/command header) bare and
        wraps the remaining body lines in an expandable MarkdownV3
        blockquote (``**>``/``>`` line-prefix syntax  -  see
        ``telethon.extensions.markdownv3.MarkdownV3.blockquote_parser``),
        so a caller sending it with ``parse_mode=RICH_MD`` (or ``mdv3``/
        ``md2``, never the classic-markdown-wrapped default parse mode  -
        it has no blockquote support) gets a folded "tap to expand" help
        body instead of a wall of text.

        Returns *text* unchanged (never raises) whenever MarkdownV3 is
        unavailable, or when there's no real multi-line body worth
        quoting  -  callers must treat an unchanged return value as "not
        rich" and keep using the plain default-parse-mode path for it,
        exactly like :meth:`get` already does.
        """
        if not text:
            return text
        try:
            from .entities import RICH_MD
        except Exception:
            return text
        if not RICH_MD.available:
            return text
        lines = text.split("\n")
        if len(lines) < 2:
            return text
        head, body_lines = lines[0], lines[1:]
        # Trailing blank lines would otherwise leave dangling empty ">"
        # lines inside the blockquote.
        while body_lines and not body_lines[-1].strip():
            body_lines.pop()
        if not body_lines:
            return text
        quoted = [
            f"{'**>' if i == 0 else '>'}{line}" for i, line in enumerate(body_lines)
        ]
        return "\n".join([head, *quoted])

    def register(self, name: str, module=None, key: str = "Official") -> str:
        text = self._resolve(name, module)
        self._cache[name] = text
        self._names = frozenset(self._cache)
        try:
            self._rich_cache[name] = self._resolve_rich(text)
        except Exception as exc:
            LOGS.debug("[loader] rich help build failed for %r: %s", name, exc)
        try:
            from pyUltroid.dB._core import HELP, HELP_CHARS

            HELP.setdefault(key, {})[name] = text
            HELP_CHARS.setdefault(key, {})[name] = len(text)
        except Exception as exc:
            LOGS.debug("[loader] HELP sync failed for %r: %s", name, exc)
        # --- Plugin manifest scan ---
        if module is not None:
            try:
                from pyUltroid._core.manifest import scan_module as _scan

                count = _scan(name, module)
                if count:
                    LOGS.info(
                        "[loader] registered %d manifest entry(s) for %r", count, name
                    )
            except Exception as exc:
                LOGS.warning("[loader] manifest scan failed for %r: %s", name, exc)
        return text

    def unregister(self, name: str) -> None:
        self._cache.pop(name, None)
        self._names = frozenset(self._cache)
        self._rich_cache.pop(name, None)
        try:
            from pyUltroid.dB._core import HELP, HELP_CHARS

            for bucket in HELP.values():
                if isinstance(bucket, dict):
                    bucket.pop(name, None)
            for bucket in HELP_CHARS.values():
                if isinstance(bucket, dict):
                    bucket.pop(name, None)
        except Exception:
            pass
        # --- Plugin manifest cleanup ---
        try:
            from pyUltroid._core.manifest import manifest_registry as _reg

            _reg.unregister(name)
        except Exception:
            pass

    def get(self, name: str, module=None) -> str:
        return self._cache.get(name, self._resolve(name, module))

    def get_rich(self, name: str, module=None) -> str:
        """MarkdownV3-blockquote variant of :meth:`get` (Phase C1).

        Returns the cached rich-formatted text built during
        :meth:`register` when one exists; otherwise degrades to
        :meth:`get`'s exact plain-text result (which itself already
        degrades to a fresh :meth:`_resolve` for a name that was never
        registered). Never returns something the plain
        default-parse-mode path hasn't already produced-or-degraded-to
         -  callers must still pair a *changed* result with
        ``parse_mode=RICH_MD``, see :meth:`_resolve_rich`.
        """
        if name in self._rich_cache:
            return self._rich_cache[name]
        return self.get(name, module)

    def all_help(self) -> dict[str, str]:
        return dict(self._cache)




# AsyncPluginLoader
class AsyncPluginLoader:
    """
    Asynchronous plugin loader with per-plugin error isolation.

    Features
    --------
    * ``load_plugin`` compiles the AST (``compile(src, path, 'exec')``) and
      checks syntax BEFORE importing, so a syntax error never contaminates
      other plugins.
    * ``load_all`` loads every plugin in parallel via ``asyncio.gather``;
      each plugin loads inside its own isolated try/except.
    * Bytecode caching: when the cached ``__pycache__`` .pyc is fresh the
      compile pre-check is skipped.
    * HRE manifest semantics: every load passes the extender ResourceManager
      pre-load gate (``__requires__`` auto-install, ``__conflicts__`` hard
      block, ``__optional__``/``__res_tags__`` recorded); every unload
      detaches the manifest. HRE is inert until ``hre.bootstrap()``.
    * Lifecycle hooks: ``on_load`` / ``on_unload`` / ``on_reload``.
    * Error cards: a failed load renders a Pillow card via
      :mod:`pyUltroid._core.cards` and logs it.
    """

    def __init__(
        self,
        path: str = "plugins",
        key: str = "Official",
        logger: Logger = LOGS,
    ) -> None:
        self.path = path
        self.key = key
        self._logger = logger

        self._import_lock = asyncio.Lock()

        self._after_load = None

    # ── lifecycle hooks (overridable)
    def on_load(self, manifest: PluginManifest) -> None:
        self._logger.info(f"[loader] loaded '{manifest.name}' ({manifest.status})")

    def on_unload(self, manifest: PluginManifest) -> None:
        self._logger.info(f"[loader] unloaded '{manifest.name}'")

    def on_reload(self, manifest: PluginManifest) -> None:
        self._logger.info(f"[loader] reloaded '{manifest.name}'")

    # ── file discovery
    def _gather_files(
        self,
        directory: str,
        include=None,
        exclude=None,
        load_all_files: bool = False,
    ) -> list[str]:
        single = os.path.isfile(directory)
        if include:
            files = glob.glob(f"{directory}/_*.py")
            for file in include:
                p = f"{directory}/{file}.py"
                if os.path.exists(p):
                    files.append(p)
            return sorted(files)
        if single:
            return [directory]
        if load_all_files:
            # Deferred: ``loader`` is imported from inside the ``fns`` init
            # chain (via ``startup.patching`` -> ``_core/__init__``), so a
            # module-level ``from ..fns import fns`` would read a partially
            # initialized package and collapse the whole chain to ``None``.
            from ..fns import fns

            files = fns.get_all_files(directory, ".py")
        else:
            files = glob.glob(f"{directory}/*.py")
        if exclude:
            for path in exclude:
                if not path.startswith("_"):
                    with contextlib.suppress(ValueError):
                        files.remove(f"{directory}/{path}.py")
        return sorted(files)

    # ── import + manifest building
    @staticmethod
    def _import_plugin(path: str, func):
        """Run the import callable for *path*, deriving the module name."""
        module_name = path
        # import_module wants a dotted module path; addons loaders want the file path.
        if getattr(func, "__name__", "") == "import_module":
            module_name = path.replace(".py", "").replace("/", ".").replace("\\", ".")
        return func(module_name)

    @staticmethod
    def _build_manifest(name: str, path: str, module) -> PluginManifest:
        version = getattr(module, "__version__", None) or "1.0"
        author = getattr(module, "__author__", None) or "Unknown"
        doc = getattr(module, "__doc__", "") or ""
        description = doc.strip().splitlines()[0] if doc.strip() else ""
        raw_deps = getattr(module, "__requires__", None) or []
        dependencies = (
            tuple(raw_deps) if isinstance(raw_deps, (list, tuple, set)) else ()
        )
        try:
            from pyUltroid.dB._core import LIST, LOADED

            handler_count = len(LIST.get(name, [])) + len(LOADED.get(name, []))
        except Exception:
            handler_count = 0
        return PluginManifest(
            name=name,
            version=str(version),
            author=str(author),
            description=description,
            path=path,
            dependencies=dependencies,
            handler_count=handler_count,
        )

    def _register_success(self, name, path, module, key) -> PluginManifest:
        from ..ultroid import core
        canonical_id = _plugin_id(path)
        manifest = self._build_manifest(canonical_id, path, module)
        if core.registry.get(canonical_id) is not None:
            return self._register_failure(
                canonical_id,
                path,
                f"Duplicate plugin id: {canonical_id}",
                "",
                render_card=False,
            )
        manifest.status = STATUS_ACTIVE
        manifest.loaded_at = time.time()
        core.registry.register(manifest)

        try:
            from .loader import plugin_help

            core.plugin_help.register(manifest.name, module, key)
        except Exception as exc:
            self._logger.debug(f"[loader] help register failed for '{name}': {exc}")
        # Lifecycle hook.
        try:
            self.on_load(manifest)
        except Exception as exc:
            self._logger.exception(exc)
        # Legacy per-plugin callback (backward compat).
        if callable(self._after_load):
            try:
                self._after_load(self, module, plugin_name=name)
            except Exception as exc:
                self._logger.exception(exc)
        return manifest

    def _register_failure(
        self, name, path, error, tb, *, render_card: bool = True
    ) -> PluginManifest:
        from ..ultroid import core
        manifest = PluginManifest(
            name=name,
            path=path,
            status=STATUS_ERRORED,
            error_info=error,
            loaded_at=time.time(),
        )
        core.registry.register(manifest)
        # The synchronous compatibility loader may render directly. The async
        # path below offloads this CPU/file work with asyncio.to_thread.
        if render_card:
            image_path = None
            try:
                from .cards import generate_error_card

                image_path = generate_error_card(
                    name, error_traceback=tb, error_message=error
                )
            except Exception as exc:
                self._logger.debug(f"[loader] error card failed for '{name}': {exc}")
            _dispatch_error_notice_sync(name, error, tb, image_path)
        self._logger.error(f"[loader] plugin '{name}' failed to load: {error}")
        return manifest

    async def _register_failure_async(self, name, path, error, tb) -> PluginManifest:
        manifest = self._register_failure(name, path, error, tb, render_card=False)
        image_path = None
        try:
            from .cards import generate_error_card

            image_path = await asyncio.to_thread(
                generate_error_card, name, error_traceback=tb, error_message=error
            )
        except Exception as exc:
            self._logger.debug("[loader] error card failed for %r: %s", name, exc)
        await _send_error_notice_async(name, error, tb, image_path)
        return manifest

    def _clients(self):
        clients = []
        try:
            from pyUltroid import asst, ultroid_bot

            clients = [client for client in (ultroid_bot, asst) if client is not None]
        except Exception:
            pass
        return list(dict.fromkeys(clients))

    def _handler_snapshot(self):
        clients = self._clients()
        snapshot = {}
        for client in clients:
            try:
                snapshot[client] = list(client.list_event_handlers())
            except Exception:
                snapshot[client] = []
        return snapshot

    def _dispatcher_snapshot(self):
        snapshot = {}
        for client in self._clients():
            dispatcher = getattr(client, "dispatcher", None)
            if dispatcher is None:
                continue
            snapshot[dispatcher] = (
                {
                    name: list(entries)
                    for name, entries in getattr(dispatcher, "_commands", {}).items()
                },
                {
                    kind: list(entries)
                    for kind, entries in getattr(dispatcher, "_listeners", {}).items()
                },
            )
        return snapshot

    def _owned_dispatcher_entries(self, before):
        owned = []
        for dispatcher, (commands, listeners) in before.items():
            for name, entries in getattr(dispatcher, "_commands", {}).items():
                old = commands.get(name, [])
                for entry in entries:
                    if entry not in old:
                        owned.append((dispatcher, "command", name, entry.func))
            for kind, entries in getattr(dispatcher, "_listeners", {}).items():
                old = listeners.get(kind, [])
                for entry in entries:
                    if entry not in old:
                        owned.append((dispatcher, "listener", kind, entry.func))
        return owned

    @staticmethod
    def _tag_ownership(name: str, manifest) -> int:
        """Label every handler in ``manifest`` with the owning plugin name."""
        from . import internals

        tagged = 0
        for _client, callback, _event in manifest.handlers:
            internals.tag_owner(callback, name)
            tagged += 1
        for _dispatcher, _kind, _key, callback in manifest.dispatcher_entries:
            internals.tag_owner(callback, name)
            tagged += 1
        return tagged

    @contextlib.contextmanager
    def _owner_scope(self, name: str):
        """Own every pipeline route registered inside the block by ``name``.

        Snapshot diffing cannot see routes, because routes live in the pipeline
        rather than in ``_event_builders``. Binding the plugin id for the
        duration of the import gives those routes an owner, so unload retires
        them with the rest of the plugin.
        """
        from .pipeline import existing_pipeline

        with contextlib.ExitStack() as stack:
            for client in self._clients():
                pipeline = existing_pipeline(client)
                if pipeline is not None:
                    stack.enter_context(pipeline.owned_by(name))
            yield

    def _owned_handlers(self, before):
        owned = []
        for client, prior in before.items():
            try:
                after = list(client.list_event_handlers())
            except Exception:
                continue
            remaining = list(prior)
            for entry in after:
                if entry in remaining:
                    remaining.remove(entry)
                else:
                    callback, event = entry
                    owned.append((client, callback, event))
        return owned

    def _import_and_register(
        self, path, func, key, *, render_card: bool = True
    ) -> PluginManifest:
        """Import serially and retain exact handlers newly registered by it."""
        name = _plugin_id(path)
        before_handlers = self._handler_snapshot()
        before_dispatcher = self._dispatcher_snapshot()
        try:
            with self._owner_scope(name):
                module = self._import_plugin(path, func)
        except ModuleNotFoundError as er:
            self._logger.error(f"{path}: '{er.name}' not installed!")
            # Attempt auto-install before giving up
            from pyUltroid._core.manifest import install_missing

            if install_missing(er.name, path, reason="auto-install on import failure"):
                try:
                    with self._owner_scope(name):
                        module = self._import_plugin(path, func)
                except ModuleNotFoundError:
                    return self._register_failure(
                        name,
                        path,
                        f"ModuleNotFoundError: {er.name} (install succeeded but import still failed)",
                        format_exc(),
                        render_card=render_card,
                    )
                except Exception as exc:
                    self._logger.error(f"pyUltroid - {key} - ERROR - {path}")
                    self._logger.exception(exc)
                    return self._register_failure(
                        name,
                        path,
                        f"{exc.__class__.__name__}: {exc}",
                        format_exc(),
                        render_card=render_card,
                    )
            else:
                return self._register_failure(
                    name,
                    path,
                    f"ModuleNotFoundError: {er.name}",
                    format_exc(),
                    render_card=render_card,
                )
        except Exception as exc:
            self._logger.error(f"pyUltroid - {key} - ERROR - {path}")
            self._logger.exception(exc)
            return self._register_failure(
                name,
                path,
                f"{exc.__class__.__name__}: {exc}",
                format_exc(),
                render_card=render_card,
            )
        requirements = getattr(module, "__requires__", ()) or ()
        missing = []
        for dependency in (
            requirements if isinstance(requirements, (list, tuple, set)) else ()
        ):
            dependency = str(dependency)
            # PEP 440 specifiers ("pillow>=10.0") and dist names that differ
            # from their import name ("pillow" -> "PIL") cannot be checked with
            # find_spec; delegate to the extender's metadata-based DepSpec.
            try:
                if not DepSpec.parse(dependency).version_satisfied():
                    missing.append(dependency)
            except Exception:
                missing.append(dependency)
        if missing:
            return self._register_failure(
                name,
                path,
                "Missing plugin dependencies: " + ", ".join(missing),
                "",
                render_card=render_card,
            )
        manifest = self._register_success(name, path, module, key)
        manifest.handlers.extend(self._owned_handlers(before_handlers))
        manifest.dispatcher_entries.extend(
            self._owned_dispatcher_entries(before_dispatcher)
        )
        # Stamp the plugin name onto every handler it just registered. Unload
        # still removes handlers from the manifest's exact records above; the
        # stamp is what lets internals.handlers_by_owner() and describe()
        # explain who owns a handler on a live client.
        self._tag_ownership(name, manifest)
        manifest.handler_count = len(manifest.handlers) + len(
            manifest.dispatcher_entries
        )
        self._drive_modules(name, module)
        return manifest

    def _drive_modules(self, plugin_name: str, module) -> None:
        """W3-B Module driver: adopt Module classes this plugin defined.

        Runs for every plugin file; the scan is a cheap ``vars()`` walk, so
        decorator-only plugins pay nothing. Failures are loud (error log +
        traceback) - a Module that cannot bind must never die silently the
        way the old import-time self-binding did. Starting is left to the
        modules' lazy ``ready()`` / a scheduled ``ensure_started``, because
        this path must also work from the legacy synchronous loader.
        """
        try:
            from .. import LOGS, asst, ultroid_bot
            from .._misc import misc

            clients = [c for c in (ultroid_bot, asst) if c is not None]
            report = misc.registry.adopt(module, clients=clients or None)
        except Exception as exc:
            self._logger.exception(f"[module-driver] failed for {plugin_name}: {exc}")
            return
        for mod_name, info in report.items():
            if "error" in info:
                self._logger.error(
                    f"[module-driver] {plugin_name} -> {mod_name} NOT BOUND: "
                    f"{info['error']}"
                )
            else:
                self._logger.info(
                    f"[module-driver] {plugin_name} -> {mod_name}: "
                    f"bound {info['handlers']} handler(s)"
                )
        if report:
            # Best-effort early start on the running loop; without one (the
            # legacy sync path) modules lazy-start via ``Module.ready()``.
            try:
                import asyncio

                asyncio.get_running_loop().create_task(
                    misc.registry.ensure_started()
                )
            except RuntimeError:
                pass

    def _load_sync(self, path, func=import_module, key=None) -> PluginManifest | None:
        """Synchronous load: compile-check + import + register."""
        key = key or self.key
        name = _plugin_id(path)
        if _plugin_base_name(path).startswith("__"):
            return None
        ok, err = _compile_check(path)
        if not ok:
            return self._register_failure(name, path, err, format_exc())
        hre_ok, hre_reason = _hre_pre_load_sync(name, path)
        if not hre_ok:
            return self._register_failure(name, path, hre_reason, "")
        return self._import_and_register(path, func, key)

    # ── public async API
    async def load_plugin(
        self,
        path: str,
        func=import_module,
        key: str | None = None,
    ) -> PluginManifest | None:
        """
        Load a single plugin file asynchronously.

        Compiles the AST first (offloaded to a thread executor) to catch
        syntax errors before import, then imports in an isolated try/except.
        Returns the resulting :class:`PluginManifest` (status ``active`` or
        ``errored``), or ``None`` for dunder files.
        """
        key = key or self.key
        name = _plugin_id(path)
        if _plugin_base_name(path).startswith("__"):
            return None

        loop = asyncio.get_event_loop()

        # Bytecode cache: skip the compile pre-check when the .pyc is fresh.
        try:
            fresh = await loop.run_in_executor(None, _bytecode_fresh, path)
        except Exception:
            fresh = False

        if not fresh:
            try:
                ok, err = await loop.run_in_executor(None, _compile_check, path)
            except Exception as exc:  # noqa: BLE001
                ok, err = False, f"{exc.__class__.__name__}: {exc}"
            if not ok:
                return await self._register_failure_async(name, path, err, format_exc())

        # HRE resource gate: parse the dependency manifest, install missing
        # __requires__, and hard-block when a __conflicts__ package is present.
        hre_ok, hre_reason = await _hre_pre_load(name, path)
        if not hre_ok:
            return await self._register_failure_async(name, path, hre_reason, "")

        async with self._import_lock:
            manifest = self._import_and_register(path, func, key, render_card=False)
        if manifest.is_errored:
            image_path = None
            try:
                from .cards import generate_error_card

                image_path = await asyncio.to_thread(
                    generate_error_card,
                    name,
                    error_traceback=manifest.error_info or "",
                    error_message=manifest.error_info or "",
                )
            except Exception as exc:
                self._logger.debug("[loader] error card failed for %r: %s", name, exc)
            await _send_error_notice_async(
                name, manifest.error_info or "", manifest.error_info or "", image_path
            )
        return manifest

    async def load_all(
        self,
        directory: str | None = None,
        func=import_module,
        include=None,
        exclude=None,
        log: bool = True,
        load_all_files: bool = False,
    ) -> tuple[int, int, list[dict]]:
        """
        Load every plugin in *directory* in parallel via ``asyncio.gather``.

        Each plugin loads in its own isolated try/except  -  one bad plugin
        never stops the others. Returns
        ``(success_count, error_count, errors)`` where *errors* is a list of
        ``{"plugin", "error"}`` dicts.
        """
        directory = directory or self.path
        single = os.path.isfile(directory)
        files = self._gather_files(
            directory,
            include=include,
            exclude=exclude,
            load_all_files=load_all_files,
        )
        if log and not single and files:
            self._logger.info(
                f"• Installing {self.key} Plugins || Count : {len(files)} •"
            )
        if not files:
            return 0, 0, []

        tasks = [self.load_plugin(p, func=func) for p in files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = error = 0
        errors: list[dict] = []
        for path, result in zip(files, results):
            name = _plugin_id(path)
            if isinstance(result, Exception):
                error += 1
                errors.append({"plugin": name, "error": str(result)})
            elif result is None:
                continue  # dunder file, skipped
            elif result.status == STATUS_ERRORED:
                error += 1
                errors.append({"plugin": name, "error": result.error_info or "unknown"})
            else:
                success += 1

        # Persist errored plugins so they are retried on the next startup.
        _persist_errored()
        return success, error, errors

    # ── unload
    @staticmethod
    def _func_belongs_to_plugin(func, name: str) -> bool:
        """Heuristic: does *func* belong to plugin *name*?"""
        mod = getattr(func, "__module__", "") or ""
        wrapped = getattr(func, "__wrapped__", None)
        if wrapped is not None:
            wmod = getattr(wrapped, "__module__", "") or ""
            if wmod:
                mod = wmod
        return mod == name or mod.endswith("." + name)

    def _unregister_dispatcher(self, dispatcher, name: str) -> int:
        """Remove dispatcher commands/listeners owned by *name*. Returns count."""
        removed = 0
        for cmd_name in list(getattr(dispatcher, "_commands", {})):
            for entry in list(dispatcher._commands.get(cmd_name, [])):
                if self._func_belongs_to_plugin(entry.func, name):
                    with contextlib.suppress(Exception):
                        dispatcher.unregister_command(cmd_name, entry.func)
                        removed += 1
        for ut in list(getattr(dispatcher, "_listeners", {})):
            for entry in list(dispatcher._listeners.get(ut, [])):
                if self._func_belongs_to_plugin(entry.func, name):
                    with contextlib.suppress(Exception):
                        dispatcher.unregister_listener(ut, entry.func)
                        removed += 1
        return removed

    def _do_unload(self, name: str) -> dict:
        """
        Synchronously unload *name*: remove all its event handlers (via the
        dispatcher's unregister methods and ``remove_event_handler``), purge
        ``sys.modules``, drop it from the registry, and return a confirmation
        dict ``{name, handlers_removed, success}``.
        """
        from ..ultroid import core
        from pyUltroid.dB._core import ADDONS, HELP, LIST, LOADED

        canonical, candidates = core.registry.resolve(name)
        if canonical is None:
            return {
                "name": name,
                "handlers_removed": 0,
                "success": False,
                "candidates": list(candidates),
                "error": "ambiguous plugin id" if candidates else "plugin not found",
            }
        name = canonical
        manifest = core.registry.get(name)
        if manifest is not None:
            try:
                self.on_unload(manifest)
            except Exception as exc:
                self._logger.exception(exc)

        handlers_removed = 0

        # 1. Remove only exact registrations captured during this plugin import.
        for client, callback, event in (manifest.handlers if manifest else ()):
            try:
                client.remove_event_handler(callback, event)
                handlers_removed += 1
            except TypeError:
                # Some Telethon builds accept only callback; this is still exact
                # because the callback was captured from this plugin import.
                with contextlib.suppress(Exception):
                    client.remove_event_handler(callback)
                    handlers_removed += 1

        # 2. Legacy lists are cleaned without blindly removing handlers. Modern
        # manifests always have exact ownership records; legacy addons are left
        # registered rather than risking another plugin's callback.
        LOADED.pop(_plugin_base_name(manifest.path) if manifest else name, None)

        # 3. Remove only dispatcher registrations captured during the import.
        for dispatcher, kind, key, callback in (
            manifest.dispatcher_entries if manifest else ()
        ):
            with contextlib.suppress(Exception):
                if kind == "command":
                    dispatcher.unregister_command(key, callback)
                else:
                    dispatcher.unregister_listener(key, callback)
                handlers_removed += 1

        # 3b. Retire the plugin's update-pipeline routes, if it registered any.
        from .pipeline import existing_pipeline

        for client in self._clients():
            pipeline = existing_pipeline(client)
            if pipeline is not None:
                handlers_removed += pipeline.remove_owner(name)

        # 3c. Drop Module-layer instances this plugin file contributed, so
        # ``.plugins unload`` removes the registry entries too, not just the
        # Telethon handlers the manifest recorded (W3-B driver parity).
        try:
            from .._misc import misc

            for gone in misc.registry.drop_from(name):
                self._logger.info(f"[module-driver] unloaded module {gone}")
        except Exception as exc:
            self._logger.debug(f"[module-driver] drop_from failed: {exc}")

        # 4. Legacy collections.
        LIST.pop(_plugin_base_name(manifest.path) if manifest else name, None)
        if name in ADDONS:
            with contextlib.suppress(ValueError):
                ADDONS.remove(name)

        # 5. Purge module cache from sys.modules.
        for key in list(sys.modules):
            if key == name or key.endswith(f".{name}"):
                sys.modules.pop(key, None)

        # 6. Registry + backward-compat set + decoupled help + legacy HELP.
        core.registry.unregister(name)
        try:
            from .loader import plugin_help

            core.plugin_help.unregister(name)
        except Exception:
            pass
        # Addons-style plugins register string bodies (``HELP.update(
        # {name: docstring})``)  -  only dict buckets are keyed per-plugin.
        # Pop both the canonical id and the file basename, mirroring the
        # LOADED/LIST cleanup above, so no help entry survives a reload.
        _help_keys = {_plugin_base_name(manifest.path) if manifest else name, name}
        for bucket in HELP.values():
            if isinstance(bucket, dict):
                for _k in _help_keys:
                    bucket.pop(_k, None)

        # 7. HRE lifecycle: detach the plugin's resource manifest. Orphan
        # cleanup stays an explicit user action (.depclean)  -  never implicit.
        _hre_post_unload(name)

        return {"name": name, "handlers_removed": handlers_removed, "success": True}

    async def unload_plugin(self, name: str) -> dict:
        """Asynchronously unload *name*. Returns the confirmation dict."""
        return self._do_unload(name)

    # ── reload
    @staticmethod
    def _preserve_metadata(old: PluginManifest | None) -> dict:
        if old is None:
            return {}
        return {
            "version": old.version,
            "author": old.author,
            "description": old.description,
        }

    @staticmethod
    def _apply_preserved(manifest: PluginManifest, preserved: dict) -> None:
        """Restore non-default metadata onto a freshly loaded manifest."""
        from ..ultroid import core
        if not manifest or not manifest.is_active or not preserved:
            return
        if manifest.version in ("1.0", "", None) and preserved.get("version") not in (
            None,
            "",
            "1.0",
        ):
            manifest.version = preserved["version"]
        if manifest.author in ("Unknown", "", None) and preserved.get("author") not in (
            None,
            "",
            "Unknown",
        ):
            manifest.author = preserved["author"]
        if not manifest.description and preserved.get("description"):
            manifest.description = preserved["description"]
        core.registry.register(manifest)

    async def reload_plugin(
        self,
        name: str,
        path: str | None = None,
        func=None,
        key: str | None = None,
    ) -> dict:
        """Unload *name*, then load its file fresh, preserving metadata."""
        from ..ultroid import core
        old = core.registry.get(name)
        preserved = self._preserve_metadata(old)

        if not path:
            path = old.path if old else _locate_plugin(name)
        if not path:
            raise FileNotFoundError(f"Could not locate plugin file path for '{name}'")
        if func is None:
            func = _func_for_path(path)
        if key is None:
            key = _key_for_path(path, self.key)

        await self.unload_plugin(name)
        manifest = await self.load_plugin(path, func=func, key=key)

        if manifest is not None:
            self._apply_preserved(manifest, preserved)
            try:
                self.on_reload(manifest)
            except Exception as exc:
                self._logger.exception(exc)

        return {
            "name": name,
            "manifest": manifest,
            "success": bool(manifest and manifest.is_active),
        }

    def _reload_sync(self, name: str, path: str | None = None) -> dict:
        """Synchronous reload used by the backward-compat module function."""
        from ..ultroid import core
        old = core.registry.get(name)
        preserved = self._preserve_metadata(old)
        if not path:
            path = old.path if old else _locate_plugin(name)
        if not path:
            raise FileNotFoundError(f"Could not locate plugin file path for '{name}'")
        func = _func_for_path(path)
        key = _key_for_path(path, self.key)
        self._do_unload(name)
        manifest = self._load_sync(path, func=func, key=key)
        if manifest is not None:
            self._apply_preserved(manifest, preserved)
            try:
                self.on_reload(manifest)
            except Exception as exc:
                self._logger.exception(exc)
        return {
            "name": name,
            "manifest": manifest,
            "success": bool(manifest and manifest.is_active),
        }




# Module-level load / unload / reload (backward compatible)
#
#: The default loader and the plugin registry the module-level helpers use.
#: Both are wired by ``pyUltroid._core.__init__`` once the Core union exists
#: (importing ``._core`` from here at module scope would be circular: the
#: ``_core`` package imports this module first). Tests patch these to drive
#: reloads with their own loader/registry instances.
_default_loader: "AsyncPluginLoader | None" = None
registry: "PluginRegistry | None" = None


def _get_default_loader() -> "AsyncPluginLoader":
    """The patched-in loader if any, else the Core union's loader."""
    if _default_loader is not None:
        return _default_loader
    from ..ultroid import core

    return core.loader


def unload_plugin(name: str) -> dict:
    """
    Synchronously unload a plugin.

    Backward-compatible signature for legacy callers (e.g.
    ``pyUltroid.fns.helper.un_plug``). Returns the confirmation dict
    ``{name, handlers_removed, success}``.
    """
    return _get_default_loader()._do_unload(name)


async def unload_plugin_async(name: str) -> dict:
    """Asynchronously unload  -  the modern entry point."""
    return await _get_default_loader().unload_plugin(name)


async def reload_plugin_async(name: str, path: str | None = None) -> dict:
    """Asynchronously reload  -  the modern entry point."""
    return await _get_default_loader().reload_plugin(name, path=path)


# Dynamic unloading with button confirmation
async def confirm_unload(
    client, chat_id, plugin_name: str, timeout: float = 60.0
) -> bool:
    """Ask an owner/sudo once whether to unload a plugin, then clean up."""
    import re as _re

    from telethon import events
    from custom.tg import ikm

    yes_data = f"unld_yes:{plugin_name}".encode()
    no_data = f"unld_no:{plugin_name}".encode()
    pattern = _re.compile(b"unld_(?:yes|no):" + _re.escape(plugin_name.encode()))
    message = await client.send_message(
        chat_id,
        f"Unload `{plugin_name}`?",
        buttons=ikm([[("Yes", yes_data, "success"), ("No", no_data, "danger")]]),
    )
    state = {"done": False}

    def _authorized(event) -> bool:
        try:
            from pyUltroid import udB

            if udB is None:
                raise RuntimeError("udB unavailable")
            owner = udB.get_key("OWNER_ID")
            sudos = udB.get_key("SUDOS") or []
            return getattr(event, "sender_id", None) in {owner, *sudos}
        except Exception:
            # In standalone/test use, limit interaction to the message sender.
            return getattr(event, "sender_id", None) == getattr(
                getattr(message, "sender", None), "id", None
            )

    def _remove_handler():
        with contextlib.suppress(Exception):
            client.remove_event_handler(_callback)

    async def _finish(text: str):
        # Prefer ``eor``: it edits our own outgoing prompt and sends instead when
        # the edit is impossible, which is exactly the retry this used to do by
        # hand. ``message.eor`` exists once ``apply_patches()`` has run, and
        # ``Message.edit`` is the floor for a client that never bootstrapped.
        state["done"] = True
        with contextlib.suppress(Exception):
            send = getattr(message, "eor", None) or message.edit
            await send(text)
        _remove_handler()

    async def _callback(event):
        if state["done"]:
            return
        if not _authorized(event):
            with contextlib.suppress(Exception):
                await event.answer("Only the owner or a sudo can do that.", alert=True)
            return
        with contextlib.suppress(Exception):
            await event.answer()
        data = bytes(getattr(event, "data", b"")).decode(errors="ignore")
        if data == f"unld_yes:{plugin_name}":
            result = await unload_plugin_async(plugin_name)
            if result.get("success"):
                await _finish(
                    f"Unloaded `{plugin_name}`  -  removed {result.get('handlers_removed', 0)} handler(s)."
                )
            else:
                await _finish(
                    f"Could not unload `{plugin_name}`: `{result.get('error', 'unknown error')}`"
                )
        elif data == f"unld_no:{plugin_name}":
            await _finish("Unload cancelled.")

    client.add_event_handler(_callback, events.CallbackQuery(pattern=pattern))

    async def _expire():
        await asyncio.sleep(timeout)
        if not state["done"]:
            await _finish("Unload confirmation timed out.")

    asyncio.create_task(_expire())
    return True


# Fallback system  -  errored plugins are retried and exposed via .errors
def _error_paths_value(value) -> list[str]:
    """Normalize native database values and older JSON-string storage."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [path for path in value if isinstance(path, str) and path.endswith(".py")]


def _persist_errored() -> None:
    """Persist errored plugin paths in the database's native list format."""
    from ..ultroid import core
    try:
        from pyUltroid import udB

        if udB is not None:
            udB.set_key(
                "ERRORED_PLUGINS",
                [
                    manifest.path
                    for manifest in core.registry.get_by_status(STATUS_ERRORED)
                    if manifest.path
                ],
            )
    except Exception:
        pass


def _load_errored_paths() -> list[str]:
    """Read persisted failures from native-list or legacy JSON-string storage."""
    try:
        from pyUltroid import udB

        return _error_paths_value(udB.get_key("ERRORED_PLUGINS") if udB else None)
    except Exception:
        return []


async def retry_persisted_errors(max_retries: int = 3, func=None) -> list[str]:
    """Seed registry entries from persisted paths and retry them safely."""
    from ..ultroid import core
    for path in _load_errored_paths():
        plugin_id = _plugin_id(path)
        if core.registry.get(plugin_id) is None:
            core.registry.register(
                PluginManifest(name=plugin_id, path=path, status=STATUS_ERRORED)
            )
    return await retry_errored(max_retries=max_retries, func=func)


async def retry_errored(max_retries: int = 3, func=None) -> list[str]:
    """
    Retry every errored plugin up to *max_retries* times this restart.

    Returns the list of plugin names that are *still* errored after retrying.
    """
    from ..ultroid import core
    for attempt in range(1, max_retries + 1):
        errored = core.registry.get_by_status(STATUS_ERRORED)
        if not errored:
            break
        for manifest in errored:
            # Any ImportError is permanent - a missing module or a broken
            # optional dependency can never import on a retry. Both class
            # names appear in error_info (``ModuleNotFoundError`` is the
            # subclass but its NAME does not contain "ImportError", so
            # both strings are checked).
            if manifest.error_info and (
                "ImportError" in manifest.error_info
                or "ModuleNotFoundError" in manifest.error_info
            ):
                continue
            path = manifest.path or _locate_plugin(manifest.name)
            if not path:
                continue
            load_func = func or _func_for_path(path)
            key = _key_for_path(path)
            # Drop the stale errored entry before re-attempting.
            core.registry.unregister(manifest.name)
            result = await core.loader.load_plugin(path, func=load_func, key=key)
            if result and result.is_active:
                LOGS.info(f"[loader] retry OK: {manifest.name} (attempt {attempt})")
            else:
                LOGS.warning(
                    f"[loader] retry failed: {manifest.name} (attempt {attempt})"
                )
    _persist_errored()
    return [m.name for m in core.registry.get_by_status(STATUS_ERRORED)]


def register_errors_command(client=None) -> bool:
    """
    Register a ``.errors`` command on the dispatcher that lists errored plugins.

    Exposes the fallback system to the owner at runtime. Best-effort: returns
    ``False`` (and logs at debug) if no dispatcher is available.
    """
    from ..ultroid import core
    try:
        from pyUltroid.startup.dispatcher import iter_dispatchers

        async def _errors_cmd(update, args, reply_msg):
            errored = core.registry.get_by_status(STATUS_ERRORED)
            if not errored:
                return "✅ No errored plugins."
            lines = [f"**Errored plugins ({len(errored)}):**", ""]
            for manifest in errored:
                err = (manifest.error_info or "unknown error")[:120]
                lines.append(f"• `{manifest.name}`  -  {err}")
            return "\n".join(lines)

        # W3-A: fan the .errors command out to every live dispatcher, not just the
        # primary assistant / user client.
        registered = False
        if client is not None:
            dispatcher = getattr(client, "dispatcher", None)
            if dispatcher is not None:
                dispatcher.register_command("errors", _errors_cmd)
                registered = True
        else:
            for dispatcher in iter_dispatchers():
                dispatcher.register_command("errors", _errors_cmd)
                registered = True
        return registered
    except Exception as exc:
        LOGS.debug(f"[loader] could not register .errors command: {exc}")
        return False


__all__ = [
    # Core types
    "PluginManifest",
    "PluginRegistry",
    "PluginHelp",
    "AsyncPluginLoader",
    # Status constants
    "STATUS_ACTIVE",
    "STATUS_INACTIVE",
    "STATUS_ERRORED",
    # Backward-compatible globals
    "ALL_PLUGINS",
    "unload_plugin",
    # Modern async entry points
    "unload_plugin_async",
    "reload_plugin_async",
    "confirm_unload",
    # Fallback system
    "retry_errored",
    "retry_persisted_errors",
    "_load_errored_paths",
    "register_errors_command",
]

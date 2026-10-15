# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

""":class:`ModuleRegistry` - owns module instances, their bound handlers,
and their lifecycle.

Concept ported from ``re-caligo``'s ``core/extender.py`` (the name -> module
registry, load/unload) fused with ``core/dispatcher.py`` (priority-ordered
listeners and a dispatch loop).

The two halves are kept as two halves here, because they answer different
questions and have different failure modes:

* **Binding** (:meth:`bind` / :meth:`unbind`) attaches a module's handlers
  to Telethon clients. It is reversible and idempotent, and a *bind* failure
  rolls the whole module back - a half-bound module is worse than an
  unbound one.
* **Dispatching** (:meth:`dispatch`) runs one lifecycle hook across every
  registered module, isolating failures so one broken module cannot stop the
  others from starting.

Neither half knows about :mod:`pyUltroid.universal`; the Module layer is
opt-in and independent.
"""

from __future__ import annotations

import bisect
import inspect
import pkgutil
from importlib import import_module
from typing import Any, Callable, Final, Iterable

from .listener import Listener
from .module import (
    STARTED,
    STARTING,
    STOPPED,
    STOPPING,
    Module,
    ModuleError,
    ModuleExists,
    ModuleMissing,
)

#: lifecycle hook order
LIFECYCLE: Final[tuple[str, ...]] = (STARTING, STARTED, STOPPING, STOPPED)


class ModuleRegistry:
    """The name -> module registry, plus binding and lifecycle dispatch."""

    def __init__(self, client: Any = None, log: Any = None) -> None:
        self.client = client
        self.modules: dict[str, Module] = {}
        self.listeners: list[Listener] = []
        self.log = log
        self._started = False
        self._started_modules: set[str] = set()

    # -- logging -----------------------------------------------------------

    def _log(self, level: str, message: str, *args: Any) -> None:
        if self.log is None:
            return
        getattr(self.log, level)(message, *args)

    # -- registration ------------------------------------------------------

    def register(self, module: Module, *, replace: bool = False) -> Module:
        """Add an already-built module. Raises on a duplicate name."""
        key = type(module).__name__
        if key in self.modules and not replace:
            raise ModuleExists(key)
        if key in self.modules:
            self.unregister(self.modules[key])
        self.modules[key] = module
        return module

    def unregister(self, name: str | Module) -> Module:
        """Drop a module from the registry (binding is separate)."""
        module = self.modules.get(name) if not isinstance(name, Module) else name
        key = type(module).__name__ if module is not None else str(name)
        if module is None:
            raise ModuleMissing(key)
        self.modules.pop(key, None)
        return module

    def get(self, name: str) -> Module | None:
        return self.modules.get(name)

    def __contains__(self, name: object) -> bool:
        return name in self.modules

    def __len__(self) -> int:
        return len(self.modules)

    def names(self) -> tuple[str, ...]:
        return tuple(self.modules)

    # -- loading -----------------------------------------------------------

    def load(self, cls: type[Module], *, replace: bool = False, **options: Any) -> Module:
        """Instantiate *cls*, register it, and return it (no binding)."""
        if not issubclass(cls, Module):
            raise ModuleError(f"{cls!r} is not a Module subclass")
        name = cls.__name__
        if name in self.modules and not replace:
            raise ModuleExists(name)
        module = cls(self.client, registry=self, **options)
        return self.register(module, replace=replace)

    def loads(
        self,
        classes: Iterable[type[Module]] | None = None,
        *,
        replace: bool = False,
        **options: Any,
    ) -> dict[str, Module]:
        """Load many classes, isolating per-module failures."""
        loaded: dict[str, Module] = {}
        for cls in classes or ():
            try:
                loaded[cls.__name__] = self.load(cls, replace=replace, **options)
            except ModuleExists:
                continue
            except Exception as exc:
                self._log("error", "module load failed %s: %s", cls.__name__, exc)
        return loaded

    def scan(self, package: str) -> tuple[type[Module], ...]:
        """Discover Module subclasses inside *package* (no import of pyUltroid).

        Mirrors ``re-caligo``'s ``Extender.loads`` auto-discovery, but it
        only inspects ``on_*``-tagged subclasses and returns them; loading is
        the caller's explicit choice.
        """
        found: list[type[Module]] = []
        try:
            root = import_module(package)
        except Exception as exc:
            self._log("warning", "module scan skipped %s: %s", package, exc)
            return ()
        paths = getattr(root, "__path__", None)
        if not paths:
            return ()
        for info in pkgutil.iter_modules(list(paths)):
            if info.name.startswith("_"):
                continue
            try:
                module = import_module(f"{package}.{info.name}")
            except Exception as exc:
                self._log("warning", "module import failed %s: %s", info.name, exc)
                continue
            for _name, obj in vars(module).items():
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, Module)
                    and obj is not Module
                    and obj.discoverable
                ):
                    found.append(obj)
        return tuple(found)

    # -- the driver --------------------------------------------------------

    def adopt(
        self, mod: Any, clients: Iterable[Any] | None = None
    ) -> dict[str, dict[str, Any]]:
        """Load + bind every Module class *defined in* ``mod``; returns a report.

        This is the runtime driver the Module layer was missing (the
        ``Extender.loads`` half of the re-caligo port): the Ultroid loader
        calls it right after importing a plugin file, so a Module is live the
        moment its file loads - no self-wiring, no silent unbound state.

        * Only classes whose ``__module__`` is *mod* itself are adopted -
          ``BoundModule`` & friends imported from ``pyUltroid._misc`` are
          skipped, as are non-``discoverable`` classes.
        * Re-adopting a reloaded plugin file unbinds the stale instance of
          the same class name first, so a reload never double-binds.
        * Per-module failures are isolated and reported, never raised: the
          report is ``{ClassName: {"handlers": n} | {"error": "..."}}``.
        * Starting is *not* done here (this must stay sync for the legacy
          loader path): modules lazy-start through ``ensure_started`` /
          :meth:`Module.ready` on first use, or eagerly via the loader.
        """
        if clients is not None:
            self.client = clients
        report: dict[str, dict[str, Any]] = {}
        for obj in vars(mod).values():
            if not (
                inspect.isclass(obj)
                and issubclass(obj, Module)
                and obj is not Module
                and obj.__module__ == mod.__name__
                and obj.discoverable
            ):
                continue
            name = obj.__name__
            try:
                if name in self.modules:
                    self.unload(name)
                module = self.load(obj)
                report[name] = {"handlers": self.bind(module)}
            except Exception as exc:
                report[name] = {"error": f"{type(exc).__name__}: {exc}"}
                self._log(
                    "error", "module adopt failed %s: %s", name, exc
                )
        return report

    def drop_from(self, module_name: str) -> tuple[str, ...]:
        """Unload every module whose class was defined in *module_name*.

        The loader's unload path calls this so ``.plugins unload`` drops the
        Module instances a plugin file contributed, not just the Telethon
        handlers its manifest recorded.
        """
        gone: list[str] = []
        for key, module in list(self.modules.items()):
            defined_in = type(module).__module__
            if defined_in == module_name or defined_in.startswith(
                module_name + "."
            ):
                try:
                    self.unload(module)
                    gone.append(key)
                except Exception as exc:
                    self._log("error", "module drop failed %s: %s", key, exc)
        return tuple(gone)

    def unload(self, name: str | Module) -> None:
        """Unbind and forget a module."""
        module = self.modules.get(name) if not isinstance(name, Module) else name
        if module is None:
            raise ModuleMissing(str(name))
        self.unbind(module)
        key = type(module).__name__
        self.modules.pop(key, None)
        # A module that is gone must not count as started: the next load of
        # the same name (a ``.plugins reload``) must run its lifecycle again,
        # not be silently skipped by ``ensure_started``'s latch.
        self._started_modules.discard(key)

    def unloads(self) -> None:
        """Unbind and forget every module, newest first."""
        for name in tuple(self.modules)[::-1]:
            try:
                self.unload(name)
            except ModuleMissing:
                continue

    # -- binding -----------------------------------------------------------

    def bind(self, module: Module, clients: Iterable[Any] | None = None) -> int:
        """Attach every handler of *module* to *clients*; returns the count.

        A failure part-way rolls back the whole module so it is never left
        half-registered on a client.
        """
        targets = list(clients) if clients is not None else self._clients()
        if not targets:
            return 0
        added: list[tuple[Any, Callable]] = []
        inserted: list[Listener] = []
        try:
            for name, tag in module.handlers:
                bound = module.binding(name)
                for client in targets:
                    client.add_event_handler(bound, tag.builder)
                    added.append((client, bound))
                listener = Listener(module, tag.event, bound, tag.builder, tag.priority)
                self._insert(listener)
                inserted.append(listener)
        except Exception:
            # Roll back BOTH halves: the client handlers already attached
            # and the listeners this call inserted. A module that is
            # registered on a client but absent from the index (or worse,
            # present in the index but not on the client) is worse than one
            # that was never bound.
            for client, bound in added:
                try:
                    client.remove_event_handler(bound)
                except Exception:
                    pass
            survivors = {id(item) for item in inserted}
            self.listeners = [
                item for item in self.listeners if id(item) not in survivors
            ]
            raise
        return len(module.handlers) * len(targets)

    def unbind(self, module: Module, clients: Iterable[Any] | None = None) -> int:
        """Detach every handler of *module*. Returns how many were removed."""
        targets = list(clients) if clients is not None else self._clients()
        removed = 0
        for name, tag in module.handlers:
            bound = module.binding(name)
            for client in targets:
                try:
                    client.remove_event_handler(bound)
                    removed += 1
                except Exception:
                    pass
        self.listeners = [li for li in self.listeners if li.module is not module]
        return removed

    def bound_modules(self) -> dict[str, int]:
        """How many handlers each module currently has bound."""
        counts: dict[str, int] = {}
        for listener in self.listeners:
            key = listener.owner
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _insert(self, listener: Listener) -> None:
        bisect.insort(self.listeners, listener)

    def _clients(self) -> list[Any]:
        client = self.client
        if client is None:
            return []
        getter = getattr(client, "clients", None)
        if callable(getter):
            try:
                return [c for c in getter() if c is not None]
            except Exception:
                pass
        if isinstance(client, (list, tuple)):
            return [c for c in client if c is not None]
        return [client]

    # -- lifecycle ---------------------------------------------------------

    async def dispatch(self, hook: str, *args: Any) -> dict[str, Any]:
        """Run lifecycle *hook* on every module; failures are isolated.

        Returns ``{module_name: exception_or_None}`` so a caller can report
        exactly which module failed without the registry raising.
        """
        results: dict[str, Any] = {}
        for name, module in tuple(self.modules.items()):
            func = getattr(module, hook, None)
            if not callable(func):
                results[name] = None
                continue
            try:
                outcome = func(*args)
                if inspect.isawaitable(outcome):
                    await outcome
            except Exception as exc:
                self._log("error", "module %s.%s failed: %s: %s", name, hook, type(exc).__name__, exc)
                results[name] = exc
                continue
            results[name] = None
        if hook == STARTED:
            self._started = True
            self._started_modules.update(self.modules)
        elif hook in (STOPPED, STOPPING):
            self._started = False
        return results

    async def start(self) -> dict[str, Any]:
        """Dispatch ``on_started`` across every module."""
        return await self.dispatch(STARTED)

    async def ensure_started(self, module: "Module | str | None" = None) -> dict[str, Any]:
        """Dispatch ``on_starting`` then ``on_started``, exactly once.

        The lazy counterpart of :meth:`start`, for callers that have no
        process-level lifecycle driver - a plugin file loaded by the Ultroid
        loader is the motivating case: it must not assume something will
        call :meth:`start` for it. Idempotent per module, and a module that
        fails its ``on_starting`` is *not* marked started, so the next call
        retries instead of latching a broken module.
        """
        if module is None:
            targets = list(self.modules.values())
        elif isinstance(module, str):
            found = self.modules.get(module)
            targets = [found] if found is not None else []
        else:
            targets = [module]

        results: dict[str, Any] = {}
        for entry in targets:
            name = type(entry).__name__
            if name in self._started_modules:
                results[name] = None
                continue
            starting = await self.dispatch_one(entry, STARTING)
            if starting is not None:
                results[name] = starting
                continue
            started = await self.dispatch_one(entry, STARTED)
            if started is not None:
                results[name] = started
                continue
            self._started_modules.add(name)
            entry._mark_started()
            results[name] = None
        if targets:
            self._started = True
        return results

    async def dispatch_one(self, module: Module, hook: str, *args: Any) -> Any:
        """Run one lifecycle hook on one module; returns the exception or None."""
        func = getattr(module, hook, None)
        if not callable(func):
            return None
        try:
            outcome = func(*args)
            if inspect.isawaitable(outcome):
                await outcome
        except Exception as exc:
            self._log(
                "error",
                "module %s.%s failed: %s: %s",
                type(module).__name__,
                hook,
                type(exc).__name__,
                exc,
            )
            return exc
        return None

    async def stop(self) -> dict[str, Any]:
        """Unbind everything and dispatch ``on_stopping``/``on_stopped``."""
        for module in tuple(self.modules.values()):
            module._mark_stopped()
        await self.dispatch(STOPPING)
        await self.dispatch(STOPPED)
        self.unloads()
        self._started_modules.clear()
        return {}

    # -- introspection -----------------------------------------------------

    def status(self) -> dict[str, Any]:
        """A snapshot for a dashboard."""
        return {
            "started": self._started,
            "modules": {
                name: {
                    "name": module.name,
                    "started": module.started,
                    "stopped": module.stopped,
                    "handlers": len(module.handlers),
                }
                for name, module in self.modules.items()
            },
            "listeners": len(self.listeners),
        }

    def __repr__(self) -> str:
        state = "started" if self._started else "idle"
        return f"<ModuleRegistry modules={len(self.modules)} listeners={len(self.listeners)} {state}>"


#: the process-wide registry, for callers that do not want their own


def register_module(
    cls: type[Module],
    client: Any = None,
    reg: ModuleRegistry | None = None,
    *,
    bind_clients: Iterable[Any] | None = None,
    **options: Any,
) -> Module:
    """One-call load + bind for a single module class.

    ``reg`` defaults to the process-wide registry held by :class:`Misc`.
    """
    from .services import misc

    target = reg if reg is not None else misc.registry
    target.client = client if client is not None else target.client
    module = target.load(cls, **options)
    target.bind(module, bind_clients)
    return module

#: The process-wide Module registry. ``Misc.registry`` (the union in
#: ``_misc.py``) is this same object, and the W3-B Module driver loads every
#: discovered plugin Module into it - one registry, one truth.
registry = ModuleRegistry()

"""Load the built-in Telethon addons before any Ultroid client starts.

The whole package is one object. :class:`Modules` unions the loader, the
update router, the executor and the business addons, so a single instance
discovers the built-ins, installs the TL patches and reaches every capability.
"""

from __future__ import annotations

import importlib
import inspect
import pathlib
import pkgutil
from collections.abc import Iterable
from types import ModuleType
from typing import Any, Callable

from telethon import events

from ._gorilla import BusinessAddons
from .execute import Executor
from .updates import HANDLED, UpdateRouter

__all__ = ["ModuleLoader", "Modules"]

_ROOT = pathlib.Path(__file__).parent.resolve()


class ModuleLoader:
    """Finds the built-in modules, applies their patches, attaches handlers.

    Patch order matters and is the reason this is a class rather than a couple
    of functions: cooperative ``@patch`` definitions install first, then the
    business/guest addons force-install last, so they win over any cooperative
    definition on the same target regardless of import order.
    """

    def __init__(self) -> None:
        super().__init__()
        self.discovered: list[ModuleType] = []
        self.bootstrapped: bool = False
        self.patch_count: int = 0

    # ── discovery ────────────────────────────────────────────────────────

    def discover(self) -> list[ModuleType]:
        """Import every built-in module in deterministic name order."""
        found = sorted(
            pkgutil.iter_modules([str(_ROOT)]), key=lambda item: item.name
        )
        return [importlib.import_module(f".{item.name}", __name__) for item in found]

    def apply_patches(self, imported: Iterable[ModuleType]) -> int:
        """Install every ``@patch``-decorated function onto its targets.

        Walks each module's ``__dict__`` directly: ``inspect.getmembers``
        would ``getattr`` and sort every attribute, including the ones that
        are not functions at all.
        """
        installed: set[tuple[type, object]] = set()

        for module in imported:
            for func in vars(module).values():
                if not inspect.isfunction(func):
                    continue
                for target in getattr(func, "_patch", ()):
                    key = (target, func)
                    if key in installed:
                        continue
                    setattr(target, func.__name__.lstrip("_"), func)
                    installed.add(key)

        return len(installed)

    # ── handlers ─────────────────────────────────────────────────────────

    def handlers(self) -> list[Callable]:
        """The Telethon handlers this object exposes.

        Walks the instance rather than the modules: a method decorated with
        ``events.register`` is a handler whether or not it sits at module
        level, and one scan of the union replaces one scan per module.
        """
        found: list[Callable] = []
        for name in dir(self):
            try:
                attr = getattr(self, name)
            except Exception:
                continue
            if events.is_handler(attr) and not any(attr is f for f in found):
                found.append(attr)
        return found

    def attach(self, clients: Iterable[object]) -> int:
        """Attach every handler to each client exactly once."""
        callbacks = self.handlers()
        attached = 0

        for client in clients:
            existing = {callback for callback, _ in client.list_event_handlers()}
            for callback in callbacks:
                if callback in existing:
                    continue
                client.add_event_handler(callback)
                attached += 1
        return attached

    # ── bootstrap ────────────────────────────────────────────────────────

    def bootstrap(self) -> int:
        """Import the built-in addons and patch Telethon once.

        Run this before any Ultroid client is constructed so every client
        class sees the complete patched API; :meth:`attach` wires the raw
        handlers to bot clients afterwards.
        """
        if self.bootstrapped:
            return self.patch_count

        self.discovered[:] = self.discover()
        self.patch_count = self.apply_patches(self.discovered) + self.install(
            force=True
        )
        self.bootstrapped = True
        return self.patch_count

    def reset(self) -> None:
        """Forget bootstrap state, so a later bootstrap runs again."""
        self.discovered.clear()
        self.bootstrapped = False
        self.patch_count = 0


class Modules(ModuleLoader, UpdateRouter, Executor, BusinessAddons):
    """The whole modules package as one object.

    :class:`ModuleLoader` discovers and patches, :class:`UpdateRouter` routes
    raw updates, :class:`Executor` runs the Python those routes hand over, and
    :class:`BusinessAddons` owns the TL patches and identity resolution. Each
    parent's ``__init__`` calls ``super()``, so one cooperative chain wires
    all four.
    """

    #: The raw updates this package consumes.
    handled = HANDLED

    def __init__(self) -> None:
        super().__init__()

    def summary(self) -> dict[str, Any]:
        """A small state snapshot, handy for a startup log."""
        return {
            "bootstrapped": self.bootstrapped,
            "patch_count": self.patch_count,
            "modules": [m.__name__.rsplit(".", 1)[-1] for m in self.discovered],
            "handlers": len(self.handlers()),
            "installed": self.installed,
        }
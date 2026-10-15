# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""``Module``: a feature whose handlers are methods and whose lifecycle is
declared, not sprinkled.

Concept ported from ``re-caligo``'s ``selfbot/module.py`` +
``core/extender.py`` + ``core/dispatcher.py``. What that design actually
buys, and what this class keeps:

* **Metadata as class attributes** - ``name``, ``cmds``, ``desc``. One
  declaration feeds the help output, the registry key and the log line.
* **Handlers are methods, discovered by the ``on_`` prefix.** The registry
  never needs a manifest: it walks the instance.
* **Lifecycle hooks are ``on_*`` methods too** (``on_starting``,
  ``on_started``, ``on_stopping``), so a module's setup and teardown live
  next to the code that uses them instead of in a loader.
* **A per-class logger**, not the root logger.

Deliberate differences from the original, and why:

* Discovery happens in :meth:`__init_subclass__` and produces *descriptors*
  (:attr:`Module.handlers`), so the event/priority table is known from the
  class object - before any instance exists. ``re-caligo`` re-derives it
  from ``dir(instance)`` on every load.
* Constructor is explicit and typed, and takes the client **and** the
  registry, because a module that can register more handlers at runtime
  needs the registry, and one that should not gets ``registry=None``.
* ``handlers`` refuses to be shadowed, and the ``on_`` scan is
  declaration-ordered (``__dict__`` of the MRO reversed) rather than
  alphabetical, so ``dir()`` ordering cannot change dispatch order.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any, ClassVar, Final, Iterator

from .listener import (
    DEFAULT_PRIORITY,
    EVENT_ATTR,
    PRIORITY_ATTR,
    HandlerTag,
    is_handler,
    tag_of,
)
from .methods import ModuleMixin

if TYPE_CHECKING:  # pragma: no cover
    from .registry import ModuleRegistry

#: method-name prefix that marks a handler
HANDLER_PREFIX: Final[str] = "on_"

#: lifecycle hook names, dispatched by the registry
STARTING: Final[str] = "on_starting"
STARTED: Final[str] = "on_started"
STOPPING: Final[str] = "on_stopping"
STOPPED: Final[str] = "on_stopped"
LIFECYCLE_HOOKS: Final[tuple[str, ...]] = (STARTING, STARTED, STOPPING, STOPPED)


class ModuleError(Exception):
    """Base for every module-layer failure."""


class ModuleExists(ModuleError):
    """A module with this class name is already registered."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Module {name!r} is already registered")
        self.name = name


class ModuleMissing(ModuleError):
    """No module is registered under this name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Module {name!r} is not registered")
        self.name = name


class Module:
    """Base class for a Module.

    Subclasses declare ``name`` / ``cmds`` / ``desc``, then methods that
    start with ``on_`` and are tagged with :func:`~pyUltroid._misc.listener.handler`.
    The registry instantiates, binds, and drives the lifecycle.
    """

    #: human name, shown in logs and help; defaults to the class name
    name: ClassVar[str] = "Module"
    #: command signature, shown in help
    cmds: ClassVar[str] = ""
    #: per-argument help, shown in help
    desc: ClassVar[dict[str, str]] = {}
    #: set False to keep the class out of auto-discovery
    discoverable: ClassVar[bool] = True
    #: ``(event, HandlerTag)`` pairs, in declaration order
    handlers: ClassVar[tuple[tuple[str, HandlerTag], ...]] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        cls.handlers = cls._scan_handlers()

    # -- discovery

    @classmethod
    def _scan_handlers(cls) -> tuple[tuple[str, HandlerTag], ...]:
        """Collect tagged ``on_*`` methods in declaration order.

        Walks the MRO base-first so an override replaces its parent rather
        than appearing twice, and ignores anything the ``on_`` prefix rule
        catches without a tag (a plain ``on_`` helper is not a handler).
        """
        found: dict[str, HandlerTag] = {}
        order: list[str] = []
        for klass in reversed(cls.__mro__):
            for attribute, value in vars(klass).items():
                if not attribute.startswith(HANDLER_PREFIX):
                    continue
                if isinstance(value, staticmethod) or isinstance(value, classmethod):
                    value = value.__func__
                if not is_handler(value):
                    continue
                if attribute not in found:
                    order.append(attribute)
                found[attribute] = tag_of(value)
        return tuple((name, found[name]) for name in order)

    @classmethod
    def handler_names(cls) -> tuple[str, ...]:
        return tuple(name for name, _tag in cls.handlers)

    @classmethod
    def handler_events(cls) -> tuple[str, ...]:
        """Distinct event names, in first-declaration order."""
        seen: dict[str, None] = {}
        for _name, tag in cls.handlers:
            seen.setdefault(tag.event, None)
        return tuple(seen)

    # -- construction

    def __init__(
        self,
        client: Any = None,
        registry: "ModuleRegistry | None" = None,
        **options: Any,
    ) -> None:
        self.client = client
        self.registry = registry
        self.options: dict[str, Any] = dict(options)
        self.logger = logging.getLogger(f"pyUltroid.module.{self.name}")
        self._started = False
        self._stopped = False
        # Identity-stable bound callbacks. ``getattr(self, name)`` builds a
        # *new* bound-method object on every access, so binding with one
        # call and removing with another silently fails to match. Every
        # bind/unbind path must go through :meth:`binding`.
        self._bindings: dict[str, Any] = {}

    # -- bound callbacks

    def binding(self, name: str) -> Any:
        """The cached bound method for handler *name*.

        Returns the *same* object every call, so a client can remove
        exactly what was added.
        """
        bound = self._bindings.get(name)
        if bound is None:
            bound = getattr(self, name)
            self._bindings[name] = bound
        return bound

    # -- lifecycle state

    @property
    def started(self) -> bool:
        return self._started

    @property
    def stopped(self) -> bool:
        return self._stopped

    def _mark_started(self) -> None:
        self._started = True
        self._stopped = False

    def _mark_stopped(self) -> None:
        self._stopped = True
        self._started = False

    # -- metadata

    @classmethod
    def describe(cls) -> dict[str, Any]:
        """The help entry for this module."""
        return {
            "name": cls.name,
            "cmds": cls.cmds,
            "desc": dict(cls.desc),
            "events": list(cls.handler_events()),
            "handlers": list(cls.handler_names()),
        }

    def option(self, key: str, default: Any = None) -> Any:
        """A construction option, with a lookup on the client as fallback."""
        if key in self.options:
            return self.options[key]
        return getattr(self.client, key, default)

    # -- introspection

    def __iter__(self) -> Iterator[tuple[str, HandlerTag]]:
        return iter(self.handlers)

    def __len__(self) -> int:
        return len(self.handlers)

    def __repr__(self) -> str:
        state = "started" if self._started else "idle"
        return f"<{type(self).__name__} {self.name!r} handlers={len(self.handlers)} {state}>"


class BoundModule(ModuleMixin, Module):
    """A :class:`Module` that also carries the bound helper verbs.

    Split deliberately: ``Module`` stays free of any dependency on
    ``custom``/``fns`` so it can be imported and inspected in isolation,
    while a module that actually renders messages opts in here.
    """

    async def shell_out(self, cmd: str) -> str:
        """Run a shell command through the bound debug verb."""
        return await self.shell(cmd)


# Re-exported so a module file can import everything it needs from one place.
__all__ = (
    "BoundModule",
    "DEFAULT_PRIORITY",
    "EVENT_ATTR",
    "HANDLER_PREFIX",
    "LIFECYCLE_HOOKS",
    "Module",
    "ModuleError",
    "ModuleExists",
    "ModuleMissing",
    "ModuleMixin",
    "PRIORITY_ATTR",
    "STARTED",
    "STARTING",
    "STOPPED",
    "STOPPING",
)
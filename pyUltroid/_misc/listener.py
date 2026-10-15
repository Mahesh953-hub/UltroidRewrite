# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""Listener values and the ``@handler`` tagger: deferred handler binding.

Concept ported from ``re-caligo``'s ``selfbot/listener.py``. Two objects,
two deliberately different jobs:

* :class:`Listener` is an **immutable value** describing one bound handler -
  which module owns it, what event it answers, how urgent it is, and the
  monotonic sequence number that makes ordering total. It does no calling.
* :class:`HandlerTag` is the **decorator object**. ``@handler(builder, 1)``
  does not bind anything: it tags the function with its event builder and
  priority, and binding happens later, once the module instance exists. That
  deferral is what lets a module be declared at class scope and constructed
  by the core.registry.

Ordering note: ``re-caligo`` relies on ``Listener.__lt__`` plus
``bisect.insort`` on priority alone, which makes equal-priority listeners
order by insertion accident. Here the sequence number is part of the sort
key, so two handlers with the same priority always dispatch in declaration
order, deterministically and on every Python version.
"""

from __future__ import annotations

import itertools
from typing import Any, Callable, Final

#: attribute the tagger writes the event builder onto
EVENT_ATTR: Final[str] = "module_event"
#: attribute the tagger writes the priority onto
PRIORITY_ATTR: Final[str] = "module_priority"
#: attribute the tagger writes the owning class name onto
OWNER_ATTR: Final[str] = "module_owner"

#: priority used when a module does not pass one
DEFAULT_PRIORITY: Final[int] = 99

_SEQUENCE: "itertools.count[int]" = itertools.count()


class Listener:
    """One bound handler, ordered by ``(priority, sequence)``.

    Immutable: the registry never mutates a listener, it only holds or drops
    it. That is what makes :meth:`identity` a valid de-duplication key.
    """

    __slots__ = ("module", "event", "func", "builder", "priority", "sequence")

    def __init__(
        self,
        module: Any,
        event: str,
        func: Callable[..., Any],
        builder: Any,
        priority: int = DEFAULT_PRIORITY,
    ) -> None:
        self.module = module
        self.event = event
        self.func = func
        self.builder = builder
        self.priority = priority
        self.sequence = next(_SEQUENCE)

    @property
    def sort_key(self) -> tuple[int, int]:
        return (self.priority, self.sequence)

    @property
    def identity(self) -> tuple[int, str]:
        """``(id(module), bound method)`` - stable across re-registration."""
        return (id(self.module), self.func)

    @property
    def owner(self) -> str:
        return type(self.module).__name__

    def __lt__(self, other: "Listener") -> bool:
        if not isinstance(other, Listener):
            return NotImplemented
        return self.sort_key < other.sort_key

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Listener):
            return NotImplemented
        return self.sort_key == other.sort_key

    def __hash__(self) -> int:
        return hash(self.sort_key)

    def __repr__(self) -> str:
        return (
            f"<Listener {self.owner}.{self.func.__name__} "
            f"event={self.event} priority={self.priority}>"
        )


class HandlerTag:
    """Callable decorator that tags a function instead of binding it.

    Used as ``@handler(events.NewMessage(pattern=...), 1)``. The returned
    object is the *decorator*; calling it with the function returns the
    unchanged function, so decorating never alters the module's own API.
    """

    __slots__ = ("builder", "priority", "event")

    def __init__(self, builder: Any, priority: int = DEFAULT_PRIORITY) -> None:
        self.builder = builder
        self.priority = priority
        self.event = _derive_event_name(builder)

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        setattr(func, EVENT_ATTR, self.builder)
        setattr(func, PRIORITY_ATTR, self.priority)
        setattr(func, OWNER_ATTR, f"{func.__module__}.{func.__qualname__}")
        return func

    def describe(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "priority": self.priority,
            "builder": type(self.builder).__name__,
        }

    def __repr__(self) -> str:
        return f"<HandlerTag {self.event} priority={self.priority}>"


def handler(builder: Any, priority: int = DEFAULT_PRIORITY) -> HandlerTag:
    """Tag a module method as a handler for *builder* at *priority*."""
    return HandlerTag(builder, priority)


def is_handler(func: Any) -> bool:
    """True when *func* was tagged by :func:`handler`."""
    return callable(func) and hasattr(func, EVENT_ATTR)


def tag_of(func: Any) -> HandlerTag:
    """Rebuild the :class:`HandlerTag` for an already-tagged function."""
    if not is_handler(func):
        raise TypeError(f"{getattr(func, '__name__', func)!r} is not a handler")
    return HandlerTag(getattr(func, EVENT_ATTR), getattr(func, PRIORITY_ATTR, DEFAULT_PRIORITY))


def _derive_event_name(builder: Any) -> str:
    """Best-effort human name for a Telethon event builder.

    Telethon stores its builder parameters in a ``build`` dict rather than
    as plain attributes, so both are probed. Never raises, and never
    guesses in a harmful way: the name is only used for grouping and
    diagnostics - **dispatch keys on the builder object itself**, so a
    wrong name can never misroute an event.
    """
    for attribute in ("__name__", "pattern", "_pattern"):
        value = getattr(builder, attribute, None)
        if isinstance(value, str) and value:
            return value
    build = getattr(builder, "build", None)
    if isinstance(build, dict):
        for key in ("pattern", "__func__"):
            value = build.get(key)
            if isinstance(value, str) and value:
                return value
            inner = getattr(value, "pattern", None)
            if isinstance(inner, str) and inner:
                return inner
    return type(builder).__name__

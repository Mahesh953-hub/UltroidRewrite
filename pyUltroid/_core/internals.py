# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""First-class, typed access to Telethon's dispatch internals.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from collections.abc import Callable, Iterator


from ..startup.logger import LOGS

__all__ = (
    "InternalsError",
    "HandlerSlot",
    "DispatchState",
    "event_builders",
    "handler_slots",
    "find_handlers",
    "insert_handler",
    "remove_handler",
    "count_handlers",
    "dispatch_update",
    "feed_update",
    "mb_entity_cache",
    "self_id",
    "conversations",
    "pending_tasks",
    "dispatch_depth",
    "dispatch_pass",
    "defer",
    "tag_owner",
    "owner_of",
    "handlers_by_owner",
    "describe",
)


class InternalsError(RuntimeError):
    """A Telethon internal attribute Ultroid relies on is absent."""


# ── slots ─────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class HandlerSlot:
    """One entry of ``client._event_builders``, with its position.

    ``index`` is the position the slot had when it was read. It is a *hint*:
    the list is live, so always address the slot by ``builder``/``callback``
    identity and re-resolve the index before an insert.
    """

    builder: Any
    callback: Callable
    index: int = -1

    @property
    def name(self) -> str:
        return getattr(self.callback, "__name__", repr(self.callback))

    @property
    def builder_type(self) -> type:
        return type(self.builder)

    def __iter__(self) -> Iterator[Any]:  # allow tuple unpacking / comparison
        yield self.builder
        yield self.callback


# ── live accessors ────────────────────────────────────────────────────────
def event_builders(client: Any) -> list[tuple[Any, Callable]]:
    """Return the **live** ``_event_builders`` list (never a copy)."""
    builders = getattr(client, "_event_builders", None)
    if builders is None:
        raise InternalsError(
            f"{type(client).__name__} has no _event_builders; not a TelegramClient?"
        )
    return builders


def handler_slots(client: Any) -> list[HandlerSlot]:
    """Materialise the current handler chain as :class:`HandlerSlot` objects."""
    return [
        HandlerSlot(builder, callback, index)
        for index, (builder, callback) in enumerate(event_builders(client))
    ]


def find_handlers(
    client: Any,
    *,
    callback: Callable | None = None,
    builder_type: type | None = None,
    builder: Any | None = None,
) -> list[HandlerSlot]:
    """Return every slot matching all of the given constraints.

    Matching rules:

    * ``callback`` matches on **identity** first, then on equality. Telethon's
      own ``remove_event_handler`` compares with ``==``; identity is what
      plugins actually mean, so it is checked first.
    * ``builder_type`` matches ``isinstance(builder, builder_type)``, so
      ``events.Raw`` finds raw handlers and ``events.NewMessage`` finds both
      the class and chained variants.
    * ``builder`` matches the builder instance by identity.
    """
    found: list[HandlerSlot] = []
    for slot in handler_slots(client):
        if callback is not None and not (
            slot.callback is callback or slot.callback == callback
        ):
            continue
        if builder_type is not None and not isinstance(slot.builder, builder_type):
            continue
        if builder is not None and slot.builder is not builder:
            continue
        found.append(slot)
    return found


def count_handlers(client: Any, builder_type: type | None = None) -> int:
    if builder_type is None:
        return len(event_builders(client))
    return sum(
        1 for slot in handler_slots(client) if isinstance(slot.builder, builder_type)
    )


# ── mutation ──────────────────────────────────────────────────────────────
def insert_handler(
    client: Any,
    builder: Any,
    callback: Callable,
    *,
    index: int | None = None,
) -> HandlerSlot:
    """Insert ``(builder, callback)`` at ``index`` (default: the front).

    Telethon's ``add_event_handler`` only appends. Front insertion is the only
    way to run *before* every plugin handler without owning the dispatch loop.
    """
    builders = event_builders(client)
    if isinstance(builder, type):
        builder = builder()
    builders.insert(0 if index is None else int(index), (builder, callback))
    return HandlerSlot(builder, callback, 0 if index is None else int(index))


def remove_handler(
    client: Any,
    callback: Callable,
    *,
    builder_type: type | None = None,
) -> int:
    """Remove every slot for ``callback``; returns the number removed.

    Safe to call from inside a handler: see :func:`dispatch_depth`.
    """
    slots = find_handlers(client, callback=callback, builder_type=builder_type)
    if not slots:
        return 0
    if dispatch_depth(client) > 0:
        defer(client, lambda: _force_remove(client, callback, builder_type))
        return len(slots)
    return _force_remove(client, callback, builder_type)


def _force_remove(client: Any, callback: Callable, builder_type: type | None) -> int:
    builders = event_builders(client)
    doomed = sorted(
        (
            slot.index
            for slot in find_handlers(
                client, callback=callback, builder_type=builder_type
            )
        ),
        reverse=True,
    )
    for index in doomed:
        del builders[index]
    return len(doomed)


# ── dispatch ──────────────────────────────────────────────────────────────
_DISPATCH_KEY = "_ultroid_dispatch_state"


@dataclass(slots=True)
class DispatchState:
    """Per-client bookkeeping for the current dispatch pass."""

    depth: int = 0
    deferred: list[Callable[[], Any]] = field(default_factory=list)
    passes: int = 0
    last_update_type: str | None = None

    def enter(self, update: Any) -> None:
        self.depth += 1
        self.passes += 1
        self.last_update_type = type(update).__name__

    def leave(self) -> list[Callable[[], Any]]:
        self.depth -= 1
        if self.depth > 0:
            return []
        runnable, self.deferred = self.deferred, []
        return runnable


def _state(client: Any) -> DispatchState:
    state = getattr(client, _DISPATCH_KEY, None)
    if state is None:
        state = DispatchState()
        try:
            setattr(client, _DISPATCH_KEY, state)
        except AttributeError:  # pragma: no cover - read-only attribute
            object.__setattr__(client, _DISPATCH_KEY, state)
    return state


def dispatch_depth(client: Any) -> int:
    """How many dispatch passes are active on ``client`` right now."""
    state = getattr(client, _DISPATCH_KEY, None)
    return state.depth if state is not None else 0


@contextmanager
def dispatch_pass(client: Any, update: Any) -> Iterator[DispatchState]:
    """Mark the start/end of one dispatch pass and flush deferred removals.

    The pipeline patch wraps every update in this so that a handler which
    removes itself (one-shot routes, ``once=True`` commands) does not corrupt
    the in-place loop Telethon runs over ``_event_builders``.
    """
    state = _state(client)
    state.enter(update)
    try:
        yield state
    finally:
        for action in state.leave():
            try:
                action()
            except Exception as e:  # never let cleanup break dispatch
                LOGS.debug(f"[internals] deferred action failed: {e}")


def defer(client: Any, action: Callable[[], Any]) -> None:
    """Run ``action`` after the current dispatch pass unwinds."""
    _state(client).deferred.append(action)


def dispatch_update(client: Any) -> Callable:
    """Return the client's bound ``_dispatch_update`` entry point.

    After :meth:`UpdatePipeline.install` this is the pipeline's wrapper, not
    Telethon's original method. :meth:`UpdatePipeline.install` keeps the
    original for its own chain.
    """
    method = getattr(client, "_dispatch_update", None)
    if method is None:
        raise InternalsError(f"{type(client).__name__} has no _dispatch_update")
    return method


async def feed_update(
    client: Any, update: Any, *, entities: dict | None = None
) -> Any:
    """Feed a synthetic update through the client's normal dispatch path.

    Telethon resolves entities through ``update._entities`` (a dict keyed by
    ``int``). Synthetic updates have no such attribute, so set it yourself when
    the event builders need real entities.
    """
    if entities is not None:
        update._entities = entities
    elif not hasattr(update, "_entities"):
        update._entities = {}
    return await dispatch_update(client)(update)


# ── caches / loop state ───────────────────────────────────────────────────
def mb_entity_cache(client: Any) -> Any:
    """Return the client's ``MessageBoxEntityCache``."""
    cache = getattr(client, "_mb_entity_cache", None)
    if cache is None:
        raise InternalsError(f"{type(client).__name__} has no _mb_entity_cache")
    return cache


def self_id(client: Any) -> int | None:
    """The logged-in user id, straight from the entity cache (no network)."""
    return getattr(mb_entity_cache(client), "self_id", None)


def conversations(client: Any) -> dict[Any, Any]:
    """The live ``_conversations`` mapping (chat id → set of conversations)."""
    store = getattr(client, "_conversations", None)
    if store is None:
        raise InternalsError(f"{type(client).__name__} has no _conversations")
    return store


def pending_tasks(client: Any) -> set:
    """Tasks Telethon spawned for chained updates (not yet finished)."""
    tasks = getattr(client, "_event_handler_tasks", None)
    if tasks is None:
        raise InternalsError(f"{type(client).__name__} has no _event_handler_tasks")
    return tasks


# ── ownership: who registered this handler? ───────────────────────────────
_OWNER_ATTR = "__ultroid_owner__"


def tag_owner(callback: Callable, owner: str | None) -> None:
    """Stamp an owner label onto ``callback`` and its wrapped target.

    Telethon's decorators wrap the user function, and the wrapper  -  not the
    original  -  is what lands in ``_event_builders``. Stamping both means the
    label survives either way.
    """
    if owner is None or callback is None:
        return
    try:
        setattr(callback, _OWNER_ATTR, owner)
    except (AttributeError, TypeError):  # builtins / slotted callables
        return
    inner = getattr(callback, "__wrapped__", None)
    if inner is not None:
        try:
            setattr(inner, _OWNER_ATTR, owner)
        except (AttributeError, TypeError):
            pass


def owner_of(callback: Callable) -> str | None:
    """Return the owner label stamped by :func:`tag_owner`, if any."""
    return getattr(callback, _OWNER_ATTR, None)


def handlers_by_owner(client: Any) -> dict[str, list[HandlerSlot]]:
    """Group the handler chain by the owner stamped on each callback.

    This replaces snapshot-diffing: an unowned handler lands under the empty
    string, so the grouping is total and never loses an entry.
    """
    grouped: dict[str, list[HandlerSlot]] = {}
    for slot in handler_slots(client):
        grouped.setdefault(owner_of(slot.callback) or "", []).append(slot)
    return grouped


def describe(client: Any) -> dict[str, Any]:
    """A one-shot diagnostic snapshot of the client's dispatch wiring."""
    slots = handler_slots(client)
    by_type: dict[str, int] = {}
    for slot in slots:
        by_type[type(slot.builder).__name__] = (
            by_type.get(type(slot.builder).__name__, 0) + 1
        )
    state = getattr(client, _DISPATCH_KEY, None)
    return {
        "handlers": len(slots),
        "by_builder": by_type,
        "conversations": sum(len(v) for v in conversations(client).values()),
        "pending_tasks": len(pending_tasks(client)),
        "dispatch_depth": state.depth if state else 0,
        "dispatch_passes": state.passes if state else 0,
        "last_update": state.last_update_type if state else None,
        "owners": sorted(k for k in handlers_by_owner(client) if k),
    }

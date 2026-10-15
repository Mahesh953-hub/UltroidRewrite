# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""The Ultroid update orchestrator.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import asyncio
import inspect
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
)
from collections.abc import Awaitable, Callable, MutableMapping
from weakref import WeakKeyDictionary, ref

from telethon import TelegramClient, events
from telethon.events.common import EventBuilder, EventCommon
from telethon.tl import types

from ..startup.logger import LOGS
from . import internals as _internals

__all__ = (
    "Route",
    "RouteStats",
    "UpdatePipeline",
    "PipelineError",
    "DuplicateRouteError",
    "pipeline_for",
    "existing_pipeline",
    "install_pipeline",
    "uninstall_pipeline",
    "instant",
    "wait_for_update",
    "STOP",
)

#: Return by a route handler to claim the update and end the pass.
STOP = events.StopPropagation


class PipelineError(RuntimeError):
    """The pipeline was used in a way it cannot honour."""


class DuplicateRouteError(PipelineError):
    """A route id was registered twice on the same pipeline."""


# ── stats ─────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class RouteStats:
    """Live counters for one route."""

    hits: int = 0
    matches: int = 0
    errors: int = 0
    total_seconds: float = 0.0
    last_fired: float | None = None

    @property
    def last_fired_ago(self) -> float | None:
        return None if self.last_fired is None else time.time() - self.last_fired

    def as_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "matches": self.matches,
            "errors": self.errors,
            "avg_ms": (
                round(self.total_seconds * 1000 / self.hits, 3) if self.hits else 0.0
            ),
            "last_fired_ago": self.last_fired_ago,
        }


# ── builder normalisation ─────────────────────────────────────────────────
def _update_types_of(builder: Any) -> tuple[type, ...]:
    """The TL update classes an ``EventBuilder`` reacts to.

    Telethon's builders declare no type table; they test ``isinstance`` inside
    ``build``. Rather than hard-coding a fork-specific table that silently rots
    when Telegram ships a new update, read the ``Update*`` names the builder
    references straight out of its ``build`` bytecode, then keep only the ones
    this Telethon actually defines. A builder whose ``build`` is opaque falls
    back to "every update", which is correct, just slower.
    """
    explicit = getattr(builder, "types", None)  # events.Raw(types=...)
    if explicit is not None:
        if isinstance(explicit, type):
            return (explicit,)
        return tuple(explicit)

    build = getattr(type(builder), "build", None)
    code = getattr(build, "__code__", None)
    if code is None:
        return ()
    found: list[type] = []
    for name in code.co_names:
        if not name.startswith("Update"):
            continue
        update_type = getattr(types, name, None)
        if isinstance(update_type, type):
            found.append(update_type)
    return tuple(dict.fromkeys(found))


def _coerce_builder(item: Any) -> Any:
    """Turn anything the caller passed into a Telethon ``EventBuilder``."""
    if isinstance(item, EventBuilder):
        return item
    if isinstance(item, type) and issubclass(item, EventBuilder):
        return item()
    if isinstance(item, type) and issubclass(item, types.TLObject):
        return events.Raw(types=item)
    raise PipelineError(
        f"cannot build a route from {item!r}; pass a TL update class, a "
        "telethon EventBuilder class, or an EventBuilder instance"
    )


def _coerce_updates(updates: Any) -> tuple[Any, ...]:
    if updates is None:
        raise PipelineError("a route needs at least one update type or builder")
    if isinstance(updates, (EventBuilder, type)) or not isinstance(
        updates, (list, tuple, set, frozenset)
    ):
        updates = (updates,)
    return tuple(_coerce_builder(item) for item in updates)


# ── route ─────────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Route:
    """One ordered entry of the pipeline."""

    builders: tuple[Any, ...]
    updates: tuple[type, ...]
    handler: Callable[[Any], Any]
    match: Callable[[Any], Any] | None = None
    priority: int = 0
    owner: str | None = None
    exclusive: bool = False
    once: bool = False
    enabled: bool = True
    route_id: str | None = None
    stats: RouteStats = field(default_factory=RouteStats)

    def __post_init__(self) -> None:
        if self.route_id is None:
            name = getattr(self.handler, "__name__", None) or "route"
            self.route_id = f"{name}#{id(self):x}"

    @property
    def id(self) -> str:
        return self.route_id or ""

    async def subject(self, client: Any, update: Any) -> Any:
        """Return the event this route would handle, or ``None``.

        This *is* Telethon's matching path, in Telethon's own order: build the
        event, attach it to the client, resolve the builder once, then apply
        its filter. The client must be attached *before* the filter runs,
        because ``func=`` predicates and ``chats=``-style filters read
        ``event.client`` and the other client-backed event helpers. Anything
        ``client.on(builder)`` would accept, a route accepts  -  including
        ``chats=``, ``pattern=``, ``func=`` and the fork's newer variants.
        """
        if not self.enabled:
            return None
        for builder in self.builders:
            try:
                event = builder.build(update, None, getattr(client, "_self_id", None))
            except Exception as e:
                self.stats.errors += 1
                LOGS.debug(f"[pipeline] {self.id}: build() failed: {e}")
                continue
            if not event:
                continue
            if isinstance(event, EventCommon):
                event.original_update = update
                event._entities = getattr(update, "_entities", None) or {}
                try:
                    event._set_client(client)
                except Exception:  # pragma: no cover - defensive, build() sets it
                    pass
            try:
                if not getattr(builder, "resolved", True):
                    await builder.resolve(client)
                verdict = builder.filter(event)
                if inspect.isawaitable(verdict):
                    verdict = await verdict
            except Exception as e:
                self.stats.errors += 1
                LOGS.debug(f"[pipeline] {self.id}: filter() failed: {e}")
                continue
            if not verdict:
                continue
            return event
        return None

    async def accepts(self, subject: Any) -> bool:
        """Apply the route's own ``match`` predicate on top of the builder."""
        if self.match is None:
            return True
        try:
            verdict = self.match(subject)
            if inspect.isawaitable(verdict):
                verdict = await verdict
        except Exception as e:
            self.stats.errors += 1
            LOGS.debug(f"[pipeline] matcher for {self.id} raised: {e}")
            return False
        return bool(verdict)

    async def invoke(self, subject: Any) -> bool | None:
        """Run the handler. ``True`` means: claim the update."""
        started = time.perf_counter()
        self.stats.matches += 1
        try:
            result = self.handler(subject)
            if inspect.isawaitable(result):
                result = await result
        except events.StopPropagation:
            self.stats.hits += 1
            self.stats.last_fired = time.time()
            self.stats.total_seconds += time.perf_counter() - started
            return True
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.stats.errors += 1
            LOGS.exception(f"[pipeline] route {self.id} failed: {e}")
            return True if self.exclusive else None

        self.stats.hits += 1
        self.stats.last_fired = time.time()
        self.stats.total_seconds += time.perf_counter() - started
        if result is False:
            return None
        stop = events.StopPropagation
        if result is stop or isinstance(result, stop):
            # Returning the STOP sentinel claims the update, exactly as
            # raising it does. `STOP` is the exception class itself, so the
            # bare class counts as well as an instance of it.
            return True
        return True if self.exclusive else None

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "owner": self.owner,
            "priority": self.priority,
            "builders": [type(b).__name__ for b in self.builders],
            "updates": [t.__name__ for t in self.updates],
            "exclusive": self.exclusive,
            "once": self.once,
            "enabled": self.enabled,
            **self.stats.as_dict(),
        }


# ── the pipeline ──────────────────────────────────────────────────────────
class UpdatePipeline:
    """Per-client ordered update router sitting in front of Telethon."""

    __slots__ = (
        "_client_ref",
        "_routes",
        "_by_type",
        "_wildcard",
        "_installed",
        "_original",
        "_original_event",
    )

    def __init__(self, client: TelegramClient) -> None:
        # The client is held weakly. A strong reference here, combined with the
        # weak-keyed registry, would keep every client alive for the life of the
        # process: the registry key could never be collected while its own value
        # pointed back at it.
        try:
            self._client_ref: Any = ref(client)
        except TypeError:
            self._client_ref = _Strong(client)
        self._routes: list[Route] = []
        self._by_type: dict[type, list[Route]] = {}
        self._wildcard: list[Route] = []
        self._installed = False
        self._original: Callable | None = None
        self._original_event: Callable | None = None

    @property
    def client(self) -> Any:
        """The bound client, or ``None`` once it has been collected."""
        return self._client_ref()

    def _require_client(self) -> Any:
        client = self._client_ref()
        if client is None:
            raise PipelineError("the pipeline's client has been collected")
        return client

    # ── registration ──────────────────────────────────────────────────────
    def add(self, route: Route) -> Route:
        if not isinstance(route, Route):
            raise PipelineError("add() expects a Route")
        self.remove(route.id)
        self._routes.append(route)
        self._reindex()
        _internals.tag_owner(route.handler, route.owner)
        return route

    def route(
        self,
        updates: Any,
        handler: Callable[[Any], Any],
        *,
        match: Callable[[Any], Any] | None = None,
        priority: int = 0,
        owner: str | None = None,
        exclusive: bool = False,
        once: bool = False,
        route_id: str | None = None,
    ) -> Route:
        """Register ``handler`` for ``updates`` and return the :class:`Route`."""
        builders = _coerce_updates(updates)
        derived: list[type] = []
        for builder in builders:
            derived.extend(_update_types_of(builder))
        return self.add(
            Route(
                builders=builders,
                updates=tuple(dict.fromkeys(derived)),
                handler=handler,
                match=match,
                priority=priority,
                owner=owner if owner is not None else _OWNER_CONTEXT.get(),
                exclusive=exclusive,
                once=once,
                route_id=route_id,
            )
        )

    def on(
        self,
        updates: Any,
        *,
        match: Callable[[Any], Any] | None = None,
        priority: int = 0,
        owner: str | None = None,
        exclusive: bool = False,
        once: bool = False,
        route_id: str | None = None,
    ) -> Callable[[Callable], Callable]:
        """Decorator form of :meth:`route`; returns the original function."""

        def decorator(func):
            self.route(
                updates,
                func,
                match=match,
                priority=priority,
                owner=owner if owner is not None else getattr(func, "__module__", None),
                exclusive=exclusive,
                once=once,
                route_id=route_id,
            )
            return func

        return decorator

    def remove(self, route: Any) -> int:
        """Remove by :class:`Route`, route id, or handler; returns the count."""
        if isinstance(route, Route):
            remaining = [r for r in self._routes if r is not route]
        elif isinstance(route, str):
            remaining = [r for r in self._routes if r.id != route]
        elif callable(route):
            remaining = [r for r in self._routes if r.handler is not route]
        else:
            raise PipelineError(f"cannot remove {route!r}")
        removed = len(self._routes) - len(remaining)
        if removed:
            self._routes = remaining
            self._reindex()
        return removed

    def remove_owner(self, owner: str) -> int:
        """Drop every route owned by ``owner`` (plugin unload path)."""
        remaining = [r for r in self._routes if r.owner != owner]
        removed = len(self._routes) - len(remaining)
        if removed:
            self._routes = remaining
            self._reindex()
        return removed

    def enable(self, route_id: str, enabled: bool = True) -> bool:
        for route in self._routes:
            if route.id == route_id:
                route.enabled = enabled
                return True
        return False

    @contextmanager
    def owned_by(self, owner: str):
        """Bind every route registered inside the block to ``owner``."""
        marker = _OWNER_CONTEXT.set(owner)
        try:
            yield owner
        finally:
            _OWNER_CONTEXT.reset(marker)

    # ── indexing ──────────────────────────────────────────────────────────
    def _reindex(self) -> None:
        """Rebuild the type index and the priority order in one pass."""
        self._routes.sort(key=lambda r: r.priority, reverse=True)
        by_type: dict[type, list[Route]] = {}
        wildcard: list[Route] = []
        for route in self._routes:
            if not route.updates:
                wildcard.append(route)
                continue
            for update_type in route.updates:
                by_type.setdefault(update_type, []).append(route)
        self._by_type = by_type
        self._wildcard = wildcard

    def candidates(self, update: Any) -> list[Route]:
        """Routes that could accept ``update``, already in priority order.

        A route whose builder exposes no ``Update*`` reference (or an
        ``events.Raw`` with no ``types``) is a wildcard: it is always a
        candidate, because its builder is the only thing that can decide.
        """
        if not self._by_type and not self._wildcard:
            return []
        seen: set[int] = set()
        out: list[Route] = []
        for cls in type(update).__mro__:
            for route in self._by_type.get(cls, ()):
                if id(route) not in seen:
                    seen.add(id(route))
                    out.append(route)
        for route in self._wildcard:
            if id(route) not in seen:
                seen.add(id(route))
                out.append(route)
        out.sort(key=lambda r: r.priority, reverse=True)
        return out

    # ── dispatch ──────────────────────────────────────────────────────────
    async def dispatch(self, update: Any) -> bool:
        """Run the ordered pass. Returns ``True`` when the update is claimed."""
        candidates = self.candidates(update)
        if not candidates:
            return False
        # A registry entry can outlive its client for one collection cycle; say
        # so plainly rather than failing inside a route on a ``None`` client.
        client = self._require_client()
        for route in candidates:
            subject = await route.subject(client, update)
            if subject is None:
                continue
            if not await route.accepts(subject):
                continue
            if route.once:
                # Retire before invoking: two updates dispatched together must
                # not both run a one-shot handler.
                self.remove(route)
            claimed = await route.invoke(subject)
            if claimed:
                return True
        return False

    async def _patched_dispatch(self, update: Any) -> Any:
        # Read the fall-through once, up front. ``uninstall()`` clears it, and a
        # route is free to uninstall the pipeline  -  or another task may  -  while
        # this pass is awaiting. An update that reaches the end of the pass
        # unclaimed still belongs to Telethon, so the pass keeps the callable it
        # started with rather than whatever the attribute holds by then.
        original = self._original
        if not _internals.self_id(self.client):
            # Telethon's own pass starts by bootstrapping self_id, and builders
            # like NewMessage need it to resolve outgoing peers. Do not
            # front-run that bootstrap: hand the update to Telethon untouched.
            return await original(update)
        with _internals.dispatch_pass(self.client, update):
            if await self.dispatch(update):
                return None
            return await original(update)

    async def _patched_dispatch_event(self, event: Any) -> Any:
        """Telethon's album path, sharing the same dispatch pass.

        This path iterates the handler chain directly, so it must be inside a
        pass or a deferred removal could land in the middle of its iteration.
        """
        original = self._original_event
        with _internals.dispatch_pass(self.client, event):
            return await original(event)

    # ── install / uninstall ───────────────────────────────────────────────
    def install(self) -> UpdatePipeline:
        """Wrap the client's ``_dispatch_update`` so the pipeline runs first.

        The pipeline also wraps ``_dispatch_event``, Telethon's album path,
        because that path iterates the handler chain too and must therefore
        share the deferred-removal pass.
        """
        if self._installed:
            return self
        client = self._require_client()
        self._original = _detach(
            client,
            "_dispatch_update",
            _internals.dispatch_update(client),
            self._client_ref,
        )
        self._original_event = _detach(
            client,
            "_dispatch_event",
            getattr(client, "_dispatch_event", None),
            self._client_ref,
        )
        try:
            self.client._dispatch_update = self._patched_dispatch
            if self._original_event is not None:
                self.client._dispatch_event = self._patched_dispatch_event
        except AttributeError as e:  # pragma: no cover - Telethon clients are open
            raise PipelineError(
                f"cannot install the pipeline on {type(self.client).__name__}: {e}"
            ) from e
        self._installed = True
        return self

    def uninstall(self) -> UpdatePipeline:
        """Undo :meth:`install`  -  but only for the patch this object made.

        A second pipeline installed on the same client would otherwise be
        silently removed by the first one's ``uninstall()``.

        An installed pipeline is runtime-only state: it holds a weak reference to
        its client, so it cannot be pickled. Uninstall before serialising a
        client, or serialise the client's own configuration instead.
        """
        if not self._installed:
            return self
        client = self.client
        if client is None:
            # The client is gone, so nothing is patched any more.
            self._installed = False
            return self
        # Compare the bound instance, not the function: two pipelines on the
        # same client share the wrapper function.
        if getattr(client._dispatch_update, "__self__", None) is not self:
            # A later install sits on top of ours. Leave it alone and stay
            # installed, so this pipeline can still unwind after it does.
            LOGS.debug(
                f"[pipeline] uninstall skipped for {client!r}: "
                "another patch is installed on top"
            )
            return self
        _restore(client, "_dispatch_update", self._original)
        if (
            self._original_event is not None
            and getattr(client._dispatch_event, "__self__", None) is self
        ):
            _restore(client, "_dispatch_event", self._original_event)
        # The saved callables are deliberately kept. A pass that is still in
        # flight  -  including a pass belonging to a pipeline stacked above this
        # one  -  falls through to them after the uninstall, so clearing them
        # would turn an unclaimed update into a ``TypeError``. Keeping them
        # costs no retention: ``_detach`` already stripped the only strong path
        # to the client, and a stacked pipeline's own wrapper holds its client
        # weakly too.
        self._installed = False
        return self

    @property
    def installed(self) -> bool:
        return self._installed

    def __enter__(self) -> UpdatePipeline:
        return self.install()

    def __exit__(self, *exc) -> None:
        self.uninstall()

    # ── one-shot / register-and-forget ────────────────────────────────────
    async def _settle(
        self,
        future: asyncio.Future,
        route: Route,
        timeout: float | None,
    ) -> Any:
        """Await a one-shot route's ``future``, then retire the route.

        A ``once`` route retires itself in :meth:`dispatch`; this is the other
        half  -  the caller that gives up early (timeout, cancellation) still
        leaves no route behind.
        """
        try:
            if timeout is None:
                return await future
            return await asyncio.wait_for(future, timeout)
        finally:
            self.remove(route)

    async def wait_for(
        self,
        updates: Any,
        *,
        match: Callable[[Any], Any] | None = None,
        timeout: float | None = None,
        owner: str | None = None,
        priority: int = 1000,
    ) -> Any:
        """Await the next ``updates`` event that satisfies ``match``.

        The route is installed at the front of the pass, claims the event, and
        removes itself in ``finally``. A rejection by ``match`` does **not**
        consume the waiter  -  the same semantics ``CommandDispatcher``'s
        ``once=`` listeners use  -  so a later match still lands.

        A claim ends the pass, so the matched update never reaches Telethon's
        own handlers and never reaches an open ``client.conversation()``.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        async def _capture(event):
            if not future.done():
                future.set_result(event)
            raise events.StopPropagation

        route = self.route(
            updates,
            _capture,
            match=match,
            priority=priority,
            owner=owner,
            exclusive=True,
            once=True,
            route_id=f"wait_for:{id(future):x}",
        )
        return await self._settle(future, route, timeout)

    def instant(
        self,
        updates: Any,
        handler: Callable[[Any], Any],
        *,
        match: Callable[[Any], Any] | None = None,
        timeout: float | None = None,
        priority: int = 1000,
        owner: str | None = None,
    ) -> Awaitable[Any]:
        """Handle one event, then drop the handler  -  no ``client.on(...)``.

        ``handler`` runs **inside** the dispatch pass. Telegram's answer window
        for inline and callback queries is short, and a late answer raises
        ``QueryIdInvalidError``, so answering where the update arrives is the
        only correct place. The awaited value is whatever ``handler`` returns.

        The event is *claimed*: it does not reach Telethon's own handlers or an
        open ``client.conversation()``. That is correct for a query you are
        answering, and wrong for an update you only want to observe.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()

        async def _run(event):
            try:
                result = handler(event)
                if inspect.isawaitable(result):
                    result = await result
            except Exception as e:
                if not future.done():
                    future.set_exception(e)
                raise events.StopPropagation from e
            if not future.done():
                future.set_result(result)
            raise events.StopPropagation

        route = self.route(
            updates,
            _run,
            match=match,
            priority=priority,
            owner=owner,
            exclusive=True,
            once=True,
            route_id=f"instant:{id(future):x}",
        )
        return loop.create_task(self._settle(future, route, timeout))

    # ── introspection ─────────────────────────────────────────────────────
    @property
    def routes(self) -> tuple[Route, ...]:
        return tuple(self._routes)

    def stats(self) -> list[dict[str, Any]]:
        return [route.describe() for route in self._routes]

    def __len__(self) -> int:
        return len(self._routes)

    def __repr__(self) -> str:
        return (
            f"<UpdatePipeline client={type(self.client).__name__} "
            f"routes={len(self._routes)} installed={self._installed}>"
        )


_OWNER_CONTEXT: ContextVar[str | None] = ContextVar(
    "ultroid_pipeline_owner", default=None
)


# ── registry ──────────────────────────────────────────────────────────────
# Keyed weakly by the client itself, not by ``id(client)``. An id-keyed dict
# leaks twice over: the value holds a strong reference to the client, so no
# client is ever collected, and a recycled id can hand back a stranger's
# pipeline. A ``WeakKeyDictionary`` drops the entry the moment the client is
# collected, and identity lookup is exact by construction.
class _Strong:
    """A ``ref``-compatible holder for objects that cannot be weak-referenced."""

    __slots__ = ("_obj",)

    def __init__(self, obj: Any) -> None:
        self._obj = obj

    def __call__(self) -> Any:
        return self._obj


class _Unbound:
    """A client-bound method stored without keeping the client alive.

    ``install()`` saves the method it displaced so ``uninstall()`` can put it
    back and so an unclaimed update can fall through to it. Saving the bound
    method directly would defeat the weak registry: the pipeline would reach the
    client through ``__self__``, so the client could never be collected while
    the pipeline lived. Saving the plain function and re-binding it through the
    pipeline's own weak reference closes that path.
    """

    __slots__ = ("_func", "_client_ref", "own")

    def __init__(self, func: Any, client_ref: Any, own: bool = False) -> None:
        self._func = func
        self._client_ref = client_ref
        #: True when the client carried this as its own instance attribute, so
        #: restoring it must assign it back rather than expose the class method.
        self.own = own

    @property
    def __self__(self) -> Any:
        """The bound instance, so the ``__self__`` identity checks still work."""
        return self._client_ref()

    def rebind(self) -> Any:
        """The bound method again, or ``None`` once the client is gone."""
        client = self._client_ref()
        return None if client is None else self._func.__get__(client)

    async def __call__(self, *args: Any, **kwargs: Any) -> Any:
        client = self._client_ref()
        if client is None:
            raise PipelineError("the pipeline's client has been collected")
        return await self._func(client, *args, **kwargs)


def _detach(client: Any, attribute: str, method: Any, client_ref: Any) -> Any:
    """Store ``method`` without a strong path back to ``client``."""
    if method is None:
        return None
    if getattr(method, "__self__", None) is client:
        own = attribute in getattr(client, "__dict__", {})
        return _Unbound(method.__func__, client_ref, own=own)
    return method


_PIPELINES: MutableMapping[Any, UpdatePipeline] = WeakKeyDictionary()

#: Fallback for clients that cannot be weak-referenced (``__slots__`` without
#: ``__weakref__``, or a test double). Entries here are only removed by
#: :func:`uninstall_pipeline`, which is why the weak map is tried first.
_STRONG_PIPELINES: dict[int, UpdatePipeline] = {}


def _restore(client: Any, attribute: str, saved: Any) -> None:
    """Put a saved dispatch method back on ``client``.

    A method this pipeline detached is reinstated by deleting the instance
    attribute, so the class's own method takes over again; anything else
    (another pipeline's wrapper) is assigned back as it was.
    """
    if isinstance(saved, _Unbound):
        if saved.own:
            # The client had its own override before the pipeline arrived.
            # Deleting the attribute would expose the class method instead  - 
            # or leave the client with no dispatch at all.
            saved = saved.rebind()
        else:
            try:
                delattr(client, attribute)
                return
            except AttributeError:
                saved = saved.rebind()
    setattr(client, attribute, saved)


def _bound_pipeline(client: Any) -> UpdatePipeline | None:
    """The pipeline registered for ``client``, or ``None``."""
    try:
        pipeline = _PIPELINES.get(client)
    except TypeError:
        pipeline = None
    if pipeline is None:
        pipeline = _STRONG_PIPELINES.get(id(client))
        if pipeline is not None and pipeline.client is not client:
            return None
    return pipeline


def pipeline_for(client: Any) -> UpdatePipeline:
    """Return (creating if needed) the pipeline bound to ``client``."""
    pipeline = _bound_pipeline(client)
    if pipeline is None:
        pipeline = UpdatePipeline(client)
        try:
            _PIPELINES[client] = pipeline
        except TypeError:
            _STRONG_PIPELINES[id(client)] = pipeline
    return pipeline


def existing_pipeline(client: Any) -> UpdatePipeline | None:
    """The pipeline already bound to ``client``, without creating one."""
    return _bound_pipeline(client)


def install_pipeline(client: Any) -> UpdatePipeline:
    """Create the client's pipeline and install it in front of dispatch."""
    return pipeline_for(client).install()


def uninstall_pipeline(client: Any) -> None:
    """Uninstall and forget the client's pipeline, from whichever map holds it."""
    pipeline = _STRONG_PIPELINES.pop(id(client), None)
    if pipeline is None:
        try:
            pipeline = _PIPELINES.pop(client, None)
        except TypeError:
            pipeline = None
    if pipeline is not None:
        pipeline.uninstall()


def registered_pipelines() -> tuple[UpdatePipeline, ...]:
    """Every pipeline currently registered. Diagnostics only."""
    return tuple(_PIPELINES.values()) + tuple(_STRONG_PIPELINES.values())


# ── convenience wrappers ──────────────────────────────────────────────────
async def wait_for_update(
    client: Any,
    updates: Any,
    *,
    match: Callable[[Any], Any] | None = None,
    timeout: float | None = None,
) -> Any:
    """Module-level :meth:`UpdatePipeline.wait_for` against ``client``."""
    return await pipeline_for(client).wait_for(updates, match=match, timeout=timeout)


class _Instant:
    """Register-and-forget entry points.

    No plugin holds a ``client.on(...)`` registration open to catch one query;
    the pipeline owns the registration and retires it in ``finally``.
    """

    @staticmethod
    def wait(client: Any, updates: Any, *, match=None, timeout=None) -> Awaitable[Any]:
        return pipeline_for(client).instant(
            updates, lambda event: event, match=match, timeout=timeout
        )

    @staticmethod
    def inline(
        client: Any, handler: Callable, *, match=None, timeout=20.0
    ) -> Awaitable[Any]:
        """Catch one ``InlineQuery`` and answer it inside the dispatch pass."""
        return pipeline_for(client).instant(
            events.InlineQuery, handler, match=match, timeout=timeout
        )

    @staticmethod
    def callback(
        client: Any, handler: Callable, *, match=None, timeout=20.0
    ) -> Awaitable[Any]:
        """Catch one ``CallbackQuery`` and answer it inside the dispatch pass."""
        return pipeline_for(client).instant(
            events.CallbackQuery, handler, match=match, timeout=timeout
        )


instant = _Instant()

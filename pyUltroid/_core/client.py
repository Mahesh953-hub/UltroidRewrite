# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Unified master client for Ultroid.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import inspect
import logging
import warnings
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from collections.abc import Callable

LOG = logging.getLogger("pyUltroid")

__all__ = (
    "MasterClient",
    "OriginMasterClient",
    "RoutingRule",
    "UnknownRoleError",
    "bind_origin",
)

#: Roles a client can be registered under, in routing precedence order.
ROLE_ORDER: tuple[str, ...] = ("user", "bot", "business", "vc")

_origin_client: ContextVar[Any] = ContextVar("master_origin_client", default=None)


class UnknownRoleError(ValueError):
    """A client was registered under a role the master does not know."""


@dataclass(slots=True)
class RoutingRule:
    """One ordered routing decision.

    ``matches`` decides whether the rule applies to the current context;
    ``resolve`` returns the client to use. Rules are consulted in *descending*
    ``priority`` order, then in registration order  -  so the default rules below
    reproduce the historical behaviour exactly while remaining replaceable.
    """

    name: str
    matches: Callable[[MasterClient], bool]
    resolve: Callable[[MasterClient], Any]
    priority: int = 0
    description: str = ""

    def __repr__(self) -> str:
        return f"<RoutingRule {self.name} priority={self.priority}>"


@contextmanager
def bind_origin(client: Any):
    """Bind the real client that dispatched work for context-aware routing."""
    token = _origin_client.set(client)
    try:
        yield client
    finally:
        _origin_client.reset(token)


def _client_in(master: MasterClient, client: Any) -> bool:
    """True when ``client`` is one of ``master``'s real clients."""
    return client is not None and any(c is client for c in master._iter_clients())


def _wants_origin(master: MasterClient) -> bool:
    return _client_in(master, _origin_client.get())


def _resolve_origin(master: MasterClient) -> Any:
    return _origin_client.get()


def _wants_business(master: MasterClient) -> bool:
    return master.in_business_context() and master.bot is not None


def _resolve_bot(master: MasterClient) -> Any:
    return master.bot


def _wants_user(master: MasterClient) -> bool:
    return master.user is not None


def _resolve_user(master: MasterClient) -> Any:
    return master.user


def _wants_bot(master: MasterClient) -> bool:
    return master.bot is not None


def _default_rules() -> list[RoutingRule]:
    """The built-in routing decisions, highest priority first.

    Splitting them out keeps the precedence order declarative  -  the ordering
    used to be implicit in the body of ``MasterClient.route``.

    The predicates live at module level on purpose: a ``MasterClient`` holds
    these rules, so closures or lambdas would make every ``MasterClient``
    unpicklable (``pickle`` cannot serialise local functions).
    """
    return [
        RoutingRule(
            name="origin",
            matches=_wants_origin,
            resolve=_resolve_origin,
            priority=1000,
            description="explicit bind_origin from the dispatching client",
        ),
        RoutingRule(
            name="business",
            matches=_wants_business,
            resolve=_resolve_bot,
            priority=800,
            description="business update; the bot owns the connection",
        ),
        RoutingRule(
            name="user",
            matches=_wants_user,
            resolve=_resolve_user,
            priority=600,
            description="normal chat, via the user account",
        ),
        RoutingRule(
            name="bot",
            matches=_wants_bot,
            resolve=_resolve_bot,
            priority=400,
            description="no user account (pure BOT_MODE)",
        ),
    ]


class MasterClient:
    """Context-aware router over the UltroidClient.

    Parameters
    ----------
    bot:
        The assistant bot ``UltroidClient`` (a real, connected Telethon client)
        or ``None`` when no bot token is configured.
    user:
        The user-account ``UltroidClient`` (a real, connected Telethon client)
        or ``None`` in pure ``BOT_MODE``. This is **never** aliased to ``bot``.
    app:
        The ``PyTgCalls`` instance (wrapping the user client) or ``None``.
    udB:
        The Ultroid database handle, exposed for convenience.
    business:
        Optional business-capable client. When omitted, the primary ``bot`` is
        registered as the business role.
    vc:
        Optional explicit VC wrapper. When omitted, ``app`` is used.
    """

    __slots__ = ("bot", "user", "app", "udB", "_clients", "_rules")

    def __init__(
        self,
        bot: Any = None,
        user: Any = None,
        app: Any = None,
        udB: Any = None,
        business: Any = None,
        vc: Any = None,
    ) -> None:
        self.bot = bot
        self.user = user
        self.app = app
        self.udB = udB
        self._clients: dict[str, list[Any]] = {role: [] for role in ROLE_ORDER}
        self._rules: list[RoutingRule] = _default_rules()
        if user is not None:
            self.add_client("user", user)

        if bot is not None:
            self.add_client("bot", bot)
            if business is None:
                business = bot

        if business is not None:
            self.add_client("business", business)

        if vc is None:
            vc = app
        if vc is not None:
            self.add_client("vc", vc)

    # Registry helpers
    def add_client(self, role: str, client: Any) -> None:
        """Register a real client under ``role`` (user/bot/business/vc)."""
        if client is None:
            return
        if role not in self._clients:
            raise UnknownRoleError(
                f"Unknown role {role!r}; expected one of {sorted(self._clients)}"
            )

        if not any(c is client for c in self._clients[role]):
            self._clients[role].append(client)

        if role == "bot" and self.bot is None:
            self.bot = client
        if role == "user" and self.user is None:
            self.user = client
        if role == "vc" and self.app is None:
            self.app = client

    def remove_client(self, role: str, client: Any) -> None:
        """Remove a previously registered client from ``role``."""
        if role not in self._clients:
            return
        self._clients[role] = [c for c in self._clients[role] if c is not client]
        if role == "bot" and self.bot is client:
            self.bot = self._clients["bot"][0] if self._clients["bot"] else None
        if role == "user" and self.user is client:
            self.user = self._clients["user"][0] if self._clients["user"] else None
        if role == "vc" and self.app is client:
            self.app = self._clients["vc"][0] if self._clients["vc"] else None

    def clients_by_role(self, role: str) -> tuple:
        """All clients registered under ``role``."""
        return tuple(self._clients.get(role, ()))

    # Dynamic client discovery / capabilities
    @property
    def available(self) -> set:
        """Set of available client roles: subset of ``{"bot", "user", "business", "vc"}``."""
        return {role for role, clients in self._clients.items() if clients}

    @property
    def has_bot(self) -> bool:
        return bool(self._clients["bot"])

    @property
    def has_user(self) -> bool:
        return bool(self._clients["user"])

    @property
    def has_business(self) -> bool:
        return bool(self._clients["business"])

    @property
    def has_vc(self) -> bool:
        return bool(self._clients["vc"])

    @property
    def business(self):
        """Primary business-capable client (usually the assistant bot)."""
        return self._clients["business"][0] if self._clients["business"] else None

    @property
    def clients(self):
        """Tuple of every real client held by this master, deduplicated."""
        return tuple(self._iter_clients())

    def _iter_clients(self):
        """Yield every registered client exactly once, in role order."""
        seen: set[int] = set()
        for role in ROLE_ORDER:
            for client in self._clients[role]:
                if id(client) not in seen:
                    seen.add(id(client))
                    yield client

    @property
    def telegram_clients(self):
        """Tuple of all real Telegram clients, excluding VC wrappers."""
        vc_ids = {id(c) for c in self._clients["vc"]}
        return tuple(c for c in self._iter_clients() if id(c) not in vc_ids)

    @property
    def dispatchers(self):
        """Yield each Telegram client's dispatcher exactly once"""
        seen: set[int] = set()
        for client in self.telegram_clients:
            dispatcher = getattr(client, "dispatcher", None)
            if dispatcher is not None and id(dispatcher) not in seen:
                seen.add(id(dispatcher))
                yield dispatcher

    def fan_out_register(self, register_fn, *args, **kwargs):
        """Call ``register_fn(dispatcher, *args, **kwargs)`` on every dispatcher."""
        for dispatcher in self.dispatchers:
            register_fn(dispatcher, *args, **kwargs)

    # Context detection
    @staticmethod
    def _business_connection_id():
        """Return the active business connection id, or ``None``.

        Read from the ``business_connection_id`` context var that
        ``pyUltroid.startup.business`` sets while dispatching a business update.
        Imported lazily to avoid a circular import at module load time.
        """
        try:
            from pyUltroid.startup.business import business_connection_id

            return business_connection_id.get()
        except Exception:
            return None

    def in_business_context(self) -> bool:
        """True while executing inside a business update handler."""
        return self._business_connection_id() is not None

    @property
    def business_connection_id(self):
        """The current business connection id (or ``None`` outside business ctx)."""
        return self._business_connection_id()

    # Routing rules
    @property
    def routing_rules(self) -> tuple[RoutingRule, ...]:
        """The ordered rule set, highest priority first."""
        return tuple(self._rules)

    def add_rule(
        self,
        rule: RoutingRule,
        *,
        replace: bool = False,
    ) -> RoutingRule:
        """Insert a routing rule (or replace the same-named one).

        Rules are consulted by descending ``priority``; ties keep registration
        order, so a new rule with equal priority lands after the existing ones.
        """
        if not isinstance(rule, RoutingRule):
            raise TypeError("add_rule() expects a RoutingRule")
        if replace:
            self._rules = [r for r in self._rules if r.name != rule.name]
        elif any(r.name == rule.name for r in self._rules):
            raise ValueError(f"routing rule {rule.name!r} already exists")
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)
        return rule

    def remove_rule(self, name: str) -> bool:
        """Drop the rule called ``name``; ``False`` when it was not present."""
        remaining = [r for r in self._rules if r.name != name]
        if len(remaining) == len(self._rules):
            return False
        self._rules = remaining
        return True

    # Routing
    def route(self):
        """Return the Telethon client to use for the current context.

        The decision is made by the ordered :class:`RoutingRule` set. The
        built-in rules, highest priority first:

        * ``origin``    -  ``bind_origin`` set by the dispatching client
        * ``business``  -  a business update (the bot owns the connection)
        * ``user``      -  a normal chat, via the user account
        * ``bot``       -  no user account (pure ``BOT_MODE``)
        """
        for rule in self._rules:
            if rule.matches(self):
                return rule.resolve(self)
        raise RuntimeError(
            "MasterClient has no connected client to route to "
            "(neither bot nor user is available)."
        )

    def explain(self) -> RoutingRule | None:
        """The rule that would win for the current context, or ``None``."""
        for rule in self._rules:
            if rule.matches(self):
                return rule
        return None

    def route_for(self, event: Any):
        """Return the client that most plausibly owns ``event``.

        Two cases beat the ambient context:

        * an event whose ``original_update`` is a ``UpdateBot*`` family update
          (inline queries, callback queries, guest-chat queries) can only have
          arrived on a bot account, so the bot answers it;
        * an event carrying a client this master already knows is routed to
          that exact client, which preserves per-client identity for secondary
          user accounts.
        """
        update = getattr(event, "original_update", None)
        name = type(update if update is not None else event).__name__
        if name.startswith("UpdateBot") and self.bot is not None:
            return self.bot

        source = getattr(event, "client", None)
        if source is not None:
            candidate = getattr(source, "master", None)
            origin = getattr(candidate, "_origin", source)
            if any(c is origin for c in self._iter_clients()):
                return origin
        return self.route()

    # Identity (context-aware)
    @property
    def me(self):
        """The identity of the routed client for the current context."""
        try:
            cli = self.route()
        except RuntimeError:
            return None
        return getattr(cli, "me", None)

    async def get_me(self):
        return await self.route().get_me()

    @property
    def is_bot(self) -> bool:
        """True when the effective (routed) client is the bot account."""
        try:
            return self.bot is not None and self.route() is self.bot
        except RuntimeError:
            return False

    @property
    def is_user(self) -> bool:
        """True when the effective (routed) client is the user account."""
        try:
            return self.user is not None and self.route() is self.user
        except RuntimeError:
            return False

    @property
    def uid(self):
        me = self.me
        return getattr(me, "id", None)

    @property
    def full_name(self):
        me = self.me
        if me is None:
            return None
        try:
            from telethon import utils as _telethon_utils

            return _telethon_utils.get_display_name(me)
        except Exception:
            return getattr(me, "first_name", None) or getattr(me, "username", None)

    @property
    def http(self):
        """Lazy shared ``aiohttp.ClientSession`` (from the bot, else the user)."""
        if self.bot is not None:
            return self.bot.http
        if self.user is not None:
            return self.user.http
        return None

    # Transparent Telethon verb delegation
    async def _call_routed(self, method, *args, **kwargs):
        """Invoke a routed Telegram verb with business context when supported."""
        fn = getattr(self.route(), method)
        connection_id = self.business_connection_id
        if connection_id is not None and "business_connection_id" not in kwargs:
            try:
                signature = inspect.signature(fn)
                if "business_connection_id" in signature.parameters:
                    kwargs["business_connection_id"] = connection_id
            except (TypeError, ValueError):
                # Telethon's generated methods may not expose a signature; their
                # existing __call__ business wrapper still handles the request.
                pass
        return await fn(*args, **kwargs)

    async def send_message(self, entity, *args, **kwargs):
        return await self._call_routed("send_message", entity, *args, **kwargs)

    async def send_file(self, entity, *args, **kwargs):
        return await self._call_routed("send_file", entity, *args, **kwargs)

    async def edit_message(self, entity, *args, **kwargs):
        return await self._call_routed("edit_message", entity, *args, **kwargs)

    async def delete_messages(self, entity, *args, **kwargs):
        return await self._call_routed("delete_messages", entity, *args, **kwargs)

    async def get_entity(self, entity):
        return await self._call_routed("get_entity", entity)

    async def get_messages(self, entity, *args, **kwargs):
        return await self._call_routed("get_messages", entity, *args, **kwargs)

    # not satisfied
    async def __call__(self, request, *args, **kwargs):
        """Raw TL request invocation, routed to the correct client.

        In a business context the bot is used and the (already-installed)
        ``TelegramClient.__call__`` patch wraps the request with
        ``InvokeWithBusinessConnectionRequest`` automatically.
        """
        return await self.route()(request, *args, **kwargs)

    def __getattr__(self, name):
        """Transparent delegation for any verb not defined above.

        ``iter_messages``, ``download_media``, ``get_input_entity``,
        ``conversation``, ``parse_id``, ``fast_uploader``, … are all routed to
        the correct client *at call time* so the effective client follows the
        current business/normal context.
        """
        if name.startswith("_"):
            raise AttributeError(name)

        value = getattr(self.route(), name)
        if not callable(value):
            return value

        def _delegate(*args, **kwargs):
            return getattr(self.route(), name)(*args, **kwargs)

        return _delegate

    # Voice / video chat (PyTgCalls) delegation
    def _vc(self):
        if self.app is None:
            raise RuntimeError(
                "Voice-chat (PyTgCalls) is not available on this MasterClient."
            )
        return self.app

    @staticmethod
    def _vc_method(app, *names):
        """Return the first existing PyTgCalls method from ``names``."""
        for n in names:
            fn = getattr(app, n, None)
            if fn is not None:
                return fn
        raise NotImplementedError(
            "This PyTgCalls version does not support the requested operation "
            f"(tried: {', '.join(names)})."
        )

    async def join_vc(self, chat, *args, **kwargs):
        app = self._vc()
        fn = self._vc_method(app, "join_group_call", "join_call")
        return await fn(chat, *args, **kwargs)

    async def leave_vc(self, chat, *args, **kwargs):
        app = self._vc()
        fn = self._vc_method(app, "leave_group_call", "leave_call")
        return await fn(chat, *args, **kwargs)

    async def play(self, chat, file=None, *args, **kwargs):
        app = self._vc()
        fn = self._vc_method(app, "play", "change_stream_call")
        if file is None:
            return await fn(chat, *args, **kwargs)
        return await fn(chat, file, *args, **kwargs)

    async def pause(self, chat, *args, **kwargs):
        app = self._vc()
        fn = self._vc_method(app, "pause", "pause_stream")
        return await fn(chat, *args, **kwargs)

    async def resume(self, chat, *args, **kwargs):
        app = self._vc()
        fn = self._vc_method(app, "resume", "resume_stream")
        return await fn(chat, *args, **kwargs)

    async def skip(self, chat, *args, **kwargs):
        app = self._vc()
        fn = self._vc_method(app, "skip", "end", "stop", "end_stream")
        return await fn(chat, *args, **kwargs)

    # Misc
    def __bool__(self) -> bool:
        return any(c is not None for c in (self.bot, self.user, self.app))

    def __repr__(self) -> str:
        roles = {
            role: len(clients) for role, clients in self._clients.items() if clients
        }
        return (
            f"<MasterClient roles={roles} "
            f"primary=[bot={'set' if self.bot else 'None'} "
            f"user={'set' if self.user else 'None'} "
            f"app={'set' if self.app else 'None'} "
            f"business={'set' if self.business else 'None'}] "
            f"available={sorted(self.available)}>"
        )


class OriginMasterClient:
    """Per-real-client facade so ``event.client.master`` preserves its origin."""

    __slots__ = ("_master", "_origin")

    def __init__(self, master: MasterClient, origin: Any) -> None:
        self._master = master
        self._origin = origin

    def __getattr__(self, name: str):
        with bind_origin(self._origin):
            value = getattr(self._master, name)
        if not callable(value):
            return value

        if inspect.iscoroutinefunction(value):

            async def _async_call(*args, **kwargs):
                with bind_origin(self._origin):
                    return await getattr(self._master, name)(*args, **kwargs)

            return _async_call

        def _call(*args, **kwargs):
            with bind_origin(self._origin):
                return getattr(self._master, name)(*args, **kwargs)

        return _call

    def __bool__(self) -> bool:
        return bool(self._master)

    def __repr__(self) -> str:
        return f"<OriginMasterClient origin={self._origin!r}>"

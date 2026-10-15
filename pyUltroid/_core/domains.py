"""The domain mixins the core union is assembled from.

One object, visibly composed: ``Ultroid(DBMixin, ServiceMixin, ClientMixin,
RegistryMixin, RuntimeMixin)``. Each mixin owns one domain's slots and its
verbs; nothing here imports the package root (the mixins are the leaves of
the graph, loaded before the runtime binds anything).
"""

from __future__ import annotations

from typing import Any

from ..startup.logger import LOGS


class DBMixin:
    """The database domain: the bound store and the config namespace.

    Mixins contribute verbs, never layout (``__slots__ = ()``); the
    concrete ``Ultroid`` declares every slot, so multiple inheritance
    stays layout-compatible.
    """

    __slots__ = ()

    def bind_db(self, db: Any) -> None:
        """Bind the live database (``udB``)."""
        _rebind(self, "db", db)

    def bind_var(self, var: Any) -> None:
        """Bind the config namespace (``configs.Var``)."""
        _rebind(self, "var", var)

    def db_get(self, key: str, default: Any = None) -> Any:
        """Read one key off the bound database (``None`` when unbound)."""
        return self.db.get_key(key) if self.db is not None else default

    def db_set(self, key: str, value: Any) -> None:
        """Write one key to the bound database."""
        if self.db is not None:
            self.db.set_key(key, value)

    def db_del(self, key: str) -> None:
        """Delete one key from the bound database."""
        if self.db is not None:
            self.db.del_key(key)


class ServiceMixin:
    """The service domain: the helper and services unions."""

    __slots__ = ()

    def bind_fns(self, fns: Any) -> None:
        """Bind the helper union (``pyUltroid.fns.fns``)."""
        _rebind(self, "fns", fns)

    def bind_misc(self, misc: Any) -> None:
        """Bind the services union (module registry + scrubber)."""
        _rebind(self, "misc", misc)

    @property
    def modules(self) -> Any:
        """The process Module registry the plugin loader drives."""
        return getattr(self.misc, "registry", None)


class ClientMixin:
    """The client domain: the master router and every derived surface."""

    __slots__ = ()

    #: roles a MasterClient can hold, for ``available_roles``/``has_role``.
    ROLES = ("user", "bot", "business", "vc")

    def bind_master(self, master: Any) -> None:
        """Bind the master client (roles, routing, dispatchers)."""
        _rebind(self, "master", master)

    @property
    def bot(self) -> Any:
        """The primary assistant bot client (or ``None``)."""
        return getattr(self.master, "bot", None)

    @property
    def user(self) -> Any:
        """The primary user client (or ``None``)."""
        return getattr(self.master, "user", None)

    @property
    def app(self) -> Any:
        """The PyTgCalls voice-chat client (or ``None``)."""
        return getattr(self.master, "app", None)

    @property
    def voice(self) -> Any:
        """The PyTgCalls wrapper the runtime started (or ``None``)."""
        return self._voice

    @property
    def clients(self) -> tuple:
        """Every real Telegram client the master knows about."""
        return tuple(getattr(self.master, "telegram_clients", ()) or ())

    @property
    def dispatchers(self) -> tuple:
        """The command dispatcher of every live client."""
        return tuple(
            d for d in (getattr(c, "dispatcher", None) for c in self.clients) if d
        )

    def available_roles(self) -> tuple[str, ...]:
        """Roles that currently hold at least one client."""
        if self.master is None:
            return ()
        return tuple(
            role for role in self.ROLES if self.master.clients_by_role(role)
        )

    def has_role(self, role: str) -> bool:
        """Whether *role* currently holds at least one client."""
        return self.master is not None and bool(self.master.clients_by_role(role))

    def clients_by_role(self, role: str) -> tuple:
        """Every client registered under *role*."""
        if self.master is None:
            return ()
        return self.master.clients_by_role(role)

    def route_client(self) -> Any:
        """The context-aware routed client (business/normal/bot fallback)."""
        return self.master.route() if self.master is not None else None


class RegistryMixin:
    """The plugin-runtime domain: the seven constructed registries.

    Attributes, not inheritance: ``register``, ``get`` and ``__contains__``
    mean different things in PluginRegistry, PluginHelp, ManifestRegistry
    and InlineResultCache, so a flat union would silently shadow four
    pairs of verbs. ``registry``, ``plugin_help`` and the rest keep all
    seven reachable and the names honest.
    """

    __slots__ = ()

    def components(self) -> tuple:
        """The constructed subsystem instances, in declaration order."""
        return (
            self.registry,
            self.plugin_help,
            self.loader,
            self.manifest,
            self.toggles,
            self.inline_cache,
            self.queue,
        )


def _rebind(union: Any, slot: str, value: Any) -> None:
    """Bind *value* to *slot*, warning loudly on a different-object rebind.

    Rebinding to a different object would desync every import-time capture
    of the old one - the exact bug the re-point pass exists to prevent.
    """
    current = getattr(union, slot)
    if current is not None and value is not None and value is not current:
        LOGS.warning(
            "runtime rebind: core.%s replaced a different object;"
            " captures of the old object will not see this one",
            slot,
        )
    if value is not None:
        setattr(union, slot, value)

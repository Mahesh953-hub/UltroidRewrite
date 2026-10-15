"""SarahUB universal runtime/plugin compatibility layer."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from .adapters import builtin_plugins
from .plugin import LegacyPluginAdapter, PluginLifecycle, UniversalPlugin
from .runtime import RuntimeContext, RuntimeSettings
from .storage import UDBStore
from .tl import RuntimeCapabilities, detect_runtime_capabilities, for_client_topology

LOG = logging.getLogger(__name__)


def install_universal_runtime(
    *,
    client,
    assistant,
    user,
    app,
    db,
    dispatcher,
    settings: RuntimeSettings,
    plugins: Iterable[UniversalPlugin] | None = None,
    runtime_capabilities: RuntimeCapabilities | None = None,
) -> PluginLifecycle:
    """Install universal plugins once on an existing Ultroid dispatcher."""
    existing = getattr(client, "universal_plugins", None)
    if existing is not None:
        # Idempotent: a second boot must not rebuild the graph.
        return existing
    detected = runtime_capabilities or detect_runtime_capabilities()
    context = RuntimeContext(
        client=client,
        assistant=assistant,
        user=user,
        app=app,
        db=db,
        dispatcher=dispatcher,
        settings=settings,
        capabilities=for_client_topology(detected, client=client, assistant=assistant),
    )
    lifecycle = PluginLifecycle(context)
    for plugin in tuple(plugins) if plugins is not None else builtin_plugins():
        lifecycle.register(plugin)
        LOG.info("Registered universal plugin: %s", plugin.id)
    client.universal_context = context
    client.universal_plugins = lifecycle

    global _UNIVERSAL
    _UNIVERSAL = Universal(context, lifecycle)
    LOG.info("Universal runtime installed on %s", client)
    return lifecycle


# ── the universal runtime, as one object ────────────────────────────────────
# RuntimeContext and PluginLifecycle already collide on useful names
# (``register``), and the UDB stores are per-key. So the union owns them as
# attributes rather than inheriting: everything is reachable, nothing is
# shadowed, and the context stays the single object plugins are handed.


class Universal:
    """The universal runtime behind one object.

    ``uni.context``   what every plugin receives
    ``uni.lifecycle`` register/start/stop
    ``uni.caps``      what this topology actually supports
    ``uni.store(key)`` a typed udB-backed store
    ``uni.authorized(user_id)`` owner-or-sudo check
    """

    __slots__ = ("context", "lifecycle", "caps", "client")

    def __init__(self, context: RuntimeContext, lifecycle: PluginLifecycle) -> None:
        self.context = context
        self.lifecycle = lifecycle
        self.caps = context.capabilities
        self.client = context.client

    # ── delegated, because these are what every caller wants ───────────────

    def register(self, plugin: UniversalPlugin) -> None:
        self.lifecycle.register(plugin)

    def plugins(self) -> tuple:
        return self.lifecycle.plugins

    async def start(self) -> None:
        await self.lifecycle.start()

    async def stop(self) -> None:
        await self.lifecycle.stop()

    def authorized(self, user_id: int | None) -> bool:
        return self.context.is_authorized(user_id)

    def store(self, key: str, default):
        """A typed udB-backed store for *key*, defaulting on a type mismatch."""
        return UDBStore(self.context.db, key, default)

    def capabilities(self) -> RuntimeCapabilities:
        return self.caps

    def supports(self, name: str) -> bool:
        """Whether a :class:`RuntimeCapabilities` field is on."""
        return bool(getattr(self.caps, name, False))


#: The process-wide universal runtime, set by :func:`install_universal_runtime`.
_UNIVERSAL: Universal | None = None


def universal() -> Universal:
    """The installed :class:`Universal`.

    Raises rather than returning ``None``: a caller that reached here wants the
    runtime, and silently continuing without it would hide a boot-order bug.
    """
    if _UNIVERSAL is None:
        raise RuntimeError(
            "the universal runtime is not installed yet; "
            "it is installed by install_universal_runtime() during startup"
        )
    return _UNIVERSAL


__all__ = (
    "LegacyPluginAdapter",
    "PluginLifecycle",
    "RuntimeCapabilities",
    "RuntimeContext",
    "RuntimeSettings",
    "UDBStore",
    "Universal",
    "UniversalPlugin",
    "install_universal_runtime",
    "universal",
)

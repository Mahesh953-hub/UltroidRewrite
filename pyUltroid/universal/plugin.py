"""Universal plugin contract and lifecycle manager."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Callable, Iterable
from typing import Any, Protocol, runtime_checkable

from .runtime import RuntimeContext

LOG = logging.getLogger(__name__)


@runtime_checkable
class UniversalPlugin(Protocol):
    id: str

    def register(self, context: RuntimeContext) -> None: ...


class LegacyPluginAdapter:
    """Adapt a legacy module/object or a bare ``register(dispatcher)`` callable."""

    def __init__(self, plugin: Any, plugin_id: str | None = None) -> None:
        register = getattr(plugin, "register", None)
        self._register: Callable[[Any], None] | None = (
            register if callable(register) else None
        )

        if self._register is None and callable(plugin):
            self._register = plugin

        if self._register is None:
            raise TypeError(
                "Legacy plugin must expose register(dispatcher) or be callable"
            )

        self.id = plugin_id or getattr(plugin, "__name__", type(plugin).__name__)

    def register(self, context: RuntimeContext) -> None:
        self._register(context.dispatcher)


class PluginLifecycle:
    """Register and lifecycle-manage explicit universal plugins once."""

    def __init__(self, context: RuntimeContext) -> None:
        self.context = context
        self._plugins: list[UniversalPlugin] = []
        self._started = False
        self._stopped = False

    @property
    def plugins(self) -> tuple[UniversalPlugin, ...]:
        return tuple(self._plugins)

    def register(self, plugin: UniversalPlugin) -> None:
        plugin.register(self.context)
        self._plugins.append(plugin)

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for plugin in self._plugins:
            await _run_hook(plugin, "start", self.context)

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        for plugin in reversed(self._plugins):
            await _run_hook(plugin, "stop", self.context)


async def _run_hook(plugin: Any, name: str, context: RuntimeContext) -> None:
    hook = getattr(plugin, name, None)
    if callable(hook):
        result = hook(context)
        if inspect.isawaitable(result):
            await result


def register_plugins(
    context: RuntimeContext, plugins: Iterable[UniversalPlugin]
) -> PluginLifecycle:
    lifecycle = PluginLifecycle(context)
    for plugin in plugins:
        lifecycle.register(plugin)
        LOG.info("Registered universal plugin: %s", plugin.id)
    return lifecycle

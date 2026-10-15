"""The one process object, visibly assembled from the domain mixins.

This is the composition root - the class statement IS the architecture:

```python
class Ultroid(DBMixin, ServiceMixin, ClientMixin, RegistryMixin,
              RuntimeMixin):
    ...
```

One instance (``core``) per process. The state mixins hold the domains
(database, services, clients, the plugin registries); ``RuntimeMixin``
holds the lifecycle (``run`` -> ``deploy`` -> ``serve_forever``, ``stop``).
Constructing the instance builds the registries and nothing else; every
runtime object is *bound* by ``core.run()`` once the real thing exists.
"""

from __future__ import annotations

from typing import Any

from ._core.domains import ClientMixin, DBMixin, RegistryMixin, ServiceMixin
from ._core.events import InlineResultCache
from ._core.loader import AsyncPluginLoader, PluginHelp, PluginRegistry
from ._core.manifest import ManifestRegistry
from ._core.queue import QueueManager
from ._core.runtime import RuntimeMixin
from ._core.toggles import ToggleRegistry

__all__ = ["Ultroid", "core"]


class Ultroid(DBMixin, ServiceMixin, ClientMixin, RegistryMixin, RuntimeMixin):
    """One object owning the process state and its lifecycle.

    Constructed at import (the plugin-runtime registries); bound at
    ``run()`` (database, config, master, unions); deployed and served by
    the same object. The module-level aliases on ``pyUltroid`` remain the
    plugin surface - the union is the same objects through one place.
    """

    __slots__ = (
        # the database domain
        "db",
        "var",
        # the service domain
        "fns",
        "misc",
        # the client domain
        "master",
        "_voice",
        # the plugin-runtime domain (constructed)
        "registry",
        "plugin_help",
        "loader",
        "manifest",
        "toggles",
        "inline_cache",
        "queue",
        # the lifecycle
        "_boot_at",
        "_hosted_on",
        "_prefix",
        "_universal_lifecycle",
    )

    def __init__(self) -> None:
        # The constructed half: exactly as the seven module-level singletons
        # were, with their own defaults (QueueManager resolves udB and the
        # client lazily, at use time).
        self.registry = PluginRegistry()
        self.plugin_help = PluginHelp()
        self.loader = AsyncPluginLoader()
        self.manifest = ManifestRegistry()
        self.toggles = ToggleRegistry()
        self.inline_cache = InlineResultCache()
        self.queue = QueueManager()
        # The bound half: None until ``run()`` binds the real objects.
        self.db = None
        self.var = None
        self.fns = None
        self.misc = None
        self.master = None
        self._voice = None
        self._boot_at = None
        self._hosted_on = None
        self._prefix = (".", "/", ".")
        self._universal_lifecycle = None

    def status(self) -> dict[str, Any]:
        """A snapshot for diagnostics: what is bound and how much is live."""
        plugins = self.registry.get_active() if self.registry is not None else ()
        return {
            "bound": {
                "db": self.db is not None,
                "var": self.var is not None,
                "master": self.master is not None,
                "fns": self.fns is not None,
                "misc": self.misc is not None,
            },
            "plugins": len(plugins),
            "modules": len(self.modules) if self.modules is not None else 0,
            "roles": self.available_roles(),
            "clients": len(self.clients),
            "dispatchers": len(self.dispatchers),
        }


#: The process-wide core state.
core = Ultroid()

# Wire the loader module's patch points to the union's single objects, so
# the module-level helpers (``unload_plugin`` / ``reload_plugin_async`` /
# ``confirm_unload``) and the tests that drive them all read one truth.
# Done here because the loader module cannot import the union at its own
# import time (the composition root imports the loader first).
from ._core import loader as _loader_module  # noqa: E402

_loader_module.registry = core.registry
_loader_module._default_loader = core.loader

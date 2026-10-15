"""The two process-wide services this package keeps: the module registry and
the secret scrubber.

They are attributes rather than base classes. ModuleRegistry has 18 public
methods and SecretScrubber 8, and they share no name but ``__init__`` - so a
flat union would work today, but the moment either grows a ``bind`` or a
``run`` it would start shadowing silently. Attributes keep that impossible, and
``scrubber`` and ``registry`` stay legible at the call site.

    misc.registry.register_module(...)      # register
    misc.scrubber.run(...)                  # scrub secrets around a block
"""

from __future__ import annotations

from typing import Any

from .keep_safe import SecretScrubber
from .registry import ModuleRegistry  # noqa: F401 - type hint

__all__ = ["Misc"]


from .registry import registry as _module_registry


class Misc:
    """The module registry and the secret scrubber, as one object."""

    __slots__ = ("registry", "scrubber")

    def __init__(
        self,
        registry: "ModuleRegistry" = None,
        scrubber: SecretScrubber = None,
    ) -> None:
        # The process-wide ModuleRegistry singleton from ``registry.py`` -
        # the same object the W3-B driver loads plugin Modules into, so
        # ``misc.registry`` and the driver's registry are one truth.
        self.registry = registry if registry is not None else _module_registry
        self.scrubber = scrubber if scrubber is not None else SecretScrubber()

    def components(self) -> dict[str, Any]:
        """Member name -> instance, for diagnostics."""
        return {name: getattr(self, name) for name in self.__slots__}


#: The process-wide misc services.
misc = Misc()

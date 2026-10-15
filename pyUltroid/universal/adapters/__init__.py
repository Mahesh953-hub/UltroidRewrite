"""Built-in source compatibility adapters."""

from .discovery import DiscoveryPlugin
from .inline import InlineInfoPlugin
from .managed_bots import ManagedBotsPlugin
from .mode import ModePlugin


def builtin_plugins():
    """Create fresh plugins for each runtime installation."""
    return (DiscoveryPlugin(), ModePlugin(), ManagedBotsPlugin(), InlineInfoPlugin())

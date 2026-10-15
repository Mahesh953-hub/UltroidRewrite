"""Command-only discovery for the universal compatibility adapters."""

from __future__ import annotations

from ..runtime import RuntimeContext


class DiscoveryPlugin:
    id = "universal.discovery"

    def register(self, context: RuntimeContext) -> None:
        @context.dispatcher.command("universal", priority=20)
        async def universal_command(update, args: str, reply):
            caps = context.capabilities
            return (
                "Universal compatibility commands\n\n"
                ".mode [business|guest|normal|buttons on|off|interval SECONDS|reset]\n"
                ".managedbot [status|list|create|rotate BOT_ID]\n"
                "Inline query: universal_info (owner/sudo only)\n\n"
                f"Guest chat: {'available' if caps.guest_chat else 'unavailable'}\n"
                f"Managed-bot updates: {'available' if caps.managed_bot_updates else 'unavailable'}\n"
                f"Token rotation: {'available' if caps.token_rotation else 'unavailable'}\n"
                f"Inline query: {'available' if caps.inline_query else 'unavailable'}"
            )

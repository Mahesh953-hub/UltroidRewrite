"""A narrow, owner-gated inline capability from the source project."""

from __future__ import annotations

import random

from telethon import functions, types

from ..runtime import RuntimeContext


def inline_surfaces():
    """Return the inline surfaces this Telethon build provides, or ``None``."""
    required = (
        "UpdateBotInlineQuery",
        "InputBotInlineResult",
        "InputBotInlineMessageText",
    )
    if not all(hasattr(types, name) for name in required):
        return None
    if not hasattr(getattr(functions, "messages", None), "SetInlineBotResultsRequest"):
        return None
    return types, functions


class InlineInfoPlugin:
    id = "universal.inline_info"

    def register(self, context: RuntimeContext) -> None:
        surfaces = inline_surfaces()
        if not context.capabilities.inline_query or surfaces is None:
            return
        types, functions = surfaces

        @context.dispatcher.listen(
            types.UpdateBotInlineQuery,
            match=lambda update: getattr(update, "query", "").strip()
            == "universal_info",
            priority=20,
        )
        async def inline_info(update):
            if not context.is_authorized(getattr(update, "user_id", None)):
                return
            result = types.InputBotInlineResult(
                id=str(random.randint(0, 1_000_000)),
                type="article",
                title="Ultroid runtime",
                description="Universal compatibility runtime status",
                send_message=types.InputBotInlineMessageText(
                    message="Ultroid Universal Runtime\nManaged-bot and mode adapters are active.",
                    no_webpage=True,
                ),
            )
            await context.client(
                functions.messages.SetInlineBotResultsRequest(
                    query_id=update.query_id,
                    results=[result],
                    cache_time=0,
                )
            )

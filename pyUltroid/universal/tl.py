"""Capability checks and safe managed-bot helpers.

This module deliberately does not patch Telethon or register custom TL objects.
Unsupported managed-bot creation uses Telegram's public deep-link flow instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from telethon import functions, types
from telethon.errors.rpcbaseerrors import RPCError


@dataclass(frozen=True, slots=True)
class RuntimeCapabilities:
    guest_chat: bool = False
    managed_bot_updates: bool = False
    token_rotation: bool = False
    inline_query: bool = False
    styled_buttons: bool = False


def capabilities(
    types_module: Any | None, functions_module: Any | None
) -> RuntimeCapabilities:
    """Detect supported public Telethon symbols without considering topology."""
    messages = getattr(functions_module, "messages", None)
    bots = getattr(functions_module, "bots", None)
    guest_chat = bool(
        types_module
        and messages
        and hasattr(types_module, "UpdateBotGuestChatQuery")
        and hasattr(types_module, "InputBotInlineResult")
        and hasattr(types_module, "InputBotInlineMessageText")
        and hasattr(messages, "SetBotGuestChatResultRequest")
    )
    inline_query = bool(
        types_module
        and messages
        and hasattr(types_module, "UpdateBotInlineQuery")
        and hasattr(types_module, "InputBotInlineResult")
        and hasattr(types_module, "InputBotInlineMessageText")
        and hasattr(messages, "SetInlineBotResultsRequest")
    )
    return RuntimeCapabilities(
        guest_chat=guest_chat,
        managed_bot_updates=bool(
            types_module and hasattr(types_module, "UpdateManagedBot")
        ),
        token_rotation=bool(bots and hasattr(bots, "ExportBotTokenRequest")),
        inline_query=inline_query,
        styled_buttons=bool(
            types_module and hasattr(types_module, "KeyboardButtonStyle")
        ),
    )


def is_bot_client(client: Any) -> bool:
    """Return true only for a client with known bot identity metadata."""
    return bool(getattr(getattr(client, "me", None), "bot", False))


def for_client_topology(
    detected: RuntimeCapabilities,
    *,
    client: Any,
    assistant: Any,
) -> RuntimeCapabilities:
    bot_runtime = (
        assistant is not None and client is assistant and is_bot_client(client)
    )
    if bot_runtime:
        return detected
    return RuntimeCapabilities()


def detect_runtime_capabilities() -> RuntimeCapabilities:
    """Symbol detection against the pinned fork's actual layer."""
    return capabilities(types, functions)


def managed_bot_deep_link(manager_username: str, suggested_name: str = "") -> str:
    """Build Telegram's public, schema-independent managed-bot creation URL."""
    username = manager_username.lstrip("@")
    url = f"https://t.me/newbot/{quote(username, safe='')}"
    return f"{url}/{quote(suggested_name, safe='')}" if suggested_name else url


async def rotate_managed_bot_token(
    client: Any, bot_user_id: int, functions_module: Any = functions
) -> bool:
    """Revoke a managed bot's current token and report whether that worked.

    Backed by the pinned fork's real ``bots.ExportBotTokenRequest`` with
    ``revoke=True``. This is **destructive**: the previous token dies with the
    call, and Telegram returns its only replacement in the response, which this
    function discards. The caller therefore learns only whether the rotation
    succeeded and must tell the operator that the bot stays offline until a new
    token is issued. The token is never returned, logged, or persisted.

    Returns ``False`` when the runtime lacks the request, when the request
    signature differs, or when Telegram rejects the call.
    """
    request_type = getattr(
        getattr(functions_module, "bots", None), "ExportBotTokenRequest", None
    )
    if request_type is None:
        return False
    try:
        await client(request_type(bot=bot_user_id, revoke=True))
    except TypeError:
        return False
    except RPCError:
        return False
    return True

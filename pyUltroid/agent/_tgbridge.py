"""Telegram bridge for the agent tool layer.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from pyUltroid.startup.logger import LOGS

__all__ = (
    "RAW_ALLOWED",
    "parse_entity_ref",
    "resolve_entity",
    "run_tg_call",
    "send_via_master",
    "send_file_via_master",
    "read_recent",
    "resolve_raw_function",
    "invoke_raw",
)

# Read-only raw MTProto functions the agent may invoke. Everything not listed
# here is refused; there is no wildcard. Write-type requests (send, edit,
# delete, join, ...) are deliberately absent.
RAW_ALLOWED: frozenset[str] = frozenset(
    {
        # conversations / messages
        "messages.GetHistoryRequest",
        "messages.SearchRequest",
        "messages.GetMessagesRequest",
        "messages.GetPeerDialogs",
        "messages.GetFullChatRequest",
        "messages.GetWebPageRequest",
        # entities / profiles
        "channels.GetChannelsRequest",
        "channels.GetFullChannelRequest",
        "users.GetUsersRequest",
        "users.GetFullUserRequest",
        "contacts.ResolveUsernameRequest",
        # misc read-only metadata
        "help.GetConfig",
        "help.GetNearestDc",
        "messages.GetAllStickers",
    }
)

_TME_RE = re.compile(
    r"^(?:https?://)?t\.me/(?:joinchat/|\+)?([A-Za-z0-9_-]+)/?$"
)
_INT_RE = re.compile(r"^-?\d+$")

_MESSAGE_MAX_TEXT = 2000
_READ_MAX_LIMIT = 100


def parse_entity_ref(ref: Any) -> Any:
    """Normalize a chat reference into something Telethon can resolve.

    Accepts numeric ids (int or digit string), ``@usernames``, bare
    usernames, and ``t.me/<name>`` links. Invite hashes come back as the
    bare token (Telethon resolves them via ``joinchat`` semantics).
    """
    if ref is None:
        return None
    s = str(ref).strip()
    if not s:
        return None
    m = _TME_RE.match(s)
    if m:
        return m.group(1)
    if _INT_RE.match(s):
        try:
            return int(s)
        except ValueError:
            return s
    return s


async def run_tg_call(coro_fn, *args, **kwargs) -> dict:
    """Await a Telegram call and normalize its failure modes.

    Returns ``{"ok": True, "result": ...}`` or a structured error the agent
    can reason about (flood wait, RPC error, transport failure).
    """
    from telethon import errors

    try:
        return {"ok": True, "result": await coro_fn(*args, **kwargs)}
    except errors.FloodWaitError as e:
        LOGS.warning(f"[AGENT:TG] FloodWait {getattr(e, 'seconds', '?')}s")
        return {
            "ok": False,
            "error": "FloodWait",
            "wait_seconds": getattr(e, "seconds", None),
        }
    except errors.RPCError as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "rpc": type(e).__name__,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _pick_route_client(master: Any, route: str) -> Any:
    """Resolve an explicit route name to a real client or raise ValueError."""
    r = (route or "auto").lower()
    if r in ("auto", ""):
        return master.route()
    if r == "user":
        client = master.user
    elif r == "bot":
        client = master.bot
    elif r == "business":
        client = master.business or master.bot
    else:
        raise ValueError(f"Unknown route '{route}' (auto|user|bot|business)")
    if client is None:
        raise ValueError(
            f"Route '{r}' has no connected client; available: "
            f"{sorted(master.available()) or 'none'}"
        )
    return client


async def send_via_master(
    text: str,
    entity_ref: Any,
    *,
    route: str = "auto",
    reply_to: int | None = None,
    parse_mode: str | None = None,
    link_preview: bool = True,
    rich: bool = False,
) -> dict:
    """Send plain text by default, or native RichMessage when explicitly asked."""
    from pyUltroid import master
    from pyUltroid._core.richmsg import (
        RichMarkup,
        blocks_to_plain_text,
        client_can_send_rich_message,
        send_rich,
    )

    from ._telegram import rich_output

    try:
        client = _pick_route_client(master, route)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "error": str(e)}

    entity = parse_entity_ref(entity_ref)
    kwargs: dict[str, Any] = {"link_preview": link_preview}
    if reply_to is not None:
        kwargs["reply_to"] = int(reply_to)

    if rich:
        if not await client_can_send_rich_message(client):
            return {
                "ok": False,
                "error": (
                    "Native RichMessages require a bot account or a confirmed "
                    "Telegram Premium user account. Send with rich=false for "
                    "ordinary plain text. No RichMessage request was sent."
                ),
            }
        if parse_mode is None:
            content = rich_output(text)
        else:
            mode = str(parse_mode).strip().lower()
            if mode in {"md", "markdown", "markdownv3"}:
                mode = "markdown"
            elif mode != "html":
                return {
                    "ok": False,
                    "error": "parse_mode must be HTML, Markdown, or omitted for automatic detection.",
                }
            content = RichMarkup(text, format=mode)
        result = await run_tg_call(send_rich, client, entity, content, **kwargs)
    else:
        if parse_mode is None:
            markup = rich_output(text)
        else:
            mode = str(parse_mode).strip().lower()
            if mode in {"md", "markdownv3"}:
                mode = "markdown"
            elif mode not in {"html", "markdown"}:
                return {
                    "ok": False,
                    "error": "parse_mode must be HTML, Markdown, or omitted for automatic detection.",
                }
            markup = RichMarkup(text, format=mode)
        plain_text = blocks_to_plain_text(markup)
        if not plain_text.strip():
            return {"ok": False, "error": "Plain-text message cannot be empty."}
        result = await run_tg_call(
            client.send_message,
            entity,
            plain_text,
            parse_mode=None,
            **kwargs,
        )
    if result.get("ok"):
        sent = result.get("result")
        return {
            "ok": True,
            "route": route,
            "message_id": getattr(sent, "id", None),
            "chat_id": getattr(getattr(sent, "peer_id", None), "user_id", None)
            or getattr(getattr(sent, "peer_id", None), "channel_id", None)
            or getattr(getattr(sent, "peer_id", None), "chat_id", None),
        }
    return result


async def send_file_via_master(
    file_obj: Any,
    entity_ref: Any,
    *,
    route: str = "auto",
    caption: str | None = None,
) -> dict:
    """Send an in-memory attachment through the master router as plain text."""
    from pyUltroid import master

    try:
        client = _pick_route_client(master, route)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "error": str(e)}

    entity = parse_entity_ref(entity_ref)
    result = await run_tg_call(
        client.send_file,
        entity,
        file_obj,
        caption=caption,
        parse_mode=None,
    )
    if result.get("ok"):
        sent = result.get("result")
        return {
            "ok": True,
            "route": route,
            "message_id": getattr(sent, "id", None),
        }
    return result


def _serialize_message(msg: Any) -> dict:
    date = getattr(msg, "date", None)
    if isinstance(date, datetime):
        date = date.isoformat()
    reply_to = getattr(msg, "reply_to", None)
    reply_id = getattr(reply_to, "reply_to_msg_id", None) if reply_to else None
    return {
        "id": getattr(msg, "id", None),
        "sender_id": getattr(msg, "sender_id", None),
        "date": date,
        "text": (msg.message or "")[:_MESSAGE_MAX_TEXT] if msg.message else "",
        "out": bool(getattr(msg, "out", False)),
        "reply_to": reply_id,
        "media": type(msg.media).__name__ if getattr(msg, "media", None) else None,
    }


async def read_recent(
    entity_ref: Any,
    *,
    limit: int = 20,
    route: str = "user",
) -> dict:
    """Read the most recent messages of a chat via the routed client."""
    from pyUltroid import master

    try:
        client = _pick_route_client(master, route)
    except (ValueError, RuntimeError) as e:
        return {"ok": False, "error": str(e)}

    limit = max(1, min(int(limit or 20), _READ_MAX_LIMIT))
    entity = parse_entity_ref(entity_ref)
    result = await run_tg_call(client.get_messages, entity, limit=limit)
    if not result.get("ok"):
        return result
    msgs = result.get("result") or []
    return {
        "ok": True,
        "count": len(msgs),
        "messages": [_serialize_message(m) for m in msgs if m is not None],
    }


def resolve_raw_function(name: str):
    """Resolve an allowlisted ``namespace.Class`` raw request name.

    Returns the request class, or ``None`` when the name is not on the
    read-only allowlist or does not exist in the installed Telethon.
    """
    if name not in RAW_ALLOWED:
        return None
    namespace, _, cls_name = name.partition(".")
    if not cls_name:
        return None
    try:
        from telethon.tl import functions

        ns = getattr(functions, namespace, None)
        if ns is None:
            return None
        return getattr(ns, cls_name, None)
    except ImportError:
        return None


async def invoke_raw(client: Any, name: str, args: dict | None = None) -> dict:
    """Build and invoke an allowlisted raw request, flood-aware."""
    request_cls = resolve_raw_function(name)
    if request_cls is None:
        return {
            "ok": False,
            "error": (
                f"Raw function '{name}' is not on the read-only allowlist. "
                f"Allowed: {', '.join(sorted(RAW_ALLOWED))}"
            ),
            "denied": True,
        }
    try:
        request = request_cls(**(args or {}))
    except TypeError as e:
        return {"ok": False, "error": f"Bad arguments for {name}: {e}"}
    result = await run_tg_call(client, request)
    if result.get("ok"):
        raw = result.get("result")
        result["result_repr"] = repr(raw)[:4000]
        # The raw result object itself is not JSON-serialisable; the repr
        # above is what the model reads. Structured serializers can be added
        # per-request-type later if needed.
        result.pop("result", None)
    return result

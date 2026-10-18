"""Schema-only RichMessage diagnostics; never log message text, peers, or credentials."""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from typing import Any

from telethon import types
from telethon.errors import RPCError, rpc_errors_dict

LOGGER = logging.getLogger("pyUltLogs.rich_debug")
# Enable only this schema-only channel, not MTProto wire/session or HTTP logs.
LOGGER.setLevel(logging.DEBUG)

_RPC_LABELS = frozenset({
    "MESSAGE_NOT_MODIFIED", "MESSAGE_ID_INVALID", "MESSAGE_EDIT_TIME_EXPIRED",
    "MESSAGE_EMPTY", "MESSAGE_TOO_LONG", "ENTITY_BOUNDS_INVALID", "ENTITIES_TOO_LONG",
    "BUTTON_DATA_INVALID", "BUTTON_TYPE_INVALID", "REPLY_MARKUP_INVALID",
    "REPLY_MARKUP_TOO_LONG", "INLINE_BOT_REQUIRED", "CHAT_WRITE_FORBIDDEN",
    "QUERY_ID_INVALID", "BAD_REQUEST", "INTERNAL", "FLOOD", "AUTH_KEY_UNREGISTERED",
    "PREMIUM_ACCOUNT_REQUIRED", "BOT_METHOD_INVALID", "MEDIA_INVALID", "FILE_REFERENCE_EXPIRED",
})
# Rich error namespaces are evolving faster than Telethon's generated subclasses.
# Only bounded symbolic identifiers in these protocol namespaces are accepted.
_RICH_RPC = re.compile(r"(?:RICH_MESSAGE|RICH_TEXT|PAGE_BLOCK)_[A-Z][A-Z0-9_]{0,71}\Z")
_FIELD = re.compile(r"[a-z0-9_.:-]{1,80}\Z")
_CHILD_FIELDS = ("blocks", "items", "rows", "cells", "text", "title", "caption", "credit", "author")


def rpc_identifier(error: Any) -> str:
    """Return a symbolic server error, never str(error) or arbitrary error text."""
    for identifier in _RPC_LABELS:
        if type(error) is rpc_errors_dict.get(identifier):
            return identifier
    message = getattr(error, "message", None)
    if type(message) is str and (message in _RPC_LABELS or _RICH_RPC.fullmatch(message)):
        return message
    return "UNKNOWN_RPC"


def _field(value: Any, default: str = "unknown") -> str:
    return value if type(value) is str and _FIELD.fullmatch(value) else default


def rich_schema(content: Any) -> dict[str, Any]:
    """Count known TL constructors and text size without exposing any text or IDs."""
    counts: Counter[str] = Counter()
    tags: Counter[str] = Counter()
    chars = 0
    details_open = 0
    details_closed = 0
    seen: set[int] = set()
    stack = [content]
    visited = 0
    while stack and visited < 4096:
        node = stack.pop()
        visited += 1
        if isinstance(node, (list, tuple)):
            stack.extend(node[:512])
            continue
        if node is None:
            continue
        name = type(node).__name__
        if not isinstance(node, types.TLObject) or getattr(types, name, None) is not type(node):
            continue
        if id(node) in seen:
            continue
        seen.add(id(node))
        if name.startswith(("InputRichMessage", "PageBlock", "PageList", "PageTable", "Text")):
            counts[name] += 1
        if name == "PageBlockDetails":
            details_open += int(node.open is True)
            details_closed += int(node.open is not True)
        if name == "InputRichMessageHTML":
            markup = node.html
            chars += len(markup)
            # Names only, no attributes or content; isolate details vs legacy quotes.
            for tag in re.findall(r"<\s*(details|summary|blockquote|pre|code|h[1-6]|table|ul|ol|li|hr)\b", markup.lower()):
                tags[tag] += 1
        elif name == "InputRichMessageMarkdown":
            chars += len(node.markdown)
        elif name == "TextPlain":
            chars += len(node.text)
        for field in _CHILD_FIELDS:
            child = getattr(node, field, None)
            if isinstance(child, (types.TLObject, list, tuple)):
                stack.append(child)
    return {
        "constructors": dict(sorted(counts.items())),
        "html_tags": dict(sorted(tags.items())),
        "characters": chars,
        "details_open": details_open,
        "details_closed": details_closed,
        "schema_truncated": bool(stack),
    }


def trace_rich_rpc(
    stage: str, *, content: Any = None, error: Any = None,
    request_kind: str | None = None, target_kind: str | None = None,
    outcome: str | None = None,
) -> None:
    """Emit one bounded DEBUG record containing only safe structural metadata."""
    record = {
        "stage": _field(stage), "request": _field(request_kind),
        "target": _field(target_kind), "outcome": _field(outcome),
        **rich_schema(content),
    }
    if error is not None:
        code = getattr(error, "code", None)
        record["rpc_id"] = rpc_identifier(error)
        record["rpc_code"] = code if type(code) is int and -1000 <= code <= 1000 else None
        record["error_kind"] = "RPCError" if isinstance(error, RPCError) else "LocalValidationError"
    LOGGER.debug("[rich_debug] %s", json.dumps(record, sort_keys=True, separators=(",", ":")))


async def call_rich_request(
    client: Any, request: Any, *, content: Any = None,
    target_kind: str = "peer", sender: Any = None,
) -> Any:
    """Trace a raw request without changing its result, routing, or error behavior."""
    kind = type(request).__name__
    # Constructor names use CamelCase; do not accept arbitrary dynamic class names.
    request_kind = {
        "SendMessageRequest": "send_message", "EditMessageRequest": "edit_message",
        "EditInlineBotMessageRequest": "edit_inline_bot_message",
    }.get(kind, "unknown")
    metadata = {"content": content, "request_kind": request_kind, "target_kind": target_kind}
    trace_rich_rpc("request", outcome="attempt", **metadata)
    try:
        result = await (client._call(sender, request) if sender is not None else client(request))
    except RPCError as error:
        trace_rich_rpc("request", error=error, outcome="rpc_error", **metadata)
        raise
    except (TypeError, ValueError) as error:
        trace_rich_rpc("request", error=error, outcome="local_error", **metadata)
        raise
    trace_rich_rpc("request", outcome="success", **metadata)
    return result

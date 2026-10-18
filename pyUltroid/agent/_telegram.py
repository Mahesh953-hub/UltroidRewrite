"""Telegram surface the agent speaks: text, entities and coloured keyboards.

The pinned Telethon fork is not uniform about what a message hands back, and
the agent reads messages from two clients that were configured differently.
Every read path funnels through :func:`text_of` so no caller has to know which
shape it got.

Observed shapes, all of them real in this fork:

* ``str`` - no parse mode on the client, or an unformatted message.
* ``(text, entities)`` - ``Message.text`` when ``parse_mode`` is set and the
  parse mode's ``unparse``/``parse`` returns a pair. ``RICH_MD.parse`` does.
* ``None`` - ``MessageService``, or a message with no body.
* the object itself - anything exposing ``.message``/``.raw_text``.

For buttons, the fork extends every ``Button.*`` with ``style=`` (mapped to
``primary``/``danger``/``success``) and ``icon=``. This module re-exports
SarahUB's own builders so agent code never constructs a raw TL button.
"""

from __future__ import annotations

import re
from typing import Any, Final, Sequence

__all__ = [
    "BUTTON_STYLES",
    "entities_of",
    "inline_rows",
    "public_text",
    "raw_message",
    "rich_output",
    "row_of",
    "text_of",
    "unwrap_pair",
]

#: The style tokens the fork's ``_get_style`` accepts. Anything else raises
#: inside Telethon, so they are validated here where the message is useful.
BUTTON_STYLES: Final[frozenset[str]] = frozenset(
    {"primary", "danger", "success"}
)


def unwrap_pair(value: Any, fallback: str = "") -> tuple[str, list[Any]]:
    """Split any parse-mode result into ``(text, entities)``.

    The single chokepoint for the fork's tuple-returning parse modes. Accepts
    a bare string, a ``(text, entities)`` pair, ``None``, or any object with a
    ``.message``/``.raw_text`` attribute. Never raises: a message that cannot
    be read becomes *fallback*.
    """
    if value is None:
        return fallback, []
    if isinstance(value, str):
        return value, []
    if isinstance(value, (tuple, list)):
        if not value:
            return fallback, []
        head = value[0]
        text = head if isinstance(head, str) else fallback
        entities = list(value[1]) if len(value) > 1 and value[1] else []
        return text, [e for e in entities if e is not None]

    # An object that carries the message: prefer the raw body, because
    # ``.text`` may itself be the tuple we are here to unwrap.
    for attribute in ("raw_text", "message"):
        inner = getattr(value, attribute, None)
        if isinstance(inner, str):
            return inner, list(getattr(value, "entities", None) or [])
        if inner is not None and not isinstance(inner, (int, float, bool)):
            return unwrap_pair(inner, fallback)

    text = getattr(value, "text", None)
    if text is not None:
        return unwrap_pair(text, fallback)
    return fallback, []


def text_of(message: Any, fallback: str = "") -> str:
    """The plain text of *message*, whatever shape the fork returned.

    This is the only text accessor agent code is allowed to use. Reading
    ``message.text`` directly is what puts ``"('hi', [...])"`` in a prompt.
    """
    text, _ = unwrap_pair(message, fallback)
    return text


def entities_of(message: Any) -> list[Any]:
    """The formatting entities of *message*, always a list, never None."""
    _, entities = unwrap_pair(message)
    return entities


def raw_message(message: Any) -> str:
    """The unformatted body, ignoring entities and any parse mode."""
    for attribute in ("message", "raw_text"):
        inner = getattr(message, attribute, None)
        if isinstance(inner, str):
            return inner
    if isinstance(message, str):
        return message
    return text_of(message)


_RICH_HTML_TAG = re.compile(
    r"(?is)</?(?:html|body|p|div|h[1-6]|b|strong|i|em|u|s|del|ins|"
    r"blockquote|ul|ol|li|table|caption|thead|tbody|tfoot|tr|td|th|details|summary|button|"
    r"pre|code|a|br|hr|span|tg-spoiler|tg-emoji)\b[^>]*>"
)
_TOOL_TRACE_RE = re.compile(
    r"(?is)(?:\[\[\s*tool_calls?\s*\]\]|<\|tool_calls?\|>|<tool_calls?>|"
    r"(?:^|\n)\s*tool_calls?\s*[:=]).*"
)


def public_text(value: Any) -> str:
    """Remove a serialized tool-protocol tail from an agent response."""
    text = str(value or "").strip()
    match = _TOOL_TRACE_RE.search(text)
    return text[: match.start()].strip() if match else text


def rich_output(value: Any):
    """Preserve an agent's HTML/Markdown answer as a native RichMarkup value.

    Explicit whole-response ``html``/``md`` fences are unwrapped. Markdown
    code blocks are excluded from HTML detection so code samples are not
    mistaken for message markup. Plain text is treated as Markdown because it
    is valid unformatted Markdown and remains safe in the fallback renderer.
    """
    from pyUltroid._core.richmsg import RichMarkup

    text = str(value or "").strip()
    format = None
    wrapper = re.fullmatch(
        r"```(html|md|markdown)[ \t]*\n([\s\S]*?)\n```", text, re.IGNORECASE
    )
    if wrapper:
        text = wrapper.group(2).strip()
        format = "html" if wrapper.group(1).lower() == "html" else "markdown"
    if format is None:
        probe = re.sub(r"```[\s\S]*?```|`[^`]*`", "", text)
        format = "html" if _RICH_HTML_TAG.search(probe) else "markdown"
    return RichMarkup(text, format=format)


def inline_rows(
    rows: Sequence[Sequence[Any]],
    *,
    default_style: str = None,
    default_icon: str = None,
) -> list[list[Any]]:
    """Build an inline keyboard, normalised, validated and style-defaulted.

    Each cell may be a pre-built Telethon button (passed through untouched), a
    ``dict`` handed to SarahUB's :func:`custom.rich.button`, or a tuple in any
    of the documented ``ikm`` shapes. A single cell is promoted to one row so
    callers may pass ``["Yes", "No"]`` for a two-button row.

    Rows are returned as a list of lists - Telethon accepts that directly, and
    returning a pre-built ``ReplyInlineMarkup`` instead raises
    ``ReplyMarkupInvalidError``.
    """
    from custom.rich import button as _rich_button

    if rows and not isinstance(rows[0], (list, tuple)):
        rows = [rows]

    built: list[list[Any]] = []
    for row in rows:
        out_row: list[Any] = []
        for cell in row:
            built_cell = _build_cell(
                cell,
                _rich_button,
                default_style=default_style,
                default_icon=default_icon,
            )
            out_row.append(built_cell)
        if out_row:
            built.append(out_row)
    return built


def _build_cell(
    cell: Any,
    rich_button: Any,
    *,
    default_style: str | None,
    default_icon: str | None,
) -> Any:
    """Normalise one keyboard cell to a Telethon button."""
    # A pre-built button, or a raw TL object, is already correct.
    if _is_built_button(cell):
        return cell

    if isinstance(cell, str):
        cell = {"text": cell, "data": cell}

    if isinstance(cell, dict):
        spec = dict(cell)
        spec.setdefault("style", default_style)
        if default_icon is not None:
            spec.setdefault("icon", default_icon)
        style = spec.get("style")
        if style is not None and style not in BUTTON_STYLES:
            raise ValueError(
                f"unknown button style {style!r}; expected one of "
                f"{sorted(BUTTON_STYLES)}"
            )
        return rich_button(**spec)

    if isinstance(cell, (tuple, list)):
        return _build_cell(
            _tuple_to_dict(cell),
            rich_button,
            default_style=default_style,
            default_icon=default_icon,
        )

    raise TypeError(f"cannot build a button from {type(cell).__name__}")


def _tuple_to_dict(cell: Sequence[Any]) -> dict:
    """Map the documented ``ikm`` tuple shapes onto a button spec.

    ``(text, data)``  ``(text, data, style)``  ``(text, data, style, icon)``
    ``(text, "url", url[, style[, icon]])``
    """
    items = list(cell)
    if not items:
        raise ValueError("empty button tuple")
    text = items[0]
    rest = items[1:]

    if len(rest) >= 2 and rest[0] == "url":
        spec: dict = {"text": text, "url": rest[1]}
        if len(rest) > 2 and rest[2]:
            spec["style"] = rest[2]
        if len(rest) > 3 and rest[3]:
            spec["icon"] = rest[3]
        return spec

    if not rest:
        return {"text": text, "data": text}
    spec = {"text": text, "data": rest[0]}
    if len(rest) > 1 and rest[1]:
        spec["style"] = rest[1]
    if len(rest) > 2 and rest[2]:
        spec["icon"] = rest[2]
    return spec


def _is_built_button(cell: Any) -> bool:
    """Whether *cell* is already a Telethon button object.

    Detected positively, before any ``len()``/indexing, because a built button
    is not a sequence and the tuple handling above would corrupt it.
    """
    from telethon.tl.custom import Button as _Button
    from telethon.tl.types import KeyboardButton

    if isinstance(cell, KeyboardButton):
        return True
    if isinstance(cell, _Button):
        return True
    # Telethon's Button helpers are classes producing a single TL object, so
    # anything already carrying a TL ``_``-prefixed type id is pre-built.
    return type(cell).__module__.startswith("telethon.tl.")


def row_of(*cells: Any) -> list[list[Any]]:
    """One keyboard row from the given cells."""
    return inline_rows([list(cells)])

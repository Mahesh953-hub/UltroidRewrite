# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Entity/format builders"""

from __future__ import annotations

import datetime
from typing import Any, List, Optional, Tuple
from collections.abc import Iterable, Sequence

from telethon.extensions.markdownv3 import MarkdownV3
from telethon.tl import types


# UTF-16 helpers (entity offset/length are in UTF-16 code units)
def _utf16_len(text: str) -> int:
    """Length of *text* in UTF-16 code units (astral-plane chars count as 2)."""
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


# Entity kinds → raw schema type name
_ENTITY_TYPES = {
    "spoiler": "MessageEntitySpoiler",
    "blockquote": "MessageEntityBlockquote",
    "expandable": "MessageEntityBlockquote",
    "custom_emoji": "MessageEntityCustomEmoji",
    "bankcard": "MessageEntityBankCard",
    "formatted_date": "MessageEntityFormattedDate",
}


def has(kind: str) -> bool:
    """Whether the raw schema type backing entity *kind* exists in this build.

    ``kind`` is one of the keys of :data:`_ENTITY_TYPES` (``"spoiler"``,
    ``"blockquote"``, ``"expandable"``, ``"custom_emoji"``, ``"bankcard"``,
    ``"formatted_date"``). Unknown kinds return ``False``.
    """
    name = _ENTITY_TYPES.get(kind)
    if not name:
        return False
    return getattr(types, name, None) is not None


def _require(kind: str):
    name = _ENTITY_TYPES[kind]
    cls = getattr(types, name, None)
    if cls is None:
        raise RuntimeError(f"telethon.tl.types.{name} is not available in this build")
    return cls


# tag(text, ...) -> (text, [entity]) builders
def spoiler(text: str) -> tuple[str, list[Any]]:
    """Wrap *text* in a ``MessageEntitySpoiler`` covering it entirely."""
    cls = _require("spoiler")
    return text, [cls(0, _utf16_len(text))]


def blockquote(text: str, *, collapsed: bool = False) -> tuple[str, list[Any]]:
    """Wrap *text* in a ``MessageEntityBlockquote``.

    ``collapsed=True`` renders as Telegram's "expandable blockquote" (the
    quote starts folded, with a chevron the reader taps to expand it)  -  see
    also :func:`expandable`, a thin alias for that case.
    """
    cls = _require("blockquote")
    return text, [cls(0, _utf16_len(text), collapsed=bool(collapsed))]


def expandable(text: str) -> tuple[str, list[Any]]:
    """``blockquote(text, collapsed=True)``  -  Telegram's expandable quote."""
    return blockquote(text, collapsed=True)


def custom_emoji(text: str, doc_id: int) -> tuple[str, list[Any]]:
    """Wrap *text* (the fallback glyph(s)) in a ``MessageEntityCustomEmoji``.

    Rendering the custom emoji document (``doc_id``) requires the *sending*
    account to have Telegram Premium; other clients see the fallback ``text``
    as plain characters  -  see the note in ``custom/rich.py``.
    """
    cls = _require("custom_emoji")
    return text, [cls(0, _utf16_len(text), document_id=int(doc_id))]


def bankcard(text: str) -> tuple[str, list[Any]]:
    """Wrap *text* (a card number) in a ``MessageEntityBankCard``."""
    cls = _require("bankcard")
    return text, [cls(0, _utf16_len(text))]


def formatted_date(
    text: str,
    date: datetime.datetime | None,
    *,
    relative: bool | None = None,
    short_time: bool | None = None,
    long_time: bool | None = None,
    short_date: bool | None = None,
    long_date: bool | None = None,
    day_of_week: bool | None = None,
) -> tuple[str, list[Any]]:
    """Wrap *text* in a ``MessageEntityFormattedDate``.

    ``date`` is the ``datetime`` the client should render *text* as; the
    ``relative``/``short_time``/``long_time``/``short_date``/``long_date``/
    ``day_of_week`` flags mirror the raw schema fields (rendering hints  - 
    the exact presentation is client-controlled).
    """
    cls = _require("formatted_date")
    return text, [
        cls(
            0,
            _utf16_len(text),
            date=date,
            relative=relative,
            short_time=short_time,
            long_time=long_time,
            short_date=short_date,
            long_date=long_date,
            day_of_week=day_of_week,
        )
    ]


_TAG_BUILDERS = {
    "spoiler": spoiler,
    "blockquote": blockquote,
    "expandable": expandable,
    "custom_emoji": custom_emoji,
    "bankcard": bankcard,
    "formatted_date": formatted_date,
}


def tag(kind: str, text: str, **kwargs) -> tuple[str, list[Any]]:
    """Generic dispatcher: ``tag("spoiler", text)`` == ``spoiler(text)``.

    Convenience for callers that pick the entity kind dynamically (e.g. from
    a manifest/config value) instead of importing each builder by name.
    """
    builder = _TAG_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(
            f"Unknown entity kind {kind!r} (known: {sorted(_TAG_BUILDERS)})"
        )
    return builder(text, **kwargs)


def combine(*segments: tuple[str, Sequence[Any]]) -> tuple[str, list[Any]]:
    """Concatenate ``(text, entities)`` pairs, shifting later entity offsets.

    Each *segment* is a ``(text, entities)`` tuple such as those returned by
    :func:`spoiler`/:func:`blockquote`/etc. or ``([], "")`` for plain text
    with no entities. Entities keep their ``length`` and get their ``offset``
    shifted by the UTF-16 length of everything already concatenated before
    them, so the result is safe to feed straight into
    ``client.send_message(..., formatting_entities=entities, parse_mode=None)``.
    """
    text_parts: list[str] = []
    entities: list[Any] = []
    offset = 0
    for chunk_text, chunk_entities in segments:
        chunk_text = chunk_text or ""
        for entity in chunk_entities or ():
            entity.offset += offset
            entities.append(entity)
        text_parts.append(chunk_text)
        offset += _utf16_len(chunk_text)
    return "".join(text_parts), entities


# RICH_MD  -  MarkdownV3 parse/unparse wrapper
class _RichMarkdown:
    """``parse``/``unparse`` wrapper around the fork's ``MarkdownV3``.

    Wraps an *instance* of ``telethon.extensions.markdownv3.MarkdownV3``
    directly rather than depending on the ``"mdv3"`` string being registered
    in ``telethon.utils.sanitize_parse_mode``. The instance can be passed
    straight through as ``parse_mode=RICH_MD``  -  Telethon's
    ``sanitize_parse_mode`` already accepts any object exposing callable
    ``parse``/``unparse`` attributes, so no registration is required either way.

    ``available`` stays part of the public surface because callers branch on
    it; with the pinned fork it is always true.
    """

    __slots__ = ("_impl",)

    def __init__(self) -> None:
        self._impl = MarkdownV3()

    @property
    def available(self) -> bool:
        return True

    def parse(self, text: str) -> tuple[str, list[Any]]:
        return self._impl.parse(text)

    def unparse(self, text: str, entities: Iterable[Any] | None) -> str:
        return self._impl.unparse(text, list(entities or ()))


#: Ready-to-use wrapper  -  pass directly as ``parse_mode=RICH_MD``.
RICH_MD = _RichMarkdown()


def mdv3_to_entities(text: str) -> tuple[str, list[Any]]:
    return RICH_MD.parse(text)


# ── parse-mode result normalisation ───────────────────────────────────────
# Telethon parse modes are not uniform about what they hand back.
# ``CustomMarkdown.parse`` and ``MarkdownV3.parse`` return a ``(text,
# entities)`` pair; ``unparse`` returns a bare string; the ``ziptools``
# fallback yields ``(text, None)``; and plenty of call sites just hold a plain
# string they never parsed. Anything that stores one of those in a slot typed
# ``str`` ends up sending ``"('hi', [...])"`` to Telegram, or raising
# ``TypeError`` on a ``str`` operation. These two functions are the single
# chokepoint that makes every shape safe, so callers never have to guess.
def normalize_parsed(
    value: Any,
    *,
    fallback: str = "",
) -> tuple[str, list[Any]]:
    """Coerce any parse-mode output into a ``(text, entities)`` pair.

    Accepts a bare string, a ``(text, entities)`` pair where ``entities`` may be
    ``None``, a ``Message``-like object exposing ``message``/``entities``, or
    ``None``. ``fallback`` is used when the value carries no text at all.

    >>> normalize_parsed("hi")
    ('hi', [])
    >>> normalize_parsed(("hi", None))
    ('hi', [])
    """
    if value is None:
        return fallback, []
    if isinstance(value, str):
        return value, []
    if isinstance(value, (tuple, list)) and len(value) == 2:
        head, tail = value
        if isinstance(head, str) and (tail is None or isinstance(tail, (list, tuple))):
            return head, list(tail or ())
    message = getattr(value, "message", None)
    if isinstance(message, str):
        return message, list(getattr(value, "entities", None) or ())
    return str(value), []


def plain_text(value: Any, *, fallback: str = "") -> str:
    """The text half of any parse-mode output, discarding entities.

    Use this wherever a ``str`` is required and the value may have come from a
    ``parse()`` call  -  it is what stops a ``CustomMarkdown`` tuple from being
    formatted into a message body verbatim.
    """
    return normalize_parsed(value, fallback=fallback)[0]


__all__ = (
    "RICH_MD",
    "bankcard",
    "blockquote",
    "combine",
    "custom_emoji",
    "expandable",
    "formatted_date",
    "has",
    "mdv3_to_entities",
    "normalize_parsed",
    "plain_text",
    "spoiler",
    "tag",
)

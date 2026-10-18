from __future__ import annotations

import datetime
from collections.abc import Sequence
from typing import Any

from telethon.extensions import html as _html
from telethon.extensions import markdown as _markdown
from telethon.extensions import markdownv2 as _markdownv2
from telethon.extensions.markdownv3 import MarkdownV3
from telethon.tl import types
from telethon.tl.custom.message import CustomMarkdown

__all__ = (
    "RICH_MD",
    "bankcard",
    "blockquote",
    "combine",
    "custom_emoji",
    "emoji_entity",
    "expandable",
    "formatted_date",
    "mdv3_to_entities",
    "normalize_parsed",
    "parse_rich",
    "plain_text",
    "premium_emoji",
    "spoiler",
    "unparse_rich",
    "with_emojis",
)

RICH_MD = MarkdownV3()


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le", "surrogatepass")) // 2


def spoiler(text: str) -> tuple[str, list[Any]]:
    return text, [types.MessageEntitySpoiler(0, _utf16_len(text))]


def blockquote(text: str, *, collapsed: bool = False) -> tuple[str, list[Any]]:
    return text, [
        types.MessageEntityBlockquote(0, _utf16_len(text), collapsed=bool(collapsed))
    ]


def expandable(text: str) -> tuple[str, list[Any]]:
    return blockquote(text, collapsed=True)


def emoji_entity(offset: int, length: int, document_id: int) -> Any:
    offset, length, document_id = int(offset), int(length), int(document_id)
    if offset < 0 or length <= 0 or document_id <= 0:
        raise ValueError(
            "emoji offset must be non-negative; length and document_id must be positive"
        )
    return types.MessageEntityCustomEmoji(
        offset=offset, length=length, document_id=document_id
    )


def custom_emoji(text: str, doc_id: int) -> tuple[str, list[Any]]:
    return text, [emoji_entity(0, _utf16_len(text), doc_id)]


def premium_emoji(document_id: int, fallback: str = "⭐"):
    return fallback, [emoji_entity(0, _utf16_len(fallback), document_id)]


def with_emojis(text: str, emojis: dict, *, default_fallback: str = "⭐"):
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    if not isinstance(default_fallback, str) or not default_fallback:
        raise ValueError("default_fallback must be a non-empty string")
    entities = []
    for placeholder, spec in (emojis or {}).items():
        if not isinstance(placeholder, str) or not placeholder:
            raise ValueError("emoji placeholders must be non-empty strings")
        if isinstance(spec, (tuple, list)):
            if not spec:
                raise ValueError(f"emoji spec for {placeholder!r} is empty")
            document_id = spec[0]
            fallback = spec[1] if len(spec) > 1 else default_fallback
        else:
            document_id, fallback = spec, default_fallback
        if not isinstance(fallback, str) or not fallback:
            raise ValueError(f"emoji fallback for {placeholder!r} must be non-empty")
        index = text.find(placeholder)
        if index < 0:
            continue
        offset = _utf16_len(text[:index])
        text = text[:index] + fallback + text[index + len(placeholder) :]
        entities.append(emoji_entity(offset, _utf16_len(fallback), document_id))
    entities.sort(key=lambda entity: entity.offset)
    return text, entities


def bankcard(text: str) -> tuple[str, list[Any]]:
    return text, [types.MessageEntityBankCard(0, _utf16_len(text))]


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
    return text, [
        types.MessageEntityFormattedDate(
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


def combine(*segments: tuple[str, Sequence[Any]]) -> tuple[str, list[Any]]:
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


def normalize_parsed(value: Any, *, fallback: str = "") -> tuple[str, list[Any]]:
    if value is None:
        return fallback, []
    if isinstance(value, str):
        return value, []
    if isinstance(value, (tuple, list)) and len(value) == 2:
        text, entities = value
        if isinstance(text, str) and (
            entities is None or isinstance(entities, (list, tuple))
        ):
            return text, list(entities or ())
    message = getattr(value, "message", None)
    if isinstance(message, str):
        return message, list(getattr(value, "entities", None) or ())
    return str(value), []


def plain_text(value: Any, *, fallback: str = "") -> str:
    return normalize_parsed(value, fallback=fallback)[0]


def mdv3_to_entities(text: str) -> tuple[str, list[Any]]:
    return RICH_MD.parse(text)


def _parser(mode: Any):
    if mode is None:
        return None
    if callable(getattr(mode, "parse", None)) and callable(
        getattr(mode, "unparse", None)
    ):
        return mode
    key = str(mode).strip().lower()
    if key in {"mdv3", "v3", "rich", "markdownv3"}:
        return RICH_MD
    if key in {"custom", "custommarkdown", "custom_markdown"}:
        return CustomMarkdown()
    if key in {"html", "htm"}:
        return _html
    if key in {"md", "markdown"}:
        return _markdown
    if key in {"v2", "mdv2", "markdownv2"}:
        return _markdownv2.Markdown()
    raise ValueError(f"Unknown rich parse mode {mode!r}")


def parse_rich(text: str, mode: Any = "mdv3") -> tuple[str, list[Any]]:
    if not isinstance(text, str):
        raise TypeError("rich text must be a string")
    parser = _parser(mode)
    if parser is None:
        return text, []
    return normalize_parsed(parser.parse(text), fallback=text)


def unparse_rich(text: Any, entities: Sequence[Any] | None = None, mode: Any = "mdv3") -> str:
    text, pair_entities = normalize_parsed(text)
    parser = _parser(mode)
    if parser is None:
        return text
    rendered = parser.unparse(text, list(entities if entities is not None else pair_entities))
    return normalize_parsed(rendered, fallback=text)[0]

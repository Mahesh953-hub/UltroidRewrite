# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Dual-engine rich-message builder for the Layer 228 Telethon fork.

The legacy engine exports every block as ordinary Telegram text plus entities
through ``telethon.extensions.markdownv3``.  The rich engine exports the same
blocks as Telegram-native ``InputRichMessageHTML`` or
``InputRichMessageMarkdown``.  Normal users therefore retain a useful message
when rich blocks are unavailable to their account, while Premium users and bots
can opt into (or automatically select) the server-rendered representation.
"""

from __future__ import annotations

import html as _html
from dataclasses import dataclass
from typing import Any, List, Literal, Optional, Tuple
from collections.abc import Sequence

from telethon import utils as _tl_utils
from telethon.errors import RPCError
from telethon.extensions import markdownv3 as _mdv3
from telethon.tl import functions as _functions
from telethon.tl import types as _types

from .entities import normalize_parsed

# ``custom.rich.parse_rich`` turns the fallback dialect into message
# entities; if the rich extras failed to import (or an old ``custom`` is on
# the path) every fallback still works, just without entities.
try:
    from custom.rich import parse_rich
except Exception:
    parse_rich = None


Engine = Literal["auto", "legacy", "rich"]
MessageFormat = Literal["html", "markdown"]

__all__ = (
    "LegacyMessage",
    "RichBlocks",
    "bot_inline_rich_message",
    "edit_rich",
    "has_native_rich_messages",
    "legacy_message",
    "rich_message",
    "rich_message_html",
    "rich_message_markdown",
    "rich_sender",
    "send_rich",
    "edit_rich",
    "table_to_text",
    "normalize_blocks",
    "render_blocks_native_html",
    "render_blocks_fallback_html",
    "blocks_to_plain_text",
)


@dataclass(frozen=True)
class LegacyMessage:
    """A normal Telegram message produced by the always-safe legacy engine."""

    text: str
    entities: list
    markup: str
    format: MessageFormat


class RichBlocks:
    """Accumulate format-neutral message blocks and account policy.

    Existing ``RichBlocks()`` call sites remain valid.  New callers may set all
    four policy axes on the builder and override any of them in
    :func:`send_rich`, :func:`edit_rich`, or :func:`rich_message`.
    """

    __slots__ = ("_blocks", "engine", "format", "is_premium", "is_bot")

    def __init__(
        self,
        *,
        engine: Engine = "auto",
        format: MessageFormat = "html",
        is_premium: bool | None = None,
        is_bot: bool | None = None,
    ) -> None:
        self.engine = _validate_engine(engine)
        self.format = _validate_format(format)
        self.is_premium = _optional_bool("is_premium", is_premium)
        self.is_bot = _optional_bool("is_bot", is_bot)
        self._blocks: list[dict] = []

    def __len__(self) -> int:
        return len(self._blocks)

    def __bool__(self) -> bool:
        return bool(self._blocks)

    def __iter__(self):
        return iter(self._blocks)

    def to_list(self) -> list[dict]:
        return list(self._blocks)

    def header(self, text: str, level: int = 1) -> RichBlocks:
        self._blocks.append(
            {"type": "header", "text": str(text), "level": max(1, min(6, int(level)))}
        )
        return self

    def paragraph(self, text: str) -> RichBlocks:
        self._blocks.append({"type": "paragraph", "text": str(text)})
        return self

    def list(self, items: Sequence[Any], ordered: bool = False) -> RichBlocks:
        self._blocks.append(
            {
                "type": "list",
                "items": [str(item) for item in items],
                "ordered": bool(ordered),
            }
        )
        return self

    def code(self, text: str, lang: str = "") -> RichBlocks:
        self._blocks.append(
            {"type": "code", "text": str(text), "lang": str(lang or "")}
        )
        return self

    def table(
        self,
        rows: Sequence[Sequence[Any]],
        *,
        is_bordered: bool = True,
        is_striped: bool = False,
        is_compact: bool = False,
        caption: str | None = None,
    ) -> RichBlocks:
        self._blocks.append(
            {
                "type": "table",
                "rows": [
                    ["" if cell is None else str(cell) for cell in row] for row in rows
                ],
                "is_bordered": bool(is_bordered),
                "is_striped": bool(is_striped),
                "is_compact": bool(is_compact),
                "caption": None if caption is None else str(caption),
            }
        )
        return self

    def expandable(self, title: str, body: str) -> RichBlocks:
        self._blocks.append(
            {"type": "expandable", "title": str(title), "body": str(body)}
        )
        return self

    def media(
        self,
        *,
        photo: Any = None,
        doc: Any = None,
        caption: str | None = None,
    ) -> RichBlocks:
        if photo is not None and doc is not None:
            raise ValueError("media accepts either photo or doc, not both")
        self._blocks.append(
            {
                "type": "media",
                "photo": photo,
                "doc": doc,
                "caption": None if caption is None else str(caption),
            }
        )
        return self

    def build(
        self,
        *,
        engine: Engine | None = None,
        format: MessageFormat | None = None,
        is_premium: bool | None = None,
        is_bot: bool | None = None,
        rtl: bool = False,
        noautolink: bool | None = None,
    ) -> Any:
        """Build a :class:`LegacyMessage` or native input rich message."""
        return rich_message(
            self,
            engine=engine,
            format=format,
            is_premium=is_premium,
            is_bot=is_bot,
            rtl=rtl,
            noautolink=noautolink,
        )


def _validate_engine(value: str) -> Engine:
    value = str(value).lower()
    if value not in {"auto", "legacy", "rich"}:
        raise ValueError("engine must be 'auto', 'legacy', or 'rich'")
    return value  # type: ignore[return-value]


def _validate_format(value: str) -> MessageFormat:
    value = str(value).lower()
    if value not in {"html", "markdown"}:
        raise ValueError("format must be 'html' or 'markdown'")
    return value  # type: ignore[return-value]


def normalize_blocks(blocks: Any) -> list[dict]:
    """Normalize *blocks* to the canonical list of block dicts every
    renderer in this module understands.

    Accepts, in order of preference:

    * a :class:`RichBlocks` instance  -  returns its own ``to_list()``;
    * an iterable already made of block dicts  -  each is shallow-copied
      (``dict(b)``) so callers can't mutate the accumulator by mutating
      the returned list's items;
    * an iterable that also mixes in bare strings, or any other
      non-dict item  -  each becomes a ``{"type": "paragraph", "text":
      ...}`` block (``str()``-coerced for non-string items). This is the
      one normalization seam simple callers (e.g. ``InlineResults
      .article_rich``) rely on so they can hand a plain string, or a
      list of strings/loose dicts, without hand-building
      :class:`RichBlocks`.
    * ``None``/falsy *blocks* normalizes to ``[]``.

    Existing dict blocks are otherwise passed through unchanged  -  every
    renderer here already tolerates the "loose" shapes a hand-written
    dict is likely to use (missing ``level``/``lang``/``ordered`` fall
    back to sane defaults via ``.get(...)``), so no further coercion is
    needed for ``{"type": "header"|"paragraph"|"code", "text": ...}`` or
    ``{"type": "list", "items": [...], "ordered": bool}`` shapes.
    """
    if isinstance(blocks, RichBlocks):
        return blocks.to_list()
    if isinstance(blocks, str):
        # a bare string is ONE paragraph, not an iterable of characters
        blocks = [blocks]
    out: list[dict] = []
    for b in blocks or []:
        if isinstance(b, dict):
            out.append(dict(b))
        elif isinstance(b, str):
            out.append({"type": "paragraph", "text": b})
        else:
            out.append({"type": "paragraph", "text": str(b)})
    return out


def _optional_bool(name: str, value: bool | None) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise TypeError(f"{name} must be bool or None")
    return value


def _normalize_blocks(blocks: Any) -> list[dict]:
    if isinstance(blocks, RichBlocks):
        return blocks.to_list()
    if isinstance(blocks, str):
        return [{"type": "paragraph", "text": blocks}]
    return [dict(block) for block in (blocks or [])]


def _policy(
    blocks: Any,
    engine: Engine | None,
    format: MessageFormat | None,
    is_premium: bool | None,
    is_bot: bool | None,
) -> tuple[Engine, MessageFormat, bool | None, bool | None]:
    if isinstance(blocks, RichBlocks):
        engine = blocks.engine if engine is None else engine
        format = blocks.format if format is None else format
        is_premium = blocks.is_premium if is_premium is None else is_premium
        is_bot = blocks.is_bot if is_bot is None else is_bot
    engine = _validate_engine(engine or "auto")
    format = _validate_format(format or "html")
    is_premium = _optional_bool("is_premium", is_premium)
    is_bot = _optional_bool("is_bot", is_bot)
    if engine == "auto":
        engine = "rich" if is_bot is True or is_premium is True else "legacy"
    return engine, format, is_premium, is_bot


def has_native_rich_messages() -> bool:
    """Compatibility helper; Layer 228 always provides native rich messages."""
    return True


def table_to_text(rows: Sequence[Sequence[Any]], caption: str | None = None) -> str:
    """Render *rows* (first row = header) as a plain monospace text grid.

    The canonical aligned-text table renderer: cells are ``str()``-coerced,
    columns padded to the widest cell, a ``-+-`` rule follows the header
    row. Public so other table views (e.g. ``plugins/stars.py``,
    ``pyUltroid/universal/adapters/managed_bots.py``) reuse it instead of
    re-implementing the alignment."""
    str_rows = [[str(cell) for cell in row] for row in (rows or [])]
    if not str_rows:
        return ""
    width = max((len(row) for row in str_rows), default=0)
    col_widths = [
        max((len(row[i]) if i < len(row) else 0) for row in str_rows)
        for i in range(width)
    ]
    lines = []
    if caption:
        lines.append(str(caption))
        lines.append("")
    for r_index, row in enumerate(str_rows):
        cells = [
            (row[i] if i < len(row) else "").ljust(col_widths[i]) for i in range(width)
        ]
        lines.append(" | ".join(cells).rstrip())
        if r_index == 0 and len(str_rows) > 1:
            lines.append("-+-".join("-" * w for w in col_widths))
    return "\n".join(lines)


def _table_to_text(block: dict) -> str:
    """Render a table block as a plain monospace text grid (fallback +
    native-embed-as-preformatted-text use this identically)."""
    return table_to_text(block.get("rows") or [], caption=block.get("caption"))


def _render_table_html(block: dict) -> str:
    classes = [
        name
        for flag, name in (
            (block.get("is_bordered"), "bordered"),
            (block.get("is_striped"), "striped"),
            (block.get("is_compact"), "compact"),
        )
        if flag
    ]
    attrs = f' class="{" ".join(classes)}"' if classes else ""
    parts = [f"<table{attrs}>"]
    if block.get("caption"):
        parts.append(f"<caption>{_html.escape(block['caption'])}</caption>")
    for r_index, row in enumerate(block.get("rows") or []):
        cell_tag = "th" if r_index == 0 else "td"
        cells = "".join(
            f"<{cell_tag}>{_html.escape(cell)}</{cell_tag}>" for cell in row
        )
        parts.append(f"<tr>{cells}</tr>")
    parts.append("</table>")
    return "".join(parts)


_MD_SPECIALS = ("*", "_", "`", "[", "]", "(", ")", "~", "#", "+", "-", "|")


def _escape_markdown(text: Any) -> str:
    """Escape the markdown specials mdv3 treats as formatting.

    ``<``/``>``/``&`` are deliberately left alone: the markdown dialects
    here never treat them as markup, and escaping them would corrupt
    HTML-looking body text in the native dialect.
    """
    text = str(text)
    for special in _MD_SPECIALS:
        if special in text:
            text = text.replace(special, "\\" + special)
    return text


def _code_html(text: str, lang: str = "") -> str:
    """A fenced ``<pre><code>`` block for the native Rich Message dialect."""
    code = _html.escape(text)
    cls = f' class="language-{_html.escape(lang)}"' if lang else ""
    return f"<pre><code{cls}>{code}</code></pre>"


def _render_expandable_html(block: dict) -> str:
    title = _html.escape(block.get("title", ""))
    body = _html.escape(block.get("body", ""))
    heading = f"<b>{title}</b>\n" if title else ""
    return f"<blockquote expandable>{heading}{body}</blockquote>"


def render_blocks_native_html(
    blocks: list[dict],
    format: str = "html",
) -> tuple[str, list[tuple[str, str, Any]]]:
    """Render *blocks* to the best-effort native Rich Message HTML dialect.

    ``blocks`` must already be a plain list of block dicts  -  run
    :func:`normalize_blocks` first if the caller has a
    :class:`RichBlocks`, or a looser shape (bare strings, dicts missing
    optional keys).

    Returns ``(html, file_specs)`` where ``file_specs`` is
    ``[(kind, file_id, input_photo_or_document), ...]`` for every
    ``media`` block that carried an attachable object  -  the caller turns
    those into ``InputRichFilePhoto``/``InputRichFileDocument`` entries.

    Supports every block type :class:`RichBlocks` knows about (header,
    paragraph, list, code, table, expandable, media); unknown block
    types are skipped rather than raised on, so a forward-compatible
    caller adding a new block kind never breaks a send that also
    contains known blocks.
    """
    _validate_format(format)
    parts: list[str] = []
    file_specs: list[tuple[str, str, Any]] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "header":
            level = max(1, min(6, int(block.get("level", 1))))
            parts.append(f"<h{level}>{_html.escape(block.get('text', ''))}</h{level}>")
        elif kind == "paragraph":
            parts.append(f"<p>{_html.escape(block.get('text', ''))}</p>")
        elif kind == "list":
            tag = "ol" if block.get("ordered") else "ul"
            items = "".join(
                f"<li>{_html.escape(item)}</li>" for item in block.get("items", [])
            )
            parts.append(f"<{tag}>{items}</{tag}>")
        elif kind == "code":
            parts.append(_code_html(block.get("text", ""), block.get("lang", "")))
        elif kind == "table":
            parts.append(_render_table_html(block))
        elif kind == "expandable":
            parts.append(_render_expandable_html(block))
        elif kind == "media":
            obj = block.get("photo") or block.get("doc")
            if obj is not None:
                file_id = f"m{len(file_specs)}"
                file_specs.append(("photo" if block.get("photo") else "doc", file_id, obj))
                parts.append(f'<figure><img data-rich-file="{file_id}"/>')
                if block.get("caption"):
                    parts.append(
                        f"<figcaption>{_html.escape(block['caption'])}</figcaption>"
                    )
                parts.append("</figure>")
            elif block.get("caption"):
                parts.append(f"<p>{_html.escape(block['caption'])}</p>")
    return "".join(parts), file_specs


def render_blocks_fallback_html(blocks: list[dict]) -> str:
    """Render *blocks* to the small, guaranteed-understood HTML dialect
    ``telethon.extensions.html`` (and therefore ``custom.rich.parse_rich``)
    actually turns into message entities: ``<b>``, ``<pre><code>``,
    ``<blockquote expandable>``. Tables/headings have no message-entity
    form, so headers become bold lines and tables become a monospace text
    grid inside a ``<pre>`` block.

    ``blocks`` must already be a plain list of block dicts  -  run
    :func:`normalize_blocks` first for a :class:`RichBlocks` or looser
    input.
    """
    chunks: list[str] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "header":
            chunks.append(f"<b>{_html.escape(block.get('text', ''))}</b>")
        elif kind == "paragraph":
            chunks.append(_html.escape(block.get("text", "")))
        elif kind == "list":
            tag = "ol" if block.get("ordered") else "ul"
            items = "".join(
                f"<li>{_html.escape(item)}</li>" for item in block.get("items", [])
            )
            chunks.append(f"<{tag}>{items}</{tag}>")
        elif kind == "code":
            chunks.append(
                f"<pre><code>{_html.escape(block.get('text', ''))}</code></pre>"
            )
        elif kind == "table":
            grid = _table_to_text(block)
            if grid:
                chunks.append(f"<pre><code>{_html.escape(grid)}</code></pre>")
        elif kind == "expandable":
            chunks.append(_render_expandable_html(block))
        elif kind == "media":
            if block.get("caption"):
                chunks.append(_html.escape(block["caption"]))
    return "\n\n".join(chunk for chunk in chunks if chunk)


def _native_markdown(blocks: list[dict]) -> tuple[str, list]:
    """Render *blocks* in the native Rich Message markdown dialect.

    Returns ``(markdown, files)`` where ``files`` are built
    ``InputRichFilePhoto``/``InputRichFileDocument`` objects referenced from
    the markup through ``attachment:<file_id>`` links.
    """
    parts: list[str] = []
    files: list = []
    for block in blocks:
        kind = block.get("type")
        if kind == "header":
            level = max(1, min(6, int(block.get("level", 1))))
            parts.append(f"{'#' * level} {_escape_markdown(block.get('text', ''))}")
        elif kind == "paragraph":
            parts.append(_escape_markdown(block.get("text", "")))
        elif kind == "list":
            parts.append(
                "\n".join(
                    (
                        f"{index}. {_escape_markdown(item)}"
                        if block.get("ordered")
                        else f"- {_escape_markdown(item)}"
                    )
                    for index, item in enumerate(block.get("items", []), 1)
                )
            )
        elif kind == "code":
            parts.append(f"```{block.get('lang', '')}\n{block.get('text', '')}\n```")
        elif kind == "table":
            rows = block.get("rows") or []
            table = []
            if block.get("caption"):
                table.append(f"**{block['caption']}**")
            if rows:
                table.append("| " + " | ".join(rows[0]) + " |")
                table.append("| " + " | ".join("---" for _ in rows[0]) + " |")
                table.extend("| " + " | ".join(row) + " |" for row in rows[1:])
            parts.append("\n".join(table))
        elif kind == "expandable":
            content = "\n".join(
                filter(None, (block.get("title", ""), block.get("body", "")))
            )
            parts.append(
                "\n".join(
                    f"> {_escape_markdown(line)}" for line in content.splitlines()
                )
            )
        elif kind == "media":
            obj = block.get("photo") or block.get("doc")
            if obj is not None:
                file_id = f"m{len(files)}"
                if block.get("photo") is not None:
                    files.append(_types.InputRichFilePhoto(id=file_id, photo=obj))
                else:
                    files.append(_types.InputRichFileDocument(id=file_id, document=obj))
                parts.append(f"![{block.get('caption') or ''}](attachment:{file_id})")
            elif block.get("caption"):
                parts.append(_escape_markdown(block["caption"]))
    return "\n\n".join(filter(None, parts)), files


def _fallback_markdown(blocks: list[dict]) -> str:
    """Render *blocks* in the small, entity-safe markdown dialect mdv3
    understands: bold headers, fenced code, quoted expandables. Tables have
    no entity form, so they become a monospace grid inside a fence.
    """
    parts: list[str] = []
    for block in blocks:
        kind = block.get("type")
        if kind == "header":
            parts.append(f"**{_escape_markdown(block.get('text', ''))}**")
        elif kind == "paragraph":
            parts.append(_escape_markdown(block.get("text", "")))
        elif kind == "list":
            parts.append(
                "\n".join(
                    (
                        f"{index}. {_escape_markdown(item)}"
                        if block.get("ordered")
                        else f"- {_escape_markdown(item)}"
                    )
                    for index, item in enumerate(block.get("items", []), 1)
                )
            )
        elif kind == "code":
            parts.append(f"```{block.get('lang', '')}\n{block.get('text', '')}\n```")
        elif kind == "table":
            grid = _table_to_text(block)
            if grid:
                parts.append(f"```\n{grid}\n```")
        elif kind == "expandable":
            content = "\n".join(
                filter(None, (block.get("title", ""), block.get("body", "")))
            )
            parts.append(
                "\n".join(
                    f"> {_escape_markdown(line)}" for line in content.splitlines()
                )
            )
        elif kind == "media":
            if block.get("caption"):
                parts.append(_escape_markdown(block["caption"]))
    return "\n\n".join(filter(None, parts))


def legacy_message(
    blocks: Any, format: MessageFormat = "html"
) -> LegacyMessage:
    """Render *blocks* with the always-safe legacy engine.

    ``format`` picks the fallback dialect the markup is rendered in
    (``html``: the small entity-tag dialect; ``markdown``: the bold/fence
    dialect), then :mod:`telethon.extensions.markdownv3` parses that markup
    into real message entities. Never touches the rich TL schema, so it
    works on every build and every account.
    """
    format = _validate_format(format)
    markup = (
        render_blocks_fallback_html(_normalize_blocks(blocks))
        if format == "html"
        else _fallback_markdown(_normalize_blocks(blocks))
    )
    if not markup:
        return LegacyMessage("", [], "", format)
    text, entities = normalize_parsed(_mdv3.parse(markup, mode=format), fallback=markup)
    return LegacyMessage(text, entities, markup, format)


def rich_message_markdown(
    blocks: Any, *, rtl: bool = False, noautolink: bool | None = None
):
    """Build a native ``InputRichMessageMarkdown``."""
    markup, files = _native_markdown(_normalize_blocks(blocks))
    return _types.InputRichMessageMarkdown(
        markdown=markup,
        rtl=bool(rtl) or None,
        noautolink=noautolink,
        files=files or None,
    )


def rich_message(
    blocks: Any,
    *,
    engine: Engine | None = None,
    format: MessageFormat | None = None,
    is_premium: bool | None = None,
    is_bot: bool | None = None,
    rtl: bool = False,
    noautolink: bool | None = None,
) -> Any:
    """Build using the selected engine and format.

    ``auto`` selects the rich engine for Premium users and all bots; ordinary
    users select the legacy mdv3 engine.  Explicit ``rich`` is an expert
    override and always builds the requested native payload.
    """
    engine, format, _, _ = _policy(blocks, engine, format, is_premium, is_bot)
    if engine == "legacy":
        return legacy_message(blocks, format=format)
    if format == "html":
        return rich_message_html(blocks, rtl=rtl, noautolink=noautolink)
    return rich_message_markdown(blocks, rtl=rtl, noautolink=noautolink)


def bot_inline_rich_message(
    blocks: Any,
    *,
    format: MessageFormat = "html",
    is_premium: bool | None = None,
    is_bot: bool | None = None,
    reply_markup: Any = None,
    rtl: bool = False,
    noautolink: bool | None = None,
) -> Any:
    """Wrap a native Rich Message payload for an inline query result.

    Uses the account policy exactly like :func:`rich_message` to pick the
    html or markdown flavour, and carries the caller's ``reply_markup``
    untouched. The pinned fork always has the rich inline schema.
    """
    payload = rich_message(
        blocks,
        format=format,
        is_premium=is_premium,
        is_bot=is_bot,
        rtl=rtl,
        noautolink=noautolink,
    )
    return _types.InputBotInlineMessageRichMessage(
        rich_message=payload, reply_markup=reply_markup
    )


def _fallback_text_entities(block_list: list[dict]) -> tuple[str, list]:
    """Render *block_list* to ``(text, entities)`` the guaranteed way.

    Falls back to an untagged plain-text join (no entities) if
    ``custom.rich`` itself failed to import  -  belt-and-suspenders so this
    module never raises merely because of the fallback path.
    """
    if parse_rich is None:
        return blocks_to_plain_text(block_list), []
    html_str = render_blocks_fallback_html(block_list)
    if not html_str:
        return "", []
    try:
        return parse_rich(html_str, mode="html")
    except Exception:  # pragma: no cover - defensive
        return blocks_to_plain_text(block_list), []


def blocks_to_plain_text(block_list: list[dict]) -> str:
    """Untagged last-resort text summary (no entities, no markup).

    ``block_list`` must already be a plain list of block dicts  -  run
    :func:`normalize_blocks` first for a :class:`RichBlocks` or looser
    input (bare strings, dicts missing optional keys). The single
    plain-text renderer for every block-driven fallback path in this
    codebase, e.g. ``InlineResults.article_rich``'s degraded-``article``
    fallback in ``pyUltroid/_core/inlineresults.py``.
    """
    bits: list[str] = []
    for block in block_list:
        btype = block.get("type")
        if btype in ("header", "paragraph"):
            bits.append(block.get("text", ""))
        elif btype == "list":
            bits.extend(block.get("items", []))
        elif btype == "code":
            bits.append(block.get("text", ""))
        elif btype == "expandable":
            bits.append(block.get("title", ""))
            bits.append(block.get("body", ""))
        elif btype == "table":
            bits.append(_table_to_text(block))
        elif btype == "media" and block.get("caption"):
            bits.append(block["caption"])
    return "\n\n".join(b for b in bits if b)


# ──────────────────────────────────────────────────────────────────────────
# Native rich_message_html
# ──────────────────────────────────────────────────────────────────────────


def rich_message_html(
    blocks: Any, *, rtl: bool = False, noautolink: bool | None = None
):
    """Render *blocks* to a native ``InputRichMessageHTML``.

    ``blocks`` may be a :class:`RichBlocks` instance or a plain iterable of
    block dicts. The pinned Telethon-v2 fork always has the rich schema
    (layer 228), so the type is used directly - no capability guards.
    """
    html_str, file_specs = render_blocks_native_html(normalize_blocks(blocks))
    files = None
    if file_specs:
        built = []
        for kind, file_id, obj in file_specs:
            if kind == "photo":
                built.append(_types.InputRichFilePhoto(id=file_id, photo=obj))
            else:
                built.append(_types.InputRichFileDocument(id=file_id, document=obj))
        files = built or None
    return _types.InputRichMessageHTML(
        html=html_str, rtl=bool(rtl) or None, noautolink=noautolink, files=files
    )


# ──────────────────────────────────────────────────────────────────────────
# send_rich / edit_rich
# ──────────────────────────────────────────────────────────────────────────


def _get_message_id(msg: Any) -> int | None:
    if msg is None:
        return None
    if isinstance(msg, int):
        return msg
    try:
        return _tl_utils.get_message_id(msg)
    except TypeError:
        return getattr(msg, "id", None)


def _build_reply_to(reply_to: Any):
    message_id = _get_message_id(reply_to)
    return _types.InputReplyToMessage(message_id) if message_id is not None else None


def _client_me_flag(client: Any, name: str) -> bool | None:
    value = getattr(getattr(client, "me", None), name, None)
    return value if isinstance(value, bool) else None


def rich_sender(client: Any) -> Any:
    """Preserve legacy assistant-bot routing for callers without policy args."""
    if _client_me_flag(client, "bot") is True:
        return client
    try:
        from .. import asst
    except Exception:
        return client
    return asst if asst is not None and asst is not client else client


async def send_rich(
    client: Any,
    peer: Any,
    blocks: Any,
    *,
    engine: Engine | None = None,
    format: MessageFormat | None = None,
    is_premium: bool | None = None,
    is_bot: bool | None = None,
    reply_to: Any = None,
    buttons: Any = None,
    rtl: bool = False,
    noautolink: bool | None = None,
    silent: bool | None = None,
    background: bool | None = None,
    link_preview: bool = False,
) -> Any:
    """Send *blocks* as a Rich Message, with a guaranteed plain-text fallback.

    Tries the native path first: a raw ``messages.SendMessageRequest(...,
    rich_message=InputRichMessageHTML(...))``  -  never
    ``client.send_message(rich_message=...)`` (the fork's high-level kwarg
    may not be present on every pinned checkout; the raw request is the
    required baseline whenever the TL schema itself has the field). The
    plain-text ``message``/``entities`` rendered from the *same* blocks
    ride along in that same request, so a viewer whose client can't render
    ``RichMessage`` still gets a legible message (risk #2 in the plan).

    If ``InputRichMessageHTML``/``rich_message`` is unavailable on this
    Telethon build, or the raw send itself raises for *any* reason
    (unsupported server, stale pinned commit, ...), this transparently
    falls back to ``client.send_message`` with that same rendered
    text/entities  -  the public API is identical either way.
    """
    block_list = normalize_blocks(blocks)
    text, entities = _fallback_text_entities(block_list)
    text = text or " "
    # legacy still interfering
    builder_policy = isinstance(blocks, RichBlocks) and any(
        value is not None for value in (blocks.is_bot, blocks.is_premium)
    )
    builder_engine = isinstance(blocks, RichBlocks) and blocks.engine != "auto"
    legacy_compat = (
        engine is None
        and is_bot is None
        and is_premium is None
        and not builder_policy
        and not builder_engine
    )
    if legacy_compat:
        client = rich_sender(client)
    resolved_bot = _client_me_flag(client, "bot") if is_bot is None else is_bot

    resolved_premium = (
        _client_me_flag(client, "premium") if is_premium is None else is_premium
    )

    selected, format, _, _ = _policy(
        blocks, engine, format, resolved_premium, resolved_bot
    )
    if selected == "legacy":
        return await client.send_message(
            peer,
            text,
            formatting_entities=entities or None,
            parse_mode=None,
            reply_to=reply_to,
            buttons=buttons,
            silent=silent,
            background=background,
            link_preview=link_preview,
        )
    # native
    native = rich_message(
        block_list,
        engine="rich",
        format=format,
        is_premium=resolved_premium,
        is_bot=resolved_bot,
        rtl=rtl,
        noautolink=noautolink,
    )
    input_peer = await client.get_input_entity(peer)
    request = _functions.messages.SendMessageRequest(
        peer=input_peer,
        message=text,
        entities=entities or None,
        no_webpage=not link_preview,
        silent=silent,
        background=background,
        reply_to=_build_reply_to(reply_to),
        reply_markup=client.build_reply_markup(buttons),
        rich_message=native,
    )
    try:
        result = await client(request)
    except RPCError:
        return await client.send_message(
            peer,
            text,
            formatting_entities=entities or None,
            parse_mode=None,
            reply_to=reply_to,
            buttons=buttons,
            silent=silent,
            background=background,
            link_preview=link_preview,
        )
    getter = getattr(client, "_get_response_message", None)
    if getter is not None:
        try:
            return getter(request, result, input_peer)
        except (TypeError, ValueError):
            pass
    return result


async def edit_rich(
    client: Any,
    peer: Any,
    msg_id: Any,
    blocks: Any,
    *,
    engine: Engine | None = None,
    format: MessageFormat | None = None,
    is_premium: bool | None = None,
    is_bot: bool | None = None,
    buttons: Any = None,
    rtl: bool = False,
    noautolink: bool | None = None,
    link_preview: bool = False,
) -> Any:
    """Edit message *msg_id* in *peer* to render *blocks* as a Rich Message.

    Same native-then-plain-text-fallback contract as :func:`send_rich`, via
    a raw ``messages.EditMessageRequest(..., rich_message=...)``.
    """
    block_list = normalize_blocks(blocks)
    text, entities = _fallback_text_entities(block_list)
    text = text or " "
    mid = _get_message_id(msg_id)
    if mid is None:
        raise ValueError(f"edit_rich: could not resolve a message id from {msg_id!r}")
    resolved_bot = _client_me_flag(client, "bot") if is_bot is None else is_bot
    resolved_premium = (
        _client_me_flag(client, "premium") if is_premium is None else is_premium
    )
    selected, format, _, _ = _policy(
        blocks, engine, format, resolved_premium, resolved_bot
    )
    block_list = _normalize_blocks(blocks)
    fallback = legacy_message(block_list, format=format)
    text = fallback.text or " "
    if selected == "legacy":
        return await client.edit_message(
            peer,
            mid,
            text,
            formatting_entities=entities or None,
            parse_mode=None,
            buttons=buttons,
            link_preview=link_preview,
        )

    native = rich_message(
        block_list,
        engine="rich",
        format=format,
        is_premium=resolved_premium,
        is_bot=resolved_bot,
        rtl=rtl,
        noautolink=noautolink,
    )
    input_peer = await client.get_input_entity(peer)
    request = _functions.messages.EditMessageRequest(
        peer=input_peer,
        id=mid,
        message=text,
        entities=entities or None,
        no_webpage=not link_preview,
        reply_markup=client.build_reply_markup(buttons),
        rich_message=native,
    )
    try:
        result = await client(request)
    except RPCError:
        return await client.edit_message(
            peer,
            mid,
            text,
            formatting_entities=entities or None,
            parse_mode=None,
            buttons=buttons,
            link_preview=link_preview,
        )
    getter = getattr(client, "_get_response_message", None)
    if getter is not None:
        try:
            return getter(request, result, input_peer)
        except (TypeError, ValueError):
            pass
    return result

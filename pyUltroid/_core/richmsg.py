# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Native Telegram RichMessage engine for Telethon TL layer 230.

Messages are sent and edited with the raw ``messages.*`` requests and the
layer-230 ``InputRichMessage`` constructors. There is deliberately no
legacy/entity downgrade or account-based engine selection.
"""

from __future__ import annotations

import copy
import html as _html
import re
from collections.abc import Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Literal

from telethon import utils as _tl_utils
from telethon.extensions import html as _telethon_html
from telethon.tl import functions as _functions
from telethon.tl import types as _types

from custom.entities import normalize_parsed, parse_rich

from .richdebug import call_rich_request

MessageFormat = Literal["html", "markdown"]

# The Rich Message guide documents these limits; apply them conservatively to
# the Layer-230 constructors as well, without implying server-side confirmation.
MAX_RICH_MESSAGE_CHARS = 32_768
MAX_RICH_MESSAGE_BLOCKS = 500
MAX_RICH_NESTING_DEPTH = 16
MAX_RICH_TABLE_COLUMNS = 20
_RICH_MEDIA_UNSUPPORTED = (
    "RichMessage photo/document references are unsupported: Telegram rejected "
    "the tested InputRichFilePhoto/InputRichFileDocument payloads, and no "
    "accepted native MTProto reference format has been verified"
)

__all__ = (
    "RichBlocks",
    "RichFile",
    "RichMarkup",
    "blocks_to_plain_text",
    "bot_inline_rich_message",
    "client_can_send_rich_message",
    "coerce_to_rich_markup",
    "edit_inline_plain",
    "edit_rich",
    "normalize_blocks",
    "render_blocks_native_html",
    "rich_markup_from_blocks",
    "rich_message",
    "rich_message_blocks",
    "rich_message_html",
    "rich_message_markdown",
    "send_rich",
    "table_to_text",
)


async def client_can_send_rich_message(client: Any) -> bool:
    """Return whether a Telegram sender is a bot or an identified Premium user.

    Unknown account state is treated as ineligible so callers can deliberately
    choose plain text before issuing a native RichMessage request.
    """
    if client is None:
        return False
    route = getattr(client, "route", None)
    if callable(route):
        try:
            routed = route()
            if routed is not None:
                client = routed
        except Exception:  # noqa: BLE001 - unknown route state fails closed
            pass
    if getattr(client, "_bot", False):
        return True

    identity = None
    for name in ("me", "_me", "user"):
        identity = getattr(client, name, None)
        if identity is not None:
            break
    if identity is None:
        get_me = getattr(client, "get_me", None)
        if callable(get_me):
            try:
                identity = await get_me()
            except Exception:  # noqa: BLE001 - inability to verify fails closed
                return False
    return bool(
        identity
        and (
            getattr(identity, "bot", False)
            or getattr(identity, "premium", False)
        )
    )


@dataclass(frozen=True, slots=True)
class RichMarkup:
    """Already-formatted Telegram Rich Message content.

    Unlike :class:`RichBlocks`, this preserves caller-supplied HTML or
    Markdown. Photo/document file references are retained as input data but
    rejected by the payload builder until an accepted MTProto format is verified.
    """

    text: str
    format: MessageFormat = "html"
    files: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("RichMarkup.text must be str")
        object.__setattr__(self, "format", _validate_format(self.format))
        if not isinstance(self.files, (list, tuple)):
            raise TypeError("RichMarkup.files must be a sequence")
        object.__setattr__(self, "files", tuple(self.files))


@dataclass(frozen=True, slots=True)
class RichFile:
    """Describe a rich-media reference, currently rejected by the engine."""

    id: str
    media: Any
    kind: Literal["photo", "document"] = "photo"

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", self.id):
            raise ValueError("RichFile.id must be 1-64 ASCII letters, digits, '_' or '-'")
        if self.kind not in {"photo", "document"}:
            raise ValueError("RichFile.kind must be 'photo' or 'document'")


_HTML_INPUT_RE = re.compile(
    r"(?is)</?\s*(?:b|strong|i|em|u|s|del|ins|a|code|pre|blockquote|"
    r"span|tg-spoiler|tg-emoji|tg-document|figure|figcaption|img|br|hr|p|div|h[1-6]|ul|ol|li|table|"
    r"thead|tbody|tfoot|tr|th|td)\b[^>]*>"
)


def coerce_to_rich_markup(
    value: Any,
    *,
    parse_mode: Any = None,
    formatting_entities: Any = None,
) -> RichMarkup | None:
    """Convert ordinary Telethon text input to safe, canonical HTML markup.

    Returns ``None`` for non-string inputs or unsupported/malformed parser
    contracts so the caller can preserve Telethon's existing code path. An
    explicit ``parse_mode=None`` means literal plain text; a recognized HTML
    body is auto-detected only when a parse mode (normally the project's
    MarkdownV2 default) would otherwise misread it.
    """
    if isinstance(value, RichMarkup):
        return value
    if not isinstance(value, str):
        return None

    if formatting_entities is not None:
        try:
            plain, entities = normalize_parsed(
                (value, formatting_entities), fallback=value
            )
            return RichMarkup(_telethon_html.unparse(plain, entities or []), "html")
        except Exception:  # noqa: BLE001 - malformed entity input stays on normal Telethon path
            return None

    if parse_mode is None:
        return RichMarkup(_html.escape(value, quote=False), "html")

    mode_name = str(parse_mode).strip().lower() if isinstance(parse_mode, str) else ""
    if mode_name in {"html", "htm"} or parse_mode is _telethon_html:
        return RichMarkup(value, "html")

    # fmtmsg() intentionally emits safe HTML, while most legacy clients default
    # to MarkdownV2. Preserve that HTML instead of passing it to the wrong parser.
    probe = re.sub(r"```[\s\S]*?```|`[^`]*`", "", value)
    if _HTML_INPUT_RE.search(probe):
        return RichMarkup(value, "html")

    try:
        plain, entities = normalize_parsed(parse_rich(value, parse_mode), fallback=value)
        markup = _telethon_html.unparse(plain, entities or [])
        return RichMarkup(markup, "html")
    except Exception:  # noqa: BLE001 - parser rejection stays on normal Telethon path
        return None


_RICH_TAGS = frozenset(
    {
        "a", "b", "strong", "i", "em", "u", "ins", "s", "strike", "del",
        "span", "tg-spoiler", "tg-emoji", "blockquote", "p", "div", "br",
        "hr", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
        "pre", "code", "table", "caption", "thead", "tbody", "tfoot",
        "tr", "th", "td", "sub", "sup", "figure", "figcaption", "img",
        "tg-document",
    }
)
_RICH_VOID_TAGS = frozenset({"br", "hr", "img"})
_RICH_DROP_CONTENT = frozenset(
    {"script", "style", "iframe", "object", "svg", "math", "template", "head", "title"}
)


class _RichHTMLSanitizer(HTMLParser):
    """Allow only Telegram rich-text tags and narrowly validated attributes."""

    def __init__(self, file_ids: set[str] | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.stack: list[str] = []
        self.dropped: list[str] = []
        self.file_ids = file_ids or set()

    @staticmethod
    def _safe_href(value: str) -> bool:
        if value.startswith("#"):
            return True
        from urllib.parse import urlsplit

        return urlsplit(value).scheme.lower() in {"http", "https", "tg", "mailto"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self.dropped:
            if tag in _RICH_DROP_CONTENT:
                self.dropped.append(tag)
            return
        if tag in _RICH_DROP_CONTENT:
            self.dropped.append(tag)
            return
        if tag in {"html", "body"} or tag not in _RICH_TAGS:
            return

        values = {
            key.lower(): "" if value is None else value for key, value in attrs
        }
        safe_attrs: list[tuple[str, str]] = []
        if tag == "a" and values.get("href") and self._safe_href(values["href"]):
            safe_attrs.append(("href", values["href"]))
        if tag in {"img", "tg-document"} and values.get("src") in self.file_ids:
            safe_attrs.append(("src", values["src"]))
        if tag == "img" and values.get("alt"):
            safe_attrs.append(("alt", values["alt"][:256]))
        if tag == "img" and "tg-spoiler" in values:
            safe_attrs.append(("tg-spoiler", ""))
        if tag == "blockquote" and "expandable" in values:
            safe_attrs.append(("expandable", ""))
        if tag == "code":
            match = re.fullmatch(r"language-([A-Za-z0-9_+-]{1,32})", values.get("class", ""))
            if match:
                safe_attrs.append(("class", f"language-{match.group(1)}"))
        if tag == "table":
            classes = [
                value for value in values.get("class", "").split()
                if value in {"bordered", "striped", "compact"}
            ]
            if classes:
                safe_attrs.append(("class", " ".join(classes)))
        if tag == "span" and values.get("class") == "tg-spoiler":
            safe_attrs.append(("class", "tg-spoiler"))
        if tag == "tg-emoji" and values.get("emoji-id", "").isdigit():
            safe_attrs.append(("emoji-id", values["emoji-id"]))

        attr_text = "".join(
            f" {name}"
            if name in {"expandable", "tg-spoiler"} and value == ""
            else f' {name}="{_html.escape(value, quote=True)}"'
            for name, value in safe_attrs
        )
        self.parts.append(f"<{tag}{attr_text}>")
        if tag not in _RICH_VOID_TAGS:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.dropped:
            if tag == self.dropped[-1]:
                self.dropped.pop()
            return
        if tag in {"html", "body"} or tag in _RICH_VOID_TAGS or tag not in self.stack:
            return
        while self.stack:
            current = self.stack.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self.dropped:
            clean = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", data)
            self.parts.append(_html.escape(clean, quote=False))

    def finish(self) -> str:
        self.close()
        while self.stack:
            self.parts.append(f"</{self.stack.pop()}>")
        return "".join(self.parts)


def sanitize_rich_html(text: str, *, file_ids: set[str] | None = None) -> str:
    """Escape text and retain only safe Telegram rich HTML elements."""
    if not isinstance(text, str):
        raise TypeError("rich HTML must be str")
    parser = _RichHTMLSanitizer(file_ids=file_ids)
    parser.feed(text)
    return parser.finish()


class _RichFileReferenceParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.references: list[str | None] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"img", "tg-document"}:
            self.references.append(dict(attrs).get("src"))


def _has_rich_media_reference(text: str, format: MessageFormat) -> bool:
    if format == "html":
        parser = _RichFileReferenceParser()
        parser.feed(text)
        return bool(parser.references)
    source = re.sub(r"```[\s\S]*?```|`[^`]*`", "", text)
    return bool(re.search(r"!\[[^\]]*\]\(\s*<?[^\s)>]+", source))


def _reject_rich_media(
    text: str, format: MessageFormat, files: Sequence[Any] | None = None
) -> None:
    if files or _has_rich_media_reference(text, format):
        raise ValueError(_RICH_MEDIA_UNSUPPORTED)


class RichBlocks:
    """Accumulate format-neutral blocks for native layer-230 RichMessages."""

    __slots__ = ("_blocks", "format", "native")

    def __init__(self, *, format: MessageFormat = "html", native: bool = False) -> None:
        self.format = _validate_format(format)
        self.native = bool(native)
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

    def paragraph(
        self, text: str, *, italic: bool = False, bold: bool = False
    ) -> RichBlocks:
        block = {"type": "paragraph", "text": str(text)}
        if italic:
            block["italic"] = True
        if bold:
            block["bold"] = True
        self._blocks.append(block)
        return self

    def divider(self) -> RichBlocks:
        self._blocks.append({"type": "divider"})
        return self

    def anchor(self, name: str) -> RichBlocks:
        if not self.native:
            raise ValueError("anchor blocks require RichBlocks(native=True)")
        self._blocks.append({"type": "anchor", "name": str(name)})
        return self

    def footer(self, text: str) -> RichBlocks:
        self._blocks.append({"type": "footer", "text": str(text)})
        return self

    def subtitle(self, text: str) -> RichBlocks:
        self._blocks.append({"type": "subtitle", "text": str(text)})
        return self

    def list(
        self,
        items: Sequence[Any],
        ordered: bool = False,
        *,
        checkbox: bool = False,
        checked: Sequence[bool | None] | None = None,
    ) -> RichBlocks:
        """Append list items, optionally with native checkbox states."""
        items = list(items)
        if (checkbox or checked is not None) and not self.native:
            raise ValueError("checkbox lists require RichBlocks(native=True)")
        checked_values = None if checked is None else list(checked)
        if checked_values is not None and len(checked_values) != len(items):
            raise ValueError("checked must provide one value per list item")
        if checked_values is not None and any(
            value is not None and not isinstance(value, bool)
            for value in checked_values
        ):
            raise TypeError("checked list values must be bool or None")
        self._blocks.append(
            {
                "type": "list",
                "items": [
                    item if isinstance(item, RichBlocks) else str(item)
                    for item in items
                ],
                "ordered": bool(ordered),
                "checkbox": bool(checkbox or checked_values is not None),
                "checked": checked_values,
            }
        )
        return self

    def code(self, text: str, lang: str = "") -> RichBlocks:
        self._blocks.append(
            {"type": "code", "text": str(text), "lang": str(lang or "")}
        )
        return self

    def math(
        self,
        source: str,
        *,
        display: bool = True,
        before: str = "",
        after: str = "",
    ) -> RichBlocks:
        if not self.native:
            raise ValueError("math blocks require RichBlocks(native=True)")
        if display and (before or after):
            raise ValueError("display math cannot have inline surrounding text")
        self._blocks.append(
            {
                "type": "math",
                "source": str(source),
                "display": bool(display),
                "before": str(before),
                "after": str(after),
            }
        )
        return self

    def quote(
        self, text: str, citation: str = "", *, collapsed: bool | None = None
    ) -> RichBlocks:
        block = {"type": "quote", "text": str(text), "citation": str(citation)}
        if collapsed is not None:
            block["collapsed"] = bool(collapsed)
        self._blocks.append(block)
        return self

    def pullquote(self, text: str, citation: str = "") -> RichBlocks:
        self._blocks.append(
            {"type": "pullquote", "text": str(text), "citation": str(citation)}
        )
        return self

    def quote_blocks(
        self, body: str | RichBlocks, citation: str = ""
    ) -> RichBlocks:
        children = (
            body.to_list()
            if isinstance(body, RichBlocks)
            else [{"type": "paragraph", "text": str(body)}]
        )
        if any(child.get("type") == "media" for child in children):
            raise ValueError("quote blocks cannot contain media blocks")
        self._blocks.append(
            {"type": "quote_blocks", "blocks": children, "citation": str(citation)}
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
        alignments: Sequence[str] | None = None,
    ) -> RichBlocks:
        normalized_alignments = None
        if alignments is not None:
            normalized_alignments = [str(value).lower() for value in alignments]
            if any(value not in {"left", "center", "right"} for value in normalized_alignments):
                raise ValueError("table alignments must be left, center, or right")
            if len(normalized_alignments) > MAX_RICH_TABLE_COLUMNS:
                raise ValueError(
                    f"RichMessage tables cannot exceed {MAX_RICH_TABLE_COLUMNS} columns"
                )
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
                "alignments": normalized_alignments,
            }
        )
        return self

    def expandable(
        self, title: str, body: str | RichBlocks, *, open: bool = False
    ) -> RichBlocks:
        block = {"type": "expandable", "title": str(title)}
        if open:
            block["open"] = True
        if isinstance(body, RichBlocks):
            if any(child.get("type") == "media" for child in body):
                raise ValueError("expandable details cannot contain media blocks")
            block["blocks"] = body.to_list()
        else:
            block["body"] = str(body)
        self._blocks.append(block)
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
        format: MessageFormat | None = None,
        rtl: bool = False,
        noautolink: bool | None = None,
    ) -> Any:
        """Build the native layer-230 input RichMessage."""
        return rich_message(
            self,
            format=format,
            rtl=rtl,
            noautolink=noautolink,
        )


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
    if isinstance(blocks, RichMarkup):
        return [{"type": "paragraph", "text": blocks.text}]
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


def table_to_text(rows: Sequence[Sequence[Any]], caption: str | None = None) -> str:
    """Render *rows* (first row = header) as a plain monospace text grid.

    The canonical aligned-text table renderer: cells are ``str()``-coerced,
    columns padded to the widest cell, a ``-+-`` rule follows the header
    row. Public so other table views (e.g. ``plugins/stars.py``,
    ``pyUltroid/universal/adapters/managed_bots.py``) reuse it instead of
    re-implementing the alignment."""
    str_rows = [[str(cell) for cell in row] for row in (rows or [])]
    if not str_rows:
        return str(caption or "")
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
    alignments = block.get("alignments") or []
    if any(value in {"center", "right"} for value in alignments):
        raise ValueError(
            "center/right table alignment requires native PageBlock or Markdown output"
        )
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
    children = block.get("blocks")
    if isinstance(children, list):
        body, _ = render_blocks_native_html(children, format="html")
    else:
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

    Returns ``(html, file_specs)`` where ``file_specs`` describes media blocks.
    RichMessage payload construction rejects these unverified references.

    Supports markup-compatible :class:`RichBlocks` (header, paragraph, list,
    code, table, expandable, media). Unknown block types are skipped, preserving
    the renderer's existing forward-compatible contract.
    """
    _validate_format(format)
    parts: list[str] = []
    file_specs: list[tuple[str, str, Any]] = []
    for block in blocks:
        kind = block.get("type")
        if kind in {"header", "subtitle"}:
            parts.append(f"<p><b>{_html.escape(block.get('text', ''))}</b></p>")
        elif kind == "paragraph":
            text = _html.escape(block.get("text", ""))
            if block.get("bold"):
                text = f"<b>{text}</b>"
            if block.get("italic"):
                text = f"<i>{text}</i>"
            parts.append(f"<p>{text}</p>")
        elif kind == "divider":
            parts.append("<hr>")
        elif kind == "anchor":
            raise ValueError("anchor blocks require native PageBlockAnchor output")
        elif kind == "footer":
            parts.append(f"<p><i>{_html.escape(block.get('text', ''))}</i></p>")
        elif kind == "quote":
            citation = block.get("citation", "")
            body = _html.escape(block.get("text", ""))
            if citation:
                body += f"<br><i>{_html.escape(citation)}</i>"
            parts.append(f"<blockquote>{body}</blockquote>")
        elif kind == "pullquote":
            citation = block.get("citation", "")
            body = f"<b>{_html.escape(block.get('text', ''))}</b>"
            if citation:
                body += f"<br><i>{_html.escape(citation)}</i>"
            parts.append(f"<blockquote>{body}</blockquote>")
        elif kind == "quote_blocks":
            body, nested_files = render_blocks_native_html(
                block.get("blocks", []), format=format
            )
            if nested_files:
                raise ValueError("quote blocks cannot contain media")
            citation = block.get("citation", "")
            if citation:
                body += f"<br><i>{_html.escape(citation)}</i>"
            parts.append(f"<blockquote>{body}</blockquote>")
        elif kind == "math":
            raise ValueError("math blocks require native PageBlockMath/TextMath output")
        elif kind == "list":
            tag = "ol" if block.get("ordered") else "ul"
            items = []
            checked = block.get("checked") or []
            for index, item in enumerate(block.get("items", [])):
                marker = ""
                if block.get("checkbox"):
                    value = checked[index] if index < len(checked) else None
                    marker = "☑ " if value is True else "☐ "
                if isinstance(item, RichBlocks):
                    nested, nested_files = render_blocks_native_html(
                        item.to_list(), format=format
                    )
                    if nested_files:
                        raise ValueError("media blocks are not supported inside list items")
                    items.append(f"<li>{marker}{nested}</li>")
                else:
                    items.append(f"<li>{marker}{_html.escape(str(item))}</li>")
            parts.append(f"<{tag}>{''.join(items)}</{tag}>")
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
                photo = block.get("photo") is not None
                file_specs.append(("photo" if photo else "document", file_id, obj))
                if photo:
                    alt = _html.escape(block.get("caption") or "", quote=True)
                    parts.append(f'<figure><img src="{file_id}" alt="{alt}"/>')
                else:
                    parts.append(f'<figure><tg-document src="{file_id}"></tg-document>')
                if block.get("caption"):
                    parts.append(
                        f"<figcaption>{_html.escape(block['caption'])}</figcaption>"
                    )
                parts.append("</figure>")
            elif block.get("caption"):
                parts.append(f"<p>{_html.escape(block['caption'])}</p>")
    return "".join(parts), file_specs


def _native_markdown(blocks: list[dict]) -> tuple[str, list[RichFile]]:
    """Render *blocks* in the native Rich Message markdown dialect.

    Returns ``(markdown, files)`` where Markdown image links use stable local
    IDs. The payload builder rejects this media path until Telegram accepts a
    native MTProto reference format.
    """
    parts: list[str] = []
    files: list = []
    for block in blocks:
        kind = block.get("type")
        if kind in {"header", "subtitle"}:
            parts.append(f"**{_escape_markdown(block.get('text', ''))}**")
        elif kind == "paragraph":
            text = _escape_markdown(block.get("text", ""))
            if block.get("bold"):
                text = f"**{text}**"
            if block.get("italic"):
                text = f"*{text}*"
            parts.append(text)
        elif kind == "divider":
            parts.append("---")
        elif kind == "anchor":
            raise ValueError("anchor blocks require native PageBlockAnchor output")
        elif kind == "footer":
            parts.append(f"*{_escape_markdown(block.get('text', ''))}*")
        elif kind in {"quote", "pullquote"}:
            text = _escape_markdown(block.get("text", ""))
            if kind == "pullquote":
                text = f"**{text}**"
            lines = [f"> {line}" for line in text.splitlines()]
            citation = block.get("citation", "")
            if citation:
                lines.extend(
                    f"> *{_escape_markdown(line)}*"
                    for line in citation.splitlines()
                )
            parts.append("\n".join(lines))
        elif kind == "quote_blocks":
            body, nested_files = _native_markdown(block.get("blocks", []))
            if nested_files:
                raise ValueError("quote blocks cannot contain media")
            lines = [f"> {line}" for line in body.splitlines()]
            citation = block.get("citation", "")
            if citation:
                lines.extend(
                    f"> *{_escape_markdown(line)}*"
                    for line in citation.splitlines()
                )
            parts.append("\n".join(lines))
        elif kind == "math":
            raise ValueError("math blocks require native PageBlockMath/TextMath output")
        elif kind == "list":
            lines = []
            checked = block.get("checked") or []
            for index, item in enumerate(block.get("items", []), 1):
                prefix = f"{index}. " if block.get("ordered") else "- "
                checked_index = index - 1
                if block.get("checkbox"):
                    value = checked[checked_index] if checked_index < len(checked) else None
                    prefix += "☑ " if value is True else "☐ "
                if isinstance(item, RichBlocks):
                    nested, nested_files = _native_markdown(item.to_list())
                    if nested_files:
                        raise ValueError("media blocks are not supported inside list items")
                    nested_lines = nested.splitlines()
                    lines.append(
                        "\n".join(
                            [prefix + nested_lines[0]]
                            + [f"  {line}" for line in nested_lines[1:]]
                        )
                        if nested_lines
                        else prefix.rstrip()
                    )
                else:
                    lines.append(prefix + _escape_markdown(item))
            parts.append("\n".join(lines))
        elif kind == "code":
            parts.append(f"```{block.get('lang', '')}\n{block.get('text', '')}\n```")
        elif kind == "table":
            rows = block.get("rows") or []
            table = []
            if block.get("caption"):
                table.append(f"**{block['caption']}**")
            if rows:
                table.append("| " + " | ".join(rows[0]) + " |")
                alignments = block.get("alignments") or []
                separators = []
                for index in range(len(rows[0])):
                    alignment = alignments[index] if index < len(alignments) else None
                    separators.append(
                        {"left": ":---", "center": ":---:", "right": "---:"}.get(
                            alignment, "---"
                        )
                    )
                table.append("| " + " | ".join(separators) + " |")
                table.extend("| " + " | ".join(row) + " |" for row in rows[1:])
            parts.append("\n".join(table))
        elif kind == "expandable":
            children = block.get("blocks")
            if isinstance(children, list):
                body, nested_files = _native_markdown(children)
                files.extend(nested_files)
            else:
                body = block.get("body", "")
            content = "\n".join(filter(None, (block.get("title", ""), body)))
            parts.append(
                "\n".join(
                    f"> {_escape_markdown(line)}" for line in content.splitlines()
                )
            )
        elif kind == "media":
            obj = block.get("photo") or block.get("doc")
            if obj is not None:
                file_id = f"m{len(files)}"
                kind = "photo" if block.get("photo") is not None else "document"
                files.append(RichFile(id=file_id, media=obj, kind=kind))
                parts.append(f"![{block.get('caption') or ''}]({file_id})")
            elif block.get("caption"):
                parts.append(_escape_markdown(block["caption"]))
    return "\n\n".join(filter(None, parts)), files


def rich_markup_from_blocks(
    blocks: Any, *, format: MessageFormat | None = None
) -> RichMarkup:
    """Render the structured block model into native HTML or Markdown."""
    if isinstance(blocks, RichMarkup):
        chosen = blocks.format if format is None else _validate_format(format)
        if chosen != blocks.format:
            raise ValueError("format must match the RichMarkup format")
        return blocks
    chosen = _validate_format(format or getattr(blocks, "format", "html"))
    if isinstance(blocks, str):
        return RichMarkup(blocks, chosen)
    normalized = normalize_blocks(blocks)
    if chosen == "html":
        markup, specs = render_blocks_native_html(normalized)
        files = tuple(
            RichFile(id=file_id, media=media, kind="photo" if kind == "photo" else "document")
            for kind, file_id, media in specs
        )
    else:
        markup, files = _native_markdown(normalized)
        files = tuple(files)
    return RichMarkup(markup, chosen, files)


def rich_message_html(
    blocks: Any,
    *,
    files: Sequence[Any] | None = None,
    rtl: bool = False,
    noautolink: bool | None = None,
):
    """Build a text-only native HTML RichMessage; media references are rejected."""
    markup = rich_markup_from_blocks(blocks, format="html")
    all_files = tuple(markup.files) + tuple(files or ())
    _reject_rich_media(markup.text, "html", all_files)
    if len(markup.text) > MAX_RICH_MESSAGE_CHARS:
        raise ValueError(
            f"RichMessages cannot exceed {MAX_RICH_MESSAGE_CHARS} characters"
        )
    html_text = sanitize_rich_html(markup.text)
    return _types.InputRichMessageHTML(
        html=html_text,
        rtl=bool(rtl) or None,
        noautolink=noautolink,
        files=None,
    )


def rich_message_markdown(
    blocks: Any,
    *,
    files: Sequence[Any] | None = None,
    rtl: bool = False,
    noautolink: bool | None = None,
):
    """Build a text-only native Markdown RichMessage; media references are rejected."""
    markup = rich_markup_from_blocks(blocks, format="markdown")
    all_files = tuple(markup.files) + tuple(files or ())
    _reject_rich_media(markup.text, "markdown", all_files)
    if len(markup.text) > MAX_RICH_MESSAGE_CHARS:
        raise ValueError(
            f"RichMessages cannot exceed {MAX_RICH_MESSAGE_CHARS} characters"
        )
    return _types.InputRichMessageMarkdown(
        markdown=markup.text,
        rtl=bool(rtl) or None,
        noautolink=noautolink,
        files=None,
    )


def _validate_native_page_blocks(blocks: Sequence[Any]) -> None:
    """Apply RichMessage guide limits to typed Layer-230 PageBlock trees."""
    count = 0

    def add_blocks(amount: int = 1) -> None:
        nonlocal count
        count += amount
        if count > MAX_RICH_MESSAGE_BLOCKS:
            raise ValueError(
                f"RichMessages cannot exceed {MAX_RICH_MESSAGE_BLOCKS} blocks"
            )

    def visit(nodes: Sequence[Any], depth: int = 0) -> None:
        if depth > MAX_RICH_NESTING_DEPTH:
            raise ValueError(
                f"RichMessages cannot exceed {MAX_RICH_NESTING_DEPTH} nested levels"
            )
        for node in nodes:
            name = type(node).__name__
            if name.startswith("PageBlock"):
                add_blocks()
            if name == "PageBlockTable":
                for row in getattr(node, "rows", ()) or ():
                    cells = getattr(row, "cells", ()) or ()
                    if len(cells) > MAX_RICH_TABLE_COLUMNS:
                        raise ValueError(
                            f"RichMessage tables cannot exceed {MAX_RICH_TABLE_COLUMNS} columns"
                        )
                    add_blocks()
            elif name in {"PageBlockList", "PageBlockOrderedList"}:
                for item in getattr(node, "items", ()) or ():
                    add_blocks()
                    child_blocks = getattr(item, "blocks", None)
                    if child_blocks:
                        visit(child_blocks, depth + 1)
            else:
                child_blocks = getattr(node, "blocks", None)
                if child_blocks:
                    visit(child_blocks, depth + 1)

    visit(blocks)

    characters = 0
    stack = list(blocks)
    while stack:
        node = stack.pop()
        if not isinstance(node, _types.TLObject):
            continue
        name = type(node).__name__
        if name == "TextPlain":
            characters += len(str(getattr(node, "text", "")))
        elif name in {"TextMath", "PageBlockMath"}:
            characters += len(str(getattr(node, "source", "")))
        if characters > MAX_RICH_MESSAGE_CHARS:
            raise ValueError(
                f"RichMessages cannot exceed {MAX_RICH_MESSAGE_CHARS} characters"
            )
        for value in vars(node).values():
            if isinstance(value, _types.TLObject):
                stack.append(value)
            elif isinstance(value, (list, tuple)):
                stack.extend(value)


_UNSUPPORTED_SEMANTIC_PAGE_BLOCKS = frozenset(
    {"PageBlockTitle", "PageBlockHeader", "PageBlockSubtitle"}
)
_UNVERIFIED_MEDIA_PAGE_BLOCKS = frozenset(
    {
        "PageBlockPhoto",
        "PageBlockDocument",
        "PageBlockCover",
        "PageBlockCollage",
        "PageBlockSlideshow",
    }
)


def _normalize_tl_page_block(node: Any) -> Any:
    name = type(node).__name__
    if name in _UNSUPPORTED_SEMANTIC_PAGE_BLOCKS:
        text = getattr(node, "text", _types.TextEmpty())
        if not isinstance(text, _types.TextBold):
            text = _types.TextBold(text=text)
        return _types.PageBlockParagraph(text=text)
    if not isinstance(node, _types.TLObject):
        return node

    clone = None
    for field in ("blocks", "items"):
        value = getattr(node, field, None)
        if isinstance(value, (list, tuple)):
            normalized = [_normalize_tl_page_block(item) for item in value]
            if clone is None:
                clone = copy.copy(node)
            setattr(
                clone,
                field,
                tuple(normalized) if isinstance(value, tuple) else normalized,
            )
    return clone if clone is not None else node


def _normalize_tl_page_blocks(blocks: Sequence[Any]) -> list[Any]:
    return [_normalize_tl_page_block(block) for block in blocks]


def _contains_unverified_page_media(value: Any) -> bool:
    stack = [value]
    while stack:
        node = stack.pop()
        if type(node).__name__ in _UNVERIFIED_MEDIA_PAGE_BLOCKS:
            return True
        if isinstance(node, _types.TLObject):
            for child in vars(node).values():
                if isinstance(child, _types.TLObject):
                    stack.append(child)
                elif isinstance(child, (list, tuple)):
                    stack.extend(child)
    return False


def _reject_unverified_native_media(message: Any) -> None:
    if isinstance(message, (_types.InputRichMessageHTML, _types.InputRichMessageMarkdown)):
        format = "html" if isinstance(message, _types.InputRichMessageHTML) else "markdown"
        text = message.html if format == "html" else message.markdown
        _reject_rich_media(text, format, message.files)
    elif isinstance(message, _types.InputRichMessage):
        if message.photos or message.documents or _contains_unverified_page_media(message.blocks):
            raise ValueError(_RICH_MEDIA_UNSUPPORTED)


def rich_message_blocks(
    blocks: Sequence[Any],
    *,
    photos: Sequence[Any] | None = None,
    documents: Sequence[Any] | None = None,
    users: Sequence[Any] | None = None,
    rtl: bool = False,
    noautolink: bool | None = None,
):
    """Construct layer-230 ``InputRichMessage`` from native ``PageBlock``s."""
    photos = list(photos) if photos is not None else None
    documents = list(documents) if documents is not None else None
    if photos or documents or _contains_unverified_page_media(blocks):
        raise ValueError(_RICH_MEDIA_UNSUPPORTED)
    blocks = _normalize_tl_page_blocks(list(blocks))
    _validate_native_page_blocks(blocks)
    return _types.InputRichMessage(
        blocks=blocks,
        rtl=bool(rtl) or None,
        noautolink=noautolink,
        photos=photos,
        documents=documents,
        users=list(users) if users is not None else None,
    )


def _is_native_rich_message(value: Any) -> bool:
    return isinstance(
        value,
        (
            _types.InputRichMessage,
            _types.InputRichMessageHTML,
            _types.InputRichMessageMarkdown,
        ),
    )


def _native_page_blocks(blocks: Any) -> list[Any]:
    """Convert supported RichBlocks to typed layer-230 Telethon PageBlocks."""
    result = []
    for block in normalize_blocks(blocks):
        kind = block.get("type")
        text = _types.TextPlain(text=str(block.get("text", "")))
        if kind in {"header", "subtitle"}:
            # Instant View title/header blocks trigger RICH_MESSAGE_BLOCK_UNSUPPORTED.
            # Preserve the title styling as a paragraph; native details/code stay intact.
            result.append(
                _types.PageBlockParagraph(text=_types.TextBold(text=text))
            )
        elif kind == "paragraph":
            text = _types.TextPlain(text=str(block.get("text", "")))
            if block.get("bold"):
                text = _types.TextBold(text=text)
            if block.get("italic"):
                text = _types.TextItalic(text=text)
            result.append(_types.PageBlockParagraph(text=text))
        elif kind == "math":
            source = str(block.get("source", ""))
            if block.get("display", True):
                result.append(_types.PageBlockMath(source=source))
            else:
                texts = []
                if block.get("before"):
                    texts.append(_types.TextPlain(text=str(block["before"])))
                texts.append(_types.TextMath(source=source))
                if block.get("after"):
                    texts.append(_types.TextPlain(text=str(block["after"])))
                rich_text = texts[0] if len(texts) == 1 else _types.TextConcat(texts=texts)
                result.append(_types.PageBlockParagraph(text=rich_text))
        elif kind == "quote":
            result.append(
                _types.PageBlockBlockquote(
                    text=_types.TextPlain(text=str(block.get("text", ""))),
                    caption=_types.TextPlain(text=str(block.get("citation", ""))),
                    collapsed=block.get("collapsed"),
                )
            )
        elif kind == "pullquote":
            result.append(
                _types.PageBlockPullquote(
                    text=_types.TextPlain(text=str(block.get("text", ""))),
                    caption=_types.TextPlain(text=str(block.get("citation", ""))),
                )
            )
        elif kind == "quote_blocks":
            result.append(
                _types.PageBlockBlockquoteBlocks(
                    blocks=_native_page_blocks(block.get("blocks", [])),
                    caption=_types.TextPlain(text=str(block.get("citation", ""))),
                )
            )
        elif kind == "code":
            result.append(
                _types.PageBlockPreformatted(
                    text=text,
                    language=str(block.get("lang", "")),
                )
            )
        elif kind == "divider":
            result.append(_types.PageBlockDivider())
        elif kind == "anchor":
            result.append(_types.PageBlockAnchor(name=str(block.get("name", ""))))
        elif kind == "footer":
            result.append(_types.PageBlockFooter(text=text))
        elif kind == "list":
            items = []
            checked_states = block.get("checked") or []
            for index, item in enumerate(block.get("items", []), 1):
                checked = (
                    checked_states[index - 1]
                    if index - 1 < len(checked_states)
                    else None
                )
                checkbox = True if block.get("checkbox") or checked is not None else None
                nested_blocks = (
                    item.to_list() if isinstance(item, RichBlocks) else None
                )
                if nested_blocks is not None:
                    nested = _native_page_blocks(nested_blocks)
                    item_type = (
                        _types.PageListOrderedItemBlocks
                        if block.get("ordered")
                        else _types.PageListItemBlocks
                    )
                    kwargs = {"blocks": nested, "checkbox": checkbox, "checked": checked}
                    if block.get("ordered"):
                        kwargs["num"] = str(index)
                    items.append(item_type(**kwargs))
                elif block.get("ordered"):
                    items.append(
                        _types.PageListOrderedItemText(
                            num=str(index),
                            text=_types.TextPlain(text=str(item)),
                            checkbox=checkbox,
                            checked=checked,
                        )
                    )
                else:
                    items.append(
                        _types.PageListItemText(
                            text=_types.TextPlain(text=str(item)),
                            checkbox=checkbox,
                            checked=checked,
                        )
                    )
            page_type = (
                _types.PageBlockOrderedList
                if block.get("ordered")
                else _types.PageBlockList
            )
            result.append(page_type(items=items))
        elif kind == "table":
            if block.get("is_compact"):
                raise ValueError(
                    "compact table styling is not part of the documented "
                    "MTProto PageBlockTable"
                )
            alignments = block.get("alignments") or []
            rows = [
                _types.PageTableRow(
                    cells=[
                        _types.PageTableCell(
                            header=row_index == 0,
                            align_center=(
                                True
                                if cell_index < len(alignments)
                                and alignments[cell_index] == "center"
                                else None
                            ),
                            align_right=(
                                True
                                if cell_index < len(alignments)
                                and alignments[cell_index] == "right"
                                else None
                            ),
                            text=_types.TextPlain(text=str(cell)),
                        )
                        for cell_index, cell in enumerate(row)
                    ]
                )
                for row_index, row in enumerate(block.get("rows") or [])
            ]
            caption = str(block.get("caption") or "")
            result.append(
                _types.PageBlockTable(
                    title=(
                        _types.TextPlain(text=caption)
                        if caption
                        else _types.TextEmpty()
                    ),
                    rows=rows,
                    bordered=bool(block.get("is_bordered", True)),
                    striped=bool(block.get("is_striped", False)),
                )
            )
        elif kind == "expandable":
            children = block.get("blocks")
            if not isinstance(children, list):
                body = str(block.get("body", ""))
                children = [{"type": "paragraph", "text": body}] if body else []
            result.append(
                _types.PageBlockDetails(
                    blocks=_native_page_blocks(children),
                    title=_types.TextPlain(text=str(block.get("title", ""))),
                    open=bool(block.get("open", False)),
                )
            )
        elif kind == "media":
            raise ValueError(_RICH_MEDIA_UNSUPPORTED)
        else:
            raise TypeError(
                f"native page-block RichBlocks does not support {kind!r} blocks"
            )
    return result


def rich_message(
    blocks: Any,
    *,
    format: MessageFormat | None = None,
    rtl: bool = False,
    noautolink: bool | None = None,
) -> Any:
    """Build or pass through a native layer-230 InputRichMessage variant."""
    if _is_native_rich_message(blocks):
        if format is not None:
            raise ValueError("format cannot override an already-built InputRichMessage")
        _reject_unverified_native_media(blocks)
        if isinstance(blocks, _types.InputRichMessage):
            return rich_message_blocks(
                blocks.blocks,
                photos=blocks.photos,
                documents=blocks.documents,
                users=blocks.users,
                rtl=bool(blocks.rtl),
                noautolink=blocks.noautolink,
            )
        markup_text = (
            blocks.html
            if isinstance(blocks, _types.InputRichMessageHTML)
            else blocks.markdown
        )
        if len(markup_text) > MAX_RICH_MESSAGE_CHARS:
            raise ValueError(
                f"RichMessages cannot exceed {MAX_RICH_MESSAGE_CHARS} characters"
            )
        return blocks
    chosen = _validate_format(format or getattr(blocks, "format", "html"))
    if isinstance(blocks, RichBlocks) and blocks.native:
        if chosen != blocks.format:
            raise ValueError("format must match a native RichBlocks format")
        return rich_message_blocks(
            _native_page_blocks(blocks), rtl=rtl, noautolink=noautolink
        )
    if chosen == "html":
        return rich_message_html(blocks, rtl=rtl, noautolink=noautolink)
    return rich_message_markdown(blocks, rtl=rtl, noautolink=noautolink)


def bot_inline_rich_message(
    blocks: Any,
    *,
    format: MessageFormat = "html",
    reply_markup: Any = None,
    rtl: bool = False,
    noautolink: bool | None = None,
) -> Any:
    """Wrap a native rich payload; keyboard markup belongs on this wrapper."""
    payload = rich_message(blocks, format=format, rtl=rtl, noautolink=noautolink)
    return _types.InputBotInlineMessageRichMessage(
        rich_message=payload, reply_markup=reply_markup
    )


class _PlainHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in {"br", "hr", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "figure"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "figure", "blockquote", "pre"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _markup_plain_text(text: str, format: MessageFormat) -> str:
    if format == "html":
        parser = _PlainHTMLParser()
        parser.feed(text)
        parser.close()
        return "\n".join(line.strip() for line in "".join(parser.parts).splitlines() if line.strip())
    text = re.sub(r"```[^\n]*\n([\s\S]*?)```", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", text)
    text = re.sub(r"(?m)^\s*(?:[-*+]|>\s?|\d+\.\s+)", "", text)
    return re.sub(r"(?<!\\)[*_~]{1,3}", "", text).replace(r"\\", "\\")


def blocks_to_plain_text(block_list: Any) -> str:
    """Extract a readable plain string for the required request ``message`` field."""
    if isinstance(block_list, RichMarkup):
        return _markup_plain_text(block_list.text, block_list.format)
    if isinstance(block_list, str):
        return block_list
    if not isinstance(block_list, list):
        block_list = normalize_blocks(block_list)
    bits: list[str] = []
    for block in block_list:
        btype = block.get("type")
        if btype in ("header", "subtitle", "paragraph", "footer"):
            bits.append(block.get("text", ""))
        elif btype in {"quote", "pullquote"}:
            bits.extend((block.get("text", ""), block.get("citation", "")))
        elif btype == "quote_blocks":
            children = block.get("blocks", [])
            bits.append(blocks_to_plain_text(children))
            if block.get("citation"):
                bits.append(block["citation"])
        elif btype == "math":
            bits.append(
                f"{block.get('before', '')}{block.get('source', '')}"
                f"{block.get('after', '')}"
            )
        elif btype == "list":
            for item in block.get("items", []):
                if isinstance(item, RichBlocks):
                    bits.append(blocks_to_plain_text(item.to_list()))
                else:
                    bits.append(str(item))
        elif btype == "code":
            bits.append(block.get("text", ""))
        elif btype == "expandable":
            bits.append(block.get("title", ""))
            children = block.get("blocks")
            if isinstance(children, list):
                bits.append(blocks_to_plain_text(children))
            else:
                bits.append(block.get("body", ""))
        elif btype == "table":
            bits.append(_table_to_text(block))
        elif btype == "media" and block.get("caption"):
            bits.append(block["caption"])
    return "\n\n".join(str(bit) for bit in bits if bit)


def _plain_message_text(blocks: Any, format: MessageFormat | None = None) -> str:
    if isinstance(blocks, RichMarkup):
        return blocks_to_plain_text(blocks)
    if isinstance(blocks, str):
        return _markup_plain_text(blocks, _validate_format(format or "html"))
    if _is_native_rich_message(blocks):
        return ""
    return blocks_to_plain_text(normalize_blocks(blocks))


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


def _build_reply_markup(client: Any, buttons: Any):
    if buttons is None:
        return None
    if isinstance(buttons, _types.ReplyInlineMarkup):
        return buttons
    return client.build_reply_markup(buttons)


def _sent_message_reference(response: Any) -> Any:
    """Resolve raw SendMessage updates to a Message or ID usable by Telethon."""
    if response is None:
        return None
    if isinstance(response, _types.UpdateShortSentMessage):
        return response.id
    if isinstance(response, _types.UpdateMessageID):
        return response.id
    if isinstance(response, (_types.UpdateNewMessage, _types.UpdateNewChannelMessage)):
        return response.message
    if isinstance(response, _types.UpdateShort):
        response = response.update

    updates = getattr(response, "updates", None)
    if updates is not None:
        messages = []
        message_ids = []
        for update in updates:
            if isinstance(update, _types.UpdateShort):
                update = update.update
            if isinstance(update, (_types.UpdateNewMessage, _types.UpdateNewChannelMessage)):
                messages.append(update.message)
            elif isinstance(update, _types.UpdateMessageID):
                message_ids.append(update.id)
        if messages:
            return messages[-1]
        if message_ids:
            return message_ids[-1]
    return response


async def _prepare_rich_message(
    client: Any,
    peer: Any,
    blocks: Any,
    *,
    format: MessageFormat | None,
    rtl: bool,
    noautolink: bool | None,
):
    if _is_native_rich_message(blocks):
        return rich_message(blocks)
    if isinstance(blocks, RichBlocks) and blocks.native:
        return rich_message(
            blocks,
            format=format,
            rtl=rtl,
            noautolink=noautolink,
        )
    markup = rich_markup_from_blocks(blocks, format=format)
    return rich_message(markup, rtl=rtl, noautolink=noautolink)


async def send_rich(
    client: Any,
    peer: Any,
    blocks: Any,
    *,
    format: MessageFormat | None = None,
    plain_text: str | None = None,
    reply_to: Any = None,
    buttons: Any = None,
    rtl: bool = False,
    noautolink: bool | None = None,
    silent: bool | None = None,
    background: bool | None = None,
    link_preview: bool = False,
) -> Any:
    """Send only a native layer-230 RichMessage using ``SendMessageRequest``.

    RPC errors propagate. There is no call to ``client.send_message``, no
    downgrade to message entities, and no unverified RichMessage media path.
    """
    input_peer = await client.get_input_entity(peer)
    native = await _prepare_rich_message(
        client,
        input_peer,
        blocks,
        format=format,
        rtl=rtl,
        noautolink=noautolink,
    )
    text = (
        plain_text
        if plain_text is not None
        else _plain_message_text(blocks, format=format)
    ) or " "
    request = _functions.messages.SendMessageRequest(
        peer=input_peer,
        message=text,
        no_webpage=not link_preview,
        silent=silent,
        background=background,
        reply_to=_build_reply_to(reply_to),
        reply_markup=_build_reply_markup(client, buttons),
        rich_message=native,
    )
    result = await call_rich_request(
        client, request, content=request.rich_message, target_kind="peer"
    )
    getter = getattr(client, "_get_response_message", None)
    if getter is not None:
        try:
            adapted = getter(request, result, input_peer)
            if adapted is not None:
                result = adapted
        except (AttributeError, TypeError, ValueError):
            pass
    return _sent_message_reference(result)


async def edit_inline_plain(
    client: Any,
    inline_id: Any,
    text: str,
    *,
    buttons: Any = None,
    link_preview: bool = False,
) -> Any:
    """Edit an inline bot message as plain text using raw layer-230 MTProto.

    This is a constrained fallback for servers that reject an inline
    ``RichMessage`` with ``RICH_MESSAGE_UNSUPPORTED``. It never calls the
    Bot API or Telethon's legacy message-edit convenience method.
    """
    inline_types = (_types.InputBotInlineMessageID, _types.InputBotInlineMessageID64)
    if not isinstance(inline_id, inline_types):
        raise TypeError("edit_inline_plain requires a Telegram inline message ID")
    if not isinstance(text, str):
        raise TypeError("edit_inline_plain text must be str")

    request = _functions.messages.EditInlineBotMessageRequest(
        id=inline_id,
        message=text or " ",
        no_webpage=not link_preview,
        reply_markup=_build_reply_markup(client, buttons),
    )
    route = getattr(client, "route", None)
    request_client = route() if callable(route) else client
    session = getattr(request_client, "session", None)
    if session is not None and session.dc_id != inline_id.dc_id:
        sender = await request_client._borrow_exported_sender(inline_id.dc_id)
        try:
            call = call_rich_request(
                request_client, request, content=request.rich_message,
                target_kind="inline_exported", sender=sender,
            )
        except Exception:
            await request_client._return_exported_sender(sender)
            raise
        try:
            return await call
        finally:
            await request_client._return_exported_sender(sender)
    return await call_rich_request(
        request_client, request, content=request.rich_message, target_kind="inline"
    )


async def edit_rich(
    client: Any,
    peer: Any,
    msg_id: Any,
    blocks: Any,
    *,
    format: MessageFormat | None = None,
    plain_text: str | None = None,
    buttons: Any = None,
    rtl: bool = False,
    noautolink: bool | None = None,
    link_preview: bool = False,
) -> Any:
    """Edit only through raw layer-230 ``EditMessageRequest`` variants."""
    inline_types = (_types.InputBotInlineMessageID, _types.InputBotInlineMessageID64)
    inline_id = peer if isinstance(peer, inline_types) else None
    mid = None if inline_id is not None else _get_message_id(msg_id)
    if inline_id is None and mid is None:
        raise ValueError(f"edit_rich: could not resolve a message id from {msg_id!r}")

    input_peer = None if inline_id is not None else await client.get_input_entity(peer)
    native = await _prepare_rich_message(
        client,
        input_peer,
        blocks,
        format=format,
        rtl=rtl,
        noautolink=noautolink,
    )
    text = (
        plain_text
        if plain_text is not None
        else _plain_message_text(blocks, format=format)
    ) or " "
    markup = _build_reply_markup(client, buttons)
    if inline_id is not None:
        request = _functions.messages.EditInlineBotMessageRequest(
            id=inline_id,
            message=text,
            no_webpage=not link_preview,
            reply_markup=markup,
            rich_message=native,
        )
        route = getattr(client, "route", None)
        request_client = route() if callable(route) else client
        session = getattr(request_client, "session", None)
        if session is not None and session.dc_id != inline_id.dc_id:
            sender = await request_client._borrow_exported_sender(inline_id.dc_id)
            try:
                call = call_rich_request(
                    request_client, request, content=request.rich_message,
                    target_kind="inline_exported", sender=sender,
                )
            except Exception:
                await request_client._return_exported_sender(sender)
                raise
            try:
                return await call
            finally:
                await request_client._return_exported_sender(sender)
        return await call_rich_request(
            request_client, request, content=request.rich_message, target_kind="inline"
        )

    request = _functions.messages.EditMessageRequest(
        peer=input_peer,
        id=mid,
        message=text,
        no_webpage=not link_preview,
        reply_markup=markup,
        rich_message=native,
    )
    result = await call_rich_request(
        client, request, content=request.rich_message, target_kind="peer"
    )
    getter = getattr(client, "_get_response_message", None)
    if getter is not None:
        try:
            return getter(request, result, input_peer)
        except (TypeError, ValueError):
            pass
    return result

"""Public agent answers with native Layer-230 structure and safe nested actions."""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from telethon.extensions import markdown
from telethon.tl import types

from pyUltroid._core.notices import sanitize_error_text

from ._telegram import public_text, rich_output


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str | None] = field(default_factory=dict)
    children: list[Any] = field(default_factory=list)


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if len(self.stack) >= 18 or self.count > 800:
            raise ValueError("The answer exceeds native structure limits")
        node = _Node(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"br", "hr", "img", "input", "meta", "link"}:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def answer_text(response: Any) -> str:
    """Extract public content without revealing provider errors or tool protocol."""
    if isinstance(response, str):
        return public_text(response)
    content = getattr(response, "content", None)
    if getattr(response, "error", None):
        if getattr(response, "provider", None) != "runtime":
            return "Sorry, the AI service couldn't complete that request. Please try again."
        return sanitize_error_text(
            public_text(content or "The agent could not complete this request.")
        )
    return public_text(content or "No response was returned.")


def render_answer(response: Any) -> Any:
    """Preserve the answer's HTML or Markdown format for plain-text extraction."""
    return rich_output(answer_text(response))


def answer_plain(response: Any) -> str:
    """Return readable answer text without synthetic action labels."""
    markup = render_answer(response)
    if markup.format == "markdown":
        return markdown.parse(markup.text)[0]

    class Plain(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
            self.hidden = 0

        def handle_starttag(self, tag, attrs):
            if tag in {"script", "style"}:
                self.hidden += 1
            if not self.hidden and tag in {"br", "hr", "p", "div", "li", "tr", "pre", "summary", "h1", "h2", "h3", "h4", "h5", "h6"}:
                self.parts.append("\n")

        def handle_endtag(self, tag):
            if tag in {"script", "style"}:
                self.hidden = max(0, self.hidden - 1)
            elif not self.hidden and tag in {"p", "div", "li", "tr", "pre", "summary", "h1", "h2", "h3", "h4", "h5", "h6"}:
                self.parts.append("\n")
            elif not self.hidden and tag in {"td", "th"}:
                self.parts.append("\t")

        def handle_data(self, data):
            if not self.hidden:
                self.parts.append(data)

    parser = Plain()
    parser.feed(markup.text)
    parser.close()
    return "".join(parser.parts).strip("\n")


def _safe_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        return bool(
            parsed.scheme.lower() in {"https", "http"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not any(
                any(
                    word in key.lower()
                    for word in ("token", "secret", "password", "api_key", "session")
                )
                for key, _value in parse_qsl(parsed.query)
            )
            and sanitize_error_text(value) == value
        )
    except ValueError:
        return False


def _plain(nodes) -> str:
    return "".join(
        item if isinstance(item, str) else _plain(item.children) for item in nodes
    )


def _inline(nodes):
    result = []
    wrappers = {
        "b": types.TextBold,
        "strong": types.TextBold,
        "i": types.TextItalic,
        "em": types.TextItalic,
        "u": types.TextUnderline,
        "s": types.TextStrike,
        "del": types.TextStrike,
        "code": types.TextFixed,
        "sub": types.TextSubscript,
        "sup": types.TextSuperscript,
        "tg-spoiler": types.TextSpoiler,
    }
    for node in nodes:
        if isinstance(node, str):
            result.append(types.TextPlain(node))
        elif node.tag in {"script", "style", "img", "iframe"}:
            continue
        elif node.tag == "br":
            result.append(types.TextPlain("\n"))
        elif node.tag in wrappers:
            result.append(wrappers[node.tag](_inline(node.children)))
        elif node.tag == "a" and _safe_url(node.attrs.get("href") or ""):
            result.append(types.TextUrl(_inline(node.children), node.attrs["href"], 0))
        else:
            result.append(_inline(node.children))
    if not result:
        return types.TextEmpty()
    return types.TextConcat(result) if len(result) != 1 else result[0]


def _blocks(nodes):
    output = []
    inline = []

    def flush():
        if _plain(inline).strip():
            output.append(types.PageBlockParagraph(_inline(inline)))
        inline.clear()

    for node in nodes:
        if isinstance(node, str):
            inline.append(node)
            continue
        if node.tag in {"script", "style", "img", "iframe", "summary"}:
            continue
        if node.tag not in {
            "html",
            "body",
            "div",
            "p",
            "section",
            "article",
            "details",
            "pre",
            "blockquote",
            "ul",
            "ol",
            "table",
            "hr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }:
            inline.append(node)
            continue
        flush()
        if node.tag == "details":
            summary = next(
                (
                    item
                    for item in node.children
                    if isinstance(item, _Node) and item.tag == "summary"
                ),
                None,
            )
            title = _inline(summary.children) if summary else types.TextPlain("Details")
            output.append(
                types.PageBlockDetails(
                    _blocks(node.children), title, open="open" in node.attrs
                )
            )
        elif node.tag == "pre":
            output.append(
                types.PageBlockPreformatted(types.TextPlain(_plain(node.children)), "")
            )
        elif node.tag == "blockquote":
            output.append(
                types.PageBlockBlockquoteBlocks(
                    _blocks(node.children), types.TextEmpty()
                )
            )
        elif node.tag in {"ul", "ol"}:
            items = [
                item
                for item in node.children
                if isinstance(item, _Node) and item.tag == "li"
            ]
            if node.tag == "ul":
                output.append(
                    types.PageBlockList(
                        [
                            types.PageListItemBlocks(_blocks(item.children))
                            for item in items
                        ]
                    )
                )
            else:
                output.append(
                    types.PageBlockOrderedList(
                        [
                            types.PageListOrderedItemBlocks(
                                _blocks(item.children), num=str(index)
                            )
                            for index, item in enumerate(items, 1)
                        ]
                    )
                )
        elif node.tag == "table":

            def rows(children):
                for item in children:
                    if isinstance(item, _Node):
                        if item.tag == "tr":
                            yield item
                        elif item.tag in {"thead", "tbody", "tfoot"}:
                            yield from rows(item.children)

            native_rows = []
            for row in rows(node.children):
                cells = [
                    types.PageTableCell(
                        text=_inline(cell.children), header=cell.tag == "th"
                    )
                    for cell in row.children
                    if isinstance(cell, _Node) and cell.tag in {"td", "th"}
                ]
                native_rows.append(types.PageTableRow(cells))
            output.append(
                types.PageBlockTable(
                    types.TextEmpty(), native_rows, bordered=True, striped=True
                )
            )
        elif node.tag == "hr":
            output.append(types.PageBlockDivider())
        elif node.tag.startswith("h") and node.tag[1:].isdigit():
            output.append(
                types.PageBlockParagraph(types.TextBold(_inline(node.children)))
            )
        else:
            output.extend(_blocks(node.children))
    flush()
    return output


def native_answer(response: Any):
    """Build a native answer with nested copy and safe public-source URL controls."""
    from pyUltroid._core.richmsg import rich_message_blocks

    markup = render_answer(response)
    urls = []
    if markup.format == "html":
        tree = _Tree()
        tree.feed(markup.text)
        body = _blocks(tree.root.children)

        def visit(nodes):
            for node in nodes:
                if isinstance(node, _Node):
                    url = node.attrs.get("href") or ""
                    if node.tag == "a" and _safe_url(url) and url not in urls:
                        urls.append(url)
                    visit(node.children)

        visit(tree.root.children)
    else:
        plain, entities = markdown.parse(markup.text)
        body = [types.PageBlockParagraph(types.TextWithEntities(plain, entities))]
        for entity in entities:
            url = getattr(entity, "url", "")
            if url and _safe_url(url) and url not in urls:
                urls.append(url)
    plain = answer_plain(response)
    controls = []
    # Small individual copy values keep the native controls usable on long answers.
    for start in range(0, len(plain), 2048):
        index = start // 2048 + 1
        label = "Copy answer" if len(plain) <= 2048 else f"Copy part {index}"
        controls.append(
            types.PageBlockButtonRow(
                [
                    types.PageButton(
                        types.TextPlain(label),
                        types.InlineButtonTypeCopy(plain[start : start + 2048]),
                    )
                ]
            )
        )
        if len(controls) == 8:
            break
    controls.extend(
        types.PageBlockButtonRow(
            [
                types.PageButton(
                    types.TextPlain(f"Open source {index}"),
                    types.InlineButtonTypeUrl(url),
                )
            ]
        )
        for index, url in enumerate(urls[:4], 1)
    )
    if controls:
        body.append(
            types.PageBlockDetails(
                [
                    types.PageBlockDetails(
                        controls, types.TextPlain("Copy and sources"), open=False
                    )
                ],
                types.TextPlain("Answer actions"),
                open=False,
            )
        )
    return rich_message_blocks(body)

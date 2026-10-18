"""Small native RichMessage helpers for assistant menu pages."""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Any

from pyUltroid._core.richmsg import RichFile, RichMarkup, edit_rich


def page_footer(page: int, pages: int, total: int) -> str:
    """Return the shared lower-case page index and item count."""
    pages = max(1, int(pages))
    page = max(1, min(int(page), pages))
    return f"page {page} of {pages} • total {max(0, int(total))}"


def rich_page(
    title: str,
    rows: Sequence[Sequence[Any]],
    *,
    description: str | None = None,
    columns: Sequence[str] = ("Option", "What it does"),
    page: int = 1,
    pages: int = 1,
    total: int | None = None,
    media: tuple[str, Any, str | None] | None = None,
) -> RichMarkup:
    """Build a large-heading, divider, table, divider, indexed RichMessage page."""
    data_rows = [tuple("" if cell is None else str(cell) for cell in row) for row in rows]
    column_names = tuple(str(name) for name in columns)
    total_items = len(data_rows) if total is None else int(total)
    parts = [f"<h1>{html.escape(str(title))}</h1>"]
    if description:
        parts.append(f"<p>{html.escape(str(description))}</p>")
    if media is not None:
        kind, source, caption = media
        if kind not in {"photo", "document"}:
            raise ValueError("media kind must be 'photo' or 'document'")
        file_id = "assistant-page-media"
        if kind == "photo":
            alt = html.escape(caption or "", quote=True)
            parts.append(f'<figure><img src="{file_id}" alt="{alt}"/></figure>')
        else:
            parts.append(f'<figure><tg-document src="{file_id}"></tg-document></figure>')
        if caption:
            parts.append(f"<p>{html.escape(caption)}</p>")
    parts.append("<hr>")
    parts.append('<table class="bordered compact">')
    parts.append("<tr>" + "".join(f"<th>{html.escape(name)}</th>" for name in column_names) + "</tr>")
    for row in data_rows:
        cells = list(row[: len(column_names)])
        cells.extend("" for _ in range(len(column_names) - len(cells)))
        parts.append("<tr>" + "".join(f"<td>{html.escape(cell)}</td>" for cell in cells) + "</tr>")
    parts.append("</table><hr>")
    parts.append(
        f"<p><i>{html.escape(page_footer(page, pages, total_items))}</i></p>"
    )
    files = ()
    if media is not None:
        kind, source, _caption = media
        files = (RichFile("assistant-page-media", source, kind),)
    return RichMarkup("".join(parts), "html", files)


def body_page(
    title: str,
    body_html: str,
    *,
    page: int = 1,
    pages: int = 1,
    total: int = 1,
    media: tuple[str, Any, str | None] | None = None,
) -> RichMarkup:
    """Wrap already-sanitized native HTML in a standard indexed page."""
    parts = [f"<h1>{html.escape(str(title))}</h1><hr>{body_html}"]
    files = ()
    if media is not None:
        kind, source, caption = media
        if kind not in {"photo", "document"}:
            raise ValueError("media kind must be 'photo' or 'document'")
        file_id = "assistant-page-media"
        if kind == "photo":
            alt = html.escape(caption or "", quote=True)
            parts.append(f'<figure><img src="{file_id}" alt="{alt}"/></figure>')
        else:
            parts.append(f'<figure><tg-document src="{file_id}"></tg-document></figure>')
        if caption:
            parts.append(f"<p>{html.escape(caption)}</p>")
        files = (RichFile(file_id, source, kind),)
    parts.extend(
        (
            "<hr>",
            f"<p><i>{html.escape(page_footer(page, pages, total))}</i></p>",
        )
    )
    return RichMarkup("".join(parts), "html", files)


def callback_edit_target(event: Any) -> tuple[Any, Any]:
    """Return the actual callback message target, never the query's random ID."""
    query = getattr(event, "query", None)
    inline_id = getattr(query, "msg_id", None)
    inline_types = {"InputBotInlineMessageID", "InputBotInlineMessageID64"}
    if type(inline_id).__name__ in inline_types:
        return inline_id, None

    message_id = getattr(event, "message_id", None)
    if type(message_id).__name__ in inline_types:
        return message_id, None
    if message_id is None:
        message_id = inline_id
    return getattr(event, "chat_id", None), message_id


async def edit_callback_page(event: Any, content: Any, buttons: Any = None) -> Any:
    """Edit a chat or inline callback message through the Layer 230 API."""
    peer, message = callback_edit_target(event)
    return await edit_rich(
        event.client,
        peer,
        message,
        content,
        format="html",
        buttons=buttons,
        link_preview=False,
    )

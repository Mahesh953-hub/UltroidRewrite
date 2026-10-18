"""Owner-only Layer-230 RichMessage examples: each index pick sends a sample."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from telethon import Button
from telethon.errors import MessageNotModifiedError, RPCError
from telethon.tl import types

from pyUltroid._core.assistant_creations import CREATIONS, creation_payload
from pyUltroid._core.matrix_arcade import lobby_payload
from pyUltroid._core.richdebug import rpc_identifier, trace_rich_rpc
from pyUltroid._core.richmsg import RichBlocks, edit_rich, rich_message, send_rich
from pyUltroid._misc._assistant import asst_cmd, callback

# Native schema examples, NOT a claim of server/account acceptance.
# No plain-text controls, unsupported title blocks or note-only index entries.
CATALOG = (
    ("native", "Paragraph", "PageBlockParagraph"),
    *((f"heading-{n}", f"Heading {n}", f"PageBlockHeading{n}") for n in range(1, 7)),
    ("inline", "Inline styles", "TextBold / Italic / Underline / Strike"),
    ("extra-styles", "Mark / sub / superscript", "TextMarked / Subscript / Superscript"),
    ("spoiler", "Spoiler", "TextSpoiler"),
    ("links", "Links / email / phone", "TextUrl / Email / Phone"),
    ("emoji", "Custom emoji", "TextCustomEmoji; account eligibility may restrict it"),
    ("date", "Formatted date", "TextDate"),
    ("divider", "Divider", "PageBlockDivider"),
    ("footer", "Footer", "PageBlockFooter"),
    ("lists", "Unordered list", "PageBlockList"),
    ("ordered", "Ordered list", "PageBlockOrderedList"),
    ("tasks", "Task list", "PageListItemText checkbox / checked"),
    ("code", "Inline / fenced code", "TextFixed / PageBlockPreformatted"),
    ("tables", "Table", "PageBlockTable"),
    ("quotes", "Quotation", "PageBlockBlockquote"),
    ("pullquote", "Pull quote", "PageBlockPullquote"),
    ("rich-expand", "Expandable quotation", "PageBlockBlockquote collapsed flag; not details"),
    ("details", "Details: closed", "PageBlockDetails"),
    ("details-open", "Details: open", "PageBlockDetails open flag"),
    ("details-code", "Details + code", "PageBlockDetails / Preformatted"),
    ("details-steps", "Two-step details", "Two PageBlockDetails blocks"),
    ("native-guide", "Seven-step guide", "Bold paragraph heading + seven details/code sections"),
    ("formula", "Inline / block formula", "TextMath / PageBlockMath"),
    ("anchors", "Named anchor", "TextAnchor / TextUrl"),
    ("map", "Map", "PageBlockMap; static public Rome coordinates"),
    ("rich-buttons", "Rich button row", "PageBlockButtonRow: URL / callback / copy"),
    ("text-buttons", "Inline text button", "TextButton"),
    ("block-quotes", "Multi-block quotation", "PageBlockBlockquoteBlocks"),
    ("nested-details", "Nested details", "PageBlockDetails inside details"),
    ("text-entities", "Native entity text", "TextWithEntities inside a native paragraph"),
    ("details-wrapper", "Native RichBlocks wrapper", "RichBlocks(native=True).expandable"),
    ("rtl", "Right-to-left", "InputRichMessage rtl flag"),
    *((sid, label, description) for sid, label, description, _builder in CREATIONS),
    ("matrix-arcade", "Matrix arcade", "Playable native games, bounded edit rain and a browser source lab"),
)
PAGE_SIZE = 8
CALLBACK_PATTERN = re.compile(r"^rich:(?:index:[0-9]{1,2}|(?:pick|send|edit):[a-z0-9-]{1,32}|ping:ok)$")

# Already-delivered keyboards still resolve, but never to their old plain or
# unsupported payloads. Removed media/footnote entries are not falsely aliased.
_ALIASES = {
    "plain": "native", "html": "native", "markdown": "native",
    "headings": "heading-1", "native-title": "native",
    "divider-footer": "footer", "details-native": "details",
    "native-open": "details-open", "native-code": "details-code",
    "native-steps": "details-steps", "legacy-styles": "text-entities",
    "legacy-expand": "rich-expand",
}


def _page(page: int) -> int:
    return max(0, min(page, (len(CATALOG) - 1) // PAGE_SIZE))


def _data(action: str, value: str) -> bytes:
    return f"rich:{action}:{value}".encode("ascii")


def _sample(sid: str):
    sid = _ALIASES.get(sid, sid)
    return next((item for item in CATALOG if item[0] == sid), None)


def _sample_page(sid: str) -> int:
    return next((i // PAGE_SIZE for i, item in enumerate(CATALOG) if item[0] == sid), 0)


def _keyboard(page: int = 0, selected: str = ""):
    page = _page(page)
    if selected:
        return [
            [Button.inline("Send again", _data("send", selected)), Button.inline("Edit this example", _data("edit", selected))],
            [Button.inline("Back to index", _data("index", str(_sample_page(selected))))],
        ]
    items = CATALOG[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows = [[Button.inline(item[1], _data("pick", item[0])) for item in items[i:i + 2]] for i in range(0, len(items), 2)]
    nav = []
    if page:
        nav.append(Button.inline("Previous", _data("index", str(page - 1))))
    if (page + 1) * PAGE_SIZE < len(CATALOG):
        nav.append(Button.inline("Next", _data("index", str(page + 1))))
    if nav:
        rows.append(nav)
    return rows


def _text(value: str):
    return types.TextPlain(value)


def _paragraph(value: str):
    return types.PageBlockParagraph(_text(value))


def _details(title: str = "Step 1", *, code: bool = False, opened: bool = False):
    children = [_paragraph("Static example instruction.")]
    if code:
        children.append(types.PageBlockPreformatted(_text("echo native_example"), "bash"))
    return types.PageBlockDetails(blocks=children, title=_text(title), open=opened)


def _guide_blocks():
    return [
        types.PageBlockParagraph(types.TextBold(_text("Seven-step guide"))),
        _paragraph("Open each section to inspect its instructions and code."),
        types.PageBlockDivider(),
        *[_details(f"Step {n}", code=True) for n in range(1, 8)],
        types.PageBlockDivider(),
        types.PageBlockParagraph(types.TextItalic(_text("page 1 of 1; seven steps"))),
    ]


def _payload(sample):
    """Every catalog entry resolves to structured InputRichMessage blocks."""
    sid = sample[0]
    if sid.startswith("heading-"):
        level = int(sid.rsplit("-", 1)[1])
        blocks = [getattr(types, f"PageBlockHeading{level}")(_text(f"Heading {level}")), _paragraph("Native heading example.")]
    elif sid == "details-wrapper":
        return rich_message(RichBlocks(native=True).expandable("Native wrapper", "A real details body."))
    elif sid in {entry[0] for entry in CREATIONS}:
        return creation_payload(sid)
    elif sid == "matrix-arcade":
        return lobby_payload()
    else:
        examples = {
            "native": [_paragraph("One native RichMessage paragraph.")],
            "inline": [types.PageBlockParagraph(types.TextConcat([
                types.TextBold(_text("Bold ")), types.TextItalic(_text("italic ")),
                types.TextUnderline(_text("underline ")), types.TextStrike(_text("strike ")),
                types.TextBold(types.TextItalic(_text("nested styles"))),
            ]))],
            "extra-styles": [types.PageBlockParagraph(types.TextConcat([
                types.TextMarked(_text("Highlighted ")), _text("H"), types.TextSubscript(_text("2")),
                _text("O x"), types.TextSuperscript(_text("2")),
            ]))],
            "spoiler": [types.PageBlockParagraph(types.TextSpoiler(_text("Tap to reveal a native spoiler.")))],
            "links": [types.PageBlockParagraph(types.TextConcat([
                types.TextUrl(_text("Telegram "), "https://telegram.org", 0),
                types.TextEmail(_text("Email example "), "example@example.com"),
                types.TextPhone(_text("Fictional phone"), "+12025550123"),
            ]))],
            "emoji": [types.PageBlockParagraph(types.TextCustomEmoji(5368324170671202286, "👍"))],
            "date": [types.PageBlockParagraph(types.TextDate(_text("Sample date"), datetime(2023, 11, 14, tzinfo=timezone.utc), long_date=True))],
            "divider": [_paragraph("Above"), types.PageBlockDivider(), _paragraph("Below")],
            "footer": [_paragraph("Native footer example"), types.PageBlockFooter(_text("Sample footer"))],
            "lists": [types.PageBlockList([types.PageListItemText(_text("First")), types.PageListItemText(_text("Second"))])],
            "ordered": [types.PageBlockOrderedList([types.PageListOrderedItemText(_text("First"), num="1"), types.PageListOrderedItemText(_text("Second"), num="2")])],
            "tasks": [types.PageBlockList([types.PageListItemText(_text("Done"), checkbox=True, checked=True), types.PageListItemText(_text("Next"), checkbox=True, checked=False)])],
            "code": [types.PageBlockParagraph(types.TextFixed(_text("inline_code()"))), types.PageBlockPreformatted(_text('def hello():\n    return "rich"'), "python")],
            "tables": [types.PageBlockTable(_text("Sample table"), [
                types.PageTableRow([types.PageTableCell(header=True, text=_text("Format")), types.PageTableCell(header=True, text=_text("Example"))]),
                types.PageTableRow([types.PageTableCell(text=_text("Native")), types.PageTableCell(text=_text("Table"))]),
            ], bordered=True, striped=True)],
            "quotes": [types.PageBlockBlockquote(_text("Native quotation"), _text("Example author"))],
            "pullquote": [types.PageBlockPullquote(_text("Native pull quote"), _text("Example author"))],
            "rich-expand": [types.PageBlockBlockquote(_text("Expandable quotation\nLine two\nLine three\nLine four\nLine five\nLine six"), _text("Example author"), collapsed=True)],
            "details": [_details()],
            "details-open": [_details(opened=True)],
            "details-code": [_details(code=True)],
            "details-steps": [_details(), _details("Step 2", code=True)],
            "native-guide": _guide_blocks(),
            "formula": [types.PageBlockParagraph(types.TextMath("x^2 + y^2")), types.PageBlockMath("E = mc^2")],
            "anchors": [types.PageBlockParagraph(types.TextUrl(_text("Jump to section"), "#section", 0)), types.PageBlockParagraph(types.TextAnchor(_text("Target section"), "section"))],
            "map": [types.PageBlockMap(types.GeoPoint(long=12.5, lat=41.9, access_hash=0), 14, 320, 200, types.PageCaption(_text("Rome"), types.TextEmpty()))],
            "rich-buttons": [types.PageBlockButtonRow([
                types.PageButton(_text("Telegram"), types.InlineButtonTypeUrl("https://telegram.org")),
                types.PageButton(_text("Test callback"), types.InlineButtonTypeCallback(b"rich:ping:ok")),
                types.PageButton(_text("Copy sample"), types.InlineButtonTypeCopy("Sample text")),
            ])],
            "text-buttons": [types.PageBlockParagraph(types.TextButton(_text("Telegram"), types.InlineButtonTypeUrl("https://telegram.org")))],
            "block-quotes": [types.PageBlockBlockquoteBlocks([_paragraph("First quoted paragraph."), types.PageBlockPreformatted(_text("echo quoted"), "bash")], _text("Example author"))],
            "nested-details": [types.PageBlockDetails([_paragraph("Outer body."), _details("Inner section", code=True)], _text("Outer section"), open=False)],
            "text-entities": [types.PageBlockParagraph(types.TextWithEntities("Bold and italic", [types.MessageEntityBold(0, 4), types.MessageEntityItalic(9, 6)]))],
            "rtl": [_paragraph("مرحبا — native right-to-left example")],
        }
        blocks = examples[sid]
    return types.InputRichMessage(blocks=blocks, rtl=True if sid == "rtl" else None)


def _index(page: int = 0, error: str = ""):
    page = _page(page)
    blocks = [
        types.PageBlockParagraph(types.TextBold(_text("RichMessage examples"))),
        _paragraph("Tap an index button to send that native example immediately. Send again creates a new example; Edit this example tests native editing."),
        _paragraph("All examples use structured Layer-230 MTProto. Server/account acceptance varies; a rejection is reported, never downgraded to plain text."),
        _paragraph("Media, collages and slideshows are not indexed: verified uploaded references are required. Raw HTML/Markdown controls and footnote syntax are not demonstrated here."),
    ]
    blocks.extend(_paragraph(f"{label} — {status}") for _sid, label, status in CATALOG[page * PAGE_SIZE:(page + 1) * PAGE_SIZE])
    blocks.append(_paragraph(f"Page {page + 1}/{(len(CATALOG) - 1) // PAGE_SIZE + 1}. {len(CATALOG)} native examples."))
    if error:
        blocks.append(_paragraph(f"Last example rejected: {error}"))
    return types.InputRichMessage(blocks=blocks)


async def _edit_index(event: Any, page: int = 0, error: str = ""):
    try:
        return await edit_rich(event.client, event.chat_id, event.message_id, _index(page, error), buttons=_keyboard(page))
    except MessageNotModifiedError:
        trace_rich_rpc("assistant.rich.index", request_kind="edit", outcome="not_modified")


async def _run_sample(event: Any, sample, operation: str):
    sid, _label, _status = sample
    payload = _payload(sample)
    buttons = _keyboard(selected=sid)
    stage = f"assistant.rich.{sid}"
    trace_rich_rpc(stage, content=payload, request_kind=operation, target_kind="peer", outcome="attempt")
    try:
        if operation == "send":
            result = await send_rich(event.client, event.chat_id, payload, buttons=buttons)
        elif operation == "edit":
            result = await edit_rich(event.client, event.chat_id, event.message_id, payload, buttons=buttons)
        else:
            raise ValueError("unknown rich operation")
        trace_rich_rpc(stage, content=payload, request_kind=operation, target_kind="peer", outcome="success")
        return result
    except (RPCError, ValueError, TypeError) as error:
        identifier = rpc_identifier(error) if isinstance(error, RPCError) else "LOCAL_VALIDATION_ERROR"
        trace_rich_rpc(stage, content=payload, error=error, request_kind=operation, target_kind="peer", outcome="rejected")
        await _edit_index(event, _sample_page(sid), error=f"{sid}/{operation}: {identifier}")
        return None


@asst_cmd(pattern="rich", owner=True)
async def rich(event: Any):
    # Send our own native index; never edit the user's incoming command.
    return await send_rich(event.client, event.chat_id, _index(), buttons=_keyboard())


@callback(CALLBACK_PATTERN, owner=True)
async def rich_callback(event: Any):
    try:
        data = event.data.decode("ascii", "strict")
    except (UnicodeDecodeError, AttributeError):
        return
    if CALLBACK_PATTERN.fullmatch(data) is None:
        return
    action, value = data.split(":")[1:]
    await event.answer()
    if action == "ping":
        return
    if action == "index":
        return await _edit_index(event, int(value))
    sample = _sample(value)
    if sample is None:
        return await _edit_index(event, error="unknown or unavailable sample")
    # Selection IS the demonstration, not a label requiring a second click.
    return await _run_sample(event, sample, "send" if action == "pick" else action)

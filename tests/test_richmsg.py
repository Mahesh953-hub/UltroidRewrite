"""Offline tests for the Telethon layer-230 native RichMessage engine."""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest
from telethon import types
from telethon.tl.functions.messages import (
    EditInlineBotMessageRequest,
    EditMessageRequest,
    SendMessageRequest,
)

from pyUltroid._core import richmsg


class Client:
    def __init__(self):
        self.requests = []

    async def get_input_entity(self, _peer):
        return types.InputPeerSelf()

    def build_reply_markup(self, buttons):
        return ("markup", buttons)

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace(id=77)


class UploadClient(Client):
    def __init__(self):
        super().__init__()
        self.uploaded_media = []

    async def upload_file(self, media):
        self.uploaded_media.append(media)
        return types.InputFile(id=1, parts=1, name="cover.jpg", md5_checksum="")


def test_rich_sender_capability_allows_bots_and_confirmed_premium_users_only():
    bot = SimpleNamespace(_bot=True)
    premium_user = SimpleNamespace(
        _bot=False,
        me=SimpleNamespace(bot=False, premium=True),
    )
    regular_user = SimpleNamespace(
        _bot=False,
        me=SimpleNamespace(bot=False, premium=False),
    )
    unknown = SimpleNamespace()

    assert asyncio.run(richmsg.client_can_send_rich_message(bot)) is True
    assert asyncio.run(richmsg.client_can_send_rich_message(premium_user)) is True
    assert asyncio.run(richmsg.client_can_send_rich_message(regular_user)) is False
    assert asyncio.run(richmsg.client_can_send_rich_message(unknown)) is False


def _blocks():
    return (
        richmsg.RichBlocks()
        .header("Report", level=2)
        .paragraph("Body <safe>")
        .list(["one", "two"], ordered=True)
        .table([["Name", "Value"], ["plugins", "42"]], caption="Summary")
        .expandable("Details", "More information")
    )


def test_native_html_and_markdown_render_all_supported_blocks():
    html = richmsg.rich_message_html(_blocks(), rtl=True, noautolink=True)
    markdown = richmsg.rich_message_markdown(_blocks(), rtl=True, noautolink=True)

    assert isinstance(html, types.InputRichMessageHTML)
    assert html.rtl is True and html.noautolink is True
    assert "<p><b>Report</b></p>" in html.html
    assert "<table" in html.html and "<blockquote expandable>" in html.html
    assert "Body &lt;safe&gt;" in html.html

    assert isinstance(markdown, types.InputRichMessageMarkdown)
    assert markdown.rtl is True and markdown.noautolink is True
    assert "**Report**" in markdown.markdown
    assert "| Name | Value |" in markdown.markdown
    assert "> Details" in markdown.markdown


def test_native_input_page_blocks_are_built_without_markup_downgrade():
    page_block = types.PageBlockParagraph(text=types.TextPlain(text="A paragraph"))
    message = richmsg.rich_message_blocks([page_block], rtl=True)

    assert isinstance(message, types.InputRichMessage)
    assert message.blocks == [page_block]
    assert message.rtl is True


def test_native_richblocks_emit_typed_details_code_and_italic_footer():
    content = (
        richmsg.RichBlocks()
        .paragraph("Run the setup command.")
        .code("python3 -m pyUltroid", lang="bash")
    )
    blocks = (
        richmsg.RichBlocks(native=True)
        .header("Deploy Ultroid")
        .expandable("Step 1", content)
        .divider()
        .paragraph("page 1 of 1 • total 1", italic=True)
    )
    fallback = richmsg.rich_markup_from_blocks(blocks).text
    assert "<blockquote expandable>" in fallback
    assert '<pre><code class="language-bash">python3 -m pyUltroid</code></pre>' in fallback
    assert "<i>page 1 of 1 • total 1</i>" in fallback

    client = Client()
    asyncio.run(richmsg.send_rich(client, "peer", blocks, format="html"))
    request = client.requests[0]
    message = request.rich_message
    assert isinstance(request, SendMessageRequest)
    assert isinstance(message, types.InputRichMessage)
    assert isinstance(message.blocks[0], types.PageBlockParagraph)
    assert isinstance(message.blocks[0].text, types.TextBold)
    assert message.blocks[0].text.text.text == "Deploy Ultroid"
    details = message.blocks[1]
    assert isinstance(details, types.PageBlockDetails)
    assert details.open is False
    assert isinstance(details.blocks[0], types.PageBlockParagraph)
    assert isinstance(details.blocks[1], types.PageBlockPreformatted)
    assert details.blocks[1].language == "bash"
    assert isinstance(message.blocks[-1].text, types.TextItalic)
    assert "python3 -m pyUltroid" in request.message
    assert bytes(request)


def test_richfile_photo_references_are_rejected_explicitly():
    photo = types.InputPhoto(id=123, access_hash=456, file_reference=b"ref")
    markup = richmsg.RichMarkup(
        '<p>Photo</p><img src="hero" alt="A photo"/>',
        "html",
        (richmsg.RichFile("hero", photo, "photo"),),
    )

    with pytest.raises(ValueError, match="RichMessage photo/document references are unsupported"):
        richmsg.rich_message(markup)
    with pytest.raises(ValueError, match="RichMessage photo/document references are unsupported"):
        richmsg.rich_message_html('<img src="hero"/>')

    rich_file = types.InputRichFilePhoto(id="hero", photo=photo)
    prebuilt = types.InputRichMessageHTML(html='<img src="hero"/>', files=[rich_file])
    with pytest.raises(ValueError, match="RichMessage photo/document references are unsupported"):
        richmsg.rich_message(prebuilt)


def test_richmessage_media_is_rejected_before_upload_or_send():
    client = UploadClient()
    blocks = richmsg.RichBlocks().media(photo=b"image bytes", caption="Cover")

    with pytest.raises(ValueError, match="RichMessage photo/document references are unsupported"):
        asyncio.run(richmsg.send_rich(client, "peer", blocks))

    assert client.uploaded_media == []
    assert client.requests == []


def test_document_and_markdown_media_references_are_rejected():
    document = types.InputDocument(id=7, access_hash=8, file_reference=b"ref")
    blocks = richmsg.RichBlocks(format="markdown").media(doc=document, caption="Manual")

    with pytest.raises(ValueError, match="RichMessage photo/document references are unsupported"):
        richmsg.rich_message_markdown(blocks)
    with pytest.raises(ValueError, match="RichMessage photo/document references are unsupported"):
        richmsg.rich_message_markdown("![logo](missing)")


def test_structured_native_photo_and_document_vectors_are_rejected():
    photo = types.InputPhoto(id=1, access_hash=2, file_reference=b"ref")
    document = types.InputDocument(id=3, access_hash=4, file_reference=b"ref")

    with pytest.raises(ValueError, match="RichMessage photo/document references are unsupported"):
        richmsg.rich_message_blocks([], photos=[photo])
    with pytest.raises(ValueError, match="RichMessage photo/document references are unsupported"):
        richmsg.rich_message_blocks([], documents=[document])


def test_raw_send_uses_send_message_request_and_keeps_mandatory_plain_text():
    client = Client()
    result = asyncio.run(
        richmsg.send_rich(
            client,
            "peer",
            richmsg.RichMarkup("<b>hello</b>", "html"),
            plain_text="hello",
            reply_to=5,
            buttons="buttons",
            link_preview=False,
        )
    )

    assert result.id == 77
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, SendMessageRequest)
    assert request.message == "hello"
    assert request.peer == types.InputPeerSelf()
    assert isinstance(request.rich_message, types.InputRichMessageHTML)
    assert request.reply_to.reply_to_msg_id == 5
    assert request.reply_markup == ("markup", "buttons")


def test_raw_send_returns_message_reference_from_updates_for_conversation():
    class UpdatesClient(Client):
        def __init__(self):
            super().__init__()
            self.message_ref = SimpleNamespace(id=88)

        async def __call__(self, request):
            self.requests.append(request)
            update = types.UpdateNewMessage(
                message=self.message_ref,
                pts=1,
                pts_count=1,
            )
            return SimpleNamespace(updates=[update])

    client = UpdatesClient()
    result = asyncio.run(
        richmsg.send_rich(client, "peer", richmsg.RichMarkup("prompt", "html"))
    )

    assert result is client.message_ref
    assert result.id == 88
    short_sent = types.UpdateShortSentMessage(id=91, pts=1, pts_count=1, date=None)
    assert richmsg._sent_message_reference(short_sent) == 91
    assert richmsg._sent_message_reference(types.UpdateMessageID(id=92)) == 92


def test_raw_edit_uses_layer230_edit_message_request():
    client = Client()
    asyncio.run(
        richmsg.edit_rich(
            client,
            "peer",
            7,
            richmsg.RichMarkup("**hello**", "markdown"),
            buttons="buttons",
        )
    )

    request = client.requests[0]
    assert isinstance(request, EditMessageRequest)
    assert request.id == 7
    assert request.message == "hello"
    assert isinstance(request.rich_message, types.InputRichMessageMarkdown)
    assert request.reply_markup == ("markup", "buttons")


@pytest.mark.parametrize(
    ("format", "markup", "expected"),
    [
        ("html", "<p><b>Hello</b></p>", "Hello"),
        ("markdown", "**Hello** world", "Hello world"),
    ],
)
def test_raw_markup_keeps_plain_request_text(format, markup, expected):
    client = Client()
    asyncio.run(richmsg.send_rich(client, "peer", markup, format=format))

    request = client.requests[0]
    assert request.message == expected
    assert isinstance(
        request.rich_message,
        types.InputRichMessageHTML if format == "html" else types.InputRichMessageMarkdown,
    )


def test_raw_inline_edit_uses_native_inline_edit_request(monkeypatch):
    class InlineID:
        dc_id = 2

    monkeypatch.setattr(richmsg._types, "InputBotInlineMessageID", InlineID, raising=False)
    monkeypatch.setattr(richmsg._types, "InputBotInlineMessageID64", InlineID, raising=False)
    client = Client()
    inline_id = InlineID()

    asyncio.run(
        richmsg.edit_rich(
            client,
            inline_id,
            None,
            richmsg.RichMarkup("<b>inline</b>", "html"),
        )
    )

    request = client.requests[0]
    assert isinstance(request, EditInlineBotMessageRequest)
    assert request.id is inline_id
    assert isinstance(request.rich_message, types.InputRichMessageHTML)


def test_engine_has_no_account_or_engine_policy_arguments():
    for function in (richmsg.rich_message, richmsg.send_rich, richmsg.edit_rich):
        parameters = inspect.signature(function).parameters
        assert "engine" not in parameters
        assert "is_bot" not in parameters
        assert "is_premium" not in parameters
    assert not hasattr(richmsg, "LegacyMessage")
    assert not hasattr(richmsg, "legacy_message")
    assert not hasattr(richmsg, "render_blocks_fallback_html")


def test_blocks_plain_text_preserves_readable_table_and_expandable_content():
    text = richmsg.blocks_to_plain_text(_blocks())
    assert "Summary" in text and "Name" in text and "plugins" in text
    assert "Details" in text and "More information" in text


def test_native_richblocks_emit_typed_lists_tables_and_nested_items():
    nested = (
        richmsg.RichBlocks(native=True).paragraph("Parent").list(["child"])
    )
    blocks = (
        richmsg.RichBlocks(native=True)
        .list(["first", nested])
        .list(["alpha", "beta"], ordered=True)
        .table(
            [["Name", "Value"], ["plugins", "42"]],
            caption="Summary",
            is_bordered=True,
            is_striped=True,
        )
    )

    message = richmsg.rich_message(blocks)

    assert isinstance(message, types.InputRichMessage)
    unordered, ordered, table = message.blocks
    assert isinstance(unordered, types.PageBlockList)
    assert isinstance(unordered.items[0], types.PageListItemText)
    assert unordered.items[0].text.text == "first"
    assert isinstance(unordered.items[1], types.PageListItemBlocks)
    assert isinstance(unordered.items[1].blocks[0], types.PageBlockParagraph)
    assert isinstance(unordered.items[1].blocks[1], types.PageBlockList)
    assert isinstance(ordered, types.PageBlockOrderedList)
    assert [item.num for item in ordered.items] == ["1", "2"]
    assert [item.text.text for item in ordered.items] == ["alpha", "beta"]
    assert isinstance(table, types.PageBlockTable)
    assert table.title.text == "Summary"
    assert table.bordered is True and table.striped is True
    assert isinstance(table.rows[0], types.PageTableRow)
    assert table.rows[0].cells[0].header is True
    assert table.rows[1].cells[0].header is False
    assert table.rows[1].cells[1].text.text == "42"
    assert bytes(message)


def test_native_table_rejects_fork_only_compact_flag():
    blocks = richmsg.RichBlocks(native=True).table([["cell"]], is_compact=True)

    with pytest.raises(ValueError, match="not part of the documented MTProto"):
        richmsg.rich_message(blocks)


def test_nested_richblocks_lists_render_in_markup_modes():
    blocks = richmsg.RichBlocks().list(
        ["top", richmsg.RichBlocks().paragraph("parent").list(["child"])]
    )

    html = richmsg.rich_markup_from_blocks(blocks, format="html").text
    markdown = richmsg.rich_markup_from_blocks(blocks, format="markdown").text

    assert "<li><p>parent</p><ul><li>child</li></ul></li>" in html
    assert "- top" in markdown and "- parent" in markdown and "- child" in markdown


def test_native_expandable_details_preserve_requested_open_state():
    blocks = richmsg.RichBlocks(native=True).expandable(
        "Details", "Body", open=True
    )

    message = richmsg.rich_message(blocks)

    assert isinstance(message.blocks[0], types.PageBlockDetails)
    assert message.blocks[0].open is True


def test_html_entity_conversion_preserves_astral_text_and_formatting():
    markup = richmsg.coerce_to_rich_markup("🍀 <b>bold</b>", parse_mode="html")

    assert markup is not None
    assert markup.format == "html"
    assert "🍀" in markup.text and "<b>bold</b>" in markup.text


def test_rich_send_does_not_fallback_to_plain_send_message_on_rpc_error():
    class RejectingClient(Client):
        def __init__(self):
            super().__init__()
            self.plain_fallback_calls = 0

        async def __call__(self, request):
            self.requests.append(request)
            raise RuntimeError("rich RPC rejected")

        async def send_message(self, *_args, **_kwargs):
            self.plain_fallback_calls += 1

    client = RejectingClient()

    with pytest.raises(RuntimeError, match="rich RPC rejected"):
        asyncio.run(
            richmsg.send_rich(client, "peer", richmsg.RichMarkup("body", "html"))
        )

    assert len(client.requests) == 1
    assert client.plain_fallback_calls == 0



def test_native_table_alignments_map_to_layer230_cell_flags():
    blocks = richmsg.RichBlocks(native=True).table(
        [["Left", "Center", "Right"]],
        alignments=["left", "center", "right"],
    )

    table = richmsg.rich_message(blocks).blocks[0]
    cells = table.rows[0].cells

    assert cells[0].align_center is None and cells[0].align_right is None
    assert cells[1].align_center is True and cells[1].align_right is None
    assert cells[2].align_center is None and cells[2].align_right is True


def test_native_richmessage_limits_reject_oversized_text_blocks_depth_and_tables():
    with pytest.raises(ValueError, match="32768 characters"):
        richmsg.rich_message_markdown("x" * (richmsg.MAX_RICH_MESSAGE_CHARS + 1))

    long_native = richmsg.RichBlocks(native=True).paragraph(
        "x" * (richmsg.MAX_RICH_MESSAGE_CHARS + 1)
    )
    with pytest.raises(ValueError, match="32768 characters"):
        richmsg.rich_message(long_native)

    too_wide = richmsg.RichBlocks(native=True).table([["x"] * 21])
    with pytest.raises(ValueError, match="20 columns"):
        richmsg.rich_message(too_wide)

    too_many = richmsg.RichBlocks(native=True).list([f"Item {i}" for i in range(500)])
    with pytest.raises(ValueError, match="500 blocks"):
        richmsg.rich_message(too_many)

    nested = richmsg.RichBlocks(native=True).paragraph("leaf")
    for _ in range(17):
        nested = richmsg.RichBlocks(native=True).list([nested])
    with pytest.raises(ValueError, match="16 nested levels"):
        richmsg.rich_message(nested)



def test_rich_edit_does_not_fallback_to_plain_edit_or_send_on_rpc_error():
    class RejectingClient(Client):
        def __init__(self):
            super().__init__()
            self.plain_edit_calls = 0
            self.plain_send_calls = 0

        async def __call__(self, request):
            self.requests.append(request)
            raise RuntimeError("rich edit RPC rejected")

        async def edit_message(self, *_args, **_kwargs):
            self.plain_edit_calls += 1

        async def send_message(self, *_args, **_kwargs):
            self.plain_send_calls += 1

    client = RejectingClient()

    with pytest.raises(RuntimeError, match="rich edit RPC rejected"):
        asyncio.run(
            richmsg.edit_rich(
                client, "peer", 7, richmsg.RichMarkup("<b>body</b>", "html")
            )
        )

    assert len(client.requests) == 1
    assert client.plain_edit_calls == 0
    assert client.plain_send_calls == 0


@pytest.mark.parametrize(
    "constructor_name",
    ["PageBlockTitle", "PageBlockHeader", "PageBlockSubtitle"],
)
def test_unsupported_semantic_page_blocks_become_bold_paragraphs(constructor_name):
    original = getattr(types, constructor_name)(
        text=types.TextPlain(text="Section title")
    )

    message = richmsg.rich_message_blocks([original])
    paragraph = message.blocks[0]

    assert isinstance(paragraph, types.PageBlockParagraph)
    assert isinstance(paragraph.text, types.TextBold)
    assert paragraph.text.text.text == "Section title"
    assert type(original).__name__ == constructor_name

    prebuilt = types.InputRichMessage(blocks=[original])
    normalized = richmsg.rich_message(prebuilt)
    assert isinstance(normalized.blocks[0], types.PageBlockParagraph)
    assert isinstance(normalized.blocks[0].text, types.TextBold)


def test_native_divider_anchor_and_footer_use_accepted_page_blocks():
    blocks = (
        richmsg.RichBlocks(native=True)
        .anchor("section-start")
        .paragraph("Visible content after the anchor")
        .divider()
        .footer("Page 1 of 1")
    )

    message = richmsg.rich_message(blocks)

    assert isinstance(message.blocks[0], types.PageBlockAnchor)
    assert message.blocks[0].name == "section-start"
    assert isinstance(message.blocks[1], types.PageBlockParagraph)
    assert isinstance(message.blocks[2], types.PageBlockDivider)
    assert isinstance(message.blocks[3], types.PageBlockFooter)
    assert message.blocks[3].text.text == "Page 1 of 1"
    assert bytes(message)


def test_native_checkbox_states_cover_ordered_unordered_and_nested_items():
    nested = richmsg.RichBlocks(native=True).paragraph("Nested task")
    blocks = (
        richmsg.RichBlocks(native=True)
        .list(["Finished", "Pending"], checked=[True, False])
        .list([nested], ordered=True, checked=[False])
    )

    unordered, ordered = richmsg.rich_message(blocks).blocks

    assert isinstance(unordered.items[0], types.PageListItemText)
    assert unordered.items[0].checkbox is True and unordered.items[0].checked is True
    assert unordered.items[1].checkbox is True and unordered.items[1].checked is False
    assert isinstance(ordered.items[0], types.PageListOrderedItemBlocks)
    assert ordered.items[0].num == "1"
    assert ordered.items[0].checkbox is True and ordered.items[0].checked is False
    assert isinstance(ordered.items[0].blocks[0], types.PageBlockParagraph)


def test_native_math_supports_inline_textmath_and_display_pageblockmath():
    blocks = (
        richmsg.RichBlocks(native=True)
        .math("x + y", display=False)
        .math("x^2 + y^2", display=True)
    )

    inline, display = richmsg.rich_message(blocks).blocks

    assert isinstance(inline, types.PageBlockParagraph)
    assert isinstance(inline.text, types.TextMath)
    assert inline.text.source == "x + y"
    assert isinstance(display, types.PageBlockMath)
    assert display.source == "x^2 + y^2"

    inline_phrase = richmsg.rich_message(
        richmsg.RichBlocks(native=True).math(
            "x^2", display=False, before="Area = ", after=" square units"
        )
    ).blocks[0].text
    assert isinstance(inline_phrase, types.TextConcat)
    assert isinstance(inline_phrase.texts[1], types.TextMath)
    assert inline_phrase.texts[0].text == "Area = "
    assert inline_phrase.texts[2].text == " square units"


def test_native_quote_pullquote_and_nested_quote_blocks_are_supported():
    body = richmsg.RichBlocks(native=True).paragraph("Nested quote content")
    blocks = (
        richmsg.RichBlocks(native=True)
        .quote("Quoted text", "Author", collapsed=True)
        .pullquote("Emphasized quote", "Source")
        .quote_blocks(body, "Nested source")
    )

    quote, pullquote, nested = richmsg.rich_message(blocks).blocks

    assert isinstance(quote, types.PageBlockBlockquote)
    assert quote.text.text == "Quoted text" and quote.caption.text == "Author"
    assert quote.collapsed is True
    assert isinstance(pullquote, types.PageBlockPullquote)
    assert pullquote.text.text == "Emphasized quote"
    assert isinstance(nested, types.PageBlockBlockquoteBlocks)
    assert isinstance(nested.blocks[0], types.PageBlockParagraph)
    assert nested.caption.text == "Nested source"


def test_rich_text_entities_are_embedded_not_sent_as_message_entities():
    markup = richmsg.coerce_to_rich_markup(
        "bold",
        formatting_entities=[types.MessageEntityBold(offset=0, length=4)],
    )
    assert markup is not None

    client = Client()
    asyncio.run(richmsg.send_rich(client, "peer", markup))
    request = client.requests[0]

    assert isinstance(request.rich_message, types.InputRichMessageHTML)
    assert "bold" in request.rich_message.html
    assert "<b>" in request.rich_message.html or "<strong>" in request.rich_message.html
    assert request.entities is None


def test_native_footer_can_be_built_without_other_blocks():
    footer = types.PageBlockFooter(text=types.TextPlain(text="Footer only"))

    message = richmsg.rich_message_blocks([footer])

    assert isinstance(message.blocks[0], types.PageBlockFooter)
    assert message.blocks[0].text.text == "Footer only"


def test_prebuilt_structured_richmessage_media_is_rejected():
    photo = types.InputPhoto(id=1, access_hash=2, file_reference=b"ref")
    prebuilt = types.InputRichMessage(
        blocks=[types.PageBlockParagraph(text=types.TextPlain(text="body"))],
        photos=[photo],
    )

    with pytest.raises(
        ValueError,
        match="RichMessage photo/document references are unsupported",
    ):
        richmsg.rich_message(prebuilt)

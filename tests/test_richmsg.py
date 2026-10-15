"""Focused unit tests for the dual-engine rich-message redesign.

All Telegram schema objects and parser calls are replaced with local recording
fakes.  These tests exercise construction and dispatch policy only; they never
open a Telegram connection.
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

import pyUltroid._core.richmsg as richmsg


class _Payload:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _RichHTML(_Payload):
    pass


class _RichMarkdown(_Payload):
    pass


class _InlineRich(_Payload):
    pass


class _RichPhoto(_Payload):
    pass


class _RichDocument(_Payload):
    pass


class _ReplyTo:
    def __init__(self, message_id):
        self.message_id = message_id


class _InlineMessageID(_Payload):
    pass


class _Request(_Payload):
    pass


class _Parser:
    def __init__(self):
        self.calls = []

    def parse(self, markup, *, mode):
        self.calls.append((markup, mode))
        return markup, [SimpleNamespace(kind="parsed", mode=mode)]


class _Client:
    def __init__(self, *, bot=False, premium=False):
        self._bot = bot
        self._premium = premium
        self.sent = []
        self.requests = []

    async def send_message(self, *args, **kwargs):
        self.sent.append((args, kwargs))
        return "legacy-result"

    async def get_input_entity(self, peer):
        return f"input:{peer}"

    def build_reply_markup(self, buttons):
        return ("markup", buttons)

    async def __call__(self, request):
        self.requests.append(request)
        return "raw-result"


@pytest.fixture
def rich_schema(monkeypatch):
    """Install local schema/parser fakes for deterministic, offline tests."""
    parser = _Parser()
    types = SimpleNamespace(
        InputRichMessageHTML=_RichHTML,
        InputRichMessageMarkdown=_RichMarkdown,
        InputBotInlineMessageRichMessage=_InlineRich,
        InputRichFilePhoto=_RichPhoto,
        InputRichFileDocument=_RichDocument,
        InputReplyToMessage=_ReplyTo,
        InputBotInlineMessageID=_InlineMessageID,
        InputBotInlineMessageID64=_InlineMessageID,
    )
    functions = SimpleNamespace(
        messages=SimpleNamespace(
            SendMessageRequest=_Request,
            EditMessageRequest=_Request,
            EditInlineBotMessageRequest=_Request,
        )
    )
    monkeypatch.setattr(richmsg, "_types", types)
    monkeypatch.setattr(richmsg, "_functions", functions)
    monkeypatch.setattr(richmsg, "_mdv3", parser)
    return parser


def _blocks(**policy):
    return (
        richmsg.RichBlocks(**policy)
        .header("Report", level=2)
        .paragraph("Body <safe>")
        .list(["one", "two"], ordered=True)
        .table([["Name", "Value"], ["plugins", "42"]], caption="Summary")
        .expandable("Details", "More information")
    )


@pytest.mark.parametrize(
    ("format", "expected"),
    [
        ("html", ("<b>Report</b>", "Body &lt;safe&gt;", "<pre><code>")),
        ("markdown", ("**Report**", "Body <safe>", "```")),
    ],
)
def test_legacy_engine_parses_html_and_markdown_through_mdv3(
    rich_schema, format, expected
):
    message = richmsg.legacy_message(_blocks(), format=format)

    assert isinstance(message, richmsg.LegacyMessage)
    assert message.format == format
    assert message.text == message.markup
    assert message.entities[0].kind == "parsed"
    assert rich_schema.calls == [(message.markup, format)]
    for fragment in expected:
        assert fragment in message.markup


def test_native_html_and_markdown_constructors_render_the_same_blocks(rich_schema):
    blocks = _blocks()

    html = richmsg.rich_message_html(blocks, rtl=True, noautolink=True)
    markdown = richmsg.rich_message_markdown(blocks, rtl=True, noautolink=True)

    assert isinstance(html, _RichHTML)
    assert html.rtl is True and html.noautolink is True
    assert "<h2>Report</h2>" in html.html
    assert "<table" in html.html and "<blockquote expandable>" in html.html

    assert isinstance(markdown, _RichMarkdown)
    assert markdown.rtl is True and markdown.noautolink is True
    assert "## Report" in markdown.markdown
    assert "| Name | Value |" in markdown.markdown
    assert "> Details" in markdown.markdown


def test_bot_inline_wrapper_keeps_the_native_payload_and_markup(rich_schema):
    reply_markup = object()

    wrapper = richmsg.bot_inline_rich_message(
        _blocks(),
        format="markdown",
        is_premium=False,
        is_bot=True,
        reply_markup=reply_markup,
    )

    assert isinstance(wrapper, _InlineRich)
    assert wrapper.reply_markup is reply_markup
    assert isinstance(wrapper.rich_message, _RichMarkdown)
    assert "## Report" in wrapper.rich_message.markdown


def test_explicit_account_flags_override_builder_policy(rich_schema):
    blocks = _blocks(is_premium=True, is_bot=True)

    forced_legacy = richmsg.rich_message(
        blocks,
        engine="auto",
        is_premium=False,
        is_bot=False,
    )
    forced_rich = richmsg.rich_message(
        _blocks(is_premium=False, is_bot=False),
        engine="auto",
        format="markdown",
        is_premium=True,
        is_bot=False,
    )

    assert isinstance(forced_legacy, richmsg.LegacyMessage)
    assert forced_legacy.format == "html"
    assert isinstance(forced_rich, _RichMarkdown)


def test_auto_selects_rich_for_premium_accounts_and_bots(rich_schema):
    premium = richmsg.rich_message(
        _blocks(), format="html", is_premium=True, is_bot=False
    )
    bot = richmsg.rich_message(
        _blocks(), format="markdown", is_premium=False, is_bot=True
    )

    assert isinstance(premium, _RichHTML)
    assert isinstance(bot, _RichMarkdown)


def test_auto_selects_legacy_for_normal_users_without_native_payload(rich_schema):
    normal = richmsg.rich_message(_blocks(), is_premium=False, is_bot=False)

    assert isinstance(normal, richmsg.LegacyMessage)
    assert normal.entities
    assert rich_schema.calls == [(normal.markup, "html")]


def test_send_rich_honours_explicit_normal_account_flags(rich_schema):
    client = _Client(bot=True, premium=True)

    result = asyncio.run(
        richmsg.send_rich(
            client,
            "peer",
            _blocks(),
            is_premium=False,
            is_bot=False,
            buttons="buttons",
        )
    )

    assert result == "legacy-result"
    assert client.requests == []
    assert len(client.sent) == 1
    args, kwargs = client.sent[0]
    assert args[0] == "peer"
    assert kwargs["parse_mode"] is None
    assert kwargs["buttons"] == "buttons"
    assert kwargs["formatting_entities"]


def test_send_rich_automatically_selects_native_for_premium_client(rich_schema):
    client = _Client(bot=False, premium=True)

    result = asyncio.run(
        richmsg.send_rich(
            client, "peer", _blocks(), format="html", is_premium=True, is_bot=False
        )
    )

    assert result == "raw-result"
    assert client.sent == []
    request = client.requests[0]
    assert request.peer == "input:peer"
    assert isinstance(request.rich_message, _RichHTML)
    assert request.entities


def test_existing_html_helper_and_default_builder_remain_usable(rich_schema):
    blocks = richmsg.RichBlocks().paragraph("legacy-compatible")

    native = richmsg.rich_message_html(blocks)
    selected = blocks.build(is_premium=False, is_bot=False)

    assert isinstance(native, _RichHTML)
    assert native.html == "<p>legacy-compatible</p>"
    assert isinstance(selected, richmsg.LegacyMessage)
    assert selected.text == "legacy-compatible"


def test_layer_227_capability_guards_are_not_used_for_the_layer_228_engine(rich_schema):
    source = inspect.getsource(richmsg)

    assert richmsg.has_native_rich_messages() is True
    assert "RichMessageUnavailable" not in source
    assert "getattr(_types" not in source


def test_generator_blocks_are_materialized_once_for_native_send(rich_schema):
    client = _Client(bot=True, premium=False)
    blocks = ({"type": "paragraph", "text": value} for value in ("one", "two"))

    asyncio.run(
        richmsg.send_rich(
            client,
            "peer",
            blocks,
            engine="rich",
            format="html",
            is_premium=False,
            is_bot=True,
        )
    )

    assert client.requests[0].rich_message.html == "<p>one</p><p>two</p>"


def test_explicit_rich_overrides_nonpremium_user_policy(rich_schema):
    payload = richmsg.rich_message(
        _blocks(), engine="rich", format="html", is_premium=False, is_bot=False
    )
    assert isinstance(payload, _RichHTML)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"engine": "typo"}, "engine must"), ({"format": "text"}, "format must")],
)
def test_invalid_policy_values_raise(rich_schema, kwargs, message):
    with pytest.raises(ValueError, match=message):
        richmsg.rich_message(_blocks(), **kwargs)


def test_media_builds_native_file_reference_and_legacy_caption(rich_schema):
    photo = object()
    blocks = richmsg.RichBlocks().media(photo=photo, caption="Preview")

    native = richmsg.rich_message_html(blocks)
    legacy = richmsg.legacy_message(blocks, format="html")

    assert native.files[0].photo is photo
    assert native.files[0].id == "m0"
    assert "Preview" in legacy.text


def test_edit_rich_uses_native_markdown_without_rerouting(rich_schema):
    client = _Client(bot=False, premium=False)
    client.edit_message = lambda *args, **kwargs: None

    result = asyncio.run(
        richmsg.edit_rich(
            client,
            "peer",
            7,
            _blocks(),
            engine="rich",
            format="markdown",
            is_premium=False,
            is_bot=False,
        )
    )

    assert result == "raw-result"
    assert client.requests[0].id == 7
    assert isinstance(client.requests[0].rich_message, _RichMarkdown)


def test_edit_rich_targets_inline_message_ids_with_native_rich_schema(rich_schema):
    client = _Client(bot=True)
    inline_id = _InlineMessageID(dc_id=2)

    result = asyncio.run(
        richmsg.edit_rich(
            client,
            inline_id,
            None,
            _blocks(),
            engine="rich",
            format="html",
            is_premium=False,
            is_bot=True,
            buttons=[["Back"]],
        )
    )

    assert result == "raw-result"
    assert len(client.requests) == 1
    request = client.requests[0]
    assert request.id is inline_id
    assert isinstance(request.rich_message, _RichHTML)
    assert request.reply_markup == ("markup", [["Back"]])

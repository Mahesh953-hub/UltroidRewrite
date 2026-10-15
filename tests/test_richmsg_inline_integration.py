"""Offline integration coverage for current rich-message redesign changes."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

import pyUltroid._core.inlineresults as inline
import pyUltroid._core.richmsg as richmsg
from pyUltroid.universal.tl import capabilities


class Payload:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)

    def __bytes__(self):
        return repr(sorted(self.__dict__.items())).encode()


class RichHTML(Payload):
    pass


class RichMarkdown(Payload):
    pass


class InlineRich(Payload):
    pass


class InlineResult(Payload):
    pass


class TextMessage(Payload):
    pass


class Parser:
    available = True

    @staticmethod
    def parse(text, mode=None):
        return text, [Payload(kind="entity", mode=mode)]


class Client:
    def build_reply_markup(self, buttons):
        return ("markup", buttons)


@pytest.fixture
def schema(monkeypatch):
    types = SimpleNamespace(
        InputRichMessageHTML=RichHTML,
        InputRichMessageMarkdown=RichMarkdown,
        InputBotInlineMessageRichMessage=InlineRich,
        InputBotInlineResult=InlineResult,
        InputBotInlineMessageText=TextMessage,
    )
    monkeypatch.setattr(inline, "_types", types)
    monkeypatch.setattr(inline, "_RICH_MD", Parser())
    return types


def make_results():
    # A supplied builder keeps this test at the wrapper integration boundary
    # without depending on Telethon's full InlineBuilder/client implementation.
    class Builder:
        _client = Client()

        async def article(self, **kwargs):
            message, entities = Parser.parse(kwargs["text"], mode=kwargs["parse_mode"])
            return InlineResult(
                id=kwargs["id"],
                type="article",
                title=kwargs["title"],
                description=kwargs["description"],
                url=kwargs["url"],
                send_message=TextMessage(
                    message=message,
                    entities=entities,
                    reply_markup=("markup", kwargs["buttons"]),
                ),
            )

    return inline.InlineResults(SimpleNamespace(builder=Builder()))


@pytest.mark.parametrize(
    ("format", "payload_attr", "payload_cls"),
    [("html", "html", RichHTML), ("markdown", "markdown", RichMarkdown)],
)
def test_article_rich_native_wire_payload(schema, format, payload_attr, payload_cls):
    result = asyncio.run(
        make_results().article_rich(
            "Title",
            "<b>body</b>",
            format=format,
            id="known",
            buttons="b",
            rtl=True,
            noautolink=True,
        )
    )
    assert isinstance(result, InlineResult)
    assert result.id == "known"
    assert result.title == "Title"
    assert isinstance(result.send_message, InlineRich)
    assert result.send_message.reply_markup == ("markup", "b")
    payload = result.send_message.rich_message
    assert isinstance(payload, payload_cls)
    assert getattr(payload, payload_attr) == "<b>body</b>"
    assert payload.rtl is True and payload.noautolink is True


def test_article_rich_legacy_fallback_preserves_article_fields(schema):
    result = asyncio.run(
        make_results().article_rich(
            "Title",
            "*body*",
            engine="legacy",
            format="markdown",
            id="legacy",
            description="Desc",
            url="https://x",
            buttons="b",
        )
    )
    assert isinstance(result, InlineResult)
    assert result.id == "legacy" and result.title == "Title"
    assert result.description == "Desc" and result.url == "https://x"
    assert isinstance(result.send_message, TextMessage)
    assert result.send_message.reply_markup == ("markup", "b")
    assert result.send_message.message == r"\*body\*"


def test_article_rich_validates_format(schema):
    with pytest.raises(ValueError, match="format must"):
        asyncio.run(make_results().article_rich("Title", "body", format="text"))


#: module names the ``_load_inline_stuff`` harness stubs into ``sys.modules``;
#: without a cleanup these leak into every later test (the fake
#: ``pyUltroid.fns.tools`` used to make ``pyUltroid.fns.fns`` collapse to
#: ``None`` for the rest of the suite).
_INLINE_STUB_NAMES = (
    "assistant",
    "assistant._cbs",
    "assistant.inlinestuff",
    # ``assistant.inlinestuff`` imports the REAL ``pyUltroid.fns`` while the
    # fake ``.tools`` is installed, so the real package gets cached with
    # ``fns = None`` (the import-chain except branch). Pop the package too,
    # or every later ``from pyUltroid.fns import fns`` gets the poison.
    "pyUltroid.fns",
    "pyUltroid.fns._fns",
    "pyUltroid.fns.helper",
    "pyUltroid.fns.misc",
    "pyUltroid.fns.tools",
    "pyUltroid._core.webupload",
)


@pytest.fixture(autouse=True)
def _cleanup_inline_stubs():
    """Pop the fake modules this file installs once each test is done."""
    yield
    import sys

    for name in _INLINE_STUB_NAMES:
        sys.modules.pop(name, None)


def _load_inline_stuff():
    import importlib.util
    import sys
    import types

    # Avoid `assistant.__init__`, which needs a configured Telegram client.
    assistant_package = types.ModuleType("assistant")
    assistant_package.__path__ = []
    assistant_package._ult_cache = {}
    assistant_package.in_pattern = lambda *args, **kwargs: lambda function: function
    assistant_package.callback = lambda *args, **kwargs: lambda function: function
    assistant_package.InlinePlugin = type(
        "InlinePlugin", (), {"update": staticmethod(lambda *args, **kwargs: None)}
    )
    sys.modules["assistant"] = assistant_package
    cbs = types.ModuleType("assistant._cbs")
    cbs.cb = cbs.cb_pattern = lambda *args, **kwargs: lambda function: function
    sys.modules["assistant._cbs"] = cbs
    # The helper's imports are unrelated to `_rich_article`; supply only the
    # names it needs so this remains an offline integration test.
    misc = types.ModuleType("pyUltroid.fns.misc")
    misc.google_search = lambda *args, **kwargs: None
    sys.modules["pyUltroid.fns.misc"] = misc
    tools = types.ModuleType("pyUltroid.fns.tools")
    tools._webupload_cache = {}
    tools.async_searcher = tools.get_ofox = tools.webupload_result = (
        lambda *args, **kwargs: None
    )
    sys.modules["pyUltroid.fns.tools"] = tools
    webupload = types.ModuleType("pyUltroid._core.webupload")
    webupload.MENU_SERVICES = {}
    webupload.SERVICE_LABELS = {}
    webupload.UploadResult = object
    sys.modules["pyUltroid._core.webupload"] = webupload
    spec = importlib.util.spec_from_file_location(
        "assistant.inlinestuff",
        str(Path(__file__).resolve().parents[1] / "assistant" / "inlinestuff.py"),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_rich_article_attaches_media_and_refinalizes_id(monkeypatch):
    stuff = _load_inline_stuff()

    class Result(Payload):
        def __init__(self):
            super().__init__(id="", thumb=None, content=None)

    class Results:
        def __init__(self):
            self.kwargs = None
            self.result = Result()

        async def article_rich(self, title, payload, **kwargs):
            self.kwargs = (title, payload, kwargs)
            return self.result

    monkeypatch.setattr(
        stuff,
        "rich_message_html",
        lambda blocks, rtl=False: SimpleNamespace(html="<p>rendered</p>"),
    )
    target = Results()
    result = asyncio.run(
        stuff._rich_article(
            target, "Title", object(), thumb="thumb", content="content", rtl=True
        )
    )
    title, payload, kwargs = target.kwargs
    assert title == "Title" and payload == "<p>rendered</p>"
    assert kwargs["engine"] == "rich" and kwargs["format"] == "html"
    assert (
        kwargs["is_premium"] is False
        and kwargs["is_bot"] is True
        and kwargs["rtl"] is True
    )
    assert result.thumb == "thumb" and result.content == "content"
    # `_rich_article` hashes the mutated result while its ID is still empty.
    expected = Result()
    expected.thumb, expected.content = "thumb", "content"
    assert result.id == hashlib.sha256(bytes(expected)).hexdigest()


def test_rich_article_keeps_explicit_id_when_media_changes(monkeypatch):
    stuff = _load_inline_stuff()

    class Result(Payload):
        def __init__(self):
            super().__init__(id="given", thumb=None, content=None)

    class Results:
        async def article_rich(self, *args, **kwargs):
            return Result()

    monkeypatch.setattr(
        stuff, "rich_message_html", lambda blocks, rtl=False: SimpleNamespace(html="x")
    )
    result = asyncio.run(
        stuff._rich_article(Results(), "Title", object(), id="given", thumb="thumb")
    )
    assert result.id == "given" and result.thumb == "thumb"


def test_pinned_layer_228_capability_contract(schema):
    # Changed contract must stay enabled even if callers do not feature-probe the schema.
    assert (
        capabilities(types_module=SimpleNamespace(), functions_module=None).rich_message
        is True
    )
    assert inline.has("article_rich") is True
    payload = richmsg.rich_message(
        "plain", engine="rich", format="html", is_bot=False, is_premium=False
    )
    # The installed pinned fork supplies the actual class; this verifies current runtime construction.
    from telethon.tl import types

    assert isinstance(payload, types.InputRichMessageHTML)
    assert payload.html == "<p>plain</p>"

"""Offline integration coverage for current rich-message redesign changes."""

from __future__ import annotations

import ast
import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import types as tl_types

import pyUltroid._core.inlineresults as inline
from pyUltroid._core import richmsg
from pyUltroid._core.events import InlineResultBuilder
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
    return types


def make_event():
    # A supplied builder keeps this test at the wrapper integration boundary
    # without depending on Telethon's full InlineBuilder/client implementation.
    class Builder:
        _client = Client()

        async def article(self, **kwargs):
            raise AssertionError("plain InlineBuilder.article must never be used")

    class Event:
        def __init__(self):
            self.builder = Builder()
            self.answers = []

        async def answer(self, results, **kwargs):
            self.answers.append((list(results), kwargs))
            return results

    return Event()


def make_results():
    return inline.InlineResults(make_event())


@pytest.mark.parametrize(
    ("format", "payload_attr", "payload_cls"),
    [
        ("html", "html", tl_types.InputRichMessageHTML),
        ("markdown", "markdown", tl_types.InputRichMessageMarkdown),
    ],
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


def test_article_rich_rejects_unverified_file_vectors(schema):
    from telethon import types

    photo = types.InputPhoto(id=12, access_hash=34, file_reference=b"ref")
    markup = richmsg.RichMarkup(
        '<p>Cover</p><img src="cover"/>',
        "html",
        (richmsg.RichFile("cover", photo),),
    )
    with pytest.raises(
        ValueError,
        match="RichMessage photo/document references are unsupported",
    ):
        asyncio.run(make_results().article_rich("Title", markup))


def test_article_rich_validates_format(schema):
    with pytest.raises(ValueError, match="format must"):
        asyncio.run(make_results().article_rich("Title", "body", format="text"))


def test_actual_event_builder_exposes_and_serializes_rich_articles(schema):
    event = make_event()
    builder = InlineResultBuilder(event)
    assert callable(builder.article_rich)
    result = asyncio.run(builder.article_rich("Title", "<b>rich body</b>", id="known"))
    assert isinstance(result, InlineResult)
    assert result.id == "known"
    assert isinstance(result.send_message, InlineRich)
    assert isinstance(result.send_message.rich_message, tl_types.InputRichMessageHTML)
    assert result.send_message.rich_message.html == "<b>rich body</b>"
    assert bytes(result)


def test_event_builder_article_alias_also_uses_native_rich(schema):
    event = make_event()
    result = asyncio.run(
        InlineResultBuilder(event).article(
            title="Title", text="literal body", parse_mode=None
        )
    )
    assert isinstance(result.send_message, InlineRich)
    assert isinstance(result.send_message.rich_message, tl_types.InputRichMessageHTML)
    assert result.send_message.rich_message.html == "literal body"


def test_inline_error_notice_is_answered_as_a_native_rich_article(schema):
    from pyUltroid._misc._assistant import _answer_inline_error

    event = make_event()
    asyncio.run(_answer_inline_error(event, "@bot failing-query", "Traceback line"))
    result = event.answers[0][0][0]
    assert isinstance(result, InlineResult)
    assert isinstance(result.send_message, InlineRich)
    payload = result.send_message.rich_message
    assert isinstance(payload, tl_types.InputRichMessageHTML)
    assert "@bot failing-query" in payload.html
    assert "Traceback line" in payload.html


def test_raw_inline_builder_articles_are_media_only():
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for source_root in (root / "plugins", root / "assistant", root / "pyUltroid"):
        for path in source_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            scopes = [
                node
                for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            for scope in scopes:
                nodes = list(ast.walk(scope))
                raw_builder_names = set()
                for node in nodes:
                    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                        continue
                    value = node.value
                    is_raw_builder = (
                        isinstance(value, ast.Attribute) and value.attr == "builder"
                    ) or (
                        isinstance(value, ast.Call)
                        and isinstance(value.func, ast.Name)
                        and value.func.id == "InlineBuilder"
                    )
                    if is_raw_builder:
                        targets = (
                            node.targets
                            if isinstance(node, ast.Assign)
                            else [node.target]
                        )
                        raw_builder_names.update(
                            target.id
                            for target in targets
                            if isinstance(target, ast.Name)
                        )
                for node in nodes:
                    if not isinstance(node, ast.Call):
                        continue
                    func = node.func
                    if not isinstance(func, ast.Attribute) or func.attr != "article":
                        continue
                    value = func.value
                    raw_article = (
                        (isinstance(value, ast.Attribute) and value.attr == "builder")
                        or (
                            isinstance(value, ast.Name)
                            and value.id in raw_builder_names
                        )
                        or (
                            isinstance(value, ast.Call)
                            and isinstance(value.func, ast.Name)
                            and value.func.id == "InlineBuilder"
                        )
                    )
                    if raw_article and not any(
                        keyword.arg == "include_media" for keyword in node.keywords
                    ):
                        offenders.append(f"{path.relative_to(root)}:{node.lineno}")
    assert not offenders, (
        "text-only inline articles must use the rich-message builder; raw article "
        "calls are permitted only for media results: " + ", ".join(offenders)
    )


def test_empty_query_alive_card_uses_the_native_rich_article_path():
    root = Path(__file__).resolve().parents[1]
    source = root / "plugins" / "_inline.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "inline_alive"
    )
    calls = [
        node.func.attr
        for node in ast.walk(handler)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert "article_rich" in calls
    assert "photo" not in calls


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

    file_ref = richmsg.RichFile(
        "cover",
        tl_types.InputPhoto(id=90, access_hash=91, file_reference=b"ref"),
    )
    expected_markup = richmsg.RichMarkup(
        '<p>rendered</p><img src="cover"/>', "html", (file_ref,)
    )
    monkeypatch.setattr(
        stuff,
        "rich_markup_from_blocks",
        lambda blocks, format: expected_markup,
    )
    target = Results()
    result = asyncio.run(
        stuff._rich_article(
            target, "Title", object(), thumb="thumb", content="content", rtl=True
        )
    )
    title, payload, kwargs = target.kwargs
    assert title == "Title" and isinstance(payload, richmsg.RichMarkup)
    assert payload == expected_markup
    assert kwargs["format"] == "html" and kwargs["rtl"] is True
    assert not {"engine", "is_bot", "is_premium"}.intersection(kwargs)
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
        stuff,
        "rich_markup_from_blocks",
        lambda blocks, format: richmsg.RichMarkup("x", format),
    )
    result = asyncio.run(
        stuff._rich_article(Results(), "Title", object(), id="given", thumb="thumb")
    )
    assert result.id == "given" and result.thumb == "thumb"


def test_pinned_layer_230_rich_message_contract(schema):
    from telethon import __version__ as telethon_version
    from telethon.tl.alltlobjects import LAYER

    assert telethon_version == "1.45.0"
    assert LAYER == 230
    assert not hasattr(
        capabilities(types_module=SimpleNamespace(), functions_module=None),
        "rich_message",
    )
    assert not hasattr(inline.InlineResults, "has")
    payload = richmsg.rich_message("plain", format="html")
    # The installed pinned fork supplies the actual class; this verifies current runtime construction.
    from telethon.tl import types

    assert isinstance(payload, types.InputRichMessageHTML)
    assert payload.html == "plain"


def test_article_rich_rejects_media_inside_prebuilt_native_payload(schema):
    photo = tl_types.InputPhoto(id=1, access_hash=2, file_reference=b"ref")
    rich_file = tl_types.InputRichFilePhoto(id="cover", photo=photo)
    payload = tl_types.InputRichMessageHTML(
        html='<img src="cover"/>',
        files=[rich_file],
    )

    with pytest.raises(
        ValueError,
        match="RichMessage photo/document references are unsupported",
    ):
        asyncio.run(make_results().article_rich("Title", payload))

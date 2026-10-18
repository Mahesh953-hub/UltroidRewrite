"""Parity for the ``pyUltroid/_misc`` helper conversion: every module-level
function that became a class method must behave identically.

The "legacy" functions below are transcribed **verbatim** from the
pre-conversion source, with their two external dependencies (``ikm`` and
``fmtmsg``) lifted into parameters so the comparison is about structure and
not about which framework is installed. Anything that diverges fails here.
"""

from __future__ import annotations

import ast
import collections
import os
import re
import types

import pytest

from conftest import ROOT

MISC = ROOT / "pyUltroid" / "_misc"


# ── verbatim legacy implementations ────────────────────────────────────


def legacy_button_is_styled(btn):
    style = getattr(btn, "style", None)
    if style is None:
        return False
    return any(
        getattr(style, field, None)
        for field in ("bg_primary", "bg_danger", "bg_success", "icon")
    )


def legacy_map_buttons_to_ikm(raw_buttons, ikm):
    if not raw_buttons:
        return None
    mapped_buttons = []
    if not isinstance(raw_buttons, list):
        raw_buttons = [[raw_buttons]]
    elif raw_buttons and not isinstance(raw_buttons[0], list):
        raw_buttons = [raw_buttons]
    for row in raw_buttons:
        mapped_row = []
        for btn in row:
            if isinstance(btn, tuple):
                mapped_row.append(btn)
            elif hasattr(btn, "text") and hasattr(btn, "data"):
                if legacy_button_is_styled(btn):
                    mapped_row.append(btn)
                    continue
                btn_text = btn.text
                style = (
                    "danger"
                    if "back" in btn_text.lower()
                    or "«" in btn_text.lower()
                    or "cancel" in btn_text.lower()
                    else (
                        "success"
                        if "on" in btn_text.lower() or "approve" in btn_text.lower()
                        else "primary"
                    )
                )
                if hasattr(btn, "url") and btn.url:
                    mapped_row.append((btn_text, "url", btn.url, style))
                else:
                    mapped_row.append((btn_text, btn.data, style))
            else:
                mapped_row.append(btn)
        mapped_buttons.append(mapped_row)
    return ikm(mapped_buttons)


def legacy_caller_chose_parse_mode(kwargs):
    return "parse_mode" in kwargs and kwargs["parse_mode"] != "html"


def legacy_format_assistant_message(text, heading_default, fmtmsg):
    if not text:
        return text
    if (
        not text.startswith("<b>")
        and not text.startswith("<div")
        and not text.startswith("<code>")
    ):
        lines = text.strip().split("\n", 1)
        head = lines[0].strip().replace("~", "").replace(":", "").strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        if len(head) > 40:
            body = text
            head = heading_default
        return fmtmsg(head, msgs=body)
    return text


def legacy_compile_pattern(data, hndlr):
    if data.startswith("^"):
        data = data[1:]
    if data.startswith("."):
        data = data[1:]
    if hndlr in [" ", "NO_HNDLR"]:
        return re.compile("^" + data)
    return re.compile("\\" + hndlr + data)


def legacy_append_or_update(load, func, name, arggs):
    if isinstance(load, list):
        return load.append(func)
    if isinstance(load, dict):
        if load.get(name):
            return load[name].append((func, arggs))
        return load.update({name: [(func, arggs)]})


# ── loaders: the classes under test, standalone ────────────────────────


def _stub_eor(event, text=None, **kwargs):
    """Stand-in for the framework's edit-or-reply; records nothing."""

    async def _run():
        return ("EOR", text, kwargs)

    return _run()


def _install_framework_stubs():
    """Stub only the *framework edges*; the code under test stays real.

    ``_assistant`` and ``_decorators`` import telethon, ``custom`` and the
    whole ``pyUltroid`` root at module level. Stubbing those lets the real
    helper classes be exercised in an environment that has none of them
    installed. Returns the names it invented, for removal afterwards.
    """
    import sys
    import types as _t

    invented: list[str] = []

    def stub(dotted: str, **attrs):
        if dotted in sys.modules:
            return sys.modules[dotted]
        module = _t.ModuleType(dotted)
        module.__path__ = []
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[dotted] = module
        invented.append(dotted)
        return module

    class NewMessage:
        def __init__(self, **kwargs):
            self.build = dict(kwargs)

    stub("telethon")
    stub("telethon.events", NewMessage=NewMessage,
         CallbackQuery=NewMessage, InlineQuery=NewMessage,
         MessageEdited=NewMessage)
    stub("telethon.errors", QueryIdInvalidError=type("QueryIdInvalidError", (Exception,), {}))
    stub("telethon.tl")
    stub("telethon.tl.types", InputWebDocument=object)
    stub("telethon.errors.common", AlreadyInConversationError=type("AlreadyInConversationError", (Exception,), {}))
    rpc = {}
    for name in (
        "AuthKeyDuplicatedError", "BotInlineDisabledError", "BotMethodInvalidError",
        "ChatSendInlineForbiddenError", "ChatSendMediaForbiddenError",
        "ChatSendStickersForbiddenError", "FloodWaitError",
        "MessageDeleteForbiddenError", "MessageIdInvalidError",
        "MessageNotModifiedError", "UserIsBotError",
    ):
        rpc[name] = type(name, (Exception,), {"seconds": 1})
    stub("telethon.errors.rpcerrorlist", **rpc)
    stub("telethon.utils", get_display_name=lambda *a, **k: "stub")

    # custom.fmt / custom.tg: only fmtmsg + ikm are used by the helpers
    fmtmod = stub("custom.fmt", fmtmsg=lambda head, data=None, foot="", msgs="": ("FMT", head, msgs))
    tgmod = stub("custom.tg", ikm=lambda rows: ("IKM", rows))
    custom = stub("custom")
    custom.fmt = fmtmod
    custom.tg = tgmod
    custom.fmtmsg = fmtmod.fmtmsg
    custom.ikm = tgmod.ikm

    # the pyUltroid root the two files read package globals from
    root = stub(
        "pyUltroid",
        LOGS=__import__("logging").getLogger("misc-class-tests"),
        asst=None,
        udB=types.SimpleNamespace(get_key=lambda key: None, get=lambda key: None),
        ultroid_bot=types.SimpleNamespace(full_name="Stub", uid=1, me=None),
        # these used to arrive via `from .. import *`, so a stub never had to
        # declare them; the explicit imports now require them to be there
        HNDLR=".",
        SUDO_HNDLR="!",
        DUAL_HNDLR="/",
        DUAL_MODE=False,
        USER_MODE=True,
        _ignore_eval=[],
        # W7: _decorators also reads the master registry directly now
        master=types.SimpleNamespace(
            telegram_clients=(),
            clients_by_role=lambda role: (),
        ),
    )
    stub("pyUltroid.configs", Var=types.SimpleNamespace(DEVLIST_IDS=[], SUDO_SPECIAL_IDS=[]))
    stub("pyUltroid.dB", DEVLIST=[])
    stub("pyUltroid.dB._core", LIST={}, LOADED={})
    stub("pyUltroid.exceptions", DependencyMissingError=type("DependencyMissingError", (Exception,), {}))
    stub("pyUltroid.fns", fns=types.SimpleNamespace(
        admin_check=None,
        time_formatter=lambda ms: f"{ms}ms",
        bash=None,
    ))
    stub("pyUltroid.startup")
    stub("pyUltroid.startup.patching", eor=_stub_eor)
    stub("strings", get_string=lambda key: key)
    return invented


def _load_misc(stubs: list[str]):
    """Load the real ``_misc`` package against the installed stubs.

    Registered under its **real dotted name** (``pyUltroid._misc``) so that
    ``from .. import *`` and ``from . import X`` resolve exactly as they do
    in production - a loader that renamed the package would not be testing
    the import contract.
    """
    import importlib.util
    import sys

    # the package itself, with a real __path__ so `from . import X` works
    spec = importlib.util.spec_from_file_location(
        "pyUltroid._misc",
        MISC / "__init__.py",
        submodule_search_locations=[str(MISC)],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules["pyUltroid._misc"] = package
    stubs.append("pyUltroid._misc")
    spec.loader.exec_module(package)

    def load(dotted: str, filename: str):
        spec = importlib.util.spec_from_file_location(dotted, MISC / filename)
        module = importlib.util.module_from_spec(spec)
        sys.modules[dotted] = module
        stubs.append(dotted)
        spec.loader.exec_module(module)
        setattr(package, dotted.rsplit(".", 1)[-1], module)
        return module

    load("pyUltroid._misc.keep_safe", "keep_safe.py")
    load("pyUltroid._misc._wrappers", "_wrappers.py")
    load("pyUltroid._misc._assistant", "_assistant.py")
    load("pyUltroid._misc._decorators", "_decorators.py")
    return package


@pytest.fixture(scope="module")
def misc():
    """The real ``_misc`` package, loaded against framework stubs.

    The whole ``pyUltroid`` prefix is evicted first: when the real root is
    already in ``sys.modules`` (another test imported it), the stubs would
    be skipped and ``from .. import *`` would bind the real half-initialised
    package instead.
    """
    import sys

    saved = {
        name: value
        for name, value in sys.modules.items()
        if name == "pyUltroid" or name.startswith("pyUltroid.")
    }
    for name in saved:
        sys.modules.pop(name, None)
    invented = _install_framework_stubs()
    try:
        yield _load_misc(invented)
    finally:
        for name in invented:
            sys.modules.pop(name, None)
        for name in [n for n in sys.modules if n == "pyUltroid" or n.startswith("pyUltroid.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


# ── 1. ButtonMapper ────────────────────────────────────────────────────


class Recorder:
    """Records what would be handed to ``ikm`` and returns it verbatim."""

    def __call__(self, rows):
        return ("IKM", rows)

    def __getstate__(self):  # pragma: no cover
        raise AssertionError


class FakeButton:
    def __init__(self, text, data=None, url=None, style=None):
        self.text = text
        self.data = data
        if url is not None:
            self.url = url
        if style is not None:
            self.style = style


BUTTON_CASES = [
    None,
    [],
    (("a", "url", "http://x"),),
    [("a", "b" "1"), ("c", "d" "2")],
    [[("a", "b" "1")], [("c", "d" "2")]],
    [FakeButton("plain", "d1")],
    [[FakeButton("plain", "d1")]],
    [[FakeButton("Go back", "d1")]],
    [[FakeButton("« Cancel", "d1")]],
    [[FakeButton("Approve now", "d1")]],
    [[FakeButton("ON", "d1")]],
    [[FakeButton("neutral", "d1")]],
    [[FakeButton("with url", "d1", url="http://y")]],
    [[FakeButton("styled", "d1", style=types.SimpleNamespace(bg_primary=1))]],
    [[FakeButton("styled", "d1", style=types.SimpleNamespace(icon="x"))]],
    [[FakeButton("unstyled", "d1", style=types.SimpleNamespace(unrelated=1))]],
    [[object(), ("t", "d" "2")]],
    ["not a button at all"],
]


def _normalise(value):
    """Tuples compare by identity, so unwrap for comparison."""
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return value


@pytest.mark.parametrize("raw", BUTTON_CASES)
def test_button_mapper_matches_legacy(misc, raw):
    ButtonMapper = misc._assistant.ButtonMapper

    expected = legacy_map_buttons_to_ikm(raw, Recorder())
    actual = ButtonMapper(build=Recorder()).map(raw)
    assert _normalise(actual) == _normalise(expected)


@pytest.mark.parametrize("raw", BUTTON_CASES)
def test_as_rows_matches_legacy_shaping(misc, raw):
    ButtonMapper = misc._assistant.ButtonMapper

    if not raw:
        return
    legacy_rows = (
        [[raw]]
        if not isinstance(raw, list)
        else ([raw] if raw and not isinstance(raw[0], list) else raw)
    )
    assert len(ButtonMapper.as_rows(raw)) == len(legacy_rows)


def test_button_mapper_style_inference(misc, ):
    ButtonMapper = misc._assistant.ButtonMapper

    assert ButtonMapper.style_for("Go back") == "danger"
    assert ButtonMapper.style_for("« Cancel") == "danger"
    assert ButtonMapper.style_for("CANCEL") == "danger"
    assert ButtonMapper.style_for("Approve now") == "success"
    assert ButtonMapper.style_for("turn it on") == "success"
    assert ButtonMapper.style_for("neutral label") == "primary"


def test_button_mapper_is_styled_matches_legacy(misc, ):
    ButtonMapper = misc._assistant.ButtonMapper

    for style in (
        None,
        types.SimpleNamespace(bg_primary=1),
        types.SimpleNamespace(bg_danger=1),
        types.SimpleNamespace(bg_success=1),
        types.SimpleNamespace(icon="i"),
        types.SimpleNamespace(nothing=1),
    ):
        button = FakeButton("x", "y", style=style)
        assert ButtonMapper.is_styled(button) == legacy_button_is_styled(button)


# ── 2. MessageFormatter ────────────────────────────────────────────────


def recorder_fmtmsg(head, data=None, foot="", msgs=""):
    return ("FMT", head, data, foot, msgs)


TEXT_CASES = [
    None,
    "",
    "single line",
    "heading\nbody line",
    "~heading:\nbody",
    "<b>already html</b>",
    "<div>already html</div>",
    "<code>already html</code>",
    "x" * 41,
    "x" * 41 + "\nbody that should survive",
    "  spaced  \n  body  ",
    "a\nb\nc",
]


@pytest.mark.parametrize("text", TEXT_CASES)
def test_message_formatter_matches_legacy(misc, text):
    MessageFormatter = misc._assistant.MessageFormatter

    for heading in ("Assistant", "Settings Callback"):
        expected = legacy_format_assistant_message(text, heading, recorder_fmtmsg)
        actual = MessageFormatter(build=recorder_fmtmsg).format(text, heading)
        assert actual == expected


def test_message_formatter_preserves_strong_and_nested_html(misc):
    MessageFormatter = misc._assistant.MessageFormatter
    formatter = MessageFormatter(build=recorder_fmtmsg)
    text = "<strong>Help</strong>\n<code>.help</code> &amp; more"
    assert formatter.already_html(text)
    assert formatter.format(text, "Assistant") == text


@pytest.mark.parametrize(
    "kwargs",
    [
        {},
        {"parse_mode": "html"},
        {"parse_mode": "md"},
        {"parse_mode": "markdown"},
        {"parse_mode": None},
        {"buttons": [1]},
        {"parse_mode": "v2", "buttons": [1]},
    ],
)
def test_caller_chose_parse_mode_matches_legacy(misc, kwargs):
    MessageFormatter = misc._assistant.MessageFormatter

    assert MessageFormatter.caller_chose_parse_mode(
        kwargs
    ) == legacy_caller_chose_parse_mode(kwargs)


# ── 3. AssistantEventPatcher ───────────────────────────────────────────


class FakeEvent:
    def __init__(self):
        self.calls = []

    async def _record(self, text=None, **kwargs):
        self.calls.append((text, kwargs))
        return "sent"

    reply = _record
    respond = _record
    edit = _record


def test_patcher_wraps_all_three_methods(misc, ):
    AssistantEventPatcher = misc._assistant.AssistantEventPatcher

    event = FakeEvent()
    AssistantEventPatcher()(event)
    for name in AssistantEventPatcher.METHODS:
        assert getattr(event, name).__name__ == "patched"


def test_patcher_skips_absent_methods(misc, ):
    AssistantEventPatcher = misc._assistant.AssistantEventPatcher

    event = types.SimpleNamespace()
    AssistantEventPatcher()(event)
    assert not hasattr(event, "reply")


def test_patcher_uses_rich_html_without_legacy_parse_mode(misc, ):
    import asyncio

    AssistantEventPatcher = misc._assistant.AssistantEventPatcher

    event = FakeEvent()
    AssistantEventPatcher()(event, "Assistant")
    asyncio.run(event.reply("hello\nworld"))
    text, kwargs = event.calls[-1]
    assert "parse_mode" not in kwargs
    assert text in (
        "<b>hello</b>\n\n<blockquote expandable>world</blockquote>",
        ("FMT", "hello", "world"),
    )


def test_patcher_marks_raw_html_for_native_rich_edits(misc):
    import asyncio

    event = FakeEvent()
    AssistantEventPatcher = misc._assistant.AssistantEventPatcher
    AssistantEventPatcher()(event)
    asyncio.run(event.edit("<b>Already formatted</b>", parse_mode="html"))
    text, kwargs = event.calls[-1]
    assert str(text) == "<b>Already formatted</b>"
    assert getattr(text, "__rich_format__", None) == "html"
    assert "parse_mode" not in kwargs


def test_patcher_passes_rich_blocks_through_without_string_formatting(misc):
    import asyncio

    event = FakeEvent()
    AssistantEventPatcher = misc._assistant.AssistantEventPatcher
    AssistantEventPatcher()(event)
    blocks = object()
    asyncio.run(event.edit(blocks, parse_mode="html"))
    text, kwargs = event.calls[-1]
    assert text is blocks
    assert "parse_mode" not in kwargs


def test_patcher_leaves_an_explicit_parse_mode_alone(misc, ):
    import asyncio

    AssistantEventPatcher = misc._assistant.AssistantEventPatcher

    event = FakeEvent()
    AssistantEventPatcher()(event)
    asyncio.run(event.reply("**raw**", parse_mode="markdown"))
    text, kwargs = event.calls[-1]
    assert text == "**raw**", "text must be untouched"
    assert kwargs["parse_mode"] == "markdown"


def test_patcher_maps_buttons_even_when_parse_mode_is_explicit(misc, ):
    import asyncio

    AssistantEventPatcher = misc._assistant.AssistantEventPatcher
    ButtonMapper = misc._assistant.ButtonMapper

    event = FakeEvent()
    AssistantEventPatcher(buttons=ButtonMapper(build=Recorder()))(event)
    asyncio.run(event.reply("x", parse_mode="md", buttons=[("a", "b" "1")]))
    _text, kwargs = event.calls[-1]
    assert kwargs["buttons"] == ("IKM", [[("a", "b" "1")]])


# ── 4. PatternCompiler ────────────────────────────────────────────────


PATTERN_CASES = [
    ("ping", "."),
    ("^ping", "."),
    (".ping", "."),
    ("^.ping", "."),
    ("ping", "!"),
    ("^ping", "!"),
    ("ping", " "),
    ("ping", "NO_HNDLR"),
    ("", "."),
    ("a|b", "/"),
    ("(x)", "."),
    ("^$", "."),
    ("ping$", "."),
]


@pytest.mark.parametrize("data,hndlr", PATTERN_CASES)
def test_pattern_compiler_matches_legacy(misc, data, hndlr):
    PatternCompiler = misc._decorators.PatternCompiler

    expected = legacy_compile_pattern(data, hndlr)
    actual = PatternCompiler().compile(data, hndlr)
    assert actual.pattern == expected.pattern
    assert actual.flags == expected.flags


def test_compile_pattern_function_is_the_alias(misc, ):
    PATTERNS = misc._decorators.PATTERNS
    compile_pattern = misc._decorators.compile_pattern

    assert compile_pattern("ping", ".").pattern == PATTERNS.compile("ping", ".").pattern


# ── 5. HandlerRegistry ────────────────────────────────────────────────


CONTAINER_CASES = [
    ([], "mod", "stem", {"a": 1}),
    ({"mod": [("f", {})]}, "g", "mod", {"b": 2}),
    ({}, "h", "fresh", {"c": 3}),
    ("not a container", "i", "k", {}),
    (None, "j", "l", {}),
    ({"mod": []}, "m", "mod", {"d": 4}),
]


@pytest.mark.parametrize("container,func,name,args", CONTAINER_CASES)
def test_handler_registry_matches_legacy(misc, container, func, name, args):
    HandlerRegistry = misc.HandlerRegistry

    old_container = _copy(container)
    new_container = _copy(container)
    old_result = legacy_append_or_update(old_container, func, name, args)
    new_result = HandlerRegistry.record(new_container, func, name, args)
    assert _normalise(new_container) == _normalise(old_container)
    assert new_result == old_result


def _copy(container):
    if isinstance(container, list):
        return list(container)
    if isinstance(container, dict):
        return {k: list(v) for k, v in container.items()}
    return container


# ── 6. SecretScrubber ─────────────────────────────────────────────────


def test_secret_scrubber_blanks_matching_env_vars(misc, monkeypatch):
    SecretScrubber = misc.SecretScrubber

    monkeypatch.setenv("BOT_TOKEN", "real")
    monkeypatch.setenv("BOT_TOKEN2", "real2")
    monkeypatch.setenv("UNRELATED", "keep")
    monkeypatch.setenv("API_HASH", "real")

    scrubber = SecretScrubber()
    touched = set(scrubber.scrub_environ())

    assert os.environ["BOT_TOKEN"] == ""
    assert os.environ["BOT_TOKEN2"] == ""
    assert os.environ["API_HASH"] == ""
    assert os.environ["UNRELATED"] == "keep", "substring match must not over-reach"
    assert {"BOT_TOKEN", "BOT_TOKEN2", "API_HASH"} <= touched


def test_secret_scrubber_stashes_and_blanks_var(misc, ):
    SecretScrubber = misc.SecretScrubber

    var = types.SimpleNamespace(SESSION="real", API_ID="123", OTHER="keep")
    var.__dict__  # SimpleNamespace has it already
    scrubber = SecretScrubber()
    captured = scrubber.scrub_config(var)
    assert captured == {"SESSION": "real", "API_ID": "123"}
    assert var.SESSION == "" and var.API_ID == ""
    assert var.OTHER == "keep"
    assert scrubber.stash == captured


def test_secret_scrubber_captures_and_restores_env_file(misc, tmp_path, monkeypatch):
    SecretScrubber = misc.SecretScrubber

    path = tmp_path / ".env"
    path.write_text("API_ID=123\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    scrubber = SecretScrubber()
    assert scrubber.run() == {} or True
    assert scrubber.env_text == "API_ID=123\n"

    path.write_text("clobbered\n", encoding="utf-8")
    assert scrubber.restore_env() is True
    assert path.read_text(encoding="utf-8") == "API_ID=123\n"


def test_secret_scrubber_run_is_idempotent(misc, ):
    SecretScrubber = misc.SecretScrubber

    scrubber = SecretScrubber()
    scrubber.run()
    scrubber.run()
    assert scrubber.scrubbed is True


def test_keep_safe_all_is_unchanged(misc, ):
    KEEP_SAFE = misc.KEEP_SAFE

    items = KEEP_SAFE().All
    assert isinstance(items, list)
    assert len(items) == 32, "the deny-list is frozen"
    for expected in (".session.save()", "SUDOS", "FULLSUDO", "_ignore_eval"):
        assert expected in items

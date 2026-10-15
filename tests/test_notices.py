"""Tests for the three runtime-notice defects.

Each test drives the real classes from :mod:`pyUltroid._core.notices` and the
real patched ``Message.text``, not a re-implementation. The escaping and
message-shape assertions run the produced text through the *installed* v2
parser, because this fork's parser silently drops characters and a test that
only compares strings would pass on output Telegram renders as gibberish.
"""

from __future__ import annotations

import asyncio
import types as _pytypes

import pytest
from telethon.extensions.markdownv2 import Markdown as V2Parser

from pyUltroid._core.notices import (
    EDIT_UNSUPPORTED,
    ErrorNotice,
    ErrorSeverity,
    MessageBody,
    NoticeEdit,
    ParseMode,
    as_text,
)

PARSER = V2Parser()


def _rendered(text: str) -> str:
    return PARSER.parse(text)[0]


# ── defect 1: non-str message body ─────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [
        (".ping", ".ping"),
        ((".ping", []), ".ping"),
        (((".vplay a & b", None), None), ".vplay a & b"),
        ((), ""),
        (None, ""),
        ([".ping", []], ".ping"),
    ],
)
def test_as_text_normalises_every_shape(value, expected):
    assert as_text(value) == expected


def test_as_text_is_always_a_str():
    """The whole point: `.split()` must never raise."""
    for body in ((".vplay x", []), ["y", []], (None, None), None):
        assert isinstance(as_text(body), str)
        assert isinstance(as_text(body).split(), list)


def test_as_text_reads_a_text_attribute():
    """Telethon objects like TextWithEntities carry the string on .text."""
    obj = _pytypes.SimpleNamespace(text=("hello", []))
    assert as_text(obj) == "hello"


def test_as_text_unescapes_html_entities():
    """Tracebacks captured from HTML-escaped builders arrive double-escaped."""
    assert as_text("Tom &amp; Jerry") == "Tom &amp; Jerry"
    assert "&amp;" in as_text("Tom &amp; Jerry")


def test_patched_message_text_is_a_str():
    """The real defect: `Message.text` was a tuple, so `.split` raised.

    Asserted against the actual installed patch, applied through the real
    PatchRegistry, because the first attempt at this fix installed the
    property under the wrong attribute name and was silently a no-op.
    """
    from telethon.tl.custom import Message

    import pyUltroid.startup.patching as patching

    registry = patching.get_patch_registry()
    assert registry.apply("normalized_text", force=True) is True

    descriptor = Message.__dict__["text"]
    assert isinstance(descriptor, property), (
        "Message.text must be a property, not a bare function - a function "
        "would make msg.text return a bound method instead of a string"
    )

    class _TupleParseMode:
        def unparse(self, text, entities):
            return (text, entities)  # the fork behaviour that breaks plugins

    msg = Message(None, None)
    msg._client = _pytypes.SimpleNamespace(parse_mode=_TupleParseMode())
    msg._text = None

    assert isinstance(msg.text, str)
    assert isinstance(msg.text.split(), list)


def test_patched_message_text_is_cached():
    from telethon.tl.custom import Message

    import pyUltroid.startup.patching as patching

    patching.get_patch_registry().apply("normalized_text", force=True)
    msg = Message(None, None)
    msg._text = "cached value"
    msg._client = None
    assert msg.text == "cached value"
    assert msg.__dict__["_ultroid_text"] == "cached value"


# ── MessageBody ────────────────────────────────────────────────────────────


def test_message_body_keeps_entities_and_disables_parse_mode():
    ents = [_pytypes.SimpleNamespace(offset=0, length=1)]
    body = MessageBody.of(("hi", ents))
    assert body.text == "hi"
    assert body.has_entities
    kwargs = body.send_kwargs()
    # Supplying both entities and a parse mode is ambiguous and can corrupt
    # already-final text.
    assert kwargs["parse_mode"] is None
    assert kwargs["formatting_entities"] == ents


def test_message_body_without_entities_uses_v2():
    assert MessageBody.of("plain").send_kwargs() == {"parse_mode": "v2"}


def test_parse_mode_coercion():
    assert ParseMode.coerce("v2") is ParseMode.V2
    assert ParseMode.coerce("HTML") is ParseMode.HTML
    assert ParseMode.coerce(ParseMode.NONE) is ParseMode.NONE
    with pytest.raises(ValueError):
        ParseMode.coerce("markdown")


# ── defect 2: edit must edit, never send a second message ──────────────────


class _Msg:
    """Records edits. Raising on edit is how a failure is simulated."""

    def __init__(self, fail: bool = False):
        self.edits: list[tuple[str, dict]] = []
        self.fail = fail

    async def edit(self, text, **kwargs):
        if self.fail:
            raise RuntimeError("MessageNotModifiedError: nothing changed")
        self.edits.append((text, kwargs))
        return self


def test_notice_edit_edits_in_place():
    msg = _Msg()
    asyncio.run(NoticeEdit(msg).edit("**done**"))
    assert len(msg.edits) == 1
    text, kwargs = msg.edits[0]
    assert text == "**done**"
    assert kwargs["parse_mode"] == "v2"


def test_notice_edit_never_sends_a_second_message():
    """A failed edit must raise, not degrade into a new message.

    This is the `.ping` defect: the placeholder stayed and the result was
    sent underneath it.
    """
    msg = _Msg(fail=True)
    with pytest.raises(EDIT_UNSUPPORTED):
        asyncio.run(NoticeEdit(msg).edit("result"))
    # Nothing was sent; only the failure was reported.
    assert msg.edits == []


def test_notice_edit_try_edit_reports_failure():
    assert asyncio.run(NoticeEdit(_Msg(fail=True)).try_edit("x")) is False
    assert asyncio.run(NoticeEdit(_Msg()).try_edit("x")) is True


def test_notice_edit_without_edit_method_raises():
    with pytest.raises(EDIT_UNSUPPORTED):
        asyncio.run(NoticeEdit(object()).edit("x"))


def test_notice_edit_coerces_a_non_str_payload():
    """`.ping` passed an InputRichMessageHTML, which the parser rejected."""
    from telethon.tl.types import InputRichMessageHTML

    msg = _Msg()
    asyncio.run(NoticeEdit(msg).edit(InputRichMessageHTML(html="<b>Pong !!</b>")))
    text = msg.edits[0][0]
    assert isinstance(text, str)
    # The real body must survive, not a repr() of the TL object.
    assert text == "<b>Pong !!</b>"
    assert "InputRichMessageHTML(" not in text
    # And it must actually be parseable now.
    assert _rendered(text) is not None


def test_as_text_reads_a_rich_message_body():
    from telethon.tl.types import InputRichMessageHTML

    assert as_text(InputRichMessageHTML(html="<b>hi</b>")) == "<b>hi</b>"


# ── defect 3: error notices, not tracebacks ────────────────────────────────


def test_syntax_error_is_a_short_actionable_notice():
    """The reported case: `return list(for m in ...)` is a SyntaxError."""
    tb = (
        'Traceback (most recent call last):\n'
        '  File "<unknown>", line 2, in <module>\n'
        'SyntaxError: invalid syntax\n'
    )
    notice = ErrorNotice.from_traceback(tb)
    assert notice.is_user_error
    text = notice.chat_text()
    assert "SyntaxError" in text
    assert "invalid syntax" in text
    # The traceback frames must not be in the chat message.
    assert "Traceback (most recent call last)" not in text
    assert 'File "<unknown>"' not in text
    assert len(text) < 400


def test_syntax_notice_survives_the_v2_parser():
    notice = ErrorNotice.from_exception(
        SyntaxError("invalid syntax", ("<t>", 2, 3, "return list(for m in x)"))
    )
    assert "SyntaxError" in _rendered(notice.chat_text())


def test_chat_text_never_contains_a_full_traceback():
    tb = "Traceback (most recent call last):\n" + "\n".join(
        f'  File "f{i}.py", line {i}, in fn' for i in range(80)
    ) + "\nValueError: boom"
    notice = ErrorNotice.from_traceback(tb)
    assert len(notice.chat_text()) < 500
    assert "boom" in notice.chat_text()


def test_log_text_keeps_the_traceback():
    tb = "Traceback (most recent call last):\n  File \"x.py\", line 1\nValueError: boom"
    notice = ErrorNotice.from_traceback(tb)
    log = notice.log_text()
    assert "Traceback" in log
    assert "boom" in log


def test_from_exception_unescapes_html_entities():
    """Tracebacks built by HTML-escaped notices arrive as &quot;/&#x27;."""
    notice = ErrorNotice.from_exception(ValueError("Tom &quot;Jerry&quot; said no"))
    assert "&quot;" not in notice.detail
    assert '"Jerry"' in notice.detail


def test_severity_classification():
    assert (
        ErrorNotice.from_exception(SyntaxError("bad")).severity
        is ErrorSeverity.USER
    )
    assert (
        ErrorNotice.from_exception(TypeError("bad")).severity
        is ErrorSeverity.USER
    )
    assert (
        ErrorNotice.from_exception(ConnectionError("net")).severity
        is ErrorSeverity.UNKNOWN
    )


def test_chat_text_is_v2_safe_for_hostile_input():
    notice = ErrorNotice.from_exception(
        ValueError("a & b <live> `c` **d**")
    )
    rendered = _rendered(notice.chat_text())
    assert "a & b <live>" in rendered
    assert "&amp;" not in rendered

"""Focused tests for brain's narrowly scoped rich-edit fallback."""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import types
from telethon.errors import BadRequestError, RPCError

ROOT = Path(__file__).resolve().parents[1]


def load_edit_function():
    tree = ast.parse((ROOT / "plugins" / "brain.py").read_text())
    node = next(
        item for item in tree.body
        if isinstance(item, ast.AsyncFunctionDef) and item.name == "_edit_ai_output"
    )
    namespace = {
        "edit_rich": None,
        "rich_output": lambda text: f"rich:{text}",
        "_plain_ai_text": lambda text: f"plain:{text}",
        "BadRequestError": BadRequestError,
        "RPCError": RPCError,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "plugins/brain.py", "exec"), namespace)  # noqa: S102
    return namespace["_edit_ai_output"], namespace


class Msg:
    def __init__(self, error=None):
        self.error = error
        self.edits = []

    async def edit(self, text, **kwargs):
        self.edits.append((text, kwargs))
        if self.error:
            raise self.error


def err(message, error_type=BadRequestError):
    return error_type(None, message, code=400)


def run(coro):
    return asyncio.run(coro)


def event():
    return SimpleNamespace(client=object(), chat_id=42)


def test_rich_success_preserves_rich_attempt_without_plain_edit():
    fn, ns = load_edit_function()
    calls = []
    async def rich(*args):
        calls.append(args)
    ns["edit_rich"] = rich
    msg = Msg()
    run(fn(event(), msg, "answer", True))
    assert len(calls) == 1
    assert calls[0][1:3] == (42, msg)
    assert isinstance(calls[0][3], types.InputRichMessage)
    assert isinstance(calls[0][3].blocks[-1], types.PageBlockDetails)
    assert msg.edits == []


def test_plain_unprivileged_branch_uses_existing_message():
    fn, ns = load_edit_function()
    ns["edit_rich"] = pytest.fail
    msg = Msg()
    run(fn(event(), msg, "answer", False))
    assert msg.edits == [("plain:answer", {"parse_mode": None})]


def test_exact_unsupported_rpc_retries_same_message_as_plain_once():
    fn, ns = load_edit_function()
    calls = []
    async def rich(client, chat_id, target, text):
        calls.append((client, chat_id, target, text))
        raise err("RICH_MESSAGE_UNSUPPORTED")
    ns["edit_rich"] = rich
    msg = Msg()
    evt = event()
    run(fn(evt, msg, "answer", True))
    assert len(calls) == 1
    assert calls[0][:3] == (evt.client, 42, msg)
    assert isinstance(calls[0][3], types.InputRichMessage)
    assert msg.edits == [("plain:answer", {"parse_mode": None})]


@pytest.mark.parametrize("error", [
    err("MESSAGE_ID_INVALID"),
    err(None),
    err("RICH_MESSAGE_UNSUPPORTED "),
    err("prefix RICH_MESSAGE_UNSUPPORTED"),
    err("rich_message_unsupported"),
    RuntimeError("RICH_MESSAGE_UNSUPPORTED"),
])
def test_unrelated_or_nonexact_errors_propagate(error):
    fn, ns = load_edit_function()
    async def rich(*args):
        raise error
    ns["edit_rich"] = rich
    msg = Msg()
    with pytest.raises(type(error)) as caught:
        run(fn(event(), msg, "answer", True))
    assert caught.value is error
    assert msg.edits == []


def test_fallback_error_propagates():
    fn, ns = load_edit_function()
    async def rich(*args):
        raise err("RICH_MESSAGE_UNSUPPORTED")
    ns["edit_rich"] = rich
    fallback_error = RuntimeError("plain edit failed")
    msg = Msg(fallback_error)
    with pytest.raises(RuntimeError, match="plain edit failed"):
        run(fn(event(), msg, "answer", True))
    assert msg.edits == [("plain:answer", {"parse_mode": None})]


@pytest.mark.parametrize("as_markup", [False, True])
def test_unsupported_details_fallback_preserves_titles_bodies_and_commands(as_markup):
    from pyUltroid._core.richmsg import (
        RichBlocks,
        blocks_to_plain_text,
        rich_markup_from_blocks,
    )

    details = RichBlocks().paragraph("Install the dependencies").code("pip install -r requirements.txt", lang="bash")
    content = RichBlocks(native=True).header("Setup guide").expandable("Install", details)
    if as_markup:
        content = rich_markup_from_blocks(content, format="html")
    fn, ns = load_edit_function()
    ns["rich_output"] = lambda _text: content
    ns["_plain_ai_text"] = lambda text: blocks_to_plain_text(ns["rich_output"](text))

    async def rich(*_args):
        raise err("RICH_MESSAGE_UNSUPPORTED")

    ns["edit_rich"] = rich
    msg = Msg()
    run(fn(event(), msg, "answer", True))
    assert len(msg.edits) == 1
    text, options = msg.edits[0]
    assert all(part in text for part in (
        "Setup guide", "Install", "Install the dependencies", "pip install -r requirements.txt",
    ))
    assert "<blockquote" not in text and "<code" not in text
    assert options == {"parse_mode": None}

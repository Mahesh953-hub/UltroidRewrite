"""Native assistant creations, catalog routing, and board-only command coverage."""

from __future__ import annotations

import asyncio
import importlib
import sys
from html.parser import HTMLParser
from types import ModuleType, SimpleNamespace

import pytest
from conftest import ROOT
from telethon import types


@pytest.fixture
def rich_modules(module_isolation):
    with module_isolation("assistant", "pyUltroid._misc._assistant"):
        package = ModuleType("assistant")
        package.__path__ = [str(ROOT / "assistant")]
        sys.modules["assistant"] = package
        shim = ModuleType("pyUltroid._misc._assistant")
        shim.asst_cmd = lambda **_kwargs: lambda function: function
        shim.callback = lambda *_args, **_kwargs: lambda function: function
        sys.modules["pyUltroid._misc._assistant"] = shim
        yield (
            importlib.import_module("assistant.rich_creations"),
            importlib.import_module("assistant.rich_showcase"),
        )


class _BoardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows = 0
        self.cells = 0
        self.pieces = []
        self._in_cell = False
        self.outside = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows += 1
        if tag == "td":
            self.cells += 1
            self._in_cell = True

    def handle_endtag(self, tag):
        if tag == "td":
            self._in_cell = False

    def handle_data(self, data):
        if self._in_cell and data.strip():
            self.pieces.append(data.strip())
        elif data.strip():
            self.outside.append(data)


def _walk(blocks):
    for block in blocks:
        yield block
        yield from _walk(getattr(block, "blocks", ()) or ())


def _depth_of(blocks, target, depth=0):
    for block in blocks:
        if block is target:
            return depth
        found = _depth_of(getattr(block, "blocks", ()) or (), target, depth + 1)
        if found >= 0:
            return found
    return -1


def test_chess_is_only_a_full_starting_board_and_serializes_as_native_html(
    rich_modules,
):
    creations, showcase = rich_modules
    markup = creations.chessboard_html()
    parser = _BoardParser()
    parser.feed(markup)
    assert parser.rows == 8
    assert parser.cells == 64
    assert len(parser.pieces) == 64
    assert "♜♞♝♛♚♝♞♜" == "".join(parser.pieces[:8])
    assert "♟" * 8 == "".join(parser.pieces[8:16])
    assert "♙" * 8 == "".join(parser.pieces[48:56])
    assert "♖♘♗♕♔♗♘♖" == "".join(parser.pieces[56:])
    assert parser.outside == []
    native = showcase.rich_message(markup, format="html")
    assert isinstance(native, types.InputRichMessageHTML)
    assert bytes(native)


def test_chess_command_sends_one_board_only_native_message(rich_modules, monkeypatch):
    creations, _showcase = rich_modules
    sent = []

    async def send(client, peer, payload, **kwargs):
        sent.append((client, peer, payload, kwargs))
        return "board"

    monkeypatch.setattr(creations, "send_rich", send)
    event = SimpleNamespace(client=object(), chat_id=123)
    assert asyncio.run(creations.chess(event)) == "board"
    assert len(sent) == 1
    assert sent[0][:2] == (event.client, event.chat_id)
    assert isinstance(sent[0][2], types.InputRichMessageHTML)
    assert sent[0][3] == {}


def test_five_templates_are_distinct_complex_native_messages_with_nested_controls(
    rich_modules,
):
    creations, _showcase = rich_modules
    assert len(creations.CREATIONS) == 5
    payloads = [creations.creation_payload(entry[0]) for entry in creations.CREATIONS]
    assert all(isinstance(payload, types.InputRichMessage) for payload in payloads)
    assert len({bytes(payload) for payload in payloads}) == 5
    for payload in payloads:
        assert bytes(payload)
        blocks = payload.blocks
        assert len(blocks) >= 5
        assert any(isinstance(block, types.PageBlockTable) for block in blocks)
        rows = [
            block
            for block in _walk(blocks)
            if isinstance(block, types.PageBlockButtonRow)
        ]
        assert rows and len(rows[0].buttons) >= 2
        assert _depth_of(blocks, rows[0]) >= 2
        kinds = {type(button.type) for row in rows for button in row.buttons}
        assert types.InlineButtonTypeUrl in kinds
        assert types.InlineButtonTypeCopy in kinds
        assert any(isinstance(block, types.PageBlockParagraph) for block in blocks)


def test_templates_resolve_through_existing_showcase_routing(rich_modules):
    creations, showcase = rich_modules
    catalog_ids = {item[0] for item in showcase.CATALOG}
    for sid, _label, _description, _builder in creations.CREATIONS:
        assert sid in catalog_ids
        item = next(entry for entry in showcase.CATALOG if entry[0] == sid)
        assert bytes(showcase._payload(item)) == bytes(creations.creation_payload(sid))
    assert bytes(showcase._payload(showcase.CATALOG[0]))

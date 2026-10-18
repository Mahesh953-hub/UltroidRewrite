"""Native Ludo presentation and nested control regression tests."""
from __future__ import annotations

import copy
import json

import pytest
import test_ludo_controller as controller
from telethon.extensions import BinaryReader
from telethon.tl import types as t
from test_ludo_controller import (
    make_event,
    payload,
    run_callback,
    setup_table,
    walk,
)


@pytest.fixture
def ludo_modules(module_isolation, monkeypatch):
    yield from controller.ludo_modules.__wrapped__(module_isolation, monkeypatch)


def strings(value):
    return json.dumps(value.to_dict(), ensure_ascii=False, default=str)


def controls(rich):
    return [button for block in walk(rich.blocks)
            if isinstance(block, t.PageBlockButtonRow) for button in block.buttons]


@pytest.mark.parametrize("state", ["lobby", "roll", "legal", "crowded", "home", "finished", "cancelled", "confirmation"])
def test_visual_states_roundtrip_without_code_or_mutation(ludo_modules, state):
    _core, app = ludo_modules
    table = setup_table(app)
    game = table.game
    for user in (8, 9, 10):
        game.apply("join", user, now=100)
    if state != "lobby":
        game.apply("start", 7, now=100)
    if state == "legal":
        game.apply("roll", 7, now=101, roll=lambda: 6)
    elif state == "crowded":
        game.players[0].tokens = [8, 8, 8, 8]
        game.players[1].tokens = [47, 47, 47, 47]  # Same safe global square 8.
        game.players[2].tokens = [52, 53, 54, 57]
        game.players[3].tokens = [-1, 0, 51, 56]
    elif state == "home":
        game.players[0].tokens = [52, 53, 56, 57]
        game.apply("roll", 7, now=101, roll=lambda: 1)
    elif state == "finished":
        game.players[0].tokens = [57] * 4
        game.players[0].finished = True
        game.players[2].resigned = True
        game.winners = [7, 8, 10]
        game.status = "finished"
    elif state == "cancelled":
        game.apply("cancel", 7, now=101)
    elif state == "confirmation":
        table.confirmation = (7, "resign", 120)
    before = copy.deepcopy(game.__dict__)
    rich = app.render(table)
    raw = bytes(rich)
    with BinaryReader(raw) as reader:
        restored = reader.tgread_object()
    assert bytes(restored) == raw
    assert game.__dict__ == before
    blocks = list(walk(rich.blocks))
    assert not any(isinstance(block, t.PageBlockPreformatted) for block in blocks)
    assert 'TextFixed' not in strings(rich)
    tables = [block for block in blocks if isinstance(block, t.PageBlockTable)]
    board = next(block for block in tables if len(block.rows) == 15)
    assert all(len(row.cells) == 15 for row in board.rows)
    assert board.compact and board.bordered
    assert any(isinstance(child, t.PageBlockDetails)
               for block in blocks if isinstance(block, t.PageBlockDetails)
               for child in block.blocks)
    for label in ("A", "B", "C", "D"):
        assert label in strings(rich)
    for button in controls(rich):
        assert len(button.type.data) <= 64
        assert app.PATTERN.fullmatch(button.type.data.decode())
    if state == "legal":
        moves = [b for b in controls(rich) if b.type.data.decode().split(":")[-1].startswith("move")]
        assert len(moves) == 4
        assert all(b.style.bg_primary for b in moves)
        assert all(len(block.buttons) <= 2 for block in blocks if isinstance(block, t.PageBlockButtonRow))
    if state == "confirmation":
        assert next(b for b in controls(rich) if b.type.data.endswith(b":resignok")).style.bg_danger
    if state in {"finished", "cancelled"}:
        assert not any(b.type.data.endswith(b":roll") or b.type.data.split(b":")[-1].startswith(b"move") for b in controls(rich))


def test_nested_controls_still_execute_original_revisioned_callbacks(ludo_modules, monkeypatch):
    _core, app = ludo_modules
    table = setup_table(app)
    table.game.apply("join", 8, now=100)
    table.game.apply("start", 7, now=100)
    sent = []

    async def edit(_client, _chat, _message, rich):
        sent.append(rich)

    monkeypatch.setattr(app, "edit_rich", edit)
    for action in ("roll", "move0", "resign", "back", "cancel", "cancelok"):
        rich = app.render(table)
        button = next(b for b in controls(rich) if b.type.data == payload(table, action).encode())
        event, answers = make_event(table, button.type.data.decode())
        old_revision = table.revision
        run_callback(app, event)
        assert table.revision == old_revision + 1
        assert answers == [((), {})]
        assert sent and bytes(sent[-1])
    assert table.game.status == "cancelled"


def test_board_and_cards_keep_exact_stacks_and_noncolour_token_identity(ludo_modules):
    _core, app = ludo_modules
    from pyUltroid._core.ludo_visuals import board_block, player_blocks

    table = setup_table(app)
    table.game.players[0].tokens = [8, 8, 52, 57]
    board = board_block(table.game)
    assert bytes(board)
    cards = player_blocks(table.game)
    content = " ".join(strings(block) for block in cards)
    for token in ("A1", "A2", "A3", "A4"):
        assert token in content
    assert "8" in content
    assert "1" in content
    assert "Finished" in content or "finished" in content

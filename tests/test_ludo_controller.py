"""Offline controller, callback, and rendering coverage for shared Ludo."""
from __future__ import annotations

import asyncio
import copy
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest
from conftest import ROOT
from telethon import types
from telethon.errors import MessageNotModifiedError, RPCError


@pytest.fixture
def ludo_modules(module_isolation, monkeypatch):
    with module_isolation("assistant", "pyUltroid._misc._assistant"):
        package = ModuleType("assistant")
        package.__path__ = [str(ROOT / "assistant")]
        sys.modules["assistant"] = package
        shim = ModuleType("pyUltroid._misc._assistant")
        shim.asst_cmd = lambda **_kwargs: lambda function: function
        shim.callback = lambda *_args, **_kwargs: lambda function: function
        sys.modules["pyUltroid._misc._assistant"] = shim
        core = importlib.import_module("pyUltroid._core.ludo_arcade")
        app = importlib.import_module("assistant.ludo_arcade")
        monkeypatch.setattr(app.time, "monotonic", lambda: 100.0)
        monkeypatch.setattr(core.secrets, "randbelow", lambda _maximum: 5)
        yield core, app


def walk(blocks):
    for block in blocks:
        yield block
        yield from walk(getattr(block, "blocks", ()) or ())


def make_event(table, data, *, user=7, chat=None, message=None, is_group=True):
    answers = []
    async def answer(*args, **kwargs):
        answers.append((args, kwargs))
    return SimpleNamespace(data=data.encode(), sender_id=user,
        chat_id=table.chat if chat is None else chat,
        message_id=table.message if message is None else message,
        is_group=is_group, client=SimpleNamespace(), answer=answer), answers


def payload(table, action, revision=None):
    rev = table.revision if revision is None else revision
    return f"lu:{table.token}:{rev}:{action}"


def setup_table(app, host=7, chat=-100):
    table = app.Table("abcdef123456", chat, app.Ludo(host, now=100), message=9, touched=100)
    app.TABLES.clear()
    app.TABLES[table.token] = table
    return table


def game_snapshot(game):
    """Compare engine fields without relying on object identity equality."""
    return copy.deepcopy(game.__dict__)


def run_callback(app, event):
    return asyncio.run(app.ludo_callback(event))


def test_launch_requires_group_and_cleans_up_failed_send(ludo_modules, monkeypatch):
    _core, app = ludo_modules
    app.TABLES.clear()
    event = SimpleNamespace(is_group=False, sender_id=7, chat_id=12, client=object())
    with pytest.raises(ValueError):
        asyncio.run(app.launch(event))
    assert not app.TABLES
    event.is_group = True
    async def fail(*_args): raise RuntimeError("offline")
    monkeypatch.setattr(app, "send_rich", fail)
    with pytest.raises(RuntimeError): asyncio.run(app.launch(event))
    assert not app.TABLES


def test_one_table_per_group_capacity_and_expiry(ludo_modules, monkeypatch):
    _core, app = ludo_modules
    now = [10.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: now[0])
    app.TABLES.clear()
    for i in range(app.CAPACITY):
        table = app.Table(f"{i:012x}", i + 1, app.Ludo(i + 1, now=now[0]), message=1, touched=now[0])
        app.TABLES[table.token] = table
    async def send(*_args): return 99
    monkeypatch.setattr(app, "send_rich", send)
    group = SimpleNamespace(is_group=True, sender_id=1000, chat_id=1, client=object())
    with pytest.raises(ValueError, match="already has"):
        asyncio.run(app.launch(group))
    group.chat_id = 1001
    with pytest.raises(ValueError, match="occupied"):
        asyncio.run(app.launch(group))
    # Expired idle tables are pruned before capacity and group checks.
    now[0] += app.TTL
    asyncio.run(app.launch(group))
    assert len(app.TABLES) == 1
    assert next(iter(app.TABLES.values())).chat == 1001


def test_join_by_non_owner_and_reject_dm_wrong_group_or_message_and_stale(ludo_modules, monkeypatch):
    _core, app = ludo_modules
    table = setup_table(app)
    async def edit(*_args): return None
    monkeypatch.setattr(app, "edit_rich", edit)
    event, _ = make_event(table, payload(table, "join"), user=22)
    run_callback(app, event)
    assert [p.user for p in table.game.players] == [7, 22]
    for kwargs in ({"is_group": False}, {"chat": -200}, {"message": 10}):
        event, answers = make_event(table, payload(table, "join"), user=33, **kwargs)
        run_callback(app, event)
        assert answers and len(table.game.players) == 2
    event, answers = make_event(table, payload(table, "join", revision=0), user=33)
    run_callback(app, event)
    assert answers and len(table.game.players) == 2


def test_unauthorized_roll_and_candidate_commit_only_after_edit(ludo_modules, monkeypatch):
    _core, app = ludo_modules
    table = setup_table(app)
    table.game.apply("join", 22, now=100)
    table.game.apply("start", 7, now=100)
    before = game_snapshot(table.game)
    async def edit(*_args): return None
    monkeypatch.setattr(app, "edit_rich", edit)
    event, answers = make_event(table, payload(table, "roll"), user=22)
    run_callback(app, event)
    assert game_snapshot(table.game) == before and answers
    event, _ = make_event(table, payload(table, "roll"), user=7)
    run_callback(app, event)
    assert table.game.dice is not None and table.revision == 1
    committed = game_snapshot(table.game)
    for failure in (MessageNotModifiedError(request=None), RPCError(None, "RICH_MESSAGE_BLOCKS_INVALID")):
        async def reject(*_args, exc=failure): raise exc
        monkeypatch.setattr(app, "edit_rich", reject)
        event, _ = make_event(table, payload(table, "move0"), user=7)
        run_callback(app, event)
        assert game_snapshot(table.game) == committed and table.revision == 1


def test_edit_timeout_expires_table_without_committing(ludo_modules, monkeypatch):
    _core, app = ludo_modules
    table = setup_table(app)
    table.game.apply("join", 22, now=100)
    table.game.apply("start", 7, now=100)
    before = game_snapshot(table.game)
    async def timeout(*_args): raise asyncio.TimeoutError()
    monkeypatch.setattr(app, "edit_rich", timeout)
    event, _ = make_event(table, payload(table, "roll"), user=7)
    run_callback(app, event)
    assert game_snapshot(table.game) == before and table.token not in app.TABLES


def test_confirmation_binds_identity_expires_cancel_and_resign(ludo_modules, monkeypatch):
    _core, app = ludo_modules
    now = [100.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: now[0])
    async def edit(*_args): return None
    monkeypatch.setattr(app, "edit_rich", edit)
    table = setup_table(app)
    event, _ = make_event(table, payload(table, "cancel"), user=7)
    run_callback(app, event)
    assert table.confirmation[:2] == (7, "cancel") and table.game.status == "lobby"
    event, _ = make_event(table, payload(table, "cancelok"), user=8)
    run_callback(app, event)
    assert table.game.status == "lobby"
    now[0] += 21
    event, _ = make_event(table, payload(table, "cancelok"), user=7)
    run_callback(app, event)
    assert table.game.status == "lobby"
    # Fresh confirmation and identity-matched acceptance.
    event, _ = make_event(table, payload(table, "cancel"), user=7)
    run_callback(app, event)
    event, _ = make_event(table, payload(table, "cancelok"), user=7)
    run_callback(app, event)
    assert table.game.status == "cancelled"
    table = setup_table(app)
    table.game.apply("join", 8, now=now[0]); table.game.apply("start", 7, now=now[0])
    event, _ = make_event(table, payload(table, "resign"), user=7)
    run_callback(app, event)
    event, _ = make_event(table, payload(table, "resignok"), user=8)
    run_callback(app, event)
    assert table.game.status == "playing"
    event, _ = make_event(table, payload(table, "resignok"), user=7)
    run_callback(app, event)
    assert table.game.status == "finished"


def test_lock_reports_busy_and_rendered_states_serialize_bounded_regex_callbacks(ludo_modules):
    core, app = ludo_modules
    table = setup_table(app)
    # Lobby, playing-before-roll, pending legal moves, and terminal states.
    states = [table]
    game = core.Ludo(7, now=100); game.apply("join", 8, now=100); game.apply("start", 7, now=100)
    playing = app.Table("abcdef123456", -100, game, 9)
    states.append(playing)
    game.apply("roll", 7, now=101, roll=lambda: 6)
    states.append(playing)
    finished_game = core.Ludo(7, now=100); finished_game.status = "finished"
    states.append(app.Table("abcdef123456", -100, finished_game, 9))
    for item in states:
        rich = app.render(item)
        assert isinstance(rich, types.InputRichMessage) and bytes(rich)
        for block in walk(rich.blocks):
            if isinstance(block, types.PageBlockButtonRow):
                for btn in block.buttons:
                    if isinstance(btn.type, types.InlineButtonTypeCallback):
                        data = btn.type.data
                        assert len(data) <= 64 and app.PATTERN.fullmatch(data.decode("ascii"))
    event, answers = make_event(table, payload(table, "join"), user=8)
    async def busy():
        async with table.lock:
            await app.ludo_callback(event)
    asyncio.run(busy())
    assert answers and "updating" in answers[0][0][0]


def test_stale_and_invalid_callback_data_are_ignored_or_rejected(ludo_modules):
    _core, app = ludo_modules
    table = setup_table(app)
    assert app.PATTERN.fullmatch("lu:abcdef123456:0:roll")
    assert not app.PATTERN.fullmatch("lu:abcdef123456:0:exec")
    event, answers = make_event(table, "lu:abcdef123456:0:roll", user=7, is_group=False)
    run_callback(app, event)
    assert answers and table.game.status == "lobby"


def test_real_ludo_decorators_register_public_command_and_scoped_callbacks(monkeypatch):
    import importlib.util

    from telethon import events

    import pyUltroid._misc._assistant as real

    registered = []
    class Registry:
        def add_event_handler(self, function, builder):
            registered.append((function, builder))
    monkeypatch.setattr(real, "asst", Registry())
    monkeypatch.setattr(real, "owner_and_sudos", lambda: {41})
    monkeypatch.setattr(real, "_patch_assistant_event", lambda *_args: None)
    name = "ludo_real_decorators_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "assistant/ludo_arcade.py")
    app = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, app)
    spec.loader.exec_module(app)
    assert len(registered) == 2
    command, builder = registered[0]
    callback, cb_builder = registered[1]
    assert isinstance(builder, events.NewMessage)
    assert builder.pattern("/ludo") and builder.pattern("/ludo@assistant ")
    assert not builder.pattern("/ludoish")
    assert cb_builder.match(b"lu:new") and not cb_builder.match(b"lu:exec")
    seen = []
    async def launch(event): seen.append(event.sender_id)
    monkeypatch.setattr(app, "launch", launch)
    event = SimpleNamespace(sender_id=99)
    asyncio.run(command(event))
    assert seen == [99]  # Not the owner: group membership is checked inside launch.
    answers = []
    async def answer(*args, **kwargs): answers.append(args)
    event = SimpleNamespace(sender_id=99, is_group=True, data=b"lu:new", answer=answer)
    asyncio.run(callback(event))
    assert seen == [99, 99] and answers == [("Ludo lobby opened",)]

"""Offline coverage for native Matrix arcade state and assistant callbacks."""
from __future__ import annotations

import asyncio
import copy
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest
from conftest import ROOT
from telethon import types
from telethon.errors import MessageNotModifiedError


@pytest.fixture
def arcade_modules(module_isolation):
    with module_isolation("assistant", "pyUltroid._misc._assistant"):
        package = ModuleType("assistant")
        package.__path__ = [str(ROOT / "assistant")]
        sys.modules["assistant"] = package
        shim = ModuleType("pyUltroid._misc._assistant")
        shim.asst_cmd = lambda **_kwargs: lambda function: function
        shim.callback = lambda *_args, **_kwargs: lambda function: function
        sys.modules["pyUltroid._misc._assistant"] = shim
        yield (importlib.import_module("pyUltroid._core.matrix_arcade"),
               importlib.import_module("assistant.matrix_arcade"))


def walk(blocks):
    for block in blocks:
        yield block
        yield from walk(getattr(block, "blocks", ()) or ())


def test_all_scenes_serialize_native_and_have_nested_source_and_bounded_callbacks(arcade_modules):
    core, _ = arcade_modules
    state = core.Arcade("abcdef123456")
    payloads = [core.lobby_payload()]
    for mode in core.MODES:
        state.enter(mode)
        payloads.append(core.payload(state))
    assert all(isinstance(p, types.InputRichMessage) and bytes(p) for p in payloads)
    lab = core.Arcade("abcdef123456")
    lab.enter("lab")
    blocks = list(walk(core.payload(lab).blocks))
    assert any(isinstance(b, types.PageBlockDetails) for b in blocks)
    assert any(isinstance(b, types.PageBlockPreformatted) for b in blocks)
    callbacks = [button.type.data for block in blocks
                 if isinstance(block, types.PageBlockButtonRow)
                 for button in block.buttons
                 if isinstance(button.type, types.InlineButtonTypeCallback)]
    assert callbacks and all(len(data) <= 64 for data in callbacks)


def test_rain_frames_are_reproducible_and_change_by_frame(arcade_modules):
    core, _ = arcade_modules
    assert core.rain_frame(42, 0) == core.rain_frame(42, 0)
    assert core.rain_frame(42, 0) != core.rain_frame(42, 1)


def test_tic_tac_toe_rejects_illegal_and_never_loses(arcade_modules):
    core, _ = arcade_modules
    state = core.Arcade("x", mode="ttt")
    state.board[0] = 1
    with pytest.raises(ValueError):
        state.act("cell0")
    # Explore every reachable human move sequence against the engine.
    seen = set()
    def explore(board):
        key = tuple(board)
        if key in seen:
            return
        seen.add(key)
        if core.winner(board) or all(board):
            assert core.winner(board) != 1
            return
        for i, val in enumerate(board):
            if val:
                continue
            nxt = board[:]
            nxt[i] = 1
            if not core.winner(nxt) and not all(nxt):
                best = max((j for j, v in enumerate(nxt) if not v), key=lambda j: core._score_move(tuple(nxt), j, 2))
                nxt[best] = 2
            explore(nxt)
    explore([0] * 9)
    assert seen


def test_lights_puzzle_inverse_toggle_and_memory_pair_flow(arcade_modules):
    core, _ = arcade_modules
    state = core.Arcade("lights", mode="lights", seed=91)
    state.enter("lights")
    scrambled = state.board[:]
    assert any(scrambled)
    solution = None
    for mask in range(1 << 9):
        candidate = scrambled[:]
        for i in range(9):
            if mask & (1 << i): core.toggle(candidate, i)
        if not any(candidate):
            solution = mask
            break
    assert solution is not None
    for i in range(9):
        if solution & (1 << i): core.toggle(state.board, i)
    assert not any(state.board)
    state = core.Arcade("memory", mode="memory", seed=4)
    state.enter("memory")
    pair = next(i for i in range(12) if state.deck[i] == state.deck[0] and i != 0)
    mismatch = next(i for i in range(12) if state.deck[i] != state.deck[0])
    state.act("cell0"); state.act(f"cell{mismatch}")
    assert len(state.revealed) == 2
    replacement = next(i for i in range(12) if i not in (0, mismatch))
    state.act(f"cell{replacement}")
    assert state.revealed == [replacement]
    replacement_pair = next(i for i in range(12) if i != replacement and state.deck[i] == state.deck[replacement])
    state.act(f"cell{replacement_pair}")
    assert {replacement, replacement_pair}.issubset(state.matched) and state.revealed == []
    for val in set(state.deck):
        inds = [i for i, v in enumerate(state.deck) if v == val and i not in state.matched]
        if inds:
            state.act(f"cell{inds[0]}"); state.act(f"cell{inds[1]}")
    assert len(state.matched) == 12


def test_session_ttl_capacity_and_binding(arcade_modules, monkeypatch):
    _core, app = arcade_modules
    store = app.SessionStore()
    now = [100.0]
    monkeypatch.setattr(app.time, "monotonic", lambda: now[0])
    s = store.create(1, 2); s.message = 3
    assert store.resolve(s.state.token, 1, 2, 3) is s
    for args in [(2, 2, 3), (1, 9, 3), (1, 2, 4)]:
        with pytest.raises(ValueError): store.resolve(s.state.token, *args)
    s.touched = now[0]
    now[0] += app.SESSION_TTL - 1
    assert store.resolve(s.state.token, 1, 2, 3) is s
    now[0] += 2
    with pytest.raises(ValueError): store.resolve(s.state.token, 1, 2, 3)
    store = app.SessionStore()
    for i in range(app.MAX_SESSIONS): store.create(i, i)
    store.create(100, 100)
    assert len(store.sessions) == app.MAX_SESSIONS


def make_event(session, data, *, owner=7, chat=8, message=9, client=None):
    answers = []
    async def answer(*args, **kwargs): answers.append((args, kwargs))
    return SimpleNamespace(data=data.encode(), sender_id=owner, chat_id=chat,
                           message_id=message, client=client or SimpleNamespace(),
                           answer=answer), answers


def callback_data(session, action, revision=None):
    rev = session.state.revision if revision is None else revision
    return f"mx:{session.state.token}:{rev}:{action}"


def test_async_callback_revision_legality_rpc_commit_and_launch_cleanup(arcade_modules, monkeypatch):
    _core, app = arcade_modules
    app.STORE.sessions.clear()
    s = app.STORE.create(7, 8); s.message = 9; s.state.enter("ttt")
    calls = []
    async def edit(*args): calls.append(args)
    monkeypatch.setattr(app, "edit_rich", edit)
    event, answers = make_event(s, callback_data(s, "cell0"))
    asyncio.run(app.matrix_callback(event))
    assert s.state.board[0] == 1 and s.state.revision == 1 and calls
    previous = copy.deepcopy(s.state)
    event, _ = make_event(s, callback_data(s, "cell1", revision=0))
    asyncio.run(app.matrix_callback(event))
    assert s.state == previous
    event, _ = make_event(s, callback_data(s, "cell0"))
    asyncio.run(app.matrix_callback(event))
    assert s.state == previous
    async def fail(*args): raise MessageNotModifiedError(request=None)
    monkeypatch.setattr(app, "edit_rich", fail)
    event, _ = make_event(s, callback_data(s, "cell1"))
    asyncio.run(app.matrix_callback(event))
    assert s.state == previous
    async def send(*args): raise RuntimeError("offline failure")
    monkeypatch.setattr(app, "send_rich", send)
    event = SimpleNamespace(sender_id=11, chat_id=12, client=object())
    with pytest.raises(RuntimeError): asyncio.run(app.launch(event))
    assert all(x.owner != 11 for x in app.STORE.sessions.values())


def test_rain_play_stop_busy_and_bounded_frames(arcade_modules, monkeypatch):
    _core, app = arcade_modules
    app.STORE.sessions.clear(); s = app.STORE.create(7, 8); s.message = 9; s.state.enter("rain")
    edits = []
    async def edit(*args): edits.append(args)
    async def sleep(_): return None
    monkeypatch.setattr(app, "edit_rich", edit); monkeypatch.setattr(app.asyncio, "sleep", sleep)
    event, _ = make_event(s, callback_data(s, "play"))
    asyncio.run(app.matrix_callback(event))
    assert len(edits) == app.FRAME_COUNT and s.state.frame == app.FRAME_COUNT
    assert not s.state.running
    # A lock is respected and stop is accepted while playback owns it.
    async def busy():
        async with s.lock:
            event, answers = make_event(s, callback_data(s, "step"))
            await app.matrix_callback(event)
            assert answers
            event, _ = make_event(s, callback_data(s, "stop"))
            await app.matrix_callback(event)
            assert s.stop
    asyncio.run(busy())


def test_lab_download_native_file_and_cooldown(arcade_modules, monkeypatch):
    _core, app = arcade_modules
    app.STORE.sessions.clear(); s = app.STORE.create(7, 8); s.message = 9; s.state.enter("lab")
    sent = []
    async def send_file(*args, **kwargs): sent.append((args, kwargs))
    client = SimpleNamespace(send_file=send_file)
    monkeypatch.setattr(type(app.COMPANION), "is_file", lambda _path: True)
    event, _ = make_event(s, callback_data(s, "download"), client=client)
    asyncio.run(app.matrix_callback(event))
    assert sent and sent[0][0][0] == s.chat
    assert sent[0][1]["force_document"] and sent[0][1]["parse_mode"] is None
    event, answers = make_event(s, callback_data(s, "download"), client=client)
    asyncio.run(app.matrix_callback(event))
    assert answers and "ten seconds" in answers[0][0][0]


def test_rpc_rejection_never_commits_candidate_and_timeout_expires_screen(arcade_modules, monkeypatch):
    from telethon.errors import RPCError
    _core, app = arcade_modules
    app.STORE.sessions.clear()
    s = app.STORE.create(7, 8); s.message = 9; s.state.enter("lights")
    original = copy.deepcopy(s.state)
    async def reject(*args):
        raise RPCError(None, "RICH_MESSAGE_BLOCKS_INVALID")
    monkeypatch.setattr(app, "edit_rich", reject)
    event, answers = make_event(s, callback_data(s, "cell0"))
    asyncio.run(app.matrix_callback(event))
    assert s.state == original
    assert "No fallback" in answers[-1][0][0]
    async def timeout(*args):
        raise asyncio.TimeoutError()
    monkeypatch.setattr(app, "edit_rich", timeout)
    event, answers = make_event(s, callback_data(s, "cell0"))
    asyncio.run(app.matrix_callback(event))
    assert s.state == original
    assert s.state.token not in app.STORE.sessions
    assert "screen expired" in answers[-1][0][0]


def test_every_scene_callback_is_pattern_valid_and_within_64_bytes(arcade_modules):
    core, app = arcade_modules
    state = core.Arcade("abcdef012345", revision=999999999, seed=230)
    for mode in core.MODES:
        state.enter(mode)
        for block in walk(core.payload(state).blocks):
            if not isinstance(block, types.PageBlockButtonRow):
                continue
            for button in block.buttons:
                if isinstance(button.type, types.InlineButtonTypeCallback):
                    assert len(button.type.data) <= 64
                    data = button.type.data.decode("ascii")
                    assert data == "lu:new" or app.CALLBACK_PATTERN.fullmatch(data)


def test_stop_interrupts_bounded_play_and_restores_idle_through_edit(arcade_modules, monkeypatch):
    _core, app = arcade_modules
    s = app.STORE.create(7, 8); s.message = 9; s.state.enter("rain")
    edits = []
    async def edit(*args):
        edits.append(args)
    async def sleep(_):
        s.stop = True
    monkeypatch.setattr(app, "edit_rich", edit)
    monkeypatch.setattr(app.asyncio, "sleep", sleep)
    event, _ = make_event(s, callback_data(s, "play"))
    asyncio.run(app.matrix_callback(event))
    assert s.state.frame == 1
    assert len(edits) == 2  # first frame + accepted idle restoration
    assert not s.state.running


def test_playback_failure_after_ack_gets_one_native_notice(arcade_modules, monkeypatch):
    from telethon.errors import RPCError
    _core, app = arcade_modules
    s = app.STORE.create(7, 8); s.message = 9; s.state.enter("rain")
    sent = []
    async def reject(*args):
        raise RPCError(None, "RICH_MESSAGE_BLOCKS_INVALID")
    async def notice(*args):
        sent.append(args)
    monkeypatch.setattr(app, "edit_rich", reject)
    monkeypatch.setattr(app, "send_rich", notice)
    event, answers = make_event(s, callback_data(s, "play"))
    asyncio.run(app.matrix_callback(event))
    assert len(answers) == 1
    assert len(sent) == 1 and isinstance(sent[0][2], types.InputRichMessage)
    assert s.state.frame == 0 and s.state.revision == 0


def test_real_matrix_decorators_register_aliases_and_reject_unauthorized(monkeypatch):
    import importlib.util
    from telethon import events
    import pyUltroid._misc._assistant as real
    registered = []
    class Registry:
        def add_event_handler(self, function, builder):
            registered.append((function, builder))
    monkeypatch.setattr(real, "asst", Registry())
    monkeypatch.setattr(real, "owner_and_sudos", lambda: {41})
    name = "matrix_arcade_real_decorators_test"
    spec = importlib.util.spec_from_file_location(name, ROOT / "assistant" / "matrix_arcade.py")
    app = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, app)
    spec.loader.exec_module(app)
    assert len(registered) == 2
    command, builder = registered[0]
    callback, callback_builder = registered[1]
    assert isinstance(builder, events.NewMessage)
    for value in ("/matrix", "/arcade", "/matrix@bot "):
        assert builder.pattern(value)
    assert not builder.pattern("/matrixish")
    assert callback_builder.match(b"mx:new")
    assert callback_builder.match(b"mx:abcdef123456:0:cell0")
    assert not callback_builder.match(b"mx:abcdef123456:0:execute")
    event = SimpleNamespace(sender_id=99)
    asyncio.run(command(event))  # no client required: denied before any side effect
    answers = []
    async def answer(*args, **kwargs):
        answers.append(args)
    event.answer = answer
    asyncio.run(callback(event))
    assert answers == [("Not allowed!",)]

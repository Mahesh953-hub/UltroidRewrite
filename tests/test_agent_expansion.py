"""Agent-expansion tests: parallel tool rounds, delegation, and the
Telegram bridge (tg tools, plugin_manager, raw MTProto allowlist).

Follows the established pattern in tests/test_agent.py: a stubbed
``pyUltroid`` namespace, fake provider SDKs, and plain ``asyncio.run``.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import time
import types

import pytest
from conftest import ROOT


def _install_stub_namespace():
    root = types.ModuleType("pyUltroid")
    root.__path__ = [str(ROOT / "pyUltroid")]
    root.LOGS = logging.getLogger("agent-expansion-tests")
    root.asst = None
    root.ultroid_bot = None
    root.udB = None
    sys.modules["pyUltroid"] = root

    startup = types.ModuleType("pyUltroid.startup")
    startup.__path__ = []
    logger = types.ModuleType("pyUltroid.startup.logger")
    logger.LOGS = root.LOGS
    sys.modules["pyUltroid.startup"] = startup
    sys.modules["pyUltroid.startup.logger"] = logger

    configs = types.ModuleType("pyUltroid.configs")
    from types import SimpleNamespace

    configs.Var = SimpleNamespace()
    sys.modules["pyUltroid.configs"] = configs
    return root


@pytest.fixture
def agent_modules(module_isolation):
    with module_isolation("pyUltroid"):
        root = _install_stub_namespace()
        importlib.import_module("pyUltroid.agent")
        yield SimpleNamespace2(
            root=root,
            pkg=sys.modules["pyUltroid.agent"],
            tools=sys.modules["pyUltroid.agent._tools"],
            agent_mod=sys.modules["pyUltroid.agent._agent"],
            bridge=sys.modules["pyUltroid.agent._tgbridge"],
            runtime=sys.modules["pyUltroid.agent._runtime"],
            ctx=sys.modules["pyUltroid.shell._context"],
        )


class SimpleNamespace2:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeMini:
    """Scripted MiniAI stand-in: pops one AIResponse per round."""

    def __init__(self, turns):
        self._turns = list(turns)

    async def mini_respond(self, **kwargs):
        return self._turns.pop(0)


def _response(mod, **kw):
    return mod.AIResponse(provider="fake", model="fake-model", **kw)


def _tool_call(name, args, call_id="c1"):
    import json

    return {"name": name, "arguments": json.dumps(args), "id": call_id}


# ── Schemas / caps ──────────────────────────────────────────────────────────


def test_new_tools_registered(agent_modules):
    names = {t["function"]["name"] for t in agent_modules.tools.TOOL_SCHEMAS}
    for expected in (
        "tg_route_send",
        "send_text_file",
        "tg_read",
        "plugin_manager",
        "raw_mtproto",
        "web_fetch",
        "think",
        "delegate",
    ):
        assert expected in names, f"{expected} missing from TOOL_SCHEMAS"
        assert expected in agent_modules.tools.TOOL_REQUIRED_CAPS
    route_send = next(
        schema["function"]
        for schema in agent_modules.tools.TOOL_SCHEMAS
        if schema["function"]["name"] == "tg_route_send"
    )
    assert route_send["parameters"]["properties"]["rich"]["type"] == "boolean"


def test_group_safe_excludes_escalations(agent_modules):
    caps = agent_modules.runtime.GROUP_SAFE_CAPS
    assert "agent.spawn" not in caps
    assert "tg.raw" not in caps
    assert agent_modules.tools.Cap.TG_SEND_FILE not in caps
    assert (
        agent_modules.tools.Cap.TG_SEND_FILE in agent_modules.tools.DEFAULT_AGENT_CAPS
    )
    # owner/sudo default keeps delegation and read access
    defaults = agent_modules.tools.DEFAULT_AGENT_CAPS
    assert "agent.spawn" in defaults
    assert "tg.read" in defaults
    assert "tg.raw" not in defaults


# ── Parallel tool execution ─────────────────────────────────────────────────


def test_tool_calls_in_one_round_run_concurrently(agent_modules):
    tools_mod = agent_modules.tools
    agent_mod = agent_modules.agent_mod

    async def slow_think(self, thought):
        await asyncio.sleep(0.08)
        return {"ok": True}

    original = tools_mod.ToolExecutor._tool_think
    tools_mod.ToolExecutor._tool_think = slow_think
    try:
        fake = FakeMini(
            [
                _response(
                    agent_modules.pkg,
                    content="",
                    tool_calls=[
                        _tool_call("think", {"thought": "a"}, "c1"),
                        _tool_call("think", {"thought": "b"}, "c2"),
                    ],
                ),
                _response(agent_modules.pkg, content="done"),
            ]
        )
        agent = agent_mod.UltroidAgent(mini_ai=fake)
        t0 = time.perf_counter()
        resp = asyncio.run(
            agent.respond("x", chat_id=1, use_history=False, caps=frozenset())
        )
        elapsed = time.perf_counter() - t0
        assert resp.ok
        assert resp.content == "done"
        assert resp.tool_rounds == 1
        assert sorted(resp.tools_used) == ["think", "think"]
        # Two 80 ms sleeps running concurrently finish well under 160 ms.
        assert elapsed < 0.15, f"tool calls appear sequential ({elapsed:.3f}s)"
    finally:
        tools_mod.ToolExecutor._tool_think = original


def test_identical_tool_calls_in_one_round_execute_once(agent_modules):
    tools_mod = agent_modules.tools
    calls = []

    async def counted_think(self, thought):
        calls.append(thought)
        return {"ok": True}

    original = tools_mod.ToolExecutor._tool_think
    tools_mod.ToolExecutor._tool_think = counted_think
    try:
        fake = FakeMini(
            [
                _response(
                    agent_modules.pkg,
                    content="",
                    tool_calls=[
                        _tool_call("think", {"thought": "same"}, "c1"),
                        _tool_call("think", {"thought": "same"}, "c2"),
                    ],
                ),
                _response(agent_modules.pkg, content="done"),
            ]
        )
        agent = agent_modules.agent_mod.UltroidAgent(mini_ai=fake)
        result = asyncio.run(
            agent.respond("x", chat_id=1, use_history=False, caps=frozenset())
        )
        assert result.ok
        assert result.content == "done"
        assert calls == ["same"]
    finally:
        tools_mod.ToolExecutor._tool_think = original


# ── Delegation depth ────────────────────────────────────────────────────────


def test_delegate_refused_at_depth_limit(agent_modules):
    executor = agent_modules.tools.ToolExecutor(chat_id=1, depth=2)
    result = asyncio.run(executor.execute("delegate", {"task": "x"}))
    assert result.get("denied")


def test_delegate_refused_without_spawn_cap(agent_modules):
    executor = agent_modules.tools.ToolExecutor(chat_id=1, caps=frozenset({"tg.read"}))
    result = asyncio.run(executor.execute("delegate", {"task": "x"}))
    assert result.get("denied")


def test_delegate_caps_strip_spawn(agent_modules, monkeypatch):
    """The sub-agent's executor must never carry agent.spawn."""
    captured = {}
    tools_mod = agent_modules.tools
    agent_mod = agent_modules.agent_mod

    parent = tools_mod.ToolExecutor(
        chat_id=1,
        caps=frozenset(tools_mod.DEFAULT_AGENT_CAPS),
        depth=0,
    )

    class FakeSubAgent:
        def __init__(self):
            pass

        async def respond(self, **kwargs):
            captured["caps"] = kwargs.get("caps")
            captured["depth"] = kwargs.get("_depth")
            return agent_modules.agent_mod.AgentResponse(
                content="sub done",
                provider="fake",
                model="fake-model",
                tools_used=[],
                tool_rounds=0,
            )

    monkeypatch.setattr(agent_mod, "UltroidAgent", FakeSubAgent)
    result = asyncio.run(parent.execute("delegate", {"task": "research"}))
    assert result["ok"]
    assert "agent.spawn" not in captured["caps"]
    assert captured["depth"] == 1


# ── plugin_manager capability escalation ────────────────────────────────────


def test_plugin_load_denied_by_default(agent_modules):
    executor = agent_modules.tools.ToolExecutor(
        chat_id=1, caps=frozenset(agent_modules.tools.DEFAULT_AGENT_CAPS)
    )
    result = asyncio.run(
        executor.execute("plugin_manager", {"action": "load", "plugin": "echo"})
    )
    assert result.get("denied")


def test_plugin_list_allowed_with_default_caps(agent_modules):
    executor = agent_modules.tools.ToolExecutor(
        chat_id=1, caps=frozenset(agent_modules.tools.DEFAULT_AGENT_CAPS)
    )
    result = asyncio.run(executor.execute("plugin_manager", {"action": "list"}))
    # list only needs PLUGIN_READ (granted); it may fail on missing runtime
    # state, but it must NOT be a capability denial.
    assert not result.get("denied")


# ── Telegram bridge ─────────────────────────────────────────────────────────


def test_parse_entity_ref(agent_modules):
    parse = agent_modules.bridge.parse_entity_ref
    assert parse("123456") == 123456
    assert parse("-100123") == -100123
    assert parse("@user") == "@user"
    assert parse("user") == "user"
    assert parse("https://t.me/somechannel") == "somechannel"
    assert parse("https://t.me/joinchat/AbCdEf") == "AbCdEf"
    assert parse("") is None
    assert parse(None) is None


def _install_fake_telethon(monkeypatch, flood_seconds=None, rpc_error=None):
    """Fake telethon.errors so run_tg_call failure paths are testable."""
    errors_mod = types.ModuleType("telethon.errors")

    class FloodWaitError(Exception):
        def __init__(self, seconds=30):
            self.seconds = seconds

    class RPCError(Exception):
        pass

    errors_mod.FloodWaitError = FloodWaitError
    errors_mod.RPCError = RPCError
    telethon = types.ModuleType("telethon")
    telethon.errors = errors_mod
    monkeypatch.setitem(sys.modules, "telethon", telethon)
    monkeypatch.setitem(sys.modules, "telethon.errors", errors_mod)
    return FloodWaitError, RPCError


def _install_fake_richmsg(monkeypatch):
    """Keep bridge tests independent of core package startup imports."""
    core = types.ModuleType("pyUltroid._core")
    core.__path__ = []
    richmsg = types.ModuleType("pyUltroid._core.richmsg")

    class RichMarkup:
        def __init__(self, text, format="html"):
            self.text = text
            self.format = format

    async def send_rich(client, entity, content, **kwargs):
        return await client.send_message(entity, content.text, **kwargs)

    def blocks_to_plain_text(content):
        return content.text

    async def client_can_send_rich_message(client):
        me = getattr(client, "me", None)
        return bool(getattr(client, "_bot", False) or getattr(me, "premium", False))

    richmsg.RichMarkup = RichMarkup
    richmsg.send_rich = send_rich
    richmsg.blocks_to_plain_text = blocks_to_plain_text
    richmsg.client_can_send_rich_message = client_can_send_rich_message
    monkeypatch.setitem(sys.modules, "pyUltroid._core", core)
    monkeypatch.setitem(sys.modules, "pyUltroid._core.richmsg", richmsg)


def test_run_tg_call_flood_wait(agent_modules, monkeypatch):
    FloodWaitError, _ = _install_fake_telethon(monkeypatch)

    async def raise_flood():
        raise FloodWaitError(42)

    result = asyncio.run(agent_modules.bridge.run_tg_call(raise_flood))
    assert result["ok"] is False
    assert result["error"] == "FloodWait"
    assert result["wait_seconds"] == 42


def test_send_via_master_routing(agent_modules, monkeypatch):
    _install_fake_telethon(monkeypatch)
    _install_fake_richmsg(monkeypatch)
    sent = {}

    async def fake_send(entity, text, **kwargs):
        sent["entity"] = entity
        sent["text"] = text
        sent["kwargs"] = kwargs
        from types import SimpleNamespace

        return SimpleNamespace(id=7, peer_id=SimpleNamespace(user_id=555))

    from types import SimpleNamespace

    user_client = SimpleNamespace(send_message=fake_send)
    master = SimpleNamespace(
        route=lambda: user_client,
        user=user_client,
        bot=None,
        business=None,
        available=lambda: {"user"},
    )
    monkeypatch.setattr(agent_modules.root, "master", master, raising=False)

    result = asyncio.run(
        agent_modules.bridge.send_via_master(
            "hello", "@someone", route="auto", reply_to=3, parse_mode="md"
        )
    )
    assert result["ok"] is True
    assert result["route"] == "auto"
    assert result["message_id"] == 7
    assert sent["kwargs"]["reply_to"] == 3
    assert sent["text"] == "hello"
    assert sent["kwargs"]["parse_mode"] is None


def test_send_via_master_rejects_rich_message_for_nonpremium_user(
    agent_modules, monkeypatch
):
    _install_fake_telethon(monkeypatch)
    _install_fake_richmsg(monkeypatch)
    from types import SimpleNamespace

    sent = []

    async def fake_send(*args, **kwargs):
        sent.append((args, kwargs))

    user_client = SimpleNamespace(
        _bot=False,
        me=SimpleNamespace(bot=False, premium=False),
        send_message=fake_send,
    )
    master = SimpleNamespace(
        route=lambda: user_client,
        user=user_client,
        bot=None,
        business=None,
        available=lambda: {"user"},
    )
    monkeypatch.setattr(agent_modules.root, "master", master, raising=False)

    result = asyncio.run(
        agent_modules.bridge.send_via_master(
            "**bold**", "@someone", route="user", rich=True
        )
    )

    assert result["ok"] is False
    assert "rich=false" in result["error"]
    assert sent == []


def test_send_via_master_missing_route(agent_modules, monkeypatch):
    _install_fake_telethon(monkeypatch)
    _install_fake_richmsg(monkeypatch)
    from types import SimpleNamespace

    master = SimpleNamespace(
        route=lambda: None.__class__,  # never called
        user=None,
        bot=None,
        business=None,
        available=lambda: set(),
    )
    monkeypatch.setattr(agent_modules.root, "master", master, raising=False)
    result = asyncio.run(agent_modules.bridge.send_via_master("hi", "@x", route="bot"))
    assert result["ok"] is False
    assert "no connected client" in result["error"]


# ── Raw MTProto allowlist ───────────────────────────────────────────────────


def _install_fake_telethon_functions(monkeypatch, build_captures):
    tl = types.ModuleType("telethon.tl")
    functions = types.ModuleType("telethon.tl.functions")
    messages = types.ModuleType("telethon.tl.functions.messages")
    channels = types.ModuleType("telethon.tl.functions.channels")

    class GetHistoryRequest:
        def __init__(self, **kwargs):
            build_captures["history"] = kwargs

    class SendMessageRequest:
        def __init__(self, **kwargs):
            build_captures["send"] = kwargs

    class GetChannelsRequest:
        def __init__(self, **kwargs):
            build_captures["channels"] = kwargs

    messages.GetHistoryRequest = GetHistoryRequest
    messages.SendMessageRequest = SendMessageRequest
    channels.GetChannelsRequest = GetChannelsRequest
    functions.messages = messages
    functions.channels = channels
    tl.functions = functions
    monkeypatch.setitem(sys.modules, "telethon.tl", tl)
    monkeypatch.setitem(sys.modules, "telethon.tl.functions", functions)
    monkeypatch.setitem(sys.modules, "telethon.tl.functions.messages", messages)
    monkeypatch.setitem(sys.modules, "telethon.tl.functions.channels", channels)


def test_raw_invoke_denies_write_request(agent_modules, monkeypatch):
    _install_fake_telethon_functions(monkeypatch, {})
    client = object()
    result = asyncio.run(
        agent_modules.bridge.invoke_raw(
            client, "messages.SendMessageRequest", {"message": "hi"}
        )
    )
    assert result["denied"]


def test_raw_invoke_runs_allowlisted_request(agent_modules, monkeypatch):
    captured = {}
    _install_fake_telethon_functions(monkeypatch, captured)
    _install_fake_telethon(monkeypatch)

    async def fake_client(request):
        return "raw-ok"

    result = asyncio.run(
        agent_modules.bridge.invoke_raw(
            fake_client,
            "messages.GetHistoryRequest",
            {"peer": "x", "limit": 5},
        )
    )
    assert result["ok"] is True
    assert result["result_repr"] == repr("raw-ok")
    assert captured["history"]["limit"] == 5


def test_raw_resolve_unknown_namespace(agent_modules, monkeypatch):
    resolve = agent_modules.bridge.resolve_raw_function
    assert resolve("messages.SendMessageRequest") is None
    assert resolve("not.on.the.list") is None
    assert resolve("bogus.Foo") is None


# ── web_fetch scheme gate ───────────────────────────────────────────────────


def test_web_fetch_denies_non_http(agent_modules):
    executor = agent_modules.tools.ToolExecutor(
        chat_id=1, caps=frozenset(agent_modules.tools.DEFAULT_AGENT_CAPS)
    )
    result = asyncio.run(executor.execute("web_fetch", {"url": "file:///etc/passwd"}))
    assert "error" in result

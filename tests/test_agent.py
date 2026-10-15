"""W2-C — agent-hardening tests (pyUltroid/agent + plugins/brain.py contract).

Covers:

* W2-C.1 — provider-abstraction unit tests with mocked SDKs/HTTP for
  OpenAI, Anthropic, Gemini (google-genai), Groq, Ollama and the
  OpenAI-compatible endpoint. No network, no real SDK imports: each
  provider imports its SDK lazily inside the method, so tests inject fakes
  via ``sys.modules`` (openai/anthropic/groq/google.genai) or monkeypatch
  ``aiohttp.ClientSession`` (Ollama).
* W2-C.2 — Ollama is the default local provider path: FREE tier is tried
  first for every non-tool request, whatever the intent's target tier;
  tool-call requests deliberately skip the local tier.
* W2-C.3 — MiniAI's response cache lives in udB ({"exp", "v"} records with
  a timestamp TTL), uses identifier-safe keys, and disables cleanly when
  udB is absent. No dedicated redis client exists on MiniAI anymore.
* W2-C.4 — ToolExecutor capability enforcement: per-tool required caps,
  arg-level UPDATE_APPLY escalation, shell cap forwarding and the
  shell_execute timeout clamp.
* W2-C.5 — AgentLifecycle registration into the universal plugin lifecycle
  (idempotent, no-op without the runtime), plus start/stop hook behaviour.

Style follows the existing suite: namespace-isolated ``pyUltroid`` stubs
(see ``tests/test_core_architecture.py`` / ``tests/test_updater_engine.py``)
and plain ``asyncio.run`` — no pytest-asyncio dependency.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import sys
import time
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── Namespace isolation ─────────────────────────────────────────────────────


class FakeUdB:
    """Dict-backed stand-in for pyUltroid's _BaseDatabase key API."""

    def __init__(self, initial=None):
        self.store = dict(initial or {})

    def get_key(self, key):
        return self.store.get(key)

    def set_key(self, key, value, cache_only=False):
        self.store[key] = value
        return True

    def del_key(self, key):
        self.store.pop(key, None)
        return True

    def keys(self):
        return list(self.store)


def _install_stub_namespace():
    """Stub the pyUltroid root + startup.logger + configs so agent modules
    import without the production bootstrap. The real pyUltroid.shell package
    (import-light) loads through the stub root's __path__."""
    root = types.ModuleType("pyUltroid")
    root.__path__ = [str(ROOT / "pyUltroid")]
    root.LOGS = logging.getLogger("agent-tests")
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
    configs.Var = SimpleNamespace()  # no AI keys — providers start unconfigured
    sys.modules["pyUltroid.configs"] = configs
    return root


@pytest.fixture
def agent_modules(module_isolation):
    """Import pyUltroid.agent fresh inside an isolated, stubbed namespace."""
    with module_isolation("pyUltroid"):
        root = _install_stub_namespace()
        importlib.import_module("pyUltroid.agent")
        yield SimpleNamespace(
            root=root,
            pkg=sys.modules["pyUltroid.agent"],
                    providers=sys.modules["pyUltroid.agent._providers"],
            mini_mod=sys.modules["pyUltroid.agent._mini"],
            tools_mod=sys.modules["pyUltroid.agent._tools"],
            agent_mod=sys.modules["pyUltroid.agent._agent"],
            lifecycle=sys.modules["pyUltroid.agent._lifecycle"],
        )


# ── SDK fakes ───────────────────────────────────────────────────────────────


def _fake_openai_module(captured, content="hello from openai", tool_calls=None):
    """Fake `openai` module: AsyncOpenAI(**kw).chat.completions.create(**kw)."""
    mod = types.ModuleType("openai")

    class FakeCompletions:
        async def create(self, **kwargs):
            captured["create_kwargs"] = kwargs
            msg = SimpleNamespace(content=content, tool_calls=tool_calls)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=msg)],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
            )

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    mod.AsyncOpenAI = FakeAsyncOpenAI
    return mod


def _fake_anthropic_module(captured, text="hello from claude", tool_use=None):
    mod = types.ModuleType("anthropic")

    class FakeMessages:
        async def create(self, **kwargs):
            captured["create_kwargs"] = kwargs
            blocks = [SimpleNamespace(type="text", text=text)]
            if tool_use:
                blocks.append(SimpleNamespace(**tool_use))  # no .text attr
            return SimpleNamespace(
                content=blocks,
                usage=SimpleNamespace(input_tokens=9, output_tokens=4),
            )

    class FakeAsyncAnthropic:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.messages = FakeMessages()

    mod.AsyncAnthropic = FakeAsyncAnthropic
    return mod


def _fake_google_genai(captured, text="hello from gemini", stream_chunks=("a", "b")):
    """Fake `google` + `google.genai` modules for the google-genai SDK."""
    genai = types.ModuleType("google.genai")

    class _Stream:
        def __aiter__(self):
            async def gen():
                for chunk in stream_chunks:
                    yield SimpleNamespace(text=chunk)

            return gen()

    class FakeModels:
        async def generate_content(self, model=None, contents=None, config=None):
            captured["model"] = model
            captured["contents"] = contents
            captured["config"] = config
            return SimpleNamespace(
                text=text,
                usage_metadata=SimpleNamespace(
                    prompt_token_count=6,
                    candidates_token_count=2,
                ),
            )

        async def generate_content_stream(self, model=None, contents=None, config=None):
            captured["stream_model"] = model
            return _Stream()

    class FakeClient:
        def __init__(self, api_key=None):
            captured["api_key"] = api_key
            self.aio = SimpleNamespace(models=FakeModels())

    genai.Client = FakeClient

    google = types.ModuleType("google")
    google.genai = genai
    google.__path__ = []  # mark as package for `from google import genai`
    return {"google": google, "google.genai": genai}


def _fake_groq_module(captured, content="hello from groq"):
    mod = types.ModuleType("groq")

    class FakeCompletions:
        async def create(self, **kwargs):
            captured["create_kwargs"] = kwargs
            msg = SimpleNamespace(content=content)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=msg)],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=3),
            )

    class FakeAsyncGroq:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    mod.AsyncGroq = FakeAsyncGroq
    return mod


class _FakeAioContent:
    """Async iterator over response body lines (Ollama streaming)."""

    def __init__(self, lines):
        self._lines = lines

    def __aiter__(self):
        async def gen():
            for line in self._lines:
                yield line

        return gen()


class _FakeAioResp:
    def __init__(self, data=None, status=200, lines=()):
        self._data = data or {}
        self.status = status
        self.content = _FakeAioContent(lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._data


class FakeAioSession:
    """Stand-in for aiohttp.ClientSession. Class attrs drive behaviour."""

    tags_status = 200
    chat_data: dict = {}
    chat_lines: tuple = ()
    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, timeout=None):
        self.calls.append(("GET", url))
        return _FakeAioResp(status=self.tags_status)

    def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url, json))
        return _FakeAioResp(data=dict(self.chat_data), lines=self.chat_lines)


@pytest.fixture
def fake_aiohttp(monkeypatch):
    import aiohttp

    FakeAioSession.calls = []
    FakeAioSession.tags_status = 200
    FakeAioSession.chat_data = {}
    FakeAioSession.chat_lines = ()
    monkeypatch.setattr(aiohttp, "ClientSession", FakeAioSession)
    return FakeAioSession


# ── W2-C.1 — provider: registry + import safety ─────────────────────────────


def test_registry_lists_all_supported_providers(agent_modules):
    p = agent_modules.providers
    status = p.ProviderRegistry().provider_health()
    assert {
        "ollama",
        "openai_compat",
        "groq",
        "gemini",
        "openrouter",
        "openai",
        "anthropic",
    } <= set(status)
    # Import-safety: with no keys configured only Ollama reports configured
    # (it self-checks reachability later), and nothing raised on import.
    assert status["ollama"] is True
    assert all(v is False for k, v in status.items() if k != "ollama")
    # W2-C.2: the cheapest provider is the local one.
    assert p.ProviderRegistry().provider_cheapest().name == "ollama"


def test_unconfigured_providers_return_error_not_raise(agent_modules):
    p = agent_modules.providers
    msgs = [p.AIMessage(role="user", content="hi")]
    for name, missing in [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("groq", "GROQ_API_KEY"),
        ("openai_compat", "COMPAT_BASE_URL"),
    ]:
        provider = p.ProviderRegistry().provider_by_name(name)
        resp = asyncio.run(provider.complete(msgs))
        assert not resp.ok
        assert missing in resp.error


# ── W2-C.1 — provider: Ollama ────────────────────────────────────────────────


def test_ollama_complete_ok(agent_modules, fake_aiohttp):
    p = agent_modules.providers
    fake_aiohttp.chat_data = {
        "message": {"content": "local says hi"},
        "prompt_eval_count": 3,
        "eval_count": 5,
    }
    resp = asyncio.run(
        p.ollama.complete(
            [p.AIMessage(role="user", content="hello")],
            system="be brief",
        )
    )
    assert resp.ok
    assert resp.content == "local says hi"
    assert resp.provider == "ollama"
    assert resp.model == "llama3"  # built-in default
    assert resp.tokens_in == 3 and resp.tokens_out == 5
    post = [c for c in fake_aiohttp.calls if c[0] == "POST"][0]
    assert post[1].endswith("/api/chat")
    assert post[2]["system"] == "be brief"
    assert post[2]["stream"] is False


def test_ollama_not_running(agent_modules, fake_aiohttp):
    fake_aiohttp.tags_status = 500
    p = agent_modules.providers
    resp = asyncio.run(p.ollama.complete([p.AIMessage(role="user", content="hi")]))
    assert not resp.ok
    assert resp.error == "Ollama not running"


def test_ollama_stream_ok(agent_modules, fake_aiohttp):
    fake_aiohttp.chat_lines = (
        json.dumps({"message": {"content": "he"}}).encode(),
        json.dumps({"message": {"content": "y"}, "done": True}).encode(),
    )
    p = agent_modules.providers

    async def collect():
        return [
            c async for c in p.ollama.stream([p.AIMessage(role="user", content="hi")])
        ]

    assert asyncio.run(collect()) == ["he", "y"]


def test_ollama_default_model_resolves_udb_then_var(agent_modules):
    """W2-C.2: `.ollama use` writes the unified AGENT key — the provider reads
    it back. The legacy OLLAMA_MODEL name is migration-only input."""
    p = agent_modules.providers
    fresh = p.OllamaProvider()
    assert fresh.default_model == "llama3"  # built-in default

    agent_modules.root.udB = FakeUdB({"AGENT_LLAMA_OLLAMA_MODEL": "mistral"})
    fresh2 = p.OllamaProvider()
    assert fresh2.default_model == "mistral"  # the database wins


def test_ollama_default_model_observes_udb_changes(agent_modules):
    """Regression: the getter must NOT memoize the udB read — a later udB
    change is observed on the SAME provider instance."""
    p = agent_modules.providers
    agent_modules.root.udB = FakeUdB({"AGENT_LLAMA_OLLAMA_MODEL": "mistral"})
    provider = p.OllamaProvider()
    assert provider.default_model == "mistral"  # first read

    agent_modules.root.udB.set_key("AGENT_LLAMA_OLLAMA_MODEL", "qwen2")
    assert provider.default_model == "qwen2"  # observed live

    # Setter = in-process fallback, consulted only when the database has no key.
    agent_modules.root.udB.del_key("AGENT_LLAMA_OLLAMA_MODEL")
    provider.default_model = "phi3"  # brain.py setter path
    assert provider.default_model == "phi3"

    # A re-appearing database key is the source of truth again.
    agent_modules.root.udB.set_key("AGENT_LLAMA_OLLAMA_MODEL", "llama3.1")
    assert provider.default_model == "llama3.1"


# ── W2-C.1 — provider: OpenAI ────────────────────────────────────────────────


def test_openai_complete_ok_with_tool_calls(agent_modules, monkeypatch):
    p = agent_modules.providers
    agent_modules.root.udB = FakeUdB(
        {
            "AGENT_CLOUD_OPENAI_KEY": "sk-test",
            "AGENT_CLOUD_OPENAI_MODEL": "gpt-4o-mini",
        }
    )
    captured = {}
    tc = SimpleNamespace(
        id="call1",
        function=SimpleNamespace(name="shell_execute", arguments='{"command": "ls"}'),
    )
    monkeypatch.setitem(
        sys.modules, "openai", _fake_openai_module(captured, tool_calls=[tc])
    )

    resp = asyncio.run(
        p.ProviderRegistry().provider_by_name("openai").complete(
            [p.AIMessage(role="user", content="list files")],
            tools=[{"type": "function", "function": {"name": "shell_execute"}}],
            system="sys",
        )
    )
    assert resp.ok and resp.provider == "openai"
    assert resp.tokens_in == 11 and resp.tokens_out == 7
    assert resp.tool_calls == [
        {"id": "call1", "name": "shell_execute", "arguments": '{"command": "ls"}'}
    ]
    assert captured["client_kwargs"]["api_key"] == "sk-test"
    assert captured["create_kwargs"]["messages"][0] == {
        "role": "system",
        "content": "sys",
    }
    assert captured["create_kwargs"]["tool_choice"] == "auto"


# ── W2-C.1 — provider: Anthropic ─────────────────────────────────────────────


def test_anthropic_complete_ok_with_tool_use(agent_modules, monkeypatch):
    p = agent_modules.providers
    provider = p.ProviderRegistry().provider_by_name("anthropic")
    provider.api_key = "sk-ant-test"  # Var snapshot was empty; set directly
    captured = {}
    monkeypatch.setitem(
        sys.modules,
        "anthropic",
        _fake_anthropic_module(
            captured,
            tool_use={
                "type": "tool_use",
                "id": "t1",
                "name": "web_search",
                "input": {"query": "x"},
            },
        ),
    )

    msgs = [
        p.AIMessage(role="system", content="from-history"),
        p.AIMessage(role="user", content="search x"),
    ]
    resp = asyncio.run(provider.complete(msgs))
    assert resp.ok and resp.content == "hello from claude"
    assert resp.tokens_in == 9 and resp.tokens_out == 4
    # tool_use blocks normalise to the OpenAI-shaped {"id", "name", "arguments"}
    assert resp.tool_calls == [
        {"id": "t1", "name": "web_search", "arguments": {"query": "x"}}
    ]
    sent = captured["create_kwargs"]
    # system extracted from history; system-role messages not in messages list
    assert sent["system"] == "from-history"
    assert all(m["role"] != "system" for m in sent["messages"])
    assert captured["client_kwargs"]["api_key"] == "sk-ant-test"


# ── W2-C.1 — provider: Gemini (google-genai) ────────────────────────────────


def test_gemini_complete_ok_google_genai(agent_modules, monkeypatch):
    p = agent_modules.providers
    provider = p.ProviderRegistry().provider_by_name("gemini")
    provider.api_key = "AIza-test"
    captured = {}
    for name, module in _fake_google_genai(captured).items():
        monkeypatch.setitem(sys.modules, name, module)

    msgs = [
        p.AIMessage(role="system", content="be terse"),
        p.AIMessage(role="user", content="hi"),
        p.AIMessage(role="assistant", content="hello"),
        p.AIMessage(role="user", content="how are you"),
    ]
    resp = asyncio.run(provider.complete(msgs, max_tokens=128, temperature=0.2))
    assert resp.ok and resp.content == "hello from gemini"
    assert resp.tokens_in == 6 and resp.tokens_out == 2
    assert captured["api_key"] == "AIza-test"
    # google-genai call shape: contents carry user/model roles, system goes
    # into config.system_instruction.
    roles = [c["role"] for c in captured["contents"]]
    assert roles == ["user", "model", "user"]
    assert captured["config"]["system_instruction"] == "be terse"
    assert captured["config"]["max_output_tokens"] == 128


def test_gemini_stream_ok(agent_modules, monkeypatch):
    p = agent_modules.providers
    provider = p.ProviderRegistry().provider_by_name("gemini")
    provider.api_key = "AIza-test"
    captured = {}
    for name, module in _fake_google_genai(
        captured, stream_chunks=("ge", "mini")
    ).items():
        monkeypatch.setitem(sys.modules, name, module)

    async def collect():
        return [
            c async for c in provider.stream([p.AIMessage(role="user", content="hi")])
        ]

    assert asyncio.run(collect()) == ["ge", "mini"]


def test_gemini_does_not_import_eol_package(agent_modules):
    """The EOL google.generativeai SDK must not appear anywhere in the module."""
    import inspect

    src = inspect.getsource(agent_modules.providers)
    assert "google.generativeai" not in src
    assert "generativeai" not in src


# ── W2-C.1 — provider: Groq ──────────────────────────────────────────────────


def test_groq_complete_ok(agent_modules, monkeypatch):
    p = agent_modules.providers
    provider = p.ProviderRegistry().provider_by_name("groq")
    provider.api_key = "gsk-test"
    captured = {}
    monkeypatch.setitem(sys.modules, "groq", _fake_groq_module(captured))

    resp = asyncio.run(
        provider.complete(
            [p.AIMessage(role="user", content="hi")],
            system="sys",
        )
    )
    assert resp.ok and resp.content == "hello from groq"
    assert resp.tokens_in == 5 and resp.tokens_out == 3
    assert captured["client_kwargs"]["api_key"] == "gsk-test"
    assert captured["create_kwargs"]["messages"][0] == {
        "role": "system",
        "content": "sys",
    }


# ── W2-C.1 — provider: OpenAI-compatible ────────────────────────────────────


def test_openai_compat_complete_ok(agent_modules, monkeypatch):
    p = agent_modules.providers
    provider = p.ProviderRegistry().provider_by_name("openai_compat")
    provider.base_url = "http://localhost:1234/v1"
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(captured))

    resp = asyncio.run(provider.complete([p.AIMessage(role="user", content="hi")]))
    assert resp.ok and resp.content == "hello from openai"
    assert captured["client_kwargs"]["base_url"] == "http://localhost:1234/v1"
    assert captured["client_kwargs"]["api_key"] == "none"  # built-in default


# ── W2-C.2 — local-first routing ─────────────────────────────────────────────


def test_build_order_local_first_for_non_tool_requests(agent_modules):
    """Regression: previously tiers below min_tier were excluded, so a
    CODE_HELP/UNKNOWN request never tried Ollama and failed when no cloud
    keys were set."""
    # Configure one cloud provider so the escalation tier is populated.
    agent_modules.root.udB = FakeUdB({"AGENT_CLOUD_OPENAI_KEY": "sk-test"})
    mini = agent_modules.mini_mod.MiniAI()
    CostTier = agent_modules.providers.CostTier
    order = mini._build_order(CostTier.MEDIUM, allow_local=True)
    names = [p.name for p in order]
    assert names[0] == "ollama"  # FREE tier first
    assert names.count("ollama") == 1  # deduped
    assert "openai" in names  # escalation still present
    assert names.index("ollama") < names.index("openai")


def test_build_order_skips_local_for_tool_requests(agent_modules):
    mini = agent_modules.mini_mod.MiniAI()
    CostTier = agent_modules.providers.CostTier
    order = mini._build_order(CostTier.MEDIUM, allow_local=False)
    assert all(p.cost_tier != CostTier.FREE for p in order)


def test_forced_unconfigured_provider_never_silently_falls_back(agent_modules):
    p = agent_modules.providers
    mini = agent_modules.mini_mod.MiniAI()
    resp = asyncio.run(
        mini.mini_respond(
            [p.AIMessage(role="user", content="hello")],
            force_provider="openai",
            use_cache=False,
        )
    )
    assert not resp.ok
    assert resp.provider == "openai"
    assert "not configured" in resp.error


def test_forced_provider_failure_is_returned(agent_modules, monkeypatch):
    p = agent_modules.providers
    mini = agent_modules.mini_mod.MiniAI()
    async def fail(*args, **kwargs):
        return p.AIResponse("", "openai", "m", error="upstream down")

    # Patched on the class: the registry is no longer a module singleton, so a
    # fresh MiniAI builds its own provider instances.
    monkeypatch.setattr(p.OpenAIProvider, "is_configured", lambda self: True)
    monkeypatch.setattr(p.OpenAIProvider, "complete", fail)
    resp = asyncio.run(
        mini.mini_respond(
            [p.AIMessage(role="user", content="hello")],
            force_provider="openai",
            use_cache=False,
        )
    )
    assert not resp.ok
    assert resp.provider == "openai"
    assert resp.error == "upstream down"


def test_respond_routes_to_ollama_by_default(agent_modules, fake_aiohttp):
    """End-to-end W2-C.2: no API keys, Ollama up → .ai-style request succeeds
    via the local provider even for a non-trivial intent."""
    fake_aiohttp.chat_data = {"message": {"content": "local answer"}}
    mini = agent_modules.mini_mod.MiniAI()
    msgs = [
        agent_modules.providers.AIMessage(
            role="user", content="write a python function"
        )
    ]
    resp = asyncio.run(mini.mini_respond(msgs, use_cache=False))
    assert resp.ok
    assert resp.provider == "ollama"
    assert resp.content == "local answer"


def test_agent_respond_end_to_end_via_local(agent_modules, fake_aiohttp):
    fake_aiohttp.chat_data = {"message": {"content": "agent local answer"}}
    agent = agent_modules.agent_mod.UltroidAgent()
    resp = asyncio.run(
        agent.respond(
            "explain this code",
            chat_id=4242,
            use_tools=False,
            use_cache=False,
        )
    )
    assert resp.ok
    assert resp.provider == "ollama"
    # History recorded for the chat
    hist = agent.history(4242).get()
    assert [m.role for m in hist] == ["user", "assistant"]


# ── W2-C.3 — udB response cache ──────────────────────────────────────────────


def test_cache_roundtrip_through_udb(agent_modules, monkeypatch):
    p = agent_modules.providers
    mini_mod = agent_modules.mini_mod
    mini = mini_mod.MiniAI()
    agent_modules.root.udB = FakeUdB()

    calls = {"n": 0}

    async def fake_route(**kwargs):
        calls["n"] += 1
        return p.AIResponse("cached answer", "fake", "fake-1")

    monkeypatch.setattr(mini, "_route", fake_route)
    msgs = [p.AIMessage(role="user", content="what is the time")]

    first = asyncio.run(mini.mini_respond(msgs))
    assert first.provider == "fake" and calls["n"] == 1

    # Record shape: {"exp": ts, "v": content} under an identifier-safe key.
    keys = [
        k for k in agent_modules.root.udB.keys() if k.startswith(mini.CACHE_KEY_PFX)
    ]
    assert len(keys) == 1 and ":" not in keys[0]
    record = agent_modules.root.udB.get_key(keys[0])
    assert record["v"] == "cached answer"
    assert record["exp"] > time.time()

    second = asyncio.run(mini.mini_respond(list(msgs)))
    assert second.provider == "cache" and second.model == "udb"
    assert second.content == "cached answer"
    assert calls["n"] == 1  # served from cache, no second route


def test_cache_expired_entry_is_dropped(agent_modules):
    mini = agent_modules.mini_mod.MiniAI()
    p = agent_modules.providers
    agent_modules.root.udB = FakeUdB()
    key = agent_modules.mini_mod._cache_key([p.AIMessage(role="user", content="stale")])
    agent_modules.root.udB.set_key(key, {"exp": time.time() - 5, "v": "old"})

    assert mini._cache_get(key) is None
    assert agent_modules.root.udB.get_key(key) is None  # lazily deleted


def test_cache_set_skips_long_values(agent_modules):
    mini = agent_modules.mini_mod.MiniAI()
    agent_modules.root.udB = FakeUdB()
    mini._cache_set("BRAIN_CACHE_x", "y" * (mini.CACHE_MAX_LEN + 1))
    assert agent_modules.root.udB.get_key("BRAIN_CACHE_x") is None


def test_cache_disabled_cleanly_without_udb(agent_modules):
    mini = agent_modules.mini_mod.MiniAI()
    assert agent_modules.root.udB is None
    assert mini._cache_get("BRAIN_CACHE_none") is None
    mini._cache_set("BRAIN_CACHE_none", "v")  # no-op, no raise
    assert asyncio.run(mini.mini_cache_size()) == 0
    assert asyncio.run(mini.mini_flush_cache()) == 0


def test_flush_cache_counts_only_brain_keys(agent_modules):
    mini = agent_modules.mini_mod.MiniAI()
    agent_modules.root.udB = FakeUdB(
        {
            "BRAIN_CACHE_a": {"exp": time.time() + 60, "v": "1"},
            "BRAIN_CACHE_b": {"exp": time.time() + 60, "v": "2"},
            "SUDO_USERS": [1],
        }
    )
    assert asyncio.run(mini.mini_cache_size()) == 2
    assert asyncio.run(mini.mini_flush_cache()) == 2
    assert agent_modules.root.udB.get_key("SUDO_USERS") == [1]  # untouched


def test_builtin_greeting_short_circuits_before_cache(agent_modules, monkeypatch):
    p = agent_modules.providers
    mini = agent_modules.mini_mod.MiniAI()
    agent_modules.root.udB = FakeUdB()

    async def boom(**kwargs):
        raise AssertionError("routing must not run for builtin intents")

    monkeypatch.setattr(mini, "_route", boom)
    resp = asyncio.run(mini.mini_respond([p.AIMessage(role="user", content="hello")]))
    assert resp.provider == "builtin"


# ── W2-C.4 — capability policy ───────────────────────────────────────────────


def test_clamp_shell_timeout(agent_modules):
    clamp = agent_modules.tools_mod.clamp_shell_timeout
    assert clamp(9999) == 120.0
    assert clamp(120) == 120.0
    assert clamp(-5) == 1.0
    assert clamp("junk") == 60.0
    assert clamp(None) == 60.0


def test_unknown_tool_still_errors(agent_modules):
    ex = agent_modules.tools_mod.make_executor()
    result = asyncio.run(ex.execute("nope_tool", {}))
    assert "Unknown tool" in result["error"]


def test_denied_tool_never_imports_target_system(agent_modules):
    """install_package without Cap.RES_INSTALL must be refused BEFORE the
    extender (HRE) is imported."""
    tools = agent_modules.tools_mod
    ex = tools.make_executor(caps=set())  # no caps at all
    sys.modules.pop("pyUltroid.extender", None)
    result = asyncio.run(ex.execute("install_package", {"package": "x"}))
    assert result["denied"] is True
    assert "res.install" in result["missing_caps"]
    assert "pyUltroid.extender" not in sys.modules


def test_update_check_apply_requires_update_apply(agent_modules):
    tools = agent_modules.tools_mod
    Cap = tools.Cap
    ex = tools.make_executor()  # DEFAULT_AGENT_CAPS — no UPDATE_APPLY
    denied = asyncio.run(ex.execute("update_check", {"apply": True}))
    assert denied["denied"] is True
    assert Cap.UPDATE_APPLY in denied["missing_caps"]
    # …but the read-only check stays within the default grant.
    assert ex._missing_caps("update_check", {"apply": False}) == []


def test_install_package_denied_by_default_and_dispatchable_with_grant(
    agent_modules, monkeypatch
):
    """Regression: RES_INSTALL left the default grant — on-demand pip installs
    are supply-chain-sensitive and require an explicit grant."""
    tools = agent_modules.tools_mod
    Cap = tools.Cap
    assert Cap.RES_INSTALL not in tools.DEFAULT_AGENT_CAPS

    ex = tools.make_executor()  # default grant
    sys.modules.pop("pyUltroid.extender", None)
    denied = asyncio.run(ex.execute("install_package", {"package": "pillow"}))
    assert denied["denied"] is True
    assert Cap.RES_INSTALL in denied["missing_caps"]
    assert "pyUltroid.extender" not in sys.modules  # refused before any import

    extender_mod = types.ModuleType("pyUltroid.extender")

    class FakeHRE:
        async def install_dep(self, package, plugin=None):
            return SimpleNamespace(
                installed=[package],
                skipped=[],
                failed=[],
                success=True,
            )

    extender_mod.ex = SimpleNamespace(resources=FakeHRE())
    monkeypatch.setitem(sys.modules, "pyUltroid.extender", extender_mod)

    granted = tools.make_executor(
        caps=set(tools.DEFAULT_AGENT_CAPS) | {Cap.RES_INSTALL}
    )
    result = asyncio.run(granted.execute("install_package", {"package": "pillow"}))
    assert result["success"] is True
    assert result["installed"] == ["pillow"]


def test_update_check_apply_allowed_with_explicit_grant(agent_modules, monkeypatch):
    """Granting UPDATE_APPLY lets dispatch proceed (proven via a stub updater)."""
    tools = agent_modules.tools_mod
    Cap = tools.Cap

    updater_mod = types.ModuleType("pyUltroid.updater")

    class FakeEngine:
        async def check(self):
            return None  # "could not fetch upstream"

    updater_mod.updater = FakeEngine()
    monkeypatch.setitem(sys.modules, "pyUltroid.updater", updater_mod)

    ex = tools.make_executor(caps=set(tools.DEFAULT_AGENT_CAPS) | {Cap.UPDATE_APPLY})
    result = asyncio.run(ex.execute("update_check", {"apply": True}))
    assert result.get("error") == "Could not fetch upstream"  # dispatched


def test_shell_execute_denied_without_shell_exec(agent_modules):
    tools = agent_modules.tools_mod
    Cap = tools.Cap
    ex = tools.make_executor(caps={Cap.PLUGIN_READ})
    result = asyncio.run(ex.execute("shell_execute", {"command": "ls"}))
    assert result["denied"] is True
    assert Cap.SHELL_EXEC in result["missing_caps"]


def test_shell_execute_forwards_granted_caps_and_clamps(agent_modules, monkeypatch):
    """The shell router must receive exactly the executor's shell-layer caps
    (not the hardcoded broad set from before W2-C.4) and a clamped timeout."""
    tools = agent_modules.tools_mod
    shell_pkg = importlib.import_module("pyUltroid.shell")
    context_mod = importlib.import_module("pyUltroid.shell._context")

    captured = {}

    async def fake_execute(ctx, command, timeout=60.0):
        captured["ctx"] = ctx
        captured["command"] = command
        captured["timeout"] = timeout
        return SimpleNamespace(stdout="ok", stderr="", exit_code=0, cwd="/tmp", ok=True)

    monkeypatch.setattr(shell_pkg.sh, "execute", fake_execute)

    Cap = tools.Cap
    ex = tools.make_executor()  # default grant
    result = asyncio.run(
        ex.execute(
            "shell_execute",
            {"command": "ls -la", "timeout": 99999},
        )
    )
    assert result["ok"] is True
    ctx = captured["ctx"]
    assert ctx.caller == "ai"
    assert ctx.trust == context_mod.TrustLevel.AI_AGENT
    assert ctx.plugin_name == "agent.agent"
    # Only shell-layer caps forwarded, and only granted ones.
    assert ctx.caps <= tools._SHELL_LAYER_CAPS
    assert ctx.caps == frozenset(
        c for c in tools.DEFAULT_AGENT_CAPS if c in tools._SHELL_LAYER_CAPS
    )
    # Privileged caps can never leak in even if granted to the executor:
    assert captured["timeout"] == 120.0  # clamped from 99999


def test_shell_execute_strips_privileged_caps_even_if_granted(
    agent_modules, monkeypatch
):
    tools = agent_modules.tools_mod
    shell_pkg = importlib.import_module("pyUltroid.shell")
    captured = {}

    async def fake_execute(ctx, command, timeout=60.0):
        captured["ctx"] = ctx
        return SimpleNamespace(stdout="", stderr="", exit_code=0, cwd="/", ok=True)

    monkeypatch.setattr(shell_pkg.sh, "execute", fake_execute)
    Cap = tools.Cap
    ex = tools.make_executor(caps=set(tools.DEFAULT_AGENT_CAPS) | {Cap.PROC_KILL})
    asyncio.run(ex.execute("shell_execute", {"command": "ps"}))
    # PROC_KILL IS a shell-layer cap and was explicitly granted → forwarded.
    assert Cap.PROC_KILL in captured["ctx"].caps
    # Non-shell caps (UPDATE_READ etc.) never reach the shell context.
    assert Cap.UPDATE_READ not in captured["ctx"].caps


# ── W2-C.5 — lifecycle ───────────────────────────────────────────────────────


def test_lifecycle_no_universal_runtime_is_noop(agent_modules):
    lc = agent_modules.lifecycle
    assert agent_modules.root.asst is None and agent_modules.root.ultroid_bot is None
    assert lc.register_lifecycle() is False
    assert lc.lifecycle_registered() is False


def test_lifecycle_registers_idempotently_and_hooks_run(agent_modules):
    lc = agent_modules.lifecycle

    class FakeLifecycle:
        def __init__(self):
            self.plugins = []

        def register(self, plugin):
            self.plugins.append(plugin)

    fake = FakeLifecycle()
    agent_modules.root.ultroid_bot = SimpleNamespace(universal_plugins=fake)

    assert lc.register_lifecycle() is True
    assert lc.register_lifecycle() is True  # idempotent
    assert len(fake.plugins) == 1  # registered exactly once
    assert lc.lifecycle_registered() is True
    assert fake.plugins[0].id == "ultroid.agent"

    plugin = fake.plugins[0]
    asyncio.run(plugin.lifecycle_start(fake))
    assert plugin.started is True
    asyncio.run(plugin.lifecycle_start(fake))  # start is idempotent
    asyncio.run(plugin.lifecycle_stop(fake))
    assert plugin.started is False
    asyncio.run(plugin.lifecycle_stop(fake))  # stop is idempotent


def test_lifecycle_start_survives_ollama_down(agent_modules, fake_aiohttp):
    lc = agent_modules.lifecycle
    fake_aiohttp.tags_status = 500
    plugin = lc.engine_lifecycle
    asyncio.run(plugin.lifecycle_start(None))  # must not raise
    assert plugin.started is True
    asyncio.run(plugin.lifecycle_stop(None))


def test_lifecycle_stop_wakes_waiters_with_shutdown_response(
    agent_modules, monkeypatch
):
    """Regression: stop() must stamp a well-formed shutdown AIResponse for
    every in-flight MiniAI cache key BEFORE waking waiters, and must not
    clear results early (a woken waiter would otherwise fall through and
    re-route a fresh request mid-shutdown)."""
    p = agent_modules.providers
    mini_mod = agent_modules.mini_mod
    mini = mini_mod.MiniAI()
    lc = agent_modules.lifecycle

    msgs = [p.AIMessage(role="user", content="unique in-flight question")]
    key = mini_mod._cache_key(msgs, "")

    async def scenario():
        route_started = asyncio.Event()
        route_release = asyncio.Event()

        async def fake_route(**kwargs):
            route_started.set()
            await route_release.wait()
            return p.AIResponse("real answer", "fake", "fake-1")

        monkeypatch.setattr(mini, "_route", fake_route)

        primary = asyncio.ensure_future(mini.mini_respond(list(msgs), use_cache=False))
        await route_started.wait()

        # Second identical request parks on the in-flight event.
        waiter = asyncio.ensure_future(mini.mini_respond(list(msgs), use_cache=False))
        for _ in range(10):
            await asyncio.sleep(0)
        assert key in mini._in_flight

        # The union, exactly as production: the lifecycle and the MiniAI are
        # one object, so stop() can reach the in-flight map it must wake.
        plugin = agent_modules.pkg.Agent()
        plugin._in_flight = mini._in_flight
        plugin._in_flight_results = mini._in_flight_results
        plugin.started = True
        await plugin.lifecycle_stop(None)

        # The waiter wakes with the shutdown response, not a re-route.
        waiter_resp = await asyncio.wait_for(waiter, 2)
        # Results NOT cleared too early: the stamped response is still there.
        assert mini._in_flight_results.get(key) is waiter_resp
        # No dead events left for future identical requests to hang on.
        assert key not in mini._in_flight

        route_release.set()
        primary_resp = await asyncio.wait_for(primary, 2)
        return waiter_resp, primary_resp

    waiter_resp, primary_resp = asyncio.run(scenario())
    assert waiter_resp.provider == "lifecycle"
    assert waiter_resp.model == "shutdown"
    assert waiter_resp.error and "stopped" in waiter_resp.error
    assert not waiter_resp.ok  # well-formed error response
    assert primary_resp.content == "real answer"  # in-flight primary unaffected


def test_lifecycle_shutdown_results_swept_after_delay(agent_modules):
    """The delayed sweep drops only entries still holding the shutdown
    sentinel — a real response that raced in for the same key is kept."""
    lc = agent_modules.lifecycle
    p = agent_modules.providers
    mini = agent_modules.mini_mod.MiniAI()

    sentinel = p.AIResponse("", "lifecycle", "shutdown", error="x")
    other = p.AIResponse("real", "prov", "model")
    mini._in_flight_results["K1"] = sentinel
    mini._in_flight_results["K2"] = sentinel
    mini._in_flight_results["K3"] = other

    asyncio.run(lc._sweep_stamped_results(mini, {"K1", "K3"}, sentinel, delay=0))

    assert "K1" not in mini._in_flight_results  # stamped sentinel swept
    assert mini._in_flight_results["K2"] is sentinel  # not stamped: untouched
    assert mini._in_flight_results["K3"] is other  # raced real resp kept


# ── Phase 1.2 — model-aware parameter dispatch ──────────────────────────────


def test_base_provider_default_capabilities(agent_modules):
    p = agent_modules.providers
    caps = p.BaseProvider().model_capabilities("anything")
    assert caps == {
        "max_tokens": "max_tokens",
        "temperature": True,
        "tools": True,
        "system": True,
        "stream": True,
    }


def test_openai_model_capabilities_o_series_vs_standard(agent_modules):
    p = agent_modules.providers
    provider = p.ProviderRegistry().provider_by_name("openai")
    std = provider.model_capabilities("gpt-4o")
    assert std["max_tokens"] == "max_tokens"
    assert std["temperature"] is True
    for m in ("o1", "o1-mini", "o3", "o3-mini", "o4-mini"):
        caps = provider.model_capabilities(m)
        assert caps["max_tokens"] == "max_completion_tokens", m
        assert caps["temperature"] is False, m


def test_gemini_model_capabilities_uses_max_output_tokens(agent_modules):
    p = agent_modules.providers
    provider = p.ProviderRegistry().provider_by_name("gemini")
    caps = provider.model_capabilities("gemini-2.5-flash")
    assert caps["max_tokens"] == "max_output_tokens"
    assert caps["temperature"] is True


def test_anthropic_model_capabilities(agent_modules):
    p = agent_modules.providers
    provider = p.ProviderRegistry().provider_by_name("anthropic")
    caps = provider.model_capabilities("claude-haiku-4-5-20251001")
    assert caps["max_tokens"] == "max_tokens"
    assert caps["temperature"] is True
    assert caps["tools"] is True


def test_groq_default_model_updated(agent_modules):
    p = agent_modules.providers
    provider = p.ProviderRegistry().provider_by_name("groq")
    assert provider.default_model == "llama-3.3-70b-versatile"


def test_gemini_default_model_updated(agent_modules):
    p = agent_modules.providers
    provider = p.ProviderRegistry().provider_by_name("gemini")
    # api_key unset but default_model falls back to the built-in
    assert provider.default_model == "gemini-2.5-flash"


def test_openai_o1_complete_uses_max_completion_tokens(agent_modules, monkeypatch):
    """o3-mini must send max_completion_tokens (not max_tokens) and omit
    temperature entirely — verified through the captured create kwargs."""
    p = agent_modules.providers
    agent_modules.root.udB = FakeUdB({"AGENT_CLOUD_OPENAI_KEY": "sk-test"})
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(captured))

    provider = p.ProviderRegistry().provider_by_name("openai")
    resp = asyncio.run(
        provider.complete(
            [p.AIMessage(role="user", content="think hard")],
            model="o3-mini",
            max_tokens=2000,
            temperature=0.5,
        )
    )
    assert resp.ok and resp.model == "o3-mini"
    kw = captured["create_kwargs"]
    assert kw["max_completion_tokens"] == 2000
    assert "max_tokens" not in kw
    assert "temperature" not in kw


def test_openai_standard_model_keeps_max_tokens(agent_modules, monkeypatch):
    """Regression: the non-o-series path is unchanged — max_tokens + temperature."""
    p = agent_modules.providers
    agent_modules.root.udB = FakeUdB({"AGENT_CLOUD_OPENAI_KEY": "sk-test"})
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(captured))

    provider = p.ProviderRegistry().provider_by_name("openai")
    asyncio.run(
        provider.complete(
            [p.AIMessage(role="user", content="hi")],
            model="gpt-4o",
            max_tokens=512,
            temperature=0.3,
        )
    )
    kw = captured["create_kwargs"]
    assert kw["max_tokens"] == 512
    assert kw["temperature"] == 0.3
    assert "max_completion_tokens" not in kw


def test_agent_respond_forwards_model_to_provider(agent_modules, monkeypatch):
    """respond(model=...) reaches the provider's complete(model=...)."""
    p = agent_modules.providers
    agent_modules.root.udB = FakeUdB({"AGENT_CLOUD_OPENAI_KEY": "sk-test"})
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_module(captured))

    agent = agent_modules.agent_mod.UltroidAgent()
    asyncio.run(
        agent.respond(
            "list the loaded plugins",
            chat_id=7,
            provider_name="openai",
            model="gpt-4o",
            use_tools=False,
            use_cache=False,
        )
    )
    assert captured["create_kwargs"]["model"] == "gpt-4o"


# ── Phase 1.2 — CloudflareProvider ──────────────────────────────────────────


class FakeCfSession:
    """aiohttp.ClientSession stand-in for Cloudflare REST calls; accepts the
    ``headers`` kwarg (unlike the Ollama FakeAioSession) and records requests."""

    response_data: dict = {}
    calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return _FakeAioResp(data=dict(self.response_data))


@pytest.fixture
def cf_aiohttp(monkeypatch):
    import aiohttp

    FakeCfSession.calls = []
    FakeCfSession.response_data = {}
    monkeypatch.setattr(aiohttp, "ClientSession", FakeCfSession)
    return FakeCfSession


def test_cloudflare_not_in_registry_when_unconfigured(agent_modules):
    """Unconfigured environments keep the six-provider shape (no CF creds)."""
    p = agent_modules.providers
    assert "cloudflare" not in p.ProviderRegistry().provider_health()
    assert p.ProviderRegistry().provider_by_name("cloudflare") is None


def test_cloudflare_unconfigured_returns_error(agent_modules):
    p = agent_modules.providers
    cf = p.CloudflareProvider()
    assert not cf.is_configured()
    resp = asyncio.run(cf.complete([p.AIMessage(role="user", content="hi")]))
    assert not resp.ok
    assert "CF_ACCOUNT/CF_TOKEN" in resp.error


def test_cloudflare_text_complete(agent_modules, cf_aiohttp):
    p = agent_modules.providers
    cf = p.CloudflareProvider()
    cf._account = "acct123"
    cf._token = "tok456"
    cf_aiohttp.response_data = {
        "success": True,
        "result": {
            "response": "cf says hi",
            "usage": {"prompt_tokens": 4, "completion_tokens": 6},
        },
    }
    resp = asyncio.run(
        cf.complete(
            [p.AIMessage(role="user", content="hi")],
            system="be brief",
        )
    )
    assert resp.ok
    assert resp.content == "cf says hi"
    assert resp.provider == "cloudflare"
    assert resp.tokens_in == 4 and resp.tokens_out == 6
    call = cf_aiohttp.calls[0]
    assert call["url"].endswith("/ai/run/@cf/meta/llama-3.1-8b-instruct")
    assert call["headers"]["Authorization"] == "Bearer tok456"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["json"]["messages"][0] == {"role": "system", "content": "be brief"}
    assert call["json"]["max_tokens"] == 1024
    assert call["json"]["temperature"] == 0.7


def test_cloudflare_image_complete(agent_modules, cf_aiohttp):
    p = agent_modules.providers
    cf = p.CloudflareProvider()
    cf._account = "acct123"
    cf._token = "tok456"
    cf_aiohttp.response_data = {
        "success": True,
        "result": {"image": "BASE64BYTES"},
    }
    resp = asyncio.run(
        cf.complete(
            [p.AIMessage(role="user", content="a cat in space")],
            model="@cf/black-forest-labs/flux-1-schnell",
        )
    )
    assert resp.ok
    assert resp.image == "BASE64BYTES"
    assert "image generated" in resp.content
    call = cf_aiohttp.calls[0]
    assert "flux-1-schnell" in call["url"]
    # image path sends only the prompt — no token cap / temperature
    assert call["json"] == {"prompt": "a cat in space"}


def test_cloudflare_model_capabilities_text_vs_image(agent_modules):
    p = agent_modules.providers
    cf = p.CloudflareProvider()
    text_caps = cf.model_capabilities("@cf/meta/llama-3.1-8b-instruct")
    assert text_caps["max_tokens"] == "max_tokens"
    assert text_caps["temperature"] is True
    assert text_caps["stream"] is True
    img_caps = cf.model_capabilities("@cf/stabilityai/stable-diffusion-xl-base-1.0")
    assert img_caps["max_tokens"] is None
    assert img_caps["temperature"] is False
    assert img_caps["stream"] is False


def test_cloudflare_api_error_surfaces(agent_modules, cf_aiohttp):
    p = agent_modules.providers
    cf = p.CloudflareProvider()
    cf._account = "acct"
    cf._token = "tok"
    cf_aiohttp.response_data = {
        "success": False,
        "errors": [{"message": "bad model"}],
    }
    resp = asyncio.run(cf.complete([p.AIMessage(role="user", content="hi")]))
    assert not resp.ok
    assert "bad model" in resp.error


def test_provider_register_is_idempotent_by_name(agent_modules):
    p = agent_modules.providers
    reg = p.ProviderRegistry()
    before = len(reg._all)
    cf = p.CloudflareProvider()
    reg.provider_register(cf)
    assert len(reg._all) == before + 1
    reg.provider_register(p.CloudflareProvider())  # same name -> deduped
    assert len(reg._all) == before + 1
    assert reg.provider_by_name("cloudflare") is cf
    reg.provider_register(None)  # None is a no-op
    assert len(reg._all) == before + 1


# ── Phase 1.2 — ToolPipeline + Codex compatibility ──────────────────────────


def test_codex_tool_kwargs(agent_modules):
    k = agent_modules.tools_mod.codex_tool_kwargs
    assert k(None) == {}
    assert k([]) == {}
    base = k([{"type": "function", "function": {"name": "x"}}])
    assert base["tool_choice"] == "auto"
    assert "parallel_tool_calls" not in base
    full = k(["t"], tool_choice="required", parallel_tool_calls=True)
    assert full["tool_choice"] == "required"
    assert full["parallel_tool_calls"] is True
    none_choice = k(["t"], tool_choice=None)
    assert "tool_choice" not in none_choice


def test_tool_pipeline_chains_with_feed(agent_modules, monkeypatch):
    tools = agent_modules.tools_mod
    ex = tools.make_executor()

    calls = []

    async def fake_execute(self, tool_name, arguments):
        calls.append((tool_name, dict(arguments)))
        if tool_name == "first":
            return {"results": "DATA"}
        return {"ok": True}

    monkeypatch.setattr(tools.ToolExecutor, "execute", fake_execute)

    pipe = tools.ToolPipeline(ex)
    pipe.add("first", {"q": "x"})
    pipe.add("second", {"action": "store", "key": "k"}, feed={"results": "value"})
    outcomes = asyncio.run(pipe.run())

    assert len(outcomes) == 2
    assert outcomes[0]["tool"] == "first"
    assert outcomes[1]["tool"] == "second"
    # first step: declared args only (no feed)
    assert calls[0] == ("first", {"q": "x"})
    # second step: declared args + fed-forward value
    assert calls[1] == ("second", {"action": "store", "key": "k", "value": "DATA"})
    assert outcomes[1]["result"] == {"ok": True}


def test_tool_pipeline_empty_run(agent_modules):
    tools = agent_modules.tools_mod
    pipe = tools.ToolPipeline(tools.make_executor())
    assert asyncio.run(pipe.run()) == []

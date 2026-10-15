"""Mock-only command-contract coverage for the multi-provider brain plugin."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class DB:
    def get_key(self, key):
        return None

    def set_key(self, key, value):
        return True


def load_brain(module_isolation):
    root = types.ModuleType("pyUltroid")
    root.__path__ = [str(ROOT / "pyUltroid")]
    root.udB, root.LOGS = DB(), logging.getLogger("brain-sdk-command-tests")
    root.asst = root.ultroid_bot = None
    sys.modules["pyUltroid"] = root
    startup = types.ModuleType("pyUltroid.startup")
    startup.__path__ = []
    logger = types.ModuleType("pyUltroid.startup.logger")
    logger.LOGS = root.LOGS
    configs = types.ModuleType("pyUltroid.configs")
    configs.Var = SimpleNamespace()
    sys.modules.update(
        {
            "pyUltroid.startup": startup,
            "pyUltroid.startup.logger": logger,
            "pyUltroid.configs": configs,
        }
    )
    misc = types.ModuleType("pyUltroid._misc")
    misc.__path__ = []
    decorators = types.ModuleType("pyUltroid._misc._decorators")
    decorators.ultroid_cmd = lambda **kwargs: (lambda function: function)
    wrappers = types.ModuleType("pyUltroid._misc._wrappers")

    async def eod(event, text, **kwargs):
        event.eod_text = text

    async def eor(event, text, **kwargs):
        event.eor_text = text
        return event

    wrappers.eod, wrappers.eor = eod, eor
    sys.modules.update(
        {
            "pyUltroid._misc": misc,
            "pyUltroid._misc._decorators": decorators,
            "pyUltroid._misc._wrappers": wrappers,
        }
    )
    importlib.import_module("pyUltroid.agent")
    # brain.py uses a package-relative import (`from . import udB`), so it must
    # be loaded as ``plugins.brain`` inside a stub ``plugins`` package rather
    # than as a loose module with no parent.
    plugins_pkg = types.ModuleType("plugins")
    plugins_pkg.__path__ = [str(ROOT / "plugins")]
    plugins_pkg.udB = root.udB
    plugins_pkg.ultroid_cmd = decorators.ultroid_cmd
    plugins_pkg.eod, plugins_pkg.eor = eod, eor
    sys.modules.setdefault("plugins", plugins_pkg)
    spec = importlib.util.spec_from_file_location(
        "plugins.brain", ROOT / "plugins" / "brain.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["plugins.brain"] = module
    spec.loader.exec_module(module)
    return module


class Pattern:
    def __init__(self, value):
        self.value = value

    def group(self, index):
        return self.value


class Event:
    def __init__(self, value=""):
        self.pattern_match = Pattern(value)
        self.chat_id = 1

    async def edit(self, text, **kwargs):
        self.edited_text = text


def test_provider_status_commands_and_verify_outcomes(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)

        class Provider:
            default_model = "test-model"

            def __init__(self, name, outcome):
                self.name, self.outcome, self.verify_calls = name, outcome, 0

            def is_configured(self):
                return True

            def model_capabilities(self, model):
                return {"tools": True, "stream": True}

            async def verify(self):
                self.verify_calls += 1
                if self.outcome == "raise":
                    raise RuntimeError("bad credential")
                return self.outcome

        good, bad = Provider("test_provider", True), Provider(
            "broken_provider", "raise"
        )
        brain.registry.register(good)
        providers_event = Event()
        asyncio.run(brain.aiproviders_cmd(providers_event))
        assert "Test Provider" in providers_event.eor_text
        assert "test-model" in providers_event.eor_text

        async def status():
            return {
                "providers": brain.registry.status(),
                "configured_count": 2,
                "cache_entries": 3,
                "active_histories": 4,
                "max_tool_rounds": 5,
                "max_history": 6,
            }

        brain.agent.status = status
        status_event = Event()
        asyncio.run(brain.aistatus_cmd(status_event))
        assert "Test Provider" in status_event.edited_text
        assert "Cache entries:** `3`" in status_event.edited_text

        brain.registry._all = [good, bad]
        verify_event = Event("")
        asyncio.run(brain.aiverify_cmd(verify_event))
        assert "verified" in verify_event.edited_text
        assert "verification failed" in verify_event.edited_text
        assert good.verify_calls == bad.verify_calls == 1


class RunEvent(Event):
    def __init__(self, value=""):
        super().__init__(value)
        self.out = True
        self.is_private = False
        self.sender_id = 7
        self.id = 1
        self.client = None

    async def get_reply_message(self):
        return None


def test_ai_and_agent_commands_route_through_the_runtime(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)
        seen = []

        class FakeResponse:
            content = "answer"
            provider = "fake"
            model = "m"
            tool_rounds = 0
            tools_used = []
            duration_ms = 1.0
            ok = True
            error = None

        class FakeRuntime:
            async def run(self, request):
                seen.append(request)
                return FakeResponse()

        brain.runtime = FakeRuntime()
        event = RunEvent("hello world")
        asyncio.run(brain.ai_cmd(event))
        assert seen[-1].origin == ".ai"
        assert seen[-1].prompt == "hello world"
        assert seen[-1].chat_id == event.chat_id

        asyncio.run(brain.agent_cmd(RunEvent("do a thing")))
        assert seen[-1].origin == ".agent"
        assert seen[-1].use_cache is False


def test_aisys_is_scoped_to_the_chat(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)
        first, second = Event("be terse"), Event("be verbose")
        asyncio.run(brain.aisys_cmd(first))
        assert brain.agent.chat_system(first.chat_id) == "be terse"
        asyncio.run(brain.aisysreset_cmd(second))
        assert brain.agent.chat_system(second.chat_id) == ""

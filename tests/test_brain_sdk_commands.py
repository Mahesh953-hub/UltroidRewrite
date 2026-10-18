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

import pytest
from telethon.tl import types as tl_types

ROOT = Path(__file__).resolve().parents[1]


class DB:
    def get_key(self, key):
        return None

    def set_key(self, key, value):
        return True


def native_visible_text(payload):
    """Read answer blocks without the companion copy-action UI."""
    def text(node):
        if isinstance(node, (tl_types.TextPlain, tl_types.TextWithEntities)):
            return node.text
        if isinstance(node, tl_types.TLObject):
            return "".join(text(value) for value in vars(node).values())
        if isinstance(node, (tuple, list)):
            return "".join(text(value) for value in node)
        return ""

    return "".join(
        text(block) for block in payload.blocks
        if not (
            isinstance(block, tl_types.PageBlockDetails)
            and getattr(block.title, "text", None) == "Answer actions"
        )
    )


class RecordingClient:
    def __init__(self):
        self._bot = True
        self.edits = []
        self.requests = []

    async def get_input_entity(self, entity):
        return entity

    async def __call__(self, request):
        self.requests.append(request)
        self.edits.append(
            {
                "entity": getattr(request, "peer", None),
                "message_id": getattr(request, "id", None),
                "wire_text": request.message,
                "text": (
                    native_visible_text(request.rich_message)
                    if isinstance(request.rich_message, tl_types.InputRichMessage)
                    else request.message
                ),
                "rich_message": request.rich_message,
            }
        )
        return SimpleNamespace(id=getattr(request, "id", None))


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
        event.eod_kwargs = kwargs

    async def eor(event, text, **kwargs):
        event.eor_text = text
        event.eor_kwargs = kwargs
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
        runtime = brain.agent()
        runtime.provider_register(good)
        runtime.provider_register(bad)
        providers_event = Event()
        asyncio.run(brain.aiproviders_cmd(providers_event))
        assert "Test Provider" in providers_event.eor_text
        assert "test-model" in providers_event.eor_text

        async def status():
            return {
                "providers": {"test_provider": True, "broken_provider": True},
                "configured_count": 2,
                "cache_entries": 3,
                "active_histories": 4,
                "max_tool_rounds": 5,
                "max_history": 6,
            }

        runtime.status = status
        status_event = Event()
        asyncio.run(brain.aistatus_cmd(status_event))
        assert "Test Provider" in status_event.edited_text
        assert "Cache entries:** `3`" in status_event.edited_text

        good_event = Event("test_provider")
        bad_event = Event("broken_provider")
        asyncio.run(brain.aiverify_cmd(good_event))
        asyncio.run(brain.aiverify_cmd(bad_event))
        assert "verified" in good_event.edited_text
        assert "verification failed" in bad_event.edited_text
        assert good.verify_calls == bad.verify_calls == 1


class RunEvent(Event):
    def __init__(self, value=""):
        super().__init__(value)
        self.out = True
        self.is_private = False
        self.sender_id = 7
        self.id = 1
        self.client = RecordingClient()

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

        runtime = brain.agent()
        runtime.runtime_run = FakeRuntime().run
        event = RunEvent("hello world")
        asyncio.run(brain.ai_cmd(event))
        assert seen[-1].origin == ".ai"
        assert seen[-1].prompt == "hello world"
        assert seen[-1].chat_id == event.chat_id
        assert seen[-1].rich_messages_allowed is True
        assert event.eor_kwargs == {"parse_mode": "html"}

        user_event = RunEvent("plain text only")
        user_event.client = SimpleNamespace(
            _bot=False,
            me=SimpleNamespace(bot=False, premium=False),
        )
        asyncio.run(brain.ai_cmd(user_event))
        assert seen[-1].rich_messages_allowed is False
        assert user_event.edited_text == "answer"

        agent_event = RunEvent("do a thing")
        asyncio.run(brain.agent_cmd(agent_event))
        assert seen[-1].origin == ".agent"
        assert seen[-1].use_cache is False
        assert seen[-1].rich_messages_allowed is True
        assert seen[-1].timeout_seconds == brain.AGENT_COMMAND_TIMEOUT_SECONDS == 105.0
        assert agent_event.eor_kwargs == {"parse_mode": "html"}

        class LocalProvider:
            async def _is_running(self):
                return True

        runtime.provider_by_name = lambda name: (
            LocalProvider() if name == "ollama" else None
        )
        local_event = RunEvent("use the local model")
        asyncio.run(brain.ailocal_cmd(local_event))
        assert seen[-1].origin == ".ailocal"
        assert seen[-1].provider == "ollama"
        assert local_event.eor_kwargs == {"parse_mode": "html"}


def test_ais_stream_renders_rich_text_and_hides_tool_protocol(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)

        async def stream(request):
            yield "Here is **plain text**."
            yield '\n[[tool_calls]]\n[{"name":"shell_execute","arguments":{}}]'

        brain.agent().runtime_stream = stream

        class StreamEvent(RunEvent):
            async def edit(self, text, **kwargs):
                self.edited_text = text
                self.edit_kwargs = kwargs

        event = StreamEvent("hello")
        asyncio.run(brain.ais_cmd(event))

        assert event.eor_kwargs == {"parse_mode": "html"}
        edited = event.client.edits[-1]
        assert edited["text"] == "Here is plain text."
        assert edited["wire_text"] == " "
        assert isinstance(edited["rich_message"], tl_types.InputRichMessage)
        assert isinstance(edited["rich_message"].blocks[0].text.entities[0], tl_types.MessageEntityBold)
        assert "shell_execute" not in edited["text"]


def test_agent_uses_one_html_placeholder_and_one_rich_final_edit(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)

        class FakeResponse:
            content = "Completed."
            provider = "fake"
            model = "test"
            tool_rounds = 0
            tools_used = []
            duration_ms = 1.0
            ok = True
            error = None

        async def fake_run(request):
            return FakeResponse()

        brain.agent().runtime_run = fake_run

        class CountingEvent(RunEvent):
            def __init__(self, value=""):
                super().__init__(value)
                self.edits = []

            async def edit(self, text, **kwargs):
                self.edits.append((text, kwargs))

        event = CountingEvent("do the task")
        asyncio.run(brain.agent_cmd(event))

        assert event.eor_text == "Working..."
        assert event.eor_kwargs == {"parse_mode": "html"}
        assert len(event.client.edits) == 1
        assert event.client.edits[0]["text"] == "Completed."
        assert isinstance(
            event.client.edits[0]["rich_message"], tl_types.InputRichMessage
        )


def test_agent_runtime_timeout_is_explained_without_provider_details(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)

        response = SimpleNamespace(
            content=(
                "The agent request exceeded its 105-second time limit. "
                "Try a smaller request or run the steps separately."
            ),
            provider="runtime",
            model="timeout",
            error="private runtime detail",
            image_result=None,
        )

        class Message:
            id = 99

            async def edit(self, text, **kwargs):
                self.text, self.kwargs = text, kwargs

        message = Message()
        client = RecordingClient()
        asyncio.run(
            brain._send_ai_response(
                SimpleNamespace(chat_id=1, id=2, client=client), message, response
            )
        )

        assert "105-second time limit" in client.edits[-1]["text"]
        assert "private runtime detail" not in client.edits[-1]["text"]
        assert isinstance(client.edits[-1]["rich_message"], tl_types.InputRichMessage)


def test_agent_failure_clears_placeholder_without_leaking_exception(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)

        async def fail(request):
            raise RuntimeError("sensitive provider payload")

        brain.agent().runtime_run = fail

        class CountingEvent(RunEvent):
            def __init__(self, value=""):
                super().__init__(value)
                self.edits = []

            async def edit(self, text, **kwargs):
                self.edits.append((text, kwargs))

        event = CountingEvent("do the task")
        asyncio.run(brain.agent_cmd(event))

        assert event.client.edits[-1]["text"] == (
            "The agent couldn't complete this request due to an internal error. Please try again."
        )
        assert isinstance(
            event.client.edits[-1]["rich_message"], tl_types.InputRichMessage
        )
        assert "sensitive provider payload" not in event.client.edits[-1]["text"]


def test_agent_cancellation_updates_placeholder_and_still_propagates(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)

        async def cancel(request):
            raise asyncio.CancelledError

        brain.agent().runtime_run = cancel

        class CountingEvent(RunEvent):
            def __init__(self, value=""):
                super().__init__(value)
                self.edits = []

            async def edit(self, text, **kwargs):
                self.edits.append((text, kwargs))

        event = CountingEvent("do the task")
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(brain.agent_cmd(event))

        assert event.client.edits[-1]["text"] == "Agent request cancelled."
        assert isinstance(
            event.client.edits[-1]["rich_message"], tl_types.InputRichMessage
        )


def test_aisys_is_scoped_to_the_chat(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)
        first, second = Event("be terse"), Event("be verbose")
        second.chat_id = 2
        asyncio.run(brain.aisys_cmd(first))
        runtime = brain.agent()
        assert runtime.chat_system(first.chat_id) == "be terse"
        asyncio.run(brain.aisysreset_cmd(second))
        assert runtime.chat_system(second.chat_id) == ""
        assert runtime.chat_system(first.chat_id) == "be terse"


def test_ai_response_is_plain_and_strips_serialized_tool_calls(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)

        class Message:
            id = 99

            async def edit(self, text, **kwargs):
                self.text = text
                self.kwargs = kwargs

        response = SimpleNamespace(
            content=(
                "**answer**\n\n[[tool_calls]]\n"
                '[{"name":"shell_execute","arguments":{"command":"touch secret"}}]'
            ),
            provider="apimix",
            model="claude-sonnet-4-6-free",
            tool_rounds=2,
            tools_used=["shell_execute"],
            duration_ms=10.0,
            modalities=None,
            reasoning=None,
            thinking=None,
            image_result=None,
            error=None,
        )
        message = Message()
        client = RecordingClient()
        asyncio.run(
            brain._send_ai_response(
                SimpleNamespace(chat_id=1, id=2, client=client), message, response
            )
        )

        assert client.edits[-1]["text"] == "answer"
        assert isinstance(client.edits[-1]["rich_message"], tl_types.InputRichMessage)
        assert isinstance(client.edits[-1]["rich_message"].blocks[0].text.entities[0], tl_types.MessageEntityBold)
        assert "shell_execute" not in client.edits[-1]["text"]
        assert "apimix" not in client.edits[-1]["text"]
        assert "tool_rounds" not in client.edits[-1]["text"]


def test_ai_502_after_tool_call_shows_no_provider_or_tool_details(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)

        class Message:
            id = 99

            async def edit(self, text, **kwargs):
                self.text = text
                self.kwargs = kwargs

        response = SimpleNamespace(
            content="⚠️ Error code: 502",
            error="Error code: 502",
            provider="apimix",
            model="claude-sonnet-4-6-free",
            tool_rounds=2,
            tools_used=["shell_execute"],
            duration_ms=29466,
            image_result=None,
        )
        message = Message()
        client = RecordingClient()
        asyncio.run(
            brain._send_ai_response(
                SimpleNamespace(chat_id=1, id=2, client=client), message, response
            )
        )

        assert client.edits[-1]["text"] == (
            "Sorry, the AI service couldn't complete that request. Please try again."
        )
        assert isinstance(client.edits[-1]["rich_message"], tl_types.InputRichMessage)
        assert all(
            value not in client.edits[-1]["text"]
            for value in ("502", "apimix", "claude-sonnet", "shell_execute")
        )


def test_long_ai_response_attachment_is_plain_and_stripped(module_isolation):
    with module_isolation("pyUltroid"):
        brain = load_brain(module_isolation)

        class Message:
            async def delete(self):
                self.deleted = True

        class Client:
            async def send_file(self, chat_id, file, **kwargs):
                self.chat_id = chat_id
                self.contents = file.getvalue()
                self.kwargs = kwargs

        client = Client()
        response = SimpleNamespace(
            content=(
                "a" * 4001
                + '\n[[tool_calls]]\n[{"name":"shell_execute","arguments":{"command":"id"}}]'
            ),
            error=None,
            image_result=None,
        )
        message = Message()
        asyncio.run(
            brain._send_ai_response(
                SimpleNamespace(chat_id=1, id=2, client=client), message, response
            )
        )

        assert message.deleted is True
        assert client.contents == b"a" * 4001
        assert client.kwargs == {"caption": None, "reply_to": 2, "parse_mode": None}

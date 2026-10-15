"""Mock-only public-contract tests for the multi-provider Agent SDK.

The SDK is deliberately exercised through :class:`pyUltroid.agent.AIClient`,
not provider internals.  Optional provider packages are injected only after the
Agent package loads, which proves imports remain lazy and no test can use a
live credential or network connection.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


class _FakeUdB:
    def __init__(self, values=None):
        self.values = dict(values or {})

    def get_key(self, key):
        return self.values.get(key)


def _install_pyultroid_stub():
    """Install the smallest root namespace needed by the agent package."""
    root = types.ModuleType("pyUltroid")
    root.__path__ = [str(ROOT / "pyUltroid")]
    root.LOGS = logging.getLogger("agent-sdk-tests")
    root.udB = _FakeUdB()
    root.asst = None
    root.ultroid_bot = None
    sys.modules["pyUltroid"] = root

    startup = types.ModuleType("pyUltroid.startup")
    startup.__path__ = []
    logger = types.ModuleType("pyUltroid.startup.logger")
    logger.LOGS = root.LOGS
    sys.modules["pyUltroid.startup"] = startup
    sys.modules["pyUltroid.startup.logger"] = logger

    configs = types.ModuleType("pyUltroid.configs")
    configs.Var = SimpleNamespace()
    sys.modules["pyUltroid.configs"] = configs
    return root


@pytest.fixture
def sdk(module_isolation):
    """Load a fresh Agent package without production bootstrap side effects."""
    with module_isolation("pyUltroid"):
        root = _install_pyultroid_stub()
        package = importlib.import_module("pyUltroid.agent")
        yield SimpleNamespace(root=root, package=package)


def _client(sdk, **kwargs):
    """Construct the documented SDK facade through its public package export."""
    return sdk.package.AIClient(**kwargs)


def _fake_openai(captured, *, reasoning_attr="reasoning_content"):
    module = types.ModuleType("openai")

    class Completions:
        async def create(self, **kwargs):
            captured["create"] = kwargs
            function = SimpleNamespace(name="weather", arguments='{"city":"Paris"}')
            message = SimpleNamespace(
                content="It is sunny.",
                tool_calls=[SimpleNamespace(id="call_weather", function=function)],
            )
            setattr(message, reasoning_attr, "provider trace")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)],
                usage=SimpleNamespace(prompt_tokens=3, completion_tokens=5),
            )

    class Models:
        async def list(self):
            captured["models_listed"] = True
            return SimpleNamespace(data=[])

    class AsyncOpenAI:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = SimpleNamespace(completions=Completions())
            self.models = Models()

    module.AsyncOpenAI = AsyncOpenAI
    return module


def _fake_google_genai(captured):
    genai = types.ModuleType("google.genai")

    class Models:
        async def generate_content(self, **kwargs):
            captured["generate"] = kwargs
            return SimpleNamespace(
                text="gemini text",
                candidates=[
                    SimpleNamespace(
                        content=SimpleNamespace(
                            parts=[
                                SimpleNamespace(text="gemini text"),
                                SimpleNamespace(
                                    function_call=SimpleNamespace(
                                        name="lookup",
                                        args={"term": "SarahUB"},
                                        id="gem-call",
                                    )
                                ),
                            ]
                        )
                    )
                ],
                usage_metadata=SimpleNamespace(
                    prompt_token_count=2,
                    candidates_token_count=4,
                ),
            )

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.aio = SimpleNamespace(models=Models())

    genai.Client = Client
    google = types.ModuleType("google")
    google.__path__ = []
    google.genai = genai
    return {"google": google, "google.genai": genai}


# ── Facade and provider verification ────────────────────────────────────────


def test_ai_client_public_surface_is_provider_agnostic(sdk):
    """The application imports one stable facade rather than provider SDKs."""
    AIClient = sdk.package.AIClient
    client = AIClient(provider="openai", key="test-key", model="model-a")

    assert callable(client.chat)
    assert callable(client.agent)
    assert callable(client.search)
    assert callable(client.verify)
    assert client.provider_name == "openai"
    assert client.model == "model-a"


def test_provider_verification_uses_a_read_only_sdk_check(sdk, monkeypatch):
    """Verification rejects missing credentials and uses the models read endpoint."""
    unconfigured = _client(sdk, provider="openai")
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai(captured))

    assert asyncio.run(unconfigured.verify()) is False
    assert asyncio.run(_client(sdk, provider="openai", key="test-key").verify()) is True
    assert captured["models_listed"] is True


# ── OpenAI-shaped providers: normalised thinking and tool exchanges ─────────


def test_openai_reasoning_and_tool_calls_normalize(sdk, monkeypatch):
    """OpenAI reasoning content and tool calls yield one normalized shape."""
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai(captured))
    message = sdk.package.AIMessage(role="user", content="What is the weather?")

    response = asyncio.run(
        _client(
            sdk,
            provider="openai",
            key="test-key",
            model="test-model",
        ).chat([message], tools=[{"type": "function", "function": {"name": "weather"}}])
    )

    assert response.content == "It is sunny."
    assert response.reasoning == "provider trace"
    assert response.tool_calls == [
        {
            "id": "call_weather",
            "name": "weather",
            "arguments": '{"city":"Paris"}',
        }
    ]
    assert captured["create"]["tools"][0]["function"]["name"] == "weather"


def test_tool_result_keeps_the_original_tool_call_id(sdk, monkeypatch):
    """A tool result must be addressable by ``tool_call_id``, not only name."""
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai(captured))
    messages = [
        sdk.package.AIMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_weather",
                    "name": "weather",
                    "arguments": "{}",
                }
            ],
        ),
        sdk.package.AIMessage(
            role="tool",
            content='{"temperature": 20}",',
            name="weather",
            tool_call_id="call_weather",
        ),
    ]

    asyncio.run(_client(sdk, provider="openai", key="test-key").chat(messages))
    sent_tool_result = captured["create"]["messages"][1]
    assert sent_tool_result["role"] == "tool"
    assert sent_tool_result["tool_call_id"] == "call_weather"


# ── Gemini's native content model ───────────────────────────────────────────


def test_gemini_preserves_native_parts_function_calls_and_modalities(sdk, monkeypatch):
    """Gemini requests retain rich parts and response modalities natively."""
    captured = {}
    for name, module in _fake_google_genai(captured).items():
        monkeypatch.setitem(sys.modules, name, module)

    image_part = {"inline_data": {"mime_type": "image/png", "data": "AA=="}}
    response = asyncio.run(
        _client(
            sdk,
            provider="gemini",
            key="test-key",
            model="gemini-test",
        ).chat(
            [
                sdk.package.AIMessage(
                    role="user", parts=[{"text": "What is this?"}, image_part]
                )
            ],
            tools=[
                {
                    "function_declarations": [
                        {"name": "lookup", "parameters": {"type": "object"}}
                    ]
                }
            ],
        )
    )

    sent = captured["generate"]
    assert sent["contents"][0]["parts"] == [{"text": "What is this?"}, image_part]
    assert sent["config"]["tools"][0]["function_declarations"][0]["name"] == "lookup"
    assert response.modalities == ["text"]
    assert response.tool_calls == [
        {
            "id": "gem-call",
            "name": "lookup",
            "arguments": {"term": "SarahUB"},
        }
    ]


# ── Registered compatible providers ─────────────────────────────────────────


def test_xai_grok_and_meta_are_registered_sdk_providers(sdk):
    """Both newly supported provider families are first-class registry entries."""
    client = _client(sdk, provider="openai", key="test-key")

    assert client._PROVIDERS["xai"].name == "xai"
    assert client._PROVIDERS["xai"].fallback_model.startswith("grok")
    assert client._PROVIDERS["meta"].name == "meta_llama"


def test_apimix_is_a_registered_sdk_provider_with_its_published_base(sdk):
    client = _client(sdk, provider="apimix", key="apx_live_test")

    assert client._PROVIDERS["apimix"].name == "apimix"
    assert client._PROVIDERS["apmix"] is client._PROVIDERS["apimix"]
    assert client._provider.base_url == "https://api.apmix.ai/v1"
    assert client._provider.api_key == "apx_live_test"


def test_custom_provider_forwards_an_arbitrary_base_url(sdk, monkeypatch):
    """Custom OpenAI-compatible hosts use the supplied URL, never a preset."""
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai(captured))
    base_url = "https://models.internal.example/v1"

    response = asyncio.run(
        _client(
            sdk,
            provider="custom",
            key="custom-key",
            base_url=base_url,
            model="my-model",
        ).chat([sdk.package.AIMessage(role="user", content="hi")])
    )

    assert response.provider == "openai_compat"
    assert captured["client"] == {"api_key": "custom-key", "base_url": base_url}
    assert captured["create"]["model"] == "my-model"


def test_apimix_calls_its_published_base_with_the_stored_key(sdk, monkeypatch):
    """apmix rides the OpenAI chat-completions path at its documented base."""
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai(captured))
    sdk.root.udB.values["AGENT_CLOUD_APIMIX_KEY"] = "apx_live_test"

    response = asyncio.run(
        _client(sdk, provider="apimix", model="anthropic/claude-sonnet-4-6").chat(
            [sdk.package.AIMessage(role="user", content="hi")]
        )
    )

    assert response.provider == "apimix"
    assert captured["client"] == {
        "api_key": "apx_live_test",
        "base_url": "https://api.apmix.ai/v1",
    }
    assert captured["create"]["model"] == "anthropic/claude-sonnet-4-6"


# ── OpenAI Responses API for reasoning models ───────────────────────────────


def _fake_openai_responses(captured):
    module = _fake_openai(captured)

    class Responses:
        async def create(self, **kwargs):
            captured["responses_create"] = kwargs
            return SimpleNamespace(
                output_text="The temperature is 20C.",
                output=[
                    SimpleNamespace(
                        type="reasoning",
                        summary=[
                            SimpleNamespace(text="I should call the weather tool.")
                        ],
                    ),
                    SimpleNamespace(
                        type="function_call",
                        call_id="resp_call_weather",
                        name="weather",
                        arguments='{"city":"Paris"}',
                    ),
                ],
                usage=SimpleNamespace(input_tokens=7, output_tokens=11),
            )

    original = module.AsyncOpenAI

    class AsyncOpenAI(original):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.responses = Responses()

    module.AsyncOpenAI = AsyncOpenAI
    return module


def test_openai_reasoning_models_use_responses_api_and_parse_output(sdk, monkeypatch):
    """GPT-5 uses Responses, retaining summaries and response function IDs."""
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_responses(captured))

    response = asyncio.run(
        _client(
            sdk,
            provider="openai",
            key="test-key",
            model="gpt-5-mini",
        ).chat(
            "What is the weather?",
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "weather",
                        "description": "Look up weather",
                        "parameters": {"type": "object"},
                    },
                }
            ],
        )
    )

    sent = captured["responses_create"]
    assert "create" not in captured
    assert sent["model"] == "gpt-5-mini"
    assert sent["max_output_tokens"] == 1024
    assert "temperature" not in sent
    assert sent["input"] == [{"role": "user", "content": "What is the weather?"}]
    assert sent["tools"] == [
        {
            "type": "function",
            "name": "weather",
            "description": "Look up weather",
            "parameters": {"type": "object"},
        }
    ]
    assert response.content == "The temperature is 20C."
    assert response.reasoning == "I should call the weather tool."
    assert response.parts[0]["type"] == "reasoning"
    assert response.parts[1]["call_id"] == "resp_call_weather"
    assert response.metadata["parts_provider"] == "openai_responses"
    assert response.tool_calls == [
        {
            "id": "resp_call_weather",
            "name": "weather",
            "arguments": '{"city":"Paris"}',
        }
    ]
    assert response.tokens_in == 7
    assert response.tokens_out == 11


def test_openai_responses_continue_tool_calls_with_original_output_items(
    sdk, monkeypatch
):
    """Tool results continue a Responses turn via function_call_output/call_id."""
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_responses(captured))
    reasoning_item = {
        "type": "reasoning",
        "id": "rs_123",
        "summary": [{"text": "Need weather."}],
    }
    call_item = {
        "type": "function_call",
        "call_id": "resp_call_weather",
        "name": "weather",
        "arguments": '{"city":"Paris"}',
    }
    messages = [
        sdk.package.AIMessage(
            role="assistant",
            content="",
            parts=[reasoning_item, call_item],
            tool_calls=[
                {
                    "id": "resp_call_weather",
                    "name": "weather",
                    "arguments": '{"city":"Paris"}',
                }
            ],
            metadata={"parts_provider": "openai_responses"},
        ),
        sdk.package.AIMessage(
            role="tool",
            name="weather",
            tool_call_id="resp_call_weather",
            tool_result='{"temperature": 20}',
        ),
    ]

    asyncio.run(
        _client(
            sdk,
            provider="openai",
            key="test-key",
            model="codex-mini-latest",
        ).chat(messages)
    )

    assert captured["responses_create"]["input"] == [
        reasoning_item,
        call_item,
        {
            "type": "function_call_output",
            "call_id": "resp_call_weather",
            "output": '{"temperature": 20}',
        },
    ]


# ── Review regression coverage ──────────────────────────────────────────────


def test_assistant_tool_calls_use_exact_openai_chat_wire_shape(sdk):
    """Normalized calls become Chat Completions function envelopes and JSON text."""
    message = sdk.package.AIMessage(
        role="assistant",
        content="",
        tool_calls=[
            {
                "id": "call_1",
                "name": "weather",
                "arguments": {"city": "Paris"},
            }
        ],
    )
    wire = message.to_openai()
    assert wire == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "weather", "arguments": '{"city":"Paris"}'},
            }
        ],
    }


def test_cross_provider_serializers_do_not_replay_foreign_native_parts(sdk):
    """A Responses continuation cannot be sent as Chat/Gemini native blocks."""
    message = sdk.package.AIMessage(
        role="assistant",
        content="visible answer",
        parts=[{"type": "reasoning", "id": "rs_1"}],
        metadata={"parts_provider": "openai_responses"},
        tool_calls=[
            {"id": "call_1", "name": "weather", "arguments": {"city": "Paris"}}
        ],
    )
    assert message.to_openai()["content"] == "visible answer"
    assert message.to_openai()["tool_calls"][0]["type"] == "function"
    gemini_parts = message.to_gemini()["parts"]
    assert all(
        "reasoning" not in part for part in gemini_parts if isinstance(part, dict)
    )
    assert (
        gemini_parts.count(
            {"function_call": {"name": "weather", "args": {"city": "Paris"}}}
        )
        == 1
    )
    anthropic = message.to_anthropic()["content"]
    assert all(block.get("type") != "reasoning" for block in anthropic)


def test_responses_output_items_are_plain_dicts_and_continue_without_duplicate_text(
    sdk, monkeypatch
):
    """Pydantic-like output is dumped before replay; native message text is not repeated."""
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai_responses(captured))

    class PydanticItem:
        def model_dump(self, **kwargs):
            return {
                "type": "message",
                "content": [{"type": "output_text", "text": "hello"}],
            }

    response = asyncio.run(
        _client(sdk, provider="openai", key="test-key", model="gpt-5-mini").chat("hi")
    )
    assert all(isinstance(part, dict) for part in response.parts)
    message = sdk.package.AIMessage(
        role="assistant",
        content="hello",
        parts=[PydanticItem()],
        metadata={"parts_provider": "openai_responses"},
    )
    assert sdk.package.OpenAIProvider._responses_input([message]) == [
        {
            "type": "message",
            "content": [{"type": "output_text", "text": "hello"}],
        }
    ]


def test_response_ok_requires_answer_tool_call_or_image(sdk):
    """Opaque native parts alone are continuation state, not a successful reply."""
    Response = sdk.package.AIResponse
    assert not Response("", "test", "model", parts=[{"type": "reasoning"}]).ok
    assert Response("", "test", "model", tool_calls=[{"id": "call_1"}]).ok
    assert Response("", "test", "model", image="base64").ok
    assert Response("answer", "test", "model").ok


def test_client_input_is_not_mutated_and_response_modalities_are_gemini_only(
    sdk, monkeypatch
):
    captured = {}
    monkeypatch.setitem(sys.modules, "openai", _fake_openai(captured))
    original = sdk.package.AIMessage(role="user", content="hi")
    asyncio.run(
        _client(sdk, provider="openai", key="test-key").chat(
            [original],
            response_modalities=["TEXT"],
            reasoning_effort="high",
        )
    )
    assert original.modalities == []
    assert "modalities" not in captured["create"]
    with pytest.raises(ValueError, match="at least one"):
        asyncio.run(_client(sdk, provider="openai", key="test-key").chat([]))


def test_ai_client_stream_and_search_use_selected_mock_provider(sdk):
    """Public stream/search APIs delegate through the selected provider only."""
    captured = {"stream_messages": [], "search_messages": []}

    class Provider:
        name = "fake"

        async def verify(self):
            return True

        async def complete(self, messages, **kwargs):
            captured["search_messages"] = messages
            captured["search_kwargs"] = kwargs
            return sdk.package.AIResponse("researched", "fake", "m")

        async def stream(self, messages, **kwargs):
            captured["stream_messages"] = messages
            captured["stream_kwargs"] = kwargs
            for chunk in ("one", "two"):
                yield chunk

    client = _client(sdk, provider=Provider(), model="m")

    async def exercise():
        chunks = [chunk async for chunk in client.stream("hello", system="s")]
        response = await client.search("current facts")
        return chunks, response

    chunks, response = asyncio.run(exercise())
    assert chunks == ["one", "two"]
    assert captured["stream_messages"][0].content == "hello"
    assert captured["stream_kwargs"]["model"] == "m"
    assert response.content == "researched"
    assert (
        captured["search_messages"][0].content == "Research this query: current facts"
    )
    assert captured["search_kwargs"]["system"].startswith("Use available search tools")


def test_public_ultroid_agent_uses_injected_ai_client(sdk):
    """The public agent keeps an injected AIClient instance-local and routes it."""
    captured = []

    class Client:
        name = "injected"

        async def complete(self, messages, **kwargs):
            captured.append((messages, kwargs))
            return sdk.package.AIResponse("injected answer", "injected", "mock-model")

    client = _client(sdk, provider=Client())
    instance = sdk.package.UltroidAgent(client=client)
    response = asyncio.run(
        instance.respond(
            "Explain the injected provider route.",
            chat_id=77,
            use_tools=False,
            use_cache=False,
        )
    )

    assert instance.mini._provider is client
    assert response.content == "injected answer"
    assert response.provider == "injected"
    assert captured[0][0][-1].content == "Explain the injected provider route."


# ── Final serializer/dispatch regressions ───────────────────────────────────


def test_assistant_serializers_ignore_untagged_native_parts(sdk):
    """Assistant parts are replayed only after an explicit provider origin tag."""
    message = sdk.package.AIMessage(
        role="assistant",
        content="normalized text",
        parts=[{"type": "foreign_native", "payload": "discard"}],
        tool_calls=[{"id": "call_1", "name": "lookup", "arguments": {}}],
    )
    assert message.to_openai()["content"] == "normalized text"
    assert message.to_anthropic()["content"][0] == {
        "type": "text",
        "text": "normalized text",
    }
    gemini_parts = message.to_gemini()["parts"]
    assert {"type": "foreign_native", "payload": "discard"} not in gemini_parts
    assert gemini_parts[0] == {"text": "normalized text"}


def test_gemini_user_parts_survive_unrelated_message_metadata(sdk):
    """Caller input parts remain valid Gemini content with non-origin metadata."""
    image = {"inline_data": {"mime_type": "image/png", "data": "AA=="}}
    message = sdk.package.AIMessage(
        role="user",
        parts=[{"text": "describe"}, image],
        metadata={"request_id": "local-only"},
    )
    assert message.to_gemini()["parts"] == [{"text": "describe"}, image]


def test_responses_input_preserves_caller_multimodal_parts(sdk):
    """Responses user input blocks retain input_text and input_image parts."""
    parts = [
        {"type": "input_text", "text": "What is shown?"},
        {"type": "input_image", "image_url": "https://example.test/cat.png"},
    ]
    message = sdk.package.AIMessage(role="user", parts=parts)
    assert sdk.package.OpenAIProvider._responses_input([message]) == [
        {
            "role": "user",
            "content": parts,
        }
    ]


def test_client_filters_unsupported_controls_before_one_adapter_call(sdk):
    """Signature filtering avoids a failed first call and never retries work."""
    captured = {"calls": 0}

    class NarrowProvider:
        name = "narrow"

        async def complete(
            self,
            messages,
            model=None,
            tools=None,
            system=None,
            max_tokens=None,
            temperature=None,
        ):
            captured["calls"] += 1
            captured["kwargs"] = {
                "model": model,
                "tools": tools,
                "system": system,
                "max_tokens": max_tokens,
                "temperature": temperature,
            }
            return sdk.package.AIResponse("ok", "narrow", "mock")

    response = asyncio.run(
        _client(sdk, provider=NarrowProvider()).chat(
            "hello",
            reasoning={"effort": "high"},
            reasoning_effort="high",
            tool_call={"id": "call_1"},
            response_modalities=["text"],
        )
    )
    assert response.ok
    assert captured["calls"] == 1
    assert captured["kwargs"]["model"] is None


def test_gemini_response_modalities_are_uppercased(sdk, monkeypatch):
    captured = {}
    for name, module in _fake_google_genai(captured).items():
        monkeypatch.setitem(sys.modules, name, module)
    asyncio.run(
        _client(sdk, provider="gemini", key="test-key").chat(
            "show an image",
            response_modalities=["text", "image"],
        )
    )
    assert captured["generate"]["config"]["response_modalities"] == ["TEXT", "IMAGE"]

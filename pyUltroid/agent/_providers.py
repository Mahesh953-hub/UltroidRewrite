"""AIProviders  -  unified interface across all AI providers running in parallel.

Supported:
   OpenAI         -  GPT-4o, GPT-4o-mini, o1, o3
   Anthropic      -  Claude Opus, Sonnet, Haiku
   Google         -  Gemini 1.5 Pro, Flash
   Groq           -  Llama, Mixtral (ultra-fast inference)
   Ollama         -  Any local model (llama3, mistral, phi3, qwen, etc.)
   OpenAI-compat  -  Any OpenAI-compatible endpoint (LM Studio, vLLM, etc.)
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from ._config import AgentConfig
from collections.abc import AsyncGenerator

from pyUltroid.startup.logger import LOGS

from . import _config as agent_config


# ── Normalized message and response types
# ``content`` remains the familiar text field for backwards compatibility.
# Native provider parts are retained only with an explicit origin tag.  This
# prevents opaque SDK blocks from one wire protocol leaking into another.
def _value(value: Any, name: str, default: Any = None) -> Any:
    return (
        value.get(name, default)
        if isinstance(value, dict)
        else getattr(value, name, default)
    )


def _plain_data(value: Any) -> Any:
    """Convert SDK/Pydantic values to transport-safe, plain Python objects."""
    if isinstance(value, dict):
        return {str(key): _plain_data(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain_data(item) for item in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _plain_data(model_dump(mode="json", exclude_none=True))
        except TypeError:
            return _plain_data(model_dump())
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _plain_data(to_dict())
    if hasattr(value, "__dict__"):
        return {
            key: _plain_data(item)
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
    return value


def _tool_arguments(value: Any) -> Any:
    if isinstance(value, str):
        try:
            import json

            return json.loads(value)
        except Exception:
            return value
    return value or {}


def _tool_arguments_json(value: Any) -> str:
    """OpenAI Chat Completions requires function arguments as JSON text."""
    if isinstance(value, str):
        return value
    import json

    return json.dumps(value or {}, separators=(",", ":"), default=str)


def _native_parts(message: AIMessage, provider: str) -> list[Any]:
    origin = message.metadata.get("parts_provider") or message.metadata.get(
        "native_provider"
    )
    return list(message.parts) if origin == provider else []


@dataclass
class AIMessage:
    role: str  # system | user | assistant | tool
    content: Any = ""
    name: str | None = None  # tool name when role == tool
    parts: list[Any] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    tool_call_id: str | None = None
    tool_result: Any = None
    reasoning: str | None = None
    thinking: str | None = None
    modalities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.content is None:
            self.content = ""
        if not self.parts and isinstance(self.content, list):
            self.parts, self.content = list(self.content), ""
        if self.tool_result is not None and not self.content:
            self.content = (
                self.tool_result
                if isinstance(self.tool_result, str)
                else str(self.tool_result)
            )

    def text(self) -> str:
        if isinstance(self.content, str):
            return self.content
        if self.content is None:
            return ""
        return str(self.content)

    def to_openai(self) -> dict:
        # Chat Completions uses the exact ``type=function`` envelope. Responses
        # output items are intentionally handled only by ``_responses_input``.
        content = _native_parts(self, "openai_chat") or self.text()
        d = {"role": self.role, "content": content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.role == "assistant" and self.tool_calls:
            d["tool_calls"] = [
                {
                    "id": call.get("id", ""),
                    "type": "function",
                    "function": {
                        "name": call.get("name", ""),
                        "arguments": _tool_arguments_json(call.get("arguments", {})),
                    },
                }
                for call in self.tool_calls
            ]
        return d

    def to_anthropic(self) -> dict:
        if self.role == "tool":
            result = self.tool_result if self.tool_result is not None else self.content
            return {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": self.tool_call_id or self.name or "",
                        "content": (
                            result if isinstance(result, (str, list)) else str(result)
                        ),
                    }
                ],
            }
        if self.role == "assistant":
            blocks = _native_parts(self, "anthropic")
            if not blocks:
                blocks = [{"type": "text", "text": self.text()}] if self.text() else []
                blocks += [
                    {
                        "type": "tool_use",
                        "id": call.get("id", ""),
                        "name": call.get("name", ""),
                        "input": _tool_arguments(call.get("arguments", {})),
                    }
                    for call in self.tool_calls
                ]
            return {"role": "assistant", "content": blocks or ""}
        origin = self.metadata.get("parts_provider") or self.metadata.get(
            "native_provider"
        )
        return {
            "role": self.role,
            "content": (
                _native_parts(self, "anthropic")
                or (list(self.parts) if self.parts and not origin else self.text())
            ),
        }

    def to_gemini(self) -> dict:
        role = "model" if self.role == "assistant" else "user"
        if self.role == "tool":
            response = (
                self.tool_result if self.tool_result is not None else self.content
            )
            return {
                "role": "user",
                "parts": [
                    {
                        "function_response": {
                            "name": self.name or "tool",
                            "response": {"result": response},
                        }
                    }
                ],
            }
        # Untagged user parts are caller-authored Gemini content. Provider
        # responses must be tagged and are replayed only to Gemini.
        origin = self.metadata.get("parts_provider") or self.metadata.get(
            "native_provider"
        )
        parts = _native_parts(self, "gemini") or (
            list(self.parts) if self.role != "assistant" and not origin else []
        )
        if not parts and self.text():
            parts = [{"text": self.text()}]
        native_calls = {
            (
                _value(_value(part, "function_call", {}), "name", ""),
                _tool_arguments(
                    _value(_value(part, "function_call", {}), "args", {})
                ).__repr__(),
            )
            for part in parts
            if _value(part, "function_call")
        }
        for call in self.tool_calls:
            identity = (
                call.get("name", ""),
                _tool_arguments(call.get("arguments", {})).__repr__(),
            )
            if identity not in native_calls:
                parts.append(
                    {
                        "function_call": {
                            "name": call.get("name", ""),
                            "args": _tool_arguments(call.get("arguments", {})),
                        }
                    }
                )
        return {"role": role, "parts": parts or [{"text": ""}]}


@dataclass
class AIResponse:
    content: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    tool_calls: list[dict] = field(default_factory=list)
    image: str | None = None
    reasoning: str | None = None
    thinking: str | None = None
    parts: list[Any] = field(default_factory=list)
    modalities: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    @property
    def ok(self) -> bool:
        # Opaque parts are diagnostic/continuation state, not an answer.
        return self.error is None and bool(
            self.content or self.tool_calls or self.image
        )

    def __str__(self) -> str:
        return self.content


def _normalise_openai_tool_calls(tool_calls: Any) -> list[dict]:
    calls = []
    for call in tool_calls or []:
        function = _value(call, "function", {}) or {}
        calls.append(
            {
                "id": _value(call, "id", ""),
                "name": _value(function, "name", _value(call, "name", "")),
                "arguments": _value(
                    function, "arguments", _value(call, "arguments", {})
                ),
            }
        )
    return calls


def _join_text_parts(parts: Any) -> str:
    text = []
    for part in parts or []:
        if isinstance(part, str):
            text.append(part)
            continue
        value = _value(part, "text")
        if value:
            text.append(str(value))
    return "".join(text)


# ── Cost tiers (for mini-AI routing)
class CostTier(Enum):
    FREE = 0  # local LLM  -  no cost
    CHEAP = 1  # Groq, flash models
    MEDIUM = 2  # Gemini Pro, GPT-4o-mini, Claude Haiku
    PREMIUM = 3  # GPT-4o, Claude Sonnet/Opus, Gemini 1.5 Pro


# ── Base provider
class BaseProvider:
    name: str = "base"
    cost_tier: CostTier = CostTier.MEDIUM
    default_model: str = ""

    def is_configured(self) -> bool:
        raise NotImplementedError

    def model_capabilities(self, model: str | None = None) -> dict:
        """Supported generation parameters for *model* (or the default model).

        Returns a dict describing how the logical generation controls map onto
        this provider's native API for the requested model:

          ``max_tokens``   -  native parameter name for the output-token cap
                             (``"max_tokens"`` / ``"max_completion_tokens"`` /
                             ``"max_output_tokens"`` / ``"num_predict"``), or
                             ``None`` when the model accepts no such cap
                             (e.g. image-generation models).
          ``temperature``  -  whether a sampling-temperature parameter is
                             accepted. OpenAI o1/o3 reasoning models reject it.
          ``tools``        -  whether function/tool calling is wired up.
          ``system``       -  whether a system prompt is honoured.
          ``stream``       -  whether streaming generation is supported.

        Subclasses override to express per-model differences; the base returns
        the common OpenAI-style defaults.
        """
        return {
            "max_tokens": "max_tokens",
            "temperature": True,
            "tools": True,
            "system": True,
            "stream": True,
        }

    def _completion_params(self, model, max_tokens, temperature) -> dict:
        """Map the logical ``max_tokens`` / ``temperature`` controls onto this
        provider's native completion kwargs for *model*, honouring
        :meth:`model_capabilities`.

        Temperature is omitted entirely for models that reject it (never sent
        as a silently-ignored value), and the token-cap is sent under whatever
        native name the model expects (``max_completion_tokens`` for o-series,
        ``max_output_tokens`` for Gemini, …). Returns an empty mapping when the
        model accepts neither (image generation)."""
        caps = self.model_capabilities(model)
        params: dict[str, Any] = {}
        token_param = caps.get("max_tokens")
        if token_param and max_tokens is not None:
            params[token_param] = max_tokens
        if caps.get("temperature", True) and temperature is not None:
            params["temperature"] = temperature
        return params

    async def verify(self) -> bool:
        """Verify configured credentials with a provider read-only endpoint."""
        return self.is_configured()

    async def list_models(self) -> list[str]:
        """Return available models where the provider exposes a listing API."""
        return []

    async def complete(
        self,
        messages: list[AIMessage],
        model: str | None = None,
        tools: list | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> AIResponse:
        raise NotImplementedError

    async def stream(
        self,
        messages: list[AIMessage],
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        system: str | None = None,
    ) -> AsyncGenerator[str]:
        raise NotImplementedError


# ── Ollama (local LLM)
class OllamaProvider(BaseProvider):
    name = "ollama"
    cost_tier = CostTier.FREE

    def __init__(self):
        self._explicit_base = None
        self._model_override = None

    @property
    def base_url(self):
        return (
            self._explicit_base
            or AgentConfig().config_field("ollama", "BASE")
            or "http://localhost:11434"
        )

    @base_url.setter
    def base_url(self, value):
        # An explicit override from a one-off caller stays in memory; the
        # persistent configuration only changes through the menu.
        self._explicit_base = value

    @property
    def default_model(self):
        # Database first: the setter is an in-process fallback that applies
        # only while the database holds no model. A later database change is
        # observed on the same instance (see tests/test_agent.py).
        return (
            AgentConfig().config_field("ollama", "MODEL")
            or self._model_override
            or "llama3"
        )

    @default_model.setter
    def default_model(self, value):
        self._model_override = value

    def is_configured(self) -> bool:
        # returns true because we haven't setup the model yet.
        return True

    def model_capabilities(self, model=None):
        # Ollama exposes the token cap as ``num_predict`` inside the
        # ``options`` object (see complete()), and drops the tool schema, so
        # tool-calling requests skip this tier entirely.
        return {
            "max_tokens": "num_predict",
            "temperature": True,
            "tools": False,
            "system": True,
            "stream": True,
        }

    async def verify(self) -> bool:
        # Listing local tags is a read-only reachability/auth check.
        return await self._is_running()

    async def _is_running(self) -> bool:
        try:
            import aiohttp

            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{self.base_url}/api/tags", timeout=aiohttp.ClientTimeout(total=3)
                ) as r:
                    return r.status == 200
        except Exception:
            return False

    async def complete(
        self,
        messages,
        model=None,
        tools=None,
        max_tokens=1024,
        temperature=0.7,
        system=None,
    ) -> AIResponse:
        t0 = time.time()
        model = model or self.default_model
        if not await self._is_running():
            return AIResponse(
                "",
                self.name,
                model,
                error="Ollama not running",
                duration_ms=(time.time() - t0) * 1000,
            )
        try:
            import aiohttp

            payload = {
                "model": model,
                "messages": [m.to_openai() for m in messages],
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            if system:
                payload["system"] = system

            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as r:
                    data = await r.json()
                    content = data.get("message", {}).get("content", "")
                    return AIResponse(
                        content=content,
                        provider=self.name,
                        model=model,
                        tokens_in=data.get("prompt_eval_count", 0),
                        tokens_out=data.get("eval_count", 0),
                        duration_ms=(time.time() - t0) * 1000,
                    )
        except Exception as e:
            return AIResponse(
                "",
                self.name,
                model,
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )

    async def stream(
        self, messages, model=None, max_tokens=1024, temperature=0.7, system=None
    ) -> AsyncGenerator[str]:
        model = model or self.default_model
        if not await self._is_running():
            yield "[Ollama not running]"
            return
        try:
            import aiohttp, json as _json

            payload = {
                "model": model,
                "messages": [m.to_openai() for m in messages],
                "stream": True,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            }
            if system:
                payload["system"] = system

            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as r:
                    async for line in r.content:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            chunk = _json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                yield token
                            if chunk.get("done"):
                                break
                        except Exception:
                            continue
        except Exception as e:
            yield f"[Ollama stream error: {e}]"

    async def list_models(self) -> list[str]:
        try:
            import aiohttp

            async with aiohttp.ClientSession() as s:
                async with s.get(
                    f"{self.base_url}/api/tags", timeout=aiohttp.ClientTimeout(total=5)
                ) as r:
                    data = await r.json()
                    return [m["name"] for m in data.get("models", [])]
        except Exception:
            return []

    async def pull_model(self, model: str) -> AsyncGenerator[str]:
        """Pull a model from Ollama registry  -  streams progress."""
        try:
            import aiohttp, json as _json

            async with aiohttp.ClientSession() as s:
                async with s.post(
                    f"{self.base_url}/api/pull",
                    json={"name": model, "stream": True},
                    timeout=aiohttp.ClientTimeout(total=600),
                ) as r:
                    async for line in r.content:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            chunk = _json.loads(line)
                            status = chunk.get("status", "")
                            if status:
                                yield status
                        except Exception:
                            continue
        except Exception as e:
            yield f"Pull error: {e}"


# ── OpenAI
_O_SERIES_RE = re.compile(r"^o\d")
# GPT-5 and Codex are Responses-first. Keep the established o-series Chat
# Completions path for backward compatibility with older OpenAI SDKs and
# compatible deployments that expose o1/o3 but not ``client.responses``.
_RESPONSES_MODEL_RE = re.compile(r"^(?:gpt-5|codex)", re.IGNORECASE)


def _is_o_series(model: str) -> bool:
    return bool(_O_SERIES_RE.match(model or ""))


def _uses_openai_responses(model: str) -> bool:
    """Return whether an OpenAI reasoning model needs the Responses API."""
    return bool(_RESPONSES_MODEL_RE.match(model or ""))


class OpenAIProvider(BaseProvider):
    name = "openai"
    cost_tier = CostTier.PREMIUM
    default_model = "gpt-4o-mini"

    def __init__(self, api_key=None, base_url=None, model=None):
        self._api_key = api_key
        self._base_url = base_url
        self._default_model = model

    @property
    def api_key(self):
        return self._api_key or AgentConfig().config_field("openai", "KEY")

    @property
    def base_url(self):
        return self._base_url or AgentConfig().config_field("openai", "BASE")

    @property
    def default_model(self):
        return (
            self._default_model
            or AgentConfig().config_field("openai", "MODEL")
            or "gpt-4o-mini"
        )

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def model_capabilities(self, model=None):
        caps = dict(super().model_capabilities(model))
        model = model or self.default_model
        if self._use_responses_api(model):
            # Native Responses reasoning models use this output-token cap and
            # reject sampling controls such as temperature.
            caps["max_tokens"] = "max_output_tokens"
            caps["temperature"] = False
        elif _is_o_series(model):
            # Compatibility endpoints retain their existing Chat Completions
            # contract even when their model name resembles an o-series model.
            caps["max_tokens"] = "max_completion_tokens"
            caps["temperature"] = False
        return caps

    def _client(self):
        from openai import AsyncOpenAI

        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return AsyncOpenAI(**kwargs)

    async def list_models(self) -> list[str]:
        if not self.is_configured():
            return []
        try:
            models = await self._client().models.list()
            return [
                _value(item, "id", "")
                for item in _value(models, "data", [])
                if _value(item, "id", "")
            ]
        except Exception:
            return []

    async def verify(self) -> bool:
        if not self.is_configured():
            return False
        try:
            await self._client().models.list()
            return True
        except Exception:
            return False

    def _use_responses_api(self, model: str) -> bool:
        """Use native Responses only for OpenAI's own reasoning models.

        OpenAI-compatible endpoints frequently expose only Chat Completions,
        even when they use an o-series-like model name.  A configured base URL
        is therefore kept on that established compatibility path.
        """
        return (
            self.__class__ is OpenAIProvider
            and not self.base_url
            and _uses_openai_responses(model)
        )

    @staticmethod
    def _responses_tools(tools) -> list:
        """Translate Chat Completions function schemas to Responses schemas."""
        result = []
        for tool in tools or []:
            if not isinstance(tool, dict):
                result.append(tool)
                continue
            if tool.get("type") != "function" or "function" not in tool:
                result.append(tool)
                continue
            function = tool["function"]
            result.append(
                {
                    key: function[key]
                    for key in ("name", "description", "parameters", "strict")
                    if key in function
                }
                | {"type": "function"}
            )
        return result

    @staticmethod
    def _responses_input(messages, system=None) -> list:
        """Build a safe Responses continuation from normalized messages.

        Only plain, OpenAI-Responses-origin items are replayed. Text represented
        by a previous message item is not appended again, and SDK objects are
        converted to dicts before being handed back to the OpenAI client.
        """
        items = [{"role": "system", "content": system}] if system else []
        for message in messages:
            if message.role == "tool":
                output = (
                    message.tool_result
                    if message.tool_result is not None
                    else message.content
                )
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": message.tool_call_id or "",
                        "output": output if isinstance(output, str) else str(output),
                    }
                )
                continue

            native = _native_parts(message, "openai_responses")
            known_call_ids = set()
            if message.role == "assistant" and native:
                native = [_plain_data(item) for item in native]
                items.extend(native)
                known_call_ids = {_value(item, "call_id", "") for item in native}
                # Native message output already contains its text, so adding
                # ``message.content`` here would duplicate the assistant turn.
            else:
                origin = message.metadata.get("parts_provider") or message.metadata.get(
                    "native_provider"
                )
                # User-provided Responses blocks (input_text/input_image/etc.)
                # are protocol-neutral request data, not provider output. Keep
                # them intact unless they declare a foreign provider origin.
                content = (
                    _plain_data(message.parts)
                    if message.role == "user" and message.parts and not origin
                    else message.text()
                )
                items.append({"role": message.role, "content": content})

            if message.role == "assistant":
                for call in message.tool_calls:
                    call_id = call.get("id", "")
                    if call_id and call_id in known_call_ids:
                        continue
                    items.append(
                        {
                            "type": "function_call",
                            "call_id": call_id,
                            "name": call.get("name", ""),
                            "arguments": _tool_arguments_json(
                                call.get("arguments", {})
                            ),
                        }
                    )
        return items

    async def _complete_responses(
        self, messages, model, tools, max_tokens, temperature, system, reasoning, t0
    ) -> AIResponse:
        kwargs = {"model": model, "input": self._responses_input(messages, system)}
        kwargs.update(self._completion_params(model, max_tokens, temperature))
        if tools:
            kwargs["tools"] = self._responses_tools(tools)
            kwargs["tool_choice"] = "auto"
        if reasoning:
            kwargs["reasoning"] = reasoning

        resp = await self._client().responses.create(**kwargs)
        output = [_plain_data(item) for item in (_value(resp, "output", []) or [])]
        content = _value(resp, "output_text", "") or ""
        reasoning_summaries = []
        tool_calls = []
        for item in output:
            item_type = _value(item, "type")
            if item_type == "reasoning":
                summary = _value(item, "summary", [])
                summary = summary if isinstance(summary, list) else [summary]
                reasoning_summaries.extend(
                    str(_value(part, "text", part)) for part in summary if part
                )
            elif item_type == "function_call":
                tool_calls.append(
                    {
                        "id": _value(item, "call_id", ""),
                        "name": _value(item, "name", ""),
                        "arguments": _value(item, "arguments", "{}"),
                    }
                )
            elif not content and item_type == "message":
                content = _join_text_parts(_value(item, "content", []))
        usage = _value(resp, "usage", {}) or {}
        return AIResponse(
            content=content,
            provider=self.name,
            model=model,
            tokens_in=_value(usage, "input_tokens", 0) or 0,
            tokens_out=_value(usage, "output_tokens", 0) or 0,
            duration_ms=(time.time() - t0) * 1000,
            tool_calls=tool_calls,
            reasoning="\n".join(reasoning_summaries) or None,
            parts=output,
            modalities=["text"] if content else [],
            metadata={"parts_provider": "openai_responses"},
            raw=resp,
        )

    async def complete(
        self,
        messages,
        model=None,
        tools=None,
        max_tokens=1024,
        temperature=0.7,
        system=None,
        reasoning=None,
        **kwargs,
    ) -> AIResponse:
        t0 = time.time()
        if not self.is_configured():
            return AIResponse(
                "",
                self.name,
                model or self.default_model,
                error="OPENAI_API_KEY not set",
            )
        model = model or self.default_model
        try:
            if self._use_responses_api(model):
                if reasoning is None and kwargs.get("reasoning_effort"):
                    reasoning = {"effort": kwargs["reasoning_effort"]}
                return await self._complete_responses(
                    messages,
                    model,
                    tools,
                    max_tokens,
                    temperature,
                    system,
                    reasoning,
                    t0,
                )

            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs += [m.to_openai() for m in messages]

            kwargs = dict(model=model, messages=msgs)
            kwargs.update(self._completion_params(model, max_tokens, temperature))
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            resp = await self._client().chat.completions.create(**kwargs)
            choice = resp.choices[0]
            msg = choice.message
            tcs = []
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                tcs = [
                    {
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                    for tc in msg.tool_calls
                ]
            return AIResponse(
                content=msg.content or "",
                provider=self.name,
                model=model,
                tokens_in=resp.usage.prompt_tokens,
                tokens_out=resp.usage.completion_tokens,
                duration_ms=(time.time() - t0) * 1000,
                tool_calls=tcs,
                reasoning=(
                    _value(msg, "reasoning_content")
                    or _value(msg, "reasoning")
                    or _value(msg, "thinking")
                ),
                parts=(
                    _plain_data(_value(msg, "content", []))
                    if isinstance(_value(msg, "content"), list)
                    else []
                ),
                modalities=["text"] if msg.content else [],
                metadata={"parts_provider": "openai_chat"},
                raw=resp,
            )
        except Exception as e:
            return AIResponse(
                "",
                self.name,
                model,
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )

    async def stream(
        self, messages, model=None, max_tokens=1024, temperature=0.7, system=None
    ) -> AsyncGenerator[str]:
        if not self.is_configured():
            yield "[OpenAI not configured]"
            return
        model = model or self.default_model
        try:
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs += [m.to_openai() for m in messages]
            stream_kwargs = dict(model=model, messages=msgs)
            stream_kwargs.update(
                self._completion_params(model, max_tokens, temperature)
            )
            async with self._client().chat.completions.stream(
                **stream_kwargs,
            ) as stream:
                async for text in stream.text_stream:
                    yield text
        except Exception as e:
            yield f"[OpenAI stream error: {e}]"


# ── Anthropic
class AnthropicProvider(BaseProvider):
    name = "anthropic"
    cost_tier = CostTier.PREMIUM

    def __init__(self):
        self._explicit_key = None
        self._explicit_model = None

    @property
    def api_key(self):
        return self._explicit_key or AgentConfig().config_field("anthropic", "KEY")

    @api_key.setter
    def api_key(self, value):
        self._explicit_key = value

    @property
    def default_model(self):
        return (
            self._explicit_model
            or AgentConfig().config_field("anthropic", "MODEL")
            or "claude-haiku-4-5-20251001"
        )

    @default_model.setter
    def default_model(self, value):
        self._explicit_model = value

    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def verify(self) -> bool:
        if not self.is_configured():
            return False
        try:
            import anthropic

            # The models listing endpoint validates credentials without usage.
            await anthropic.AsyncAnthropic(api_key=self.api_key).models.list(limit=1)
            return True
        except Exception:
            return False

    def model_capabilities(self, model=None):
        # Anthropic uses ``max_tokens`` (required) and supports temperature,
        # tool_use and system prompts across the Claude family.
        return {
            "max_tokens": "max_tokens",
            "temperature": True,
            "tools": True,
            "system": True,
            "stream": True,
        }

    async def complete(
        self,
        messages,
        model=None,
        tools=None,
        max_tokens=1024,
        temperature=0.7,
        system=None,
    ) -> AIResponse:
        t0 = time.time()
        if not self.is_configured():
            return AIResponse(
                "",
                self.name,
                model or self.default_model,
                error="ANTHROPIC_API_KEY not set",
            )
        model = model or self.default_model
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=self.api_key)
            kwargs = dict(
                model=model,
                messages=[m.to_anthropic() for m in messages if m.role != "system"],
            )
            kwargs.update(self._completion_params(model, max_tokens, temperature))
            if system:
                kwargs["system"] = system
            elif any(m.role == "system" for m in messages):
                kwargs["system"] = next(
                    m.content for m in messages if m.role == "system"
                )
            if tools:
                kwargs["tools"] = tools

            resp = await client.messages.create(**kwargs)
            blocks = list(resp.content)
            content = "".join(
                _value(b, "text", "") for b in blocks if _value(b, "type") == "text"
            )
            thinking = (
                "".join(
                    _value(b, "thinking", "")
                    for b in blocks
                    if _value(b, "type") == "thinking"
                )
                or None
            )
            tcs = [
                {
                    "id": _value(b, "id", ""),
                    "name": _value(b, "name", ""),
                    "arguments": _value(b, "input", {}),
                }
                for b in blocks
                if _value(b, "type") == "tool_use"
            ]
            return AIResponse(
                content=content,
                provider=self.name,
                model=model,
                tokens_in=resp.usage.input_tokens,
                tokens_out=resp.usage.output_tokens,
                duration_ms=(time.time() - t0) * 1000,
                tool_calls=tcs,
                thinking=thinking,
                reasoning=thinking,
                parts=[_plain_data(block) for block in blocks],
                modalities=["text"] if content else [],
                metadata={"parts_provider": "anthropic"},
                raw=resp,
            )
        except Exception as e:
            return AIResponse(
                "",
                self.name,
                model,
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )

    async def stream(
        self, messages, model=None, max_tokens=1024, temperature=0.7, system=None
    ) -> AsyncGenerator[str]:
        if not self.is_configured():
            yield "[Anthropic not configured]"
            return
        model = model or self.default_model
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=self.api_key)
            sys_msg = system or next(
                (m.content for m in messages if m.role == "system"), None
            )
            kwargs = dict(
                model=model,
                messages=[m.to_anthropic() for m in messages if m.role != "system"],
            )
            kwargs.update(self._completion_params(model, max_tokens, temperature))
            if sys_msg:
                kwargs["system"] = sys_msg
            async with client.messages.stream(**kwargs) as s:
                async for text in s.text_stream:
                    yield text
        except Exception as e:
            yield f"[Anthropic stream error: {e}]"


# ── Gemini
class GeminiProvider(BaseProvider):
    name = "gemini"
    cost_tier = CostTier.MEDIUM

    def __init__(self):
        self._explicit_key = None
        self._explicit_model = None

    @property
    def api_key(self):
        return self._explicit_key or AgentConfig().config_field("gemini", "KEY")

    @api_key.setter
    def api_key(self, value):
        self._explicit_key = value

    @property
    def default_model(self):
        return (
            self._explicit_model
            or AgentConfig().config_field("gemini", "MODEL")
            or "gemini-2.5-flash"
        )

    @default_model.setter
    def default_model(self, value):
        self._explicit_model = value

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def model_capabilities(self, model=None):
        return {
            "max_tokens": "max_output_tokens",
            "temperature": True,
            "tools": True,
            "system": True,
            "stream": True,
        }

    async def _model_page(self, page_size=None):
        """Resolve the google-genai async pager across supported SDK releases."""
        config = {"page_size": page_size} if page_size else None
        page = (
            self._client().aio.models.list(config=config)
            if config
            else self._client().aio.models.list()
        )
        if hasattr(page, "__await__"):
            page = await page
        return page

    async def verify(self) -> bool:
        if not self.is_configured():
            return False
        try:
            page = await self._model_page(1)
            if hasattr(page, "__aiter__"):
                await anext(page.__aiter__(), None)
            else:
                iter(page)
            return True
        except Exception:
            return False

    async def list_models(self) -> list[str]:
        if not self.is_configured():
            return []
        try:
            models = await self._model_page()
            values = (
                [item async for item in models]
                if hasattr(models, "__aiter__")
                else models
            )
            return [
                _value(item, "name", "").removeprefix("models/")
                for item in values
                if _value(item, "name", "")
            ]
        except Exception:
            return []

    def _client(self):
        from google import genai

        return genai.Client(api_key=self.api_key)

    @staticmethod
    def _contents(messages) -> list:
        contents = [m.to_gemini() for m in messages if m.role != "system"]
        return contents or [{"role": "user", "parts": [{"text": ""}]}]

    @staticmethod
    def _tools(tools) -> list:
        """Accept native Gemini declarations and OpenAI function schemas."""
        if not tools:
            return []
        if any(
            "function_declarations" in tool for tool in tools if isinstance(tool, dict)
        ):
            return tools
        declarations = []
        for tool in tools:
            function = tool.get("function", tool) if isinstance(tool, dict) else tool
            if isinstance(function, dict) and function.get("name"):
                declaration = {
                    key: function[key]
                    for key in ("name", "description", "parameters")
                    if key in function
                }
                declarations.append(declaration)
        return [{"function_declarations": declarations}] if declarations else []

    def _config(
        self,
        messages,
        max_tokens,
        temperature,
        system,
        model=None,
        response_modalities=None,
    ) -> dict:
        cfg: dict[str, Any] = {}
        cfg.update(
            self._completion_params(
                model or self.default_model, max_tokens, temperature
            )
        )
        sys_msg = system or next(
            (m.content for m in messages if m.role == "system"), None
        )
        if sys_msg:
            cfg["system_instruction"] = sys_msg
        if response_modalities:
            cfg["response_modalities"] = list(response_modalities)
        return cfg

    async def complete(
        self,
        messages,
        model=None,
        tools=None,
        max_tokens=1024,
        temperature=0.7,
        system=None,
        response_modalities=None,
        **kwargs,
    ) -> AIResponse:
        t0 = time.time()
        if not self.is_configured():
            return AIResponse(
                "",
                self.name,
                model or self.default_model,
                error="GEMINI_API_KEY not set",
            )
        model = model or self.default_model
        try:
            client = self._client()
            config = self._config(
                messages,
                max_tokens,
                temperature,
                system,
                model,
                response_modalities=response_modalities,
            )
            gemini_tools = self._tools(tools)
            if gemini_tools:
                config["tools"] = gemini_tools
            resp = await client.aio.models.generate_content(
                model=model,
                contents=self._contents(messages),
                config=config,
            )
            usage = getattr(resp, "usage_metadata", None)
            candidates = _value(resp, "candidates", []) or []
            parts = (
                _value(_value(candidates[0], "content", {}), "parts", [])
                if candidates
                else []
            )
            content = getattr(resp, "text", "") or _join_text_parts(parts)
            thinking = (
                "".join(
                    str(_value(part, "thought", ""))
                    for part in parts
                    if _value(part, "thought", False)
                )
                or None
            )
            calls = []
            for part in parts:
                call = _value(part, "function_call")
                if call:
                    calls.append(
                        {
                            "id": _value(call, "id", "") or _value(part, "id", ""),
                            "name": _value(call, "name", ""),
                            "arguments": _value(call, "args", {}),
                        }
                    )
            return AIResponse(
                content=content,
                provider=self.name,
                model=model,
                tokens_in=getattr(usage, "prompt_token_count", 0) or 0,
                tokens_out=getattr(usage, "candidates_token_count", 0) or 0,
                duration_ms=(time.time() - t0) * 1000,
                tool_calls=calls,
                thinking=thinking,
                reasoning=thinking,
                parts=[_plain_data(part) for part in parts],
                modalities=["text"] if content else [],
                metadata={"parts_provider": "gemini"},
                raw=resp,
            )
        except Exception as e:
            return AIResponse(
                "",
                self.name,
                model,
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )

    async def stream(
        self, messages, model=None, max_tokens=1024, temperature=0.7, system=None
    ) -> AsyncGenerator[str]:
        if not self.is_configured():
            yield "[Gemini not configured]"
            return
        model = model or self.default_model
        try:
            client = self._client()
            stream = await client.aio.models.generate_content_stream(
                model=model,
                contents=self._contents(messages),
                config=self._config(messages, max_tokens, temperature, system, model),
            )
            async for chunk in stream:
                text = getattr(chunk, "text", "")
                if text:
                    yield text
        except Exception as e:
            yield f"[Gemini stream error: {e}]"


# ── Groq
class GroqProvider(BaseProvider):
    name = "groq"
    cost_tier = CostTier.CHEAP

    def __init__(self):
        self._explicit_key = None
        self._explicit_model = None

    @property
    def api_key(self):
        return self._explicit_key or AgentConfig().config_field("groq", "KEY")

    @api_key.setter
    def api_key(self, value):
        self._explicit_key = value

    @property
    def default_model(self):
        return (
            self._explicit_model
            or AgentConfig().config_field("groq", "MODEL")
            or "llama-3.3-70b-versatile"
        )

    @default_model.setter
    def default_model(self, value):
        self._explicit_model = value

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def model_capabilities(self, model=None):
        return {
            "max_tokens": "max_tokens",
            "temperature": True,
            "tools": True,
            "system": True,
            "stream": True,
        }

    async def verify(self) -> bool:
        if not self.is_configured():
            return False
        try:
            from groq import AsyncGroq

            await AsyncGroq(api_key=self.api_key).models.list()
            return True
        except Exception:
            return False

    async def complete(
        self,
        messages,
        model=None,
        tools=None,
        max_tokens=1024,
        temperature=0.7,
        system=None,
    ) -> AIResponse:
        t0 = time.time()
        if not self.is_configured():
            return AIResponse(
                "", self.name, model or self.default_model, error="GROQ_API_KEY not set"
            )
        model = model or self.default_model
        try:
            from groq import AsyncGroq

            client = AsyncGroq(api_key=self.api_key)
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs += [m.to_openai() for m in messages]
            kwargs = dict(model=model, messages=msgs)
            kwargs.update(self._completion_params(model, max_tokens, temperature))
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            resp = await client.chat.completions.create(**kwargs)
            choice = resp.choices[0].message
            return AIResponse(
                content=choice.content or "",
                provider=self.name,
                model=model,
                tokens_in=resp.usage.prompt_tokens,
                tokens_out=resp.usage.completion_tokens,
                duration_ms=(time.time() - t0) * 1000,
                tool_calls=_normalise_openai_tool_calls(
                    _value(choice, "tool_calls", [])
                ),
                reasoning=_value(choice, "reasoning")
                or _value(choice, "reasoning_content"),
                parts=(
                    _value(choice, "content", [])
                    if isinstance(_value(choice, "content"), list)
                    else []
                ),
                modalities=["text"] if choice.content else [],
                metadata={"parts_provider": "openai_chat"},
                raw=resp,
            )
        except Exception as e:
            return AIResponse(
                "",
                self.name,
                model,
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )

    async def stream(
        self, messages, model=None, max_tokens=1024, temperature=0.7, system=None
    ) -> AsyncGenerator[str]:
        if not self.is_configured():
            yield "[Groq not configured]"
            return
        model = model or self.default_model
        try:
            from groq import AsyncGroq

            client = AsyncGroq(api_key=self.api_key)
            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs += [m.to_openai() for m in messages]
            create_kwargs = dict(model=model, messages=msgs, stream=True)
            create_kwargs.update(
                self._completion_params(model, max_tokens, temperature)
            )
            stream = await client.chat.completions.create(**create_kwargs)
            async for chunk in stream:
                t = chunk.choices[0].delta.content
                if t:
                    yield t
        except Exception as e:
            yield f"[Groq stream error: {e}]"


# ── Configurable OpenAI-compatible providers
class ConfigurableOpenAIProvider(OpenAIProvider):
    """OpenAI wire-protocol provider with explicit per-client configuration.

    This is intentionally small and reusable: custom gateways, xAI and the
    official Meta Llama API's OpenAI-compatible deployment all share the same
    normalized tool/message contract without importing another SDK.
    """

    env_prefix = "COMPAT"
    default_base_url = None
    #: Maps the class's legacy ``env_prefix`` to the unified config provider.
    config_provider = "compat"

    def __init__(self, api_key=None, base_url=None, model=None, name=None):
        self.name = name or self.name
        self._explicit_key = api_key
        self._explicit_base_url = base_url
        self._explicit_model = model
        super().__init__(api_key=api_key, base_url=base_url, model=model)

    def _setting(self, suffix):
        # The unified configuration is the only source. The legacy
        # ``<PREFIX>_<SUFFIX>`` environment names reached it through
        # ``migrate_env_config`` on first boot.
        field = {"API_KEY": "KEY", "MODEL": "MODEL", "BASE_URL": "BASE"}.get(suffix)
        if field is None:
            return None
        try:
            return AgentConfig().config_field(self.config_provider, field)
        except ValueError:
            return None

    @property
    def api_key(self):
        return self._explicit_key or self._setting("API_KEY")

    @property
    def base_url(self):
        return (
            self._explicit_base_url
            or self._setting("BASE_URL")
            or self.default_base_url
        )

    @base_url.setter
    def base_url(self, value):
        self._explicit_base_url = value

    @property
    def default_model(self):
        return (
            self._explicit_model
            or self._setting("MODEL")
            or getattr(self, "fallback_model", "gpt-4o-mini")
        )

    def is_configured(self):
        return bool(self.api_key and self.base_url)

    async def complete(
        self,
        messages,
        model=None,
        tools=None,
        max_tokens=1024,
        temperature=0.7,
        system=None,
    ):
        if not self.is_configured():
            return AIResponse(
                "",
                self.name,
                model or self.default_model,
                error=f"{self.env_prefix}_API_KEY/{self.env_prefix}_BASE_URL not set",
            )
        return await super().complete(
            messages, model, tools, max_tokens, temperature, system
        )


class OpenAICompatProvider(ConfigurableOpenAIProvider):
    name = "openai_compat"
    cost_tier = CostTier.FREE
    env_prefix = "COMPAT"
    fallback_model = "local-model"

    def __init__(self, api_key=None, base_url=None, model=None):
        # Local gateways frequently do not authenticate, but require a value.
        super().__init__(api_key=api_key, base_url=base_url, model=model)

    @property
    def api_key(self):
        return self._explicit_key or self._setting("API_KEY") or "none"

    def is_configured(self):
        return bool(self.base_url)


class XAIProvider(ConfigurableOpenAIProvider):
    """xAI Grok API, which uses the OpenAI chat-completions wire contract."""

    name = "xai"
    cost_tier = CostTier.PREMIUM
    env_prefix = "XAI"
    config_provider = "xai"
    default_base_url = "https://api.x.ai/v1"
    fallback_model = "grok-3-mini"


class ApimixProvider(ConfigurableOpenAIProvider):
    """apmix.ai aggregator  -  one key across many upstream model vendors.

    apmix publishes an OpenAI-compatible Chat Completions surface at
    ``https://api.apmix.ai/v1`` and authenticates with a bearer key
    (``apx_live_…``), so the whole OpenAI protocol path is reused.  The base
    URL is still overridable because apmix may move endpoints, and pinning a
    default keeps an unconfigured deployment from silently reporting itself
    configured.
    """

    name = "apimix"
    cost_tier = CostTier.CHEAP
    env_prefix = "APIMIX"
    config_provider = "apimix"
    default_base_url = "https://api.apmix.ai/v1"
    #: apmix accepts the upstream vendor prefix; a cheap broadly-available id.
    fallback_model = "openai/gpt-5.6-luna"


class MetaLlamaProvider(ConfigurableOpenAIProvider):
    """Meta Llama API adapter.

    Meta's documented Llama API endpoint is used when configured.  A custom
    ``META_LLAMA_BASE_URL`` deliberately remains supported for hosted Llama,
    vLLM, Together, or future official endpoint migrations.
    """

    name = "meta_llama"
    cost_tier = CostTier.MEDIUM
    env_prefix = "META_LLAMA"
    config_provider = "meta_llama"
    default_base_url = "https://api.llama.com/compat/v1"
    fallback_model = "Llama-4-Maverick-17B-128E-Instruct-FP8"


class OpenRouterProvider(ConfigurableOpenAIProvider):
    """OpenRouter's OpenAI-compatible gateway with its own configuration."""

    name = "openrouter"
    cost_tier = CostTier.CHEAP
    env_prefix = "OPENROUTER"
    config_provider = "openrouter"
    default_base_url = "https://openrouter.ai/api/v1"
    fallback_model = "openai/gpt-4o-mini"

    @staticmethod
    def _model_unavailable(error: str | None) -> bool:
        """Recognize OpenRouter's model-specific 404 responses."""
        message = (error or "").lower()
        return any(
            marker in message
            for marker in ("no endpoints found", "model not found", "unknown model")
        )

    async def complete(
        self,
        messages,
        model=None,
        tools=None,
        max_tokens=1024,
        temperature=0.7,
        system=None,
    ):
        """Retry an unavailable configured model once with the safe default."""
        requested_model = model or self.default_model
        response = await super().complete(
            messages,
            model=requested_model,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
        )
        if (
            response.ok
            or requested_model == self.fallback_model
            or not self._model_unavailable(response.error)
        ):
            return response

        fallback = await super().complete(
            messages,
            model=self.fallback_model,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
        )
        if not fallback.ok:
            fallback.error = (
                f"Configured OpenRouter model {requested_model!r} has no available "
                f"endpoint; fallback {self.fallback_model!r} also failed: "
                f"{fallback.error}"
            )
        return fallback


class ProviderTemplate(ConfigurableOpenAIProvider):
    """Template for a new OpenAI-compatible provider.

    Subclass it, set ``name``, ``env_prefix``, ``default_base_url`` and
    ``fallback_model``. Configure ``<PREFIX>_API_KEY`` and
    ``<PREFIX>_BASE_URL``. No import-time SDK dependency or registry change is
    required until the provider should be a built-in option.
    """

    name = "template"
    env_prefix = "TEMPLATE"


# ── Cloudflare Workers AI
# Talks to the Workers AI REST API directly (no SDK): one endpoint per model,
#   POST https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}
# The same provider dispatches text-generation AND image-generation models  - 
# the model name decides which response shape to parse. Credentials
class CloudflareProvider(BaseProvider):
    name = "cloudflare"
    cost_tier = CostTier.CHEAP
    default_model = "@cf/meta/llama-3.1-8b-instruct"

    _IMAGE_MODEL_HINTS = (
        "stable-diffusion",
        "sdxl",
        "flux-1",
        "dreamshaper",
        "sd-v1-5",
        "real-esrgan",
        "img2img",
        "inpainting",
    )

    TEXT_MODELS = (
        "@cf/meta/llama-3.1-8b-instruct",
        "@cf/meta/llama-3.1-70b-instruct",
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "@cf/meta/llama-3-8b-instruct",
        "@hf/nous/hermes-2-pro-mistral-7b",
        "@cf/qwen/qwen1.5-14b-chat-awq",
    )
    IMAGE_MODELS = (
        "@cf/black-forest-labs/flux-1-schnell",
        "@cf/stabilityai/stable-diffusion-xl-base-1.0",
        "@cf/stabilityai/stable-diffusion-xl-lightning",
        "@cf/bytedance/stable-diffusion-xl-lightning",
        "@cf/lykon/dreamshaper-8-lcm",
    )

    def __init__(self):
        self._account = None
        self._token = None
        self._default_model = None

    # ── credentials (udB, lazily resolved like OpenAI)

    @property
    def account(self):
        if self._account is None:
            try:
                from pyUltroid import udB

                if udB:
                    self._account = udB.get_key("CF_ACCOUNT")
            except Exception:
                pass
        return self._account

    @property
    def token(self):
        if self._token is None:
            try:
                from pyUltroid import udB

                if udB:
                    self._token = udB.get_key("CF_TOKEN")
            except Exception:
                pass
        return self._token

    @property
    def default_model(self):
        if self._default_model is None:
            try:
                from pyUltroid import udB

                if udB:
                    self._default_model = udB.get_key("CF_MODEL")
            except Exception:
                pass
        return self._default_model or "@cf/meta/llama-3.1-8b-instruct"

    @default_model.setter
    def default_model(self, value):
        self._default_model = value

    def is_configured(self) -> bool:
        return bool(self.account and self.token)

    def _is_image_model(self, model: str) -> bool:
        model = (model or "").lower()
        return any(hint in model for hint in self._IMAGE_MODEL_HINTS)

    def model_capabilities(self, model=None):
        model = model or self.default_model
        if self._is_image_model(model):
            return {
                "max_tokens": None,
                "temperature": False,
                "tools": False,
                "system": False,
                "stream": False,
            }
        return {
            "max_tokens": "max_tokens",
            "temperature": True,
            "tools": False,  # not wired through the REST path
            "system": True,
            "stream": True,
        }

    def _endpoint(self, model: str) -> str:
        return (
            f"https://api.cloudflare.com/client/v4/accounts/"
            f"{self.account}/ai/run/{model}"
        )

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _cf_error(data: dict) -> str:
        errs = data.get("errors") or []
        if errs:
            first = errs[0] if isinstance(errs, list) else errs
            if isinstance(first, dict):
                return first.get("message") or str(first)
            return str(first)
        return data.get("message") or "Cloudflare AI request failed"

    # ── text generation
    async def _complete_text(
        self, messages, model, max_tokens, temperature, system, t0
    ) -> AIResponse:
        import aiohttp

        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs += [m.to_openai() for m in messages]
        payload = self._completion_params(model, max_tokens, temperature)
        payload["messages"] = msgs
        async with aiohttp.ClientSession() as s:
            async with s.post(
                self._endpoint(model),
                json=payload,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=120),
            ) as r:
                data = await r.json()
                if not data.get("success"):
                    return AIResponse(
                        "",
                        self.name,
                        model,
                        error=self._cf_error(data),
                        duration_ms=(time.time() - t0) * 1000,
                    )
                result = data.get("result", {}) or {}
                usage = result.get("usage", {}) or {}
                return AIResponse(
                    content=result.get("response", "") or "",
                    provider=self.name,
                    model=model,
                    tokens_in=usage.get("prompt_tokens", 0) or 0,
                    tokens_out=usage.get("completion_tokens", 0) or 0,
                    duration_ms=(time.time() - t0) * 1000,
                )

    # ── image generation
    async def _complete_image(
        self, messages, model, max_tokens, temperature, system, t0
    ) -> AIResponse:
        import aiohttp

        prompt = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        payload = {"prompt": prompt}
        async with aiohttp.ClientSession() as s:
            async with s.post(
                self._endpoint(model),
                json=payload,
                headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=120),
            ) as r:
                data = await r.json()
                if not data.get("success"):
                    return AIResponse(
                        "",
                        self.name,
                        model,
                        error=self._cf_error(data),
                        duration_ms=(time.time() - t0) * 1000,
                    )
                result = data.get("result", {}) or {}
                image_b64 = result.get("image")
                if not image_b64:
                    return AIResponse(
                        "",
                        self.name,
                        model,
                        error="No image in Cloudflare response",
                        duration_ms=(time.time() - t0) * 1000,
                    )
                return AIResponse(
                    content=f"[image generated via {model}]",
                    provider=self.name,
                    model=model,
                    image=image_b64,
                    duration_ms=(time.time() - t0) * 1000,
                )

    async def verify(self) -> bool:
        if not self.is_configured():
            return False
        try:
            import aiohttp

            url = f"https://api.cloudflare.com/client/v4/accounts/{self.account}"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    return response.status == 200
        except Exception:
            return False

    async def complete(
        self,
        messages,
        model=None,
        tools=None,
        max_tokens=1024,
        temperature=0.7,
        system=None,
    ) -> AIResponse:
        t0 = time.time()
        if not self.is_configured():
            return AIResponse(
                "",
                self.name,
                model or self.default_model,
                error="CF_ACCOUNT/CF_TOKEN not set",
            )
        model = model or self.default_model
        try:
            if self._is_image_model(model):
                return await self._complete_image(
                    messages, model, max_tokens, temperature, system, t0
                )
            return await self._complete_text(
                messages, model, max_tokens, temperature, system, t0
            )
        except Exception as e:
            return AIResponse(
                "",
                self.name,
                model,
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )

    async def stream(
        self, messages, model=None, max_tokens=1024, temperature=0.7, system=None
    ) -> AsyncGenerator[str]:
        if not self.is_configured():
            yield "[Cloudflare not configured]"
            return
        model = model or self.default_model
        if self._is_image_model(model):
            yield "[Cloudflare image models do not support streaming]"
            return
        try:
            import aiohttp, json as _json

            msgs = []
            if system:
                msgs.append({"role": "system", "content": system})
            msgs += [m.to_openai() for m in messages]
            payload = self._completion_params(model, max_tokens, temperature)
            payload["messages"] = msgs
            payload["stream"] = True
            async with aiohttp.ClientSession() as s:
                async with s.post(
                    self._endpoint(model),
                    json=payload,
                    headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=120),
                ) as r:
                    async for line in r.content:
                        line = line.strip()
                        if not line or not line.startswith(b"data:"):
                            continue
                        chunk = line[5:].strip()
                        if chunk == b"[DONE]":
                            break
                        try:
                            obj = _json.loads(chunk)
                            token = obj.get("response", "")
                            if token:
                                yield token
                        except Exception:
                            continue
        except Exception as e:
            yield f"[Cloudflare stream error: {e}]"


# ── Provider registry
class ProviderRegistry:
    """
    Holds all provider instances. Exposes configured providers
    in priority order: FREE → CHEAP → MEDIUM → PREMIUM.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self._all: list[BaseProvider] = [
            OllamaProvider(),
            OpenAICompatProvider(),
            GroqProvider(),
            GeminiProvider(),
            ApimixProvider(),
            OpenRouterProvider(),
            OpenAIProvider(),
            AnthropicProvider(),
        ]
        cf = CloudflareProvider()
        if cf.is_configured():
            self._all.append(cf)

    def provider_register(self, provider: BaseProvider) -> None:
        if provider is None:
            return
        if not any(p.name == provider.name for p in self._all):
            self._all.append(provider)

    def providers_configured(self) -> list[BaseProvider]:
        return [p for p in self._all if p.is_configured()]

    def provider_by_name(self, name: str) -> BaseProvider | None:
        return next((p for p in self._all if p.name == name), None)

    def providers_by_tier(self, tier: CostTier) -> list[BaseProvider]:
        return [p for p in self.providers_configured() if p.cost_tier == tier]

    def provider_cheapest(self) -> BaseProvider | None:
        for tier in CostTier:
            providers = self.providers_by_tier(tier)
            if providers:
                return providers[0]
        return None

    def provider_health(self) -> dict[str, bool]:
        return {p.name: p.is_configured() for p in self._all}

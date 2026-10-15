"""Public, provider-neutral AI client facade.

The client deliberately delegates protocol details to ``_providers``.  It is
safe to import in a minimal bot installation because provider SDK imports stay
inside the adapter methods that actually use them.
"""

from __future__ import annotations

import inspect

from typing import Any, Dict, List, Optional, Union
from collections.abc import Iterable

from ._providers import (
    AIMessage,
    AIResponse,
    AnthropicProvider,
    ApimixProvider,
    GeminiProvider,
    GroqProvider,
    MetaLlamaProvider,
    OpenRouterProvider,
    OllamaProvider,
    OpenAICompatProvider,
    OpenAIProvider,
    XAIProvider,
)

MessageInput = Union[str, AIMessage, dict[str, Any]]


class AIClient:
    """Small uniform client for built-in and custom AI providers.

    ``provider`` accepts a registered provider name, a :class:`BaseProvider`
    instance, or ``"custom"``/``"openai_compat"`` for any OpenAI-compatible
    service.  Explicit ``key``, ``base_url`` and ``model`` values always win
    over saved configuration, which makes it suitable for one-off clients.
    """

    _PROVIDERS = {
        "openai": OpenAIProvider,
        "gemini": GeminiProvider,
        "google": GeminiProvider,
        "groq": GroqProvider,
        "anthropic": AnthropicProvider,
        "claude": AnthropicProvider,
        "ollama": OllamaProvider,
        "xai": XAIProvider,
        "grok": XAIProvider,
        "apimix": ApimixProvider,
        "apmix": ApimixProvider,
        "meta": MetaLlamaProvider,
        "meta_llama": MetaLlamaProvider,
        "llama": MetaLlamaProvider,
        "openrouter": OpenRouterProvider,
        "openai_compat": OpenAICompatProvider,
        "compat": OpenAICompatProvider,
        "custom": OpenAICompatProvider,
    }

    def __init__(
        self,
        provider="openai",
        base_url=None,
        model=None,
        use_tools=True,
        key=None,
        *args,
        **kwargs,
    ):
        # ``api_key`` is a conventional alias while ``key`` is the documented
        # concise argument. Keep both for callers migrating from SDK clients.
        key = key if key is not None else kwargs.pop("api_key", None)
        self.provider_name = (
            provider.name if hasattr(provider, "name") else str(provider).lower()
        )
        self.base_url = base_url
        self.model = model
        self.use_tools = use_tools
        self.key = key
        self.options = dict(kwargs)
        self._provider = self._resolve_provider(
            provider, base_url, model, key, *args, **kwargs
        )
        self.provider = self.provider_name

    @classmethod
    def _resolve_provider(cls, provider, base_url, model, key, *args, **kwargs):
        if hasattr(provider, "complete"):
            return provider
        name = str(provider).lower().replace("-", "_")
        provider_cls = cls._PROVIDERS.get(name)
        if provider_cls is None:
            known = ", ".join(sorted(cls._PROVIDERS))
            raise ValueError(
                f"Unknown AI provider {provider!r}; expected one of: {known}"
            )
        # Only OpenAI-protocol classes take these generic overrides.  Native
        # SDK providers retain their config behaviour unless an SDK-specific
        # caller supplies a provider instance.
        if issubclass(
            provider_cls,
            (
                OpenAIProvider,
                OpenAICompatProvider,
                XAIProvider,
                ApimixProvider,
                MetaLlamaProvider,
            ),
        ):
            return provider_cls(api_key=key, base_url=base_url, model=model)
        instance = provider_cls(*args, **kwargs)
        if key is not None:
            instance.api_key = key
        if model is not None:
            instance.default_model = model
        if base_url is not None and hasattr(instance, "base_url"):
            instance.base_url = base_url
        return instance

    @staticmethod
    def _messages(
        messages: MessageInput | Iterable[MessageInput],
    ) -> list[AIMessage]:
        if isinstance(messages, (str, AIMessage, dict)):
            messages = [messages]
        normalized = []
        for message in messages:
            if isinstance(message, AIMessage):
                # Never attach request-only state (such as modalities) to the
                # caller's history object. Preserve structured blocks by copy.
                normalized.append(
                    AIMessage(
                        role=message.role,
                        content=message.content,
                        name=message.name,
                        parts=list(message.parts),
                        tool_calls=[dict(call) for call in message.tool_calls],
                        tool_call_id=message.tool_call_id,
                        tool_result=message.tool_result,
                        reasoning=message.reasoning,
                        thinking=message.thinking,
                        modalities=list(message.modalities),
                        metadata=dict(message.metadata),
                    )
                )
            elif isinstance(message, str):
                normalized.append(AIMessage(role="user", content=message))
            elif isinstance(message, dict):
                normalized.append(AIMessage(**dict(message)))
            else:
                raise TypeError(
                    "messages must be strings, AIMessage values, or dictionaries"
                )
        if not normalized:
            raise ValueError("messages must contain at least one message")
        return normalized

    async def _complete(self, messages, **kwargs) -> AIResponse:
        """Call the adapter once, filtering controls its signature cannot accept."""
        complete = self._provider.complete
        try:
            parameters = inspect.signature(complete).parameters.values()
        except (TypeError, ValueError):
            # Some callable extension adapters are not introspectable. They
            # receive the full normalized contract exactly once.
            return await complete(messages, **kwargs)
        if not any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters
        ):
            accepted = {
                parameter.name
                for parameter in parameters
                if parameter.kind
                in (
                    inspect.Parameter.POSITIONAL_OR_KEYWORD,
                    inspect.Parameter.KEYWORD_ONLY,
                )
            }
            kwargs = {name: value for name, value in kwargs.items() if name in accepted}
        return await complete(messages, **kwargs)

    async def verify(self) -> bool:
        """Perform the adapter's read-only authentication/model-list check."""
        return await self._provider.verify()

    async def chat(
        self,
        messages,
        *,
        tools=None,
        system=None,
        model=None,
        max_tokens=1024,
        temperature=0.7,
        **kwargs,
    ) -> AIResponse:
        """Generate one normalized response from messages or a prompt string."""
        selected_tools = tools if self.use_tools else None
        if selected_tools is None and self.use_tools:
            selected_tools = kwargs.pop("tool_schemas", None)
        response_modalities = kwargs.pop("response_modalities", None)
        # ``modalities`` was briefly accepted by early client callers. It maps
        # only to Gemini's response field, never OpenAI Chat Completions.
        response_modalities = response_modalities or kwargs.pop("modalities", None)
        normalized = self._messages(messages)
        controls = {
            "model": model or self.model,
            "tools": selected_tools,
            "system": system,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "reasoning": kwargs.pop("reasoning", None),
            "reasoning_effort": kwargs.pop("reasoning_effort", None),
            "tool_call": kwargs.pop("tool_call", None),
        }
        if self.provider_name in ("gemini", "google") and response_modalities:
            controls["response_modalities"] = [
                str(modality).upper() for modality in response_modalities
            ]
        controls.update(kwargs)
        return await self._complete(
            normalized,
            **{key: value for key, value in controls.items() if value is not None},
        )

    async def complete(self, messages, **kwargs) -> AIResponse:
        """Compatibility alias for :meth:`chat`."""
        return await self.chat(messages, **kwargs)

    async def stream(
        self, messages, *, system=None, model=None, max_tokens=1024, temperature=0.7
    ):
        """Yield text chunks from the selected provider."""
        async for chunk in self._provider.stream(
            self._messages(messages),
            model=model or self.model,
            system=system,
            max_tokens=max_tokens,
            temperature=temperature,
        ):
            yield chunk

    def providers(self) -> dict[str, Any]:
        """Return built-in provider instances by their public names."""
        from ._providers import ProviderRegistry

        known = {p.name: p for p in ProviderRegistry()._all}
        known.setdefault("xai", XAIProvider())
        known.setdefault("meta", MetaLlamaProvider())
        known.setdefault("meta_llama", known["meta"])
        known.setdefault("apimix", ApimixProvider())
        return known

    async def search(self, query, **kwargs) -> AIResponse:
        """Ask a search-capable model to research a query.

        Providers without a native search tool still receive an explicit
        research request rather than silently claiming that a web lookup ran.
        Supply a provider-native search tool through ``tools`` when available.
        """
        system = kwargs.pop(
            "system",
            "Use available search tools when present. Cite uncertainty clearly.",
        )
        return await self.chat(f"Research this query: {query}", system=system, **kwargs)

    async def agent(self, messages, **kwargs) -> AIResponse:
        """Run an agent-ready completion with tool calling enabled.

        Tool execution remains application-owned; returned calls include their
        provider IDs so callers can append matching ``AIMessage(role='tool',
        tool_call_id=...)`` results on the following turn.
        """
        previous = self.use_tools
        self.use_tools = True
        try:
            return await self.chat(messages, **kwargs)
        finally:
            self.use_tools = previous

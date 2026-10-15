"""MiniAI  -  the lightweight layer that sits in front of the full LLM."""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

from pyUltroid.startup.logger import LOGS
from ._providers import AIMessage, AIResponse, CostTier, ProviderRegistry


# ── Intent
class Intent(Enum):
    GREETING = auto()  # hi, hello, hey
    SIMPLE_FACT = auto()  # what is X, define X
    CODE_HELP = auto()  # write code, fix this, debug
    IMAGE_REQUEST = auto()  # generate image, draw, make art
    SHELL_REQUEST = auto()  # run command, execute, terminal
    UPDATE_REQUEST = auto()  # update bot, check updates
    SEARCH = auto()  # search for, find info about
    CREATIVE = auto()  # write a poem, story, caption
    ANALYSIS = auto()  # analyse this, explain, summarise
    SYSTEM = auto()  # bot status, stats, trace
    UNKNOWN = auto()


# ── Intent classifier
_INTENT_PATTERNS: list[tuple[Intent, list[str]]] = [
    (Intent.GREETING, [r"^(hi|hello|hey|sup|yo|howdy|greetings)\b"]),
    (
        Intent.CODE_HELP,
        [
            r"\b(code|debug|fix|error|function|script|python|bash|class|import)\b",
            r"```",
            r"def |class |import ",
        ],
    ),
    (
        Intent.IMAGE_REQUEST,
        [r"\b(image|picture|photo|draw|paint|generate.*image|art|meme|sticker)\b"],
    ),
    (Intent.SHELL_REQUEST, [r"\b(run|execute|terminal|shell|command|bash|sh |sudo)\b"]),
    (Intent.UPDATE_REQUEST, [r"\b(update|upgrade|pull|latest|new version)\b"]),
    (
        Intent.SEARCH,
        [r"\b(search|find|look up|google|what is|who is|when did|where is)\b"],
    ),
    (Intent.CREATIVE, [r"\b(write|poem|story|caption|lyrics|compose|create a)\b"]),
    (
        Intent.ANALYSIS,
        [r"\b(analyse|analyze|explain|summarise|summarize|describe|review)\b"],
    ),
    (Intent.SYSTEM, [r"\b(status|stats|trace|uptime|plugins|version|bot info)\b"]),
    (Intent.SIMPLE_FACT, [r"\b(what|who|when|where|how many|define|meaning of)\b"]),
]

_BUILTIN_RESPONSES: dict[Intent, str] = {
    Intent.GREETING: "Hey! I'm Ultroid  -  fully operational. What do you need?",
}


def classify_intent(text: str) -> Intent:
    text_lower = text.lower().strip()
    for intent, patterns in _INTENT_PATTERNS:
        for p in patterns:
            if re.search(p, text_lower):
                return intent
    return Intent.UNKNOWN


def _cache_key(
    messages: list[AIMessage], system: str = "", *, scope: str = ""
) -> str:
    content = (
        f"{scope}|{system}|"
        + "|".join(f"{m.role}:{m.content}" for m in messages[-3:])
    )
    return "BRAIN_CACHE_" + hashlib.md5(content.encode()).hexdigest()


# ── MiniAI
class MiniAI:
    """
    Lightweight AI router. Import `mini` from pyUltroid.agent.

    Sits in front of the full agent  -  handles cheap/simple requests
    without touching premium APIs.

    Usage:
        from pyUltroid.agent import mini

        response = await mini.mini_respond(messages, system=system_prompt)
        # response is an AIResponse  -  always has .content
    """

    CACHE_TTL = 3600  # seconds  -  cache responses for 1 hour
    CACHE_MAX_LEN = 500  # max chars of response to cache
    CACHE_KEY_PFX = "BRAIN_CACHE_"  # keep in sync with _cache_key()

    def __init__(self, provider=None, client=None, **kw):
        super().__init__(**kw)
        self._in_flight: dict[str, asyncio.Event] = {}
        self._in_flight_results: dict[str, AIResponse] = {}
        self._provider = provider if provider is not None else client

    def mini_set_provider(self, provider=None, client=None) -> None:
        """Use *provider*/*client* for requests, or restore registry routing."""
        self._provider = provider if provider is not None else client

    @property
    def db(self):
        """The live database. Absent before startup has built it."""
        try:
            from pyUltroid import udB
        except Exception:
            return None
        return udB

    # ── Cache
    def _cache_get(self, key: str) -> str | None:
        db = self.db
        if db is None:
            return None
        try:
            record = db.get_key(key)
        except Exception:
            return None
        if not isinstance(record, dict):
            return None
        if record.get("exp", 0) < time.time():
            try:
                db.del_key(key)
            except Exception:
                pass
            return None
        value = record.get("v")
        return value if isinstance(value, str) else None

    def _cache_set(self, key: str, value: str) -> None:
        if len(value) > self.CACHE_MAX_LEN:
            return  # don't cache long creative responses
        db = self.db
        if db is None:
            return
        try:
            db.set_key(key, {"exp": time.time() + self.CACHE_TTL, "v": value})
        except Exception:
            pass

    # ── Core respond
    async def mini_respond(
        self,
        messages: list[AIMessage],
        system: str = "",
        force_provider: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        use_cache: bool = True,
        tools: list | None = None,
        tool_call: object | None = None,
        model: str | None = None,
        scope: str = "",
    ) -> AIResponse:
        """
        Route a conversation to the best available provider.
        Returns AIResponse  -  always populated, never raises.
        """
        import asyncio

        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )

        # A tool-result continuation must always reach the selected provider.
        # ``tool_call`` accepts a native call/result object for SDK clients that
        # keep that state outside the normalized message list.
        if tool_call is not None:
            use_cache = False

        # A pinned/forced provider must not be bypassed by a zero-cost
        # classifier shortcut; selection is part of the requested contract.
        if force_provider:
            p = ProviderRegistry().provider_by_name(force_provider)
            if p is None:
                return AIResponse(
                    content="",
                    provider=force_provider,
                    model="none",
                    error=f"Unknown provider `{force_provider}`.",
                )
            if not p.is_configured():
                return AIResponse(
                    content="",
                    provider=force_provider,
                    model="none",
                    error=f"Provider `{force_provider}` is not configured.",
                )
            return await self._complete(
                p,
                messages,
                tools,
                tool_call,
                model,
                max_tokens,
                temperature,
                system,
            )

        # 1. Built-in pattern response (zero cost)
        intent = classify_intent(last_user)
        if intent in _BUILTIN_RESPONSES and not tools and tool_call is None:
            return AIResponse(
                content=_BUILTIN_RESPONSES[intent],
                provider="builtin",
                model="pattern",
            )

        # 2. Cache check
        cache_key = _cache_key(messages, system, scope=scope)
        if use_cache and not tools and tool_call is None:
            cached = self._cache_get(cache_key)
            if cached:
                LOGS.debug(f"[BRAIN:MINI] Cache hit for {cache_key[-8:]}")
                return AIResponse(
                    content=cached,
                    provider="cache",
                    model="udb",
                )

        # 3. Dedup concurrent identical requests
        if cache_key in self._in_flight:
            LOGS.debug(f"[BRAIN:MINI] Waiting for in-flight request {cache_key[-8:]}")
            await self._in_flight[cache_key].wait()
            result = self._in_flight_results.get(cache_key)
            if result:
                return result

        evt = asyncio.Event()
        self._in_flight[cache_key] = evt

        try:
            response = await self._route(
                messages=messages,
                system=system,
                intent=intent,
                force_provider=force_provider,
                max_tokens=max_tokens,
                temperature=temperature,
                tools=tools,
                tool_call=tool_call,
                model=model,
            )

            # Cache only ordinary text exchanges; tool state is provider-native.
            if response.ok and use_cache and not tools and tool_call is None:
                self._cache_set(cache_key, response.content)

            self._in_flight_results[cache_key] = response
            return response

        finally:
            evt.set()
            self._in_flight.pop(cache_key, None)

            # Clean result after small delay so waiters can read it
            async def _cleanup():
                await asyncio.sleep(1)
                self._in_flight_results.pop(cache_key, None)

            asyncio.ensure_future(_cleanup())

    async def _route(
        self,
        messages: list[AIMessage],
        system: str,
        intent: Intent,
        force_provider: str | None,
        max_tokens: int,
        temperature: float,
        tools: list | None,
        tool_call: object | None = None,
        model: str | None = None,
    ) -> AIResponse:
        """
        Select provider and call it.
        Priority: forced → local → cheap → medium → premium
        """

        # An injected provider/client takes precedence over registry routing.
        # It intentionally need not implement is_configured(), which keeps
        # small SDK adapters and test clients straightforward.
        if self._provider is not None:
            return await self._complete(
                self._provider,
                messages,
                tools,
                tool_call,
                model,
                max_tokens,
                temperature,
                system,
            )

        # Determine target tier from intent
        target_tier = self._intent_to_tier(intent, tools)
        attempt_order = self._build_order(target_tier, allow_local=not tools)

        for provider in attempt_order:
            LOGS.debug(f"[BRAIN:MINI] Trying {provider.name}…")
            response = await self._complete(
                provider,
                messages,
                tools,
                tool_call,
                model,
                max_tokens,
                temperature,
                system,
            )
            if response.ok:
                LOGS.debug(
                    f"[BRAIN:MINI] ✅ {provider.name}/{response.model} "
                    f"({response.duration_ms:.0f}ms)"
                )
                return response
            LOGS.warning(f"[BRAIN:MINI] ❌ {provider.name}: {response.error}")

        # All failed
        return AIResponse(
            content="",
            provider="none",
            model="none",
            error="All providers failed or unconfigured.",
        )

    @staticmethod
    def _accepted_kwargs(method, kwargs):
        """Filter kwargs before invocation without retrying a provider call.

        Providers predating ``tool_call`` receive one request without that
        unsupported argument. Compatible providers advertise it in their
        signature (or accept ``**kwargs``) and receive it explicitly.
        """
        try:
            parameters = inspect.signature(method).parameters.values()
        except (TypeError, ValueError):
            # Some extension-backed callables cannot be inspected; preserve the
            # full SDK-facing contract rather than issuing a speculative retry.
            return kwargs
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters):
            return kwargs
        accepted = {
            p.name
            for p in parameters
            if p.kind
            in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
        }
        return {key: value for key, value in kwargs.items() if key in accepted}

    @classmethod
    async def _complete(
        cls,
        provider,
        messages,
        tools,
        tool_call,
        model,
        max_tokens,
        temperature,
        system,
    ) -> AIResponse:
        """Call a normalized provider or compatible SDK adapter exactly once."""
        kwargs = {
            "tools": tools,
            "tool_call": tool_call,
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
        }
        complete = getattr(provider, "complete", None) or getattr(
            provider, "respond", None
        )
        if complete is None:
            raise TypeError("Injected provider must define complete() or respond().")
        return await complete(messages, **cls._accepted_kwargs(complete, kwargs))

    def _intent_to_tier(self, intent: Intent, tools) -> CostTier:
        """Map intent to minimum cost tier needed."""
        if tools:
            return CostTier.MEDIUM  # tool calls need capable models
        if intent in (Intent.GREETING, Intent.SIMPLE_FACT):
            return CostTier.FREE
        if intent in (Intent.CODE_HELP, Intent.ANALYSIS, Intent.CREATIVE):
            return CostTier.MEDIUM
        return CostTier.CHEAP

    def _build_order(self, min_tier: CostTier, allow_local: bool = True) -> list:
        """
        Build ordered list of providers to try.

        With ``allow_local`` the configured FREE-tier providers (Ollama, then
        any OpenAI-compatible local endpoint) come FIRST regardless of
        ``min_tier``  -  the local model is the default path and cloud tiers are
        the escalation, not the other way around. Tool-call requests pass
        ``allow_local=False`` because Ollama ignores the tool schema.
        """
        ordered = []
        if allow_local:
            ordered.extend(ProviderRegistry().providers_by_tier(CostTier.FREE))
        for tier in CostTier:
            if tier.value >= min_tier.value:
                ordered.extend(ProviderRegistry().providers_by_tier(tier))
        # Dedup by provider name, preserving order (FREE providers reappear in
        # the tier sweep when min_tier == FREE).
        seen, unique = set(), []
        for provider in ordered:
            if provider.name not in seen:
                seen.add(provider.name)
                unique.append(provider)
        return unique

    # ── Convenience: single prompt ─────────────────────────────────────────

    async def mini_ask(
        self,
        prompt: str,
        system: str = "",
        provider: str | None = None,
        tools: list | None = None,
        tool_call: object | None = None,
        model: str | None = None,
    ) -> str:
        """Single-turn prompt returning text while retaining tool-call support."""
        messages = [AIMessage(role="user", content=prompt)]
        resp = await self.mini_respond(
            messages,
            system=system,
            force_provider=provider,
            tools=tools,
            tool_call=tool_call,
            model=model,
        )
        return resp.content or f"[Error: {resp.error}]"

    # ── Stats
    def mini_provider_status(self) -> dict[str, bool]:
        return ProviderRegistry().provider_health()

    async def mini_cache_size(self) -> int:
        db = self.db
        if db is None:
            return 0
        try:
            return sum(
                1
                for k in db.keys()
                if isinstance(k, str) and k.startswith(self.CACHE_KEY_PFX)
            )
        except Exception:
            return 0

    async def mini_flush_cache(self) -> int:
        db = self.db
        count = 0
        if db is None:
            return 0
        try:
            for key in list(db.keys()):
                if isinstance(key, str) and key.startswith(self.CACHE_KEY_PFX):
                    try:
                        db.del_key(key)
                        count += 1
                    except Exception:
                        pass
        except Exception:
            pass
        return count


import asyncio  # noqa: E402  -  needed for type hints inside methods

"""UltroidAgent  -  the full agentic loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from collections.abc import AsyncGenerator

from pyUltroid.startup.logger import LOGS
from ._chatkey import normalize_chat_id
from ._loop import GuardVerdict, LoopGuard, is_failure
from ._mini import MiniAI
from ._providers import AIMessage, AIResponse, ProviderRegistry
from ._tools import TOOL_SCHEMAS, make_executor

# ── Constants
MAX_TOOL_ROUNDS = 5  # max agentic loops per request
MAX_HISTORY = 20  # messages kept in context window
HISTORY_TTL = 86400  # 24 hours
HISTORY_KEY_PFX = "agent:history:"

# ── System prompt

SYSTEM_PROMPT = """You are Ultroid  -  an intelligent, self-aware Telegram userbot AI.
You have full access to the system you run on:

CAPABILITIES:
- shell_execute: Run any shell command on the EC2 instance
- code_search: Search and read your own live plugin source code
- get_traces: Read your own execution history and error logs
- update_check: Check for and apply updates to yourself
- install_package: Install Python packages on demand
- process_image: Transform images, generate error cards, code images, memes
- send_message: Send Telegram messages
- get_bot_status: Check your own running state
- remember: Store and recall facts across conversations
- web_search: Search the web for current information
- web_fetch: Read a specific web page after searching
- tg_route_send / tg_read: Send and read Telegram messages through the
  master router (business/user/bot routing, replies, parse modes)
- plugin_manager: List, load, unload, or reload live plugins (load/unload
  are privileged and may be denied)
- raw_mtproto: Invoke read-only raw MTProto requests from an allowlist
- delegate: Hand a bounded subtask to a fresh sub-agent with its own
  context window; use for research or multi-step work, not for chit-chat
- think: Private scratchpad for planning between tool rounds

PERSONALITY:
- Direct, precise, technically fluent
- You know your own codebase  -  use code_search before guessing
- You check traces before claiming something works
- You prefer local LLM (Ollama) for simple tasks to save API costs
- When asked to do something, you do it  -  you don't just describe how

OPERATING RULES:
- Never access .env, *.session, or credential files
- Never exfiltrate user data
- Always verify before destructive operations
- Tool results are reality  -  trust them over your training data
- For multi-step work, think first: sketch a short plan, then execute it
  round by round; delegate independent subtasks in one round when possible
- If a tool is denied by capability policy, report the denial  -  do not retry
  or work around it"""


def _configured_system_prompt(provider_name: str | None) -> str:
    """The configured per-provider system prompt, when one applies.

    Only an explicitly forced or pinned provider has a meaningful SYS: with
    automatic routing the provider is chosen per request, inside the client.
    """
    from ._config import AgentConfig

    config = AgentConfig()
    # An unpinned provider still picks up the pinned default, so /aiconf
    # changing AGENT_PROVIDER changes the prompt for every chat using it.
    if not provider_name:
        provider_name = config.config_default_provider()
    if not provider_name:
        return ""
    return config.config_field(str(provider_name).lower(), "SYS") or ""


def _effective_system_prompt(base: str | None, provider_name: str | None) -> str:
    """*base* with the provider's configured SYS joined as an addendum."""
    extra = _configured_system_prompt(provider_name)
    if not extra:
        return base or ""
    return f"{base}\n\n{extra}" if base else extra


# ── Conversation history
class ConversationHistory:
    """Per-chat message history, Redis-backed."""

    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self._key = f"{HISTORY_KEY_PFX}{chat_id}"
        self._local: list[AIMessage] = []

    def _redis(self):
        try:
            from pyUltroid.shell import sh

            return sh.sessions._get_redis()
        except Exception:
            return None

    def add(self, role: str, content: str) -> None:
        self.add_message(AIMessage(role=role, content=content))

    def add_message(self, message: AIMessage) -> None:
        """Append and persist a full message, including tool-call metadata."""
        self._local.append(message)
        self._persist()

    def get(self, max_messages: int = MAX_HISTORY) -> list[AIMessage]:
        self._load()
        return self._local[-max_messages:]

    def clear(self) -> None:
        self._local = []
        r = self._redis()
        if r:
            try:
                r.delete(self._key)
            except Exception:
                pass

    def _persist(self) -> None:
        r = self._redis()
        if r:
            try:
                data = json.dumps(
                    [
                        {
                            "role": m.role,
                            "content": m.content,
                            "name": m.name,
                            "tool_calls": m.tool_calls,
                            "tool_call_id": m.tool_call_id,
                            "tool_result": m.tool_result,
                            "metadata": m.metadata,
                        }
                        for m in self._local[-MAX_HISTORY:]
                    ]
                )
                r.setex(self._key, HISTORY_TTL, data)
            except Exception:
                pass

    def _load(self) -> None:
        if self._local:
            return
        r = self._redis()
        if r:
            try:
                raw = r.get(self._key)
                if raw:
                    self._local = [
                        AIMessage(
                            role=d.get("role", "user"),
                            content=d.get("content", ""),
                            name=d.get("name"),
                            tool_calls=d.get("tool_calls"),
                            tool_call_id=d.get("tool_call_id"),
                            tool_result=d.get("tool_result"),
                            metadata=d.get("metadata") or {},
                        )
                        for d in json.loads(raw)
                    ]
            except Exception:
                pass


# ── AgentResponse
@dataclass
class AgentResponse:
    content: str
    provider: str
    model: str
    tool_rounds: int = 0
    tools_used: list[str] = field(default_factory=list)
    duration_ms: float = 0.0
    error: str | None = None
    image_result: object | None = None  # set if process_image was called
    reasoning: str | None = None
    thinking: str | None = None
    modalities: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None

    def __str__(self) -> str:
        return self.content


# ── UltroidAgent
class UltroidAgent:
    """
    The full agentic brain of Ultroid.
    One instance per bot  -  histories are per-chat.

    Usage:
        from pyUltroid.agent import agent

        # Single response
        response = await agent.respond("What plugins are loaded?", chat_id=123)

        # Streaming
        async for chunk in agent.stream("write me a haiku", chat_id=123):
            print(chunk)
    """

    def __init__(self, mini_ai: MiniAI | None = None, provider=None, client=None, **kw):
        super().__init__(**kw)
        self._histories: dict[int, ConversationHistory] = {}
        self._history_locks: dict[int, asyncio.Lock] = {}
        self._chat_systems: Dict[str, str] = {}
        self._system = SYSTEM_PROMPT
        configured = provider if provider is not None else client
        # Never mutate the module singleton when this instance receives an
        # explicit provider/client: separate agents may run concurrently.
        self._mini = mini_ai or MiniAI(configured)
        if configured is not None and mini_ai is not None:
            self._mini = MiniAI(configured)

    def set_provider(self, provider=None, client=None) -> None:
        """Set an instance-local MiniAI provider/client."""
        configured = provider if provider is not None else client
        self._mini.mini_set_provider(configured)

    @property
    def mini(self) -> MiniAI:
        """The injected or default MiniAI routing facade."""
        return self._mini

    # ── History management
    def history(self, chat_id: int) -> ConversationHistory:
        if chat_id not in self._histories:
            self._histories[chat_id] = ConversationHistory(chat_id)
        return self._histories[chat_id]

    def clear_history(self, chat_id: int) -> None:
        # Always construct the history so a persisted-only chat is cleared too.
        self.history(chat_id).clear()

    @staticmethod
    def _tool_call_id(tool_call: dict, round_num: int, position: int) -> str:
        """Preserve native IDs or make a stable ID for providers that omit one."""
        if tool_call.get("id"):
            return str(tool_call["id"])
        payload = json.dumps(
            {
                "name": tool_call.get("name", ""),
                "arguments": tool_call.get("arguments", {}),
                "round": round_num,
                "position": position,
            },
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )
        return "call_" + hashlib.sha256(payload.encode()).hexdigest()[:16]

    # ── Core respond
    async def respond(
        self,
        user_message: str,
        chat_id: int = 0,
        provider_name: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        use_tools: bool = True,
        use_history: bool = True,
        use_cache: bool = True,
        model: str | None = None,
        tool_call: object | None = None,
        max_rounds: int | None = None,
        scope: str = "",
        caps: frozenset[str] | None = None,
        _depth: int = 0,
    ) -> AgentResponse:
        """
        Full agentic loop. Returns AgentResponse when done.

        *max_rounds* caps the tool loop for this call only; ``None`` keeps the
        module default. It exists so a chat can hold the agent on a shorter
        leash (``.airounds``) without changing the global default.
        """
        locks = getattr(self, "_history_locks", None)
        if locks is None:
            locks = self._history_locks = {}
        lock = locks.setdefault(chat_id, asyncio.Lock())
        async with lock:
            return await self._respond_locked(
                user_message=user_message,
                chat_id=chat_id,
                provider_name=provider_name,
                max_tokens=max_tokens,
                temperature=temperature,
                use_tools=use_tools,
                use_history=use_history,
                use_cache=use_cache,
                model=model,
                tool_call=tool_call,
            max_rounds=max_rounds,
            scope=scope,
            caps=caps,
            _depth=_depth,
        )

    async def _respond_locked(
        self,
        *,
        user_message: str,
        chat_id: int,
        provider_name: str | None,
        max_tokens: int,
        temperature: float,
        use_tools: bool,
        use_history: bool,
        use_cache: bool,
        model: str | None,
        tool_call: object | None,
        max_rounds: int | None,
        scope: str,
        caps: frozenset[str] | None = None,
        _depth: int = 0,
    ) -> AgentResponse:
        t0 = time.time()
        hist = self.history(chat_id)
        # Direct callers get the legacy default grant; the runtime passes an
        # explicit, surface-aware policy through ``caps``.
        executor = make_executor(chat_id, caps=caps, depth=_depth)
        guard = LoopGuard()
        tools_used = []
        tool_rounds = 0
        image_result = None
        pending_tool_call = tool_call
        response_reasoning = None
        response_thinking = None
        response_modalities = []

        # Add user message to history
        if use_history:
            hist.add("user", user_message)

        # Build message list
        messages = (
            hist.get()
            if use_history
            else [AIMessage(role="user", content=user_message)]
        )

        tools = TOOL_SCHEMAS if use_tools else None

        # The menu stores a per-provider system prompt (AGENT_*_SYS); it
        # joins the agent prompt as an addendum rather than replacing it.
        system = _effective_system_prompt(self._system, provider_name)
        chat_prompt = self.chat_system(chat_id)
        if chat_prompt:
            system = f"{system}\n\nChat instructions:\n{chat_prompt}"
        from pyUltroid.agent._skills import render_skills

        skill_block = render_skills(scope.split(":", 1)[0] or None)
        if skill_block:
            system = f"{system}\n\n{skill_block}"

        # ── Agentic loop
        rounds_cap = max_rounds if max_rounds is not None else MAX_TOOL_ROUNDS
        rounds_cap = max(0, min(rounds_cap, MAX_TOOL_ROUNDS))
        for round_num in range(rounds_cap + 1):
            response = await self._mini.mini_respond(
                messages=messages,
                system=system,
                force_provider=provider_name,
                max_tokens=max_tokens,
                temperature=temperature,
                use_cache=use_cache and not use_tools,
                tools=tools,
                tool_call=pending_tool_call,
                model=model,
                scope=f"{chat_id}:{provider_name or 'auto'}:{model or 'default'}:{scope}",
            )

            response_reasoning = response.reasoning or response_reasoning
            response_thinking = response.thinking or response_thinking
            response_modalities = list(response.modalities or response_modalities)

            if not response.ok:
                return AgentResponse(
                    content=f"⚠️ {response.error}",
                    provider=response.provider,
                    model=response.model,
                    tool_rounds=tool_rounds,
                    tools_used=tools_used,
                    duration_ms=(time.time() - t0) * 1000,
                    error=response.error,
                    reasoning=response_reasoning,
                    thinking=response_thinking,
                    modalities=response_modalities,
                )

            # No tool calls  -  we have the final answer
            if not response.tool_calls:
                if use_history:
                    hist.add("assistant", response.content)
                return AgentResponse(
                    content=response.content,
                    provider=response.provider,
                    model=response.model,
                    tool_rounds=tool_rounds,
                    tools_used=tools_used,
                    duration_ms=(time.time() - t0) * 1000,
                    image_result=image_result,
                    reasoning=response_reasoning,
                    thinking=response_thinking,
                    modalities=response_modalities,
                )

            # ── Execute tool calls
            tool_rounds += 1
            pending_tool_call = None

            # Normalize omitted IDs before serializing the assistant call
            # message; both sides of the exchange must use the same value.
            normalized_calls = []
            for position, tool_call_data in enumerate(response.tool_calls):
                tc = dict(tool_call_data)
                tc["id"] = self._tool_call_id(tc, round_num, position)
                normalized_calls.append(tc)
            response.tool_calls = normalized_calls
            if use_history:
                hist.add_message(
                    AIMessage(
                        role="assistant",
                        content=response.content or "",
                        tool_calls=normalized_calls,
                        parts=list(response.parts),
                        reasoning=response.reasoning,
                        thinking=response.thinking,
                        metadata=dict(response.metadata),
                    )
                )

            # Preserve the normalized tool-call payload. Provider serializers
            # use it to recreate native assistant tool-use/function-call blocks.
            messages.append(
                AIMessage(
                    role="assistant",
                    content=response.content or "",
                    tool_calls=normalized_calls,
                    # Responses continuations must retain native reasoning and
                    # function-call output items alongside normalized calls.
                    parts=list(response.parts),
                    reasoning=response.reasoning,
                    thinking=response.thinking,
                    metadata=dict(response.metadata),
                )
            )

            # Parse every argument payload first, then execute the calls of
            # this round concurrently: tool calls in one round are
            # independent by construction (the model chose them together),
            # and parallel execution matches how production agent runtimes
            # (OpenAI Agents SDK, Pydantic AI) run local tools.
            parsed_args: list[dict] = []
            for tc in normalized_calls:
                raw_args = tc.get("arguments", "{}")
                try:
                    arguments = (
                        json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                    )
                except (TypeError, json.JSONDecodeError):
                    arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                parsed_args.append(arguments)

            # One guard verdict per call, decided before dispatch. A blocked
            # call never runs: it becomes a synthetic result the model reads,
            # so a model stuck in a retry loop is told why instead of silently
            # losing the result it asked for.
            verdicts = [
                guard.before_call(tc.get("name", ""), args)
                for tc, args in zip(normalized_calls, parsed_args)
            ]

            async def _run_one(tc: dict, args: dict, verdict: GuardVerdict) -> dict:
                if verdict.blocked:
                    LOGS.warning(
                        "[AGENT:GUARD] blocked %s (%s) after %d occurrence(s)",
                        tc.get("name", ""),
                        verdict.code,
                        verdict.count,
                    )
                    return verdict.as_result()
                result = await executor.execute(tc.get("name", ""), args)
                guard.observe(tc.get("name", ""), args, result)
                return result

            results = await asyncio.gather(
                *(
                    _run_one(tc, args, verdict)
                    for tc, args, verdict in zip(normalized_calls, parsed_args, verdicts)
                )
            )

            round_progressed = any(not is_failure(r) for r in results)
            guard.begin_round(progressed=round_progressed)

            # A halt has to actually end the turn, and it must still produce
            # an answer: the model is told why the tools stopped working
            # rather than being cut off mid-sentence.
            if any(v.halt for v in verdicts):
                guard.halt(next(v.code for v in verdicts if v.halt))
            if guard.halted or guard.stalled:
                LOGS.warning(
                    "[AGENT:GUARD] halting turn after round %d: %s",
                    round_num + 1,
                    guard.halted_reason,
                )
                halt_nudge = AIMessage(
                    role="user",
                    content=(
                        "The tools are no longer running "
                        f"({guard.halted_reason or 'the loop stopped making progress'}). "
                        "Do not call any tool again. Summarise what you found, "
                        "or say plainly what you could not do and why."
                    ),
                )
                messages.append(halt_nudge)
                if use_history:
                    hist.add_message(halt_nudge)
                break

            # Append each result with the provider-issued id retained on the
            # matching call. OpenAI requires tool_call_id; Anthropic requires
            # tool_use_id; Gemini uses the function name in a response part.
            for tc, result in zip(normalized_calls, results):
                tc_id = tc["id"]
                tool_name = tc.get("name", "")
                tools_used.append(tool_name)
                LOGS.info(f"[AGENT:AGENT] Round {round_num + 1}: {tool_name}")

                # Extract image result for sending after response.
                if tool_name == "process_image" and "_image_result" in result:
                    image_result = result.pop("_image_result")

                result_str = json.dumps(result, default=str)[:3000]
                messages.append(
                    AIMessage(
                        role="tool",
                        content=result_str,
                        name=tool_name,
                        tool_call_id=tc_id,
                        tool_result=result_str,
                        metadata={"tool_call": tc},
                    )
                )
                if use_history:
                    hist.add_message(messages[-1])

            if tool_rounds >= rounds_cap:
                # Force final answer. This has to use the effective cap: with
                # a lower per-call cap the loop ends before the global
                # constant is ever reached, and the run would return the
                # model's empty tool-call turn instead of an answer.
                final_nudge = AIMessage(
                    role="user",
                    content="Please provide your final response now.",
                )
                messages.append(final_nudge)
                if use_history:
                    hist.add_message(final_nudge)

        # Shouldn't reach here  -  but return what we have
        return AgentResponse(
            content=response.content if response else "(no response)",
            provider=getattr(response, "provider", "unknown"),
            model=getattr(response, "model", "unknown"),
            tool_rounds=tool_rounds,
            tools_used=tools_used,
            duration_ms=(time.time() - t0) * 1000,
            reasoning=response_reasoning,
            thinking=response_thinking,
            modalities=response_modalities,
        )

    # ── Streaming respond
    async def stream(
        self,
        user_message: str,
        chat_id: int = 0,
        provider_name: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        model: str | None = None,
    ) -> AsyncGenerator[str]:
        """
        Stream the agent's response token by token.
        Yields text chunks. No tool calls in stream mode  -  use respond() for tools.
        """
        hist = self.history(chat_id)
        hist.add("user", user_message)
        messages = hist.get()
        system = _effective_system_prompt(self._system, provider_name)
        from pyUltroid.agent._skills import render_skills

        skill_block = render_skills(provider_name or None)
        if skill_block:
            system = f"{system}\n\n{skill_block}"

        # Pick best streaming provider
        provider = None
        if provider_name:
            provider = ProviderRegistry().provider_by_name(provider_name)
            if provider is None or not provider.is_configured():
                yield (
                    f"⚠️ Provider `{provider_name}` is not configured."
                    if provider is not None
                    else f"⚠️ Unknown provider `{provider_name}`."
                )
                return
        if provider is None:
            # Prefer local first
            from ._providers import CostTier

            for tier in CostTier:
                providers = ProviderRegistry().providers_by_tier(tier)
                if providers:
                    provider = providers[0]
                    break

        if provider is None:
            yield "(no provider configured)"
            return

        full = ""
        try:
            async for chunk in provider.stream(
                messages=messages,
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system,
            ):
                full += chunk
                yield chunk
        except Exception as e:
            yield f"\n[stream error: {e}]"

        if full:
            hist.add("assistant", full)

    # ── System prompt customization
    def set_chat_system(self, chat_id: Any, prompt: str) -> None:
        """Set a persisted system addendum for one chat.

        ``chat_id`` may be a numeric Telegram id (groups, private chats) or a
        DM session key like ``dm:<user>:<anchor>``; both are normalized
        through :class:`ChatKey` so the same dict can hold both without
        colliding. The previous implementation coerced to ``int`` and crashed
        on the string form.
        """
        key = normalize_chat_id(chat_id)
        if getattr(self, "_chat_systems", None) is None:
            self._chat_systems = self._load_chat_systems()
        self._chat_systems[key] = str(prompt)
        self._save_chat_systems()

    def reset_chat_system(self, chat_id: Any) -> None:
        """Remove the persisted system addendum for one chat, if any."""
        key = normalize_chat_id(chat_id)
        if getattr(self, "_chat_systems", None) is None:
            self._chat_systems = self._load_chat_systems()
        self._chat_systems.pop(key, None)
        self._save_chat_systems()

    def _load_chat_systems(self) -> dict[str, str]:
        try:
            from pyUltroid import udB

            raw = udB.get_key("AGENT_CHAT_SYSTEMS") or {}
            if isinstance(raw, dict):
                return {str(k): str(v) for k, v in raw.items() if v}
        except Exception:
            pass
        return {}

    def _save_chat_systems(self) -> None:
        try:
            from pyUltroid import udB

            udB.set_key(
                "AGENT_CHAT_SYSTEMS",
                dict(getattr(self, "_chat_systems", {})),
            )
        except Exception:
            pass

    def chat_system(self, chat_id: Any) -> str:
        """Return the system addendum for *chat_id*, or ``""`` if none.

        Accepts any :func:`normalize_chat_id` input; the key is resolved once
        and all lookup paths converge on the canonical string form, so a DM
        session key and a numeric group id never share a slot.
        """
        if getattr(self, "_chat_systems", None) is None:
            self._chat_systems = self._load_chat_systems()
        return self._chat_systems.get(normalize_chat_id(chat_id), "")

    def set_system(self, prompt: str) -> None:
        self._system = prompt

    def append_system(self, extra: str) -> None:
        self._system += f"\n\n{extra}"

    def reset_system(self) -> None:
        self._system = SYSTEM_PROMPT

    # ── Status
    async def status(self) -> dict:
        providers = ProviderRegistry().provider_health()
        cache_size = await self._mini.mini_cache_size()
        configured = [k for k, v in providers.items() if v]
        return {
            "providers": providers,
            "configured_count": len(configured),
            "configured": configured,
            "cache_entries": cache_size,
            "active_histories": len(self._histories),
            "max_tool_rounds": MAX_TOOL_ROUNDS,
            "max_history": MAX_HISTORY,
        }


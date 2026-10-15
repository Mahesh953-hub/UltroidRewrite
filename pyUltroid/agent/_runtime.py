"""Single orchestration boundary for every AI/agent surface.

Command handlers must not resolve providers, controls, or capabilities
independently.  ``AgentRuntime.run`` is the one place where a request is
normalized, capability policy is applied, and the agent is invoked.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from pyUltroid.agent._agent import AgentResponse, UltroidAgent
from pyUltroid.agent._config import AgentConfig
from pyUltroid.agent._controls import get_controls
from pyUltroid.agent._tools import DEFAULT_AGENT_CAPS, make_executor

__all__ = ("AgentRequest", "AgentRuntime", "resolve_caps")


# Capabilities that are safe on a shared/group surface.  Shell, filesystem
# writes, package installation and sending messages stay owner/sudo-only.
GROUP_SAFE_CAPS = frozenset(
    cap
    for cap in DEFAULT_AGENT_CAPS
    if cap
    not in {
        # Explicitly denied on group surfaces; these are the escalation paths.
        "shell_exec",
        "shell_write",
        "fs_write",
        "tg_send",
        "res_install",
        "tg_raw",
        "agent.spawn",
        # Asking a human interrupts them and paints a keyboard in their chat.
        "agent.ask",
    }
)


def _owner_and_sudos() -> set[int]:
    try:
        from pyUltroid._misc import owner_and_sudos

        return {int(user_id) for user_id in owner_and_sudos() if user_id is not None}
    except Exception:
        return set()


def resolve_caps(
    *,
    surface: str,
    user_id: int | None = None,
    tools_enabled: bool = True,
    is_private: bool = True,
) -> frozenset[str]:
    """Return the exact capability grant for one request.

    ``owner``/``sudo`` may use the full default set.  Everyone else receives
    only read-only/research tools, and tools can always be disabled by chat
    controls.  DM and group surfaces use the same rule, fixing the previous
    asymmetry where a group command could grant shell execution.
    """
    if not tools_enabled:
        return frozenset()
    try:
        privileged = int(user_id) in _owner_and_sudos() if user_id is not None else False
    except (TypeError, ValueError):
        privileged = False
    return frozenset(DEFAULT_AGENT_CAPS if privileged else GROUP_SAFE_CAPS)


@dataclass(slots=True)
class AgentRequest:
    """A normalized, surface-independent agent request."""

    prompt: str
    chat_id: int | str
    origin: str = "ai"
    user_id: int | None = None
    is_private: bool = True
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_rounds: int | None = None
    use_tools: bool | None = None
    use_history: bool = True
    use_cache: bool = True
    caps: frozenset[str] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class AgentRuntime:
    """The only caller of :meth:`UltroidAgent.respond` for command surfaces."""

    def __init__(self, agent: Any | None = None, **kw):
        super().__init__(**kw)
        # No module singleton to fall back on: the runtime runs on the
        # object that owns it, and standalone callers pass one explicitly.
        self.agent = agent if agent is not None else self

    def runtime_prepare(self, request: AgentRequest) -> AgentRequest:
        controls = get_controls(request.chat_id)
        tools = (
            bool(controls["tools"])
            if request.use_tools is None
            else bool(request.use_tools)
        )
        caps = request.caps
        if caps is None:
            caps = resolve_caps(
                surface=request.origin,
                user_id=request.user_id,
                tools_enabled=tools,
                is_private=request.is_private,
            )
        return AgentRequest(
            prompt=request.prompt,
            chat_id=request.chat_id,
            origin=request.origin,
            user_id=request.user_id,
            is_private=request.is_private,
            provider=(
                request.provider
                if request.provider is not None
                else AgentConfig().config_default_provider()
            ),
            model=request.model if request.model is not None else controls["model"],
            temperature=(
                request.temperature
                if request.temperature is not None
                else controls["temp"]
            ),
            max_rounds=(
                request.max_rounds
                if request.max_rounds is not None
                else controls["rounds"]
            ),
            use_tools=tools,
            use_history=request.use_history,
            use_cache=request.use_cache,
            caps=caps,
            metadata=dict(request.metadata),
        )

    async def runtime_run(self, request: AgentRequest) -> AgentResponse:
        prepared = self.runtime_prepare(request)
        return await self.agent.respond(
            user_message=prepared.prompt,
            chat_id=prepared.chat_id,  # type: ignore[arg-type]
            provider_name=prepared.provider,
            use_tools=bool(prepared.use_tools),
            use_history=prepared.use_history,
            use_cache=prepared.use_cache,
            temperature=(
                prepared.temperature
                if prepared.temperature is not None
                else 0.7
            ),
            model=prepared.model,
            max_rounds=prepared.max_rounds,
            scope=prepared.origin,
            caps=prepared.caps,
        )

    def runtime_stream(self, request: AgentRequest):
        """Yield normalized streaming chunks for *request*."""
        prepared = self.runtime_prepare(request)
        return self.agent.stream(
            user_message=prepared.prompt,
            chat_id=prepared.chat_id,  # type: ignore[arg-type]
            provider_name=prepared.provider,
            temperature=(
                prepared.temperature
                if prepared.temperature is not None
                else 0.7
            ),
            model=prepared.model,
        )

    def runtime_executor(self, request: AgentRequest):
        prepared = self.runtime_prepare(request)
        return make_executor(prepared.chat_id, caps=prepared.caps)  # type: ignore[arg-type]



"""Public API for the Ultroid AI"""

from typing import Any

from ._providers import (
    AIMessage,
    AIResponse,
    CostTier,
    BaseProvider,
    OllamaProvider,
    OpenAIProvider,
    AnthropicProvider,
    GeminiProvider,
    GroqProvider,
    OpenAICompatProvider,
    ConfigurableOpenAIProvider,
    XAIProvider,
    ApimixProvider,
    MetaLlamaProvider,
    OpenRouterProvider,
    ProviderTemplate,
    CloudflareProvider,
    ProviderRegistry,
)
from ._client import AIClient
from . import _config as config
from ._config import AgentConfig
from . import _controls as controls
from ._reporting import report_run
from ._runtime import AgentRequest, AgentRuntime, resolve_caps
from ._sessions import SessionStore
from ._skills import SKILLS, Skill, available_skills, render_skills, skill_names
from ._mini import MiniAI, Intent, classify_intent
from ._tools import (
    TOOL_SCHEMAS,
    TOOL_REQUIRED_CAPS,
    DEFAULT_AGENT_CAPS,
    MAX_AGENT_DEPTH,
    ToolExecutor,
    ToolPipeline,
    codex_tool_kwargs,
    clamp_shell_timeout,
    make_executor,
)
from ._agent import (
    UltroidAgent,
    AgentResponse,
    ConversationHistory,
    SYSTEM_PROMPT,
)
from ._tgbridge import (
    RAW_ALLOWED,
    invoke_raw,
    parse_entity_ref,
    read_recent,
    resolve_raw_function,
    send_via_master,
)
from ._lifecycle import (
    AgentLifecycle,
    lifecycle_registered,
    register_lifecycle,
)

__all__ = [
    # the union and the process instance
    "Agent",
    "agent",
    "registry",
    "miniai",
    "controls",
    "report_run",
    "AgentRequest",
    "AgentRuntime",
    "resolve_caps",
    "Skill",
    "SKILLS",
    "available_skills",
    "render_skills",
    "skill_names",
    # Primary singletons
    # Data types
    "AIMessage",
    "AIResponse",
    "AIClient",
    "AgentResponse",
    "Intent",
    # Utilities
    "classify_intent",
    "make_executor",
    "TOOL_SCHEMAS",
    "SYSTEM_PROMPT",
    # Cost routing
    "CostTier",
    # Capability policy (W2-C.4)
    "TOOL_REQUIRED_CAPS",
    "DEFAULT_AGENT_CAPS",
    "MAX_AGENT_DEPTH",
    "clamp_shell_timeout",
    # Model-aware providers
    "BaseProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "AnthropicProvider",
    "GeminiProvider",
    "GroqProvider",
    "OpenAICompatProvider",
    "ConfigurableOpenAIProvider",
    "XAIProvider",
    "ApimixProvider",
    "MetaLlamaProvider",
    "OpenRouterProvider",
    "ProviderTemplate",
    "CloudflareProvider",
    "ProviderRegistry",
    # Tool pipeline + Codex compatibility
    "ToolPipeline",
    "ToolExecutor",
    "RAW_ALLOWED",
    "invoke_raw",
    "parse_entity_ref",
    "read_recent",
    "resolve_raw_function",
    "send_via_master",
    "codex_tool_kwargs",
    # Lifecycle (W2-C.5)
    "AgentLifecycle",
    "engine_lifecycle",
    "register_lifecycle",
    "lifecycle_registered",
]


# ── the union ───────────────────────────────────────────────────────────────
# Bases are ordered weakest-last on purpose. Every ``__init__`` below calls
# ``super().__init__()`` first, so the MRO unwind initialises in the opposite
# order - config, sessions, lifecycle, providers, runtime, mini, agent - which
# is exactly the dependency order. A class can therefore rely on every layer
# beneath it already existing, and each collaborator is found with
# ``kw.get("...", self)`` defaulting to the union itself.


class Agent(
    AgentRuntime,
    MiniAI,
    UltroidAgent,
    ProviderRegistry,
    AgentLifecycle,
    SessionStore,
    AgentConfig,
):
    """Every agent subsystem, as one object.

    Layering, bottom first:

    * :class:`AgentConfig` - the ``AGENT_*`` provider keys
    * :class:`SessionStore` - DM session records
    * :class:`AgentLifecycle` - start/stop hooks
    * :class:`ProviderRegistry` - the provider catalogue
    * :class:`MiniAI` - single-shot routing, no tool loop and no history
    * :class:`UltroidAgent` - the agentic loop, which delegates to its MiniAI
    * :class:`AgentRuntime` - request normalisation and capability policy

    Each subsystem keeps its own namespace prefix, so nothing shadows anything
    else: ``config_*``, ``session_*``, ``lifecycle_*``, ``provider_*``,
    ``mini_*``, ``runtime_*``, and bare names for the agent loop itself
    (``respond``, ``stream``, ``chat_system``). The two former collisions -
    ``AgentConfig.key`` vs ``SessionStore.key``, which pointed at *different*
    databases, and ``status``/``register``/``respond`` across three classes -
    are gone by construction rather than by MRO luck.
    """

    def __init__(self, *, provider: Any = None, client: Any = None) -> None:
        super().__init__(provider=provider, client=client)


async def miniai(*, provider: Any = None, client: Any = None) -> Agent:
    """An :class:`Agent` on the fast path: no tool loop, no history.

    Same object, different entry point. :meth:`Agent.mini_ask` and
    :meth:`Agent.respond` share the provider catalogue and the cache, so a
    fast-path Agent can be handed to a full turn without re-resolving anything.
    """
    return Agent(provider=provider, client=client)


#: The process-wide agent.
#
#: Plugins, the assistant and the runtime all need the *same* agent: two would
#: mean two provider resolutions, two caches and two chat histories. This is
#: shared state on purpose, and it is the only instance in the package.
_AGENT: Agent | None = None


def agent() -> Agent:
    """The process-wide :class:`Agent`, built on first use."""
    global _AGENT
    if _AGENT is None:
        _AGENT = Agent()
    return _AGENT


class _ProviderRegistryCompat:
    """Legacy provider-registry calls forwarded to the process Agent."""

    @staticmethod
    def by_name(name: str) -> BaseProvider | None:
        return agent().provider_by_name(name)

    @staticmethod
    def register(provider: BaseProvider) -> None:
        agent().provider_register(provider)


registry = _ProviderRegistryCompat()

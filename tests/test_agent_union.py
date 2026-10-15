"""The Agent union: no collisions, no singletons, correct layer order."""

from __future__ import annotations

import asyncio

import pytest

from pyUltroid.agent import (
    Agent,
    AgentConfig,
    AgentLifecycle,
    AgentRuntime,
    MiniAI,
    ProviderRegistry,
    SessionStore,
    UltroidAgent,
    agent,
    miniai,
)


def test_mro_covers_every_subsystem():
    for cls in (
        AgentRuntime,
        MiniAI,
        UltroidAgent,
        ProviderRegistry,
        AgentLifecycle,
        SessionStore,
        AgentConfig,
    ):
        assert cls in Agent.__mro__


def test_weakest_base_is_last():
    """Otherwise the cooperative __init__ unwinds in the wrong order."""
    mro = Agent.__mro__
    assert mro.index(AgentConfig) > mro.index(SessionStore)
    assert mro.index(SessionStore) > mro.index(AgentLifecycle)
    assert mro.index(AgentLifecycle) > mro.index(ProviderRegistry)
    assert mro.index(ProviderRegistry) > mro.index(MiniAI)


def test_constructs():
    assert isinstance(Agent(), Agent)


@pytest.mark.parametrize(
    "method",
    [
        "config_field", "config_set", "config_status", "config_default_provider",
        "session_list", "session_find", "session_history_key",
        "lifecycle_register", "lifecycle_start", "lifecycle_stop",
        "provider_by_name", "provider_health", "providers_by_tier",
        "providers_configured", "provider_cheapest", "provider_register",
        "mini_respond", "mini_ask", "mini_cache_size", "mini_flush_cache",
        "runtime_prepare", "runtime_run", "runtime_stream", "runtime_executor",
        "respond", "stream", "history", "chat_system", "set_chat_system",
        "status", "set_provider",
    ],
)
def test_every_capability_is_reachable(method):
    assert hasattr(Agent(), method)


def test_the_two_dangerous_collisions_are_gone_by_construction():
    """These pointed at different databases; one silently won under MRO."""
    a = Agent()
    assert a.CONFIG_KEY != a.SESSION_KEY
    assert a.CONFIG_KEY == "AGENT_ENV_MIGRATED"
    assert a.SESSION_KEY.startswith("AGENT_DM_SESSIONS")


def test_no_shadowing_between_layers():
    """Each name resolves to exactly one class in the MRO."""
    mro = Agent.__mro__
    for name in ("status", "register", "respond", "stream", "run", "set_provider"):
        owners = [c.__name__ for c in mro if name in vars(c)]
        assert len(owners) <= 1, f"{name} defined on {owners}"


def test_miniai_returns_an_agent():
    result = asyncio.run(miniai())
    assert isinstance(result, Agent)


def test_the_process_agent_is_one_instance():
    assert agent() is agent()
    assert isinstance(agent(), Agent)


def test_there_are_no_module_singletons_left():
    from pyUltroid.agent import _config, _lifecycle, _mini, _providers, _sessions

    for module, gone in (
        (_config, ("agent_config", "get_field", "set_field", "_udb", "_var")),
        (_sessions, ("session_store", "create_session", "sessions_for", "_udb")),
        (_providers, ("registry", "ollama", "cloudflare")),
        (_mini, ("mini",)),
        (_lifecycle, ("engine_lifecycle",)),
    ):
        for name in gone:
            assert not hasattr(module, name), f"{module.__name__}.{name}"


def test_the_config_var_hook_is_patchable():
    """It was a function-local import, so nothing could reach it."""
    from pyUltroid.agent import _config

    assert hasattr(_config, "Var")

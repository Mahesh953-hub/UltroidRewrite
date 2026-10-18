"""Tests for the unified AI configuration, per-chat controls, and DM sessions.

The configuration tests matter most: they pin the precedence that makes
"route the configured AI" true - the menu's value wins, the one-time migration
copies legacy environment values in without overwriting it, and after
migration an edited environment variable is ignored.
"""

import asyncio
import types
import unittest
from unittest.mock import patch

from pyUltroid.agent._config import AgentConfig
from pyUltroid.agent._sessions import SessionStore
from pyUltroid.agent import _controls as ai_controls


class _FakeDB:
    def __init__(self, initial=None):
        self.store = dict(initial or {})

    def get_key(self, key):
        return self.store.get(key)

    def set_key(self, key, value):
        self.store[key] = value

    def del_key(self, key):
        self.store.pop(key, None)


def _var(**values):
    return types.SimpleNamespace(**values)


class ConfigFieldTests(unittest.TestCase):
    def setUp(self):
        self.db = _FakeDB()
        patcher = patch("pyUltroid.udB", new=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_menu_value_is_what_a_request_uses(self):
        self.db.store["AGENT_CLOUD_GEMINI_KEY"] = "menu-key"
        self.db.store["AGENT_CLOUD_GEMINI_MODEL"] = "menu-model"
        self.assertEqual(AgentConfig().config_field("gemini", "KEY"), "menu-key")
        self.assertEqual(AgentConfig().config_field("gemini", "MODEL"), "menu-model")

    def test_an_unknown_provider_is_rejected(self):
        with self.assertRaises(ValueError):
            AgentConfig().config_field("nope", "KEY")

    def test_the_claude_menu_takes_precedence_over_the_cloud_namespace(self):
        self.db.store["AGENT_CLAUDE_KEY"] = "claude-key"
        self.db.store["AGENT_CLOUD_ANTHROPIC_KEY"] = "cloud-key"
        self.assertEqual(AgentConfig().config_field("anthropic", "KEY"), "claude-key")

    def test_setting_a_field_writes_the_first_key_and_clear_removes_all(self):
        AgentConfig().config_set("openai", "KEY", "k")
        self.assertEqual(self.db.store["AGENT_CLOUD_OPENAI_KEY"], "k")
        AgentConfig().config_clear("anthropic", "KEY")
        self.assertNotIn("AGENT_CLAUDE_KEY", self.db.store)
        self.assertNotIn("AGENT_CLOUD_ANTHROPIC_KEY", self.db.store)

    def test_field_source_names_the_key_that_holds_the_value(self):
        self.db.store["AGENT_CLOUD_GROQ_MODEL"] = "m"
        self.assertEqual(
            AgentConfig().config_source("groq", "MODEL"), "AGENT_CLOUD_GROQ_MODEL"
        )
        self.assertIsNone(AgentConfig().config_source("groq", "KEY"))

    def test_cloud_providers_need_a_key_local_servers_need_a_base(self):
        self.assertFalse(AgentConfig().config_ready("openai"))
        self.db.store["AGENT_CLOUD_OPENAI_KEY"] = "k"
        self.assertTrue(AgentConfig().config_ready("openai"))
        self.assertFalse(AgentConfig().config_ready("ollama"))
        self.db.store["AGENT_LLAMA_OLLAMA_BASE"] = "http://x:11434"
        self.assertTrue(AgentConfig().config_ready("ollama"))

    def test_compat_alias_is_canonicalized_to_the_registry_name(self):
        self.db.store["AGENT_CLOUD_COMPAT_BASE"] = "http://local/v1"
        self.assertTrue(AgentConfig().config_use_default("compat"))
        self.assertEqual(AgentConfig().config_default_provider(), "openai_compat")
        from pyUltroid.agent import agent

        self.assertIsNotNone(agent().provider_by_name(AgentConfig().config_default_provider()))

    def test_the_default_provider_pin_is_validated(self):
        self.assertIsNone(AgentConfig().config_default_provider())
        self.assertTrue(AgentConfig().config_use_default("gemini"))
        self.assertEqual(AgentConfig().config_default_provider(), "gemini")
        with self.assertRaises(ValueError):
            AgentConfig().config_use_default("not-a-provider")
        self.assertTrue(AgentConfig().config_use_default(None))
        self.assertIsNone(AgentConfig().config_default_provider())

    def test_openrouter_is_a_registered_configured_provider(self):
        from pyUltroid.agent import agent

        provider = agent().provider_by_name("openrouter")
        self.assertIsNotNone(provider)
        self.db.store["AGENT_CLOUD_OPENROUTER_KEY"] = "or-key"
        self.db.store["AGENT_CLOUD_OPENROUTER_MODEL"] = "vendor/model"
        self.assertTrue(provider.is_configured())
        self.assertEqual(provider.api_key, "or-key")
        self.assertEqual(provider.default_model, "vendor/model")

    def test_a_pin_requires_a_registered_and_configured_provider(self):
        with self.assertRaises(ValueError):
            AgentConfig().config_use_default("openrouter", require_configured=True)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.db = _FakeDB()
        patcher = patch("pyUltroid.udB", new=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_legacy_environment_is_copied_once(self):
        var = _var(
            OPENAI_API_KEY="env-key",
            OPENAI_MODEL="env-model",
            OLLAMA_URL="http://env:11434",
        )
        with patch("pyUltroid.agent._config.Var", new=var):
            written = AgentConfig().config_migrate()
        self.assertIn("AGENT_CLOUD_OPENAI_KEY", written)
        self.assertEqual(self.db.store["AGENT_CLOUD_OPENAI_KEY"], "env-key")
        self.assertEqual(self.db.store["AGENT_CLOUD_OPENAI_MODEL"], "env-model")
        self.assertEqual(self.db.store["AGENT_LLAMA_OLLAMA_BASE"], "http://env:11434")

    def test_a_second_run_does_nothing(self):
        var = _var(OPENAI_API_KEY="env-key")
        with patch("pyUltroid.agent._config.Var", new=var):
            AgentConfig().config_migrate()
        var2 = _var(OPENAI_API_KEY="changed-later")
        with patch("pyUltroid.agent._config.Var", new=var2):
            written = AgentConfig().config_migrate()
        self.assertEqual(written, [])
        # The database is the only source after migration: a later env edit is
        # deliberately ignored.
        self.assertEqual(self.db.store["AGENT_CLOUD_OPENAI_KEY"], "env-key")

    def test_a_cleared_key_is_not_refilled_from_the_environment(self):
        """After migration the database is the only source.

        A user who clears a key in the menu to disable a provider must not
        find it silently refilled from the environment on the next boot.
        """
        var = _var(OPENAI_API_KEY="env-key")
        with patch("pyUltroid.agent._config.Var", new=var):
            AgentConfig().config_migrate()
        self.db.del_key("AGENT_CLOUD_OPENAI_KEY")
        with patch("pyUltroid.agent._config.Var", new=var):
            AgentConfig().config_migrate()
        self.assertNotIn("AGENT_CLOUD_OPENAI_KEY", self.db.store)

    def test_a_menu_value_is_never_overwritten_by_migration(self):
        self.db.store["AGENT_CLOUD_OPENAI_KEY"] = "menu-key"
        var = _var(OPENAI_API_KEY="env-key")
        with patch("pyUltroid.agent._config.Var", new=var):
            AgentConfig().config_migrate()
        self.assertEqual(self.db.store["AGENT_CLOUD_OPENAI_KEY"], "menu-key")

    def test_the_gemini_fallback_key_is_honoured(self):
        var = _var(GOOGLE_API_KEY="google-key")
        with patch("pyUltroid.agent._config.Var", new=var):
            AgentConfig().config_migrate()
        self.assertEqual(self.db.store["AGENT_CLOUD_GEMINI_KEY"], "google-key")

    def test_a_legacy_database_key_migrates_too(self):
        self.db.store["OPENAI_API_KEY"] = "old-udb-key"
        with patch("pyUltroid.agent._config.Var", new=_var()):
            AgentConfig().config_migrate()
        self.assertEqual(self.db.store["AGENT_CLOUD_OPENAI_KEY"], "old-udb-key")

    def test_status_reports_what_a_request_would_use(self):
        self.db.store["AGENT_CLOUD_GROQ_KEY"] = "k"
        status = AgentConfig().config_status()
        self.assertTrue(status["groq"]["configured"])
        self.assertEqual(status["groq"]["sources"]["KEY"], "AGENT_CLOUD_GROQ_KEY")
        self.assertFalse(status["openai"]["configured"])


class ProviderReadsThroughConfigTests(unittest.TestCase):
    """The providers themselves must read the unified keys, not the env."""

    def setUp(self):
        self.db = _FakeDB()
        patcher = patch("pyUltroid.udB", new=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _import_providers(self):
        from pyUltroid.agent import _providers

        return _providers

    def test_openai_reads_the_unified_keys(self):
        providers = self._import_providers()
        self.db.store["AGENT_CLOUD_OPENAI_KEY"] = "k"
        self.db.store["AGENT_CLOUD_OPENAI_MODEL"] = "m"
        self.db.store["AGENT_CLOUD_OPENAI_BASE"] = "http://b"
        provider = providers.OpenAIProvider()
        self.assertEqual(provider.api_key, "k")
        self.assertEqual(provider.default_model, "m")
        self.assertEqual(provider.base_url, "http://b")

    def test_an_unconfigured_provider_has_no_key(self):
        providers = self._import_providers()
        provider = providers.OpenAIProvider()
        self.assertIsNone(provider.api_key)
        self.assertFalse(provider.is_configured())

    def test_anthropic_reads_the_claude_menu_first(self):
        providers = self._import_providers()
        self.db.store["AGENT_CLAUDE_KEY"] = "claude"
        provider = providers.AnthropicProvider()
        self.assertEqual(provider.api_key, "claude")
        self.assertTrue(provider.is_configured())

    def test_an_explicit_override_still_wins(self):
        providers = self._import_providers()
        self.db.store["AGENT_CLOUD_GEMINI_KEY"] = "stored"
        provider = providers.GeminiProvider()
        provider.api_key = "explicit"
        self.assertEqual(provider.api_key, "explicit")

    def test_compat_maps_its_prefix_to_the_unified_namespace(self):
        providers = self._import_providers()
        self.db.store["AGENT_CLOUD_COMPAT_KEY"] = "ck"
        self.db.store["AGENT_CLOUD_COMPAT_BASE"] = "http://cb"
        provider = providers.ConfigurableOpenAIProvider()
        self.assertEqual(provider.api_key, "ck")
        self.assertEqual(provider.base_url, "http://cb")
        self.assertTrue(provider.is_configured())

    def test_xai_keeps_its_own_namespace(self):
        providers = self._import_providers()
        self.db.store["AGENT_CLOUD_XAI_KEY"] = "xk"
        provider = providers.XAIProvider()
        self.assertEqual(provider.api_key, "xk")

    def test_apimix_reads_its_namespace_and_pins_the_public_base(self):
        providers = self._import_providers()
        self.db.store["AGENT_CLOUD_APIMIX_KEY"] = "apx"
        provider = providers.ApimixProvider()
        self.assertEqual(provider.api_key, "apx")
        self.assertEqual(provider.base_url, "https://api.apmix.ai/v1")
        self.assertTrue(provider.is_configured())
        # A stored base URL still wins over the published default.
        self.db.store["AGENT_CLOUD_APIMIX_BASE"] = "https://mirror.example/v1"
        self.assertEqual(provider.base_url, "https://mirror.example/v1")

    def test_configured_apimix_is_default_unless_another_provider_is_pinned(self):
        self.db.store["AGENT_CLOUD_APIMIX_KEY"] = "apx-test-key"
        self.assertEqual(AgentConfig().config_default_provider(), "apimix")
        self.db.store["AGENT_PROVIDER"] = "openrouter"
        self.assertEqual(AgentConfig().config_default_provider(), "openrouter")

    def test_apimix_is_unconfigured_without_a_key(self):
        providers = self._import_providers()
        self.assertFalse(providers.ApimixProvider().is_configured())


class ControlsTests(unittest.TestCase):
    def setUp(self):
        self.db = _FakeDB()
        patcher = patch("pyUltroid.udB", new=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_defaults_match_the_pre_controls_behaviour(self):
        controls = ai_controls.get_controls(123)
        self.assertTrue(controls["tools"])
        self.assertIsNone(controls["rounds"])
        self.assertEqual(controls["temp"], 0.7)
        self.assertIsNone(controls["model"])

    def test_a_control_round_trips_through_the_database(self):
        ai_controls.set_control(123, "tools", False)
        ai_controls.set_control(123, "rounds", 2)
        controls = ai_controls.get_controls(123)
        self.assertFalse(controls["tools"])
        self.assertEqual(controls["rounds"], 2)
        # another chat is untouched
        self.assertTrue(ai_controls.get_controls(456)["tools"])

    def test_clearing_a_control_restores_the_default(self):
        ai_controls.set_control(123, "model", "big-model")
        ai_controls.set_control(123, "model", None)
        self.assertIsNone(ai_controls.get_controls(123)["model"])

    def test_reset_clears_the_whole_chat(self):
        ai_controls.set_control(123, "tools", False)
        ai_controls.reset_controls(123)
        self.assertTrue(ai_controls.get_controls(123)["tools"])

    def test_an_unknown_control_is_rejected(self):
        with self.assertRaises(ValueError):
            ai_controls.set_control(123, "nope", 1)


class SessionStoreTests(unittest.TestCase):
    """The DM session store: anchors, ordering, and history keys."""

    def setUp(self):
        self.db = _FakeDB()
        patcher = patch("pyUltroid.udB", new=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        from pyUltroid.agent import _sessions as store

        self.store = store

    def test_sessions_are_stored_newest_first(self):
        self.store.SessionStore().session_create(7, 100, "first")
        self.store.SessionStore().session_create(7, 200, "second")
        sessions = self.store.SessionStore().session_list(7)
        self.assertEqual([s["anchor"] for s in sessions], [200, 100])

    def test_an_anchor_lookup_finds_the_session(self):
        self.store.SessionStore().session_create(7, 100, "first")
        self.assertEqual(self.store.SessionStore().session_find(7, 100)["title"], "first")
        self.assertIsNone(self.store.SessionStore().session_find(7, 999))
        self.assertIsNone(self.store.SessionStore().session_find(8, 100))  # another user

    def test_re_anchoring_replaces_the_old_session(self):
        self.store.SessionStore().session_create(7, 100, "old")
        self.store.SessionStore().session_create(7, 100, "new")
        sessions = self.store.SessionStore().session_list(7)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["title"], "new")

    def test_the_history_key_never_collides_with_a_chat_id(self):
        key = self.store.SessionStore().session_history_key(7, 100)
        self.assertEqual(key, "dm:7:100")
        self.assertNotEqual(key, "7")

    def test_the_session_cap_is_enforced(self):
        for i in range(self.store.MAX_SESSIONS + 5):
            self.store.SessionStore().session_create(7, 1000 + i, f"s{i}")
        self.assertEqual(len(self.store.SessionStore().session_list(7)), self.store.MAX_SESSIONS)

    def test_corrupt_stored_sessions_are_ignored(self):
        self.db.store["AGENT_DM_SESSIONS_7"] = "not-a-list"
        self.assertEqual(self.store.SessionStore().session_list(7), [])


class RunReportingTests(unittest.TestCase):
    """Log-channel reporting: rich first, plain fallback, never a dependency."""

    def setUp(self):
        from pyUltroid.agent import _reporting

        self.reporting = _reporting
        self.sent = []

    def _run(self, **overrides):
        """Start a report and drive it to completion.

        ``gather(..., return_exceptions=True)`` mirrors the production
        contract: the caller of ``report_run`` never awaits the task, so a
        failure stays inside it and the done-callback swallows it.
        """
        kwargs = dict(
            origin=".ai",
            chat_id=42,
            user="someone",
            provider="gemini",
            model="m",
            prompt="hello",
            duration=1.5,
        )
        kwargs.update(overrides)
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            task = self.reporting.report_run(**kwargs)
            if task is None:
                return None
            return loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    def test_no_log_channel_means_no_report(self):
        with patch.object(self.reporting, "_log_channel", return_value=None):
            self.assertIsNone(self._run())

    def test_the_rich_path_is_used_first(self):
        rich_calls = []

        async def fake_send_rich(client, channel, blocks):
            rich_calls.append((channel, blocks))
            return object()

        fake_client = types.SimpleNamespace()
        with (
            patch.object(self.reporting, "_log_channel", return_value=777),
            patch.object(self.reporting, "_client", return_value=fake_client),
            patch("pyUltroid._core.richmsg.send_rich", fake_send_rich),
        ):
            self._run(tools_used=["shell"], ok=False, error="boom")
        self.assertEqual(len(rich_calls), 1)
        self.assertEqual(rich_calls[0][0], 777)

    def test_a_rich_failure_falls_back_to_plain_text(self):
        plain = []

        async def failing_send_rich(client, channel, blocks):
            raise RuntimeError("rich unavailable")

        async def send_message(peer, text):
            plain.append((peer, text))

        fake_client = types.SimpleNamespace(send_message=send_message)
        with (
            patch.object(self.reporting, "_log_channel", return_value=777),
            patch.object(self.reporting, "_client", return_value=fake_client),
            patch("pyUltroid._core.richmsg.send_rich", failing_send_rich),
        ):
            self._run()
        self.assertEqual(len(plain), 1)
        self.assertIn("Agent run", plain[0][1])
        self.assertIn("hello", plain[0][1])

    def test_a_failing_log_channel_never_raises(self):
        async def failing_send_rich(client, channel, blocks):
            raise RuntimeError("log channel down")

        async def send_message(peer, text):
            raise RuntimeError("still down")

        fake_client = types.SimpleNamespace(send_message=send_message)
        with (
            patch.object(self.reporting, "_log_channel", return_value=777),
            patch.object(self.reporting, "_client", return_value=fake_client),
            patch("pyUltroid._core.richmsg.send_rich", failing_send_rich),
        ):
            results = self._run()
        # the exception stayed inside the task; nothing raised here
        self.assertEqual(len(results), 1)
        self.assertIsInstance(results[0], RuntimeError)


class MaxRoundsTests(unittest.TestCase):
    """``respond(max_rounds=...)`` caps the loop without touching the default."""

    def _stubbed_agent(self, tool_rounds_before_stopping):
        """An agent whose model asks for a tool on every round until it stops.

        Returns ``(agent, calls)`` where *calls* counts provider requests.
        """
        from pyUltroid.agent._agent import UltroidAgent
        from pyUltroid.agent._providers import AIResponse

        calls = {"n": 0}

        class _Mini:
            async def mini_respond(self, **kwargs):
                calls["n"] += 1
                if calls["n"] <= tool_rounds_before_stopping:
                    return AIResponse(
                        content="",
                        provider="stub",
                        model="stub",
                        tool_calls=[
                            {
                                "id": f"c{calls['n']}",
                                "name": "no_such_tool",
                                "arguments": {},
                            }
                        ],
                    )
                return AIResponse(content="done", provider="stub", model="stub")

        agent = UltroidAgent.__new__(UltroidAgent)
        agent._mini = _Mini()
        agent._system = "system"
        agent._histories = {}
        return agent, calls

    def test_zero_rounds_makes_exactly_one_provider_call(self):
        agent, calls = self._stubbed_agent(tool_rounds_before_stopping=99)
        response = asyncio.run(
            agent.respond("hi", chat_id=1, use_history=False, max_rounds=0)
        )
        self.assertEqual(calls["n"], 1)
        # the model asked for a tool and the loop ended before it could answer,
        # so the response is the model's last (empty) turn, not a hallucination
        self.assertEqual(response.content, "")

    def test_the_default_cap_bounds_the_loop(self):
        from pyUltroid.agent._agent import MAX_TOOL_ROUNDS

        agent, calls = self._stubbed_agent(tool_rounds_before_stopping=99)
        asyncio.run(agent.respond("hi", chat_id=1, use_history=False))
        self.assertEqual(calls["n"], MAX_TOOL_ROUNDS + 1)

    def test_an_explicit_cap_bounds_the_loop(self):
        agent, calls = self._stubbed_agent(tool_rounds_before_stopping=99)
        asyncio.run(agent.respond("hi", chat_id=1, use_history=False, max_rounds=2))
        self.assertEqual(calls["n"], 3)


if __name__ == "__main__":
    unittest.main()


class OllamaSetterTests(unittest.TestCase):
    """.ollama use sets the model without a traceback."""

    def setUp(self):
        self.db = _FakeDB()
        patcher = patch("pyUltroid.udB", new=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_model_setter_overrides_in_memory_only(self):
        from pyUltroid.agent import _providers

        provider = _providers.OllamaProvider()
        provider.default_model = "custom"
        self.assertEqual(provider.default_model, "custom")
        # the database holds the persistent choice, not the override
        self.assertNotIn("AGENT_LLAMA_OLLAMA_MODEL", self.db.store)
        AgentConfig().config_set("ollama", "MODEL", "persisted")
        provider2 = _providers.OllamaProvider()
        self.assertEqual(provider2.default_model, "persisted")


class PackageContractTests(unittest.TestCase):
    """The package must export what plugins import from it.

    ``plugins/brain.py`` and ``assistant/ai_dm.py`` import ``controls`` and
    ``report_run`` from ``pyUltroid.agent``; the plugin loader imports the
    plugin, so a missing export fails the whole plugin at load time.
    """

    def test_the_plugin_imports_resolve(self):
        from pyUltroid.agent import (  # noqa: F401
            agent,
            config,
            controls,
            report_run,
        )

    def test_controls_and_reporting_are_the_real_modules(self):
        from pyUltroid.agent import _controls, _reporting, controls, report_run

        self.assertIs(controls, _controls)
        self.assertIs(report_run, _reporting.report_run)


class DmToolsBoundaryTests(unittest.TestCase):
    """Tools in a DM session are owner/sudo-only, and fail closed."""

    def setUp(self):
        self.db = _FakeDB()
        patcher = patch("pyUltroid.udB", new=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_owner_with_tools_on_gets_tools(self):
        with patch("pyUltroid.agent._controls._owner_and_sudos", return_value={7}):
            self.assertTrue(ai_controls.dm_tools_enabled(7, {"tools": True}))

    def test_a_stranger_with_tools_on_gets_no_tools(self):
        """The controls alone can never grant DM tools to a non-owner."""
        with patch("pyUltroid.agent._controls._owner_and_sudos", return_value={7}):
            self.assertFalse(ai_controls.dm_tools_enabled(999, {"tools": True}))

    def test_an_owner_with_tools_off_gets_no_tools(self):
        with patch("pyUltroid.agent._controls._owner_and_sudos", return_value={7}):
            self.assertFalse(ai_controls.dm_tools_enabled(7, {"tools": False}))

    def test_the_boundary_fails_closed_without_a_bot(self):
        # No patch: _owner_and_sudos cannot read the owner list here, so it
        # returns an empty set and tools stay off.
        self.assertFalse(ai_controls.dm_tools_enabled(7, {"tools": True}))


class MigrationAliasTests(unittest.TestCase):
    """Migration must respect every alias a field can live under."""

    def setUp(self):
        self.db = _FakeDB()
        patcher = patch("pyUltroid.udB", new=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_cloud_key_blocks_migration_into_the_claude_namespace(self):
        self.db.store["AGENT_CLOUD_ANTHROPIC_KEY"] = "menu-key"
        var = _var(ANTHROPIC_API_KEY="env-key")
        with patch("pyUltroid.agent._config.Var", new=var):
            AgentConfig().config_migrate()
        self.assertNotIn("AGENT_CLAUDE_KEY", self.db.store)
        self.assertEqual(AgentConfig().config_field("anthropic", "KEY"), "menu-key")

    def test_a_failed_write_does_not_set_the_migrated_flag(self):
        class _Flaky(_FakeDB):
            def set_key(self, key, value):
                if key != "AGENT_ENV_MIGRATED":
                    raise RuntimeError("database down")
                super().set_key(key, value)

        db = _Flaky({})
        patcher = patch("pyUltroid.udB", new=db)
        patcher.start()
        self.addCleanup(patcher.stop)
        var = _var(OPENAI_API_KEY="env-key")
        with patch("pyUltroid.agent._config.Var", new=var):
            written = AgentConfig().config_migrate()
        self.assertEqual(written, [])
        self.assertNotIn("AGENT_ENV_MIGRATED", db.store)
        # Recovery: the next boot retries, because the flag was never set.
        db2 = _FakeDB()
        patcher2 = patch("pyUltroid.udB", new=db2)
        patcher2.start()
        self.addCleanup(patcher2.stop)
        with patch("pyUltroid.agent._config.Var", new=var):
            AgentConfig().config_migrate()
        self.assertEqual(db2.store.get("AGENT_CLOUD_OPENAI_KEY"), "env-key")


class SessionStoreFailureTests(unittest.TestCase):
    def setUp(self):
        self.db = _FakeDB()
        patcher = patch("pyUltroid.udB", new=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        from pyUltroid.agent import _sessions as store

        self.store = store

    def test_a_failed_save_raises_instead_of_stranding_the_session(self):
        class _ReadOnly(_FakeDB):
            def set_key(self, key, value):
                raise RuntimeError("database down")

        with patch("pyUltroid.udB", new=_ReadOnly()):
            with self.assertRaises(self.store.SessionStoreError):
                self.store.SessionStore().session_create(7, 100, "doomed")
        # nothing was recorded, so no anchor can be half-alive
        self.assertEqual(self.store.SessionStore().session_list(7), [])


class StoreClassLayerTests(unittest.TestCase):
    """The config + session stores follow the dB class-layer contract."""

    def test_there_are_no_module_singletons_left(self):
        """The stores are classes now; nothing is instantiated at import."""
        from pyUltroid.agent import _config, _sessions

        for module, gone in (
            (_config, ("agent_config", "get_field", "set_field", "_udb", "_var")),
            (_sessions, ("session_store", "create_session", "sessions_for", "_udb")),
        ):
            for name in gone:
                self.assertFalse(
                    hasattr(module, name),
                    f"{module.__name__} still exposes {name!r}",
                )

    def test_the_stores_read_the_live_database(self):
        with patch("pyUltroid.udB", new=_FakeDB()):
            self.assertEqual(AgentConfig().config_field("openai", "KEY", "d"), "d")
            self.assertIsNone(AgentConfig().config_field("openai", "KEY"))
            with self.assertRaises(ValueError):
                AgentConfig().config_field("nope", "KEY")

    def test_the_session_store_round_trips(self):
        with patch("pyUltroid.udB", new=_FakeDB()):
            mine = SessionStore()
            session = mine.session_create(11, 200, "mine")
            self.assertEqual(session["anchor"], 200)
            self.assertEqual(mine.session_list(11), [session])
            self.assertEqual(mine.session_find(11, 200), session)
            self.assertEqual(
                mine.session_history_key(11, 200),
                SessionStore().session_history_key(11, 200),
            )


class ForceFinalAnswerTests(unittest.TestCase):
    """A capped loop still has to produce a text answer."""

    def _agent(self):
        from pyUltroid.agent._agent import UltroidAgent
        from pyUltroid.agent._providers import AIResponse

        class _Mini:
            def __init__(self):
                self.calls = 0

            async def mini_respond(self, *, messages, system, **kwargs):
                self.calls += 1
                nudged = any(
                    getattr(m, "content", "")
                    == "Please provide your final response now."
                    for m in messages
                )
                if nudged:
                    return AIResponse(
                        content="final answer", provider="stub", model="stub"
                    )
                return AIResponse(
                    content="",
                    provider="stub",
                    model="stub",
                    tool_calls=[
                        {
                            "id": f"c{self.calls}",
                            "name": "no_such_tool",
                            "arguments": {},
                        }
                    ],
                )

        agent = UltroidAgent.__new__(UltroidAgent)
        agent._mini = _Mini()
        agent._system = "system"
        agent._histories = {}
        return agent

    def test_a_capped_loop_ends_with_a_text_answer(self):
        agent = self._agent()
        response = asyncio.run(
            agent.respond("hi", chat_id=1, use_history=False, max_rounds=2)
        )
        self.assertEqual(response.content, "final answer")

    def test_the_default_cap_behaves_the_same(self):
        agent = self._agent()
        response = asyncio.run(agent.respond("hi", chat_id=1, use_history=False))
        self.assertEqual(response.content, "final answer")


class ProviderSystemPromptTests(unittest.TestCase):
    """A provider's configured SYS joins the agent system prompt."""

    def setUp(self):
        self.db = _FakeDB()
        patcher = patch("pyUltroid.udB", new=self.db)
        patcher.start()
        self.addCleanup(patcher.stop)
        from pyUltroid.agent import _agent as agent_mod

        self.agent_mod = agent_mod

    def test_a_forced_provider_sys_is_appended(self):
        self.db.store["AGENT_CLOUD_GROQ_SYS"] = "Always answer in rhyme."
        merged = self.agent_mod._effective_system_prompt("base", "groq")
        self.assertTrue(merged.startswith("base\n\nAlways answer in rhyme."))
        self.assertIn(self.agent_mod._RICH_RESPONSE_POLICY, merged)

    def test_no_sys_leaves_the_prompt_alone(self):
        merged = self.agent_mod._effective_system_prompt("base", "groq")
        self.assertEqual(
            merged, "base\n\n" + self.agent_mod._RICH_RESPONSE_POLICY
        )

    def test_a_sys_without_a_base_stands_alone(self):
        self.db.store["AGENT_CLOUD_GROQ_SYS"] = "You are terse."
        merged = self.agent_mod._effective_system_prompt("", "groq")
        self.assertEqual(
            merged, "You are terse.\n\n" + self.agent_mod._RICH_RESPONSE_POLICY
        )

    def test_an_unpinned_provider_applies_the_pinned_default(self):
        self.db.store["AGENT_PROVIDER"] = "gemini"
        self.db.store["AGENT_CLOUD_GEMINI_SYS"] = "Be brief."
        merged = self.agent_mod._effective_system_prompt("base", None)
        self.assertIn("Be brief.", merged)


class ReportingTimeoutTests(unittest.TestCase):
    """The report task is bounded by the declared timeout."""

    def test_a_hung_send_is_cancelled_by_the_timeout(self):
        from pyUltroid.agent import _reporting

        started = asyncio.Event()

        async def hanging_send_rich(client, channel, blocks):
            started.set()
            await asyncio.sleep(60)  # would outlive the test without the cap

        fake_client = types.SimpleNamespace()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            with (
                patch.object(_reporting, "_log_channel", return_value=777),
                patch.object(_reporting, "_client", return_value=fake_client),
                patch("pyUltroid._core.richmsg.send_rich", hanging_send_rich),
            ):
                task = _reporting.report_run(
                    origin=".ai",
                    chat_id=1,
                    user=None,
                    provider=None,
                    model=None,
                    prompt="hi",
                )
                results = loop.run_until_complete(
                    asyncio.gather(task, return_exceptions=True)
                )
        finally:
            asyncio.set_event_loop(None)
            loop.close()
        self.assertIsInstance(results[0], asyncio.TimeoutError)
        self.assertTrue(task.done())


if __name__ == "__main__":
    unittest.main()

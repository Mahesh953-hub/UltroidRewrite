"""Credential-free tests for the universal runtime layer."""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def universal_namespace():
    """Load isolated universal submodules without retaining sys.modules changes."""
    names = ("pyUltroid", "pyUltroid.universal", "pyUltroid.universal.adapters")
    previous = {name: sys.modules.get(name) for name in names}
    try:
        root = types.ModuleType("pyUltroid")
        root.__path__ = [str(ROOT / "pyUltroid")]
        sys.modules["pyUltroid"] = root
        spec = importlib.util.spec_from_file_location(
            "pyUltroid.universal",
            ROOT / "pyUltroid" / "universal" / "__init__.py",
            submodule_search_locations=[str(ROOT / "pyUltroid" / "universal")],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["pyUltroid.universal"] = module
        spec.loader.exec_module(module)
        yield
    finally:
        for name in list(sys.modules):
            if name.startswith("pyUltroid.universal"):
                del sys.modules[name]
        for name, module in previous.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


class MemoryDB:
    def __init__(self):
        self.data = {}

    def get_key(self, key):
        return self.data.get(key)

    def set_key(self, key, value):
        self.data[key] = value


class Dispatcher:
    def __init__(self):
        self.commands = {}
        self.listeners = []

    def command(self, name, **kwargs):
        def decorate(func):
            self.commands[name] = func
            return func

        return decorate

    def listen(self, update_type, **kwargs):
        def decorate(func):
            self.listeners.append((update_type, kwargs, func))
            return func

        return decorate


class FakeClient:
    def __init__(self, *, bot=False):
        self.me = types.SimpleNamespace(bot=bot, username="assistant" if bot else None)
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)


class UniversalRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.namespace = universal_namespace()
        self.namespace.__enter__()
        self.mode = importlib.import_module("pyUltroid.universal.adapters.mode")
        self.managed = importlib.import_module(
            "pyUltroid.universal.adapters.managed_bots"
        )
        self.inline = importlib.import_module("pyUltroid.universal.adapters.inline")
        self.discovery = importlib.import_module(
            "pyUltroid.universal.adapters.discovery"
        )
        self.plugin = importlib.import_module("pyUltroid.universal.plugin")
        self.runtime = importlib.import_module("pyUltroid.universal.runtime")
        self.storage = importlib.import_module("pyUltroid.universal.storage")
        self.tl = importlib.import_module("pyUltroid.universal.tl")
        self.universal = importlib.import_module("pyUltroid.universal")

    def tearDown(self):
        self.namespace.__exit__(None, None, None)

    def context(
        self,
        *,
        capabilities=None,
        dispatcher=None,
        client=None,
        assistant=None,
        db=None,
    ):
        client = client or FakeClient()
        return self.runtime.RuntimeContext(
            client=client,
            assistant=(
                assistant
                if assistant is not None
                else types.SimpleNamespace(
                    me=types.SimpleNamespace(username="assistant")
                )
            ),
            user=None,
            app=None,
            db=db or MemoryDB(),
            dispatcher=dispatcher or Dispatcher(),
            settings=self.runtime.RuntimeSettings(),
            capabilities=capabilities or self.tl.RuntimeCapabilities(),
        )

    def test_mode_and_legacy_cache_time_are_persisted_and_migrated(self):
        db = MemoryDB()
        store = self.mode.ModeStore(db)
        self.assertEqual(store.get_mode(), self.mode.BotMode.BUSINESS)
        store.set_mode(self.mode.BotMode.GUEST)
        store.update_guest_config(show_buttons=False, result_id_interval=60)
        restored = self.mode.ModeStore(db)
        self.assertEqual(restored.get_mode(), self.mode.BotMode.GUEST)
        self.assertFalse(restored.guest_config()["show_buttons"])
        self.assertEqual(restored.guest_config()["result_id_interval"], 60)
        db.set_key("UNIVERSAL_GUEST_CONFIG", {"cache_time": 45})
        migrated = self.mode.ModeStore(db).guest_config()
        self.assertEqual(migrated["result_id_interval"], 45)
        self.assertNotIn("cache_time", db.get_key("UNIVERSAL_GUEST_CONFIG"))

    def test_storage_type_mismatch_uses_default(self):
        db = MemoryDB()
        db.set_key("wrong", ["not", "a", "dict"])
        store = self.storage.UDBStore(db, "wrong", {"safe": True})
        self.assertEqual(store.get(), {"safe": True})

    def test_managed_metadata_never_persists_tokens(self):
        db = MemoryDB()
        bots = self.managed.ManagedBotStore(db)
        bots.record(42, username="safe_bot", token="must-not-persist")
        saved = bots.all()["42"]
        self.assertEqual(saved["username"], "safe_bot")
        self.assertNotIn("token", saved)

    def test_deep_link_and_capability_fallback_are_dependency_free(self):
        self.assertEqual(
            self.tl.managed_bot_deep_link("@manager", "my bot"),
            "https://t.me/newbot/manager/my%20bot",
        )
        detected = self.tl.detect_runtime_capabilities()
        try:
            from telethon import functions, types
        except ImportError:
            # Bare environment (no Telethon): detection must degrade to the
            # all-False fallback surface instead of raising.
            self.assertEqual(detected, self.tl.RuntimeCapabilities())
        else:
            # Telethon importable: detection must agree with a direct symbol
            # probe of the installed runtime and never raise. The original
            # all-False assertion only holds when Telethon is absent, so gate
            # on the environment instead of assuming it (the failure this
            # fixes). The topology fallback for a primary-user client without
            # an assistant still collapses every capability to False.
            self.assertEqual(detected, self.tl.capabilities(types, functions))
            fallback = self.tl.for_client_topology(
                detected, client=FakeClient(bot=False), assistant=None
            )
            self.assertEqual(fallback, self.tl.RuntimeCapabilities())

    def test_topology_disables_bot_features_for_primary_user_fallback(self):
        detected = self.tl.RuntimeCapabilities(True, True, True, True)
        fallback = self.tl.for_client_topology(
            detected, client=FakeClient(bot=False), assistant=None
        )
        self.assertEqual(fallback, self.tl.RuntimeCapabilities())
        bot = FakeClient(bot=True)
        self.assertEqual(
            self.tl.for_client_topology(detected, client=bot, assistant=bot), detected
        )

    def test_context_authorization_uses_owner_and_sudos(self):
        db = MemoryDB()
        db.set_key("OWNER_ID", 10)
        db.set_key("SUDOS", [20])
        context = self.context(db=db)
        self.assertTrue(context.is_authorized(10))
        self.assertTrue(context.is_authorized(20))
        self.assertFalse(context.is_authorized(30))

    def test_lifecycle_order_idempotency_and_legacy_callable_adapter(self):
        events = []
        dispatcher = Dispatcher()
        context = self.context(dispatcher=dispatcher)

        class Plugin:
            id = "test"

            def register(self, context):
                events.append("register")

            async def start(self, context):
                events.append("start")

            async def stop(self, context):
                events.append("stop")

        legacy = self.plugin.LegacyPluginAdapter(lambda value: events.append(value))
        lifecycle = self.plugin.register_plugins(context, [Plugin(), legacy])
        self.assertEqual(events, ["register", dispatcher])
        asyncio.run(lifecycle.start())
        asyncio.run(lifecycle.start())
        asyncio.run(lifecycle.stop())
        asyncio.run(lifecycle.stop())
        self.assertEqual(events, ["register", dispatcher, "start", "stop"])
        with self.assertRaises(TypeError):
            self.plugin.LegacyPluginAdapter(object())

    def test_installer_wires_once_and_topology_gates_context(self):
        client = FakeClient(bot=False)
        dispatcher = Dispatcher()
        plugin = types.SimpleNamespace(id="one", register=lambda context: None)
        detected = self.tl.RuntimeCapabilities(guest_chat=True, inline_query=True)
        first = self.universal.install_universal_runtime(
            client=client,
            assistant=None,
            user=None,
            app=None,
            db=MemoryDB(),
            dispatcher=dispatcher,
            settings=self.runtime.RuntimeSettings(),
            plugins=[plugin],
            runtime_capabilities=detected,
        )
        second = self.universal.install_universal_runtime(
            client=client,
            assistant=None,
            user=None,
            app=None,
            db=MemoryDB(),
            dispatcher=Dispatcher(),
            settings=self.runtime.RuntimeSettings(),
        )
        self.assertIs(first, second)
        self.assertEqual(
            client.universal_context.capabilities, self.tl.RuntimeCapabilities()
        )
        self.discovery.DiscoveryPlugin().register(client.universal_context)
        output = asyncio.run(dispatcher.commands["universal"](None, "", None))
        self.assertIn("Guest chat: unavailable", output)
        self.assertIn("Managed-bot updates: unavailable", output)
        self.assertIn("Token rotation: unavailable", output)
        self.assertIn("Inline query: unavailable", output)

    def test_command_parsing_branches(self):
        store = self.mode.ModeStore(MemoryDB())
        self.assertIsNone(self.mode._parse_command(store, "guest"))
        self.assertEqual(store.get_mode(), self.mode.BotMode.GUEST)
        self.assertIsNone(self.mode._parse_command(store, "buttons off"))
        self.assertFalse(store.guest_config()["show_buttons"])
        self.assertIsNone(self.mode._parse_command(store, "interval 12"))
        self.assertEqual(store.guest_config()["result_id_interval"], 12)
        self.assertIsNone(self.mode._parse_command(store, "cache 13"))
        self.assertEqual(store.guest_config()["result_id_interval"], 13)
        self.assertIn("Interval", self.mode._parse_command(store, "interval nope"))
        self.assertIn("Usage", self.mode._parse_command(store, "unknown"))
        store.update_guest_config(result_id_interval=None)
        self.assertEqual(
            self.mode._interval(store.guest_config()),
            self.mode.DEFAULT_GUEST_CONFIG["result_id_interval"],
        )

    def test_url_markup_off_or_missing_username(self):
        fake_types = types.SimpleNamespace(
            KeyboardInlineButtonRow=lambda **kwargs: ("row", kwargs),
            KeyboardInlineButton=lambda *args: ("button", args),
            InlineButtonTypeUrl=lambda *args: ("url", args),
            ReplyInlineMarkup=lambda **kwargs: ("markup", kwargs),
        )
        context = self.context()
        self.assertIsNone(self.mode._safe_url_markup(context, fake_types, False))
        context.assistant = types.SimpleNamespace(
            me=types.SimpleNamespace(username=None)
        )
        self.assertIsNone(self.mode._safe_url_markup(context, fake_types, True))

    def test_guest_mode_registers_only_when_supported_and_responds_only_in_guest_mode(
        self,
    ):
        query_type = type("GuestQuery", (), {})
        fake_types = types.SimpleNamespace(
            UpdateBotGuestChatQuery=query_type,
            InputBotInlineResult=lambda **kwargs: ("result", kwargs),
            InputBotInlineMessageRichMessage=lambda **kwargs: (
                "rich-message",
                kwargs,
            ),
            InputRichMessageHTML=lambda **kwargs: ("rich-html", kwargs),
        )
        fake_functions = types.SimpleNamespace(
            messages=types.SimpleNamespace(
                SetBotGuestChatResultRequest=lambda **kwargs: ("request", kwargs)
            )
        )
        dispatcher, client = Dispatcher(), FakeClient(bot=True)
        context = self.context(
            capabilities=self.tl.RuntimeCapabilities(guest_chat=True),
            dispatcher=dispatcher,
            client=client,
            assistant=client,
        )
        with patch.object(
            self.mode, "guest_chat_surfaces", return_value=(fake_types, fake_functions)
        ):
            self.mode.ModePlugin().register(context)
        handler = dispatcher.listeners[0][2]
        asyncio.run(handler(types.SimpleNamespace(query_id=1)))
        self.assertEqual(client.requests, [])
        asyncio.run(dispatcher.commands["mode"](None, "guest", None))
        asyncio.run(handler(types.SimpleNamespace(query_id=2)))
        result = client.requests[-1][1]["result"]
        self.assertEqual(result[1]["title"], "Chat with Bot")
        self.assertEqual(result[1]["description"], "Tap to start a conversation")
        send_message = result[1]["send_message"]
        self.assertEqual(send_message[0], "rich-message")
        self.assertEqual(
            send_message[1]["rich_message"],
            (
                "rich-html",
                {"html": "<h3>Chat with Bot</h3><p>Welcome to Guest Chat!</p>"},
            ),
        )

    def test_mode_status_reports_unavailable_guest_capability(self):
        dispatcher = Dispatcher()
        context = self.context(dispatcher=dispatcher)
        with patch.object(self.mode, "guest_chat_surfaces", return_value=None):
            self.mode.ModePlugin().register(context)
        self.assertEqual(dispatcher.listeners, [])
        status = asyncio.run(dispatcher.commands["mode"](None, "status", None))
        self.assertIn("unavailable", status)

    def test_inline_handler_requires_owner_or_sudo_and_capability(self):
        update_type = type("InlineQuery", (), {})
        fake_types = types.SimpleNamespace(
            UpdateBotInlineQuery=update_type,
            InputBotInlineResult=lambda **kwargs: ("result", kwargs),
            InputBotInlineMessageText=lambda **kwargs: ("message", kwargs),
        )
        fake_functions = types.SimpleNamespace(
            messages=types.SimpleNamespace(
                SetInlineBotResultsRequest=lambda **kwargs: ("request", kwargs)
            )
        )
        db, dispatcher, client = MemoryDB(), Dispatcher(), FakeClient(bot=True)
        db.set_key("OWNER_ID", 1)
        context = self.context(
            capabilities=self.tl.RuntimeCapabilities(inline_query=True),
            dispatcher=dispatcher,
            client=client,
            assistant=client,
            db=db,
        )
        with patch.object(
            self.inline, "inline_surfaces", return_value=(fake_types, fake_functions)
        ):
            self.inline.InlineInfoPlugin().register(context)
        handler = dispatcher.listeners[0][2]
        asyncio.run(handler(types.SimpleNamespace(user_id=2, query_id=10)))
        self.assertEqual(client.requests, [])
        asyncio.run(handler(types.SimpleNamespace(user_id=1, query_id=11)))
        self.assertEqual(client.requests[-1][0], "request")

    def test_managed_command_paths_and_rotation_signature_failure(self):
        dispatcher, client = Dispatcher(), FakeClient(bot=True)
        context = self.context(
            capabilities=self.tl.RuntimeCapabilities(token_rotation=True),
            dispatcher=dispatcher,
            client=client,
            assistant=client,
        )
        self.managed.ManagedBotsPlugin().register(context)
        command = dispatcher.commands["managedbot"]
        self.assertIn("No managed", asyncio.run(command(None, "", None)))
        self.assertIn("Usage", asyncio.run(command(None, "rotate nope", None)))
        self.assertIn("managed-bot flow", asyncio.run(command(None, "create", None)))
        # Rotation is destructive and one-way, so a bare ``rotate BOT_ID``
        # must warn instead of revoking the token.
        warning = asyncio.run(command(None, "rotate 1", None))
        self.assertIn("invalidates the bot's current token", warning)
        self.assertIn("confirm", warning)
        with patch.object(
            self.managed,
            "functions",
            types.SimpleNamespace(bots=types.SimpleNamespace()),
        ):
            self.assertIn(
                "does not support",
                asyncio.run(command(None, "rotate 1 confirm", None)),
            )

        class Request:
            """Mimics the pinned fork's ``bots.ExportBotTokenRequest``."""

            def __init__(self, *, bot, revoke):
                self.bot = bot
                self.revoke = revoke

        narrowed = types.SimpleNamespace(
            bots=types.SimpleNamespace(ExportBotTokenRequest=Request)
        )
        # ``rotated`` is truthy on the happy path, and the request carries only
        # the bot id + ``revoke=True`` — never a returned token.
        rotated = asyncio.run(self.tl.rotate_managed_bot_token(client, 1, narrowed))
        self.assertTrue(rotated)
        request = client.requests[-1]
        self.assertEqual(request.bot, 1)
        self.assertTrue(request.revoke)

        # The command reaches the same call only with the explicit
        # confirmation, and it states the one-way consequence.
        with patch.object(self.managed, "functions", narrowed):
            reply = asyncio.run(command(None, "rotate 1 confirm", None))
        self.assertIn("Token revoked", reply)
        self.assertIn("re-issue a token", reply)

    def test_rotate_managed_bot_token_reports_unsupported_symbol(self):
        client = FakeClient(bot=True)
        functions = types.SimpleNamespace(bots=types.SimpleNamespace())
        self.assertFalse(
            asyncio.run(self.tl.rotate_managed_bot_token(client, 1, functions))
        )

    def test_discovery_output_reports_all_capabilities(self):
        dispatcher = Dispatcher()
        caps = self.tl.RuntimeCapabilities(True, True, True, True)
        self.discovery.DiscoveryPlugin().register(
            self.context(capabilities=caps, dispatcher=dispatcher)
        )
        output = asyncio.run(dispatcher.commands["universal"](None, "", None))
        self.assertIn("Guest chat: available", output)
        self.assertIn("Managed-bot updates: available", output)
        self.assertIn("Token rotation: available", output)
        self.assertIn("Inline query: available", output)


class UniversalUnionTests(unittest.TestCase):
    """The union is the entry point; client.universal_plugins stays for compat."""

    def setUp(self):
        self._ctx = universal_namespace()
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self.uni = sys.modules["pyUltroid.universal"]

    def _install(self, caps=None, db=None, bot=False):
        return self.uni.install_universal_runtime(
            client=FakeClient(bot=bot),
            assistant=None,
            user=None,
            app=None,
            db=db,
            dispatcher=Dispatcher(),
            settings=self.uni.RuntimeSettings(),
            plugins=[],
            runtime_capabilities=caps or self.uni.RuntimeCapabilities(),
        )

    def _install_bot(self, caps=None):
        """Capabilities survive only when the client *is* the assistant bot."""
        client = FakeClient(bot=True)
        return self.uni.install_universal_runtime(
            client=client,
            assistant=client,
            user=None,
            app=None,
            db=MemoryDB(),
            dispatcher=Dispatcher(),
            settings=self.uni.RuntimeSettings(),
            plugins=[],
            runtime_capabilities=caps or self.uni.RuntimeCapabilities(),
        )

    def test_install_exposes_one_universal_object(self):
        lifecycle = self._install()
        uni = self.uni.universal()
        self.assertIsInstance(uni, self.uni.Universal)
        self.assertIs(uni.lifecycle, lifecycle)
        self.assertIs(uni.context, uni.client.universal_context)

    def test_universal_delegates_the_common_path(self):
        # for_client_topology() gates capabilities off for a non-bot primary
        # client, so detect on the bot client to keep the flags on.
        self._install_bot(caps=self.uni.RuntimeCapabilities(styled_buttons=True))
        uni = self.uni.universal()
        self.assertTrue(uni.supports("styled_buttons"))
        self.assertFalse(uni.supports("guest_chat"))
        self.assertEqual(uni.plugins(), ())

    def test_authorized_delegates_to_the_context(self):
        db = MemoryDB()
        db.set_key("OWNER_ID", 10)
        self._install(db=db)
        uni = self.uni.universal()
        self.assertFalse(uni.authorized(None))
        self.assertTrue(uni.authorized(10))

    def test_store_is_typed_and_falls_back_on_a_mismatch(self):
        db = MemoryDB()
        self._install(db=db)
        store = self.uni.universal().store("KEY", {})
        self.assertEqual(store.get(), {})
        store.set({"a": 1})
        self.assertEqual(store.get(), {"a": 1})
        self.assertEqual(self.uni.UDBStore(None, "KEY", {}).get(), {})

    def test_universal_raises_before_installation(self):
        self.uni._UNIVERSAL = None
        with self.assertRaisesRegex(RuntimeError, "not installed"):
            self.uni.universal()


if __name__ == "__main__":
    unittest.main()


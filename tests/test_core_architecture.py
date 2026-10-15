"""Dependency-light regression tests for the consolidated private core."""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import tempfile
import time
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from telethon import TelegramClient
from telethon.tl import types as tl_types

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def core_namespace():
    """Import core modules without starting the production pyUltroid bootstrap."""
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "pyUltroid" or name.startswith("pyUltroid.")
    }
    for name in list(saved):
        sys.modules.pop(name, None)
    try:
        root = types.ModuleType("pyUltroid")
        root.__path__ = [str(ROOT / "pyUltroid")]
        root.LOGS = logging.getLogger("core-tests")
        root.HNDLR = "."
        root.udB = None
        root.asst = None
        root.ultroid_bot = None
        sys.modules["pyUltroid"] = root

        startup = types.ModuleType("pyUltroid.startup")
        startup.__path__ = []
        logger = types.ModuleType("pyUltroid.startup.logger")
        logger.LOGS = root.LOGS
        sys.modules["pyUltroid.startup"] = startup
        sys.modules["pyUltroid.startup.logger"] = logger

        fns = types.ModuleType("pyUltroid.fns")
        fns.__path__ = []
        tools = types.ModuleType("pyUltroid.fns.tools")
        tools.get_all_files = lambda directory, suffix: [
            str(path) for path in Path(directory).rglob(f"*{suffix}")
        ]
        sys.modules["pyUltroid.fns"] = fns
        sys.modules["pyUltroid.fns.tools"] = tools

        database = types.ModuleType("pyUltroid.dB")
        database.__path__ = []
        database_core = types.ModuleType("pyUltroid.dB._core")
        database_core.ADDONS = []
        database_core.HELP = {}
        database_core.LIST = {}
        database_core.LOADED = {}
        sys.modules["pyUltroid.dB"] = database
        sys.modules["pyUltroid.dB._core"] = database_core
        yield root, database_core
    finally:
        for name in list(sys.modules):
            if name == "pyUltroid" or name.startswith("pyUltroid."):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


class FakeClient:
    def __init__(self):
        self.handlers = []
        self.removed = []

    def add_event_handler(self, handler, event=None):
        self.handlers.append((handler, event))

    def remove_event_handler(self, handler):
        self.removed.append(handler)


class CoreArchitectureTests(unittest.TestCase):
    def setUp(self):
        self.namespace = core_namespace()
        self.root, self.database = self.namespace.__enter__()
        self.loader = importlib.import_module("pyUltroid._core.loader")
        self.client = importlib.import_module("pyUltroid._core.client")
        self.cards = importlib.import_module("pyUltroid._core.cards")
        self.events = importlib.import_module("pyUltroid._core.events")

    def tearDown(self):
        self.namespace.__exit__(None, None, None)

    def test_master_client_class_identity(self):
        # the deprecated root re-export shims (pyUltroid.loader/client) are
        # gone; the meaningful identity left: the core union's loader IS an
        # AsyncPluginLoader, so the union and the module agree on one class.
        union = importlib.import_module("pyUltroid.ultroid").core
        self.assertIsInstance(union.loader, self.loader.AsyncPluginLoader)
        self.assertIs(union.registry.__class__, self.loader.PluginRegistry)


    def test_registry_exposes_immutable_snapshot_and_mutable_legacy_set(self):
        registry = self.loader.PluginRegistry()
        registry.register(
            self.loader.PluginManifest("working", status=self.loader.STATUS_ACTIVE)
        )
        snapshot = registry.names
        self.assertIsInstance(snapshot, frozenset)
        self.assertEqual(snapshot, frozenset({"working"}))
        self.assertIn("working", self.loader.ALL_PLUGINS)
        registry.unregister("working")
        self.assertEqual(snapshot, frozenset({"working"}))
        self.assertNotIn("working", self.loader.ALL_PLUGINS)

    def test_compile_failure_is_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            bad = Path(directory) / "bad.py"
            good = Path(directory) / "good.py"
            bad.write_text("def broken(:\n")
            good.write_text("pass\n")
            loader = self.loader.AsyncPluginLoader(directory)
            loaded = asyncio.run(
                loader.load_all(func=lambda _: types.SimpleNamespace())
            )
        self.assertEqual(loaded[:2], (1, 1))
        self.assertTrue(self.loader.registry.get("bad").is_errored)
        self.assertTrue(self.loader.registry.get("good").is_active)

    def test_load_all_serializes_import_registration(self):
        calls, active, max_active = [], 0, 0

        def importing(name):
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            calls.append(name)
            time.sleep(0.01)
            active -= 1
            return types.SimpleNamespace()

        with tempfile.TemporaryDirectory() as directory:
            for name in ("one", "two", "three"):
                Path(directory, f"{name}.py").write_text("pass\n")
            result = asyncio.run(
                self.loader.AsyncPluginLoader(directory).load_all(func=importing)
            )
        self.assertEqual(result[:2], (3, 0))
        self.assertEqual(max_active, 1)
        self.assertEqual(len(calls), 3)

    def test_unload_removes_only_handlers_owned_by_plugin(self):
        owner = FakeClient()
        other = FakeClient()
        self.root.ultroid_bot = owner
        self.root.asst = other

        def mine():
            pass

        def theirs():
            pass

        manifest = self.loader.PluginManifest(
            "plugins.owned",
            path="plugins/owned.py",
            status=self.loader.STATUS_ACTIVE,
            handlers=[(owner, mine, "owned-event")],
        )
        self.loader.registry.register(manifest)
        outcome = self.loader.AsyncPluginLoader()._do_unload("plugins.owned")
        self.assertTrue(outcome["success"])
        self.assertIn(mine, owner.removed)
        self.assertNotIn(theirs, owner.removed)

    def test_help_preserves_text_when_format_string_is_malformed(self):
        text = "Use {i}ok then {missing"
        self.assertEqual(
            self.loader.PluginHelp.format_help(text, "!"), "Use !ok then {missing"
        )

    def test_master_client_routes_normal_business_and_bot_mode(self):
        calls = []

        class Routed:
            def __init__(self, name):
                self.name = name

            async def send_message(self, *args, **kwargs):
                calls.append((self.name, args, kwargs))
                return self.name

        bot, user = Routed("bot"), Routed("user")
        master = self.client.MasterClient(bot=bot, user=user)
        self.assertEqual(asyncio.run(master.send_message(1, "normal")), "user")
        with patch.object(
            self.client.MasterClient, "_business_connection_id", return_value="business"
        ):
            self.assertEqual(asyncio.run(master.send_message(1, "business")), "bot")
        self.assertEqual(
            asyncio.run(self.client.MasterClient(bot=bot).send_message(1, "bot mode")),
            "bot",
        )
        self.assertEqual([entry[0] for entry in calls], ["user", "bot", "bot"])

    def _greeting_entities(self):
        user = types.SimpleNamespace(
            id=42,
            first_name="Ada",
            last_name="Lovelace",
            username="ada",
            title=None,
        )
        chat = types.SimpleNamespace(title="Otaku Club", participants_count=7)
        return user, chat

    def test_render_supports_legacy_and_notifier_placeholders(self):
        user, chat = self._greeting_entities()
        text = self.events.render_welcome(
            "{mention}|{group}|{chat}|{count}|{name}|{fullname}|{username}|{userid}",
            user,
            chat,
            7,
        )
        mention = "[Ada](tg://user?id=42)"
        self.assertEqual(
            text,
            f"{mention}|Otaku Club|Otaku Club|7|Ada|Ada Lovelace|@ada|42",
        )

    def test_render_short_name_uses_a_real_users_first_name(self):
        # {name} and {mention} are documented as the short name, so they must
        # stay first-name-only for a real Telethon User even though
        # telethon.utils.get_display_name would return the full name.
        real_user = tl_types.User(
            id=42,
            is_self=False,
            access_hash=1,
            first_name="Ada",
            last_name="Lovelace",
            username="ada",
        )
        real_chat = tl_types.Chat(
            id=7,
            title="Otaku Club",
            photo=tl_types.ChatPhotoEmpty(),
            participants_count=7,
            date=None,
            version=0,
        )
        self.assertEqual(
            self.events.render_welcome("{name}|{mention}", real_user, real_chat, 1),
            "Ada|[Ada](tg://user?id=42)",
        )
        self.assertEqual(
            self.events.render_welcome("{name}", real_user, real_chat, 1),
            "Ada",
        )
        self.assertEqual(self.events._full_name(real_user), "Ada Lovelace")

    def test_render_leaves_unknown_placeholders_literal(self):
        user, chat = self._greeting_entities()
        self.assertEqual(
            self.events.render_welcome("Hi {name} {unknown}", user, chat, 3),
            "Hi Ada {unknown}",
        )

    def test_render_count_falls_back_to_N_and_username_to_mention(self):
        user = types.SimpleNamespace(
            id=9,
            first_name="Bob",
            last_name=None,
            username=None,
            title=None,
        )
        chat = types.SimpleNamespace(title=None)
        text = self.events.render_welcome("{count} {username} {group}", user, chat, 0)
        self.assertEqual(text, "N [Bob](tg://user?id=9) this chat")

    def test_render_farewell_falls_back_to_farewell_default(self):
        user, chat = self._greeting_entities()
        text = self.events.render_farewell("{broken", user, chat, 1)
        self.assertEqual(text, "👋 Goodbye Ada, **Otaku Club** will miss you!")

    def test_render_welcome_falls_back_to_welcome_default(self):
        user, chat = self._greeting_entities()
        text = self.events.render_welcome("{broken", user, chat, 7)
        self.assertIn("Welcome [Ada](tg://user?id=42)", text)
        self.assertIn("#7", text)

    def test_inline_result_cache_ttl_lifecycle(self):
        cache = self.events.InlineResultCache()
        cache.set("k", "v", ttl=60)
        self.assertTrue(cache.has("k"))
        self.assertEqual(cache.get("k"), "v")
        self.assertIn("k", cache)
        cache.delete("k")
        self.assertIsNone(cache.get("k"))
        cache.set("x", 1, ttl=60)
        cache.clear()
        self.assertIsNone(cache.get("x"))
        cache.set("old", "v", ttl=-1)  # already expired
        self.assertIsNone(cache.get("old"))
        self.assertNotIn("old", cache._store)

    def test_inline_result_builder_builds_answers_and_paginates(self):
        calls = {}

        class FakeBuilder:
            def article(self, **kwargs):
                calls["article"] = kwargs
                return ("article", kwargs)

        class FakeEvent:
            def __init__(self):
                self.builder = FakeBuilder()

            async def answer(self, results, cache_time=0, **kwargs):
                calls["answer"] = (list(results), cache_time, kwargs)
                return results

        ib = self.events.InlineResultBuilder(FakeEvent())
        result = asyncio.run(ib.article(title="T", text="x"))
        self.assertEqual(result[0], "article")
        self.assertEqual(calls["article"], {"title": "T", "text": "x"})
        asyncio.run(
            ib.answer(
                [1, 2, 3],
                per_page=2,
                switch_pm="Go",
                switch_pm_param="start",
                cache_time=10,
                private=True,
            )
        )
        results, cache_time, extra = calls["answer"]
        self.assertEqual(results, [1, 2])
        self.assertEqual(cache_time, 10)
        self.assertEqual(
            extra,
            {"switch_pm": "Go", "switch_pm_param": "start", "private": True},
        )
        self.assertEqual(
            self.events.InlineResultBuilder.paginate(list(range(120)), page=1),
            list(range(50, 100)),
        )
        self.assertEqual(self.events.InlineResultBuilder.paginate([1, 2], page=5), [])
        self.assertEqual(
            self.events.InlineResultBuilder.page_count(list(range(120))), 3
        )
        self.assertEqual(self.events.InlineResultBuilder.page_count([]), 1)

    def test_inline_result_builder_answer_requires_event(self):
        ib = self.events.InlineResultBuilder(types.SimpleNamespace())
        with self.assertRaises(RuntimeError):
            asyncio.run(ib.answer([]))

    def test_build_menu_grid_chunks_and_coerces_button_specs(self):
        fake_telethon = types.ModuleType("telethon")
        fake_telethon.Button = types.SimpleNamespace(
            inline=lambda text, data: ("inline", text, data),
            url=lambda text, url: ("url", text, url),
        )
        with patch.dict(sys.modules, {"telethon": fake_telethon}):
            # Ready-made buttons pass through untouched (legacy page_num parity
            # with split_list(List, cols)).
            rows = self.events.build_menu_grid(["a", "b", "c", "d", "e"], columns=2)
            self.assertEqual(rows, [["a", "b"], ["c", "d"], ["e"]])
            rows = self.events.build_menu_grid(
                [("Yes", b"y"), ("Site", "url", "https://x")],
                columns=2,
            )
            self.assertEqual(
                rows,
                [[("inline", "Yes", b"y"), ("url", "Site", "https://x")]],
            )

    def test_duplicate_stems_have_canonical_ids_and_ambiguous_unload_refuses(self):
        registry = self.loader.PluginRegistry()
        registry.register(
            self.loader.PluginManifest(
                "plugins.afk", path="plugins/afk.py", status=self.loader.STATUS_ACTIVE
            )
        )
        registry.register(
            self.loader.PluginManifest(
                "assistant.manager.afk",
                path="assistant/manager/afk.py",
                status=self.loader.STATUS_ACTIVE,
            )
        )
        self.assertEqual(
            registry.candidates("afk"), ("assistant.manager.afk", "plugins.afk")
        )
        self.assertIsNone(registry.get("afk"))
        old = self.loader.registry
        self.loader.registry = registry
        try:
            result = self.loader.AsyncPluginLoader()._do_unload("afk")
        finally:
            self.loader.registry = old
        self.assertFalse(result["success"])
        self.assertEqual(result["candidates"], ["assistant.manager.afk", "plugins.afk"])

    def test_import_snapshot_owns_only_new_handler(self):
        client = FakeClient()
        client.list_event_handlers = lambda: list(client.handlers)
        self.root.ultroid_bot = client
        self.root.asst = None

        def imported(_):
            def callback():
                pass

            client.add_event_handler(callback, "event")
            return types.SimpleNamespace()

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "tracked.py")
            path.write_text("pass\n")
            manifest = asyncio.run(
                self.loader.AsyncPluginLoader().load_plugin(str(path), func=imported)
            )
        self.assertEqual(len(manifest.handlers), 1)
        outcome = self.loader.AsyncPluginLoader()._do_unload(manifest.name)
        self.assertTrue(outcome["success"])
        self.assertEqual(len(client.removed), 1)

    def test_persisted_error_paths_are_normalized_and_retried(self):
        class DB:
            def __init__(self):
                self.data = {"ERRORED_PLUGINS": '["plugins/retry.py"]'}

            def get_key(self, key):
                return self.data.get(key)

            def set_key(self, key, value):
                self.data[key] = value

        self.root.udB = DB()
        self.assertEqual(self.loader._load_errored_paths(), ["plugins/retry.py"])
        with patch.object(
            self.loader._default_loader,
            "load_plugin",
            return_value=types.SimpleNamespace(is_active=True),
        ) as load:
            asyncio.run(
                self.loader.retry_persisted_errors(
                    max_retries=1, func=lambda _: types.SimpleNamespace()
                )
            )
        self.assertTrue(load.called)
        self.assertIsInstance(self.root.udB.data["ERRORED_PLUGINS"], list)

    def test_master_client_origin_and_values_are_not_wrapped(self):
        bot = types.SimpleNamespace(flag="bot")
        user = types.SimpleNamespace(flag="user")
        master = self.client.MasterClient(bot=bot, user=user)
        self.assertEqual(master.flag, "user")
        with self.client.bind_origin(bot):
            self.assertEqual(master.flag, "bot")

    def test_origin_facade_routes_event_client_master_to_assistant(self):
        calls = []

        class Routed:
            async def send_message(self, *args):
                calls.append(self)

        bot, user = Routed(), Routed()
        facade = self.client.OriginMasterClient(
            self.client.MasterClient(bot=bot, user=user), bot
        )
        asyncio.run(facade.send_message(1, "assistant"))
        self.assertEqual(calls, [bot])

    def test_duplicate_canonical_id_never_replaces_existing_manifest(self):
        registry = self.loader.PluginRegistry()
        first = self.loader.PluginManifest("plugins.same", path="plugins/same.py")
        second = self.loader.PluginManifest(
            "plugins.same", path="plugins/same.py", author="second"
        )
        self.assertTrue(registry.register(first))
        self.assertFalse(registry.register(second))
        self.assertIs(registry.get("plugins.same"), first)

    def test_core_package_is_lazy(self):
        for name in list(sys.modules):
            if name.startswith("pyUltroid._core"):
                sys.modules.pop(name)
        core = importlib.import_module("pyUltroid._core")
        self.assertEqual(core.__all__, ())
        self.assertNotIn("pyUltroid._core.cards", sys.modules)

    def test_cards_return_none_when_pillow_is_unavailable(self):
        original_import = __import__

        def no_pillow(name, *args, **kwargs):
            if name == "PIL" or name.startswith("PIL."):
                raise ImportError("Pillow intentionally unavailable")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=no_pillow):
            self.assertIsNone(self.cards.generate_error_card("broken", save=False))
            self.assertIsNone(self.cards.generate_now_playing("track", "artist", 1, 0))


if __name__ == "__main__":
    unittest.main()
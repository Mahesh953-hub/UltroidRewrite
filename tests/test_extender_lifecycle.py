"""W1-D regression tests: HRE (extender) lifecycle wiring into the loader.

Covers the four W1-D work items:

* W1-D.1 — ``hre.on_plugin_load`` / ``on_plugin_unload`` honored by
  ``pyUltroid._core.loader``: manifests (with ``__res_tags__``) attach on
  load and detach on unload, ``__conflicts__`` hard-blocks the import,
  ``__optional__`` is recorded but never installed at load time.
* W1-D.2 — ``hre.status`` / ``hre.audit`` / ``hre.cleanup`` behave sanely
  once plugins are managed (the Telegram surfaces in ``plugins/core.py``
  are thin wrappers over exactly these calls).
* W1-D.3 — ``load_other_plugins`` bootstraps HRE and warms the tracer
  index (``plugin_index.index_all``) at startup; ``index_all`` picks up
  loaded ``plugins/`` modules and ignores non-plugin trees.
* W1-D.4 — ``confirm_unload`` drives a button-confirmed unload end to
  end, and ``plugins/core.py``'s ``.unload`` command is wired to it.

Hermeticity rules (per tests/README.md): no network, no database, no
Telegram credentials, and — critically — no real pip runs. HRE stays
bootstrap-gated, and every test that needs it active calls
``hre.bootstrap()`` explicitly; installs are intercepted with fakes.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import logging
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]

# A package that is genuinely not installed anywhere sane.
MISSING_DEP = "definitely-missing-dep-xyz123"


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
        root.LOGS = logging.getLogger("extender-lifecycle-tests")
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

        # The composition root wires the loader module's patch points
        # (registry/_default_loader) after the singleton exists; importing
        # it here against the stub root runs that wiring so the module
        # helpers read the union's registries, exactly like production.
        importlib.import_module("pyUltroid.ultroid")
        yield root, database_core
    finally:
        for name in list(sys.modules):
            if name == "pyUltroid" or name.startswith("pyUltroid."):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


class _FakeMessage:
    def __init__(self):
        self.id = 1
        self.sender = types.SimpleNamespace(id=4242)
        self.edits = []

    async def edit(self, text, **kwargs):
        self.edits.append(text)


class _FakeClient:
    """Minimal Telethon-client stand-in for confirm_unload."""

    def __init__(self):
        self.messages = []
        self.callbacks = []

    async def send_message(self, chat_id, text, buttons=None):
        msg = _FakeMessage()
        self.messages.append((chat_id, text, buttons, msg))
        return msg

    def add_event_handler(self, callback, event=None):
        self.callbacks.append(callback)

    def remove_event_handler(self, callback, event=None):
        if callback in self.callbacks:
            self.callbacks.remove(callback)


def _callback_event(data: bytes, sender_id: int = 4242):
    return types.SimpleNamespace(
        sender_id=sender_id,
        data=data,
        answer=mock.AsyncMock(),
    )


class ExtenderLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.namespace = core_namespace()
        self.root, self.database = self.namespace.__enter__()
        self.loader = importlib.import_module("pyUltroid._core.loader")
        self.manifest_mod = importlib.import_module("pyUltroid.extender._manifest")
        self.manager_mod = importlib.import_module("pyUltroid.extender._manager")
        self.installer_mod = importlib.import_module("pyUltroid.extender._installer")
        self.indexer_mod = importlib.import_module("pyUltroid.tracer._indexer")
        self.hre = self.manager_mod.hre
        self.registry = self.manifest_mod.manifest_registry

    def tearDown(self):
        self.namespace.__exit__(None, None, None)

    # ── helpers ──────────────────────────────────────────────────────────

    def _write_plugin(self, source: str, name: str = "fakeplug", subdir: str = ""):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        base = Path(directory.name)
        if subdir:
            base = base / subdir
            base.mkdir()
        path = base / f"{name}.py"
        path.write_text(source)
        return directory, path

    def _canonical(self, path) -> str:
        return self.loader._plugin_id(str(path))

    @staticmethod
    def _module(**attrs):
        return types.SimpleNamespace(**attrs)

    # ── W1-D.1: attach / detach across load & unload (acceptance) ───────

    def test_manifest_attaches_on_load_and_detaches_on_unload(self):
        """The W1-D acceptance criterion, via the async loader path."""
        self.hre.bootstrap()
        source = (
            '"""tagged plugin"""\n'
            '__requires__ = ["pillow>=10.0"]\n'
            '__res_tags__ = ["image", "media"]\n'
        )
        _, path = self._write_plugin(source, name="resp_tagged")
        name = self._canonical(path)
        fake_module = self._module(
            __doc__="tagged plugin", __requires__=["pillow>=10.0"]
        )
        loader = self.loader.AsyncPluginLoader(str(path.parent), key="Test")

        manifest = asyncio.run(
            loader.load_plugin(str(path), func=lambda _n: fake_module, key="Test")
        )
        self.assertTrue(manifest.is_active, manifest.error_info)

        # Attached: manifest registered, tags queryable.
        hre_manifest = self.registry.get(name)
        self.assertIsNotNone(hre_manifest)
        self.assertEqual(hre_manifest.tags, ["image", "media"])
        status = self.hre.status(name)
        self.assertEqual(status["tags"], ["image", "media"])
        self.assertTrue(status["is_loadable"])
        self.assertIn(name, self.hre.plugins_by_tag("image"))

        # Detached: unload removes the manifest and the tag mapping.
        result = asyncio.run(loader.unload_plugin(name))
        self.assertTrue(result["success"])
        self.assertIsNone(self.registry.get(name))
        self.assertEqual(self.hre.plugins_by_tag("image"), [])
        self.assertIsNone(self.hre.status(name))

    def test_reload_plugin_async_drives_a_real_reload(self):
        """The updater's hot-reload entry point must survive refactors.

        ``pyUltroid/updater/_engine.py`` calls ``reload_plugin_async()`` for every
        changed file. That is the only caller, so nothing else notices when the
        method underneath it disappears — the engine catches the exception and
        logs it, and the bot silently keeps running the old code.
        """
        self.hre.bootstrap()
        _, path = self._write_plugin(
            '"""reloadable plugin"""\n__res_tags__ = ["reload"]\n', name="resp_reload"
        )
        name = self._canonical(path)
        loader = self.loader.AsyncPluginLoader(str(path.parent), key="Test")
        module = self._module(__doc__="reloadable plugin")

        manifest = asyncio.run(
            loader.load_plugin(str(path), func=lambda _n: module, key="Test")
        )
        self.assertTrue(manifest.is_active, manifest.error_info)

        with (
            mock.patch.object(self.loader, "_default_loader", loader),
            mock.patch.object(
                self.loader, "_func_for_path", lambda _p: (lambda _n: module)
            ),
        ):
            result = asyncio.run(self.loader.reload_plugin_async(name, path=str(path)))

        self.assertTrue(result["success"], result)
        self.assertEqual(result["name"], name)
        self.assertIsNotNone(self.registry.get(name), "the reload must re-attach HRE")

    def test_sync_facade_also_attaches_and_detaches(self):
        """The legacy sync path (_load_sync / unload_plugin) honors HRE too."""
        self.hre.bootstrap()
        _, path = self._write_plugin(
            '"""tagged plugin"""\n__res_tags__ = ["legacy"]\n', name="resp_sync"
        )
        name = self._canonical(path)
        loader = self.loader.AsyncPluginLoader(str(path.parent), key="Test")
        manifest = loader._load_sync(
            str(path), func=lambda _n: self._module(__doc__="tagged plugin"), key="Test"
        )
        self.assertTrue(manifest.is_active, manifest.error_info)
        self.assertIn(name, self.hre.plugins_by_tag("legacy"))

        result = self.loader.unload_plugin(name)  # module-level sync facade
        self.assertTrue(result["success"])
        self.assertEqual(self.hre.plugins_by_tag("legacy"), [])

    def test_conflicts_hard_block_before_import(self):
        """An installed __conflicts__ package blocks the load; no import runs."""
        self.hre.bootstrap()
        _, path = self._write_plugin(
            '"""conflicting plugin"""\n__conflicts__ = ["pillow"]\n', name="resp_conf"
        )
        name = self._canonical(path)
        imported = []

        def func(_name):
            imported.append(True)
            return self._module(__doc__="conflicting plugin")

        loader = self.loader.AsyncPluginLoader(str(path.parent), key="Test")
        manifest = asyncio.run(loader.load_plugin(str(path), func=func, key="Test"))

        self.assertEqual(imported, [], "conflict-blocked plugin must never import")
        self.assertTrue(manifest.is_errored)
        self.assertIn("Conflicting packages installed", manifest.error_info)
        # The manifest stays registered so audit surfaces can show the block.
        status = self.hre.status(name)
        self.assertIsNotNone(status)
        self.assertFalse(status["is_loadable"])
        self.assertEqual(status["conflicting"], ["pillow"])

    def test_sync_facade_inside_running_loop_still_enforces_conflicts(self):
        """The degraded sync gate (no pip possible) still blocks conflicts."""
        self.hre.bootstrap()
        _, path = self._write_plugin(
            '__conflicts__ = ["pillow"]\n', name="resp_loop_conf"
        )

        async def scenario():
            loader = self.loader.AsyncPluginLoader(str(path.parent), key="Test")
            return loader._load_sync(
                str(path),
                func=lambda _n: self._module(__doc__="x"),
                key="Test",
            )

        manifest = asyncio.run(scenario())
        self.assertTrue(manifest.is_errored)
        self.assertIn("Conflicting packages installed", manifest.error_info)

    def test_optional_deps_recorded_but_never_installed_at_load(self):
        """__optional__ is for on-demand installs; load must not trigger pip."""
        self.hre.bootstrap()
        _, path = self._write_plugin(
            f'"""optional plugin"""\n__optional__ = ["{MISSING_DEP}"]\n',
            name="resp_opt",
        )
        name = self._canonical(path)
        install_calls = []

        async def fake_install(manifest, include_optional=False, progress_cb=None):
            install_calls.append(manifest.plugin)
            return self.installer_mod.InstallResult(plugin=manifest.plugin)

        with mock.patch.object(
            self.installer_mod.installer, "install_for_plugin", fake_install
        ):
            loader = self.loader.AsyncPluginLoader(str(path.parent), key="Test")
            manifest = asyncio.run(
                loader.load_plugin(
                    str(path),
                    func=lambda _n: self._module(__doc__="optional plugin"),
                    key="Test",
                )
            )

        self.assertTrue(manifest.is_active, manifest.error_info)
        self.assertEqual(install_calls, [], "optional deps must not install at load")
        status = self.hre.status(name)
        self.assertEqual(status["optional"], [MISSING_DEP])
        self.assertEqual(status["missing_optional"], [MISSING_DEP])
        self.assertTrue(status["is_loadable"])

    def test_missing_required_triggers_install_before_import(self):
        """A missing __requires__ dep is installed via the installer (faked)."""
        self.hre.bootstrap()
        _, path = self._write_plugin(
            f'"""needy plugin"""\n__requires__ = ["{MISSING_DEP}"]\n', name="resp_need"
        )
        name = self._canonical(path)
        install_calls = []

        async def fake_install(manifest, include_optional=False, progress_cb=None):
            install_calls.append([d.raw for d in manifest.missing_required])
            return self.installer_mod.InstallResult(
                plugin=manifest.plugin, installed=[MISSING_DEP]
            )

        with mock.patch.object(
            self.installer_mod.installer, "install_for_plugin", fake_install
        ):
            loader = self.loader.AsyncPluginLoader(str(path.parent), key="Test")
            manifest = asyncio.run(
                loader.load_plugin(
                    str(path),
                    func=lambda _n: self._module(__doc__="needy plugin"),
                    key="Test",
                )
            )

        self.assertEqual(install_calls, [[MISSING_DEP]])
        self.assertTrue(manifest.is_active, manifest.error_info)

    def test_failed_install_soft_allows_load(self):
        """Install failure warns but lets the plugin try (graceful ImportError)."""
        self.hre.bootstrap()
        _, path = self._write_plugin(
            f'__requires__ = ["{MISSING_DEP}"]\n', name="resp_soft"
        )

        async def fake_install(manifest, include_optional=False, progress_cb=None):
            return self.installer_mod.InstallResult(
                plugin=manifest.plugin, failed=[MISSING_DEP]
            )

        with mock.patch.object(
            self.installer_mod.installer, "install_for_plugin", fake_install
        ):
            loader = self.loader.AsyncPluginLoader(str(path.parent), key="Test")
            manifest = asyncio.run(
                loader.load_plugin(
                    # The injected module declares no __requires__, so the
                    # post-import gate passes — isolating the soft-allow path.
                    str(path),
                    func=lambda _n: self._module(__doc__="soft"),
                    key="Test",
                )
            )
        self.assertTrue(manifest.is_active, manifest.error_info)

    def test_hre_failure_never_breaks_load(self):
        """An exception inside HRE is logged and the load proceeds."""
        self.hre.bootstrap()
        _, path = self._write_plugin('"""plain plugin"""\n', name="resp_boom")
        with mock.patch.object(
            self.hre, "on_plugin_load", side_effect=RuntimeError("boom")
        ):
            loader = self.loader.AsyncPluginLoader(str(path.parent), key="Test")
            manifest = asyncio.run(
                loader.load_plugin(
                    str(path), func=lambda _n: self._module(__doc__="plain"), key="Test"
                )
            )
        self.assertTrue(manifest.is_active, manifest.error_info)

    def test_hre_inert_until_bootstrap(self):
        """Pre-bootstrap loads pass through: no register, no install attempts."""
        _, path = self._write_plugin(
            '"""inert plugin"""\n__requires__ = ["pillow>=10.0"]\n'
            '__res_tags__ = ["image"]\n',
            name="resp_inert",
        )
        name = self._canonical(path)
        install_calls = []

        async def fake_install(manifest, include_optional=False, progress_cb=None):
            install_calls.append(manifest.plugin)
            return self.installer_mod.InstallResult(plugin=manifest.plugin)

        with mock.patch.object(
            self.installer_mod.installer, "install_for_plugin", fake_install
        ):
            loader = self.loader.AsyncPluginLoader(str(path.parent), key="Test")
            manifest = asyncio.run(
                loader.load_plugin(
                    str(path),
                    func=lambda _n: self._module(
                        __doc__="inert plugin", __requires__=["pillow>=10.0"]
                    ),
                    key="Test",
                )
            )

        self.assertTrue(manifest.is_active, manifest.error_info)
        self.assertEqual(install_calls, [])
        self.assertEqual(self.registry.all_manifests(), [])
        self.assertIsNone(self.hre.status(name))

        # Unload detach is equally inert (and harmless) pre-bootstrap.
        result = asyncio.run(loader.unload_plugin(name))
        self.assertTrue(result["success"])

    # ── W1-D.2: status / audit / cleanup surfaces ────────────────────────

    def test_audit_status_and_cleanup_surfaces(self):
        self.hre.bootstrap()
        _, ok_path = self._write_plugin('__res_tags__ = ["ok"]\n', name="resp_ok")
        _, bad_path = self._write_plugin(
            '__conflicts__ = ["pillow"]\n', name="resp_bad"
        )
        ok_name = self._canonical(ok_path)
        bad_name = self._canonical(bad_path)
        loader = self.loader.AsyncPluginLoader(str(ok_path.parent), key="Test")
        asyncio.run(
            loader.load_plugin(
                str(ok_path), func=lambda _n: self._module(__doc__="ok"), key="Test"
            )
        )
        asyncio.run(
            loader.load_plugin(
                str(bad_path), func=lambda _n: self._module(__doc__="bad"), key="Test"
            )
        )

        audit = self.hre.audit()
        self.assertEqual(audit["total"], 2)
        self.assertEqual(audit["ok"], 1)
        self.assertEqual(audit["blocked"], 1)
        self.assertIn(bad_name, audit["blocked_plugins"])
        self.assertIn(bad_name, audit["conflicts"])
        self.assertIn("registry_stats", audit)
        self.assertGreaterEqual(audit["registry_stats"]["baseline_packages"], 1)

        self.assertIsNone(self.hre.status("no.such.plugin"))
        ok_status = self.hre.status(ok_name)
        self.assertEqual(ok_status["tags"], ["ok"])

        # cleanup() delegates to the installer (faked — no real pip).
        async def fake_cleanup():
            return ["orphan_pkg"]

        with mock.patch.object(
            self.installer_mod.installer, "cleanup_orphans", fake_cleanup
        ):
            removed = asyncio.run(self.hre.cleanup())
        self.assertEqual(removed, ["orphan_pkg"])

    def test_orphan_tracking_across_unload(self):
        """HRE-installed packages become orphans once the last needer unloads."""
        self.hre.bootstrap()
        _, path = self._write_plugin(
            '__requires__ = ["some-hre-pkg"]\n', name="resp_orphan"
        )
        name = self._canonical(path)

        async def fake_install(manifest, include_optional=False, progress_cb=None):
            return self.installer_mod.InstallResult(
                plugin=manifest.plugin, installed=["some-hre-pkg"]
            )

        loader = self.loader.AsyncPluginLoader(str(path.parent), key="Test")
        with mock.patch.object(
            self.installer_mod.installer, "install_for_plugin", fake_install
        ):
            asyncio.run(
                loader.load_plugin(
                    str(path), func=lambda _n: self._module(__doc__="x"), key="Test"
                )
            )
        dep = self.manifest_mod.DepSpec.parse("some-hre-pkg")
        self.registry.mark_installed(dep, name)
        # Declared by a loaded plugin → not an orphan.
        self.assertEqual(self.registry.orphaned_packages(), [])

        asyncio.run(loader.unload_plugin(name))
        # Detached → the HRE-installed package is now orphaned.
        self.assertEqual(self.registry.orphaned_packages(), ["some_hre_pkg"])

    # ── W1-D.3: startup indexing ─────────────────────────────────────────

    def test_index_all_picks_up_loaded_plugins_only(self):
        indexer = self.indexer_mod.plugin_index
        _, path = self._write_plugin(
            "from pyUltroid import ultroid_cmd\n\n"
            '@ultroid_cmd(pattern="fake")\n'
            "async def fake_cmd(event):\n"
            "    pass\n",
            name="fakeidx",
            subdir="plugins",
        )
        _, addon_path = self._write_plugin("X = 1\n", name="fakeaddon", subdir="addons")
        plugin_module = types.ModuleType("plugins.fakeidx")
        plugin_module.__file__ = str(path)
        addon_module = types.ModuleType("addons.fakeaddon")
        addon_module.__file__ = str(addon_path)
        sys.modules["plugins.fakeidx"] = plugin_module
        sys.modules["addons.fakeaddon"] = addon_module
        self.addCleanup(sys.modules.pop, "plugins.fakeidx", None)
        self.addCleanup(sys.modules.pop, "addons.fakeaddon", None)

        count = indexer.index_all()
        self.assertGreaterEqual(count, 1)

        rec = indexer.get_plugin("fakeidx")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.handlers[0].pattern, "fake")
        self.assertEqual(rec.handlers[0].decorator, "ultroid_cmd")
        # Non-plugins/ trees are out of scope for the boot index.
        self.assertIsNone(indexer.get_plugin("fakeaddon"))

        self.assertTrue(indexer.remove_plugin("fakeidx"))
        self.assertIsNone(indexer.get_plugin("fakeidx"))

    def test_startup_loader_bootstraps_hre_and_warms_index(self):
        """load_other_plugins must bootstrap HRE first, index_all last."""
        source = (ROOT / "pyUltroid/startup/loader.py").read_text()
        tree = ast.parse(source)
        func = next(
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef) and node.name == "load_other_plugins"
        )
        segment = ast.get_source_segment(source, func)
        self.assertIn("hre.bootstrap()", segment)
        self.assertIn("index_all()", segment)
        self.assertLess(
            segment.index("hre.bootstrap()"),
            segment.index("index_all()"),
            "baseline snapshot must precede plugin loads; index warm-up follows them",
        )

    # ── W1-D.4: confirm_unload user surface ──────────────────────────────

    def _register_fake_plugin(self, name="plugins.fake"):
        self.loader.registry.register(
            self.loader.PluginManifest(
                name=name, path="plugins/fake.py", status=self.loader.STATUS_ACTIVE
            )
        )

    def test_confirm_unload_yes_unloads_plugin(self):
        self._register_fake_plugin()

        async def scenario():
            client = _FakeClient()
            ok = await self.loader.confirm_unload(client, 123, "plugins.fake")
            self.assertTrue(ok)
            chat_id, text, buttons, message = client.messages[0]
            self.assertIn("plugins.fake", text)
            self.assertIsNotNone(buttons)

            callback = client.callbacks[0]
            await callback(_callback_event(b"unld_yes:plugins.fake"))
            return message

        message = asyncio.run(scenario())
        self.assertIsNone(self.loader.registry.get("plugins.fake"))
        self.assertTrue(
            any("Unloaded `plugins.fake`" in edit for edit in message.edits),
            message.edits,
        )

    def test_confirm_unload_no_keeps_plugin(self):
        self._register_fake_plugin()

        async def scenario():
            client = _FakeClient()
            await self.loader.confirm_unload(client, 123, "plugins.fake")
            callback = client.callbacks[0]
            await callback(_callback_event(b"unld_no:plugins.fake"))
            return client.messages[0][3]

        message = asyncio.run(scenario())
        self.assertIsNotNone(self.loader.registry.get("plugins.fake"))
        self.assertIn("Unload cancelled.", message.edits[-1])

    def test_confirm_unload_times_out(self):
        self._register_fake_plugin()

        async def scenario():
            client = _FakeClient()
            await self.loader.confirm_unload(client, 123, "plugins.fake", timeout=0.05)
            await asyncio.sleep(0.2)
            return client.messages[0][3]

        message = asyncio.run(scenario())
        self.assertIsNotNone(self.loader.registry.get("plugins.fake"))
        self.assertIn("timed out", message.edits[-1])

    def test_confirm_unload_rejects_strangers(self):
        self._register_fake_plugin()

        async def scenario():
            client = _FakeClient()
            await self.loader.confirm_unload(client, 123, "plugins.fake")
            callback = client.callbacks[0]
            # udB is None in this namespace → fallback auth compares the
            # clicker to the original message sender (id 4242).
            stranger = _callback_event(b"unld_yes:plugins.fake", sender_id=999)
            await callback(stranger)
            return stranger, client.messages[0][3]

        stranger, message = asyncio.run(scenario())
        stranger.answer.assert_awaited()
        self.assertIsNotNone(self.loader.registry.get("plugins.fake"))
        self.assertEqual(message.edits, [])

    def test_core_plugin_commands_wire_hre_surfaces(self):
        """plugins/core.py: .unload uses confirm_unload; dep surfaces exist."""
        source = (ROOT / "plugins/core.py").read_text()
        tree = ast.parse(source)
        functions = {
            node.name: node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for expected in ("unload", "depstatus_cmd", "depaudit_cmd", "depclean_cmd"):
            self.assertIn(expected, functions)
        unload_segment = ast.get_source_segment(source, functions["unload"])
        self.assertIn("confirm_unload(", unload_segment)
        for cmd in ("depstatus_cmd", "depaudit_cmd", "depclean_cmd"):
            segment = ast.get_source_segment(source, functions[cmd])
            self.assertIn("hre", segment)


if __name__ == "__main__":
    unittest.main()

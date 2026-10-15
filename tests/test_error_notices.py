"""Tests for the C5 plugin-load error notice: builder + real send site.

Plan item C5 wanted a failed plugin load to produce an image error card
*plus* a text notice (header + collapsed/expandable-blockquote traceback)
delivered to the owner's log channel. The notice builder
(:func:`pyUltroid._core.cards.build_error_notice`) previously existed only
as dead code (deleted in dc5d17b — never wired to a real send path). This
file covers:

* :func:`pyUltroid._core.cards.build_error_notice` output shapes — the
  entities path, the plain-HTML ``<blockquote expandable>`` fallback, and
  the non-expandable variant — and that it never raises.
* the actual send site added to :mod:`pyUltroid._core.loader`
  (``_send_error_notice_async``/``_dispatch_error_notice_sync``, wired into
  ``_register_failure``/``_register_failure_async``/``load_plugin``): sent
  when a connected client + configured ``LOG_CHANNEL`` exist, silently
  skipped otherwise, and never raises even when the client send itself
  fails.

Uses the same dependency-light ``pyUltroid`` namespace-isolation pattern as
``tests/test_core_architecture.py`` (hand-rolled here rather than imported,
since ``tests/conftest.py`` documents it as a pattern any test may
replicate) so these tests don't need the production bootstrap.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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
        root.LOGS = logging.getLogger("error-notice-tests")
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


class FakeUdb:
    """Minimal ``udB``-shaped stub: just ``get_key``."""

    def __init__(self, values=None):
        self._values = dict(values or {})

    def get_key(self, key, default=None):
        return self._values.get(key, default)


class FakeClient:
    """Minimal Telethon-client-shaped stub for the notice send path."""

    def __init__(self, *, connected=True, raise_on_send=None):
        self._connected = connected
        self._raise_on_send = raise_on_send
        self.sent = []

    def is_connected(self):
        return self._connected

    async def send_message(self, *args, **kwargs):
        if self._raise_on_send is not None:
            raise self._raise_on_send
        self.sent.append((args, kwargs))
        return types.SimpleNamespace(id=1)


async def _run_and_settle(coro):
    """Await *coro*, then yield a few times so any fire-and-forget task runs."""
    result = await coro
    for _ in range(5):
        await asyncio.sleep(0)
    return result


class BuildErrorNoticeTests(unittest.TestCase):
    def setUp(self):
        self.namespace = core_namespace()
        self.root, _ = self.namespace.__enter__()
        self.cards = importlib.import_module("pyUltroid._core.cards")
        self.entities = importlib.import_module("pyUltroid._core.entities")

    def tearDown(self):
        self.namespace.__exit__(None, None, None)

    def test_entities_path_wraps_traceback_in_expandable_blockquote(self):
        notice = self.cards.build_error_notice(
            "plugins.demo",
            error_traceback="Traceback (most recent call last):\n  File x\nValueError: boom",
            error_message="ValueError: boom",
        )
        self.assertIn("plugins.demo", notice.text)
        self.assertIsNone(notice.parse_mode)
        self.assertEqual(len(notice.entities), 1)
        entity = notice.entities[0]
        self.assertTrue(getattr(entity, "collapsed", False))
        # The blockquote entity must cover only the summary+traceback part,
        # not the plain-text header in front of it.
        self.assertLess(entity.offset, len(notice.text))
        self.assertIn("ValueError: boom", notice.text)

    def test_html_fallback_when_blockquote_entity_unavailable(self):
        original_has = self.entities.has
        self.entities.has = lambda kind: False
        try:
            notice = self.cards.build_error_notice(
                "plugins.demo", error_traceback="tb line", error_message="err msg"
            )
        finally:
            self.entities.has = original_has
        self.assertEqual(notice.entities, [])
        self.assertEqual(notice.parse_mode, "html")
        self.assertIn("<blockquote expandable>", notice.text)
        self.assertIn("err msg", notice.text)
        self.assertIn("tb line", notice.text)

    def test_non_expandable_variant_has_no_blockquote_markup(self):
        notice = self.cards.build_error_notice(
            "plugins.demo",
            error_traceback="tb line",
            error_message="err msg",
            expandable=False,
        )
        self.assertEqual(notice.entities, [])
        self.assertIsNone(notice.parse_mode)
        self.assertNotIn("<blockquote", notice.text)
        self.assertIn("err msg", notice.text)
        self.assertIn("tb line", notice.text)

    def test_falls_back_to_last_traceback_line_when_no_error_message(self):
        notice = self.cards.build_error_notice(
            "plugins.demo",
            error_traceback="Traceback (most recent call last):\nValueError: boom",
            error_message="",
        )
        self.assertIn("ValueError: boom", notice.text)

    def test_never_raises_even_when_internals_blow_up(self):
        original_combine = self.entities.combine
        self.entities.combine = lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("boom")
        )
        try:
            notice = self.cards.build_error_notice(
                "plugins.demo", error_traceback="tb", error_message="err"
            )
        finally:
            self.entities.combine = original_combine
        # combine() blowing up on the entities path must still fall back to
        # the HTML convention rather than propagate.
        self.assertIn("plugins.demo", notice.text)


class LoaderSendPathTests(unittest.TestCase):
    def setUp(self):
        self.namespace = core_namespace()
        self.root, _ = self.namespace.__enter__()
        self.loader = importlib.import_module("pyUltroid._core.loader")

    def tearDown(self):
        self.namespace.__exit__(None, None, None)

    def test_sent_when_client_and_channel_available(self):
        client = FakeClient(connected=True)
        self.root.ultroid_bot = client
        self.root.udB = FakeUdb({"LOG_CHANNEL": 12345})

        asyncio.run(
            self.loader._send_error_notice_async(
                "plugins.demo",
                "ValueError: boom",
                "Traceback...\nValueError: boom",
                "card.png",
            )
        )

        self.assertEqual(len(client.sent), 1)
        args, kwargs = client.sent[0]
        self.assertEqual(args[0], 12345)
        self.assertIn("plugins.demo", args[1])
        self.assertEqual(kwargs.get("file"), "card.png")

    def test_skipped_when_no_log_channel_configured(self):
        client = FakeClient(connected=True)
        self.root.ultroid_bot = client
        self.root.udB = FakeUdb({})  # no LOG_CHANNEL key

        asyncio.run(self.loader._send_error_notice_async("plugins.demo", "err", "tb"))

        self.assertEqual(client.sent, [])

    def test_skipped_when_no_client_connected(self):
        self.root.ultroid_bot = None
        self.root.asst = None
        self.root.udB = FakeUdb({"LOG_CHANNEL": 12345})

        # Must not raise even though there's nothing to send with.
        asyncio.run(self.loader._send_error_notice_async("plugins.demo", "err", "tb"))

    def test_skipped_when_client_exists_but_not_connected_yet(self):
        client = FakeClient(connected=False)
        self.root.ultroid_bot = client
        self.root.udB = FakeUdb({"LOG_CHANNEL": 12345})

        asyncio.run(self.loader._send_error_notice_async("plugins.demo", "err", "tb"))

        self.assertEqual(client.sent, [])

    def test_skipped_when_udb_not_bootstrapped(self):
        self.root.udB = None
        self.root.ultroid_bot = FakeClient(connected=True)

        asyncio.run(self.loader._send_error_notice_async("plugins.demo", "err", "tb"))

    def test_never_raises_when_client_send_fails(self):
        client = FakeClient(connected=True, raise_on_send=RuntimeError("network down"))
        self.root.ultroid_bot = client
        self.root.udB = FakeUdb({"LOG_CHANNEL": 12345})

        # Should complete without raising, despite the client always failing.
        asyncio.run(self.loader._send_error_notice_async("plugins.demo", "err", "tb"))

    def test_falls_back_to_second_client_when_first_fails(self):
        failing = FakeClient(connected=True, raise_on_send=RuntimeError("nope"))
        working = FakeClient(connected=True)
        self.root.ultroid_bot = failing
        self.root.asst = working
        self.root.udB = FakeUdb({"LOG_CHANNEL": 12345})

        asyncio.run(self.loader._send_error_notice_async("plugins.demo", "err", "tb"))

        self.assertEqual(len(working.sent), 1)

    def test_dispatch_sync_is_noop_without_running_loop(self):
        client = FakeClient(connected=True)
        self.root.ultroid_bot = client
        self.root.udB = FakeUdb({"LOG_CHANNEL": 12345})

        # No event loop running here — must be a silent no-op, not raise.
        self.loader._dispatch_error_notice_sync("plugins.demo", "err", "tb")
        self.assertEqual(client.sent, [])

    def test_register_failure_fires_notice_when_loop_running(self):
        client = FakeClient(connected=True)
        self.root.ultroid_bot = client
        self.root.udB = FakeUdb({"LOG_CHANNEL": 12345})

        async def run():
            with tempfile.TemporaryDirectory() as directory:
                bad = Path(directory) / "boom.py"
                loader = self.loader.AsyncPluginLoader(directory)
                loader._register_failure(
                    "plugins.boom",
                    str(bad),
                    "ValueError: kaboom",
                    "Traceback...\nValueError: kaboom",
                )
                for _ in range(10):
                    await asyncio.sleep(0)

        asyncio.run(run())
        self.assertEqual(len(client.sent), 1)
        args, _kwargs = client.sent[0]
        self.assertIn("plugins.boom", args[1])

    def test_register_failure_never_raises_without_client(self):
        self.root.ultroid_bot = None
        self.root.asst = None
        self.root.udB = FakeUdb({"LOG_CHANNEL": 12345})

        async def run():
            loader = self.loader.AsyncPluginLoader()
            manifest = loader._register_failure(
                "plugins.boom", "plugins/boom.py", "ValueError: kaboom", "tb"
            )
            for _ in range(5):
                await asyncio.sleep(0)
            return manifest

        manifest = asyncio.run(run())
        self.assertTrue(manifest.is_errored)

    def test_register_failure_async_dispatches_notice(self):
        client = FakeClient(connected=True)
        self.root.ultroid_bot = client
        self.root.udB = FakeUdb({"LOG_CHANNEL": 12345})

        loader = self.loader.AsyncPluginLoader()
        manifest = asyncio.run(
            loader._register_failure_async(
                "plugins.boom", "plugins/boom.py", "ValueError: kaboom", "tb"
            )
        )

        self.assertTrue(manifest.is_errored)
        self.assertEqual(len(client.sent), 1)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the plugin toggle system (SarahUB Phase — toggles).

Covers ``pyUltroid/_core/toggles.py``:

* the :func:`plugin_toggle` decorator — metadata attachment, no-wrap passthrough,
  full field population;
* :class:`ToggleRegistry` — discovery of decorated async handlers, skipping of
  non-async / non-callable candidates, duplicate-key warnings, unregister,
  sorted metadata listing, default/after-flip state reads, handler dispatch
  with the flipped bool, revert-on-error, revert-on-timeout, and per-plugin
  namespacing.

Async tests use ``asyncio.run`` — the suite has no pytest-asyncio, matching
``tests/test_queue_manager.py`` and ``tests/test_webupload_backbone.py``.  The
toggles module is import-safe (no top-level ``pyUltroid`` imports), so it loads
without a running bot; every registry test builds a fresh ``ToggleRegistry()``
instance for isolation rather than touching the module-level singleton.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyUltroid._core.toggles import (  # noqa: E402
    PLUGIN_TOGGLES,
    ToggleRegistry,
    plugin_toggle,
)

# ──────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────


def _make_module(**attrs):
    """Build a throwaway module-like object exposing *attrs* via ``dir``/``getattr``."""
    mod = types.ModuleType("mock_plugin")
    for name, value in attrs.items():
        setattr(mod, name, value)
    return mod


def _decorate(mock_or_func, *, label="L", key="k", description="", icon=""):
    """Attach ``@plugin_toggle`` metadata to *mock_or_func* and return it."""
    return plugin_toggle(label=label, key=key, description=description, icon=icon)(
        mock_or_func
    )


# ──────────────────────────────────────────────────────────────────────────
# Decorator
# ──────────────────────────────────────────────────────────────────────────


class TestPluginToggleDecorator:
    def test_decorator_attaches_metadata(self):
        @plugin_toggle(label="Auto Update", key="auto_update", icon="🔄")
        async def handler(enabled):
            return "ok"

        assert hasattr(handler, "_plugin_toggle_")
        meta = handler._plugin_toggle_
        assert meta["label"] == "Auto Update"
        assert meta["key"] == "auto_update"

    def test_decorator_returns_same_function(self):
        async def handler(enabled):
            return "ok"

        original = handler
        decorated = plugin_toggle(label="L", key="k")(handler)
        # No wrapping — the returned object is the very same function.
        assert decorated is original
        assert asyncio.iscoroutinefunction(decorated)

    def test_decorator_all_fields(self):
        @plugin_toggle(
            label="Auto Update",
            key="auto_update",
            description="Toggle auto-update behaviour",
            icon="🔄",
        )
        async def handler(enabled):
            return "ok"

        assert handler._plugin_toggle_ == {
            "label": "Auto Update",
            "key": "auto_update",
            "description": "Toggle auto-update behaviour",
            "icon": "🔄",
        }


# ──────────────────────────────────────────────────────────────────────────
# Registry
# ──────────────────────────────────────────────────────────────────────────


class TestToggleRegistry:
    def test_register_discovers_decorated_functions(self):
        @plugin_toggle(label="Auto Update", key="auto_update")
        async def handler(enabled):
            return "ok"

        mod = _make_module(toggle_auto_update=handler)
        reg = ToggleRegistry()
        count = reg.register("chatbot", mod)

        assert count == 1
        toggles = reg.get_toggles("chatbot")
        assert len(toggles) == 1
        assert toggles[0]["plugin"] == "chatbot"
        assert toggles[0]["key"] == "auto_update"
        assert toggles[0]["label"] == "Auto Update"
        # handler callable must not leak into UI metadata
        assert "handler" not in toggles[0]
        # freshly registered toggle defaults to OFF and no udB → in-memory only.
        assert reg.get_state("chatbot.auto_update") is False

    def test_register_skips_non_async(self):
        @plugin_toggle(label="L", key="sync_thing")
        def handler(enabled):  # synchronous — must be skipped
            return "ok"

        mod = _make_module(handler=handler)
        reg = ToggleRegistry()
        count = reg.register("p", mod)

        assert count == 0
        assert reg.get_toggles("p") == []

    def test_register_skips_non_callable(self):
        # Non-callable object that still carries the marker attribute.
        fake = types.SimpleNamespace(
            _plugin_toggle_={"label": "L", "key": "k", "description": "", "icon": ""}
        )
        mod = _make_module(thing=fake)
        reg = ToggleRegistry()
        count = reg.register("p", mod)

        assert count == 0
        assert reg.get_toggles("p") == []

    def test_register_duplicate_key_warns(self):
        @plugin_toggle(label="L1", key="dup")
        async def h1(enabled):
            return "ok"

        @plugin_toggle(label="L2", key="dup")
        async def h2(enabled):
            return "ok"

        mod = _make_module(h1=h1, h2=h2)
        reg = ToggleRegistry()

        with patch("pyUltroid._core.toggles.LOGS") as log:
            count = reg.register("p", mod)

        # First ``dup`` registers; the second is skipped with a warning.
        assert count == 1
        log.warning.assert_called()
        toggles = reg.get_toggles("p")
        assert [t["key"] for t in toggles] == ["dup"]

    def test_unregister_removes_all_for_plugin(self):
        @plugin_toggle(label="L", key="a")
        async def ha(enabled):
            return "ok"

        @plugin_toggle(label="L", key="b")
        async def hb(enabled):
            return "ok"

        mod = _make_module(ha=ha, hb=hb)
        reg = ToggleRegistry()
        reg.register("p", mod)
        assert len(reg.get_toggles("p")) == 2

        removed = reg.unregister("p")
        assert removed == 2
        assert reg.get_toggles("p") == []
        # State entries are cleaned up too; defaults back to False.
        assert reg.get_state("p.a") is False

    def test_get_toggles_returns_sorted_list(self):
        @plugin_toggle(label="L", key="zeta")
        async def hz(enabled):
            return "ok"

        @plugin_toggle(label="L", key="alpha")
        async def ha(enabled):
            return "ok"

        @plugin_toggle(label="L", key="mike")
        async def hm(enabled):
            return "ok"

        mod = _make_module(hz=hz, ha=ha, hm=hm)
        reg = ToggleRegistry()
        reg.register("p", mod)

        keys = [t["key"] for t in reg.get_toggles("p")]
        assert keys == ["alpha", "mike", "zeta"]
        # verify toggle_key is included in output
        for t in reg.get_toggles("p"):
            assert "toggle_key" in t
            assert t["toggle_key"].startswith("p.")

    def test_get_toggle_retrieves_single_entry(self):
        @plugin_toggle(label="L", key="feat")
        async def handler(enabled):
            return "done"

        mod = _make_module(handler=handler)
        reg = ToggleRegistry()
        reg.register("plug", mod)

        meta = reg.get_toggle("plug.feat")
        assert meta is not None
        assert meta["toggle_key"] == "plug.feat"
        assert meta["plugin"] == "plug"
        assert meta["label"] == "L"
        assert meta["key"] == "feat"
        assert meta["description"] == ""
        assert meta["icon"] == ""

    def test_get_toggle_missing_returns_none(self):
        reg = ToggleRegistry()
        assert reg.get_toggle("nonexistent.key") is None

    def test_get_state_defaults_to_false(self):
        reg = ToggleRegistry()
        assert reg.get_state("nonexistent.key") is False

    def test_get_state_after_flip_returns_true(self):
        @plugin_toggle(label="L", key="feat")
        async def handler(enabled):
            return "done"

        mod = _make_module(handler=handler)
        reg = ToggleRegistry()
        reg.register("p", mod)
        assert reg.get_state("p.feat") is False

        new_state, _msg = asyncio.run(reg.flip("p.feat", 1))
        assert new_state is True
        assert reg.get_state("p.feat") is True

    def test_flip_calls_handler_with_correct_state(self):
        handler = _decorate(AsyncMock(return_value="applied"), key="feat")
        mod = _make_module(handler=handler)
        reg = ToggleRegistry()
        reg.register("p", mod)

        new_state, msg = asyncio.run(reg.flip("p.feat", 42))
        # Handler receives the *new* (flipped) state, not the old one.
        handler.assert_awaited_once_with(True)
        assert new_state is True
        assert msg == "applied"

        # Flip back: handler now receives False.
        new_state2, msg2 = asyncio.run(reg.flip("p.feat", 42))
        handler.assert_awaited_with(False)
        assert new_state2 is False
        assert msg2 == "applied"

    def test_flip_reverts_on_handler_error(self):
        @plugin_toggle(label="L", key="feat")
        async def handler(enabled):
            raise RuntimeError("boom")

        mod = _make_module(handler=handler)
        reg = ToggleRegistry()
        reg.register("p", mod)
        assert reg.get_state("p.feat") is False

        new_state, msg = asyncio.run(reg.flip("p.feat", 1))
        assert new_state is False
        assert "boom" in msg
        # State reverted to its pre-flip value.
        assert reg.get_state("p.feat") is False

    def test_flip_timeout_reverts(self):
        @plugin_toggle(label="L", key="slow")
        async def handler(enabled):
            await asyncio.sleep(1)  # slower than the patched timeout
            return "never"

        mod = _make_module(handler=handler)
        reg = ToggleRegistry()
        reg.register("p", mod)

        # Shrink the handler timeout so the test doesn't wait 10 real seconds
        # while still exercising the timeout-revert code path.
        with patch("pyUltroid._core.toggles._HANDLER_TIMEOUT", 0.05):
            new_state, msg = asyncio.run(reg.flip("p.slow", 1))

        assert new_state is False
        assert "timed out" in msg.lower()
        assert reg.get_state("p.slow") is False

    def test_multiple_plugins_namespaced(self):
        @plugin_toggle(label="L", key="shared")
        async def h_a(enabled):
            return "a"

        @plugin_toggle(label="L", key="shared")
        async def h_b(enabled):
            return "b"

        reg = ToggleRegistry()
        reg.register("pluginA", _make_module(handler=h_a))
        reg.register("pluginB", _make_module(handler=h_b))

        # Same key under different plugins are distinct toggle ids.
        assert reg.get_state("pluginA.shared") is False
        assert reg.get_state("pluginB.shared") is False
        assert len(reg.get_toggles("pluginA")) == 1
        assert len(reg.get_toggles("pluginB")) == 1

        new_state, _msg = asyncio.run(reg.flip("pluginA.shared", 1))
        assert new_state is True
        assert reg.get_state("pluginA.shared") is True
        # Flipping one plugin must not affect the other.
        assert reg.get_state("pluginB.shared") is False


# ──────────────────────────────────────────────────────────────────────────
# Module surface
# ──────────────────────────────────────────────────────────────────────────


class TestToggleModuleSurface:
    def test_module_singleton_is_gone(self):
        import pyUltroid._core.toggles as toggles_mod

        assert not hasattr(toggles_mod, "toggle_registry")

    def test_persistence_key_constant(self):
        assert PLUGIN_TOGGLES == "PLUGIN_TOGGLES"

    def test_module_is_import_safe(self):
        """No top-level pyUltroid imports — importing must not pull the bot."""
        import ast

        source = (ROOT / "pyUltroid" / "_core" / "toggles.py").read_text()
        tree = ast.parse(source)
        for node in tree.body:  # top-level statements only
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(
                        "pyUltroid"
                    ), f"top-level import {alias.name!r}"
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                # stdlib + typing only; no ``pyUltroid`` at module top level.
                assert "pyUltroid" not in module, f"top-level import of {module!r}"

"""Unit tests for the Plugin Manifest Framework (pyUltroid/_core/manifest.py).

Covers: decorator stamps, ManifestRegistry registration / unregistration /
queries, toggle state persistence, action execution, placeholder state
transitions, module scanner, and import-safety.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyUltroid._core.manifest import (  # noqa: E402
    PLUGIN_MANIFEST_RESULT_BUDGET,
    ManifestRegistry,
    action,
    handle_manifest,
    manifest,
    scan_module,
    toggle,
)

# ── helpers ────────────────────────────────────────────────────────────


def _make_module(**attrs):
    mod = types.ModuleType("mock_plugin")
    for name, value in attrs.items():
        setattr(mod, name, value)
    return mod


# ── decorator tests ────────────────────────────────────────────────────


class TestDecoratorStamps:
    """@toggle, @action, @manifest, @handle_manifest attach _pmf_ correctly."""

    def test_toggle_attaches_metadata(self):
        @toggle(label="L", key="k", icon="i", description="d")
        async def fn(event, enabled):
            pass

        assert fn._pmf_ == {
            "type": "toggle",
            "label": "L",
            "key": "k",
            "icon": "i",
            "description": "d",
        }

    def test_toggle_returns_same_function(self):
        @toggle(label="L", key="k")
        async def fn(event, enabled):
            pass

        assert callable(fn)
        assert fn.__name__ == "fn"

    def test_action_attaches_metadata(self):
        @action(label="A", key="ak", icon="▶️")
        async def fn(event):
            pass

        assert fn._pmf_["type"] == "action"
        assert fn._pmf_["label"] == "A"

    def test_manifest_attaches_buttons(self):
        btns = [{"label": "B1", "key": "b1"}, {"label": "B2", "key": "b2"}]

        @manifest(buttons=btns)
        async def fn(event):
            pass

        assert fn._pmf_["type"] == "manifest"
        assert fn._pmf_["buttons"] == btns

    def test_handle_manifest_attaches_key(self):
        @handle_manifest(key="mykey")
        async def fn(event):
            pass

        assert fn._pmf_["type"] == "handle_manifest"
        assert fn._pmf_["key"] == "mykey"


# ── registry tests ─────────────────────────────────────────────────────


class TestManifestRegistry:
    """ManifestRegistry registration, queries, unregistration."""

    def test_register_toggle(self):
        reg = ManifestRegistry()
        entry = {
            "type": "toggle",
            "label": "L",
            "key": "k",
            "icon": "",
            "description": "",
            "handler": _make_async_fn(),
        }
        assert reg.register("plug", dict(entry)) is True
        assert reg.get_entry("plug.k") is not None

    def test_register_toggle_defaults_state_to_false(self):
        reg = ManifestRegistry()
        entry = {
            "type": "toggle",
            "label": "L",
            "key": "k",
            "handler": _make_async_fn(),
        }
        reg.register("p", entry)
        assert reg.get_state("p.k") is False

    def test_register_action(self):
        reg = ManifestRegistry()
        entry = {
            "type": "action",
            "label": "A",
            "key": "ak",
            "handler": _make_async_fn(),
        }
        assert reg.register("p", entry) is True
        assert reg.get_entry("p.ak")["type"] == "action"

    def test_register_rejects_missing_key(self):
        reg = ManifestRegistry()
        entry = {"type": "toggle", "label": "L", "handler": _make_async_fn()}
        assert reg.register("p", entry) is False

    def test_register_rejects_non_async_handler(self):
        reg = ManifestRegistry()

        def sync_fn(event, enabled):
            pass

        entry = {"type": "toggle", "label": "L", "key": "k", "handler": sync_fn}
        assert reg.register("p", entry) is False

    def test_register_rejects_duplicate_key(self):
        reg = ManifestRegistry()
        e1 = {"type": "toggle", "label": "L1", "key": "k", "handler": _make_async_fn()}
        e2 = {"type": "toggle", "label": "L2", "key": "k", "handler": _make_async_fn()}
        assert reg.register("p", e1) is True
        assert reg.register("p", e2) is False

    def test_register_manifest_buttons(self):
        reg = ManifestRegistry()
        entry = {
            "type": "manifest",
            "buttons": [{"label": "X", "key": "x"}, {"label": "Y", "key": "y"}],
        }
        reg.register("p", entry)
        entries = reg.get_entries("p")
        manifest_entries = [e for e in entries if e["type"] == "manifest_button"]
        assert len(manifest_entries) == 2

    def test_register_handle_manifest_wires_handler(self):
        reg = ManifestRegistry()

        async def h(event):
            return "ok"

        entry = {"type": "handle_manifest", "key": "btn", "handler": h}
        reg.register("p", entry)
        handler = reg.get_handler("p.btn")
        assert handler is not None

    def test_unregister_removes_all(self):
        reg = ManifestRegistry()
        reg.register(
            "p",
            {"type": "toggle", "label": "L", "key": "k", "handler": _make_async_fn()},
        )
        reg.register(
            "p",
            {"type": "action", "label": "A", "key": "ak", "handler": _make_async_fn()},
        )
        assert len(reg.get_entries("p")) == 2
        removed = reg.unregister("p")
        assert removed >= 2
        assert reg.get_entries("p") == []

    def test_get_entries_sorted(self):
        reg = ManifestRegistry()
        for k in ("c", "a", "b"):
            reg.register(
                "p",
                {"type": "action", "label": k, "key": k, "handler": _make_async_fn()},
            )
        keys = [e["key"] for e in reg.get_entries("p")]
        assert keys == ["a", "b", "c"]

    def test_get_entry_returns_none_for_missing(self):
        reg = ManifestRegistry()
        assert reg.get_entry("nonexistent") is None

    def test_get_handler_returns_none_for_missing(self):
        reg = ManifestRegistry()
        assert reg.get_handler("nonexistent") is None

    def test_namespacing(self):
        reg = ManifestRegistry()
        reg.register(
            "a",
            {"type": "toggle", "label": "L", "key": "k", "handler": _make_async_fn()},
        )
        reg.register(
            "b",
            {"type": "toggle", "label": "L", "key": "k", "handler": _make_async_fn()},
        )
        assert reg.get_entry("a.k") is not None
        assert reg.get_entry("b.k") is not None
        assert reg.get_entry("a.k")["plugin"] == "a"

    # ── execution ──────────────────────────────────────────────────

    def test_flip_toggle_calls_handler_with_inverted_state(self):
        reg = ManifestRegistry()
        received_state = None

        async def real_handler(event, enabled):
            nonlocal received_state
            received_state = enabled
            return "ok"

        reg.register(
            "p", {"type": "toggle", "label": "L", "key": "k", "handler": real_handler}
        )
        new_state, alert = asyncio.run(reg.flip_toggle("p.k"))
        assert new_state is True
        assert alert == "ok"
        assert received_state is True

    def test_flip_toggle_reverts_on_handler_error(self):
        reg = ManifestRegistry()

        async def crash(event, enabled):
            raise RuntimeError("boom")

        reg.register(
            "p", {"type": "toggle", "label": "L", "key": "k", "handler": crash}
        )
        new_state, alert = asyncio.run(reg.flip_toggle("p.k"))
        # State reverts to False.
        assert reg.get_state("p.k") is False
        assert new_state is False

    def test_flip_toggle_timeout_reverts(self):
        reg = ManifestRegistry()

        async def slow(event, enabled):
            await asyncio.sleep(999)

        reg.register("p", {"type": "toggle", "label": "L", "key": "k", "handler": slow})
        new_state, _ = asyncio.run(reg.flip_toggle("p.k"))
        assert reg.get_state("p.k") is False

    def test_run_action_returns_output(self):
        reg = ManifestRegistry()

        async def h(event):
            return "result text"

        entry = {"type": "action", "label": "A", "key": "ak", "handler": h}
        reg.register("p", entry)
        success, output = asyncio.run(reg.run_action("p.ak"))
        assert success is True
        assert output == "result text"

    def test_run_action_false_triggers_alert_mode(self):
        reg = ManifestRegistry()

        async def h(event):
            return False

        reg.register("p", {"type": "action", "label": "A", "key": "ak", "handler": h})
        success, output = asyncio.run(reg.run_action("p.ak"))
        assert success is True
        assert output is None  # alert mode

    def test_run_action_handler_crash_returns_failure(self):
        reg = ManifestRegistry()

        async def crash(event):
            raise RuntimeError("boom")

        reg.register(
            "p", {"type": "action", "label": "A", "key": "ak", "handler": crash}
        )
        success, _ = asyncio.run(reg.run_action("p.ak"))
        assert success is False

    # ── placeholder ────────────────────────────────────────────────

    def test_placeholder_idle_by_default(self):
        reg = ManifestRegistry()
        ph = reg.get_placeholder("p")
        assert ph["text"] == ""
        assert ph["url"] == ""
        assert ph["is_error"] is False

    def test_placeholder_set_text(self):
        reg = ManifestRegistry()
        reg.set_placeholder("p", text="Hello world")
        ph = reg.get_placeholder("p")
        assert ph["text"] == "Hello world"
        assert ph["is_error"] is False

    def test_placeholder_set_error(self):
        reg = ManifestRegistry()
        reg.set_placeholder("p", text="fail", is_error=True)
        ph = reg.get_placeholder("p")
        assert ph["is_error"] is True

    def test_placeholder_set_url(self):
        reg = ManifestRegistry()
        reg.set_placeholder("p", url="https://example.com")
        ph = reg.get_placeholder("p")
        assert ph["url"] == "https://example.com"

    def test_placeholder_text_truncated_to_budget(self):
        reg = ManifestRegistry()
        long_text = "x" * (PLUGIN_MANIFEST_RESULT_BUDGET + 100)
        reg.set_placeholder("p", text=long_text)
        ph = reg.get_placeholder("p")
        assert len(ph["text"]) == PLUGIN_MANIFEST_RESULT_BUDGET

    def test_placeholder_per_plugin_isolation(self):
        reg = ManifestRegistry()
        reg.set_placeholder("a", text="A message")
        reg.set_placeholder("b", text="B message")
        assert reg.get_placeholder("a")["text"] == "A message"
        assert reg.get_placeholder("b")["text"] == "B message"

    def test_unregister_clears_placeholder(self):
        reg = ManifestRegistry()
        reg.set_placeholder("p", text="msg")
        reg.unregister("p")
        ph = reg.get_placeholder("p")
        assert ph["text"] == ""


# ── scanner tests ──────────────────────────────────────────────────────


class TestScanModule:
    """scan_module() discovers _pmf_-decorated functions."""

    def test_scan_discovers_toggle(self):
        @toggle(label="L", key="k")
        async def handler(event, enabled):
            pass

        mod = _make_module(handler=handler)
        reg = ManifestRegistry()
        from types import SimpleNamespace as _SN
        core_pkg = importlib.import_module("pyUltroid.ultroid")
        with patch.object(core_pkg, "core", _SN(manifest=reg)):
            count = scan_module("p", mod)
        assert count == 1
        assert reg.get_entry("p.k") is not None

    def test_scan_discovers_action(self):
        @action(label="A", key="ak")
        async def runner(event):
            pass

        mod = _make_module(runner=runner)
        reg = ManifestRegistry()
        from types import SimpleNamespace as _SN
        core_pkg = importlib.import_module("pyUltroid.ultroid")
        with patch.object(core_pkg, "core", _SN(manifest=reg)):
            count = scan_module("p", mod)
        assert count == 1

    def test_scan_skips_sync_functions(self):
        @toggle(label="L", key="k")
        def sync_fn(event, enabled):
            pass

        mod = _make_module(handler=sync_fn)
        reg = ManifestRegistry()
        from types import SimpleNamespace as _SN
        core_pkg = importlib.import_module("pyUltroid.ultroid")
        with patch.object(core_pkg, "core", _SN(manifest=reg)):
            count = scan_module("p", mod)
        assert count == 0

    def test_scan_skips_non_callable_attrs(self):
        class Thing:
            pass

        obj = Thing()
        obj._pmf_ = {"type": "toggle", "label": "L", "key": "k"}
        mod = _make_module(x=obj)
        reg = ManifestRegistry()
        from types import SimpleNamespace as _SN
        core_pkg = importlib.import_module("pyUltroid.ultroid")
        with patch.object(core_pkg, "core", _SN(manifest=reg)):
            count = scan_module("p", mod)
        assert count == 0

    def test_scan_handles_mixed_entries(self):
        @toggle(label="T", key="t")
        async def t(event, enabled):
            pass

        @action(label="A", key="a")
        async def a(event):
            pass

        mod = _make_module(t=t, a=a)
        reg = ManifestRegistry()
        from types import SimpleNamespace as _SN
        core_pkg = importlib.import_module("pyUltroid.ultroid")
        with patch.object(core_pkg, "core", _SN(manifest=reg)):
            count = scan_module("p", mod)
        assert count == 2


# ── surface tests ──────────────────────────────────────────────────────


class TestModuleSurface:
    """Module-level constants and singleton."""

    def test_module_exposes_no_singleton(self):
        import pyUltroid._core.manifest as m

        assert not hasattr(m, "manifest_registry")

    def test_budget_constant_is_positive(self):
        assert PLUGIN_MANIFEST_RESULT_BUDGET > 0

    def test_module_is_import_safe(self):
        """No top-level pyUltroid imports — the module loads without a bot."""
        import importlib

        mod = importlib.import_module("pyUltroid._core.manifest")
        assert mod is not None
        assert hasattr(mod, "toggle")
        assert not hasattr(mod, "manifest_registry")


# ── helpers ────────────────────────────────────────────────────────────


def _make_async_fn():
    async def _h(event, enabled=False):
        return "ok"

    return _h


# ── toggle state persistence ───────────────────────────────────────────


class _FakeDB:
    """The parts of ``udB`` the toggle states use."""

    def __init__(self, initial=None):
        self.store = {} if initial is None else {"PMF_TOGGLES": dict(initial)}
        self.writes = 0

    def get_key(self, key):
        return self.store.get(key)

    def set_key(self, key, value):
        self.store[key] = value
        self.writes += 1
        return True


def _toggle_entry(key="k"):
    return {
        "type": "toggle",
        "label": "L",
        "key": key,
        "icon": "",
        "description": "",
        "handler": _make_async_fn(),
    }


class TestToggleStatePersistence:
    """A toggle the user set must survive a restart.

    The states were written to ``PMF_TOGGLES`` on every flip, but nothing read
    the key back, so every plugin came up with its toggle off again.
    """

    def test_a_stored_state_is_restored_at_registration(self):
        db = _FakeDB({"plug.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            assert reg.get_state("plug.k") is True

    def test_an_unset_toggle_still_defaults_to_off(self):
        db = _FakeDB({"other.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            assert reg.get_state("plug.k") is False

    def test_a_flip_round_trips_through_the_database(self):
        db = _FakeDB()
        with patch("pyUltroid.udB", db):
            first = ManifestRegistry()
            first.register("plug", _toggle_entry())
            state, _ = asyncio.run(first.flip_toggle("plug.k"))
            assert state is True
            # A restart: a brand new registry reading the same database.
            second = ManifestRegistry()
            second.register("plug", _toggle_entry())
            assert second.get_state("plug.k") is True

    def test_a_save_keeps_the_state_of_a_plugin_that_is_not_loaded(self):
        """Only loaded plugins are in ``_states``; the rest must not be erased."""
        db = _FakeDB({"absent.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            asyncio.run(reg.flip_toggle("plug.k"))
            assert db.store["PMF_TOGGLES"]["absent.k"] is True
            assert db.store["PMF_TOGGLES"]["plug.k"] is True

    def test_an_unload_does_not_erase_the_stored_state(self):
        db = _FakeDB()
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            asyncio.run(reg.flip_toggle("plug.k"))
            reg.unregister("plug")
            assert reg.get_state("plug.k") is False
            # Reloading the plugin brings the user's choice back.
            reg.register("plug", _toggle_entry())
            assert reg.get_state("plug.k") is True

    def test_a_reverted_flip_persists_the_original_state(self):
        db = _FakeDB()

        async def boom(event, state):
            raise RuntimeError("no")

        entry = _toggle_entry()
        entry["handler"] = boom
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", entry)
            state, _ = asyncio.run(reg.flip_toggle("plug.k"))
            assert state is False
            assert db.store["PMF_TOGGLES"]["plug.k"] is False

    def test_no_database_yet_is_not_cached_as_empty(self):
        """The registry is built before ``udB`` exists, so the miss must not stick."""
        db = _FakeDB({"plug.k": True})
        with patch("pyUltroid.udB", None):
            reg = ManifestRegistry()
            assert reg._restore_state("plug.k") is False
        with patch("pyUltroid.udB", db):
            assert reg._restore_state("plug.k") is True

    def test_a_corrupt_stored_value_is_ignored(self):
        db = _FakeDB()
        db.store["PMF_TOGGLES"] = "not a mapping"
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            assert reg.get_state("plug.k") is False

    def test_pruning_drops_only_unknown_plugins(self):
        db = _FakeDB({"plug.k": True, "gone.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            assert reg.prune_stored_states(["plug"]) == 1
            assert "gone.k" not in db.store["PMF_TOGGLES"]
            assert db.store["PMF_TOGGLES"]["plug.k"] is True

    def test_pruning_is_never_automatic(self):
        """An absent plugin is usually just not loaded yet, so nothing prunes."""
        db = _FakeDB({"gone.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            asyncio.run(reg.flip_toggle("plug.k"))
            assert db.store["PMF_TOGGLES"]["gone.k"] is True

    def test_pruning_matches_plugins_the_way_unregister_does(self):
        """A key holding its own dot must not be attributed to a missing plugin."""
        db = _FakeDB({"plug.sub.k": True, "gone.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            assert reg.prune_stored_states(["plug"]) == 1
            assert db.store["PMF_TOGGLES"]["plug.sub.k"] is True
            assert "gone.k" not in db.store["PMF_TOGGLES"]

    def test_pruning_with_no_known_plugins_drops_everything(self):
        db = _FakeDB({"a.k": True, "b.k": False})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            assert reg.prune_stored_states([]) == 2
            assert db.store["PMF_TOGGLES"] == {}

    def test_a_save_does_not_overwrite_a_state_written_since_the_read(self):
        """The cache is not trusted at save time, so a later write survives."""
        db = _FakeDB()
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            assert reg.get_state("plug.k") is False  # fills the cache
            db.store["PMF_TOGGLES"] = {"late.k": True}
            asyncio.run(reg.flip_toggle("plug.k"))
            assert db.store["PMF_TOGGLES"]["late.k"] is True
            assert db.store["PMF_TOGGLES"]["plug.k"] is True

    def test_a_failed_read_is_not_cached_as_empty(self):
        """A transient database error must not blank the stored states."""

        class _Flaky(_FakeDB):
            def __init__(self, initial):
                super().__init__(initial)
                self.fail = True

            def get_key(self, key):
                if self.fail:
                    raise RuntimeError("database down")
                return super().get_key(key)

        db = _Flaky({"plug.k": True, "absent.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            assert reg.get_state("plug.k") is False  # the read failed
            db.fail = False
            # Recovery: the stored choice is applied to the registered toggle.
            assert reg.get_state("plug.k") is True

    def test_a_flip_is_not_persisted_while_the_states_cannot_be_read(self):
        """Writing live states over an unreadable mapping would erase choices."""

        class _WriteOnly(_FakeDB):
            def get_key(self, key):
                raise RuntimeError("database down")

        db = _WriteOnly({"absent.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            asyncio.run(reg.flip_toggle("plug.k"))
            assert reg.get_state("plug.k") is True  # the live state still flips
            assert db.writes == 0
            assert db.store["PMF_TOGGLES"] == {"absent.k": True}

    def test_a_flip_after_recovery_starts_from_the_stored_choice(self):
        class _Flaky(_FakeDB):
            def __init__(self, initial):
                super().__init__(initial)
                self.fail = True

            def get_key(self, key):
                if self.fail:
                    raise RuntimeError("database down")
                return super().get_key(key)

        db = _Flaky({"plug.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            db.fail = False
            state, _ = asyncio.run(reg.flip_toggle("plug.k"))
            assert state is False, "the flip must start from the stored True"

    def test_an_empty_stored_mapping_is_still_a_successful_read(self):
        db = _FakeDB({})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            asyncio.run(reg.flip_toggle("plug.k"))
            assert db.store["PMF_TOGGLES"] == {"plug.k": True}

    def test_pruning_does_nothing_when_the_states_cannot_be_read(self):
        class _Down(_FakeDB):
            def get_key(self, key):
                raise RuntimeError("database down")

        db = _Down({"gone.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            assert reg.prune_stored_states(["plug"]) == 0
            assert db.store["PMF_TOGGLES"] == {"gone.k": True}

    def test_a_second_read_cannot_lose_the_first_successful_one(self):
        """The restore path must read once, not probe and then re-read."""

        class _OnceOnly(_FakeDB):
            def __init__(self, initial):
                super().__init__(initial)
                self.reads = 0

            def get_key(self, key):
                self.reads += 1
                if self.reads > 1:
                    raise RuntimeError("database down")
                return super().get_key(key)

        db = _OnceOnly({"plug.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            assert reg.get_state("plug.k") is True

    def test_a_failed_write_is_not_remembered_as_stored(self):
        """A write the database refused must not be cached as persisted."""

        class _ReadOnly(_FakeDB):
            def set_key(self, key, value):
                raise RuntimeError("database read-only")

        db = _ReadOnly({})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            reg.register("plug", _toggle_entry())
            asyncio.run(reg.flip_toggle("plug.k"))
            reg.unregister("plug")
            reg.register("plug", _toggle_entry())
            assert (
                reg.get_state("plug.k") is False
            ), "the flip never reached the database, so a reload must not show it"

    def test_a_failed_prune_write_reports_nothing_dropped(self):
        class _ReadOnly(_FakeDB):
            def set_key(self, key, value):
                raise RuntimeError("database read-only")

        db = _ReadOnly({"gone.k": True})
        with patch("pyUltroid.udB", db):
            reg = ManifestRegistry()
            assert reg.prune_stored_states(["plug"]) == 0
            assert db.store["PMF_TOGGLES"] == {"gone.k": True}
            reg.register("gone", _toggle_entry())
            assert (
                reg.get_state("gone.k") is True
            ), "the prune did not reach the database, so the state is still there"

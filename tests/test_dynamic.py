"""Unit tests for the dynamic runtime patch registry and dataclass schemas.

W3-C acceptance: define/patch/unpatch at runtime, round-trip frozen/unfrozen
models, and verify the documented extension contracts for ``eor``/``eod``/``ikm``.
"""

from __future__ import annotations

import dataclasses
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyUltroid._core.dynamic import (
    AuditRecord,
    ExtensionContract,
    PatchRegistry,
    freeze,
    get_contract,
    list_contracts,
    make_schema,
    register_contract,
    schema_from_dict,
    schema_to_dict,
    unfreeze,
)
from pyUltroid.startup import patching as patching_module


class FakeDB:
    """In-memory stand-in for ``udB`` with the key methods used by the registry."""

    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    def get_key(self, key: str):
        return self._store.get(key)

    def set_key(self, key: str, value: object) -> None:
        self._store[key] = value

    def del_key(self, key: str) -> None:
        self._store.pop(key, None)


class TargetClass:
    """Dummy target class for patch registry tests."""

    def existing_method(self) -> str:
        return "original"


def sample_new_patch(self) -> str:
    """Adds a brand-new attribute."""
    return "new"


def sample_override_patch(self) -> str:
    """Overrides existing_method."""
    return "patched"


class PatchRegistryTests(unittest.TestCase):
    def setUp(self):
        self.db = FakeDB()
        self.registry = PatchRegistry(db=self.db)

    def tearDown(self):
        self.registry.reset()

    def test_register_and_apply_new_attr(self):
        self.registry.register(
            "new_attr", TargetClass, sample_new_patch, attr_name="new_attr"
        )
        self.assertTrue(self.registry.apply("new_attr"))
        self.assertTrue(self.registry.is_applied("new_attr"))
        self.assertEqual(TargetClass().new_attr(), "new")
        self.assertTrue(any(r.action == "apply" for r in self.registry.audit_records()))

    def test_unapply_deletes_new_attr(self):
        self.registry.register(
            "new_attr", TargetClass, sample_new_patch, attr_name="new_attr"
        )
        self.registry.apply("new_attr")
        self.assertTrue(self.registry.unapply("new_attr"))
        self.assertFalse(self.registry.is_applied("new_attr"))
        self.assertFalse(hasattr(TargetClass, "new_attr"))

    def test_unapply_restores_existing_attr(self):
        self.registry.register(
            "override", TargetClass, sample_override_patch, attr_name="existing_method"
        )
        self.registry.apply("override")
        self.assertEqual(TargetClass().existing_method(), "patched")
        self.assertTrue(self.registry.unapply("override"))
        self.assertEqual(TargetClass().existing_method(), "original")

    def test_apply_is_idempotent(self):
        self.registry.register(
            "new_attr", TargetClass, sample_new_patch, attr_name="new_attr"
        )
        self.assertTrue(self.registry.apply("new_attr"))
        self.assertTrue(self.registry.apply("new_attr"))
        self.assertEqual(TargetClass().new_attr(), "new")
        apply_records = [
            r for r in self.registry.audit_records() if r.action == "apply"
        ]
        self.assertEqual(len(apply_records), 2)
        self.assertTrue(all(r.success for r in apply_records))

    def test_unapply_is_idempotent(self):
        self.registry.register("new_attr", TargetClass, sample_new_patch)
        self.assertFalse(self.registry.unapply("new_attr"))
        self.assertFalse(self.registry.is_applied("new_attr"))

    def test_enable_disable_persistence(self):
        self.registry.register(
            "toggle", TargetClass, sample_new_patch, attr_name="toggle", enabled=True
        )
        self.registry.disable("toggle")
        self.assertFalse(self.registry.is_enabled("toggle"))
        self.assertFalse(self.registry.apply("toggle"))
        self.assertFalse(self.registry.is_applied("toggle"))

        self.registry.enable("toggle")
        self.assertTrue(self.registry.is_enabled("toggle"))
        self.assertTrue(self.registry.apply("toggle"))
        self.assertTrue(self.registry.is_applied("toggle"))

        # Disable again so we can verify a fresh registry reads the persisted flag.
        self.registry.disable("toggle")

        # Simulate a fresh registry reading the same database.
        fresh = PatchRegistry(db=self.db)
        fresh.register(
            "toggle", TargetClass, sample_new_patch, attr_name="toggle", enabled=True
        )
        self.assertFalse(fresh.is_enabled("toggle"))

    def test_apply_all_skips_disabled(self):
        self.registry.register("a", TargetClass, sample_new_patch, attr_name="a")
        self.registry.register("b", TargetClass, lambda self: "b", attr_name="b")
        self.registry.disable("b")
        results = self.registry.apply_all()
        self.assertTrue(results["a"])
        self.assertFalse(results["b"])
        self.assertTrue(self.registry.is_applied("a"))
        self.assertFalse(self.registry.is_applied("b"))

    def test_unapply_all(self):
        self.registry.register("a", TargetClass, sample_new_patch, attr_name="a")
        self.registry.apply("a")
        self.registry.register("b", TargetClass, lambda self: "b", attr_name="b")
        self.registry.apply("b")
        results = self.registry.unapply_all()
        self.assertTrue(results["a"])
        self.assertTrue(results["b"])
        self.assertEqual(self.registry.applied_patches(), [])

    def test_duplicate_registration_raises(self):
        self.registry.register("dup", TargetClass, sample_new_patch, attr_name="dup")
        with self.assertRaises(ValueError):
            self.registry.register("dup", TargetClass, lambda self: "x")

    def test_allow_override(self):
        self.registry.register("ov", TargetClass, sample_new_patch, attr_name="ov")
        self.registry.register(
            "ov", TargetClass, lambda self: "x", attr_name="ov", allow_override=True
        )
        self.registry.apply("ov")
        self.assertEqual(TargetClass().ov(), "x")

    def test_unknown_patch_raises(self):
        with self.assertRaises(KeyError):
            self.registry.apply("missing")
        with self.assertRaises(KeyError):
            self.registry.is_enabled("missing")
        with self.assertRaises(KeyError):
            self.registry.is_applied("missing")

    def test_default_attr_name_strips_single_leading_underscore(self):
        """Legacy behaviour: ``@patch`` decorator stripped one leading underscore."""

        def _underscored(self) -> str:
            return "stripped"

        self.registry.register("underscore", TargetClass, _underscored)
        self.registry.apply("underscore")
        self.assertEqual(TargetClass().underscored(), "stripped")
        self.assertTrue(self.registry.unapply("underscore"))
        self.assertFalse(hasattr(TargetClass, "underscored"))

    def test_audit_records_are_snapshot(self):
        self.registry.register("x", TargetClass, sample_new_patch, attr_name="x")
        self.registry.apply("x")
        first = self.registry.audit_records()
        self.registry.unapply("x")
        second = self.registry.audit_records()
        self.assertEqual(len(second), len(first) + 1)
        self.assertIsInstance(first[0], AuditRecord)

    def test_no_db_means_no_persistence_but_still_works(self):
        reg = PatchRegistry(db=None)
        reg.register("x", TargetClass, sample_new_patch, attr_name="x", enabled=True)
        self.assertTrue(reg.is_enabled("x"))
        reg.disable("x")
        self.assertFalse(reg.is_enabled("x"))
        reg.enable("x")
        self.assertTrue(reg.is_enabled("x"))
        self.assertTrue(reg.apply("x"))
        self.assertTrue(reg.unapply("x"))


class DynamicDataclassTests(unittest.TestCase):
    def test_make_mutable_schema(self):
        Config = make_schema("Config", [("x", int), ("y", str, "default")])
        self.assertTrue(dataclasses.is_dataclass(Config))
        self.assertFalse(Config.__dataclass_params__.frozen)
        c = Config(x=1, y="a")
        self.assertEqual(c.x, 1)
        c.x = 2  # mutable
        self.assertEqual(c.x, 2)

    def test_make_frozen_schema(self):
        Frozen = make_schema("Frozen", [("x", int)], frozen=True)
        self.assertTrue(Frozen.__dataclass_params__.frozen)
        f = Frozen(x=5)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            f.x = 9

    def test_freeze_unfreeze_round_trip(self):
        Config = make_schema(
            "Config", [("x", int), ("y", str, "default")], frozen=False
        )
        c = Config(x=1, y="a")
        f = freeze(c)
        self.assertTrue(f.__dataclass_params__.frozen)
        self.assertEqual(f.x, 1)
        self.assertEqual(f.y, "a")
        u = unfreeze(f)
        self.assertFalse(u.__dataclass_params__.frozen)
        self.assertEqual(u.x, 1)
        self.assertEqual(u.y, "a")
        f2 = freeze(u)
        self.assertTrue(f2.__dataclass_params__.frozen)
        self.assertEqual(f2.x, 1)
        self.assertEqual(f2.y, "a")

    def test_freeze_already_frozen_is_no_op(self):
        Frozen = make_schema("Frozen", [("x", int)], frozen=True)
        f = Frozen(x=1)
        self.assertIs(freeze(f), f)

    def test_unfreeze_already_mutable_is_no_op(self):
        Mutable = make_schema("Mutable", [("x", int)], frozen=False)
        m = Mutable(x=1)
        self.assertIs(unfreeze(m), m)

    def test_schema_to_dict_and_back(self):
        Config = make_schema(
            "Config", [("x", int), ("y", str, "default")], frozen=False
        )
        c = Config(x=1, y="a")
        d = schema_to_dict(c)
        self.assertEqual(d, {"x": 1, "y": "a"})
        c2 = schema_from_dict(Config, d)
        self.assertEqual(c2, c)

    def test_nested_schema_round_trip(self):
        Inner = make_schema("Inner", [("z", int)], frozen=False)
        Outer = make_schema(
            "Outer",
            [
                ("inner", Inner),
                ("items", list[Inner], dataclasses.field(default_factory=list)),
            ],
            frozen=False,
        )
        o = Outer(inner=Inner(z=42), items=[Inner(z=1), Inner(z=2)])
        f = freeze(o)
        self.assertTrue(f.__dataclass_params__.frozen)
        self.assertTrue(f.inner.__dataclass_params__.frozen)
        self.assertTrue(all(item.__dataclass_params__.frozen for item in f.items))
        u = unfreeze(f)
        self.assertFalse(u.__dataclass_params__.frozen)
        self.assertFalse(u.inner.__dataclass_params__.frozen)
        self.assertEqual(u.inner.z, 42)
        self.assertEqual(len(u.items), 2)
        self.assertEqual([item.z for item in u.items], [1, 2])

    def test_schema_registry_reuses_counterpart(self):
        M = make_schema("Counterpart", [("x", int)], frozen=False)
        F = make_schema("Counterpart", [("x", int)], frozen=True)
        self.assertIs(M._schema_counterpart, F)
        self.assertIs(F._schema_counterpart, M)

    def test_non_dataclass_raises(self):
        with self.assertRaises(TypeError):
            freeze(123)
        with self.assertRaises(TypeError):
            unfreeze("abc")
        with self.assertRaises(TypeError):
            schema_to_dict([])
        with self.assertRaises(TypeError):
            schema_from_dict(int, {})


class ExtensionContractTests(unittest.TestCase):
    def test_register_and_lookup_contract(self):
        def demo(a: int, b: str) -> str:
            return f"{a}{b}"

        contract = register_contract(
            "demo",
            demo,
            description="demo contract",
            category="test",
            signature=("a", "b"),
        )
        self.assertIsInstance(contract, ExtensionContract)
        self.assertIs(contract.func, demo)
        self.assertEqual(get_contract("demo").description, "demo contract")
        self.assertIn("demo", {c.name for c in list_contracts(category="test")})

    def test_builtin_telegram_contracts(self):
        contracts = list_contracts(category="telegram")
        names = {c.name for c in contracts}
        self.assertIn("eor", names)
        self.assertIn("eod", names)
        self.assertIn("ikm", names)
        self.assertIsNotNone(get_contract("eor"))
        self.assertIn("telethon.tl.custom.Message", get_contract("eor").description)
        self.assertIn("custom.tg", get_contract("ikm").description)
        self.assertIn("pyUltroid._misc._wrappers", get_contract("eod").description)


class PatchingModuleCompatibilityTests(unittest.TestCase):
    def test_patches_list_still_populated(self):
        self.assertIsInstance(patching_module.PATCHES, list)
        names = {func.__name__ for _, func in patching_module.PATCHES}
        self.assertIn("eor", names)
        self.assertIn("try_delete", names)

    def test_registry_exposes_core_patches(self):
        registry = patching_module.get_patch_registry()
        names = {p.name for p in registry.list_patches()}
        self.assertIn("eor", names)
        self.assertIn("try_delete", names)

    def test_bind_database_does_not_require_db(self):
        # Fresh binding is allowed; registry keeps working.
        patching_module.bind_database(None)
        registry = patching_module.get_patch_registry()
        self.assertIsNotNone(registry)

    def test_core_patches_are_explicitly_forced(self):
        self.assertEqual(patching_module.CORE_PATCHES, frozenset({"eor", "try_delete"}))

    def test_apply_patches_respects_persisted_state_after_bind(self):
        """Reproduce startup ordering: import (registry created) -> bind udB -> apply."""
        original_registry = patching_module._PATCH_REGISTRY
        original_core = patching_module.CORE_PATCHES
        try:
            # Fresh registry simulates the state after import, before udB exists.
            mock_registry = PatchRegistry(db=None)
            patching_module._PATCH_REGISTRY = mock_registry
            patching_module.CORE_PATCHES = frozenset({"core_patch"})
            mock_registry.register(
                "core_patch", TargetClass, lambda self: "core", attr_name="core_patch"
            )
            mock_registry.register(
                "user_patch", TargetClass, lambda self: "user", attr_name="user_patch"
            )

            # User previously disabled user_patch in udB.
            db = FakeDB()
            db.set_key("DYNAMIC_PATCH_STATE", {"user_patch": False})

            # Simulate startup: bind database, then apply patches.
            patching_module.bind_database(db)
            patching_module.apply_patches()

            self.assertTrue(mock_registry.is_applied("core_patch"))
            self.assertFalse(mock_registry.is_applied("user_patch"))
            self.assertTrue(hasattr(TargetClass, "core_patch"))
            self.assertFalse(hasattr(TargetClass, "user_patch"))
        finally:
            # Unapply any leftover test attributes and restore the global registry.
            if patching_module._PATCH_REGISTRY is not original_registry:
                for name in ("core_patch", "user_patch"):
                    if name in patching_module._PATCH_REGISTRY._patches:
                        patching_module._PATCH_REGISTRY.unapply(name)
            patching_module._PATCH_REGISTRY = original_registry
            patching_module.CORE_PATCHES = original_core

    def test_bind_after_import_reloads_disabled_state(self):
        """A patch registered before db is bound must read persisted state on apply."""
        registry = PatchRegistry(db=None)
        registry.register(
            "late_bind", TargetClass, lambda self: "late", attr_name="late_bind"
        )
        db = FakeDB()
        db.set_key("DYNAMIC_PATCH_STATE", {"late_bind": False})
        registry.bind_database(db)
        self.assertFalse(registry.apply("late_bind"))
        self.assertFalse(registry.is_applied("late_bind"))


if __name__ == "__main__":
    unittest.main()

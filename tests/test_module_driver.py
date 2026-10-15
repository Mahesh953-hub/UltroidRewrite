"""The W3-B Module driver: ``ModuleRegistry.adopt`` / ``drop_from``.

The driver is what the Module layer was missing - the ``Extender.loads``
half of the re-caligo port. The Ultroid loader calls ``adopt`` right after
importing a plugin file; this file pins the driver's own semantics:

* only classes *defined in* the plugin module are adopted,
* a reload re-adopts without double-binding,
* a failed module is reported, never raised,
* ``unload`` resets the started latch so a reload runs the lifecycle again,
* ``drop_from`` removes exactly one plugin's modules on ``.plugins unload``.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from test_genai_module_plugin import FakeClient, _fake_telethon, _load_layer


@pytest.fixture
def layer(module_isolation):
    """The Module layer, loaded standalone (no pyUltroid bootstrap)."""
    invented = _fake_telethon()
    loaded = _load_layer()
    try:
        with module_isolation("pyUltroid"):
            yield loaded
    finally:
        import sys

        for name in [
            n
            for n in sys.modules
            if n == "ul_misc" or n.startswith("ul_misc.")
        ]:
            sys.modules.pop(name, None)
        for name in invented:
            sys.modules.pop(name, None)


class Builder:
    """A stand-in Telethon event builder; the registry stores it verbatim."""

    def __init__(self, tag: str) -> None:
        self.tag = tag


def _plugin_module(layer, name: str, classes: dict) -> types.ModuleType:
    """A fake plugin module whose classes are *defined in* it."""
    mod = types.ModuleType(name)
    for cls in classes.values():
        cls.__module__ = name
    vars(mod).update(classes)
    return mod


def _make_class(layer, name: str, started: list | None = None, broken: bool = False):
    """Build a one-handler Module class, optionally counting lifecycle runs."""

    class Cls(layer.module.Module):
        name_ = name

        def __init__(self, client=None, registry=None, **options):
            super().__init__(client, registry, **options)
            if broken:
                raise RuntimeError("broken module")

        @layer.listener.handler(Builder(name), 1)
        async def on_ping(self, event):
            return None

        async def on_starting(self):
            if started is not None:
                started.append(name)

    Cls.__name__ = name
    return Cls


# ── adopt --------------------------------------------------------------------


def test_adopt_loads_and_binds_classes_defined_in_the_plugin(layer):
    registry = layer.registry.ModuleRegistry()
    client = FakeClient()
    Alive = _make_class(layer, "Alive")
    mod = _plugin_module(layer, "fakeplug", {"Alive": Alive})

    report = registry.adopt(mod, clients=[client])
    assert report == {"Alive": {"handlers": 1}}
    assert len(client.handlers) == 1
    assert "Alive" in registry


def test_adopt_skips_classes_imported_from_elsewhere(layer):
    registry = layer.registry.ModuleRegistry()
    client = FakeClient()
    Alive = _make_class(layer, "Alive")
    # a Module subclass merely imported by the plugin (like BoundModule is)
    Foreign = _make_class(layer, "Foreign")
    mod = _plugin_module(layer, "fakeplug", {"Alive": Alive, "Foreign": Foreign})
    Foreign.__module__ = "somewhere.else"

    report = registry.adopt(mod, clients=[client])
    assert report == {"Alive": {"handlers": 1}}
    assert "Foreign" not in registry


def test_adopt_of_a_reloaded_plugin_never_double_binds(layer):
    registry = layer.registry.ModuleRegistry()
    client = FakeClient()
    first = _make_class(layer, "Alive")
    mod = _plugin_module(layer, "fakeplug", {"Alive": first})
    registry.adopt(mod, clients=[client])
    assert len(client.handlers) == 1

    # a reload produces a fresh class object with the same name
    second = _make_class(layer, "Alive")
    mod = _plugin_module(layer, "fakeplug", {"Alive": second})
    report = registry.adopt(mod, clients=[client])

    assert report == {"Alive": {"handlers": 1}}
    assert len(client.handlers) == 1, "the stale instance must be unbound"
    assert registry.get("Alive") is not None


def test_adopt_isolates_a_broken_module(layer):
    registry = layer.registry.ModuleRegistry()
    client = FakeClient()
    Broken = _make_class(layer, "Broken", broken=True)
    Alive = _make_class(layer, "Alive")
    mod = _plugin_module(
        layer, "fakeplug", {"Broken": Broken, "Alive": Alive}
    )

    report = registry.adopt(mod, clients=[client])
    assert "error" in report["Broken"], report
    assert "RuntimeError" in report["Broken"]["error"]
    assert report["Alive"] == {"handlers": 1}, "the sibling still loads"


# ── lifecycle ----------------------------------------------------------------


def test_unload_resets_the_started_latch_for_a_reload(layer):
    started: list = []
    registry = layer.registry.ModuleRegistry()
    client = FakeClient()
    cls = _make_class(layer, "Alive", started=started)
    mod = _plugin_module(layer, "fakeplug", {"Alive": cls})
    registry.adopt(mod, clients=[client])
    asyncio.run(registry.ensure_started())
    assert started == ["Alive"]

    registry.unload("Alive")
    fresh = _make_class(layer, "Alive", started=started)
    registry.adopt(_plugin_module(layer, "fakeplug", {"Alive": fresh}), clients=[client])
    asyncio.run(registry.ensure_started())
    assert started == ["Alive", "Alive"], "the reload must run on_starting again"


# ── drop_from -----------------------------------------------------------------


def test_drop_from_removes_exactly_one_plugins_modules(layer):
    registry = layer.registry.ModuleRegistry()
    client = FakeClient()
    mine = _plugin_module(layer, "plug.a", {"Mine": _make_class(layer, "Mine")})
    other = _plugin_module(layer, "plug.b", {"Other": _make_class(layer, "Other")})
    registry.adopt(mine, clients=[client])
    registry.adopt(other, clients=[client])
    assert len(registry) == 2

    gone = registry.drop_from("plug.a")
    assert gone == ("Mine",)
    assert "Mine" not in registry and "Other" in registry
    # the dropped module's handlers are unbound from the client too
    assert len(client.handlers) == 1

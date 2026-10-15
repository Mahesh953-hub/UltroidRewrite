"""The Module layer in ``pyUltroid/_misc/``: a feature declared as a class
whose handlers are methods and whose lifecycle is declared.

The four layer files (``module``, ``listener``, ``methods``, ``registry``)
carry **no top-level ``pyUltroid`` import**, so the suite loads them as a
standalone package: that property is the folder's circular-import guarantee
and it is asserted directly rather than assumed.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
import types

import pytest

from conftest import ROOT

MISC = ROOT / "pyUltroid" / "_misc"
LAYER = ("listener", "methods", "module", "registry")


@pytest.fixture(scope="module")
def layer():
    """The Module layer loaded under a neutral package name.

    Deliberately not ``pyUltroid._misc``: importing that runs
    ``from .. import *`` and the whole decorator stack. Here we prove the
    layer stands on its own.
    """
    saved = {n: m for n, m in sys.modules.items() if n == "ul_misc" or n.startswith("ul_misc.")}
    for name in saved:
        sys.modules.pop(name, None)
    package = types.ModuleType("ul_misc")
    package.__path__ = [str(MISC)]
    package.__package__ = "ul_misc"
    sys.modules["ul_misc"] = package
    try:
        for name in LAYER:
            spec = importlib.util.spec_from_file_location(
                f"ul_misc.{name}", MISC / f"{name}.py"
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[f"ul_misc.{name}"] = module
            spec.loader.exec_module(module)
        yield types.SimpleNamespace(
            listener=sys.modules["ul_misc.listener"],
            methods=sys.modules["ul_misc.methods"],
            module=sys.modules["ul_misc.module"],
            registry=sys.modules["ul_misc.registry"],
        )
    finally:
        for name in [n for n in sys.modules if n == "ul_misc" or n.startswith("ul_misc.")]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


class Builder:
    """Stand-in for a Telethon event builder."""

    def __init__(self, pattern: str):
        self.build = {"pattern": pattern}

    def __repr__(self) -> str:
        return f"Builder({self.build['pattern']!r})"


class FakeClient:
    def __init__(self) -> None:
        self.handlers: list[tuple] = []

    def add_event_handler(self, callback, event=None) -> None:
        self.handlers.append((callback, event))

    def remove_event_handler(self, callback) -> None:
        self.handlers = [h for h in self.handlers if h[0] is not callback]


class FailingClient(FakeClient):
    """Fails on the second handler, to exercise bind rollback."""

    def add_event_handler(self, callback, event=None) -> None:
        if len(self.handlers) >= 1:
            raise RuntimeError("client refused the handler")
        super().add_event_handler(callback, event)


def make_module(layer, cls_name="Demo", **attrs):
    """Build a Module subclass bound to this test's layer."""
    body = {
        "name": cls_name,
        "cmds": "d(emo)?",
        "desc": {"?": "Optional"},
        "on_started_calls": [],
        **attrs,
    }
    return type(cls_name, (layer.module.BoundModule,), body)


# ── 1. the import contract ────────────────────────────────────────────


def test_layer_files_have_no_top_level_pyultroid_imports():
    import ast

    offenders = []
    for name in LAYER:
        path = MISC / f"{name}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                found = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                found = [node.module or ""] + [
                    f"{node.module or ''}.{a.name}" for a in node.names
                ]
            else:
                continue
            for item in found:
                if item.startswith("pyUltroid") or item.startswith(".."):
                    offenders.append(f"{path.name}:{node.lineno} {item}")
    assert not offenders, offenders


def test_layer_loads_without_the_pyultroid_root(tmp_path):
    """The circular-import guarantee, checked in a clean interpreter.

    Asserting ``"pyUltroid" not in sys.modules`` inside the test process
    would depend on whatever ran before, so this loads the layer from a
    subprocess instead.
    """
    import subprocess

    probe = tmp_path / "probe.py"
    probe.write_text(
        "import importlib.util, pathlib, sys\n"
        f"root = pathlib.Path({str(MISC)!r})\n"
        f"names = {LAYER!r}\n"
        "import types\n"
        "package = types.ModuleType('probe')\n"
        "package.__path__ = [str(root)]\n"
        "package.__package__ = 'probe'\n"
        "sys.modules['probe'] = package\n"
        "for name in names:\n"
        "    spec = importlib.util.spec_from_file_location(\n"
        "        'probe.' + name, root / (name + '.py')\n"
        "    )\n"
        "    module = importlib.util.module_from_spec(spec)\n"
        "    sys.modules['probe.' + name] = module\n"
        "    spec.loader.exec_module(module)\n"
        "print('pyUltroid' in sys.modules)\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(probe)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", "the Module layer pulled in pyUltroid"


# ── 2. the tagger ─────────────────────────────────────────────────────


def test_handler_only_tags_and_never_binds(layer):
    calls = []

    class C:
        def add_event_handler(self, cb, ev=None):
            calls.append(cb)

    client = C()

    @layer.listener.handler(Builder("^p$"), 3)
    async def on_thing(event):
        return "x"

    assert calls == [], "the tagger must not bind at decoration time"
    assert layer.listener.is_handler(on_thing)
    assert getattr(on_thing, layer.listener.PRIORITY_ATTR) == 3
    client.add_event_handler(on_thing, Builder("^p$"))
    assert calls == [on_thing]


def test_handler_tag_derives_the_event_name(layer):
    tag = layer.listener.handler(Builder("^ping$"), 1)
    assert tag.event == "^ping$"
    assert tag.describe() == {
        "event": "^ping$",
        "priority": 1,
        "builder": "Builder",
    }
    # an attribute-style builder also works
    class Attr:
        pattern = "^attr$"

    assert layer.listener.handler(Attr()).event == "^attr$"
    # and a hopeless builder degrades to its class name rather than raising
    assert layer.listener.handler(object()).event == "object"


def test_tag_of_rejects_an_untagged_function(layer):
    with pytest.raises(TypeError):
        layer.listener.tag_of(lambda: None)


# ── 3. discovery ──────────────────────────────────────────────────────


def test_handlers_are_discovered_in_declaration_order(layer):
    h = layer.listener.handler

    class Demo(layer.module.Module):
        @h(Builder("^c$"), 1)
        async def on_c(self, event): ...

        @h(Builder("^a$"), 2)
        async def on_a(self, event): ...

        @h(Builder("^b$"), 3)
        async def on_b(self, event): ...

        async def on_started(self): ...

        def helper(self): ...

    # declaration order, NOT alphabetical: on_c before on_a before on_b
    assert Demo.handler_names() == ("on_c", "on_a", "on_b")
    # an untagged on_* method is not a handler
    assert "on_started" not in Demo.handler_names()
    assert "helper" not in Demo.handler_names()


def test_subclass_override_replaces_the_parent_handler(layer):
    h = layer.listener.handler

    class Base(layer.module.Module):
        @h(Builder("^base$"), 1)
        async def on_thing(self, event): ...

    class Child(Base):
        @h(Builder("^child$"), 2)
        async def on_thing(self, event): ...

    assert Child.handler_names() == ("on_thing",)
    assert dict(Child.handlers)["on_thing"].event == "^child$"


def test_describe_and_event_listing(layer):
    h = layer.listener.handler

    class Demo(layer.module.Module):
        name = "Demo Module"
        cmds = "d"
        desc = {"a": "b"}

        @h(Builder("^x$"), 1)
        async def on_x(self, event): ...

        @h(Builder("^x$"), 2)
        async def on_y(self, event): ...

    described = Demo.describe()
    assert described["name"] == "Demo Module"
    assert described["cmds"] == "d"
    assert described["desc"] == {"a": "b"}
    assert described["handlers"] == ["on_x", "on_y"]
    assert described["events"] == ["^x$"], "distinct events, first-declaration order"


# ── 4. listener ordering ──────────────────────────────────────────────


def test_listener_ordering_is_total_and_stable(layer):
    module_obj = object()
    a = layer.listener.Listener(module_obj, "e", lambda: 1, None, 5)
    b = layer.listener.Listener(module_obj, "e", lambda: 2, None, 1)
    c = layer.listener.Listener(module_obj, "e", lambda: 3, None, 5)
    assert sorted([a, b, c]) == [b, a, c], "priority first, then insertion order"
    assert b < a and a < c and not (c < b)
    assert (a == c) is False
    assert len({a, b, c}) == 3
    assert "Listener" in repr(a)


def test_listener_comparison_with_a_foreign_type_is_notimplemented(layer):
    listener = layer.listener.Listener(object(), "e", lambda: 1, None)
    with pytest.raises(TypeError):
        listener < 1


# ── 5. registry: load / bind / unbind ─────────────────────────────────


def test_load_registers_and_binds(layer):
    h = layer.listener.handler

    class Demo(layer.module.Module):
        @h(Builder("^d$"), 1)
        async def on_d(self, event): ...

    client = FakeClient()
    reg = layer.registry.ModuleRegistry(client=client)
    module_obj = reg.load(Demo)
    assert reg.names() == ("Demo",)
    assert reg.get("Demo") is module_obj
    assert "Demo" in reg and len(reg) == 1

    assert reg.bind(module_obj) == 1
    assert len(client.handlers) == 1
    callback, event = client.handlers[0]
    assert callback == module_obj.on_d
    assert event is not None
    assert reg.bound_modules() == {"Demo": 1}
    assert [li.event for li in reg.listeners] == ["^d$"]


def test_duplicate_load_raises_module_exists(layer):
    class Demo(layer.module.Module):
        pass

    reg = layer.registry.ModuleRegistry(client=FakeClient())
    reg.load(Demo)
    with pytest.raises(layer.module.ModuleExists):
        reg.load(Demo)
    # replace=True is the documented escape
    assert reg.load(Demo, replace=True) is not None


def test_load_rejects_a_non_module(layer):
    reg = layer.registry.ModuleRegistry(client=FakeClient())
    with pytest.raises(layer.module.ModuleError):
        reg.load(dict)


def test_unload_of_an_unknown_name_raises(layer):
    reg = layer.registry.ModuleRegistry(client=FakeClient())
    with pytest.raises(layer.module.ModuleMissing):
        reg.unload("Nope")


def test_bind_rolls_back_on_a_client_failure(layer):
    """A half-bound module is worse than an unbound one."""
    h = layer.listener.handler

    class Demo(layer.module.Module):
        @h(Builder("^a$"), 1)
        async def on_a(self, event): ...

        @h(Builder("^b$"), 2)
        async def on_b(self, event): ...

    client = FailingClient()
    reg = layer.registry.ModuleRegistry(client=client)
    module_obj = reg.load(Demo)
    with pytest.raises(RuntimeError):
        reg.bind(module_obj)
    assert client.handlers == [], "the first handler must be rolled back"
    assert reg.listeners == [], "and no listener may survive a failed bind"


def test_unbind_detaches_everything(layer):
    h = layer.listener.handler

    class Demo(layer.module.Module):
        @h(Builder("^a$"), 1)
        async def on_a(self, event): ...

        @h(Builder("^b$"), 2)
        async def on_b(self, event): ...

    client = FakeClient()
    reg = layer.registry.ModuleRegistry(client=client)
    module_obj = reg.load(Demo)
    reg.bind(module_obj)
    assert len(client.handlers) == 2
    reg.unbind(module_obj)
    assert client.handlers == []
    assert reg.listeners == []


def test_bind_fans_out_across_multiple_clients(layer):
    h = layer.listener.handler

    class Demo(layer.module.Module):
        @h(Builder("^a$"), 1)
        async def on_a(self, event): ...

    clients = [FakeClient(), FakeClient(), FakeClient()]
    reg = layer.registry.ModuleRegistry(client=clients)
    module_obj = reg.load(Demo)
    assert reg.bind(module_obj) == 3
    assert all(len(c.handlers) == 1 for c in clients)


def test_registry_accepts_a_clients_callable(layer):
    h = layer.listener.handler

    class Demo(layer.module.Module):
        @h(Builder("^a$"), 1)
        async def on_a(self, event): ...

    client = FakeClient()

    class Holder:
        clients = staticmethod(lambda: [client])

    reg = layer.registry.ModuleRegistry(client=Holder())
    reg.bind(reg.load(Demo))
    assert len(client.handlers) == 1


# ── 6. lifecycle ──────────────────────────────────────────────────────


def test_dispatch_runs_every_hook_and_isolates_failures(layer):
    calls = []

    class Good(layer.module.Module):
        async def on_started(self):
            calls.append("good")

    class Bad(layer.module.Module):
        async def on_started(self):
            raise RuntimeError("nope")

    class Silent(layer.module.Module):
        pass

    reg = layer.registry.ModuleRegistry(client=FakeClient())
    reg.loads([Good, Bad, Silent])
    results = asyncio.run(reg.dispatch(layer.module.STARTED))
    assert calls == ["good"]
    assert results["Good"] is None
    assert isinstance(results["Bad"], RuntimeError)
    assert results["Silent"] is None, "a module without the hook is not an error"
    assert reg.status()["started"] is True


def test_stop_dispatches_teardown_and_unloads(layer):
    seen = []

    class Demo(layer.module.Module):
        @layer.listener.handler(Builder("^a$"), 1)
        async def on_a(self, event): ...

        async def on_stopping(self):
            seen.append("stopping")

        async def on_stopped(self):
            seen.append("stopped")

    client = FakeClient()
    reg = layer.registry.ModuleRegistry(client=client)
    module_obj = reg.load(Demo)
    reg.bind(module_obj)
    module_obj._mark_started()

    asyncio.run(reg.stop())
    assert seen == ["stopping", "stopped"]
    assert module_obj.stopped is True
    assert len(reg) == 0
    assert client.handlers == []
    assert reg.status()["started"] is False


def test_dispatch_passes_arguments_through(layer):
    seen = []

    class Demo(layer.module.Module):
        async def on_starting(self, value):
            seen.append(value)

    reg = layer.registry.ModuleRegistry(client=FakeClient())
    reg.load(Demo)
    asyncio.run(reg.dispatch(layer.module.STARTING, 42))
    assert seen == [42]


def test_ensure_started_is_lazy_and_idempotent(layer):
    """The plugin-loader case: nothing else will call ``start()`` for us."""
    calls = []

    class Demo(layer.module.Module):
        async def on_starting(self):
            calls.append("starting")

        async def on_started(self):
            calls.append("started")

    reg = layer.registry.ModuleRegistry(client=FakeClient())
    module_obj = reg.load(Demo)

    assert asyncio.run(reg.ensure_started()) == {"Demo": None}
    assert calls == ["starting", "started"]
    assert module_obj.started is True

    asyncio.run(reg.ensure_started())
    assert calls == ["starting", "started"], "must not run twice"


def test_ensure_started_retries_after_a_failing_hook(layer):
    """A broken ``on_starting`` must not latch the module as started."""
    attempts = []

    class Demo(layer.module.Module):
        async def on_starting(self):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("first attempt fails")

    reg = layer.registry.ModuleRegistry(client=FakeClient())
    module_obj = reg.load(Demo)
    first = asyncio.run(reg.ensure_started())
    assert isinstance(first["Demo"], RuntimeError)
    assert module_obj.started is False

    second = asyncio.run(reg.ensure_started())
    assert second["Demo"] is None
    assert module_obj.started is True
    assert len(attempts) == 2


def test_ensure_started_accepts_a_single_module(layer):
    calls = []

    class One(layer.module.Module):
        async def on_started(self):
            calls.append("one")

    class Two(layer.module.Module):
        async def on_started(self):
            calls.append("two")

    reg = layer.registry.ModuleRegistry(client=FakeClient())
    one = reg.load(One)
    reg.load(Two)
    asyncio.run(reg.ensure_started(one))
    assert calls == ["one"]


def test_ensure_started_ignores_an_unknown_name(layer):
    reg = layer.registry.ModuleRegistry(client=FakeClient())
    assert asyncio.run(reg.ensure_started("Nope")) == {}


# ── 7. discovery by scan ──────────────────────────────────────────────


def test_scan_discovers_module_subclasses_in_a_package(layer, tmp_path):
    """Auto-discovery, the way ``re-caligo``'s Extender.loads does it."""
    package = tmp_path / "fake_modules"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "widgets.py").write_text(
        "import ul_misc.module as _m\n"
        "import ul_misc.listener as _l\n"
        "\n"
        "class NotAModule:\n"
        "    pass\n"
        "\n"
        "class Widget(_m.Module):\n"
        "    name = 'Widget'\n"
        "    @_l.handler(object(), 1)\n"
        "    async def on_widget(self, event):\n"
        "        return 'widget'\n"
        "\n"
        "class Hidden(_m.Module):\n"
        "    discoverable = False\n",
        encoding="utf-8",
    )
    # a module that raises on import must not abort the whole scan
    (package / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    sys.path.insert(0, str(tmp_path))
    try:
        found = layer.registry.ModuleRegistry().scan("fake_modules")
    finally:
        sys.path.remove(str(tmp_path))

    names = {cls.__name__ for cls in found}
    assert "Widget" in names, found
    assert "NotAModule" not in names
    assert "Hidden" not in names, "discoverable=False must be honoured"
    for name in ("fake_modules", "fake_modules.widgets", "fake_modules.broken"):
        sys.modules.pop(name, None)


def test_scan_survives_a_missing_package(layer):
    reg = layer.registry.ModuleRegistry(client=FakeClient())
    assert reg.scan("ul_misc.definitely_absent") == ()


# ── 8. the bound-verb mixin ───────────────────────────────────────────


def test_mixin_exposes_the_verb_table(layer):
    module_obj = make_module(layer)
    verbs = module_obj().verbs()
    for expected in ("fmtmsg", "ikm", "fmtsec", "time_formatter"):
        assert expected in verbs


def test_mixin_raises_a_clear_error_for_an_unknown_verb(layer):
    module_obj = make_module(layer)()
    with pytest.raises(layer.methods.MissingVerb, match="no verb 'nope'"):
        module_obj.verb("nope")


def test_mixin_reports_a_missing_helper_by_path(layer):
    module_obj = make_module(layer)()
    module_obj.VERBS = {"ghost": "definitely.not.a.module.ghost"}
    with pytest.raises(layer.methods.MissingVerb, match="definitely.not.a.module"):
        module_obj.verb("ghost")


def test_mixin_verb_table_is_per_class(layer):
    module_obj = make_module(layer)()
    module_obj.VERBS = dict(module_obj.VERBS, extra="custom.fmt.fmtmsg")
    assert "extra" in module_obj.verbs()
    assert "extra" not in make_module(layer)().verbs(), "not shared across classes"


# ── 9. module-level API ───────────────────────────────────────────────


def test_module_registry_singleton_exists(layer):
    assert isinstance(layer.registry.registry, layer.registry.ModuleRegistry)


def test_register_module_helper_loads_and_binds(layer):
    h = layer.listener.handler

    class Demo(layer.module.Module):
        @h(Builder("^d$"), 1)
        async def on_d(self, event): ...

    client = FakeClient()
    reg = layer.registry.ModuleRegistry()
    module_obj = layer.registry.register_module(Demo, client=client, reg=reg)
    assert len(client.handlers) == 1
    assert reg.names() == ("Demo",)
    assert module_obj.client is client


def test_module_option_lookup_falls_back_to_the_client(layer):
    client = types.SimpleNamespace(api_id=1234)
    module_obj = make_module(layer)(client=client, explicit="yes")
    assert module_obj.option("explicit") == "yes"
    assert module_obj.option("api_id") == 1234
    assert module_obj.option("nope", "fallback") == "fallback"


def test_module_repr_reports_name_and_handler_count(layer):
    module_obj = make_module(layer)(client=FakeClient())
    text = repr(module_obj)
    assert "Demo" in text and "handlers=0" in text and "idle" in text


def test_errors_carry_the_module_name(layer):
    exists = layer.module.ModuleExists("GenAI")
    missing = layer.module.ModuleMissing("GenAI")
    assert exists.name == missing.name == "GenAI"
    assert "already registered" in str(exists)
    assert "not registered" in str(missing)
    assert isinstance(exists, layer.module.ModuleError)
"""``plugins/genai_module.py``: the Gemini feature declared on the Module
layer, next to the decorator plugin ``plugins/genai.py``.

The Module layer lives in ``pyUltroid/_misc`` and deliberately has no
top-level ``pyUltroid`` import, so the fixture loads it standalone and then
loads the plugin against a stubbed root and a stubbed ``plugins`` package.
"""

from __future__ import annotations

import asyncio
import logging
import sys
import types

import pytest
from conftest import ROOT


class FakeClient:
    """Records Module bindings instead of talking to Telegram."""

    def __init__(self) -> None:
        self.handlers: list[tuple] = []

    def add_event_handler(self, callback, event=None) -> None:
        self.handlers.append((callback, event))

    def remove_event_handler(self, callback) -> None:
        self.handlers = [h for h in self.handlers if h[0] is not callback]


class FakeUdB:
    def get_key(self, key):
        return None


def _builder_pattern(builder):
    """Return the regex text from Telethon's compiled pattern matcher."""
    matcher = getattr(builder, "pattern", None)
    compiled = getattr(matcher, "__self__", matcher)
    return getattr(compiled, "pattern", compiled)


def _fake_telethon():
    """A minimal ``telethon.events`` so the plugin imports without Telethon.

    Only ``NewMessage`` is needed: the Module layer stores the builder and
    hands it to ``add_event_handler`` verbatim, so a stand-in exercises the
    real binding path. Returns the names it had to invent, so the caller can
    remove them again - a stub left in ``sys.modules`` silently changes what
    every later test imports.
    """
    invented = []
    if "telethon" not in sys.modules:
        telethon = types.ModuleType("telethon")
        telethon.__path__ = []
        events = types.ModuleType("telethon.events")

        class NewMessage:
            def __init__(self, **kwargs):
                self.build = dict(kwargs)

            @property
            def pattern(self):
                return self.build.get("pattern")

            def __repr__(self):
                return f"NewMessage({self.build})"

        events.NewMessage = NewMessage
        telethon.events = events
        sys.modules["telethon"] = telethon
        sys.modules["telethon.events"] = events
        invented = ["telethon", "telethon.events"]
    return invented


def _load_layer():
    """Load the Module layer standalone, as ``tests/test_misc_module.py`` does."""
    misc = ROOT / "pyUltroid" / "_misc"
    package = types.ModuleType("ul_misc")
    package.__path__ = [str(misc)]
    package.__package__ = "ul_misc"
    sys.modules["ul_misc"] = package
    import importlib.util

    for name in ("listener", "methods", "module", "registry"):
        spec = importlib.util.spec_from_file_location(
            f"ul_misc.{name}", misc / f"{name}.py"
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules[f"ul_misc.{name}"] = module
        spec.loader.exec_module(module)
    return types.SimpleNamespace(
        listener=sys.modules["ul_misc.listener"],
        methods=sys.modules["ul_misc.methods"],
        module=sys.modules["ul_misc.module"],
        registry=sys.modules["ul_misc.registry"],
    )


@pytest.fixture
def genai_module(module_isolation):
    saved = {
        name: value
        for name, value in sys.modules.items()
        if name == "ul_misc" or name.startswith("ul_misc.")
    }
    for name in saved:
        sys.modules.pop(name, None)
    layer = _load_layer()
    invented = _fake_telethon()
    try:
        with module_isolation("plugins", "pyUltroid"):
            root = types.ModuleType("pyUltroid")
            root.__path__ = [str(ROOT / "pyUltroid")]
            root.udB = FakeUdB()
            root.LOGS = logging.getLogger("genai-module-tests")
            sys.modules["pyUltroid"] = root
            configs = types.ModuleType("pyUltroid.configs")
            configs.Var = types.SimpleNamespace(DEVLIST_IDS=[])
            sys.modules["pyUltroid.configs"] = configs

            # make the layer importable under its real dotted path too
            misc = ROOT / "pyUltroid" / "_misc"
            sub = types.ModuleType("pyUltroid._misc")
            sub.__path__ = [str(misc)]
            sub.__package__ = "pyUltroid._misc"
            sys.modules["pyUltroid._misc"] = sub
            for name, module in (
                ("listener", layer.listener),
                ("methods", layer.methods),
                ("module", layer.module),
                ("registry", layer.registry),
            ):
                sys.modules[f"pyUltroid._misc.{name}"] = module
                setattr(sub, name, module)

            plugins = types.ModuleType("plugins")
            plugins.__path__ = [str(ROOT / "plugins")]
            plugins.LOGS = logging.getLogger("genai-module-tests")
            plugins.ultroid_bot = FakeClient()
            plugins.get_help = lambda key: None
            plugins.get_string = lambda key: key
            sys.modules["plugins"] = plugins

            mod = __import__("plugins.genai_module", fromlist=["genai_module"])
            # The W3-B driver pattern: the plugin file wires nothing at
            # import time; the registry adopts it, exactly as the Ultroid
            # loader does right after a successful plugin import.
            registry = layer.registry.ModuleRegistry()
            report = registry.adopt(mod, clients=[plugins.ultroid_bot])
            assert report == {"GenAIModule": {"handlers": 2}}, (
                f"driver report mismatch: {report}"
            )
            plugin = registry.get("GenAIModule")
            assert plugin is not None, "adopt must register the module"
            yield types.SimpleNamespace(
                mod=mod, layer=layer, registry=registry, plugin=plugin
            )
    finally:
        for name in [
            n for n in sys.modules if n == "ul_misc" or n.startswith("ul_misc.")
        ]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)
        for name in invented:
            sys.modules.pop(name, None)
        for name in ("plugins.genai_module", "pyUltroid._misc"):
            sys.modules.pop(name, None)


# ── declaration ───────────────────────────────────────────────────────


def test_plugin_is_a_module_subclass(genai_module):
    assert issubclass(
        genai_module.mod.GeminiModule, genai_module.layer.module.BoundModule
    )
    assert issubclass(
        genai_module.mod.GeminiModule, genai_module.layer.module.ModuleMixin
    )


def test_handlers_are_discovered_not_declared_by_hand(genai_module):
    """The whole point: no client is touched at definition time."""
    cls = genai_module.mod.GeminiModule
    assert cls.handler_names() == ("on_message_out", "on_cleared")
    priorities = [tag.priority for _name, tag in cls.handlers]
    assert priorities == [1, 2]
    assert cls.handler_events() == ("NewMessage",)
    assert [_builder_pattern(tag.builder) for _name, tag in cls.handlers] == [
        genai_module.mod.TRIGGER.pattern,
        genai_module.mod.CLEAR.pattern,
    ]


def test_module_declares_its_metadata(genai_module):
    described = genai_module.mod.GeminiModule.describe()
    assert described["name"] == "GenAI (Module)"
    assert "!?" in described["cmds"]
    assert described["desc"]["infix"] == "!?"


# ── binding ───────────────────────────────────────────────────────────


def _client(mod):
    """The fake client the stubbed ``plugins`` package exposes."""
    import plugins

    return plugins.ultroid_bot


def test_bind_attaches_both_handlers_to_the_client(genai_module):
    mod, registry, plugin = genai_module.mod, genai_module.registry, genai_module.plugin
    registry.unbind(plugin)

    client = _client(mod)
    assert registry.bind(plugin, [client]) == 2, "bind returns the handler count"
    assert len(registry.listeners) == 2
    assert len(client.handlers) == 2
    assert [li.event for li in registry.listeners] == ["NewMessage", "NewMessage"]
    assert [_builder_pattern(li.builder) for li in registry.listeners] == [
        genai_module.mod.TRIGGER.pattern,
        genai_module.mod.CLEAR.pattern,
    ]
    assert [_builder_pattern(builder) for _callback, builder in client.handlers] == [
        genai_module.mod.TRIGGER.pattern,
        genai_module.mod.CLEAR.pattern,
    ]


def test_unbind_removes_exactly_what_was_added(genai_module):
    """Identity-stable bindings: removal must actually match."""
    mod, registry, plugin = genai_module.mod, genai_module.registry, genai_module.plugin
    client = _client(mod)
    registry.bind(plugin, [client])
    registry.unbind(plugin)
    assert client.handlers == []
    assert registry.listeners == []


def test_binding_fans_out_across_clients(genai_module):
    mod, registry, plugin = genai_module.mod, genai_module.registry, genai_module.plugin
    first, second = _client(mod), FakeClient()
    registry.unbind(plugin)
    assert registry.bind(plugin, [first, second]) == 4, "2 handlers x 2 clients"
    assert len(first.handlers) == 2 and len(second.handlers) == 2
    registry.unbind(plugin)
    registry.bind(plugin, [first])


# Runtime-specific checks use mocks only; no provider or Telegram connection.

def test_module_runtime_request_and_shared_history_clear(genai_module, monkeypatch):
    plugin = genai_module.plugin
    import plugins.genai_module as module
    seen = []
    class Response:
        content = "ok"
        def __str__(self): return self.content
    class Runtime:
        async def runtime_run(self, request): seen.append(request); return Response()
        def clear_history(self, key): seen.append(key)
    monkeypatch.setattr(module, "agent", lambda: Runtime())
    class Message:
        async def edit(self, *args, **kwargs): pass
    class Event:
        raw_text = "hello !?"
        chat_id = 5; sender_id = 9; is_private = True; client = None
        async def reply(self, *args): return Message()
        async def get_reply_message(self): return None
    asyncio.run(plugin.on_message_out(Event()))
    assert seen[0].prompt == "hello"
    assert seen[0].chat_id == "genai:5:9"
    async def fake_respond(event, text): seen.append("genai:5:9")
    monkeypatch.setattr(plugin, "respond", fake_respond)
    asyncio.run(plugin.on_cleared(Event()))
    assert seen[1] == "genai:5:9"

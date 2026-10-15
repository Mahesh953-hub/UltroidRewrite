"""``plugins/genai_module.py``: the Gemini feature declared on the Module
layer, next to the decorator plugin ``plugins/genai.py``.

The Module layer lives in ``pyUltroid/_misc`` and deliberately has no
top-level ``pyUltroid`` import, so the fixture loads it standalone and then
loads the plugin against a stubbed root and a stubbed ``plugins`` package.
"""

from __future__ import annotations

import asyncio
import collections
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
            assert report == {
                "GeminiModule": {"handlers": 2}
            }, f"driver report mismatch: {report}"
            plugin = registry.get("GeminiModule")
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
    assert issubclass(genai_module.mod.GeminiModule, genai_module.layer.module.BoundModule)
    assert issubclass(
        genai_module.mod.GeminiModule, genai_module.layer.module.ModuleMixin
    )


def test_handlers_are_discovered_not_declared_by_hand(genai_module):
    """The whole point: no client is touched at definition time."""
    cls = genai_module.mod.GeminiModule
    assert cls.handler_names() == ("on_message_out", "on_cleared")
    priorities = [tag.priority for _name, tag in cls.handlers]
    assert priorities == [1, 2]
    assert cls.handler_events() == (genai_module.mod.TRIGGER.pattern, genai_module.mod.CLEAR.pattern)


def test_module_declares_its_metadata(genai_module):
    described = genai_module.mod.GeminiModule.describe()
    assert described["name"] == "Google Gemini (Module)"
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
    assert [li.event for li in registry.listeners] == [
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


# ── lifecycle ─────────────────────────────────────────────────────────


def test_ready_runs_the_lifecycle_once(genai_module):
    plugin = genai_module.plugin
    calls = []

    async def starting():
        calls.append("starting")

    plugin.on_starting = starting
    plugin._started = False
    plugin.models_loaded = False
    plugin.api_key = None
    asyncio.run(plugin.ready())
    assert plugin.started is True
    asyncio.run(plugin.ready())
    assert calls == ["starting"], "lifecycle must be latched"


def test_on_stopping_drops_the_history(genai_module):
    plugin = genai_module.plugin
    plugin.history.append({"role": "user", "parts": []})
    asyncio.run(plugin.on_stopping())
    assert len(plugin.history) == 0
    assert plugin.http is None


# ── behaviour ─────────────────────────────────────────────────────────


def test_history_is_bounded(genai_module):
    plugin = genai_module.plugin
    assert isinstance(plugin.history, collections.deque)
    assert plugin.history.maxlen == 64


def test_model_rotation_on_transport_failure(genai_module):
    plugin = genai_module.plugin
    plugin.history.append({"role": "user", "parts": [{"text": "q"}]})
    plugin.models = collections.deque(["models/a", "models/b", "models/c"])

    class Boom:
        def __aenter__(self):
            raise RuntimeError("network down")

        async def __aexit__(self, *a):
            return False

    tried = []

    class Recording:
        def __init__(self, model):
            self.model = model

        async def __aenter__(self):
            tried.append(self.model)
            raise RuntimeError("network down")

        async def __aexit__(self, *a):
            return False

    plugin._session = lambda: Recording(plugin.models[0])
    result = asyncio.run(plugin.ask())

    # every model is attempted exactly once before giving up
    assert tried == ["models/a", "models/b", "models/c"], tried
    assert result == "**Error**:\n  `All models failed`", (
        "exhausted rotation reports honestly, not the old 'Rate Limited' lie"
    )


def test_empty_response_pops_the_history(genai_module):
    plugin = genai_module.plugin
    turn = {"role": "user", "parts": [{"text": "q"}]}
    plugin.history.append(turn)

    class Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        async def json(self):
            return {"candidates": []}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **k):
            return Resp()

    plugin._session = lambda: Session()
    result = asyncio.run(plugin.ask())
    assert result == "**Error**:\n  `Empty Response`"
    assert turn not in plugin.history, "the empty exchange must not be kept"
    assert result == "**Error**:\n  `Empty Response`"


def test_successful_generation_appends_the_model_turn(genai_module):
    plugin = genai_module.plugin
    plugin.history.append({"role": "user", "parts": [{"text": "q"}]})

    class Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        async def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "hello"}]}}]}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def post(self, *a, **k):
            return Resp()

    plugin._session = lambda: Session()
    assert asyncio.run(plugin.ask()) == "hello"
    assert plugin.history[-1] == {"role": "model", "parts": [{"text": "hello"}]}


def test_first_text_is_defensive(genai_module):
    first_text = genai_module.mod.first_text
    assert first_text({}) is None
    assert first_text({"candidates": []}) is None
    assert first_text({"candidates": [{"content": {}}]}) is None
    assert first_text({"candidates": [{"content": {"parts": []}}]}) is None
    assert first_text({"candidates": [{"content": {"parts": [{}]}}]}) is None
    assert first_text({"candidates": [{"content": {"parts": [{"text": "x"}]}}]}) == "x"
    assert first_text(None) is None


def test_trigger_pattern(genai_module):
    trigger = genai_module.mod.TRIGGER
    assert trigger.match("hello !?").group(1) == "hello"
    assert trigger.match("!?").group(1) is None
    assert trigger.match("hello !? -i").group(1) == "hello"
    assert trigger.match("hello") is None


def test_media_size_and_mime_guards(genai_module):
    plugin = genai_module.plugin

    class Reply:
        sticker = None
        client = None

        def __init__(self, size, mime):
            self.file = types.SimpleNamespace(size=size, mime_type=mime)

    too_big = Reply(genai_module.mod.MAX_MEDIA_SIZE + 1, "image/png")
    assert asyncio.run(plugin.media_part(too_big)) == "genai_media_too_big"

    bad_mime = Reply(10, "application/zip")
    assert asyncio.run(plugin.media_part(bad_mime)) == "genai_media_bad_mime"

    class NoMedia:
        sticker = None
        client = None
        file = None

    assert asyncio.run(plugin.media_part(NoMedia())) is None


def test_run_without_a_key_answers_immediately(genai_module):
    plugin = genai_module.plugin
    plugin.api_key = None
    seen = []

    async def eod(event, text=None, **kwargs):
        seen.append(text)
        return None

    plugin.eod = eod
    asyncio.run(plugin.run(types.SimpleNamespace(raw_text="hi !?")))
    assert seen and "Gemini key" in seen[0]


def test_registry_status_reports_the_module(genai_module):
    status = genai_module.registry.status()
    assert "GeminiModule" in status["modules"]
    assert status["modules"]["GeminiModule"]["name"] == "Google Gemini (Module)"
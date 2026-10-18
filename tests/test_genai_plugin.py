"""The unified GenAI plugin: trigger pattern, generation flow, and history.

The plugin module imports the ``plugins`` package (bot globals), so the
tests stub it the way the dB tests stub ``pyUltroid``: a fake package with
the attributes the plugin consumes, plus a fake client collecting handlers.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import types

import pytest
from conftest import ROOT


class FakeClient:
    """Records handler registrations instead of talking to Telegram."""

    def __init__(self):
        self.handlers = []

    def on(self, *args, **kwargs):
        def _wrap(func):
            self.handlers.append((func.__name__, args, kwargs))
            return func

        return _wrap


class FakeUdB:
    def get_key(self, key):
        return None


@pytest.fixture
def genai_module(module_isolation):
    # the stubbed root runs with a bound udB, so anything importing
    # pyUltroid.startup configures a FileHandler named after sys.argv[6]
    # (a pytest CLI can name a tests/ path): give it a directory, clean later
    shutil.rmtree(ROOT / "ultroidtests", ignore_errors=True)
    (ROOT / "ultroidtests").mkdir(exist_ok=True)
    try:
        yield _load_genai(module_isolation)
    finally:
        shutil.rmtree(ROOT / "ultroidtests", ignore_errors=True)


def _load_genai(module_isolation):
    with module_isolation("plugins", "pyUltroid"):
        # the plugin imports pyUltroid.agent._config + fns, which import the
        # pyUltroid root: stub it library-mode (no bootstrap).
        root = types.ModuleType("pyUltroid")
        root.__path__ = [str(ROOT / "pyUltroid")]
        root.udB = FakeUdB()
        root.LOGS = logging.getLogger("pyUltroid-stub")
        sys.modules["pyUltroid"] = root
        # helper.py reads the dB layer (udB bound) and the configs
        # Var: stub both so no bootstrap and no env is needed.
        configs = types.ModuleType("pyUltroid.configs")
        configs.Var = types.SimpleNamespace(DEVLIST_IDS=[])
        sys.modules["pyUltroid.configs"] = configs

        # the plugin binds ``fns`` (the union) at import time; a plain
        # namespace keeps the stub deterministic and monkeypatch-able
        fns_mod = types.ModuleType("pyUltroid.fns")
        fns_mod.fns = types.SimpleNamespace(
            async_searcher=None,
            time_formatter=lambda ms: "0s",
            get_paste=None,
        )
        sys.modules["pyUltroid.fns"] = fns_mod

        plugins = types.ModuleType("plugins")
        plugins.__path__ = [str(ROOT / "plugins")]
        plugins.LOGS = logging.getLogger("genai-tests")
        client = FakeClient()
        plugins.ultroid_bot = client
        plugins.get_help = lambda key: None
        plugins.get_string = lambda key: key

        async def _eod(event, text=None, time=None, **kwargs):
            return ("eod", text)

        plugins.eod = _eod
        for name in ("callback", "asst_cmd", "ultroid_cmd"):

            def _decorator(*args, __name=name, **kwargs):
                def _wrap(func):
                    client.handlers.append((__name, args, kwargs))
                    return func

                return _wrap

            setattr(plugins, name, _decorator)

        sys.modules["plugins"] = plugins
        mod = __import__("plugins.genai", fromlist=["genai"])
        return types.SimpleNamespace(
            mod=mod,
            genai=mod.genai,
            client=client,
        )


def test_trigger_pattern(genai_module):
    p = genai_module.mod.pattern
    assert p.match("Hello, World! !?").group(1) == "Hello, World!"
    # "-i" only means "ignore the reply": a query before it is still a query
    assert p.match("Analyze !? -i").group(1) == "Analyze"
    assert p.match("!? -i").group(1) is None
    assert p.match("Analyze") is None


def test_handlers_registered(genai_module):
    # the fake client records (function name) for raw .on() handlers and
    # (decorator name) for the decorated ones. The outgoing ``!?`` trigger
    # moved to plugins/genai_module.py (Module layer, driver-bound), so this
    # plugin registers exactly the assistant command, cleargenai and the
    # close button - and no raw .on() handler.
    names = [h[0] for h in genai_module.client.handlers]
    assert "on_message_out" not in names
    assert names.count("ultroid_cmd") == 1
    assert names.count("asst_cmd") == 1
    assert names.count("callback") == 1


def test_genai_request_uses_shared_runtime_and_edit_delivery(genai_module, monkeypatch):
    mod = genai_module.mod
    seen = {}
    class Response:
        content = "runtime answer"
        def __str__(self): return self.content
    class Runtime:
        async def runtime_run(self, request):
            seen.update(prompt=request.prompt, chat_id=request.chat_id, origin=request.origin)
            return Response()
    monkeypatch.setattr(mod, "agent", lambda: Runtime())
    class Message:
        async def edit(self, text, **kw): self.text = text
    class Event:
        raw_text = "hello !?"
        chat_id = 42
        sender_id = 7
        is_private = True
        client = None
        async def reply(self, text): return Message()
        async def get_reply_message(self): return None
    asyncio.run(mod.genai.execute(Event()))
    assert seen == {"prompt": "hello", "chat_id": "genai:42:7", "origin": "genai"}


def test_reply_context_and_ignore_suffix(genai_module, monkeypatch):
    mod = genai_module.mod
    seen = []
    class Response:
        content = "ok"
        def __str__(self): return self.content
    class Runtime:
        async def runtime_run(self, req): seen.append(req.prompt); return Response()
    monkeypatch.setattr(mod, "agent", lambda: Runtime())
    class Msg:
        async def edit(self, *a, **kw): pass
    class Reply:
        media = None
        text = "quoted words"
    class Event:
        raw_text = "ask !?"
        chat_id = 1; sender_id = 2; is_private = True; client = None
        async def reply(self, text): return Msg()
        async def get_reply_message(self): return Reply()
    asyncio.run(mod.genai.execute(Event()))
    assert "quoted words" in seen[-1]
    Event.raw_text = "ask !? -i"
    asyncio.run(mod.genai.execute(Event()))
    assert seen[-1] == "ask"


def test_clear_targets_shared_runtime_history(genai_module, monkeypatch):
    mod = genai_module.mod
    seen = []
    class Runtime:
        def clear_history(self, key): seen.append(key)
    monkeypatch.setattr(mod, "agent", lambda: Runtime())
    asyncio.run(mod.genai.clear(12, 3))
    assert seen == ["genai:12:3"]

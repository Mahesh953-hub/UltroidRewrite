"""The unified GenAI plugin: trigger pattern, generation flow, and history.

The plugin module imports the ``plugins`` package (bot globals), so the
tests stub it the way the dB tests stub ``pyUltroid``: a fake package with
the attributes the plugin consumes, plus a fake client collecting handlers.
"""

from __future__ import annotations

import asyncio
import collections
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


def test_gemini_rotates_models_on_failure(genai_module, monkeypatch):
    genai = genai_module.genai
    fns = genai_module.mod.fns
    calls = []

    async def flaky(*args, **kwargs):
        calls.append(kwargs.get("url", args[0] if args else ""))
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {"candidates": [{"content": {"parts": [{"text": "hi there"}]}}]}

    monkeypatch.setattr(fns, "async_searcher", flaky)
    genai.data.append({"role": "user", "parts": [{"text": "q"}]})
    first = genai.models[0]
    result = asyncio.run(genai.gemini())
    assert result == "hi there"
    assert genai.models[0] != first  # rotated past the failing model
    assert genai.data[-1]["role"] == "model"


def test_gemini_pops_history_on_empty_response(genai_module, monkeypatch):
    genai = genai_module.genai
    fns = genai_module.mod.fns

    async def empty(*args, **kwargs):
        return {"candidates": []}

    monkeypatch.setattr(fns, "async_searcher", empty)
    user_msg = {"role": "user", "parts": [{"text": "q"}]}
    genai.data.append(user_msg)
    result = asyncio.run(genai.gemini())
    assert result == "**Error**:\n  `Empty Response`"
    assert user_msg not in genai.data  # the empty exchange is not kept


def test_history_is_bounded(genai_module):
    genai = genai_module.genai
    assert isinstance(genai.data, collections.deque)
    assert genai.data.maxlen == 64


def test_execute_without_key_answers_eod(genai_module, monkeypatch):
    mod = genai_module.mod
    # ``GenAI.api_key`` reads the agent config store; the no-key path is
    # exercised by pinning the property to None for the duration.
    monkeypatch.setattr(
        type(mod.genai), "api_key", property(lambda self: None)
    )
    called = {}

    async def _eod(event, text=None, time=None, **kwargs):
        called["text"] = text

    monkeypatch.setattr(mod, "eod", _eod)

    class _Event:
        raw_text = "hi !?"

    asyncio.run(mod.genai.execute(_Event()))
    assert "GEMINI_API_KEY" in called["text"]

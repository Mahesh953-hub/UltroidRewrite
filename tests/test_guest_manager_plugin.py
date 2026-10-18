from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path


def test_guest_manager_registers_and_lists_with_current_guest_api(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    registrations = []
    plugins = types.ModuleType("plugins")
    plugins.__path__ = [str(root / "plugins")]

    def ultroid_cmd(*args, **kwargs):
        def register(func):
            registrations.append((args, kwargs, func))
            return func

        return register

    plugins.ultroid_cmd = ultroid_cmd
    monkeypatch.setitem(sys.modules, "plugins", plugins)

    pyultroid = types.ModuleType("pyUltroid")
    pyultroid.__path__ = []
    startup = types.ModuleType("pyUltroid.startup")
    startup.__path__ = []
    guestmode = types.ModuleType("pyUltroid.startup.guestmode")
    guestmode.add_guest = lambda *args, **kwargs: None
    guestmode.remove_guest = lambda *args, **kwargs: False
    guestmode.get_guests = dict
    monkeypatch.setitem(sys.modules, "pyUltroid", pyultroid)
    monkeypatch.setitem(sys.modules, "pyUltroid.startup", startup)
    monkeypatch.setitem(sys.modules, "pyUltroid.startup.guestmode", guestmode)

    custom = types.ModuleType("custom")
    custom.__path__ = []
    fmt = types.ModuleType("custom.fmt")
    fmt.fmtmsg = lambda **kwargs: "formatted"
    fmt.fmtsec = lambda *args, **kwargs: "0s"
    monkeypatch.setitem(sys.modules, "custom", custom)
    monkeypatch.setitem(sys.modules, "custom.fmt", fmt)

    name = "plugins.guest_manager"
    spec = importlib.util.spec_from_file_location(
        name, root / "plugins/guest_manager.py"
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)

    assert len(registrations) == 1
    assert "guest_manager_cmd" in registrations[0][2].__name__

    class PatternMatch:
        def group(self, index):
            return "list" if index == 1 else None

    class Event:
        pattern_match = PatternMatch()
        response = None

        async def eor(self, text, **kwargs):
            self.response = text
            return text

    event = Event()
    asyncio.run(module.guest_manager_cmd(event))
    assert event.response == "📝 **Active Guest Sessions**: None"

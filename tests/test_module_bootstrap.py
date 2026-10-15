"""Smoke tests for the built-in Telethon module patch bootstrap."""

from __future__ import annotations

from types import ModuleType

from telethon import events

from pyUltroid.modules import ModuleLoader, Modules


class FirstTarget:
    pass


class SecondTarget:
    pass


def test_apply_patches_applies_all_decorated_targets():
    loader = ModuleLoader()
    module = ModuleType("test_addons")

    async def _addon(self):
        return self

    _addon.__module__ = module.__name__
    _addon._patch = [FirstTarget, SecondTarget]
    module._addon = _addon

    assert loader.apply_patches([module]) == 2
    assert FirstTarget.addon is _addon
    assert SecondTarget.addon is _addon

    del FirstTarget.addon
    del SecondTarget.addon


def test_apply_patches_deduplicates_reexported_functions():
    loader = ModuleLoader()
    module = ModuleType("test_addons")

    def _addon(self):
        return self

    _addon.__module__ = module.__name__
    _addon._patch = [FirstTarget]
    module._addon = _addon
    module.alias = _addon

    assert loader.apply_patches([module]) == 1
    assert FirstTarget.addon is _addon

    del FirstTarget.addon


class FakeClient:
    def __init__(self):
        self.handlers = []

    def list_event_handlers(self):
        return list(self.handlers)

    def add_event_handler(self, callback, event=None):
        builders = events._get_handlers(callback)
        if builders:
            self.handlers.extend((callback, builder) for builder in builders)
        else:
            self.handlers.append((callback, event))


def test_bootstrap_discovers_patches_once_and_retries_after_discovery_failure(
    monkeypatch,
):
    """A failed discovery must remain retryable; success applies patches once."""
    modules = Modules()
    module = ModuleType("test_discovered_addons")

    def _addon(self):
        return self

    _addon.__module__ = module.__name__
    _addon._patch = [FirstTarget]
    module._addon = _addon

    attempts = 0

    def discover():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ImportError("broken built-in module")
        return [module]

    monkeypatch.setattr(modules, "discover", discover)
    with __import__("pytest").raises(ImportError, match="broken built-in module"):
        modules.bootstrap()

    assert modules.bootstrapped is False
    # 1 cooperative patch from the stub module + 6 forced installs.
    assert modules.bootstrap() == 7
    assert FirstTarget.addon is _addon
    assert modules.bootstrap() == 7
    assert attempts == 2

    del FirstTarget.addon


def test_attach_adds_handlers_to_every_client_exactly_once():
    modules = Modules()
    callback = modules.on_update
    clients = [FakeClient(), FakeClient()]

    assert modules.attach(clients) == 2
    assert all(client.handlers[0][0] == callback for client in clients)
    assert modules.attach(clients) == 0


def test_handlers_finds_the_raw_router():
    modules = Modules()
    assert modules.handlers() == [modules.on_update]
    assert events.is_handler(modules.on_update)


def test_reset_makes_bootstrap_rerun():
    modules = Modules()
    modules.bootstrap()
    assert modules.bootstrapped is True

    modules.reset()
    assert modules.bootstrapped is False
    assert modules.patch_count == 0
    assert modules.discovered == []


def test_real_modules_import_and_apply_expected_telethon_addons(monkeypatch):
    """Exercise the production discovery path against the pinned fork."""
    import pyUltroid
    from telethon import TelegramClient
    from telethon.types import (
        UpdateBotGuestChatQuery,
        UpdateBotNewBusinessMessage,
    )

    class FakeDB:
        def get_key(self, key):
            return None

    monkeypatch.setattr(pyUltroid, "udB", FakeDB())
    modules = Modules()

    # _addons.py is gone; the 6 forced installs are the whole patch set.
    assert modules.bootstrap() == 6
    # The forced installs run last, so _gorilla owns these methods.
    assert TelegramClient.invoke.__module__ == "pyUltroid.modules._gorilla"
    assert UpdateBotGuestChatQuery.answer.__module__ == "pyUltroid.modules._gorilla"
    assert UpdateBotNewBusinessMessage.respond.__module__ == "pyUltroid.modules._gorilla"
    assert modules.summary()["installed"] is True


def test_startup_bootstraps_modules_before_clients_and_attaches_afterward():
    """Keep the production startup ordering around the two bootstrap hooks.

    The startup lives on the core union's runtime since W9: ``run()`` calls
    ``apply_dynamic_patches`` (module bootstrap) before ``start_bot_clients``
    (first client), and ``compose_master`` wires every client
    (``wire_clients``) before attaching the module handlers.
    """
    import pyUltroid.modules as module_bootstrap

    source = module_bootstrap.__file__.replace(
        "modules/__init__.py", "_core/runtime.py"
    )
    with open(source, encoding="utf-8") as file:
        runtime = file.read()

    patches_at = runtime.index("modules_runtime = self.apply_dynamic_patches()")
    first_client_at = runtime.index("asst, extra_bots = self.start_bot_clients(")
    master_at = runtime.index("self.compose_master(")

    assert patches_at < first_client_at < master_at

    wiring_source = source.replace("_core/runtime.py", "_core/_client_wiring.py")
    with open(wiring_source, encoding="utf-8") as file:
        wiring = file.read()

    wire_at = wiring.index("def wire_clients(")
    runtime_at = runtime.index("modules_runtime.attach(")
    master_call_at = runtime.index("self.compose_master(")
    # attach happens inside compose_master, after wire_clients ran
    assert wire_at < runtime_at and master_call_at < runtime_at
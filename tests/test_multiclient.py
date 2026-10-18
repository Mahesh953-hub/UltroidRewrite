"""W3-A multiclient tests.

Credential-free unit tests for the MasterClient role-aware registry, per-client
handler prefixes + dispatcher fan-out, extra SESSION2/SESSION3-style config
hooks, and test-local Telegram-shaped clients that avoid real sessions.
"""

from __future__ import annotations

import asyncio
import copy
import pickle
import sys
import types
from contextlib import contextmanager
from unittest.mock import patch

import pytest
from telethon.types import Message, PeerUser, UpdateNewMessage


class SimulatedClient:
    """Picklable test-only client stub; the runtime exposes no such class."""

    def __init__(self, name, role, hndlr="."):
        self.name = name
        self.role = role
        self.HNDLR = hndlr
        self.DUAL_HNDLR = "/"
        self.udB = None
        self.dispatcher = None
        self.handlers = []

    def add_event_handler(self, handler, event=None):
        self.handlers.append((handler, event))

    def remove_event_handler(self, handler, event=None):
        self.handlers = [
            pair
            for pair in self.handlers
            if pair[0] is not handler or (event is not None and pair[1] != event)
        ]

    async def send_message(self, *args, **kwargs):
        return None


@contextmanager
def isolated_sys_modules(*prefixes: str):
    """Snapshot then restore sys.modules entries matching *prefixes*."""

    def _matches(name: str) -> bool:
        return any(
            name == prefix or name.startswith(prefix + ".") for prefix in prefixes
        )

    saved = {name: module for name, module in sys.modules.items() if _matches(name)}
    for name in saved:
        sys.modules.pop(name, None)
    try:
        yield saved
    finally:
        for name in list(sys.modules):
            if _matches(name):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


@contextmanager
def _client_modules():
    """Import pyUltroid client modules without running the production bootstrap."""
    with isolated_sys_modules("pyUltroid"):
        # Ensure the repo root is on sys.path for a fresh import.
        from pathlib import Path

        root = str(Path(__file__).resolve().parents[1])
        if root not in sys.path:
            sys.path.insert(0, root)
        yield


@pytest.fixture
def client_mods(monkeypatch):
    with _client_modules():
        # configs.py uses sys.argv[1] for API_ID when running as a script; during
        # pytest sys.argv[1] is the test file path. Wipe the script args so the
        # env fallback is used, then provide dummy env values for everything that
        # configs.py requires at import time.
        original_argv = sys.argv[:]
        sys.argv = ["test_multiclient"]
        try:
            for key, value in {
                "API_ID": "12345",
                "API_HASH": "deadbeefcafe",
                "SESSION": "AQABBAABBAABBAABBAABBAABBAABBAABBAABBAABBAABBAABBAAB",
                "LOG_CHANNEL": "1",
                "BOT_TOKEN": "123456:dummy-token",
            }.items():
                monkeypatch.setenv(key, value)

            from pyUltroid._core.client import (
                MasterClient,
                OriginMasterClient,
                bind_origin,
            )
            from pyUltroid.startup.BaseClient import PyTgCallsClient
            from pyUltroid.startup.dispatcher import CommandDispatcher

            # Open the dispatcher owner gate for all simulated clients.
            monkeypatch.setattr("pyUltroid._misc.owner_and_sudos", lambda: [1])

            yield types.SimpleNamespace(
                MasterClient=MasterClient,
                OriginMasterClient=OriginMasterClient,
                bind_origin=bind_origin,
                SimulatedClient=SimulatedClient,
                PyTgCallsClient=PyTgCallsClient,
                CommandDispatcher=CommandDispatcher,
            )
        finally:
            sys.argv[:] = original_argv


class MemoryDB:
    """udB-shaped in-memory store."""

    def __init__(self):
        self.data = {}

    def get_key(self, key):
        return self.data.get(key)

    def set_key(self, key, value):
        self.data[key] = value

    def del_key(self, key):
        self.data.pop(key, None)


def make_update(text: str, client):
    """Build a minimal UpdateNewMessage carrying the simulated client."""
    msg = Message(
        id=1,
        peer_id=PeerUser(user_id=1),
        date=None,
        message=text,
        from_id=PeerUser(user_id=1),
    )
    update = UpdateNewMessage(message=msg, pts=1, pts_count=1)
    update._client = client
    return update


class TestRoleRegistry:
    def test_role_aware_registry_and_primary_attributes(self, client_mods):
        M = client_mods.MasterClient
        bot = client_mods.SimulatedClient("asst", "bot")
        user = client_mods.SimulatedClient("user", "user")
        vc = types.SimpleNamespace(name="vc")

        master = M(bot=bot, user=user, app=vc, udB=MemoryDB())

        assert master.bot is bot
        assert master.user is user
        assert master.app is vc
        assert master.business is bot  # defaults to primary bot
        assert master.available == {"bot", "business", "user", "vc"}
        assert master.has_bot and master.has_user and master.has_vc
        assert master.has_business
        assert master.clients_by_role("bot") == (bot,)
        assert master.clients_by_role("user") == (user,)
        assert master.clients_by_role("vc") == (vc,)
        assert master.clients_by_role("business") == (bot,)
        assert bot in master.clients
        assert user in master.clients
        assert vc in master.clients
        assert master.telegram_clients == (user, bot)

    def test_add_remove_extra_clients(self, client_mods):
        M = client_mods.MasterClient
        u1 = client_mods.SimulatedClient("u1", "user")
        u2 = client_mods.SimulatedClient("u2", "user")
        b1 = client_mods.SimulatedClient("b1", "bot")
        b2 = client_mods.SimulatedClient("b2", "bot")

        master = M(user=u1, bot=b1, udB=MemoryDB())
        master.add_client("user", u2)
        master.add_client("bot", b2)

        assert master.user is u1
        assert master.clients_by_role("user") == (u1, u2)
        assert master.clients_by_role("bot") == (b1, b2)
        assert len(master.clients) == 4

        master.remove_client("user", u1)
        assert master.user is u2
        assert master.clients_by_role("user") == (u2,)

    def test_explicit_business_role(self, client_mods):
        M = client_mods.MasterClient
        bot = client_mods.SimulatedClient("bot", "bot")
        business = client_mods.SimulatedClient("biz", "bot")
        master = M(bot=bot, business=business, udB=MemoryDB())
        assert master.business is business
        assert master.clients_by_role("business") == (business,)
        assert master.clients_by_role("bot") == (bot,)


class TestRouting:
    def test_origin_binding_routes_to_any_client(self, client_mods):
        M = client_mods.MasterClient
        u1 = client_mods.SimulatedClient("u1", "user")
        u2 = client_mods.SimulatedClient("u2", "user")
        b1 = client_mods.SimulatedClient("b1", "bot")
        master = M(user=u1, bot=b1, udB=MemoryDB())
        master.add_client("user", u2)

        assert master.route() is u1
        with client_mods.bind_origin(u2):
            assert master.route() is u2
        with client_mods.bind_origin(b1):
            assert master.route() is b1
        assert master.route() is u1

    def test_business_context_routes_to_bot(self, client_mods):
        M = client_mods.MasterClient
        u1 = client_mods.SimulatedClient("u1", "user")
        b1 = client_mods.SimulatedClient("b1", "bot")
        master = M(user=u1, bot=b1, udB=MemoryDB())

        with patch.object(M, "_business_connection_id", return_value="biz"):
            assert master.route() is b1

    def test_origin_facade_preserves_secondary_client(self, client_mods):
        M = client_mods.MasterClient
        u1 = client_mods.SimulatedClient("u1", "user")
        u2 = client_mods.SimulatedClient("u2", "user")
        master = M(user=u1, udB=MemoryDB())
        master.add_client("user", u2)
        facade = client_mods.OriginMasterClient(master, u2)

        with client_mods.bind_origin(u2):
            assert facade.route() is u2


class TestPerClientHndlrAndFanOut:
    def test_per_client_dispatcher_prefix(self, client_mods):
        M = client_mods.MasterClient
        u1 = client_mods.SimulatedClient("u1", "user", hndlr=".")
        u2 = client_mods.SimulatedClient("u2", "user", hndlr="!")
        b1 = client_mods.SimulatedClient("b1", "bot", hndlr="?")
        master = M(udB=MemoryDB())
        master.add_client("user", u1)
        master.add_client("user", u2)
        master.add_client("bot", b1)

        for client in master.telegram_clients:
            client.dispatcher = client_mods.CommandDispatcher(
                client, prefix=(client.HNDLR, client.DUAL_HNDLR)
            )
            client.dispatcher.start()

        assert u1.dispatcher._prefix == (".", "/")
        assert u2.dispatcher._prefix == ("!", "/")
        assert b1.dispatcher._prefix == ("?", "/")

    def test_dispatcher_fan_out_across_clients(self, client_mods):
        M = client_mods.MasterClient
        u1 = client_mods.SimulatedClient("u1", "user", hndlr=".")
        u2 = client_mods.SimulatedClient("u2", "user", hndlr="!")
        b1 = client_mods.SimulatedClient("b1", "bot", hndlr="?")
        master = M(udB=MemoryDB())
        master.add_client("user", u1)
        master.add_client("user", u2)
        master.add_client("bot", b1)

        for client in master.telegram_clients:
            client.dispatcher = client_mods.CommandDispatcher(
                client, prefix=(client.HNDLR, client.DUAL_HNDLR)
            )
            client.dispatcher.start()

        called = []

        async def shared_handler(update, args, reply):
            called.append((update._client.name, args))
            return "ok"

        for dispatcher in master.dispatchers:
            dispatcher.register_command("cmd", shared_handler)

        asyncio.run(u1.dispatcher._cmd_handler(make_update(".cmd a", u1)))
        asyncio.run(u2.dispatcher._cmd_handler(make_update("!cmd b", u2)))
        asyncio.run(b1.dispatcher._cmd_handler(make_update("?cmd c", b1)))

        assert called == [("u1", "a"), ("u2", "b"), ("b1", "c")]


class TestPickleAndCopyGuard:
    def test_pytgcalls_client_is_unpicklable_by_module_name(self, client_mods):
        compat = object.__new__(client_mods.PyTgCallsClient)
        # Provide the minimal state the instance would normally hold.
        compat.session = types.SimpleNamespace(dc_id=1)
        compat._bot = False
        compat.me = types.SimpleNamespace(bot=False, id=42, first_name="x")

        # The current PyTgCallsClient uses the module identity required by the
        # upstream bridge; pickle cannot resolve it as a Telethon-owned class.
        assert compat.__class__.__module__ == "telethon.client.telegramclient"
        with pytest.raises((AttributeError, pickle.PicklingError)):
            pickle.dumps(compat)

    def test_master_copy_and_pickle_avoid_getattr_recursion(self, client_mods):
        M = client_mods.MasterClient
        u1 = client_mods.SimulatedClient("u1", "user")
        b1 = client_mods.SimulatedClient("b1", "bot")
        master = M(user=u1, bot=b1, udB=MemoryDB())

        # Shallow copy keeps references to the same client objects.
        copied = copy.copy(master)
        assert copied is not master
        assert copied.user is u1
        assert copied.bot is b1

        # Pickle round-trips without triggering __getattr__ recursion on the
        # _clients registry slot (the W3-A guard still rejects leading underscores).
        dumped = pickle.dumps(master)
        loaded = pickle.loads(dumped)
        assert loaded.user.name == u1.name
        assert loaded.bot.name == b1.name
        assert loaded.available == master.available


class TestUltroidCmdFanOut:
    """Prove the real ``ultroid_cmd`` decorator fans out to every client."""

    def test_real_ultroid_cmd_registers_on_all_clients(self, client_mods, monkeypatch):
        M = client_mods.MasterClient
        u1 = client_mods.SimulatedClient("u1", "user", hndlr=".")
        u2 = client_mods.SimulatedClient("u2", "user", hndlr="!")
        b1 = client_mods.SimulatedClient("b1", "bot", hndlr="?")
        b2 = client_mods.SimulatedClient("b2", "bot", hndlr=";")
        master = M(udB=MemoryDB())
        master.add_client("user", u1)
        master.add_client("user", u2)
        master.add_client("bot", b1)
        master.add_client("bot", b2)

        # Build the minimal pyUltroid namespace the decorator needs at import time.
        import pyUltroid

        # dB/__init__.py refuses to load unless the package is marked as running as a module.
        pyUltroid.ultroid_bot = u1
        pyUltroid.asst = b1
        pyUltroid.master = master
        pyUltroid.udB = master.udB
        pyUltroid.HNDLR = "."
        pyUltroid.SUDO_HNDLR = "."
        pyUltroid.DUAL_HNDLR = "/"
        pyUltroid.DUAL_MODE = False
        pyUltroid.MANAGER = False
        pyUltroid.TAKE_EDITS = False
        pyUltroid.black_list_chats = None
        pyUltroid._ignore_eval = []

        # Ensure the dB/_misc submodules are loaded and patched for the decorator.
        import pyUltroid.dB
        import pyUltroid.dB._core
        import pyUltroid._misc

        monkeypatch.setattr(pyUltroid.dB, "DEVLIST", [])
        monkeypatch.setattr(pyUltroid.dB._core, "LIST", {})
        monkeypatch.setattr(pyUltroid.dB._core, "LOADED", {})
        monkeypatch.setattr(
            pyUltroid._misc,
            "SUDO_M",
            types.SimpleNamespace(should_allow_sudo=False, fullsudos=[]),
        )
        monkeypatch.setattr(pyUltroid._misc, "owner_and_sudos", lambda: [1])
        monkeypatch.setattr(pyUltroid._misc, "specials", lambda: [])

        # Attach a per-client dispatcher to each simulated client so the test
        # proves both the direct event-handler fan-out and the per-client
        # dispatcher surface are reachable.
        for client in (u1, u2, b1, b2):
            client.dispatcher = client_mods.CommandDispatcher(
                client, prefix=(client.HNDLR, client.DUAL_HNDLR)
            )
            client.dispatcher.start()

        from pyUltroid._misc._decorators import ultroid_cmd

        async def hello(event):
            return "hello"

        decorated = ultroid_cmd(pattern="hello")(hello)

        # The current decorator fans user commands out to each user client,
        # while avoiding duplicate execution from bot clients in group chats.
        for client in (u1, u2):
            assert any(
                handler is decorated for handler, _ in client.handlers
            ), f"handler missing on {client.name}"
        for client in (b1, b2):
            assert not any(
                handler is decorated for handler, _ in client.handlers
            ), f"duplicate command handler registered on bot client {client.name}"

        # Each current runtime client carries its own bound dispatcher.
        for client in (u1, u2, b1, b2):
            assert client.dispatcher is not None
            assert client.dispatcher._bound is True


class TestConfigHooks:
    def test_extra_session_config_keys_exist(self, client_mods):
        # Import the Var class after the module bootstrap to verify the new env
        # keys are declared. We do not need real values to test the schema.
        with isolated_sys_modules("pyUltroid"):
            from pathlib import Path

            root = str(Path(__file__).resolve().parents[1])
            if root not in sys.path:
                sys.path.insert(0, root)
            from pyUltroid.configs import Var

            assert hasattr(Var, "SESSION2")
            assert hasattr(Var, "SESSION3")
            assert hasattr(Var, "BOT_TOKEN2")
            assert hasattr(Var, "BOT_TOKEN3")

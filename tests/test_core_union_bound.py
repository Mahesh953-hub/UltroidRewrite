"""The W8 union surface: runtime state bound by the bootstrap.

The core union's bound half - db/var/master/fns/misc - degrades to ``None``
unbound (a library import never raises), warns loudly on a *different-object*
rebind (an import-time capture desyncing from the live object is the bug the
re-point pass exists to prevent), and derives the client/role/dispatcher
surface from the bound master.
"""

from __future__ import annotations

import logging
import types

import pytest


def _load_core_union():
    """Import the composition root (``pyUltroid.ultroid``) against a stubbed root."""
    import importlib
    import sys

    saved = {
        n: m
        for n, m in sys.modules.items()
        if n.split(".")[0] in ("pyUltroid", "telethon")
    }
    for n in saved:
        sys.modules.pop(n, None)
    try:
        root = types.ModuleType("pyUltroid")
        root.__path__ = ["pyUltroid"]
        root.LOGS = logging.getLogger("core-union-tests")
        sys.modules["pyUltroid"] = root
        startup = types.ModuleType("pyUltroid.startup")
        logger = types.ModuleType("pyUltroid.startup.logger")
        logger.LOGS = root.LOGS
        sys.modules["pyUltroid.startup"] = startup
        sys.modules["pyUltroid.startup.logger"] = logger
        return importlib.import_module("pyUltroid.ultroid")
    finally:
        for n in [n for n in sys.modules if n.split(".")[0] in ("pyUltroid", "telethon")]:
            sys.modules.pop(n, None)
        sys.modules.update(saved)


@pytest.fixture
def union():
    module = _load_core_union()
    fresh = module.Ultroid()
    return types.SimpleNamespace(module=module, core=fresh)


class FakeDB:
    def __init__(self) -> None:
        self.data = {}

    def get_key(self, key):
        return self.data.get(key)

    def set_key(self, key, value):
        self.data[key] = value

    def del_key(self, key):
        self.data.pop(key, None)


class FakeClient:
    def __init__(self, name: str) -> None:
        self.name = name
        self.dispatcher = types.SimpleNamespace(name=f"dispatcher-{name}")


class FakeMaster:
    def __init__(self, bot=None, user=None, app=None) -> None:
        self.bot = bot
        self.user = user
        self.app = app
        self._by_role = {"user": [], "bot": [], "business": [], "vc": []}
        for role, client in (("user", user), ("bot", bot), ("vc", app)):
            if client is not None:
                self._by_role[role].append(client)

    def clients_by_role(self, role):
        return tuple(self._by_role.get(role, ()))

    @property
    def telegram_clients(self):
        seen = []
        for role in ("user", "bot", "vc"):
            for client in self._by_role[role]:
                if client not in seen:
                    seen.append(client)
        return tuple(seen)

    def route(self):
        return self.user or self.bot


# ── degradation ───────────────────────────────────────────────────────────


def test_unbound_reads_degrade_to_none(union):
    core = union.core
    assert core.db is None and core.var is None and core.master is None
    assert core.fns is None and core.misc is None
    assert core.bot is None and core.user is None and core.app is None
    assert core.clients == () and core.dispatchers == ()
    assert core.available_roles() == ()
    assert core.has_role("user") is False
    assert core.clients_by_role("bot") == ()
    assert core.route_client() is None
    assert core.modules is None
    # db verbs are no-ops (returns) rather than raises
    assert core.db_get("x", "default") == "default"
    core.db_set("x", 1)
    core.db_del("x")


def test_status_reports_unbound_state(union):
    status = union.core.status()
    assert status["bound"] == {
        "db": False,
        "var": False,
        "master": False,
        "fns": False,
        "misc": False,
    }
    assert status["plugins"] == 0 and status["modules"] == 0
    assert status["roles"] == () and status["clients"] == 0


# ── binding ──────────────────────────────────────────────────────────────


def test_bind_db_and_verbs(union):
    core = union.core
    db = FakeDB()
    core.bind_db(db)
    assert core.db is db
    core.db_set("k", "v")
    assert core.db_get("k") == "v" and db.data["k"] == "v"
    core.db_del("k")
    assert core.db_get("k") is None


def test_rebind_same_object_is_silent(union, caplog):
    core = union.core
    db = FakeDB()
    core.bind_db(db)
    with caplog.at_level(logging.WARNING):
        core.bind_db(db)
    assert not [r for r in caplog.records if "rebound" in r.message]


def test_rebind_different_object_warns_loudly(union, caplog):
    core = union.core
    core.bind_db(FakeDB())
    with caplog.at_level(logging.WARNING):
        core.bind_db(FakeDB())
    assert any(
        "runtime rebind: core.db" in r.message
        for r in caplog.records
    ), "a different-object rebind must warn: captures would desync"


def test_bind_none_is_a_no_op(union):
    core = union.core
    db = FakeDB()
    core.bind_db(db)
    core.bind_db(None)
    assert core.db is db


# ── client surface ────────────────────────────────────────────────────────


def test_client_surface_derives_from_master(union):
    core = union.core
    bot, user = FakeClient("bot"), FakeClient("user")
    master = FakeMaster(bot=bot, user=user)
    core.bind_master(master)

    assert core.bot is bot and core.user is user and core.app is None
    assert set(core.clients) == {bot, user}
    assert {d.name for d in core.dispatchers} == {"dispatcher-bot", "dispatcher-user"}
    assert core.available_roles() == ("user", "bot")
    assert core.has_role("user") is True and core.has_role("vc") is False
    assert core.clients_by_role("bot") == (bot,)
    assert core.route_client() is user

    master._by_role["vc"].append(FakeClient("vc"))
    assert core.available_roles() == ("user", "bot", "vc")


def test_modules_registry_via_misc(union):
    core = union.core
    sentinel = types.SimpleNamespace(registry="the-module-registry")
    core.bind_misc(sentinel)
    assert core.modules == "the-module-registry"
    assert core.misc is sentinel


def test_status_reports_bound_state(union):
    core = union.core
    core.bind_db(FakeDB())
    core.bind_master(FakeMaster(bot=FakeClient("bot")))
    core.bind_misc(types.SimpleNamespace(registry=[object()]))
    status = core.status()
    assert status["bound"] == {
        "db": True,
        "var": False,
        "master": True,
        "fns": False,
        "misc": True,
    }
    assert status["roles"] == ("bot",) and status["clients"] == 1
    assert status["dispatchers"] == 1


# ── the constructed half is untouched ────────────────────────────────────


def test_constructed_half_still_seven(union):
    assert len(union.core.components()) == 7
    assert all(c is not None for c in union.core.components())

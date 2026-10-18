"""The per-feature DB class layer: each pyUltroid/dB feature module now owns
one class (with an injectable database) plus backward-compatible module-level
function aliases, exported from pyUltroid.dB and surfaced on the core union as
``core.dbs``.

The dB package refuses library-mode imports, so the tests stub the pyUltroid
root with a fake udB (the runtime-bound gate).
"""

from __future__ import annotations

import importlib
import logging
import sys
import types
from types import SimpleNamespace

import pytest

from conftest import ROOT


class FakeUdB:
    def __init__(self):
        self.store = {}

    def get_key(self, key):
        return self.store.get(key)

    def set_key(self, key, value, cache_only=False):
        self.store[key] = value
        return True

    def del_key(self, key):
        self.store.pop(key, None)
        return True


@pytest.fixture
def dB_modules(module_isolation):
    with module_isolation("pyUltroid"):
        root = types.ModuleType("pyUltroid")
        root.__path__ = [str(ROOT / "pyUltroid")]
        root.LOGS = logging.getLogger("dB-tests")
        root.udB = FakeUdB()
        sys.modules["pyUltroid"] = root

        configs = types.ModuleType("pyUltroid.configs")
        configs.Var = SimpleNamespace(DEVLIST_IDS=[])
        sys.modules["pyUltroid.configs"] = configs

        mods = {
            name: importlib.import_module(f"pyUltroid.dB.{name}")
            for name in (
                "filter_db",
                "afk_db",
                "mute_db",
                "antiflood_db",
                "snips_db",
                "notes_db",
                "asstcmd_db",
                "warn_db",
                "blacklist_db",
                "blacklist_chat_db",
                "botchat_db",
                "echo_db",
                "filestore_db",
                "forcesub_db",
                "gban_mute_db",
                "greetings_db",
                "nsfw_db",
                "vc_sudos",
                "guest_db",
            )
        }
        yield SimpleNamespace(
            root=root,
            udb=root.udB,
            dbs=importlib.import_module("pyUltroid.dB").db,
            **mods,
        )


def test_filter_db_round_trip(dB_modules):
    m = dB_modules.dbs.filter
    m.add("chat1", "hello", "hi there", None, None)
    assert m.get("chat1") == {
        "hello": {"msg": "hi there", "media": None, "button": None}
    }
    assert "`hello`" in m.list("chat1")
    m.remove("chat1", "hello")
    assert m.get("chat1") is None
    m.add("chat1", "x", "y", None, None)
    m.remove_all("chat1")
    assert m.get_stuff() == {}


def test_afk_db_round_trip(dB_modules):
    m = dB_modules.dbs.afk
    m.add("brb", None, None)
    afk = m.is_afk()
    assert afk[0] == "brb"
    assert m.delete()
    assert m.is_afk() is False


def test_mute_db_round_trip(dB_modules):
    m = dB_modules.dbs.mute
    m.mute("chat1", 42)
    assert m.is_muted("chat1", 42)
    m.unmute("chat1", 42)
    assert not m.is_muted("chat1", 42)


def test_antiflood_db_round_trip(dB_modules):
    m = dB_modules.dbs.antiflood
    m.set_flood("chat1", 7)
    assert m.get_flood_limit("chat1") == 7
    m.rem_flood("chat1")
    assert m.get_flood_limit("chat1") is None


def test_snips_db_round_trip(dB_modules):
    m = dB_modules.dbs.snips
    m.add("x", "hello", None, None)
    assert m.get("x")["msg"] == "hello"
    assert "$x" in m.list()
    m.remove("x")
    assert m.get("x") is False


def test_notes_db_uses_int_chats(dB_modules):
    m = dB_modules.dbs.notes
    m.add("123", "rule", "be nice", None, None)
    assert m.get(123, "rule")["msg"] == "be nice"
    assert "#rule" in m.list(123)
    m.remove(123, "rule")
    m.remove_all(123)
    # Quirk preserved from the original functions: rem_all_note's falsy
    # check leaves an emptied chat entry behind instead of popping it.
    assert m.get_stuff() == {123: {}}


def test_asstcmd_db_round_trip(dB_modules):
    m = dB_modules.dbs.asstcmd
    m.add("ping", "pong", None, None)
    assert "ping" in m.list()
    msg, media, button = m.reply("ping")
    assert msg == "pong"
    m.remove("ping")
    assert m.reply("ping") is None


def test_warn_db_round_trip(dB_modules):
    m = dB_modules.dbs.warn
    m.add("chat1", "user1", 1, "spam")
    assert m.warns("chat1", "user1") == (1, "spam")
    m.reset("chat1", "user1")
    assert m.warns("chat1", "user1") == (0, None)


def test_blacklist_dbs(dB_modules):
    m = dB_modules.dbs.blacklist
    m.add("chat1", "bad")
    # Original behavior: the first store keeps the whole string; further
    # adds into an existing chat split on whitespace.
    m.add("chat1", "words here")
    assert sorted(m.get("chat1")) == ["bad", "here", "words"]
    m.remove("chat1", "bad")
    assert "bad" not in m.get("chat1")
    c = dB_modules.dbs.blacklist_chat
    c.add(123)
    c.remove(123)


def test_botchat_db_tags(dB_modules):
    m = dB_modules.dbs.botchat
    m.add("11", "user1")
    assert m.get_who("11") == "user1"
    m.tag_add("22", "chat1", "user2")
    assert m.who_tag("22") == ["chat1", "user2"]


def test_echo_db_round_trip(dB_modules):
    m = dB_modules.dbs.echo
    # Real call sites pass int sender ids; the original str/int comparison
    # quirk is preserved, so the test uses ints like the plugins do.
    m.add("100", 200)
    assert m.list(100) == [200]
    assert m.check(100, 200)
    m.remove("100", 200)
    assert not m.check(100, 200)


def test_filestore_and_forcesub(dB_modules):
    f = dB_modules.dbs.filestore
    f.store("h1", 55)
    assert f.get("h1") == 55
    assert f.list_all() == ["h1"]
    f.delete("h1")
    s = dB_modules.dbs.forcesub
    s.add("chat1", "channel1")
    assert s.get_setting("chat1") == "channel1"
    s.remove("chat1")


def test_gban_mute_db(dB_modules):
    m = dB_modules.dbs.gban_mute
    m.gban(42, "spam")
    assert m.is_gbanned(42) == "spam"
    m.ungban(42)
    assert m.is_gbanned(42) is None
    m.gmute(43)
    assert m.is_gmuted(43)
    m.ungmute(43)
    assert not m.is_gmuted(43)


def test_greetings_and_nsfw(dB_modules):
    g = dB_modules.dbs.greetings
    g.add_welcome("chat1", "hi", None, None)
    assert g.get_welcome("chat1")["welcome"] == "hi"
    g.delete_welcome("chat1")
    g.add_thanks("chat2")
    assert g.must_thank("chat2")
    g.remove_thanks("chat2")
    n = dB_modules.dbs.nsfw
    n.nsfw_chat("chat1", "ban")
    assert n.is_nsfw("chat1") == "ban"
    n.rem_nsfw("chat1")
    n.profan_chat("chat1", "delete")
    assert n.is_profan("chat1") == "delete"


def test_vc_sudos(dB_modules):
    m = dB_modules.dbs.vc_sudos
    m.add(99)
    assert m.is_vcsudo(99)
    m.delete(99)
    assert not m.is_vcsudo(99)


def test_guest_db(dB_modules):
    m = dB_modules.dbs.guest
    m.add_guest(7)
    assert m.is_guest(7)
    assert 7 in m.get_guests()
    m.add_guest(8, expiry_seconds=-1)
    assert not m.is_guest(8)  # expired on first check, purged
    assert 8 not in m.get_guests()
    assert m.remove_guest(7)
    assert not m.remove_guest(7)
    assert not m.is_guest(7)


def test_guestmode_wrappers_use_database_union_instance(dB_modules):
    startup = types.ModuleType("pyUltroid.startup")
    startup.__path__ = [str(ROOT / "pyUltroid" / "startup")]
    sys.modules["pyUltroid.startup"] = startup
    guestmode = importlib.import_module("pyUltroid.startup.guestmode")

    assert guestmode._store() is dB_modules.dbs.guest


def test_guest_db_clean_expired(dB_modules):
    m = dB_modules.dbs.guest
    m.add_guest(9, expiry_seconds=-1)
    m.add_guest(10)
    assert m.clean_expired() == [9]
    assert 9 not in m.get_guests()
    assert 10 in m.get_guests()


def test_the_union_is_one_object_per_feature(dB_modules):
    union = dB_modules.dbs
    assert type(union).__name__ == "Database"
    assert len(union.features()) == 19
    assert union.keys()["filter"] == "FILTERS"


def test_no_feature_singleton_survives(dB_modules):
    """Each module exports a class, never an instance."""
    from pyUltroid.dB import afk_db, filter_db, warn_db

    assert not hasattr(filter_db, "filter_db")
    assert not hasattr(afk_db, "afk_db")
    assert not hasattr(warn_db, "warn_db")


def test_features_do_not_take_an_injected_db(dB_modules):
    """There is nothing to inject: a feature is stateless."""
    import inspect

    FeatureDB = type(dB_modules.dbs.filter)

    init = FeatureDB.__mro__[1].__dict__.get("__init__", object.__init__)
    assert "db" not in inspect.signature(init).parameters
    assert not hasattr(FeatureDB, "_udb")


def test_two_instances_of_a_feature_share_the_database(dB_modules):
    """They are stateless, so a second instance sees the same live data."""
    first = dB_modules.dbs.filter
    second = dB_modules.dbs.filter.__class__()
    first.add("c", "w", "m", None, None)
    assert second.get("c") == {"w": {"msg": "m", "media": None, "button": None}}



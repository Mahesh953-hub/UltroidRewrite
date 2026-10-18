"""Tests for pyUltroid.startup._database (W3-D)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class TestDatabaseNoRuntimePip:
    def test_no_os_system_pip_install_calls(self):
        source = (ROOT / "pyUltroid" / "startup" / "_database.py").read_text()
        assert (
            "os.system" not in source
        ), "_database.py still contains runtime pip installation calls"
        assert (
            "pip install" not in source
        ), "_database.py still references pip installation"

    def test_backends_imported_without_installing(self):
        """Each backend module is imported or left None; no install side effects."""
        # Provide a minimal namespace so the module can import without Var.
        saved = sys.modules.copy()
        for name in list(sys.modules):
            if name == "pyUltroid" or name.startswith("pyUltroid."):
                sys.modules.pop(name, None)
        try:
            import types

            root = types.ModuleType("pyUltroid")
            root.__path__ = [str(ROOT / "pyUltroid")]
            sys.modules["pyUltroid"] = root

            startup = types.ModuleType("pyUltroid.startup")
            startup.__path__ = [str(ROOT / "pyUltroid" / "startup")]
            logger = types.ModuleType("pyUltroid.startup.logger")
            import logging

            logger.LOGS = logging.getLogger("test-db")
            startup.LOGS = logger.LOGS
            sys.modules["pyUltroid.startup"] = startup
            sys.modules["pyUltroid.startup.logger"] = logger

            import pyUltroid.startup._database as db

            # The module-level backend handles should be None or real classes,
            # never the install-and-retry pattern.
            assert db.Redis is None or db.Redis.__module__.startswith("redis")
            assert db.MongoClient is None or db.MongoClient.__module__.startswith(
                "pymongo"
            )
            assert db.psycopg2 is None or db.psycopg2.__name__ == "psycopg2"
            assert db.Database is None or hasattr(db.Database, "get")
        finally:
            sys.modules.clear()
            sys.modules.update(saved)

    def test_ultroiddb_falls_back_to_localdb(self, tmp_path, monkeypatch):
        """With no backend package installed UltroidDB must fall back to
        the built-in LocalDB JSON store, never raise."""
        import pyUltroid.startup._database as db

        sys.modules["pyUltroid"].HOSTED_ON = "local"
        monkeypatch.chdir(tmp_path)

        saved = db.Redis, db.MongoClient, db.psycopg2, db.Database
        db.Redis = db.MongoClient = db.psycopg2 = db.Database = None
        try:
            udb = db.UltroidDB()
            assert udb.name == "LocalDB"
        finally:
            db.Redis, db.MongoClient, db.psycopg2, db.Database = saved

    def test_configured_backend_missing_driver_falls_through(
        self, tmp_path, monkeypatch
    ):
        """MONGO_URI set but pymongo not installed -> LocalDB, not a crash
        (the exact production failure this guards against)."""
        import types

        import pyUltroid.startup._database as db

        sys.modules["pyUltroid"].HOSTED_ON = "local"
        monkeypatch.chdir(tmp_path)

        fake_var = types.SimpleNamespace(
            REDIS_URI=None,
            REDISHOST=None,
            REDIS_PASSWORD=None,
            REDISPASSWORD=None,
            REDISPORT=None,
            MONGO_URI="mongodb://example.invalid:27017/ub",
            DATABASE_URL=None,
        )
        saved = db.Var, db.Redis, db.MongoClient, db.psycopg2, db.Database
        db.Var = fake_var
        db.Redis = db.MongoClient = db.psycopg2 = db.Database = None
        try:
            udb = db.UltroidDB()
            assert udb.name == "LocalDB"
        finally:
            db.Var, db.Redis, db.MongoClient, db.psycopg2, db.Database = saved

    def test_json_database_roundtrip(self, tmp_path, monkeypatch):
        import pyUltroid.startup._database as db

        monkeypatch.chdir(tmp_path)
        store = db._JSONDatabase("t")
        assert store.get("k") is None
        assert store.set("k", "v") is True
        assert store.get("k") == "v"
        assert store.keys() == ["k"]
        # persisted across instances
        assert db._JSONDatabase("t").get("k") == "v"
        store.delete("k")
        assert db._JSONDatabase("t").keys() == []


class TestBaseDatabase:
    def test_literal_eval_data_roundtrip(self):
        import pyUltroid.startup._database as db

        base = db._BaseDatabase()
        assert base._get_data(data="{'a': 1}") == {"a": 1}
        assert base._get_data(data="plain") == "plain"

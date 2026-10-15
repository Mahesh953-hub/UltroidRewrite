"""Unit tests for the download/upload queue manager backbone (Phase 1.1).

Covers GID/FID generation, enqueue/cancel/status round-trips, directory file
listing, the owner PM notification format, TTL expiry and udB persistence.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pyUltroid._core.queue import (  # noqa: E402
    DEFAULT_TTL,
    QUEUE_TASKS,
    FID,
    GID,
    QueueManager,
    QueueTask,
    list_directory_files,
)

EM = "\u2014"  # em-dash used by the user-spec PM notification format


# ──────────────────────────────────────────────────────────────────────────
# Test doubles
# ──────────────────────────────────────────────────────────────────────────


class FakeDB:
    """In-memory stand-in for ``udB`` with the key methods the manager uses."""

    def __init__(self) -> None:
        self._store: dict[str, object] = {}

    def get_key(self, key: str):
        return self._store.get(key)

    def set_key(self, key: str, value: object) -> None:
        self._store[key] = value

    def del_key(self, key: str) -> None:
        self._store.pop(key, None)


class FakeClock:
    """A controllable clock so TTL behaviour is deterministic (no real sleeps)."""

    def __init__(self, start: float = 1000.0) -> None:
        self.t = float(start)

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += float(seconds)


class FakeUser:
    """Minimal Telethon-user-like object for mention building."""

    def __init__(self, id=0, first_name=None, username=None):
        self.id = id
        self.first_name = first_name
        self.username = username


def _dl_task(fid_name: str, size_bytes: int = 100) -> QueueTask:
    return QueueTask(
        operation="dl",
        files=[fid_name],
        chat_id=10,
        user_id=20,
        size_bytes=size_bytes,
    )


# ──────────────────────────────────────────────────────────────────────────
# GID / FID generation
# ──────────────────────────────────────────────────────────────────────────


class TestGIDFIDGeneration:
    def test_gid_length_and_hex(self):
        gid = GID()
        assert isinstance(gid, str)
        assert len(gid) == 8
        assert all(c in "0123456789abcdef" for c in gid)

    def test_gid_uniqueness_sample(self):
        # 8 hex chars = 32 bits; a small sample is collision-free in practice
        # (P(collision) ~ 1e-6 for 100 draws) while still exercising variety.
        seen = {GID() for _ in range(100)}
        assert len(seen) == 100

    def test_gid_truncates_uuid4_hex(self, monkeypatch):
        # Deterministically prove GID() surfaces the first 8 hex chars of uuid4.
        import uuid as _uuid

        import pyUltroid._core.queue as qmod

        fixed = _uuid.UUID("12345678-9abc-def0-1234-567890abcdef")
        monkeypatch.setattr(qmod.uuid, "uuid4", lambda: fixed)
        assert GID() == "12345678"

    def test_fid_format(self):
        assert FID("abcd1234", 0) == "abcd1234-0"
        assert FID("abcd1234", 7) == "abcd1234-7"

    def test_fid_index_coercion(self):
        # index is coerced to int so str/float indices still format cleanly
        assert FID("abcd1234", "2") == "abcd1234-2"
        assert FID("abcd1234", 3.0) == "abcd1234-3"


# ──────────────────────────────────────────────────────────────────────────
# QueueTask dataclass
# ──────────────────────────────────────────────────────────────────────────


class TestQueueTask:
    def test_task_defaults(self):
        task = QueueTask()
        assert task.gid == ""
        assert task.fid == ""
        assert task.status == "pending"
        assert task.files == []
        assert task.created_at == 0.0
        assert task.size_bytes == 0

    def test_task_terminal_active_properties(self):
        pending = QueueTask(status="pending")
        downloading = QueueTask(status="downloading")
        done = QueueTask(status="done")
        cancelled = QueueTask(status="cancelled")
        failed = QueueTask(status="failed")
        assert pending.is_active and not pending.is_terminal
        assert downloading.is_active and not downloading.is_terminal
        assert not done.is_active and done.is_terminal
        assert not cancelled.is_active and cancelled.is_terminal
        assert not failed.is_active and failed.is_terminal


# ──────────────────────────────────────────────────────────────────────────
# Enqueue / cancel / status round-trip
# ──────────────────────────────────────────────────────────────────────────


class TestEnqueueCancelStatus:
    def test_enqueue_assigns_gid_fid_and_status(self):
        mgr = QueueManager(db=FakeDB())
        task = QueueTask(operation="dl", files=["a.mp4"], chat_id=1, user_id=2)
        gid = mgr.enqueue([task])
        assert isinstance(gid, str) and len(gid) == 8
        assert task.gid == gid
        assert task.fid == f"{gid}-0"
        assert task.status == "pending"
        assert task.created_at > 0

    def test_enqueue_multiple_tasks_share_gid_distinct_fids(self):
        mgr = QueueManager(db=FakeDB())
        tasks = [_dl_task(f"f{i}") for i in range(4)]
        gid = mgr.enqueue(tasks)
        assert {t.gid for t in tasks} == {gid}
        assert [t.fid for t in tasks] == [f"{gid}-{i}" for i in range(4)]

    def test_enqueue_returns_gid_and_persists(self):
        db = FakeDB()
        mgr = QueueManager(db=db)
        gid = mgr.enqueue([_dl_task("a"), _dl_task("b")])
        blob = db.get_key(QUEUE_TASKS)
        assert isinstance(blob, dict)
        assert gid in blob
        assert len(blob[gid]["tasks"]) == 2
        assert blob[gid]["expiry"] > 0

    def test_cancel_by_gid(self):
        mgr = QueueManager(db=FakeDB())
        gid = mgr.enqueue([_dl_task("a"), _dl_task("b"), _dl_task("c")])
        assert mgr.cancel(gid) is True
        info = mgr.status(gid)
        assert info["cancelled"] == 3
        assert info["active"] == 0
        # idempotent: nothing left to cancel
        assert mgr.cancel(gid) is False

    def test_cancel_by_fid(self):
        mgr = QueueManager(db=FakeDB())
        gid = mgr.enqueue([_dl_task("a"), _dl_task("b"), _dl_task("c")])
        fid = f"{gid}-1"
        assert mgr.cancel(fid) is True
        assert mgr.status(fid)["status"] == "cancelled"
        assert mgr.status(f"{gid}-0")["status"] == "pending"
        info = mgr.status(gid)
        assert info["cancelled"] == 1 and info["active"] == 2

    def test_cancel_unknown_returns_false(self):
        mgr = QueueManager(db=FakeDB())
        assert mgr.cancel("deadbeef") is False
        assert mgr.cancel("deadbeef-9") is False

    def test_cancel_already_terminal_returns_false(self):
        mgr = QueueManager(db=FakeDB())
        gid = mgr.enqueue([_dl_task("a")])
        fid = f"{gid}-0"
        mgr.update_status(fid, "done")
        assert mgr.cancel(fid) is False  # already done, nothing to cancel
        assert mgr.status(fid)["status"] == "done"

    def test_status_by_gid_aggregate(self):
        mgr = QueueManager(db=FakeDB())
        gid = mgr.enqueue(
            [
                QueueTask(operation="dl", files=["a"], size_bytes=100),
                QueueTask(operation="dl", files=["b"], size_bytes=200),
                QueueTask(operation="dl", files=["c"], size_bytes=300),
            ]
        )
        info = mgr.status(gid)
        assert info["found"] is True
        assert info["gid"] == gid
        assert info["total_files"] == 3
        assert info["size_bytes"] == 600
        assert info["done"] == 0
        assert info["active"] == 3
        assert info["progress"] == 0.0
        assert {f["fid"] for f in info["files"]} == {f"{gid}-{i}" for i in range(3)}

    def test_status_by_fid_single(self):
        mgr = QueueManager(db=FakeDB())
        gid = mgr.enqueue([_dl_task("a", size_bytes=128)])
        fid = f"{gid}-0"
        info = mgr.status(fid)
        assert info["found"] is True
        assert info["fid"] == fid
        assert info["gid"] == gid
        assert info["status"] == "pending"
        assert info["operation"] == "dl"
        assert info["size_bytes"] == 128
        assert info["group_total"] == 1

    def test_status_unknown_not_found(self):
        mgr = QueueManager(db=FakeDB())
        assert mgr.status("nope")["found"] is False
        assert mgr.status("nope-0")["found"] is False

    def test_update_status_progress(self):
        mgr = QueueManager(db=FakeDB())
        gid = mgr.enqueue([_dl_task("a"), _dl_task("b"), _dl_task("c"), _dl_task("d")])
        mgr.update_status(f"{gid}-0", "downloading")
        mgr.update_status(f"{gid}-1", "done")
        mgr.update_status(f"{gid}-2", "done")
        mgr.update_status(f"{gid}-3", "failed")
        info = mgr.status(gid)
        assert info["done"] == 2
        assert info["failed"] == 1
        # progress is done/total = 2/4 = 50%
        assert info["progress"] == 50.0
        # group-level status update sets every member
        assert mgr.update_status(gid, "done") is True
        assert mgr.status(gid)["progress"] == 100.0
        # invalid status is rejected
        assert mgr.update_status(gid, "frobnicated") is False

    def test_active_tasks_filters_terminal(self):
        mgr = QueueManager(db=FakeDB())
        gid = mgr.enqueue([_dl_task("a"), _dl_task("b"), _dl_task("c")])
        assert len(mgr.active_tasks()) == 3
        mgr.update_status(f"{gid}-0", "done")
        mgr.cancel(f"{gid}-1")
        active = mgr.active_tasks()
        assert len(active) == 1
        assert {t.fid for t in active} == {f"{gid}-2"}


# ──────────────────────────────────────────────────────────────────────────
# Directory listing
# ──────────────────────────────────────────────────────────────────────────


class TestListDirectoryFiles:
    def test_sorted_ascending_with_total(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "big.bin").write_bytes(b"x" * 500)
            (root / "tiny.txt").write_bytes(b"hi")  # 2 bytes
            (root / "mid.dat").write_bytes(b"y" * 100)
            (root / "sub").mkdir()  # directory should be skipped (non-recursive)
            rows = list_directory_files(directory)
            names = [r[0] for r in rows]
            sizes = [r[1] for r in rows]
            # file rows sorted ascending by size, total appended last
            assert names[:-1] == ["tiny.txt", "mid.dat", "big.bin"]
            assert sizes[:-1] == [2, 100, 500]
            assert rows[-1] == ("Total", 602)
            # whole list stays ascending (total >= every file)
            assert sizes == sorted(sizes)

    def test_empty_directory_and_missing_path(self):
        with tempfile.TemporaryDirectory() as directory:
            assert list_directory_files(directory) == []  # no files, no total row
        assert list_directory_files("/no/such/path/here") == []
        # a file path (not a directory) yields nothing
        with tempfile.NamedTemporaryFile() as tmp:
            assert list_directory_files(tmp.name) == []


# ──────────────────────────────────────────────────────────────────────────
# Owner PM notification format
# ──────────────────────────────────────────────────────────────────────────


class TestNotifyOwner:
    def test_download_format(self):
        mgr = QueueManager(db=FakeDB())
        tasks = [_dl_task(f"f{i}") for i in range(3)]
        gid = mgr.enqueue(tasks)
        user = FakeUser(id=123, first_name="Alice")
        text = mgr.notify_owner(10, user, "dl", tasks[0])
        assert text == (
            f"[Alice](tg://user?id=123) triggered 3 file(s) "
            f"download bulk process {EM} GID: {gid}"
        )

    def test_upload_format(self):
        mgr = QueueManager(db=FakeDB())
        tasks = [QueueTask(operation="ul", files=[f"u{i}"]) for i in range(2)]
        gid = mgr.enqueue(tasks)
        text = mgr.notify_owner(10, FakeUser(id=7, first_name="Bob"), "ul", tasks[0])
        assert text == (
            f"[Bob](tg://user?id=7) triggered 2 file(s) "
            f"upload batch process {EM} GID: {gid}"
        )

    def test_int_user_mention(self):
        mgr = QueueManager(db=FakeDB())
        task = _dl_task("a")
        mgr.enqueue([task])
        text = mgr.notify_owner(10, 42, "dl", task)
        assert text.startswith("[user](tg://user?id=42) triggered 1 file(s) ")
        assert text.endswith(f"download bulk process {EM} GID: {task.gid}")

    def test_standalone_task_count_falls_back_to_files(self):
        # A task not yet enqueued: count derives from its own files list.
        mgr = QueueManager(db=FakeDB())
        task = QueueTask(operation="dl", files=["a", "b", "c", "d"])
        text = mgr.notify_owner(10, FakeUser(id=1, first_name="Zed"), "dl", task)
        assert "triggered 4 file(s) download bulk process" in text


# ──────────────────────────────────────────────────────────────────────────
# TTL expiry + persistence
# ──────────────────────────────────────────────────────────────────────────


class TestTTLAndPersistence:
    def test_ttl_expiry_prunes_group(self):
        clock = FakeClock()
        db = FakeDB()
        mgr = QueueManager(db=db, ttl=3600, clock=clock)
        gid = mgr.enqueue([_dl_task("a"), _dl_task("b")])
        assert mgr.status(gid)["found"] is True
        assert len(mgr.active_tasks()) == 2
        clock.advance(3601)  # past the 1-hour TTL
        assert mgr.status(gid)["found"] is False
        assert mgr.active_tasks() == []
        # cancel / update no longer find the expired group
        assert mgr.cancel(gid) is False
        assert mgr.update_status(gid, "done") is False

    def test_persistence_round_trip_across_instances(self):
        clock = FakeClock()
        db = FakeDB()
        mgr = QueueManager(db=db, ttl=3600, clock=clock)
        gid = mgr.enqueue([_dl_task("a", 50), _dl_task("b", 60)])
        # A brand-new manager pointing at the same store rehydrates the group.
        mgr2 = QueueManager(db=db, ttl=3600, clock=clock)
        info = mgr2.status(gid)
        assert info["found"] is True
        assert info["total_files"] == 2
        assert info["size_bytes"] == 110
        assert {t.fid for t in mgr2.active_tasks()} == {f"{gid}-0", f"{gid}-1"}

    def test_expired_group_skipped_on_reload(self):
        clock = FakeClock()
        db = FakeDB()
        QueueManager(db=db, ttl=3600, clock=clock).enqueue([_dl_task("a")])
        clock.advance(3601)  # group now past TTL in the persisted blob
        mgr = QueueManager(db=db, ttl=3600, clock=clock)
        assert mgr.active_tasks() == []
        blob = db.get_key(QUEUE_TASKS) or {}
        # the stale entry may still be in the blob until a persist rewrites it,
        # but the manager must not surface expired tasks
        assert all(mgr.status(g)["found"] is False for g in blob)

    def test_persist_drops_expired_entries_from_blob(self):
        clock = FakeClock()
        db = FakeDB()
        mgr = QueueManager(db=db, ttl=3600, clock=clock)
        old = mgr.enqueue([_dl_task("a")])
        clock.advance(3601)  # old group expired
        # enqueuing a fresh group rewrites the blob, pruning the expired one
        new = mgr.enqueue([_dl_task("b")])
        blob = db.get_key(QUEUE_TASKS) or {}
        assert old not in blob
        assert new in blob


# ──────────────────────────────────────────────────────────────────────────
# Package re-export contract
# ──────────────────────────────────────────────────────────────────────────


class TestCoreReexport:
    def test_lazy_export_via_getattr(self):
        # The package re-exports the queue backbone eagerly (the PEP 562 hook
        # is gone): ``__all__`` is the explicit export list and every name
        # resolves directly to the queue module's public object.
        core = importlib.import_module("pyUltroid._core")
        assert "QueueManager" in core.__all__
        assert "list_directory_files" in core.__all__
        from pyUltroid._core import (  # noqa: F401
            FID as core_FID,
            GID as core_GID,
            QueueManager as core_QM,
            QueueTask as core_QT,
            list_directory_files as core_ldf,
        )

        assert core_QM is QueueManager
        assert core_QT is QueueTask
        assert core_GID is GID
        assert core_FID is FID
        assert core_ldf is list_directory_files
        # eager names are plain module attributes
        assert getattr(core, "QueueManager") is QueueManager
        assert "pyUltroid._core.queue" in sys.modules

    def test_module_singleton_is_gone(self):
        import pyUltroid._core.queue as qm_module

        assert not hasattr(qm_module, "queue_manager")
        assert DEFAULT_TTL == 3600
        assert QUEUE_TASKS == "QUEUE_TASKS"

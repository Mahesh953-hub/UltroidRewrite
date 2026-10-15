# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Download/upload queue manager.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional
from collections.abc import Callable

QUEUE_TASKS = "QUEUE_TASKS"

DEFAULT_TTL = 3600

_EM_DASH = "\u2014"

_STATUSES = ("pending", "downloading", "uploading", "done", "cancelled", "failed")
_TERMINAL = frozenset(("done", "cancelled", "failed"))


# Identifiers
def GID() -> str:
    """Return a fresh 8-char group id (first 8 hex chars of ``uuid4``)."""
    return uuid.uuid4().hex[:8]


def FID(gid: str, index: int) -> str:
    """Return the file id for *index* within group *gid* (``"{gid}-{index}"``)."""
    return f"{gid}-{int(index)}"


# Data model
@dataclass
class QueueTask:
    """One file within a queued download/upload operation.

    A group of tasks shares a :func:`GID`; each task carries its own
    :func:`FID`.  ``status`` transitions through the lifecycle
    ``pending → downloading|uploading → done|cancelled|failed``.
    """

    gid: str = ""
    fid: str = ""
    status: str = "pending"
    chat_id: int = 0
    user_id: int = 0
    operation: str = ""  # "dl" or "ul"
    files: list[str] = field(default_factory=list)
    created_at: float = 0.0
    size_bytes: int = 0

    @property
    def is_terminal(self) -> bool:
        """``True`` once the task has reached a final state."""
        return self.status in _TERMINAL

    @property
    def is_active(self) -> bool:
        """``True`` while the task can still progress (not terminal)."""
        return self.status not in _TERMINAL


def _task_to_dict(task: QueueTask) -> dict:
    """Serialize a task to a primitives-only dict (``ast.literal_eval`` safe)."""
    return {
        "gid": task.gid,
        "fid": task.fid,
        "status": task.status,
        "chat_id": task.chat_id,
        "user_id": task.user_id,
        "operation": task.operation,
        "files": list(task.files),
        "created_at": task.created_at,
        "size_bytes": task.size_bytes,
    }


def _task_from_dict(data: dict) -> QueueTask:
    """Reconstruct a task from a persisted dict, tolerating missing keys."""
    return QueueTask(
        gid=str(data.get("gid", "")),
        fid=str(data.get("fid", "")),
        status=str(data.get("status", "pending")),
        chat_id=int(data.get("chat_id", 0) or 0),
        user_id=int(data.get("user_id", 0) or 0),
        operation=str(data.get("operation", "")),
        files=list(data.get("files", []) or []),
        created_at=float(data.get("created_at", 0.0) or 0.0),
        size_bytes=int(data.get("size_bytes", 0) or 0),
    )


# Notification helpers
def _user_mention(user: Any) -> str:
    """Build a Telegram mention string from a user-like object or numeric id."""
    if user is None or isinstance(user, bool):
        return "Someone"
    if isinstance(user, int):
        return f"[user](tg://user?id={user})"
    uid = getattr(user, "id", None)
    name = (
        getattr(user, "first_name", None)
        or getattr(user, "title", None)
        or getattr(user, "username", None)
    )
    name = str(name) if name else "there"
    if uid:
        return f"[{name}](tg://user?id={uid})"
    return name


def _operation_words(operation: Any) -> tuple:
    """Map an operation code to ``(verb, mode)`` for the PM notification.

    ``dl``/download → ``("download", "bulk")``; ``ul``/upload →
    ``("upload", "batch")``.  Unknown values fall back to download/bulk.
    """
    op = str(operation or "").lower()
    if op in ("ul", "upload", "up", "u"):
        return "upload", "batch"
    return "download", "bulk"


# Queue manager
class QueueManager:
    """In-memory + udB-persisted queue of download/upload tasks.

    Args:
        db: Optional key/value store with ``get_key``/``set_key``/``del_key``
            (typically ``pyUltroid.udB``).  When ``None`` (the default for the
            module singleton) the manager lazily resolves ``udB`` on first use
            and operates purely in-memory if that is also unavailable.
        client: Optional Telethon client bound via :meth:`bind_client` for
            best-effort owner notifications.
        ttl: Time-to-live for a persisted group, in seconds (default 1 hour).
        store_key: udB key used to persist the queue blob.
        clock: Callable returning the current time (default ``time.time``);
            injectable for deterministic TTL tests.
    """

    def __init__(
        self,
        db: Any = None,
        client: Any = None,
        *,
        ttl: float = DEFAULT_TTL,
        store_key: str = QUEUE_TASKS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db
        self._client = client
        self._ttl = float(ttl)
        self._store_key = store_key
        self._clock = clock
        self._groups: dict[str, list[QueueTask]] = {}
        self._expiry: dict[str, float] = {}
        self._loaded = False

    # ── dependency injection
    def bind_database(self, db: Any) -> None:
        """Attach (or re-attach) a udB instance, forcing a fresh load."""
        self._db = db
        self._loaded = False

    def bind_client(self, client: Any) -> None:
        """Attach a Telethon client for best-effort owner notifications."""
        self._client = client

    # ── internals
    def _now(self) -> float:
        return float(self._clock())

    def _resolve_db(self) -> Any:
        if self._db is not None:
            return self._db
        try:
            from pyUltroid import udB

            return udB
        except Exception:
            return None

    def _log(self):
        try:
            from ..startup.logger import LOGS

            return LOGS
        except Exception:
            import logging

            return logging.getLogger("pyUltroid._core.queue")

    def _load_blob(self, db: Any) -> dict:
        raw = db.get_key(self._store_key)
        return raw if isinstance(raw, dict) else {}

    def _ensure_loaded(self) -> None:
        """Populate in-memory state from ``udB`` once (skipping expired)."""
        if self._loaded:
            return
        self._loaded = True
        db = self._resolve_db()
        if db is None:
            return
        now = self._now()
        for gid, entry in self._load_blob(db).items():
            if not isinstance(entry, dict):
                continue
            expiry = entry.get("expiry", 0.0) or 0.0
            if expiry and expiry <= now:
                continue
            tasks = entry.get("tasks", []) or []
            self._groups[gid] = [
                _task_from_dict(t) for t in tasks if isinstance(t, dict)
            ]
            self._expiry[gid] = expiry or (now + self._ttl)

    def _prune(self) -> None:
        """Drop groups whose TTL has elapsed from in-memory state."""
        now = self._now()
        for gid in [gid for gid, exp in self._expiry.items() if exp <= now]:
            self._groups.pop(gid, None)
            self._expiry.pop(gid, None)

    def _refresh(self) -> None:
        self._ensure_loaded()
        self._prune()

    def _persist_group(self, gid: str) -> None:
        """Write one group back to ``udB``, dropping expired groups meanwhile."""
        db = self._resolve_db()
        if db is None:
            return
        blob = self._load_blob(db)
        now = self._now()
        for bgid in list(blob.keys()):
            entry = blob.get(bgid)
            if not isinstance(entry, dict):
                blob.pop(bgid, None)
                continue
            exp = entry.get("expiry", 0.0) or 0.0
            if exp and exp <= now:
                blob.pop(bgid, None)
        group = self._groups.get(gid)
        if group:
            blob[gid] = {
                "expiry": self._expiry.get(gid, now + self._ttl),
                "tasks": [_task_to_dict(t) for t in group],
            }
        else:
            blob.pop(gid, None)
        try:
            db.set_key(self._store_key, blob)
        except Exception as exc:  # noqa: BLE001
            self._log().debug("[queue] persist failed for %s: %s", gid, exc)

    @staticmethod
    def _split(gid_or_fid: str) -> tuple[str, int | None]:
        """Split a GID or FID into ``(gid, optional_index)``."""
        if "-" in gid_or_fid:
            gid, _, idx = gid_or_fid.partition("-")
            try:
                return gid, int(idx)
            except ValueError:
                return gid, None
        return gid_or_fid, None

    def _targets(self, gid: str, index: int | None):
        """Return the tasks targeted by a GID/FID, or ``None`` if not found."""
        group = self._groups.get(gid)
        if not group:
            return None
        if index is None:
            return list(group)
        if 0 <= index < len(group):
            return [group[index]]
        return None

    # ── public API
    def enqueue(self, tasks: list[QueueTask]) -> str:
        """Register *tasks* as one group, returning the new GID.

        Each task is assigned ``gid``/``fid`` (``FID(gid, index)``), a
        ``pending`` status (unless already set to a valid state) and a
        ``created_at`` timestamp when missing, then persisted with a TTL.
        """
        self._refresh()
        gid = GID()
        now = self._now()
        prepared: list[QueueTask] = []
        for index, task in enumerate(tasks):
            task.gid = gid
            task.fid = FID(gid, index)
            if task.status not in _STATUSES:
                task.status = "pending"
            if not task.created_at:
                task.created_at = now
            prepared.append(task)
        self._groups[gid] = prepared
        self._expiry[gid] = now + self._ttl
        self._persist_group(gid)
        return gid

    def cancel(self, gid_or_fid: str) -> bool:
        """Cancel a whole group (GID) or a single file (FID).

        Returns ``True`` if at least one non-terminal task was cancelled.
        Already-terminal tasks are left untouched; unknown ids return ``False``.
        """
        self._refresh()
        gid, index = self._split(gid_or_fid)
        targets = self._targets(gid, index)
        if not targets:
            return False
        changed = False
        for task in targets:
            if not task.is_terminal:
                task.status = "cancelled"
                changed = True
        if changed:
            self._persist_group(gid)
        return changed

    def update_status(self, gid_or_fid: str, status: str) -> bool:
        """Move a single file (FID) or whole group (GID) to *status*.

        *status* must be one of the valid lifecycle states.  Returns ``True``
        if at least one task was updated.  This is the transition hook plugins
        use to mark tasks ``downloading``/``uploading``/``done``/``failed``.
        """
        self._refresh()
        if status not in _STATUSES:
            return False
        gid, index = self._split(gid_or_fid)
        targets = self._targets(gid, index)
        if not targets:
            return False
        changed = False
        for task in targets:
            if task.status != status:
                task.status = status
                changed = True
        if changed:
            self._persist_group(gid)
        return changed

    def status(self, gid_or_fid: str) -> dict:
        """Return progress info for a group (GID) or a single file (FID)."""
        self._refresh()
        gid, index = self._split(gid_or_fid)
        group = self._groups.get(gid)
        if not group:
            return {"found": False, "gid": gid, "fid": gid_or_fid}
        if index is not None:
            if not 0 <= index < len(group):
                return {"found": False, "gid": gid, "fid": gid_or_fid}
            return self._task_status(group[index], group)
        return self._group_status(gid, group)

    def _task_status(self, task: QueueTask, group: list[QueueTask]) -> dict:
        total = len(group)
        done = sum(1 for t in group if t.status == "done")
        progress = round(done / total * 100.0, 2) if total else 0.0
        return {
            "found": True,
            "gid": task.gid,
            "fid": task.fid,
            "status": task.status,
            "operation": task.operation,
            "chat_id": task.chat_id,
            "user_id": task.user_id,
            "files": list(task.files),
            "size_bytes": task.size_bytes,
            "created_at": task.created_at,
            "group_total": total,
            "group_done": done,
            "progress": progress,
        }

    def _group_status(self, gid: str, group: list[QueueTask]) -> dict:
        counts: dict[str, int] = {s: 0 for s in _STATUSES}
        total_bytes = 0
        for t in group:
            counts[t.status] = counts.get(t.status, 0) + 1
            total_bytes += t.size_bytes or 0
        total = len(group)
        done = counts["done"]
        cancelled = counts["cancelled"]
        failed = counts["failed"]
        active = total - done - cancelled - failed
        progress = round(done / total * 100.0, 2) if total else 0.0
        return {
            "found": True,
            "gid": gid,
            "total_files": total,
            "status_counts": counts,
            "done": done,
            "cancelled": cancelled,
            "failed": failed,
            "active": active,
            "size_bytes": total_bytes,
            "progress": progress,
            "files": [
                {"fid": t.fid, "status": t.status, "size_bytes": t.size_bytes}
                for t in group
            ],
        }

    def active_tasks(self) -> list[QueueTask]:
        """Return all tasks that are not in a terminal state."""
        self._refresh()
        out: list[QueueTask] = []
        for group in self._groups.values():
            for task in group:
                if task.is_active:
                    out.append(task)
        return out

    def notify_owner(self, chat_id, user, operation, task) -> str:
        """Build the owner PM notification text for a queued operation.

        Returns the user-spec formatted string::

            "{user_mention} triggered {count} file(s) {verb} {mode} process  -  GID: {gid}"

        with ``verb`` = download/upload and ``mode`` = bulk/batch.  ``count``
        is the total number of files across the task's group (falling back to
        the task's own ``files`` list, then ``1``).

        ``chat_id`` is accepted for delivery routing and reserved for future
        best-effort send hooks; the caller (the async plugin handler) owns
        actual delivery of the returned text to the owner.
        """
        op = operation if operation is not None else getattr(task, "operation", None)
        verb, mode = _operation_words(op)
        mention = _user_mention(user)
        gid = getattr(task, "gid", None) or _EM_DASH
        count = self._notification_count(task)
        return (
            f"{mention} triggered {count} file(s) {verb} {mode} "
            f"process {_EM_DASH} GID: {gid}"
        )

    def _notification_count(self, task: QueueTask) -> int:
        gid = getattr(task, "gid", None)
        group = self._groups.get(gid) if gid else None
        if group:
            total = sum(len(t.files) for t in group)
            return total if total else len(group)
        files = getattr(task, "files", None)
        if files:
            return len(files)
        return 1


# Directory listing helper
def list_directory_files(path: str) -> list[tuple]:
    """Return an ascending-by-size table of ``(filename, size_bytes)``.

    Regular files directly under *path* (non-recursive) are listed sorted by
    size ascending.  A final ``("Total", total_bytes)`` row is appended  -  it
    is always the largest value, so the whole list stays ascending by size.
    Returns an empty list when *path* is missing, not a directory, or empty.
    """
    root = Path(path)
    if not root.is_dir():
        return []
    rows: list[tuple] = []
    total = 0
    for entry in root.iterdir():
        if not entry.is_file():
            continue
        try:
            size = entry.stat().st_size
        except OSError:
            continue
        rows.append((entry.name, size))
        total += size
    rows.sort(key=lambda r: (r[1], r[0]))
    if rows:
        rows.append(("Total", total))
    return rows


# better ways are ahead, use here or there.

__all__ = [
    "QueueManager",
    "QueueTask",
    "GID",
    "FID",
    "list_directory_files",
    "QUEUE_TASKS",
    "DEFAULT_TTL",
]

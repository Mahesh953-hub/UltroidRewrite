# Ultroid - UserBot Rewrite
# pyUltroid/shell/_session.py
#
# ShellSession  -  stateful execution environment per caller.
#
# Each session maintains:
#   cwd          -  current working directory, persists across commands
#   env          -  environment variables layered on top of os.environ
#   history      -  last N commands with output, queryable
#   owner        -  who owns this session (user_id or "ultroid" or "ai:<id>")
#   created_at   -  for TTL enforcement
#   last_used    -  idle timeout enforcement
#
# Sessions are stored in Redis with a TTL.
# On Redis miss, the session is rebuilt from defaults  -  no crash.
#
# Design rule:
#   Sessions are isolated per caller identity.
#   An AI session cannot read a user session's history.
#   Ultroid internal sessions are invisible to all external callers.

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

from pyUltroid.startup.logger import LOGS

# ── Constants ──────────────────────────────────────────────────────────────

SESSION_TTL = 3600  # 1 hour idle TTL in Redis
SESSION_MAX_HIST = 100  # max commands kept in history per session
SESSION_KEY_PREFIX = "hss:session:"  # Redis key namespace
WORKDIR_BASE = "/tmp/ultroid_shell"  # base for per-session working dirs

# ── Session history entry ──────────────────────────────────────────────────


@dataclass
class HistoryEntry:
    command: str
    stdout: str
    stderr: str
    exit_code: int
    cwd: str
    duration_ms: float
    timestamp: float = field(default_factory=time.time)
    request_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> HistoryEntry:
        return cls(**d)

    def __str__(self) -> str:
        status = "✓" if self.exit_code == 0 else f"✗({self.exit_code})"
        return f"[{status}] {self.command[:80]}"


# ── ShellSession ───────────────────────────────────────────────────────────


@dataclass
class ShellSession:
    """
    One stateful shell context. Instantiate via ShellSessionManager, not directly.
    """

    session_id: str
    owner: str  # "user:<uid>", "ultroid", "ai:<uuid>"
    cwd: str = field(default_factory=lambda: WORKDIR_BASE)
    env: dict[str, str] = field(default_factory=dict)
    history: list[HistoryEntry] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_used: float = field(default_factory=time.time)

    # ── Working directory ──────────────────────────────────────────────────

    def set_cwd(self, path: str) -> None:
        """Update cwd after a successful cd command."""
        if os.path.isdir(path):
            self.cwd = path
            self.touch()
        else:
            raise NotADirectoryError(f"Not a directory: {path}")

    # ── Environment ────────────────────────────────────────────────────────

    def set_env(self, key: str, value: str) -> None:
        """Layer a variable into this session's env overlay."""
        self.env[key] = value
        self.touch()

    def unset_env(self, key: str) -> None:
        self.env.pop(key, None)
        self.touch()

    def effective_env(self) -> dict[str, str]:
        """Merge session env overlay on top of real os.environ."""
        merged = dict(os.environ)
        merged.update(self.env)
        # Strip secrets from the merged env before passing to subprocess
        for sensitive in (
            "SESSION",
            "BOT_TOKEN",
            "API_HASH",
            "REDIS_PASSWORD",
            "MONGO_URI",
            "DATABASE_URL",
            "AWS_SECRET_ACCESS_KEY",
        ):
            merged.pop(sensitive, None)
        return merged

    # ── History ────────────────────────────────────────────────────────────

    def add_history(self, entry: HistoryEntry) -> None:
        self.history.append(entry)
        if len(self.history) > SESSION_MAX_HIST:
            self.history = self.history[-SESSION_MAX_HIST:]
        self.touch()

    def last_n(self, n: int = 10) -> list[HistoryEntry]:
        return self.history[-n:]

    def find_in_history(self, query: str) -> list[HistoryEntry]:
        q = query.lower()
        return [h for h in self.history if q in h.command.lower()]

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def touch(self) -> None:
        self.last_used = time.time()

    def is_expired(self) -> bool:
        return (time.time() - self.last_used) > SESSION_TTL

    # ── Serialisation ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "owner": self.owner,
            "cwd": self.cwd,
            "env": self.env,
            "history": [h.to_dict() for h in self.history],
            "created_at": self.created_at,
            "last_used": self.last_used,
        }

    @classmethod
    def from_dict(cls, d: dict) -> ShellSession:
        history = [HistoryEntry.from_dict(h) for h in d.get("history", [])]
        return cls(
            session_id=d["session_id"],
            owner=d["owner"],
            cwd=d.get("cwd", WORKDIR_BASE),
            env=d.get("env", {}),
            history=history,
            created_at=d.get("created_at", time.time()),
            last_used=d.get("last_used", time.time()),
        )

    def __repr__(self) -> str:
        return f"ShellSession(id={self.session_id[:8]}, owner={self.owner}, cwd={self.cwd})"


# ── ShellSessionManager ────────────────────────────────────────────────────


class ShellSessionManager:
    """
    Creates, retrieves, persists, and expires ShellSessions.

    Backed by Redis. Falls back to an in-process dict on Redis failure
    (acceptable for single-instance AWS deployment  -  no cross-process
    session sharing needed).
    """

    def __init__(self) -> None:
        self._local: dict[str, ShellSession] = {}  # fallback store
        self._redis = None  # set lazily
        self._workdir_initialised = False

    # ── Redis connection ───────────────────────────────────────────────────

    def _get_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis as _redis
            from pyUltroid.configs import Var

            uri = getattr(Var, "REDIS_URI", None) or getattr(Var, "REDIS_URL", None)
            if uri:
                self._redis = _redis.from_url(uri, decode_responses=True)
                self._redis.ping()
                LOGS.info("[HSS] Session manager connected to Redis.")
            else:
                LOGS.warning("[HSS] No Redis URI  -  sessions stored in-process only.")
        except Exception as e:
            LOGS.warning(
                f"[HSS] Redis unavailable ({e})  -  falling back to in-process store."
            )
            self._redis = None
        return self._redis

    # ── Workdir init ───────────────────────────────────────────────────────

    def _ensure_workdir(self) -> None:
        if self._workdir_initialised:
            return
        os.makedirs(WORKDIR_BASE, exist_ok=True)
        self._workdir_initialised = True

    # ── Session workdir ────────────────────────────────────────────────────

    def _session_workdir(self, session_id: str) -> str:
        path = os.path.join(WORKDIR_BASE, session_id[:8])
        os.makedirs(path, exist_ok=True)
        return path

    # ── Core CRUD ──────────────────────────────────────────────────────────

    def _redis_key(self, session_id: str) -> str:
        return f"{SESSION_KEY_PREFIX}{session_id}"

    def _save(self, session: ShellSession) -> None:
        r = self._get_redis()
        if r:
            try:
                r.setex(
                    self._redis_key(session.session_id),
                    SESSION_TTL,
                    json.dumps(session.to_dict()),
                )
                return
            except Exception as e:
                LOGS.warning(f"[HSS] Redis save failed ({e}), using local store.")
        self._local[session.session_id] = session

    def _load(self, session_id: str) -> ShellSession | None:
        r = self._get_redis()
        if r:
            try:
                raw = r.get(self._redis_key(session_id))
                if raw:
                    return ShellSession.from_dict(json.loads(raw))
            except Exception as e:
                LOGS.warning(f"[HSS] Redis load failed ({e}), trying local store.")
        return self._local.get(session_id)

    def _delete(self, session_id: str) -> None:
        r = self._get_redis()
        if r:
            try:
                r.delete(self._redis_key(session_id))
            except Exception:
                pass
        self._local.pop(session_id, None)

    # ── Public API ─────────────────────────────────────────────────────────

    def get_or_create(self, session_id: str, owner: str) -> ShellSession:
        """
        Return an existing session or create a fresh one.
        owner format: "user:<uid>", "ultroid", "ai:<uuid>"
        """
        self._ensure_workdir()
        session = self._load(session_id)

        if session is None or session.is_expired():
            workdir = self._session_workdir(session_id)
            session = ShellSession(
                session_id=session_id,
                owner=owner,
                cwd=workdir,
            )
            LOGS.info(f"[HSS] New session {session_id[:8]} for {owner}")

        self._save(session)
        return session

    def get(self, session_id: str) -> ShellSession | None:
        """Retrieve an existing session. Returns None if missing or expired."""
        session = self._load(session_id)
        if session and session.is_expired():
            self._delete(session_id)
            return None
        return session

    def save(self, session: ShellSession) -> None:
        """Persist session state after modifications."""
        self._save(session)

    def destroy(self, session_id: str) -> None:
        """Explicitly destroy a session."""
        self._delete(session_id)
        LOGS.info(f"[HSS] Session {session_id[:8]} destroyed.")

    def list_sessions(self, owner_prefix: str = "") -> list[str]:
        """List all active session IDs, optionally filtered by owner prefix."""
        r = self._get_redis()
        sessions = []
        if r:
            try:
                for key in r.scan_iter(f"{SESSION_KEY_PREFIX}*"):
                    sid = key[len(SESSION_KEY_PREFIX) :]
                    if owner_prefix:
                        s = self._load(sid)
                        if s and s.owner.startswith(owner_prefix):
                            sessions.append(sid)
                    else:
                        sessions.append(sid)
                return sessions
            except Exception:
                pass
        # fallback
        for sid, s in self._local.items():
            if not owner_prefix or s.owner.startswith(owner_prefix):
                sessions.append(sid)
        return sessions


# ── Module singleton ───────────────────────────────────────────────────────
# Import this  -  do not instantiate ShellSessionManager yourself.


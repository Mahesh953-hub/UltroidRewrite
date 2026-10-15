"""DM agent-session state: anchors, ordering, and history keys.

The store is deliberately free of any assistant-stack import so it can be
used  -  and tested  -  without a started bot. ``assistant/ai_dm.py`` owns the
user-facing handlers and calls into this module for state.

A session is identified by its *anchor*: the id of the session-start card in
the bot's DM. Every reply into that card's thread belongs to the session.
"""

from __future__ import annotations

import time
from typing import Any, ClassVar, Dict, List, Optional

__all__ = (
    "MAX_SESSIONS",
    "SESSIONS_PREFIX",
    "SessionStore",
    "SessionStoreError",
)

#: Sessions kept per user, most recent first.
MAX_SESSIONS = 20

#: uDB key prefix for a user's session list.
SESSIONS_PREFIX = "AGENT_DM_SESSIONS_"

#: A DM session's history key namespace. Never a bare chat id, so a session's
#: history can never collide with a normal chat's history.
_SESSION_KEY = "dm:{user}:{anchor}"


class SessionStore:
    """The DM agent-session store (udB keys ``AGENT_DM_SESSIONS_<user>``).

    Same shape as the dB layer's ``FeatureDB`` classes: one instance per
    feature (``session_store``), with an injectable database for tests and
    """

    SESSION_KEY: ClassVar[str] = SESSIONS_PREFIX


    def session_list(self, user_id: Any) -> list[dict[str, Any]]:
        """A user's sessions, newest first. Each is ``anchor``, ``title``, ``created``."""
        db = self._db_access()
        if db is None:
            return []
        try:
            stored = db.get_key(self._key(user_id))
        except Exception:
            stored = None
        if not isinstance(stored, list):
            return []
        return [s for s in stored if isinstance(s, dict) and s.get("anchor")]

    def _save(self, user_id: Any, sessions: list[dict[str, Any]]) -> bool:
        db = self._db_access()
        if db is None:
            return False
        try:
            db.set_key(self._key(user_id), sessions[:MAX_SESSIONS])
            return True
        except Exception:
            return False

    def session_find(self, user_id: Any, anchor: int) -> dict[str, Any] | None:
        """The session anchored at *anchor* for *user_id*, or ``None``."""
        for session in self.session_list(user_id):
            if session.get("anchor") == anchor:
                return session
        return None

    def session_create(self, user_id: Any, anchor: int, title: str) -> dict[str, Any]:
        """Record a session anchored at *anchor*, replacing any older one there.

        Raises :class:`SessionStoreError` when the write does not land: a
        session the store cannot find again is not a session, and pretending
        otherwise would strand the user's next reply.
        """
        session = {
            "anchor": int(anchor),
            "title": (title or "New session")[:60],
            "created": int(time.time()),
        }
        sessions = [s for s in self.session_list(user_id) if s.get("anchor") != anchor]
        sessions.insert(0, session)
        if not self._save(user_id, sessions):
            raise SessionStoreError(
                f"the session for {user_id} at anchor {anchor} could not be stored"
            )
        return session

    def session_history_key(self, user_id: Any, anchor: int) -> str:
        """The agent history key for one session."""
        return _SESSION_KEY.format(user=user_id, anchor=anchor)

    def _db_access(self):
        """The live database. Absent before startup has built it."""
        try:
            from pyUltroid import udB
        except Exception:
            return None
        return udB

    def _key(self, user_id: Any) -> str:
        return f"{SESSIONS_PREFIX}{user_id}"









class SessionStoreError(RuntimeError):
    """A session could not be persisted."""




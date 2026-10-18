"""Bounded state for opt-in Telegram bot-to-bot conversations."""

from __future__ import annotations

import asyncio
import re
from collections import OrderedDict, defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any


@dataclass(slots=True)
class B2BSession:
    """One in-memory conversation between an allowlisted bot pair."""

    bot_id: int
    chat_id: int
    username: str | None
    started_at: float
    last_activity: float
    turns: int = 0
    outbound_ids: deque[int] = field(default_factory=lambda: deque(maxlen=16))
    model: Any = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def is_bot_to_bot_disabled(error: BaseException) -> bool:
    """Recognize Telegram's B2B-disabled RPC code or its CamelCase class."""
    detail = " ".join(
        (
            error.__class__.__name__,
            str(getattr(error, "message", "")),
            str(error),
        )
    ).upper()
    compact = re.sub(r"[^A-Z0-9]", "", detail)
    return "USERBOTTOBOTDISABLED" in compact or "BOTTOBOTDISABLED" in compact


class B2BConversationManager:
    """Persist opt-in access settings and bound active conversations in memory."""

    CONFIG_KEY = "B2B_CONFIG"
    WINDOW_SECONDS = 60.0
    DEDUP_SECONDS = 600.0
    IDLE_TIMEOUT_SECONDS = 600.0
    MAX_SESSION_SECONDS = 1800.0
    MAX_TURNS = 8
    MAX_CHAT_MESSAGES_PER_MINUTE = 6
    MAX_BOT_MESSAGES_PER_MINUTE = 12
    MAX_ACTIVE_SESSIONS = 8
    MAX_ALLOWED_BOTS = 32
    MAX_GROUPS_PER_BOT = 16
    MAX_DEDUP_ENTRIES = 2048

    def __init__(
        self,
        config: Any = None,
        *,
        save: Callable[[dict[str, Any]], Any] | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """Create a manager from persisted JSON-like config and injectable clock."""
        self._save = save
        self._clock = clock
        raw = config if isinstance(config, dict) else {}
        self.enabled = bool(raw.get("enabled", False))
        self.allowed_bots: set[int] = set()
        for value in raw.get("allowed_bots", ()):
            bot_id = self._positive_id(value)
            if bot_id is not None:
                self.allowed_bots.add(bot_id)
        self.allowed_groups: dict[int, set[int]] = defaultdict(set)
        raw_groups = raw.get("allowed_groups", {})
        if isinstance(raw_groups, dict):
            for raw_bot, values in raw_groups.items():
                bot_id = self._positive_id(raw_bot)
                if bot_id is None or not isinstance(values, (list, tuple, set)):
                    continue
                for value in values:
                    group_id = self._group_id(value)
                    if group_id is not None:
                        self.allowed_groups[bot_id].add(group_id)
        self._sessions: dict[tuple[int, int], B2BSession] = {}
        self._seen: OrderedDict[tuple[int, int, int], float] = OrderedDict()
        self._chat_hits: dict[int, deque[float]] = defaultdict(deque)
        self._bot_hits: dict[int, deque[float]] = defaultdict(deque)

    @staticmethod
    def _positive_id(value: Any) -> int | None:
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return result if result > 0 else None

    @staticmethod
    def _group_id(value: Any) -> int | None:
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return result if result < 0 else None

    @staticmethod
    def _key(chat_id: int, bot_id: int) -> tuple[int, int]:
        return int(chat_id), int(bot_id)

    @property
    def known_bot_ids(self) -> set[int]:
        """Return every peer with private or group access configured."""
        return self.allowed_bots | set(self.allowed_groups)

    def _snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "allowed_bots": sorted(self.allowed_bots),
            "allowed_groups": {
                str(bot_id): sorted(group_ids)
                for bot_id, group_ids in sorted(self.allowed_groups.items())
                if group_ids
            },
        }

    def _persist(self) -> None:
        if self._save is not None:
            self._save(self._snapshot())

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable the feature; disabling terminates every session."""
        self.enabled = bool(enabled)
        if not self.enabled:
            self.stop_all()
        self._persist()

    def allow_bot(self, bot_id: int, group_id: int | None = None) -> bool:
        """Allow a bot in private chats or one exact group, never both implicitly."""
        bot = self._positive_id(bot_id)
        if bot is None:
            raise ValueError("bot ID must be a positive integer")
        group = None if group_id is None else self._group_id(group_id)
        if group_id is not None and group is None:
            raise ValueError("group ID must be negative")
        known = self.known_bot_ids
        is_new = bot not in known
        if is_new and len(known) >= self.MAX_ALLOWED_BOTS:
            raise ValueError("the bot allowlist is full")
        groups = self.allowed_groups.get(bot, set())
        if (
            group is not None
            and group not in groups
            and len(groups) >= self.MAX_GROUPS_PER_BOT
        ):
            raise ValueError("the group allowlist for this bot is full")
        if group is None:
            self.allowed_bots.add(bot)
        else:
            self.allowed_groups[bot].add(group)
        self._persist()
        return is_new

    def revoke_bot(self, bot_id: int) -> bool:
        """Remove a bot and terminate its active private and group sessions."""
        bot = self._positive_id(bot_id)
        if bot is None:
            return False
        existed = bot in self.known_bot_ids
        self.allowed_bots.discard(bot)
        self.allowed_groups.pop(bot, None)
        for key in [key for key in self._sessions if key[1] == bot]:
            self._sessions.pop(key, None)
        self._persist()
        return existed

    def is_allowed(self, bot_id: int, chat_id: int) -> bool:
        """Check the bot allowlist and exact chat scope."""
        bot = self._positive_id(bot_id)
        if bot is None:
            return False
        chat = int(chat_id)
        if chat == bot:
            return bot in self.allowed_bots
        return chat < 0 and chat in self.allowed_groups.get(bot, set())

    def start(
        self, bot_id: int, chat_id: int, username: str | None = None
    ) -> B2BSession:
        """Start or replace one allowed private or group conversation."""
        bot = self._positive_id(bot_id)
        if bot is None:
            raise ValueError("bot ID must be a positive integer")
        chat = int(chat_id)
        if not self.enabled:
            raise RuntimeError("bot-to-bot conversations are disabled")
        if not self.is_allowed(bot, chat):
            raise PermissionError("this bot/chat pair is not allowlisted")
        if chat != bot and chat >= 0:
            raise ValueError("conversation chat must be the bot's private chat or a group")
        key = self._key(chat, bot)
        if key not in self._sessions and len(self._sessions) >= self.MAX_ACTIVE_SESSIONS:
            raise RuntimeError("the maximum number of active conversations is reached")
        now = self._clock()
        session = B2BSession(
            bot_id=bot,
            chat_id=chat,
            username=username,
            started_at=now,
            last_activity=now,
        )
        self._sessions[key] = session
        return session

    def stop(self, bot_id: int, chat_id: int) -> bool:
        """Stop one private or group conversation."""
        return self._sessions.pop(self._key(chat_id, bot_id), None) is not None

    def stop_all(self) -> int:
        """Stop all active conversations and release their model histories."""
        count = len(self._sessions)
        self._sessions.clear()
        return count

    def active_count(self) -> int:
        """Expire stale conversations and return the remaining session count."""
        now = self._clock()
        for key, session in list(self._sessions.items()):
            if self._expired(session, now):
                self._sessions.pop(key, None)
        return len(self._sessions)

    def record_outbound(self, session: B2BSession, message_id: Any) -> None:
        """Remember an outgoing reply ID so group replies remain in-chain."""
        if message_id is None or self._sessions.get(self._key(session.chat_id, session.bot_id)) is not session:
            return
        try:
            value = int(message_id)
        except (TypeError, ValueError, OverflowError):
            return
        if value > 0:
            session.outbound_ids.append(value)
            session.last_activity = self._clock()

    def is_active(self, session: B2BSession) -> bool:
        """Return whether a session is still current and within both timeouts."""
        key = self._key(session.chat_id, session.bot_id)
        if self._sessions.get(key) is not session:
            return False
        if self._expired(session, self._clock()):
            self._sessions.pop(key, None)
            return False
        return self.enabled and self.is_allowed(session.bot_id, session.chat_id)

    def _expired(self, session: B2BSession, now: float) -> bool:
        return (
            now - session.last_activity > self.IDLE_TIMEOUT_SECONDS
            or now - session.started_at > self.MAX_SESSION_SECONDS
        )

    def _remember_once(self, key: tuple[int, int, int], now: float) -> bool:
        for old_key, timestamp in list(self._seen.items()):
            if now - timestamp > self.DEDUP_SECONDS:
                self._seen.pop(old_key, None)
        if key in self._seen:
            self._seen.move_to_end(key)
            return False
        self._seen[key] = now
        if len(self._seen) > self.MAX_DEDUP_ENTRIES:
            self._seen.popitem(last=False)
        return True

    def _rate_allowed(self, bot_id: int, chat_id: int, now: float) -> bool:
        chat_hits = self._chat_hits[chat_id]
        bot_hits = self._bot_hits[bot_id]
        cutoff = now - self.WINDOW_SECONDS
        while chat_hits and chat_hits[0] <= cutoff:
            chat_hits.popleft()
        while bot_hits and bot_hits[0] <= cutoff:
            bot_hits.popleft()
        if (
            len(chat_hits) >= self.MAX_CHAT_MESSAGES_PER_MINUTE
            or len(bot_hits) >= self.MAX_BOT_MESSAGES_PER_MINUTE
        ):
            return False
        chat_hits.append(now)
        bot_hits.append(now)
        return True

    def accept_incoming(
        self,
        *,
        chat_id: int,
        bot_id: int,
        message_id: int,
        reply_to_id: int | None,
        is_private: bool,
        sender_is_bot: bool,
    ) -> tuple[B2BSession | None, str]:
        """Validate and account for one incoming peer-bot message."""
        if not self.enabled:
            return None, "disabled"
        bot = self._positive_id(bot_id)
        if not sender_is_bot or bot is None:
            return None, "not_bot"
        chat = int(chat_id)
        key = self._key(chat, bot)
        session = self._sessions.get(key)
        if session is None:
            return None, "no_session"
        if not self.is_allowed(bot, chat):
            self._sessions.pop(key, None)
            return None, "not_allowed"
        now = self._clock()
        if self._expired(session, now):
            self._sessions.pop(key, None)
            return None, "expired"
        if is_private:
            if chat != bot:
                return None, "private_peer_mismatch"
        elif chat >= 0 or reply_to_id not in session.outbound_ids:
            return None, "not_in_reply_chain"
        try:
            message = int(message_id)
        except (TypeError, ValueError, OverflowError):
            return None, "invalid_message"
        if not self._remember_once((chat, bot, message), now):
            return None, "duplicate"
        if session.turns >= self.MAX_TURNS:
            self._sessions.pop(key, None)
            return None, "turn_limit"
        if not self._rate_allowed(bot, chat, now):
            return None, "rate_limited"
        session.turns += 1
        session.last_activity = now
        return session, "accepted"

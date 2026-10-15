"""Player state holder for the voice-chat queue.

The previous cache stored bare :class:`CachedTrack` values in a per-chat deque
with an ``is_active`` flag. That is a *container*, not a player: it cannot
answer "what is playing right now", cannot track elapsed time, cannot tell
paused from playing, and has no history. Commands had to recompute state from
scratch and most of them simply could not.

This module adds the missing state holder:

``Track``
    One item of work, with position in the queue and its own status.
``Queue``
    Ordered tracks with ``current``/``next``/``upcoming`` semantics, plus
    ``move`` and ``remove_at`` for the reorder/remove commands.
``PlayerState``
    Live playback state: current track, playing/paused/stopped, when the
    current track started, and the per-chat loop count.
``PlayerRegistry``
    One :class:`PlayerState` per chat, created lazily, with an asyncio lock
    per chat so a skip racing a join cannot interleave.

Deliberately free of PyTgCalls imports so the state machine is unit-testable
without a Telegram connection.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, Iterator, Optional

from ._dataclass import CachedTrack, ChannelPlay

__all__ = [
    "TrackStatus",
    "PlaybackState",
    "Track",
    "Queue",
    "PlayerState",
    "PlayerRegistry",
    "track_from_cached",
]


class TrackStatus(str, Enum):
    PENDING = "pending"
    PLAYING = "playing"
    FINISHED = "finished"
    FAILED = "failed"


class PlaybackState(str, Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


@dataclass
class Track:
    """A single playable item plus its queue-local metadata."""

    cached: CachedTrack
    status: TrackStatus = TrackStatus.PENDING
    #: Set when this track failed, for surfacing in the error message.
    error: str = ""

    # -- identity ----------------------------------------------------------
    @property
    def title(self) -> str:
        return self.cached.name or "Unknown"

    @property
    def artist(self) -> str:
        return self.cached.artist or "Unknown artist"

    @property
    def duration(self) -> int:
        return int(self.cached.duration or 0)

    @property
    def requester(self) -> str:
        return self.cached.user or "unknown"

    @property
    def thumbnail(self) -> str:
        return self.cached.thumbnail or ""

    @property
    def platform(self) -> str:
        return self.cached.platform or "unknown"

    @property
    def url(self) -> str:
        return self.cached.url

    @property
    def file_path(self):
        return self.cached.file_path

    @property
    def is_video(self) -> bool:
        return bool(self.cached.is_video)

    @property
    def is_live(self) -> bool:
        # A live stream reports no meaningful duration.
        return self.duration <= 0

    def to_cached(self) -> CachedTrack:
        return self.cached

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.title} - {self.artist}"


def track_from_cached(cached: CachedTrack, status: TrackStatus = TrackStatus.PENDING) -> Track:
    return Track(cached=cached, status=status)


class Queue:
    """Ordered tracks with a live ``current`` head."""

    def __init__(self, tracks: Iterable[Track] = ()) -> None:
        self._items: list[Track] = list(tracks)

    # -- container protocol ------------------------------------------------
    def __len__(self) -> int:
        return len(self._items)

    def __iter__(self) -> Iterator[Track]:
        return iter(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    def __getitem__(self, index: int) -> Track:
        return self._items[index]

    # -- mutation ----------------------------------------------------------
    def add(self, track: Track) -> Track:
        self._items.append(track)
        return track

    def clear(self) -> list[Track]:
        """Drop every track and return what was removed."""
        removed, self._items = self._items, []
        return removed

    def remove_at(self, index: int) -> Optional[Track]:
        """Remove the track at *index* (1-based, as users count the queue)."""
        idx = self._normalize(index)
        if idx is None or idx >= len(self._items):
            return None
        return self._items.pop(idx)

    def move(self, src: int, dest: int) -> bool:
        """Move the track at 1-based *src* to 1-based *dest*."""
        s, d = self._normalize(src), self._normalize(dest)
        if s is None or d is None:
            return False
        if s >= len(self._items) or d >= len(self._items):
            return False
        item = self._items.pop(s)
        self._items.insert(d, item)
        return True

    # -- head/tail ---------------------------------------------------------
    @property
    def current(self) -> Optional[Track]:
        return self._items[0] if self._items else None

    @property
    def next(self) -> Optional[Track]:
        return self._items[1] if len(self._items) > 1 else None

    @property
    def upcoming(self) -> list[Track]:
        """Everything after the current track."""
        return self._items[1:]

    # -- consumption -------------------------------------------------------
    def pop_current(self) -> Optional[Track]:
        """Remove and return the head. The core of ``skip``/advance."""
        return self._items.pop(0) if self._items else None

    def promote_next(self) -> Optional[Track]:
        """Drop the head and return the new head, marking it PLAYING.

        This is what finishing a track does: the played item is removed and
        the next one becomes current without a second lookup.
        """
        if self._items:
            self._items.pop(0)
        nxt = self.current
        if nxt is not None:
            nxt.status = TrackStatus.PLAYING
        return nxt

    def rotate(self) -> Optional[Track]:
        """Move the head to the back and return the new head (looping)."""
        if not self._items:
            return None
        head = self._items.pop(0)
        head.status = TrackStatus.PENDING
        self._items.append(head)
        nxt = self.current
        if nxt is not None:
            nxt.status = TrackStatus.PLAYING
        return nxt

    def requeue(self, track: Track) -> Track:
        """Put *track* back at the head (used when playback fails)."""
        self._items.insert(0, track)
        return track

    def to_cached_list(self) -> list[CachedTrack]:
        return [t.cached for t in self._items]

    @staticmethod
    def _normalize(index: int) -> Optional[int]:
        """Map a 1-based user index to a 0-based list index."""
        if not isinstance(index, int) or isinstance(index, bool):
            return None
        if index < 1:
            return None
        return index - 1

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Queue len={len(self._items)}>"


@dataclass
class PlayerState:
    """Live playback state for a single chat."""

    queue: Queue = field(default_factory=Queue)
    state: PlaybackState = PlaybackState.STOPPED
    #: ``time.monotonic()`` when the current track started, else None.
    started_at: Optional[float] = None
    loop: int = 0
    channel: Optional[ChannelPlay] = None
    history: list[Track] = field(default_factory=list)

    # -- derived -----------------------------------------------------------
    @property
    def is_active(self) -> bool:
        """Backwards-compatible view used by the existing cache API."""
        return self.state is PlaybackState.PLAYING

    @property
    def is_paused(self) -> bool:
        return self.state is PlaybackState.PAUSED

    @property
    def is_playing(self) -> bool:
        return self.state is PlaybackState.PLAYING

    @property
    def is_stopped(self) -> bool:
        return self.state is PlaybackState.STOPPED

    @property
    def current(self) -> Optional[Track]:
        return self.queue.current

    @property
    def next(self) -> Optional[Track]:
        return self.queue.next

    @property
    def upcoming(self) -> list[Track]:
        return self.queue.upcoming

    def position(self, now: Optional[float] = None) -> float:
        """Seconds elapsed on the current track (0.0 when not playing)."""
        if self.started_at is None or not self.is_playing:
            return 0.0
        return max(0.0, (now if now is not None else time.monotonic()) - self.started_at)

    def progress(self, now: Optional[float] = None) -> float:
        """Fraction of the current track elapsed, 0.0-1.0.

        Returns 0.0 for live streams, which have no duration to divide by.
        """
        dur = self.current.duration if self.current else 0
        if dur <= 0:
            return 0.0
        return min(1.0, self.position(now) / dur)

    # -- transitions -------------------------------------------------------
    def start(self, now: Optional[float] = None) -> Optional[Track]:
        """Mark the head as playing and start the clock."""
        cur = self.queue.current
        if cur is None:
            self.state = PlaybackState.STOPPED
            self.started_at = None
            return None
        cur.status = TrackStatus.PLAYING
        self.state = PlaybackState.PLAYING
        self.started_at = now if now is not None else time.monotonic()
        return cur

    def pause(self) -> None:
        if self.state is PlaybackState.PLAYING:
            self.state = PlaybackState.PAUSED

    def resume(self, now: Optional[float] = None) -> None:
        """Resume from pause, preserving elapsed time.

        The clock is rebased on resume: without this, a paused track would
        report the paused interval as elapsed playback.
        """
        if self.state is not PlaybackState.PAUSED:
            return
        self.state = PlaybackState.PLAYING
        # started_at is monotonic; shift it forward by the paused interval so
        # position() resumes where it left off.
        if self.started_at is not None:
            self.started_at = now if now is not None else time.monotonic()

    def finish_current(self) -> Optional[Track]:
        """Archive the head and advance, honouring the loop count."""
        done = self.queue.current
        if done is not None:
            done.status = TrackStatus.FINISHED
            self.history.append(done)
            # Bound the history so a long-lived chat cannot grow unbounded.
            if len(self.history) > 50:
                del self.history[:-50]

        if self.loop > 0 and done is not None:
            self.loop -= 1
            nxt = self.queue.rotate()
        else:
            nxt = self.queue.promote_next()

        if nxt is None:
            self.state = PlaybackState.STOPPED
            self.started_at = None
        else:
            self.started_at = time.monotonic()
            self.state = PlaybackState.PLAYING
        return nxt

    def fail_current(self, error: str = "") -> Optional[Track]:
        """Drop the failed head and advance past it."""
        bad = self.queue.current
        if bad is not None:
            bad.status = TrackStatus.FAILED
            bad.error = error
        return self.queue.promote_next()

    def stop(self) -> None:
        self.queue.clear()
        self.state = PlaybackState.STOPPED
        self.started_at = None
        self.loop = 0

    def snapshot(self) -> dict:
        """Serialisable view, for debug logging and command replies."""
        cur = self.current
        return {
            "state": self.state.value,
            "length": len(self.queue),
            "loop": self.loop,
            "current": str(cur) if cur else None,
            "position": round(self.position(), 1),
            "progress": round(self.progress(), 3),
        }


class PlayerRegistry:
    """Per-chat :class:`PlayerState`, each guarded by its own lock."""

    def __init__(self) -> None:
        self._players: dict[int, PlayerState] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    def get(self, chat_id: int) -> PlayerState:
        """Return the chat's state, creating it on first use."""
        if chat_id not in self._players:
            self._players[chat_id] = PlayerState()
        return self._players[chat_id]

    def peek(self, chat_id: int) -> Optional[PlayerState]:
        """Return the state only if it already exists."""
        return self._players.get(chat_id)

    def lock(self, chat_id: int) -> asyncio.Lock:
        """The chat's mutation lock, created on first use."""
        if chat_id not in self._locks:
            self._locks[chat_id] = asyncio.Lock()
        return self._locks[chat_id]

    def drop(self, chat_id: int) -> None:
        self._players.pop(chat_id, None)
        self._locks.pop(chat_id, None)

    def clear(self) -> None:
        self._players.clear()
        self._locks.clear()

    def active_chats(self) -> list[int]:
        return [cid for cid, p in self._players.items() if p.is_active]

    def __contains__(self, chat_id: object) -> bool:
        return chat_id in self._players

    def __len__(self) -> int:
        return len(self._players)

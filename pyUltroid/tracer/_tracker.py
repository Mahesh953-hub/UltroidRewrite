# Ultroid - UserBot Rewrite
# pyUltroid/tracer/_tracker.py
#
# TraceTracker  -  records and queries execution traces for every
# @ultroid_cmd / @asst_cmd handler invocation.
#
# A trace captures:
#   - which handler fired
#   - who triggered it (user_id, chat_id)
#   - what DB keys were accessed
#   - what shell commands were spawned (via HSS request_id correlation)
#   - what external APIs were called
#   - how long it took
#   - what exception occurred (if any)
#   - the full call chain leading here
#
# Traces are stored in Redis (fast ring buffer, last 10,000)
# AND MongoDB (persistent, queryable forever).
#
# The AI agent reads traces to understand what the system already did
# and to avoid repeating failed actions.

from __future__ import annotations

import asyncio
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from pyUltroid.startup.logger import LOGS

# ── Constants ──────────────────────────────────────────────────────────────

REDIS_TRACE_KEY = "hcs:traces"
REDIS_TRACE_LIMIT = 10_000  # ring buffer size in Redis
MONGO_COLLECTION = "hcs_traces"


# ── TraceRecord ────────────────────────────────────────────────────────────


@dataclass
class TraceRecord:
    """
    One complete execution trace.
    Created at handler entry, closed at handler exit.
    """

    trace_id: str
    plugin: str
    handler: str  # function name
    pattern: str | None  # matched pattern
    user_id: int
    chat_id: int
    is_private: bool
    started_at: float
    ended_at: float = 0.0
    duration_ms: float = 0.0

    # What happened during execution
    db_reads: list[str] = field(default_factory=list)  # keys read
    db_writes: list[str] = field(default_factory=list)  # keys written
    shell_calls: list[str] = field(default_factory=list)  # request_ids
    api_calls: list[str] = field(default_factory=list)  # URLs called
    messages_sent: int = 0

    # Outcome
    success: bool = True
    exception: str | None = None
    exc_type: str | None = None

    # Input context
    raw_text: str = ""  # first 200 chars of message text
    args: str = ""  # parsed command args

    def close(self, success: bool = True, exc: Exception | None = None) -> None:
        self.ended_at = time.time()
        self.duration_ms = (self.ended_at - self.started_at) * 1000
        self.success = success
        if exc:
            self.exception = traceback.format_exc()[-2000:]
            self.exc_type = type(exc).__name__

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> TraceRecord:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def summary(self) -> str:
        status = "✅" if self.success else f"❌ {self.exc_type}"
        elapsed = f"{self.duration_ms:.0f}ms"
        return (
            f"[{status}] {self.plugin}.{self.handler} "
            f"| user={self.user_id} chat={self.chat_id} "
            f"| {elapsed}"
        )


# ── Active trace context ───────────────────────────────────────────────────
# Thread-local storage so nested async handlers don't clobber each other.
# asyncio tasks share a thread  -  we use task-local storage via a dict keyed
# by the current Task object.

_active_traces: dict[Any, TraceRecord] = {}


def _current_task_key():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def get_current_trace() -> TraceRecord | None:
    """Return the active trace for the current asyncio task, if any."""
    return _active_traces.get(_current_task_key())


def _set_trace(trace: TraceRecord | None) -> None:
    key = _current_task_key()
    if key is None:
        return
    if trace is None:
        _active_traces.pop(key, None)
    else:
        _active_traces[key] = trace


# ── TraceTracker ───────────────────────────────────────────────────────────


class TraceTracker:
    """
    Records, stores, and queries execution traces.
    Module singleton  -  import `tracer` from pyUltroid.tracer.

    Core usage (automatic via @trace_handler decorator):

        @trace_handler
        @ultroid_cmd(pattern="play")
        async def play_cmd(event):
            ...

    Manual recording within a handler:

        trace = get_current_trace()
        if trace:
            trace.db_reads.append("MUSIC_QUEUE")
            trace.api_calls.append("https://youtube.com/...")

    Querying:

        recent = await tracer.recent(n=20)
        fails  = await tracer.failures(plugin="play")
        user   = await tracer.by_user(user_id=123456)
    """

    def __init__(self):
        self._redis = None
        self._mongo = None
        self._buffer: list[TraceRecord] = []  # in-process fallback
        self._buf_limit = 1000

    # ── Storage backends ───────────────────────────────────────────────────

    def _get_redis(self):
        if self._redis is not None:
            return self._redis
        try:
            import redis as _r
            from pyUltroid.configs import Var

            uri = getattr(Var, "REDIS_URI", None) or getattr(Var, "REDIS_URL", None)
            if uri:
                self._redis = _r.from_url(uri, decode_responses=True)
                self._redis.ping()
        except Exception:
            self._redis = None
        return self._redis

    def _get_mongo(self):
        if self._mongo is not None:
            return self._mongo
        try:
            from pyUltroid import udB

            if hasattr(udB, "_mongo") and udB._mongo is not None:
                self._mongo = udB._mongo[MONGO_COLLECTION]
        except Exception:
            pass
        return self._mongo

    # ── Record a trace ─────────────────────────────────────────────────────

    async def record(self, trace: TraceRecord) -> None:
        """Persist a completed trace. Non-blocking  -  errors are swallowed."""
        import json

        def _json_default(obj):
            """Fallback for non-serializable Telethon entities."""
            if hasattr(obj, "to_dict"):
                return obj.to_dict()
            if hasattr(obj, "__dict__"):
                return str(obj)
            return str(obj)

        payload = json.dumps(trace.to_dict(), default=_json_default)

        # Redis ring buffer
        r = self._get_redis()
        if r:
            try:
                r.lpush(REDIS_TRACE_KEY, payload)
                r.ltrim(REDIS_TRACE_KEY, 0, REDIS_TRACE_LIMIT - 1)
            except Exception as e:
                LOGS.debug(f"[HCS:TRACE] Redis write failed: {e}")

        # MongoDB persistent store
        col = self._get_mongo()
        if col:
            try:
                col.insert_one(
                    json.loads(json.dumps(trace.to_dict(), default=_json_default))
                )
            except Exception as e:
                LOGS.debug(f"[HCS:TRACE] MongoDB write failed: {e}")

        # In-process fallback
        self._buffer.append(trace)
        if len(self._buffer) > self._buf_limit:
            self._buffer = self._buffer[-self._buf_limit :]

    # ── Queries ─────────────────────────────────────────────────────────────

    async def recent(self, n: int = 20) -> list[TraceRecord]:
        """Last n traces across all handlers."""
        return await self._fetch(limit=n)

    async def by_plugin(self, plugin: str, n: int = 20) -> list[TraceRecord]:
        return await self._fetch(filter={"plugin": plugin}, limit=n)

    async def by_handler(self, handler: str, n: int = 20) -> list[TraceRecord]:
        return await self._fetch(filter={"handler": handler}, limit=n)

    async def by_user(self, user_id: int, n: int = 20) -> list[TraceRecord]:
        return await self._fetch(filter={"user_id": user_id}, limit=n)

    async def failures(self, plugin: str = None, n: int = 20) -> list[TraceRecord]:
        f = {"success": False}
        if plugin:
            f["plugin"] = plugin
        return await self._fetch(filter=f, limit=n)

    async def slow(self, threshold_ms: float = 2000, n: int = 20) -> list[TraceRecord]:
        """Traces that took longer than threshold_ms."""
        all_traces = await self._fetch(limit=500)
        return [t for t in all_traces if t.duration_ms > threshold_ms][:n]

    async def by_db_key(self, key: str, n: int = 20) -> list[TraceRecord]:
        """Traces that read or wrote a specific DB key."""
        all_traces = await self._fetch(limit=500)
        return [t for t in all_traces if key in t.db_reads or key in t.db_writes][:n]

    async def by_shell_request(self, request_id: str) -> TraceRecord | None:
        """Find the trace that spawned a specific shell request_id."""
        all_traces = await self._fetch(limit=1000)
        for t in all_traces:
            if request_id in t.shell_calls:
                return t
        return None

    async def stats(self) -> dict:
        """Summary statistics."""
        all_traces = await self._fetch(limit=1000)
        if not all_traces:
            return {"total": 0}

        total = len(all_traces)
        failures = sum(1 for t in all_traces if not t.success)
        avg_dur = sum(t.duration_ms for t in all_traces) / total
        plugins = {}
        exc_types = {}

        for t in all_traces:
            plugins[t.plugin] = plugins.get(t.plugin, 0) + 1
            if t.exc_type:
                exc_types[t.exc_type] = exc_types.get(t.exc_type, 0) + 1

        top_plugins = sorted(plugins.items(), key=lambda x: -x[1])[:10]

        return {
            "total": total,
            "failures": failures,
            "success_rate": f"{((total - failures) / total * 100):.1f}%",
            "avg_duration_ms": f"{avg_dur:.0f}",
            "top_plugins": top_plugins,
            "exc_types": exc_types,
        }

    # ── Internal fetch ─────────────────────────────────────────────────────

    async def _fetch(
        self,
        filter: dict = None,
        limit: int = 20,
    ) -> list[TraceRecord]:
        import json

        records = []

        # Try Redis first (fast, recent)
        r = self._get_redis()
        if r:
            try:
                raw_list = r.lrange(REDIS_TRACE_KEY, 0, max(limit * 3, 300) - 1)
                for raw in raw_list:
                    try:
                        d = json.loads(raw)
                        if filter:
                            if not all(d.get(k) == v for k, v in filter.items()):
                                continue
                        records.append(TraceRecord.from_dict(d))
                        if len(records) >= limit:
                            break
                    except Exception:
                        continue
                if records:
                    return records
            except Exception as e:
                LOGS.debug(f"[HCS:TRACE] Redis fetch failed: {e}")

        # Try MongoDB
        col = self._get_mongo()
        if col:
            try:
                cursor = col.find(
                    filter or {},
                    sort=[("started_at", -1)],
                    limit=limit,
                )
                return [TraceRecord.from_dict(d) for d in cursor]
            except Exception as e:
                LOGS.debug(f"[HCS:TRACE] MongoDB fetch failed: {e}")

        # In-process fallback
        candidates = list(reversed(self._buffer))
        if filter:
            candidates = [
                t
                for t in candidates
                if all(getattr(t, k, None) == v for k, v in filter.items())
            ]
        return candidates[:limit]


_RECORDER: "TraceTracker | None" = None


def _live_tracker() -> "TraceTracker":
    """The tracker ``@trace_handler`` records into.

    Resolved through the package's ``tr`` union so a decorated handler and a
    caller reading ``tr.live`` see the same object. Falls back to a private
    instance only if the union is unreachable (import cycles at boot).
    """
    global _RECORDER
    if _RECORDER is None:
        try:
            from . import tr

            _RECORDER = tr.live
        except Exception:
            _RECORDER = TraceTracker()
    return _RECORDER


# ── @trace_handler decorator ───────────────────────────────────────────────


def trace_handler(func):
    """
    Decorator that wraps an @ultroid_cmd handler with automatic trace recording.

    Apply AFTER @ultroid_cmd:

        @ultroid_cmd(pattern="play")
        @trace_handler
        async def play_cmd(event):
            ...

    Or apply to the function before decoration (cleaner):

        @trace_handler
        async def play_cmd(event):
            ...

    The decorator reads plugin/handler metadata from the function's
    __module__ and __name__ attributes.
    """
    import functools

    @functools.wraps(func)
    async def wrapper(event):
        plugin = getattr(func, "__module__", "unknown")
        handler = func.__name__

        # Extract context from event
        try:
            user_id = event.sender_id or 0
            chat_id = event.chat_id or 0
            is_private = event.is_private
            raw_text = (event.raw_text or "")[:200]
        except Exception:
            user_id = chat_id = 0
            is_private = False
            raw_text = ""

        trace = TraceRecord(
            trace_id=str(uuid.uuid4()),
            plugin=plugin,
            handler=handler,
            pattern=None,
            user_id=user_id,
            chat_id=chat_id,
            is_private=is_private,
            started_at=time.time(),
            raw_text=raw_text,
        )

        _set_trace(trace)
        exc_caught = None

        try:
            result = await func(event)
            trace.close(success=True)
            return result
        except Exception as e:
            exc_caught = e
            trace.close(success=False, exc=e)
            raise
        finally:
            _set_trace(None)
            record = _live_tracker()
            asyncio.ensure_future(record.record(trace))





# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""Dependency-free SSE log streaming.

Four classes, four unrelated jobs:

* :class:`RedactionFilter` rewrites one ``LogRecord`` in place.
* :class:`BrokerHandler` is the logging -> broker bridge.
* :class:`LogBroker` is the thread-safe fan-out with replay history, and
  hands out subscriptions as an explicit async context manager.
* :class:`LogStreamHub` owns the *attachment*: it remembers the handler it
  installed so the process-global logger mutation can be undone. The old
  ``attach_broker`` returned the handler and every caller threw it away,
  which meant the root logger was permanently wired to a broker nobody
  could reach.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import threading
from types import TracebackType
from typing import Final

from .security import AUTHORITY, SecretRedactor

LOG_FORMAT: Final[str] = "%(asctime)s | %(name)s [%(levelname)s] : %(message)s"

DEFAULT_HISTORY: Final[int] = 200


class RedactionFilter(logging.Filter):
    """Replaces a record's message with its redacted form.

    ``record.args`` is cleared afterwards so the formatter cannot re-apply
    ``%`` interpolation to text that has already been substituted - that is
    the classic way secrets leak back in through the args tuple.
    """

    __slots__ = ("redactor",)

    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        super().__init__()
        self.redactor = redactor if redactor is not None else AUTHORITY.redactor

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            redacted = self.redactor.redact(record.getMessage())
        except Exception:
            record.msg = "<log message unavailable>"
            redacted = record.msg
        record.msg = redacted
        record.args = ()
        return True


class BrokerHandler(logging.Handler):
    """Formats a record and hands the text to a :class:`LogBroker`."""

    __slots__ = ("broker",)

    def __init__(self, broker: "LogBroker", level: int = logging.INFO) -> None:
        super().__init__(level)
        self.broker = broker
        self.setFormatter(logging.Formatter(LOG_FORMAT))
        self.addFilter(RedactionFilter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            text = self.format(record)
        except Exception:
            return
        self.broker.publish(text)


class LogBroker:
    """Thread-safe publish/subscribe fan-out with a bounded replay buffer.

    Producer side may run on any thread (the logging module calls it from
    whatever thread logged); consumer side lives on the server loop, which is
    why publishing hops through ``call_soon_threadsafe``.
    """

    __slots__ = ("_history", "_subscribers", "_lock")

    def __init__(self, history: int = DEFAULT_HISTORY) -> None:
        self._history: collections.deque[str] = collections.deque(maxlen=history)
        self._subscribers: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()

    # -- producer side (any thread)

    def publish(self, text: str) -> None:
        with self._lock:
            self._history.append(text)
            subscribers = list(self._subscribers.items())
        for queue_, loop in subscribers:
            try:
                loop.call_soon_threadsafe(queue_.put_nowait, text)
            except RuntimeError:
                # The consumer loop is gone; drop it instead of leaking it.
                self.unsubscribe(queue_)

    # -- consumer side (server loop)

    def subscribe(self, loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Queue:
        loop = loop or asyncio.get_running_loop()
        queue_: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._subscribers[queue_] = loop
        return queue_

    def unsubscribe(self, queue_: asyncio.Queue) -> None:
        with self._lock:
            self._subscribers.pop(queue_, None)

    def history(self) -> list[str]:
        with self._lock:
            return list(self._history)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    @contextlib.asynccontextmanager
    async def subscription(self, loop: asyncio.AbstractEventLoop | None = None):
        """Async context manager wrapping subscribe/unsubscribe.

        The SSE routes used to hand-roll the unsubscribe in a ``finally``;
        an early ``return`` from a client disconnect inside a generator is
        exactly the case that is easy to miss, so the pairing now lives in
        one place.
        """
        queue_ = self.subscribe(loop)
        try:
            yield queue_
        finally:
            self.unsubscribe(queue_)


class LogStreamHub:
    """Owns one broker + handler attachment to a logger.

    :meth:`attach` is idempotent per hub, and :meth:`detach` reverses it -
    including the level bump, which is restored only if we were the ones who
    raised it.
    """

    __slots__ = ("broker", "handler", "logger", "_previous_level", "_attached")

    def __init__(self, broker: LogBroker | None = None) -> None:
        self.broker: LogBroker = broker if broker is not None else LogBroker()
        self.handler: BrokerHandler | None = None
        self.logger: logging.Logger | None = None
        self._previous_level: int | None = None
        self._attached = False

    @property
    def attached(self) -> bool:
        return self._attached

    def attach(
        self, logger_name: str | None = None, level: int = logging.INFO
    ) -> tuple[LogBroker, BrokerHandler]:
        """Wire this hub's broker into *logger_name* (the root logger if None).

        The level is only raised, never lowered, and only when the logger was
        left at ``NOTSET`` (the root default) - hijacking an explicit level
        would be rude.
        """
        if self._attached and self.handler is not None:
            return self.broker, self.handler
        logger = logging.getLogger(logger_name)
        handler = BrokerHandler(self.broker, level=level)
        logger.addHandler(handler)
        if logger.level > level or logger.level == logging.NOTSET:
            self._previous_level = logger.level
            logger.setLevel(level)
        self.logger = logger
        self.handler = handler
        self._attached = True
        return self.broker, handler

    def detach(self) -> bool:
        """Undo :meth:`attach`. Returns whether anything was removed."""
        if not self._attached or self.logger is None or self.handler is None:
            return False
        self.logger.removeHandler(self.handler)
        if self._previous_level is not None:
            self.logger.setLevel(self._previous_level)
        self._attached = False
        self.logger = None
        self.handler = None
        self._previous_level = None
        return True

    def __enter__(self) -> "LogStreamHub":
        self.attach()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.detach()

    def __repr__(self) -> str:
        state = "attached" if self._attached else "detached"
        return f"<LogStreamHub {self.broker!r} {state}>"


# -- module surface -------------------------------------------------------


def attach_broker(
    logger_name: str | None = None,
    level: int = logging.INFO,
    broker: LogBroker | None = None,
) -> tuple[LogBroker, BrokerHandler]:
    return LogStreamHub(broker).attach(logger_name, level)
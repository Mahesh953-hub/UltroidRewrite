# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""HuggingFace keep-alive pinger.

Full narrative: see docs/HANDBOOK.md (module narratives appendix).

:class:`KeepAliveConfig` is a frozen value object parsed out of the
environment. :class:`KeepAlivePinger` is the actor: it owns the pacing
loop, the stop flag and the thread, so ``server.py`` no longer has to
reach for a bare ``threading.Thread``.

``requests`` moved from a top-level import to a lazy one inside the HTTP
call. It is an *optional* extra (``[project.optional-dependencies].local``),
and this module used to be the one file in the package that made the import
mandatory - so ``server.py`` could not even start without it.
"""

from __future__ import annotations

import logging
import os
import random
import threading
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Callable, Final, Mapping

LOGS: logging.Logger = logging.getLogger(__name__)
if not LOGS.handlers:
    LOGS.addHandler(logging.StreamHandler())
    LOGS.setLevel(logging.INFO)
    LOGS.propagate = False

DEFAULT_MIN: Final[int] = 7200
DEFAULT_MAX: Final[int] = 28800
FLOOR_INTERVAL: Final[int] = 60
REQUEST_TIMEOUT: Final[int] = 10
THREAD_NAME: Final[str] = "hf-keep-alive"

FALSY: Final[frozenset[str]] = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class KeepAliveConfig:
    """One pinger's parameters, all of them environment-derived."""

    url: str | None
    token: str | None
    enabled: bool
    min_interval: int = DEFAULT_MIN
    max_interval: int = DEFAULT_MAX

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "KeepAliveConfig":
        source = os.environ if env is None else env
        url = source.get("SPACE_URL")
        token = source.get("HF_TOKEN")
        flag = (source.get("KEEP_ALIVE") or "1").strip().lower()
        enabled = bool(url and token) and flag not in FALSY

        def _int(name: str, default: int) -> int:
            try:
                return int(source.get(name) or default)
            except (TypeError, ValueError):
                return default

        min_interval = max(FLOOR_INTERVAL, _int("KEEP_ALIVE_MIN_SECONDS", DEFAULT_MIN))
        max_interval = max(
            min_interval, _int("KEEP_ALIVE_MAX_SECONDS", DEFAULT_MAX)
        )
        return cls(
            url=url,
            token=token,
            enabled=enabled,
            min_interval=min_interval,
            max_interval=max_interval,
        )

    @property
    def headers(self) -> dict[str, str]:
        return {
            "authorization": f"bearer {self.token}",
            "content-type": "application/json",
        }


class KeepAlivePinger:
    """The pacing loop, plus the thread that runs it.

    ``stop`` is a real flag rather than "kill the daemon": the loop checks
    it between pings, so a stop request costs at most one sleep interval
    and never a half-finished HTTP call.
    """

    __slots__ = ("config", "_stop", "_thread", "_sleeper", "_ping")

    def __init__(
        self,
        config: KeepAliveConfig,
        sleeper: Callable[[float], None] | None = None,
        ping: Callable[[KeepAliveConfig], Any] | None = None,
    ) -> None:
        self.config = config
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._sleeper = sleeper if sleeper is not None else self._interruptible_sleep
        self._ping = ping if ping is not None else self._http_ping

    # -- one request

    @staticmethod
    def _http_ping(config: KeepAliveConfig) -> None:
        """GET the Space endpoint and log whatever it says."""
        import requests  # noqa: PLC0415 - optional extra, lazy on purpose

        response = requests.get(config.url, headers=config.headers, timeout=REQUEST_TIMEOUT)
        LOGS.info(response.json())

    def ping_once(self) -> bool:
        """One request. Returns success; never raises."""
        try:
            self._ping(self.config)
        except Exception as exc:
            LOGS.warning("keep_alive ping failed: %s", type(exc).__name__)
            return False
        return True

    # -- pacing

    def next_delay(self) -> int:
        return random.randint(self.config.min_interval, self.config.max_interval)

    def _interruptible_sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    # -- loop

    def run(self) -> None:
        """Ping, sleep, repeat until stopped. Blocks the calling thread."""
        if not self.config.enabled:
            LOGS.info(
                "keep_alive: disabled (SPACE_URL/HF_TOKEN unset or KEEP_ALIVE=0)  -  pinger skipped."
            )
            return
        while not self._stop.is_set():
            self.ping_once()
            self._sleeper(self.next_delay())

    def start(self) -> threading.Thread:
        """Run :meth:`run` on a daemon thread and hand back the thread."""
        if self._thread is not None:
            return self._thread
        thread = threading.Thread(target=self.run, daemon=True, name=THREAD_NAME)
        self._thread = thread
        thread.start()
        return thread

    def stop(self, timeout: float | None = 5.0) -> bool:
        self._stop.set()
        thread = self._thread
        if thread is None:
            return False
        thread.join(timeout)
        return not thread.is_alive()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def __enter__(self) -> "KeepAlivePinger":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    def __repr__(self) -> str:
        state = "running" if self.running else "idle"
        return f"<KeepAlivePinger enabled={self.config.enabled} {state}>"


# -- module surface -------------------------------------------------------


def keep_alive(config: KeepAliveConfig) -> None:
    KeepAlivePinger(config).run()


def start_keepalive_thread(config: KeepAliveConfig | None = None) -> threading.Thread:
    return KeepAlivePinger(config or KeepAliveConfig.from_env()).start()
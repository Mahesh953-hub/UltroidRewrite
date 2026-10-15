# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""uvicorn runners for the setup server.

Two classes with different jobs and different vocabularies:

* :class:`PortProbe` answers one question - can I bind this address right
  now? It is stateless apart from its configuration, and it deliberately
  probes by binding and releasing (the only portable way).
* :class:`ThreadedServer` owns a uvicorn ``Server`` plus the daemon thread
  that runs it, and exposes a real lifecycle (``start`` / ``stop`` /
  ``join`` / :meth:`ThreadedServer.__enter__`).

``uvicorn`` resolves at call time inside :meth:`ThreadedServer.build_server`,
so importing this module (and the ``pyUltroid.webserver`` package) never
pulls it in - a minimal install can still boot the bot.
"""

from __future__ import annotations

import socket
import threading

import uvicorn
from types import TracebackType
from typing import Any, Final

THREAD_NAME: Final[str] = "pyultroid-webserver"

LOG_LEVEL: Final[str] = "info"


class PortProbe:
    """Best-effort "is this address free?" arbiter.

    Racy by construction - another process can win the race between the
    probe returning and the server starting. That is acceptable here because
    the only caller (the bootstrap gate) re-checks and degrades to waiting.
    """

    __slots__ = ("family", "sock_type", "reuse_address")

    def __init__(
        self,
        family: int = socket.AF_INET,
        sock_type: int = socket.SOCK_STREAM,
        reuse_address: bool = True,
    ) -> None:
        self.family = family
        self.sock_type = sock_type
        self.reuse_address = reuse_address

    def bindable(self, host: str, port: int) -> bool:
        """Bind and immediately release; ``False`` when the address is taken."""
        sock = socket.socket(self.family, self.sock_type)
        if self.reuse_address:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
        finally:
            sock.close()
        return True


class ThreadedServer:
    """A uvicorn server running on a daemon thread, with a real lifecycle."""

    __slots__ = ("server", "thread", "host", "port")

    def __init__(self, app: Any, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.server = self.build_server(app, host, port)
        self.thread = threading.Thread(
            target=self.server.run, daemon=True, name=THREAD_NAME
        )

    # -- construction

    @staticmethod
    def build_server(app: Any, host: str, port: int) -> Any:
        """The uvicorn ``Server`` for *app*, without starting anything."""
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            log_level=LOG_LEVEL,
            access_log=False,
        )
        return uvicorn.Server(config)

    # -- lifecycle

    def start(self) -> "ThreadedServer":
        self.thread.start()
        return self

    def stop(self, timeout: float | None = None) -> bool:
        """Ask uvicorn to wind down, optionally waiting for the thread.

        uvicorn's ``should_exit`` is cooperative, so the caller that is about
        to ``exec`` a fresh interpreter must not assume the socket is free the
        instant this returns. Pass ``timeout`` to actually wait.
        """
        self.server.should_exit = True
        if timeout is None:
            return False
        self.join(timeout)
        return not self.thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)

    def is_running(self) -> bool:
        return self.thread.is_alive()

    def __enter__(self) -> "ThreadedServer":
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    def __repr__(self) -> str:
        state = "running" if self.thread.is_alive() else "stopped"
        return f"<ThreadedServer {self.host}:{self.port} {state}>"


# -- module surface -------------------------------------------------------


def bind_available(host: str, port: int) -> bool:
    return PortProbe().bindable(host, port)


def make_server(app, host: str, port: int) -> Any:
    return ThreadedServer.build_server(app, host, port)


def serve_forever(app, host: str, port: int) -> None:
    """Run uvicorn on the calling thread (installs signal handlers)."""
    make_server(app, host, port).run()
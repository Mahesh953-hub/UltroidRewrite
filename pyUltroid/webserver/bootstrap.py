# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""The boot gate: decide whether to run the bot or serve web setup.

Two classes, split by *what they know about*:

* :class:`ConfigGate` knows about credentials and the environment. It asks
  one question - is the bot configured? - and it answers it out of the
  ``.env`` file plus the real environment.
* :class:`SetupBootstrap` knows about *processes*: the port, the PID marker,
  the blocking wait, the re-exec. Every one of its methods that touches the
  filesystem or the process table is a method on the object, so the whole
  flow can be driven from a test by handing it a different marker path.

The free functions at the bottom are the historical entry points
(``pyUltroid/__init__.py`` imports ``ensure_config_or_setup``) and are kept
verbatim in behaviour. They deliberately delegate to a module-level
singleton so a monkeypatched ``marker_path`` keeps working.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Final

from .envfile import REPO_ROOT, EnvFile

LOGS: logging.Logger = logging.getLogger(__name__)
if not LOGS.handlers:
    LOGS.addHandler(logging.StreamHandler())
    LOGS.setLevel(logging.INFO)
    LOGS.propagate = False

REQUIRED_KEYS: Final[tuple[str, ...]] = ("API_ID", "API_HASH", "SESSION")

DEFAULT_PORT: Final[int] = 7860
DEFAULT_BIND: Final[str] = "0.0.0.0"
DEFAULT_WAIT_TIMEOUT: Final[float] = 7200.0
WAIT_POLL_SECONDS: Final[float] = 2.0

MARKER_NAME: Final[str] = ".setup-waiting"
BANNER_WIDTH: Final[int] = 64


class ConfigGate:
    """Answers "may the bot boot?" from the env file plus the environment.

    The argv short-circuit is part of the contract: ``python -m pyUltroid
    <api_id> <api_hash> <session>`` is how the container passes credentials
    directly, and in that case there is no reason to interrupt the user with
    a setup server.
    """

    __slots__ = ("env_path", "argv")

    def __init__(
        self,
        env_path: str | Path | None = None,
        argv: list[str] | None = None,
    ) -> None:
        self.env_path = env_path
        self.argv = argv

    @property
    def env(self) -> EnvFile:
        return EnvFile(self.env_path)

    def argv_supplied(self) -> bool:
        return len(self._argv()) > 3

    def complete(self) -> bool:
        """True when API_ID/API_HASH/SESSION are all usable."""
        if self.argv_supplied():
            return True
        return not self.env.missing()

    def export(self) -> int:
        """Seed ``os.environ`` from the file; real values always win."""
        return self.env.export()

    def _argv(self) -> list[str]:
        return sys.argv if self.argv is None else self.argv

    def __repr__(self) -> str:
        return f"<ConfigGate {self.env.path} complete={self.complete()}>"


class SetupBootstrap:
    """Owns the setup-server process lifecycle."""

    __slots__ = ("env_path",)

    def __init__(self, env_path: str | Path | None = None) -> None:
        self.env_path = env_path

    # -- collaborators (resolved late so a monkeypatch is honoured)

    @property
    def gate(self) -> ConfigGate:
        return ConfigGate(self.env_path)

    def env(self) -> EnvFile:
        return EnvFile(self.env_path)

    # -- filesystem

    def marker_file(self) -> Path:
        """The PID marker path.

        Resolved through the module global on purpose: tests (and any future
        override) patch ``bootstrap.marker_path`` and must be able to move
        this file without touching the object.
        """
        return globals()["marker_path"]()

    def waiter_active(self, marker: Path | None = None) -> bool:
        """True when a ``python -m pyUltroid`` process is parked on setup."""
        target = marker if marker is not None else self.marker_file()
        try:
            pid = int(target.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    # -- environment

    def host_port(self) -> tuple[str, int]:
        """The bind address, from ``SETUP_BIND`` / ``PORT`` with safe defaults."""
        host = os.environ.get("SETUP_BIND", DEFAULT_BIND)
        try:
            port = int(os.environ.get("PORT", str(DEFAULT_PORT)))
        except ValueError:
            port = DEFAULT_PORT
        return host, port

    def token(self, mint: Callable[[], str]) -> str:
        return os.environ.get("SETUP_TOKEN") or mint()

    # -- output

    def banner(
        self,
        host: str,
        port: int,
        token: str,
        log: Callable[[str], Any] | None = None,
    ) -> None:
        """Print the one-time-token URL the user has to open."""
        display = "127.0.0.1" if host == DEFAULT_BIND else host
        emit = log if log is not None else LOGS.info
        rule = "=" * BANNER_WIDTH
        for line in (
            "",
            rule,
            "  SarahUB setup: no Telegram config found  -  starting web setup.",
            f"  Open:  http://{display}:{port}/?token={token}",
            "  The one-time token above gates every setup API call.",
            rule,
            "",
        ):
            emit(line)

    # -- process control

    def spawn_bot(self, spawn: Callable[..., Any] | None = None) -> bool:
        """Start ``python -m pyUltroid`` as a child. Returns whether we did."""
        if os.environ.get("SETUP_NO_SPAWN"):
            LOGS.info("setup: SETUP_NO_SPAWN set  -  not spawning the bot.")
            return False
        if self.waiter_active():
            LOGS.info(
                "setup: a pyUltroid bootstrap waiter will boot the bot  -  not spawning."
            )
            return False
        LOGS.info("setup: starting the bot (python -m pyUltroid)…")
        launch = spawn if spawn is not None else subprocess.Popen
        launch(
            [sys.executable, "-m", "pyUltroid"],
            cwd=str(REPO_ROOT),
            stdout=None,
            stderr=None,
        )
        return True

    def reexec(self, argv: list[str] | None = None) -> None:
        """Replace this process with a fresh ``python -m pyUltroid``."""
        args = argv if argv is not None else [sys.executable, sys.executable, "-m", "pyUltroid"]
        os.execl(*args)

    # -- the two flows

    def ensure(self) -> None:
        """Return immediately when configured; otherwise serve setup.

        Returns only when the bot is configured (or web setup is
        unavailable), because a completed setup ends in :meth:`reexec`.
        """
        gate = self.gate
        if gate.complete():
            return
        try:
            from .app import create_app
            from .runner import ThreadedServer
            from .runner import PortProbe
            from .security import AUTHORITY
        except Exception as exc:  # fastapi/uvicorn unimportable  -  keep legacy path
            LOGS.warning("web setup unavailable (%r); continuing legacy bootstrap.", exc)
            return

        host, port = self.host_port()
        if not PortProbe().bindable(host, port):
            self.wait_for_config(host, port)
            return

        token = self.token(AUTHORITY.mint)
        done = threading.Event()
        app = create_app(
            standalone=False,
            token=token,
            env_path=self.env_path,
            on_complete=done.set,
        )
        server = ThreadedServer(app, host, port).start()
        self.banner(host, port, token)
        done.wait()
        server.stop()
        gate.export()
        LOGS.info("Setup complete  -  restarting into pyUltroid…")
        self.reexec()

    def wait_for_config(self, host: str, port: int) -> None:
        """Another process owns the setup port: wait for it to finish setup.

        Publishes our PID in the marker while waiting, so a concurrently
        starting ``server.py`` knows not to spawn a second bot. Raises
        ``SystemExit(1)`` when the deadline passes.
        """
        timeout = _float_env("SETUP_WAIT_TIMEOUT", DEFAULT_WAIT_TIMEOUT)
        marker = self.marker_file()
        try:
            marker.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass
        try:
            LOGS.info(
                "setup: %s:%s is served by another process  -  waiting for web setup to complete there…",
                host,
                port,
            )
            gate = self.gate
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if gate.complete():
                    LOGS.info("setup: config detected  -  continuing normal bootstrap.")
                    gate.export()
                    return
                time.sleep(WAIT_POLL_SECONDS)
            LOGS.warning("setup: timed out waiting for web setup to complete.")
            raise SystemExit(1)
        finally:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                pass

    def __repr__(self) -> str:
        return f"<SetupBootstrap {self.env().path}>"


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


# -- the shared singleton + module surface -------------------------------
#
# Named BOOTSTRAP, not ``bootstrap``: a module-level ``bootstrap`` attribute
# would shadow this very submodule in ``pyUltroid.webserver`` and break
# ``from pyUltroid.webserver import bootstrap``.

BOOTSTRAP: Final[SetupBootstrap] = SetupBootstrap()


def marker_path() -> Path:
    return REPO_ROOT / MARKER_NAME


def export_env_file(env_path=None) -> None:
    SetupBootstrap(env_path).gate.export()


def config_complete(env_path=None) -> bool:
    return ConfigGate(env_path).complete()


def server_host_port() -> tuple[str, int]:
    return BOOTSTRAP.host_port()


def print_setup_url(host: str, port: int, token: str, log=None) -> None:
    BOOTSTRAP.banner(host, port, token, log)


def ensure_config_or_setup(env_path=None) -> None:
    SetupBootstrap(env_path).ensure()


def wait_for_config(host: str, port: int, env_path=None) -> None:
    SetupBootstrap(env_path).wait_for_config(host, port)


def bootstrap_waiter_active(marker=None) -> bool:
    return BOOTSTRAP.waiter_active(marker)


def spawn_bot_after_setup() -> None:
    BOOTSTRAP.spawn_bot()
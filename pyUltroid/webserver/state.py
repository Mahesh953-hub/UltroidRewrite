# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""Shared mutable state for the setup server.

:class:`SetupState` is the one object every setup route mutates: credentials,
the login manager, and the one-shot completion latch.

Two deliberate changes from the previous shape:

* ``telethon`` is **not** imported here. It used to come in transitively via
  ``from .login import LoginManager`` even though this module only stores a
  manager reference, which made ``import pyUltroid.webserver.state`` fail
  outright on an install without Telethon. The annotation is now a string
  and the manager is built through an injectable factory.
* :meth:`SetupState.retarget` disposes the manager it replaces. Re-posting
  the API keys mid-login used to leave the previous manager's connected
  Telethon client behind forever.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .envfile import EnvFile

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from .login import LoginManager

COMPLETION_DELAY_SECONDS: float = 0.5


def _build_login_manager(
    api_id: Any, api_hash: Any, client_factory: Callable[[], Any] | None = None
) -> "LoginManager":
    """The production manager builder; the only place ``telethon`` arrives."""
    from .login import LoginManager

    return LoginManager(api_id, api_hash, client_factory=client_factory)


@dataclass
class SetupState:
    """Mutable state shared by every setup route."""

    token: str
    env_path: Path
    standalone: bool
    on_complete: Callable[[], None] | None
    client_factory: Callable[[], Any] | None = None
    api_id: int | None = None
    api_hash: str | None = None
    bot_token: str | None = None
    log_channel: int | None = None
    session_string: str | None = None  # server-side only, never sent over HTTP
    login_manager: "LoginManager | None" = field(default=None)
    completed: bool = False
    manager_factory: Callable[..., "LoginManager"] = field(
        default=_build_login_manager
    )

    # -- construction

    @classmethod
    def create(
        cls,
        *,
        token: str,
        env_path: str | Path | None = None,
        standalone: bool,
        on_complete: Callable[[], None] | None = None,
        client_factory: Callable[[], Any] | None = None,
        manager_factory: Callable[..., "LoginManager"] | None = None,
    ) -> "SetupState":
        """The factory the routes used to call as ``make_state``."""
        return cls(
            token=token,
            env_path=Path(env_path) if env_path else EnvFile.default_path(),
            standalone=standalone,
            on_complete=on_complete,
            client_factory=client_factory,
            manager_factory=manager_factory or _build_login_manager,
        )

    # -- credentials

    @property
    def has_api_keys(self) -> bool:
        return bool(self.api_id and self.api_hash)

    @property
    def has_session(self) -> bool:
        return bool(self.session_string)

    def adopt_env_keys(self, env: dict[str, str] | None = None) -> bool:
        """Fill in API keys from the ``.env`` file if they are not set yet."""
        if self.has_api_keys:
            return False
        env = self.env.read() if env is None else env
        raw_id = env.get("API_ID")
        raw_hash = env.get("API_HASH")
        if not (raw_id and raw_hash):
            return False
        try:
            self.api_id = int(raw_id)
        except ValueError:
            return False
        self.api_hash = raw_hash
        return True

    @property
    def env(self) -> EnvFile:
        return EnvFile(self.env_path)

    def store_session(self, session_string: str) -> None:
        """Keep the session in memory only; persisting is the route's call."""
        self.session_string = session_string

    # -- login manager

    def manager(self) -> "LoginManager":
        """The login manager, rebuilt when the API keys change."""
        current = self.login_manager
        if (
            current is not None
            and current.api_id == self.api_id
            and current.api_hash == self.api_hash
        ):
            return current
        self.login_manager = self.retarget(self.api_id, self.api_hash)
        return self.login_manager

    def retarget(self, api_id: Any, api_hash: Any) -> "LoginManager":
        """Build a new manager for *api_id* / *api_hash*.

        The previous manager's sessions are closed first: swapping the
        credentials out from under a live login otherwise strands its
        connected client.
        """
        previous, self.login_manager = self.login_manager, None
        if previous is not None:
            self._close(previous)
        return self.manager_factory(api_id, api_hash, self.client_factory)

    @staticmethod
    def _close(manager: "LoginManager") -> None:
        """Best-effort async teardown of a superseded manager.

        Only works when there is a running loop to host the task, which is
        the normal case: the manager is swapped from inside a route
        handler. Outside a loop the old client is simply left to its own
        session lifetime rather than raising.
        """
        dispose = getattr(manager, "dispose", None)
        if dispose is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if loop.is_running():
            loop.create_task(dispose())

    def manager_or_none(self) -> "LoginManager | None":
        """The manager, or ``None`` when no usable API keys exist yet."""
        self.adopt_env_keys()
        if not self.has_api_keys:
            return None
        return self.manager()

    # -- completion

    def trigger_completion(self) -> bool:
        """Fire the completion callback exactly once.

        Returns whether *this* call was the one that fired it. The callback
        runs on a short timer so the HTTP response reaches the browser
        before any re-exec or spawn.
        """
        if self.completed:
            return False
        self.completed = True
        callback = self.on_complete
        if callback is None:
            return False
        timer = threading.Timer(COMPLETION_DELAY_SECONDS, callback)
        timer.daemon = True
        timer.start()
        return True


# -- module surface -------------------------------------------------------


def make_state(
    *,
    token: str,
    env_path=None,
    standalone: bool,
    on_complete=None,
    client_factory=None,
) -> SetupState:
    return SetupState.create(
        token=token,
        env_path=env_path,
        standalone=standalone,
        on_complete=on_complete,
        client_factory=client_factory,
    )
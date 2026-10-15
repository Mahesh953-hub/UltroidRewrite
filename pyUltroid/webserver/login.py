# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""Phone-OTP and QR login flows.

The only standalone machinery in this module is the *session factory*:
building a Telethon client is the one step that needs to be replaceable
(tests inject a fake) and lazy (importing ``telethon`` must not be forced
on anyone who merely imports the package). That is why
:class:`SessionFactory` exists as its own class with its own
``__call__``, instead of a module-level ``_default_client_factory``.

:class:`LoginManager` then owns *nothing but* the in-flight sessions: it
creates clients through the factory, tracks them by id, expires them, and
guarantees a client is disconnected exactly once.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Final

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

SESSION_TTL_SECONDS: Final[float] = 600

KIND_PHONE: Final[str] = "phone"
KIND_QR: Final[str] = "qr"


@dataclass
class LoginOutcome:
    """What a login step produced. ``session_string`` never leaves the server."""

    ok: bool
    session_string: str | None = None
    needs_password: bool = False
    error: str | None = None


@dataclass
class LoginSession:
    """One in-flight login attempt and the client it owns."""

    id: str
    kind: str
    client: Any
    phone: str | None = None
    phone_code_hash: str | None = None
    qr: Any | None = None
    needs_2fa: bool = False
    loop: asyncio.AbstractEventLoop | None = None
    created: float = field(default_factory=time.monotonic)

    def age(self, now: float | None = None) -> float:
        return (now if now is not None else time.monotonic()) - self.created


class LoginSessionUnknown(KeyError):
    """No live session with that id (unknown, expired, or wrong kind)."""


class SessionFactory:
    """Builds the Telethon client a login attempt runs on.

    Kept separate from :class:`LoginManager` so the manager has no knowledge
    of ``TelegramClient``/``StringSession`` and the whole manager can be
    exercised against a fake client with no Telethon involved.
    """

    __slots__ = ("_custom",)

    def __init__(self, custom: Callable[[], Any] | None = None) -> None:
        self._custom = custom

    @property
    def custom(self) -> bool:
        return self._custom is not None

    def __call__(self) -> Any:
        if self._custom is not None:
            return self._custom()
        return TelegramClient(StringSession(), 0, "")

    def for_credentials(self, api_id: int, api_hash: str) -> "SessionFactory":
        """A factory bound to *api_id* / *api_hash*.

        A custom factory ignores the credentials entirely - that is the
        contract tests rely on.
        """
        if self._custom is not None:
            return self
        return SessionFactory(lambda: TelegramClient(StringSession(), api_id, api_hash))

    def __repr__(self) -> str:
        kind = "custom" if self.custom else "telethon"
        return f"<SessionFactory {kind}>"


class LoginManager:
    """The registry of in-flight login attempts for one credential pair."""

    __slots__ = ("api_id", "api_hash", "_factory", "_ttl", "_sessions")

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        *,
        client_factory: Callable[[], Any] | None = None,
        factory: SessionFactory | None = None,
        ttl: float = SESSION_TTL_SECONDS,
    ) -> None:
        self.api_id = api_id
        self.api_hash = api_hash
        self._factory = factory or SessionFactory(client_factory).for_credentials(
            api_id, api_hash
        )
        self._ttl = ttl
        self._sessions: dict[str, LoginSession] = {}

    # -- introspection

    def __len__(self) -> int:
        return len(self._sessions)

    def __contains__(self, login_id: object) -> bool:
        return login_id in self._sessions

    def __repr__(self) -> str:
        return f"<LoginManager api_id={self.api_id} live={len(self._sessions)}>"

    def peek(self, login_id: str) -> bool:
        """True when *login_id* is live. Never raises."""
        try:
            self._get(login_id)
        except LoginSessionUnknown:
            return False
        return True

    # -- client lifecycle

    def _new_client(self) -> Any:
        return self._factory()

    @staticmethod
    async def _safe_disconnect(client: Any) -> None:
        try:
            await client.disconnect()
        except Exception:
            pass

    def _discard_soon(self, session: LoginSession) -> None:
        """Pop the session and disconnect on *its own* loop (thread-safe)."""
        self._sessions.pop(session.id, None)
        loop = session.loop
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(
                lambda: loop.create_task(self._safe_disconnect(session.client))
            )

    async def _drop(self, session: LoginSession) -> None:
        """Pop the session and disconnect inside the caller's async context."""
        self._sessions.pop(session.id, None)
        await self._safe_disconnect(session.client)

    async def _finish(self, session: LoginSession) -> str:
        """Extract the session string, then dispose of the client."""
        self._sessions.pop(session.id, None)
        try:
            return session.client.session.save()
        finally:
            await self._safe_disconnect(session.client)

    def _sweep(self) -> int:
        """Drop every expired session. Returns how many were dropped."""
        now = time.monotonic()
        stale = [s for s in list(self._sessions.values()) if s.age(now) > self._ttl]
        for session in stale:
            self._discard_soon(session)
        return len(stale)

    async def dispose(self) -> int:
        """Disconnect every live client. Call on shutdown / key rotation."""
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            await self._safe_disconnect(session.client)
        return len(sessions)

    # -- registry

    def _get(self, login_id: str, kind: str | None = None) -> LoginSession:
        session = self._sessions.get(login_id)
        if session is None:
            raise LoginSessionUnknown(login_id)
        if session.age() > self._ttl:
            self._discard_soon(session)
            raise LoginSessionUnknown(login_id)
        if kind is not None and session.kind != kind:
            raise LoginSessionUnknown(login_id)
        return session

    def _register(self, session: LoginSession) -> str:
        self._sessions[session.id] = session
        return session.id

    def _new_id(self) -> str:
        return uuid.uuid4().hex

    # -- phone OTP flow

    async def start_phone(self, phone: str) -> dict:
        """Send the login code. Returns {login_id, phone_code_hash, code_type}."""
        self._sweep()
        client = self._new_client()
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
        except Exception:
            # Never leak a connected client for a login that never started.
            await self._safe_disconnect(client)
            raise
        login_id = self._register(
            LoginSession(
                id=self._new_id(),
                kind=KIND_PHONE,
                client=client,
                phone=phone,
                phone_code_hash=getattr(sent, "phone_code_hash", None),
                loop=asyncio.get_running_loop(),
            )
        )
        return {
            "login_id": login_id,
            "phone_code_hash": getattr(sent, "phone_code_hash", None),
            "code_type": type(getattr(sent, "type", None)).__name__,
        }

    async def submit_code(
        self, login_id: str, code: str, password: str | None = None
    ) -> LoginOutcome:
        session = self._get(login_id, KIND_PHONE)
        try:
            if session.needs_2fa:
                if password is None:
                    return LoginOutcome(ok=False, needs_password=True)
                await session.client.sign_in(password=password)
            else:
                try:
                    await session.client.sign_in(
                        session.phone,
                        code,
                        phone_code_hash=session.phone_code_hash,
                    )
                except errors.SessionPasswordNeededError:
                    session.needs_2fa = True
                    if password is None:
                        return LoginOutcome(ok=False, needs_password=True)
                    await session.client.sign_in(password=password)
        except (errors.PhoneCodeExpiredError, errors.AuthRestartError):
            await self._drop(session)
            raise

        return LoginOutcome(ok=True, session_string=await self._finish(session))

    async def submit_password(self, login_id: str, password: str) -> LoginOutcome:
        """2FA step, valid for both phone and QR logins."""
        session = self._get(login_id)
        try:
            await session.client.sign_in(password=password)
        except errors.PasswordHashInvalidError:
            raise  # retryable  -  keep the session alive
        except Exception:
            await self._drop(session)
            raise
        return LoginOutcome(ok=True, session_string=await self._finish(session))

    # -- QR flow

    async def start_qr(self) -> dict:
        """Begin QR login. Returns {login_id, url, expires}."""
        self._sweep()
        client = self._new_client()
        await client.connect()
        try:
            qr = await client.qr_login()
        except Exception:
            await self._safe_disconnect(client)
            raise
        login_id = self._register(
            LoginSession(
                id=self._new_id(),
                kind=KIND_QR,
                client=client,
                qr=qr,
                loop=asyncio.get_running_loop(),
            )
        )
        return {"login_id": login_id, "url": qr.url, "expires": qr.expires.isoformat()}

    def qr_url(self, login_id: str) -> str:
        return self._get(login_id, KIND_QR).qr.url

    async def wait_qr(self, login_id: str, timeout: float | None = None) -> dict:
        """Await one QR expiry window.

        Returns one of:
          ``{"status": "success", "session_string": ...}``  (the session string
          is consumed by the route layer, never sent over HTTP)
          ``{"status": "expired", "url": ..., "expires": ...}``  (auto-recreated)
          ``{"status": "needs_password"}``
        """
        session = self._get(login_id, KIND_QR)
        try:
            await session.qr.wait(timeout=timeout)
        except TimeoutError:
            await session.qr.recreate()
            return {
                "status": "expired",
                "url": session.qr.url,
                "expires": session.qr.expires.isoformat(),
            }
        except errors.SessionPasswordNeededError:
            session.needs_2fa = True
            return {"status": "needs_password"}
        except Exception:
            await self._drop(session)
            raise

        return {
            "status": "success",
            "session_string": await self._finish(session),
        }


# -- module surface -------------------------------------------------------


def _default_client_factory(api_id: int, api_hash: str) -> Any:
    return TelegramClient(StringSession(), api_id, api_hash)
# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""FastAPI application factory and routes for the setup server."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
from pathlib import Path
from types import TracebackType
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.datastructures import Headers, QueryParams
from telethon import errors as tl_errors

from .bootstrap import SetupBootstrap
from .envfile import REPO_ROOT, EnvFile
from .login import LoginManager, LoginSessionUnknown
from .logs import LogStreamHub
from .security import AUTHORITY, RateLimiter, TokenAuthority
from .state import SetupState

LOGS: logging.Logger = logging.getLogger("pyUltroid.webserver")

STATIC_DIR: Final[Path] = REPO_ROOT / "resources" / "web"

BOT_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^\d+:[A-Za-z0-9_-]{30,}$")
MAX_QR_CYCLES: Final[int] = 8  # expiry/recreate cycles before giving up
MAX_REQUEST_BODY_BYTES: Final[int] = 1024 * 1024

API_HASH_MIN_LENGTH: Final[int] = 8
LOG_KEEPALIVE_SECONDS: Final[float] = 15.0
OTP_ATTEMPTS: Final[int] = 5
PHONE_ATTEMPTS: Final[int] = 3
ATTEMPT_WINDOW: Final[float] = 60.0


# -- payloads ------------------------------------------------------------


class KeysPayload(BaseModel):
    api_id: int
    api_hash: str
    bot_token: str | None = None
    log_channel: int | None = None


class PhonePayload(BaseModel):
    phone: str


class OtpPayload(BaseModel):
    login_id: str
    code: str
    password: str | None = None


class PasswordPayload(BaseModel):
    password: str


# -- SSE -----------------------------------------------------------------


class SseFrame:
    """One ``text/event-stream`` frame.

    A tiny immutable value with an explicit ``render`` rather than a bare
    f-string, because the log and QR routes both need the same framing and
    both need dict-vs-string handling done once.
    """

    __slots__ = ("event", "data")

    def __init__(self, event: str, data: dict[str, Any] | str) -> None:
        self.event = event
        self.data = data

    def render(self) -> str:
        payload = json.dumps(self.data) if isinstance(self.data, dict) else self.data
        return f"event: {self.event}\ndata: {payload}\n\n"

    def __str__(self) -> str:
        return self.render()

    def __repr__(self) -> str:
        return f"<SseFrame {self.event}>"


class TokenGateMiddleware:
    """Pure-ASGI token gate for the whole ``/api/*`` surface.

    Deliberately not BaseHTTPMiddleware: it runs before body parsing
    (unauthenticated callers always get 401, never 422) and it streams
    SSE responses through untouched  -  BaseHTTPMiddleware's response
    wrapping is a known flake source with streaming under TestClient.
    Routes keep their own token checks as defense in depth.
    """

    __slots__ = ("app", "token", "authority")

    def __init__(
        self,
        app: Any,
        token: str,
        authority: TokenAuthority | None = None,
    ) -> None:
        self.app = app
        self.token = token
        self.authority = authority if authority is not None else AUTHORITY

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            headers = Headers(scope=scope)
            provided = headers.get("X-Setup-Token")
            if not provided:
                provided = QueryParams(scope.get("query_string", b"").decode()).get(
                    "token"
                )
            if not self.authority.verify(provided, self.token):
                await unauthorized()(scope, receive, send)
                return
            declared_length = headers.get("content-length")
            if declared_length:
                try:
                    if int(declared_length) > MAX_REQUEST_BODY_BYTES:
                        await JSONResponse(
                            {"detail": "request body too large"}, status_code=413
                        )(scope, receive, send)
                        return
                except ValueError:
                    pass

            body_messages = []
            body_size = 0
            while True:
                message = await receive()
                body_messages.append(message)
                if message["type"] != "http.request":
                    break
                body_size += len(message.get("body", b""))
                if body_size > MAX_REQUEST_BODY_BYTES:
                    await JSONResponse(
                        {"detail": "request body too large"}, status_code=413
                    )(scope, receive, send)
                    return
                if not message.get("more_body", False):
                    break

            replay_index = 0

            async def replay_receive():
                nonlocal replay_index
                if replay_index < len(body_messages):
                    message = body_messages[replay_index]
                    replay_index += 1
                    return message
                return await receive()

            await self.app(scope, replay_receive, send)
            return
        await self.app(scope, receive, send)


# -- error mapping -------------------------------------------------------


class LoginErrorMapper:
    """Telethon login exception -> HTTP status + safe detail.

    The exception text goes through the redactor before logging and
    ``exc_info`` is deliberately never attached, so a stray provider
    message can never leak a phone number or password into the log stream.
    """

    __slots__ = ("redactor", "_table")

    def __init__(self, redactor: Any | None = None) -> None:
        self.redactor = redactor if redactor is not None else AUTHORITY.redactor
        self._table: tuple[tuple[type[BaseException], int, str], ...] = (
            (tl_errors.PhoneNumberInvalidError, 400, "invalid phone number"),
            (tl_errors.PhoneCodeInvalidError, 400, "invalid login code"),
            (
                tl_errors.PhoneCodeExpiredError,
                400,
                "login code expired  -  request a new one",
            ),
            (tl_errors.PasswordHashInvalidError, 400, "invalid 2FA password"),
            (
                tl_errors.ApiIdInvalidError,
                400,
                "API_ID/API_HASH combination is invalid",
            ),
        )

    def log(self, exc: Exception) -> None:
        LOGS.warning(
            "login failed: %s: %s", type(exc).__name__, self.redactor.redact(str(exc))
        )

    def describe(self, exc: Exception) -> tuple[int, str]:
        """The (status, detail) pair for *exc*."""
        for error_type, status, detail in self._table:
            if isinstance(exc, error_type):
                return status, detail
        if isinstance(exc, tl_errors.FloodWaitError):
            return 429, f"rate limited by Telegram  -  retry in {exc.seconds}s"
        return 502, f"login failed ({type(exc).__name__})"

    def response(self, exc: Exception) -> JSONResponse:
        self.log(exc)
        status, detail = self.describe(exc)
        return JSONResponse({"detail": detail}, status_code=status)


# -- helpers shared by the routes ----------------------------------------


def unauthorized() -> JSONResponse:
    """The one 401 body every gated route returns."""
    return JSONResponse({"detail": "invalid or missing setup token"}, status_code=401)


# -- the application -----------------------------------------------------


class SetupApplication:
    """The setup/status FastAPI app and its routes.

    ``standalone=True`` (server.py): completion spawns ``python -m
    pyUltroid``. ``standalone=False`` (the bootstrap gate): completion fires
    ``on_complete`` and the caller re-execs. ``client_factory`` exists for
    tests (fake Telethon).
    """

    __slots__ = (
        "state",
        "hub",
        "broker",
        "otp_limiter",
        "phone_limiter",
        "authority",
        "errors",
        "bootstrap",
        "log_keepalive_seconds",
        "static_dir",
    )

    def __init__(
        self,
        *,
        token: str | None = None,
        env_path: str | Path | None = None,
        standalone: bool = True,
        on_complete: Any = None,
        client_factory: Any = None,
        log_keepalive_seconds: float = LOG_KEEPALIVE_SECONDS,
        static_dir: Path | None = None,
    ) -> None:
        resolved = token or os.environ.get("SETUP_TOKEN") or AUTHORITY.mint()
        AUTHORITY.redactor.add(resolved)
        if standalone and on_complete is None:
            on_complete = SetupBootstrap(env_path).spawn_bot

        self.authority = AUTHORITY
        self.errors = LoginErrorMapper()
        self.bootstrap = SetupBootstrap(env_path)
        self.log_keepalive_seconds = log_keepalive_seconds
        self.static_dir = static_dir if static_dir is not None else STATIC_DIR
        self.state = SetupState.create(
            token=resolved,
            env_path=env_path,
            standalone=standalone,
            on_complete=on_complete,
            client_factory=client_factory,
        )
        self.hub = LogStreamHub()
        self.broker = self.hub.broker
        self.otp_limiter = RateLimiter(OTP_ATTEMPTS, ATTEMPT_WINDOW)
        self.phone_limiter = RateLimiter(PHONE_ATTEMPTS, ATTEMPT_WINDOW)

    # -- collaborators

    @property
    def env(self) -> EnvFile:
        return self.state.env

    @property
    def token(self) -> str:
        return self.state.token

    @property
    def manager(self) -> LoginManager | None:
        return self.state.login_manager

    def authorized(self, request: Request) -> bool:
        return self.authority.permits(request, self.state.token)

    def client_ip(self, request: Request) -> str:
        return request.client.host if request.client else "unknown"

    # -- side effects

    def store_session(self, session_string: str) -> None:
        """Keep the session in memory and persist it; never log its value."""
        self.state.store_session(session_string)
        self.authority.redactor.add(session_string)
        self.env.write({"SESSION": session_string})
        LOGS.info("SESSION saved to .env (value never leaves the server).")

    def save_credentials(
        self,
        api_id: int,
        api_hash: str,
        bot_token: str | None,
        log_channel: int | None,
    ) -> None:
        """Persist the API keys plus the optional bot token / log channel.

        The empty-string and zero sentinels matter: they tell the bootstrap
        gate "the user was asked and said no", which is different from
        "nobody has answered yet".
        """
        updates: dict[str, Any] = {"API_ID": api_id, "API_HASH": api_hash}
        if bot_token:
            updates["BOT_TOKEN"] = bot_token
        elif not (self.env.read().get("BOT_TOKEN") or os.environ.get("BOT_TOKEN")):
            updates["BOT_TOKEN"] = ""

        if log_channel is None and not (
            self.env.read().get("LOG_CHANNEL") or os.environ.get("LOG_CHANNEL")
        ):
            log_channel = 0
        if log_channel is not None:
            updates["LOG_CHANNEL"] = log_channel

        self.env.write(updates)
        self.state.api_id = api_id
        self.state.api_hash = api_hash
        self.state.bot_token = bot_token
        self.state.log_channel = log_channel
        self.authority.redactor.add(api_hash)
        self.authority.redactor.add(bot_token)
        LOGS.info("API credentials saved (api_id=%s).", api_id)

    # -- build

    def build(self) -> FastAPI:
        """Construct the FastAPI app and register every route."""
        app = FastAPI(
            title="Ultroid Setup",
            version="2.0.0",
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        app.state.setup = self.state
        # The hub stays attached for the lifetime of the app: that attachment
        # is what feeds the /api/logs stream. ``close()`` reverses it.
        self.hub.attach()
        app.state.log_broker = self.broker

        app.add_middleware(TokenGateMiddleware, token=self.state.token)
        if self.static_dir.is_dir():
            app.mount(
                "/static",
                StaticFiles(directory=str(self.static_dir)),
                name="static",
            )

        self._register_legacy(app)
        self._register_state(app)
        self._register_credentials(app)
        self._register_phone(app)
        self._register_qr(app)
        self._register_logs(app)
        self._register_completion(app)
        return app

    def close(self) -> bool:
        """Detach the log hub and drop any live login clients."""
        detached = self.hub.detach()
        manager = self.state.login_manager
        if manager is not None:
            dispose = getattr(manager, "dispose", None)
            if dispose is not None:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop is not None and loop.is_running():
                    loop.create_task(dispose())
        return detached

    def __enter__(self) -> "SetupApplication":
        self.build()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- routes: legacy + static

    @staticmethod
    def _register_legacy(app: FastAPI) -> None:
        @app.get("/status")
        def status() -> dict:
            """Unauthenticated on purpose: keep the exact legacy shape."""
            return {"message": "running"}

    def _index(self) -> Response | JSONResponse:
        index_file = self.static_dir / "index.html"
        if index_file.is_file():
            return FileResponse(str(index_file))
        return JSONResponse(
            {"detail": "setup UI assets missing (resources/web/index.html)"},
            status_code=404,
        )

    # -- routes: state

    def _register_state(self, app: FastAPI) -> None:
        index = self._index

        @app.get("/", response_model=None)
        def root() -> Response | JSONResponse:
            return index()

        @app.get("/api/state", response_model=None)
        def api_state(request: Request) -> dict | JSONResponse:
            if not self.authorized(request):
                return unauthorized()
            env = self.env.read()
            state = self.state
            return {
                "setup_required": not (state.has_session or env.get("SESSION")),
                "has_api_keys": state.has_api_keys
                or bool(env.get("API_ID") and env.get("API_HASH")),
                "has_bot_token": bool(state.bot_token or env.get("BOT_TOKEN")),
                "has_session": state.has_session or bool(env.get("SESSION")),
                "completed": state.completed,
                "standalone": state.standalone,
            }

    # -- routes: step 1, the API keys

    def _register_credentials(self, app: FastAPI) -> None:
        @app.post("/api/setup/keys")
        def setup_keys(payload: KeysPayload, request: Request):
            if not self.authorized(request):
                return unauthorized()
            api_hash = payload.api_hash.strip()
            invalid = self._reject_keys(payload, api_hash)
            if invalid is not None:
                return invalid
            self.save_credentials(
                payload.api_id,
                api_hash,
                (payload.bot_token or "").strip() or None,
                payload.log_channel,
            )
            return {"ok": True}

    def _reject_keys(self, payload: KeysPayload, api_hash: str) -> JSONResponse | None:
        """422 for each invalid field, in the order the UI reports them."""
        if payload.api_id <= 0:
            return JSONResponse(
                {"detail": "api_id must be a positive integer"}, status_code=422
            )
        if len(api_hash) < API_HASH_MIN_LENGTH or not api_hash.isalnum():
            return JSONResponse({"detail": "api_hash looks invalid"}, status_code=422)
        bot_token = (payload.bot_token or "").strip() or None
        if bot_token and not BOT_TOKEN_RE.match(bot_token):
            return JSONResponse(
                {"detail": "bot_token format is invalid"}, status_code=422
            )
        return None

    # -- routes: step 2a, the phone OTP

    def _register_phone(self, app: FastAPI) -> None:
        @app.post("/api/login/phone")
        async def login_phone(payload: PhonePayload, request: Request):
            if not self.authorized(request):
                return unauthorized()
            if not self.phone_limiter.allow(self.client_ip(request)):
                return self._too_many()
            manager = self.state.manager_or_none()
            if manager is None:
                return JSONResponse(
                    {"detail": "save API_ID/API_HASH first"}, status_code=400
                )
            phone = payload.phone.strip()
            # Phone numbers never appear in any log output.
            self.authority.redactor.add(phone)
            try:
                result = await manager.start_phone(phone)
            except Exception as exc:
                return self.errors.response(exc)
            LOGS.info("login code sent to phone ending …%s", phone[-4:])
            return {"login_id": result["login_id"], "code_type": result["code_type"]}

        @app.post("/api/login/otp")
        async def login_otp(payload: OtpPayload, request: Request):
            if not self.authorized(request):
                return unauthorized()
            if not self.otp_limiter.allow(self.client_ip(request)):
                return self._too_many()
            manager = self.state.login_manager
            if manager is None:
                return JSONResponse({"detail": "no login in progress"}, status_code=400)
            self.authority.redactor.add(payload.password)
            try:
                outcome = await manager.submit_code(
                    payload.login_id, payload.code.strip(), password=payload.password
                )
            except LoginSessionUnknown:
                return self._unknown_login()
            except Exception as exc:
                return self.errors.response(exc)
            if outcome.needs_password:
                return JSONResponse(
                    {"detail": "two-factor password required", "needs_password": True},
                    status_code=428,
                )
            self.store_session(outcome.session_string)
            return {"ok": True}

    # -- routes: step 2b, the QR login

    def _register_qr(self, app: FastAPI) -> None:
        @app.post("/api/login/qr")
        async def login_qr(request: Request):
            if not self.authorized(request):
                return unauthorized()
            manager = self.state.manager_or_none()
            if manager is None:
                return JSONResponse(
                    {"detail": "save API_ID/API_HASH first"}, status_code=400
                )
            try:
                result = await manager.start_qr()
            except Exception as exc:
                return self.errors.response(exc)
            LOGS.info("QR login started (expires %s).", result["expires"])
            return result

        @app.get("/api/login/qr/{login_id}/wait")
        async def login_qr_wait(login_id: str, request: Request):
            if not self.authorized(request):
                return unauthorized()
            if self.state.login_manager is None or not self.state.login_manager.peek(
                login_id
            ):
                return self._unknown_login()
            return StreamingResponse(
                self._qr_events(login_id, request), media_type="text/event-stream"
            )

        @app.post("/api/login/qr/{login_id}/password")
        async def login_qr_password(
            login_id: str, payload: PasswordPayload, request: Request
        ):
            if not self.authorized(request):
                return unauthorized()
            if not self.otp_limiter.allow(self.client_ip(request)):
                return self._too_many()
            manager = self.state.login_manager
            if manager is None or not manager.peek(login_id):
                return self._unknown_login()
            self.authority.redactor.add(payload.password)
            try:
                outcome = await manager.submit_password(login_id, payload.password)
            except LoginSessionUnknown:
                return self._unknown_login()
            except Exception as exc:
                return self.errors.response(exc)
            self.store_session(outcome.session_string)
            return {"ok": True}

        @app.get("/api/login/qr/{login_id}/image")
        def login_qr_image(login_id: str, request: Request):
            """PNG of the tg:// login URL.

            Only produced when the optional ``qrcode`` package is
            installed; the UI falls back to showing the URL as text.
            """
            if not self.authorized(request):
                return unauthorized()
            manager = self.state.login_manager
            if manager is None or not manager.peek(login_id):
                return self._unknown_login()
            try:
                import qrcode
            except ImportError:
                return JSONResponse(
                    {
                        "detail": "qrcode package not installed",
                        "url": manager.qr_url(login_id),
                    },
                    status_code=404,
                )
            image = qrcode.make(manager.qr_url(login_id))
            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            return Response(content=buffer.getvalue(), media_type="image/png")

    async def _qr_events(self, login_id: str, request: Request):
        """The QR wait stream: waiting, expired/recreated, 2FA, or success."""
        manager = self.state.login_manager
        yield SseFrame("waiting", {"login_id": login_id}).render()
        for _cycle in range(MAX_QR_CYCLES):
            if await request.is_disconnected():
                return
            try:
                result = await manager.wait_qr(login_id)
            except LoginSessionUnknown:
                yield SseFrame("error", {"detail": "login session expired"}).render()
                return
            except Exception as exc:
                LOGS.warning(
                    "QR wait failed: %s: %s",
                    type(exc).__name__,
                    self.authority.redactor.redact(str(exc)),
                )
                yield SseFrame("error", {"detail": type(exc).__name__}).render()
                return
            status_ = result["status"]
            if status_ == "expired":
                yield SseFrame(
                    "expired", {"url": result["url"], "expires": result["expires"]}
                ).render()
                continue
            if status_ == "needs_password":
                yield SseFrame("needs_password", {}).render()
                return
            self.store_session(result["session_string"])
            yield SseFrame("success", {}).render()
            return
        yield SseFrame(
            "error", {"detail": "too many QR expiries  -  start over"}
        ).render()

    # -- routes: live logs

    def _register_logs(self, app: FastAPI) -> None:
        @app.get("/api/logs")
        async def stream_logs(request: Request):
            if not self.authorized(request):
                return unauthorized()
            return StreamingResponse(
                self._log_events(request), media_type="text/event-stream"
            )

    async def _log_events(self, request: Request):
        """Replay the history, then stream live lines with keep-alives.

        The subscription is an explicit context manager so the unsubscribe
        happens on every exit path, including a client disconnect.
        """
        keepalive = self.log_keepalive_seconds
        async with self.broker.subscription() as queue_:
            for line in self.broker.history():
                yield SseFrame("log", line).render()
            yield ": ready\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    line = await asyncio.wait_for(queue_.get(), timeout=keepalive)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield SseFrame("log", line).render()

    # -- routes: completion handshake

    def _register_completion(self, app: FastAPI) -> None:
        @app.post("/api/setup/complete")
        def setup_complete(request: Request):
            if not self.authorized(request):
                return unauthorized()
            if not (self.state.has_session or self.env.read().get("SESSION")):
                return JSONResponse(
                    {"detail": "no SESSION yet  -  finish phone or QR login first"},
                    status_code=409,
                )
            self.state.trigger_completion()
            mode = "spawn" if self.state.standalone else "exec"
            LOGS.info("setup complete  -  bot restart handshake (%s).", mode)
            return {"ok": True, "restart": True, "mode": mode}

    # -- shared error responses

    @staticmethod
    def _too_many() -> JSONResponse:
        return JSONResponse(
            {"detail": "too many attempts  -  slow down"}, status_code=429
        )

    @staticmethod
    def _unknown_login() -> JSONResponse:
        return JSONResponse({"detail": "unknown or expired login_id"}, status_code=404)

    def __repr__(self) -> str:
        return f"<SetupApplication token={self.state.token[:4]}…>"


# -- module surface -------------------------------------------------------


def create_app(
    *,
    token: str | None = None,
    env_path=None,
    standalone: bool = True,
    on_complete=None,
    client_factory=None,
    log_keepalive_seconds: float = LOG_KEEPALIVE_SECONDS,
) -> FastAPI:
    """Build the setup/status app (the historical entry point)."""
    return SetupApplication(
        token=token,
        env_path=env_path,
        standalone=standalone,
        on_complete=on_complete,
        client_factory=client_factory,
        log_keepalive_seconds=log_keepalive_seconds,
    ).build()

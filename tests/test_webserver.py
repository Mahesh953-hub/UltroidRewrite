"""W2-A tests for pyUltroid/webserver — credential-free, fully mocked.

Every Telethon interaction goes through an injected ``client_factory``
returning a scripted fake client; no network, no real credentials, no
database. The env file is redirected to ``tmp_path`` and ``os.environ`` is
snapshotted/restored around every test so the setup routes' environ writes
cannot leak into the rest of the suite.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import sys
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from starlette.testclient import TestClient

from pyUltroid.webserver import bootstrap, envfile
from pyUltroid.webserver.app import (
    MAX_REQUEST_BODY_BYTES,
    TokenGateMiddleware,
    create_app,
)
from pyUltroid.webserver.keepalive import KeepAliveConfig
from pyUltroid.webserver.login import LoginManager, LoginSessionUnknown
from pyUltroid.webserver.logs import LogBroker, RedactionFilter, attach_broker
from pyUltroid.webserver.security import (
    RateLimiter,
    SecretRedactor,
    generate_setup_token,
    token_matches,
)

TOKEN = "test-setup-token"
AUTH = {"X-Setup-Token": TOKEN}
FAKE_SESSION_STRING = "1" + "A" * 352
API_HASH = "deadbeefcafe1234567890"
BOT_TOKEN = "123456:ABCdefGHIjklMNOpqrSTUvwxYZ1234567890"


# --------------------------------------------------------------------------
# fixtures / fakes
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_environ():
    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    before = list(root.handlers)
    level_before = root.level
    yield
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
    root.setLevel(level_before)


@pytest.fixture
def env_path(tmp_path):
    return tmp_path / ".env"


@pytest.fixture
def make_client(env_path):
    """Build a TestClient with a fresh app; extra kwargs go to create_app."""
    created = []

    def factory(client_factory=None, on_complete=None, **kwargs):
        app = create_app(
            token=TOKEN,
            env_path=env_path,
            on_complete=on_complete or MagicMock(),
            client_factory=client_factory,
            log_keepalive_seconds=0.2,
            **kwargs,
        )
        client = TestClient(app)
        created.append(client)
        return client

    yield factory
    for client in created:
        client.close()


class FakeTelethonClient:
    """Scripted stand-in for TelegramClient (phone OTP path).

    Mirrors real Telethon sign_in precedence: a presented code is attempted
    BEFORE any password fallback, the first presented code consumes it, and
    a consumed/invalid code raises PhoneCodeInvalidError even when a valid
    password accompanies it (real Telethon never reaches the password step
    in that case — which is why the manager must not resubmit consumed
    codes).
    """

    def __init__(
        self,
        *,
        two_factor=False,
        valid_code="12345",
        valid_password="correctpw",
        expire_code=False,
    ):
        self.two_factor = two_factor
        self.valid_code = valid_code
        self.valid_password = valid_password
        self.expire_code = expire_code
        self.code_consumed = False
        self.connected = False
        self.calls = []
        self.session = SimpleNamespace(save=lambda: FAKE_SESSION_STRING)

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def send_code_request(self, phone):
        from telethon import errors

        self.calls.append(("send_code_request", phone))
        if phone == "+bad":
            raise errors.PhoneNumberInvalidError(None)
        return SimpleNamespace(phone_code_hash="hash-123", type="SentCodeTypeApp")

    def _check_password(self, password):
        from telethon import errors

        if password != self.valid_password:
            raise errors.PasswordHashInvalidError(None)
        return SimpleNamespace(id=1)

    async def sign_in(
        self, phone=None, code=None, *, password=None, phone_code_hash=None, **kwargs
    ):
        from telethon import errors

        self.calls.append(("sign_in", phone, code, password, phone_code_hash))
        if code is not None:
            if self.expire_code:
                raise errors.PhoneCodeExpiredError(None)
            if self.code_consumed or code != self.valid_code:
                raise errors.PhoneCodeInvalidError(None)
            self.code_consumed = True
            if self.two_factor:
                if password is None:
                    raise errors.SessionPasswordNeededError(None)
                return self._check_password(password)
            return SimpleNamespace(id=1)
        if password is not None:
            return self._check_password(password)
        raise ValueError("sign_in called with neither code nor password")


class FakeQRLogin:
    """Scripted QRLogin (recreate/wait/expires/url)."""

    def __init__(self, wait_outcomes):
        self._waits = list(wait_outcomes)
        self.version = 0
        self.recreate_calls = 0

    @property
    def url(self):
        return f"tg://login?token=RkFLRXRva2Vu{self.version}"

    @property
    def expires(self):
        return datetime.now(timezone.utc) + timedelta(seconds=30)

    async def recreate(self):
        self.recreate_calls += 1
        self.version += 1

    async def wait(self, timeout=None):
        from telethon import errors

        outcome = self._waits.pop(0) if self._waits else "timeout"
        if outcome == "timeout":
            raise asyncio.TimeoutError()
        if outcome == "2fa":
            raise errors.SessionPasswordNeededError(None)
        if outcome == "boom":
            raise RuntimeError("connection lost")
        return SimpleNamespace(id=1)


class FakeQRTelethonClient(FakeTelethonClient):
    def __init__(self, wait_outcomes):
        super().__init__()
        self.qr = FakeQRLogin(wait_outcomes)

    async def qr_login(self):
        self.calls.append(("qr_login",))
        return self.qr


def _sse_events(lines):
    """Group raw SSE lines into (event, data) tuples."""
    events = []
    event = data = None
    for line in lines:
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data = line.split(":", 1)[1].strip()
        elif line == "" and event:
            events.append((event, data))
            event = data = None
    if event:  # flush a trailing event whose data line we stopped short of
        events.append((event, data))
    return events


# --------------------------------------------------------------------------
# legacy /status contract
# --------------------------------------------------------------------------


def test_status_exact_legacy_shape(make_client):
    client = make_client()
    resp = client.get("/status")
    assert resp.status_code == 200
    assert resp.json() == {"message": "running"}


def test_no_openapi_exposed(make_client):
    client = make_client()
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_index_served_without_token(make_client):
    client = make_client()
    resp = client.get("/")
    assert resp.status_code == 200
    assert "SarahUB Setup" in resp.text


def test_static_assets_served(make_client):
    client = make_client()
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/setup.js").status_code == 200


# --------------------------------------------------------------------------
# token gate
# --------------------------------------------------------------------------


def test_api_requires_token(make_client):
    client = make_client()
    assert client.get("/api/state").status_code == 401
    assert (
        client.get("/api/state", headers={"X-Setup-Token": "wrong"}).status_code == 401
    )
    assert client.get("/api/state", headers=AUTH).status_code == 200
    assert client.get("/api/state", params={"token": TOKEN}).status_code == 200
    assert client.post("/api/setup/keys", json={}).status_code == 401
    assert client.post("/api/login/phone", json={"phone": "+1"}).status_code == 401
    assert client.post("/api/setup/complete").status_code == 401
    assert client.get("/api/logs").status_code == 401


def test_api_rejects_oversized_declared_body_after_authentication(make_client):
    client = make_client()
    payload = b"x" * (MAX_REQUEST_BODY_BYTES + 1)
    headers = {**AUTH, "Content-Type": "application/json"}

    unauthorized = client.post(
        "/api/setup/keys", content=payload, headers={"Content-Type": "application/json"}
    )
    assert unauthorized.status_code == 401

    response = client.post("/api/setup/keys", content=payload, headers=headers)
    assert response.status_code == 413
    assert response.json() == {"detail": "request body too large"}


def test_api_rejects_oversized_chunked_body_without_content_length():
    chunks = [
        {
            "type": "http.request",
            "body": b"x" * MAX_REQUEST_BODY_BYTES,
            "more_body": True,
        },
        {"type": "http.request", "body": b"x", "more_body": True},
    ]
    responses = []
    app_called = False

    async def receive():
        return chunks.pop(0)

    async def send(message):
        responses.append(message)

    async def app(_scope, _receive, _send):
        nonlocal app_called
        app_called = True

    scope = {
        "type": "http",
        "path": "/api/state",
        "method": "POST",
        "headers": [(b"x-setup-token", TOKEN.encode())],
        "query_string": b"",
    }
    middleware = TokenGateMiddleware(app, TOKEN)
    asyncio.run(middleware(scope, receive, send))

    assert not app_called
    assert responses[0]["status"] == 413


def test_token_matches_constant_time():
    token = generate_setup_token()
    assert token_matches(token, token)
    assert not token_matches(token + "x", token)
    assert not token_matches(None, token)
    assert not token_matches("", token)


# --------------------------------------------------------------------------
# envfile: atomic writes
# --------------------------------------------------------------------------


def test_envfile_write_and_merge(tmp_path):
    path = tmp_path / ".env"
    envfile.write_env_atomic({"API_ID": "123", "API_HASH": "abc"}, path)
    first = envfile.read_env(path)
    assert first == {"API_ID": "123", "API_HASH": "abc"}

    # merge: existing key updated in place, comments/unknown keys preserved
    path.write_text("# comment\nAPI_ID=123\nCUSTOM_KEY=keepme\n", encoding="utf-8")
    envfile.write_env_atomic({"API_ID": "456", "SESSION": "sess"}, path)
    text = path.read_text(encoding="utf-8")
    assert "# comment" in text
    assert "CUSTOM_KEY=keepme" in text
    merged = envfile.read_env(path)
    assert merged["API_ID"] == "456"
    assert merged["SESSION"] == "sess"


def test_envfile_permissions_and_environ(tmp_path):
    path = tmp_path / ".env"
    envfile.write_env_atomic({"API_ID": "1"}, path)
    mode = stat.S_IMODE(path.stat().st_mode)
    assert mode == 0o600
    assert os.environ["API_ID"] == "1"  # apply_to_environ


def test_envfile_atomic_failure_leaves_original(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("API_ID=999\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("simulated crash mid-write")

    monkeypatch.setattr(envfile.os, "replace", boom)
    with pytest.raises(OSError):
        envfile.write_env_atomic({"API_ID": "1"}, path)
    assert path.read_text(encoding="utf-8") == "API_ID=999\n"
    assert not list(tmp_path.glob("*.tmp"))  # temp file cleaned up


def test_envfile_quoting_roundtrip(tmp_path):
    path = tmp_path / ".env"
    envfile.write_env_atomic({"WEIRD": "value with #hash"}, path)
    assert envfile.read_env(path)["WEIRD"] == "value with #hash"


def test_envfile_quote_escape_roundtrip(tmp_path):
    """Values with quotes + backslashes must survive write→read exactly
    (naive strip('"')/strip("'") parsing drops escapes and over-strips)."""
    path = tmp_path / ".env"
    tricky = 'say "hi" #5 \\path\\to'
    envfile.write_env_atomic({"TRICKY": tricky}, path)
    assert envfile.read_env(path)["TRICKY"] == tricky
    # exact one-pair stripping: a genuinely quote-wrapped unquoted value
    path.write_text("A=''\nB=\"x\"\n", encoding="utf-8")
    parsed = envfile.read_env(path)
    assert parsed["A"] == ""
    assert parsed["B"] == "x"


# --------------------------------------------------------------------------
# /api/setup/keys
# --------------------------------------------------------------------------


def test_setup_keys_success(make_client, env_path):
    client = make_client()
    resp = client.post(
        "/api/setup/keys",
        json={
            "api_id": 12345,
            "api_hash": API_HASH,
            "bot_token": BOT_TOKEN,
            "log_channel": -100123,
        },
        headers=AUTH,
    )
    assert resp.status_code == 200
    env = envfile.read_env(env_path)
    assert env["API_ID"] == "12345"
    assert env["API_HASH"] == API_HASH
    assert env["BOT_TOKEN"] == BOT_TOKEN
    assert env["LOG_CHANNEL"] == "-100123"
    # secrets must never be reflected back
    assert API_HASH not in resp.text
    assert BOT_TOKEN not in resp.text


def test_setup_keys_defaults_for_optional_vars(make_client, env_path, monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("LOG_CHANNEL", raising=False)
    client = make_client()
    resp = client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    assert resp.status_code == 200
    env = envfile.read_env(env_path)
    # An omitted optional bot token is equivalent to a blank value.
    assert env.get("BOT_TOKEN", "") == ""
    assert env["LOG_CHANNEL"] == "0"


def test_setup_keys_validation(make_client):
    client = make_client()
    bad_id = client.post(
        "/api/setup/keys", json={"api_id": 0, "api_hash": API_HASH}, headers=AUTH
    )
    assert bad_id.status_code == 422
    bad_hash = client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": "x"}, headers=AUTH
    )
    assert bad_hash.status_code == 422
    bad_token = client.post(
        "/api/setup/keys",
        json={"api_id": 1, "api_hash": API_HASH, "bot_token": "not-a-token"},
        headers=AUTH,
    )
    assert bad_token.status_code == 422
    assert (
        client.post(
            "/api/setup/keys", json={"api_hash": API_HASH}, headers=AUTH
        ).status_code
        == 422
    )  # missing api_id


# --------------------------------------------------------------------------
# phone OTP login
# --------------------------------------------------------------------------


def test_phone_login_happy_path(make_client, env_path):
    fake = FakeTelethonClient()
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )

    resp = client.post("/api/login/phone", json={"phone": "+15551234567"}, headers=AUTH)
    assert resp.status_code == 200
    login_id = resp.json()["login_id"]
    assert ("send_code_request", "+15551234567") in fake.calls

    resp = client.post(
        "/api/login/otp", json={"login_id": login_id, "code": "12345"}, headers=AUTH
    )
    assert resp.status_code == 200
    assert FAKE_SESSION_STRING not in resp.text
    assert envfile.read_env(env_path)["SESSION"] == FAKE_SESSION_STRING
    # phone_code_hash is threaded through, not re-requested
    assert ("sign_in", "+15551234567", "12345", None, "hash-123") in fake.calls


def test_phone_login_invalid_code_and_phone(make_client):
    fake = FakeTelethonClient()
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )

    resp = client.post("/api/login/phone", json={"phone": "+bad"}, headers=AUTH)
    assert resp.status_code == 400
    assert "phone" in resp.json()["detail"]

    resp = client.post("/api/login/phone", json={"phone": "+1555"}, headers=AUTH)
    login_id = resp.json()["login_id"]
    resp = client.post(
        "/api/login/otp", json={"login_id": login_id, "code": "00000"}, headers=AUTH
    )
    assert resp.status_code == 400
    assert "code" in resp.json()["detail"]


def test_phone_login_2fa_flow(make_client):
    fake = FakeTelethonClient(two_factor=True)
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    login_id = client.post(
        "/api/login/phone", json={"phone": "+1555"}, headers=AUTH
    ).json()["login_id"]

    resp = client.post(
        "/api/login/otp", json={"login_id": login_id, "code": "12345"}, headers=AUTH
    )
    assert resp.status_code == 428
    assert resp.json()["needs_password"] is True

    # wrong password → 400 (session kept — password errors are retryable)
    resp = client.post(
        "/api/login/otp",
        json={"login_id": login_id, "code": "12345", "password": "wrong"},
        headers=AUTH,
    )
    assert resp.status_code == 400

    # correct password → success
    resp = client.post(
        "/api/login/otp",
        json={"login_id": login_id, "code": "12345", "password": "correctpw"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert FAKE_SESSION_STRING not in resp.text


def test_phone_2fa_never_resubmits_consumed_code(make_client):
    """Regression: after SessionPasswordNeededError the OTP is consumed.

    The UI re-sends the code together with the 2FA password; the manager
    must route the attempt to a password-only sign_in — resubmitting the
    consumed code would raise PhoneCodeInvalidError in real Telethon (code
    is always attempted before the password fallback).
    """
    fake = FakeTelethonClient(two_factor=True)
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    login_id = client.post(
        "/api/login/phone", json={"phone": "+1555"}, headers=AUTH
    ).json()["login_id"]

    first = client.post(
        "/api/login/otp", json={"login_id": login_id, "code": "12345"}, headers=AUTH
    )
    assert first.status_code == 428
    assert fake.code_consumed is True

    # UI pattern: same code + password in one payload.
    second = client.post(
        "/api/login/otp",
        json={"login_id": login_id, "code": "12345", "password": "correctpw"},
        headers=AUTH,
    )
    assert second.status_code == 200
    code_bearing = [c for c in fake.calls if c[0] == "sign_in" and c[2] is not None]
    assert len(code_bearing) == 1  # the consumed OTP was never resubmitted
    password_only = [
        c for c in fake.calls if c[0] == "sign_in" and c[2] is None and c[3]
    ]
    assert password_only  # 2FA completed via password-only sign_in


def test_phone_login_requires_keys_first(make_client):
    client = make_client(client_factory=lambda: FakeTelethonClient())
    resp = client.post("/api/login/phone", json={"phone": "+1555"}, headers=AUTH)
    assert resp.status_code == 400
    assert "API" in resp.json()["detail"]


def test_otp_rate_limited(make_client):
    client = make_client(client_factory=lambda: FakeTelethonClient())
    for attempt in range(5):
        client.post(
            "/api/login/otp", json={"login_id": "nope", "code": "1"}, headers=AUTH
        )
    resp = client.post(
        "/api/login/otp", json={"login_id": "nope", "code": "1"}, headers=AUTH
    )
    assert resp.status_code == 429


def test_qr_password_rate_limited(make_client):
    client = make_client(client_factory=lambda: FakeTelethonClient())
    for attempt in range(5):
        client.post("/api/login/qr/nope/password", json={"password": "x"}, headers=AUTH)
    resp = client.post(
        "/api/login/qr/nope/password", json={"password": "x"}, headers=AUTH
    )
    assert resp.status_code == 429


# --------------------------------------------------------------------------
# login client lifecycle: disconnect on success/failure/expiry/abandonment
# --------------------------------------------------------------------------


def test_client_disconnected_after_successful_login(make_client):
    fake = FakeTelethonClient()
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    login_id = client.post(
        "/api/login/phone", json={"phone": "+1555"}, headers=AUTH
    ).json()["login_id"]
    assert fake.connected is True
    resp = client.post(
        "/api/login/otp", json={"login_id": login_id, "code": "12345"}, headers=AUTH
    )
    assert resp.status_code == 200
    assert fake.connected is False


def test_client_disconnected_on_terminal_code_failure(make_client):
    """PhoneCodeExpiredError is terminal: session dropped, client disconnected."""
    fake = FakeTelethonClient(expire_code=True)
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    login_id = client.post(
        "/api/login/phone", json={"phone": "+1555"}, headers=AUTH
    ).json()["login_id"]
    resp = client.post(
        "/api/login/otp", json={"login_id": login_id, "code": "12345"}, headers=AUTH
    )
    assert resp.status_code == 400
    assert "expired" in resp.json()["detail"]
    manager = client.app.state.setup.login_manager
    assert manager._sessions == {}
    assert fake.connected is False


def test_client_disconnected_when_code_request_fails(make_client):
    """A login that never started must not leak a connected client."""
    fake = FakeTelethonClient()
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    resp = client.post("/api/login/phone", json={"phone": "+bad"}, headers=AUTH)
    assert resp.status_code == 400
    assert fake.connected is False


def test_client_disconnected_on_ttl_expiry():
    """Expired login sessions disconnect their client (via the home loop)."""
    fake = FakeTelethonClient()
    manager = LoginManager(1, "hash", client_factory=lambda: fake, ttl=-1)

    async def run():
        result = await manager.start_phone("+1555")
        assert fake.connected is True
        with pytest.raises(LoginSessionUnknown):
            await manager.submit_code(result["login_id"], "12345")
        # disconnect is scheduled on the loop — let it drain
        for _ in range(5):
            await asyncio.sleep(0.02)
        assert fake.connected is False

    asyncio.run(run())


def test_abandoned_sessions_swept_on_new_login():
    """Starting a new login sweeps expired ones (disconnecting clients)."""
    fakes = [FakeTelethonClient(), FakeTelethonClient()]
    manager = LoginManager(1, "hash", client_factory=lambda: fakes.pop(0), ttl=0.05)

    async def run():
        await manager.start_phone("+1555111")
        await asyncio.sleep(0.08)  # first session expires
        await manager.start_phone("+1555222")
        assert len(manager._sessions) == 1
        for _ in range(5):
            await asyncio.sleep(0.02)
        assert len(manager._sessions) == 1

    asyncio.run(run())


def test_qr_wait_terminal_failure_disconnects(make_client):
    """A hard QR wait failure drops the session and disconnects the client."""
    fake = FakeQRTelethonClient(wait_outcomes=["boom"])
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    login_id = client.post("/api/login/qr", headers=AUTH).json()["login_id"]

    events = []
    with client.stream("GET", f"/api/login/qr/{login_id}/wait", headers=AUTH) as stream:
        for line in stream.iter_lines():
            events.append(line)
            if line.startswith("event:") and "error" in line:
                break
    assert any("error" in line for line in events)
    manager = client.app.state.setup.login_manager
    assert manager._sessions == {}
    assert fake.connected is False


# --------------------------------------------------------------------------
# QR login
# --------------------------------------------------------------------------


def test_qr_login_success(make_client, env_path):
    fake = FakeQRTelethonClient(wait_outcomes=["user"])
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )

    resp = client.post("/api/login/qr", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["url"].startswith("tg://login?token=")
    assert "expires" in body

    events = []
    with client.stream(
        "GET", f"/api/login/qr/{body['login_id']}/wait", headers=AUTH
    ) as stream:
        for line in stream.iter_lines():
            events.append(line)
            if line.startswith("event:") and "success" in line:
                break
    names = [name for name, _ in _sse_events(events)]
    assert "waiting" in names
    assert "success" in names
    assert envfile.read_env(env_path)["SESSION"] == FAKE_SESSION_STRING
    # session string never crosses the wire
    assert FAKE_SESSION_STRING not in "\n".join(events)


def test_qr_login_expiry_recreates_token(make_client):
    fake = FakeQRTelethonClient(wait_outcomes=["timeout", "user"])
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    login_id = client.post("/api/login/qr", headers=AUTH).json()["login_id"]

    events = []
    with client.stream("GET", f"/api/login/qr/{login_id}/wait", headers=AUTH) as stream:
        for line in stream.iter_lines():
            events.append(line)
            if line.startswith("event:") and "success" in line:
                break
    parsed = _sse_events(events)
    names = [name for name, _ in parsed]
    assert "expired" in names
    assert "success" in names
    assert fake.qr.recreate_calls == 1
    expired = next(data for name, data in parsed if name == "expired")
    assert "RkFLRXRva2Vu1" in expired  # recreated token v1 in the new URL


def test_qr_login_2fa(make_client, env_path):
    fake = FakeQRTelethonClient(wait_outcomes=["2fa"])
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    login_id = client.post("/api/login/qr", headers=AUTH).json()["login_id"]

    events = []
    with client.stream("GET", f"/api/login/qr/{login_id}/wait", headers=AUTH) as stream:
        for line in stream.iter_lines():
            events.append(line)
            if "needs_password" in line:
                break
    assert any("needs_password" in line for line in events)

    resp = client.post(
        f"/api/login/qr/{login_id}/password",
        json={"password": "correctpw"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert envfile.read_env(env_path)["SESSION"] == FAKE_SESSION_STRING


def test_qr_unknown_login(make_client):
    client = make_client(client_factory=lambda: FakeQRTelethonClient([]))
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    assert client.get("/api/login/qr/nope/wait", headers=AUTH).status_code == 404
    assert (
        client.post(
            "/api/login/qr/nope/password", json={"password": "x"}, headers=AUTH
        ).status_code
        == 404
    )


def test_qr_image_404_without_qrcode_package(make_client, monkeypatch):
    fake = FakeQRTelethonClient(wait_outcomes=["user"])
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    login_id = client.post("/api/login/qr", headers=AUTH).json()["login_id"]
    monkeypatch.setitem(sys.modules, "qrcode", None)  # force ImportError
    resp = client.get(f"/api/login/qr/{login_id}/image", headers=AUTH)
    assert resp.status_code == 404
    assert resp.json()["url"].startswith("tg://login?token=")


def test_qr_image_png_with_qrcode_package(make_client, monkeypatch):
    fake = FakeQRTelethonClient(wait_outcomes=["user"])
    client = make_client(client_factory=lambda: fake)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    login_id = client.post("/api/login/qr", headers=AUTH).json()["login_id"]

    class FakeQRImage:
        def save(self, buf, format=None):
            buf.write(b"\x89PNG-fake")

    fake_qrcode = SimpleNamespace(make=lambda url: FakeQRImage())
    monkeypatch.setitem(sys.modules, "qrcode", fake_qrcode)
    resp = client.get(f"/api/login/qr/{login_id}/image", headers=AUTH)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/png"
    assert resp.content == b"\x89PNG-fake"


# --------------------------------------------------------------------------
# SSE logs + redaction
#
# NOTE: starlette's TestClient (httpx2) cannot handle *never-terminating*
# streams — client.stream() hangs at enter even for a minimal app (verified
# empirically). Finite streams (the QR wait endpoint) work fine over HTTP.
# So /api/logs is driven at the ASGI level here: deterministic, no network.
# --------------------------------------------------------------------------


def asgi_sse_lines(app, *, publish=None, stop_after=None, timeout=5.0):
    """Drive the app's ASGI interface against GET /api/logs.

    Collects SSE lines until ``stop_after(line)`` is true (or timeout),
    then sends http.disconnect so the generator exits cleanly.
    """

    async def main():
        lines = []
        buffer = b""
        state = {"sent_request": False, "disconnected": False}
        done = asyncio.Event()

        async def receive():
            if state["disconnected"]:
                return {"type": "http.disconnect"}
            if not state["sent_request"]:
                state["sent_request"] = True
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.sleep(3600)  # parked; cancelled by starlette's probe

        async def send(message):
            nonlocal buffer
            if message["type"] != "http.response.body":
                return
            buffer += message.get("body", b"")
            while b"\n" in buffer:
                raw, buffer = buffer.split(b"\n", 1)
                lines.append(raw.decode())
                if stop_after and stop_after(lines[-1]):
                    done.set()

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/logs",
            "raw_path": b"/api/logs",
            "query_string": f"token={TOKEN}".encode(),
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
        task = asyncio.create_task(app(scope, receive, send))
        try:
            if publish:
                await asyncio.sleep(0.05)  # let the generator subscribe first
                publish()
            await asyncio.wait_for(done.wait(), timeout)
        finally:
            state["disconnected"] = True
            try:
                await asyncio.wait_for(task, 2)
            except (asyncio.TimeoutError, Exception):
                task.cancel()
        return lines

    return asyncio.run(main())


def test_log_stream_redacts_secrets(make_client):
    client = make_client()
    secret = "super-secret-session-value-12345"
    from pyUltroid.webserver.security import REDACTOR

    REDACTOR.add(secret)
    try:
        lines = asgi_sse_lines(
            client.app,
            publish=lambda: logging.getLogger().info("leaked %s here", secret),
            stop_after=lambda line: line.startswith("data:") and "leaked" in line,
        )
    finally:
        REDACTOR.discard(secret)
    joined = "\n".join(lines)
    assert ": ready" in lines  # stream reached the live phase
    assert "leaked" in joined
    assert secret not in joined
    assert "**********" in joined


def test_log_stream_history_replayed(make_client):
    client = make_client()
    broker = client.app.state.log_broker
    broker.publish("line-before-subscribe")
    lines = asgi_sse_lines(client.app, stop_after=lambda line: line == ": ready")
    assert any("line-before-subscribe" in line for line in lines)
    assert ": ready" in lines


def test_redaction_filter_directly():
    redactor = SecretRedactor()
    redactor.add("shhh-secret")
    filt = RedactionFilter(redactor)
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "token is %s", ("shhh-secret",), None
    )
    assert filt.filter(record) is True
    assert "shhh-secret" not in record.getMessage()


def test_redactor_ignores_short_values():
    redactor = SecretRedactor()
    redactor.add("ab")  # too short — false-positive guard
    assert redactor.redact("ab cd ab") == "ab cd ab"


def test_broker_unsubscribe_on_closed_loop():
    broker = LogBroker()
    loop = asyncio.new_event_loop()
    queue_ = broker.subscribe(loop)
    loop.close()
    broker.publish("x")  # must not raise; subscriber dropped
    assert queue_ not in broker._subscribers


# --------------------------------------------------------------------------
# completion / restart handshake
# --------------------------------------------------------------------------


def test_complete_requires_session(make_client):
    client = make_client()
    resp = client.post("/api/setup/complete", headers=AUTH)
    assert resp.status_code == 409


def test_complete_fires_callback_once(make_client, env_path):
    on_complete = MagicMock()
    fake = FakeTelethonClient()
    client = make_client(client_factory=lambda: fake, on_complete=on_complete)
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    login_id = client.post(
        "/api/login/phone", json={"phone": "+1555"}, headers=AUTH
    ).json()["login_id"]
    client.post(
        "/api/login/otp", json={"login_id": login_id, "code": "12345"}, headers=AUTH
    )

    resp = client.post("/api/setup/complete", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["restart"] is True
    resp2 = client.post("/api/setup/complete", headers=AUTH)
    assert resp2.status_code == 200
    time.sleep(0.8)  # completion timer (0.5s) fires async
    on_complete.assert_called_once()


def test_state_endpoint_tracks_progress(make_client):
    client = make_client(client_factory=lambda: FakeTelethonClient())
    body = client.get("/api/state", headers=AUTH).json()
    assert body["setup_required"] is True
    assert body["has_api_keys"] is False
    client.post(
        "/api/setup/keys", json={"api_id": 1, "api_hash": API_HASH}, headers=AUTH
    )
    body = client.get("/api/state", headers=AUTH).json()
    assert body["has_api_keys"] is True
    assert body["setup_required"] is True  # still no session


# --------------------------------------------------------------------------
# bootstrap helpers
# --------------------------------------------------------------------------


def test_config_complete_missing_and_present(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["-m"])
    for key in ("API_ID", "API_HASH", "SESSION"):
        monkeypatch.delenv(key, raising=False)
    path = tmp_path / ".env"
    assert bootstrap.config_complete(path) is False
    envfile.write_env_atomic(
        {"API_ID": "1", "API_HASH": "abc", "SESSION": "s"},
        path,
        apply_to_environ=False,
    )
    assert bootstrap.config_complete(path) is True


def test_config_complete_argv_shortcut(monkeypatch):
    # configs.py contract: python -m pyUltroid <api_id> <api_hash> <session>
    monkeypatch.setattr(sys, "argv", ["-m", "1", "hash", "sess"])
    assert bootstrap.config_complete() is True


def test_config_complete_environ_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["-m"])
    monkeypatch.delenv("API_ID", raising=False)
    monkeypatch.delenv("API_HASH", raising=False)
    monkeypatch.delenv("SESSION", raising=False)
    path = tmp_path / ".env"
    envfile.write_env_atomic({"API_ID": "1"}, path, apply_to_environ=False)
    monkeypatch.setenv("API_HASH", "h")
    monkeypatch.setenv("SESSION", "s")
    assert bootstrap.config_complete(path) is True


def test_ensure_config_or_setup_noop_when_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["-m"])
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "h")
    monkeypatch.setenv("SESSION", "s")
    assert bootstrap.ensure_config_or_setup(tmp_path / ".env") is None


def test_bootstrap_waiter_marker(tmp_path):
    marker = tmp_path / ".setup-waiting"
    assert bootstrap.bootstrap_waiter_active(marker) is False
    marker.write_text(str(os.getpid()), encoding="utf-8")
    assert bootstrap.bootstrap_waiter_active(marker) is True
    marker.write_text("99999999", encoding="utf-8")  # dead pid
    assert bootstrap.bootstrap_waiter_active(marker) is False
    marker.write_text("garbage", encoding="utf-8")
    assert bootstrap.bootstrap_waiter_active(marker) is False


def test_spawn_bot_suppressed_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SETUP_NO_SPAWN", "1")
    monkeypatch.setattr(bootstrap, "marker_path", lambda: tmp_path / "m")
    popen = MagicMock()
    monkeypatch.setattr(bootstrap.subprocess, "Popen", popen)
    bootstrap.spawn_bot_after_setup()
    popen.assert_not_called()


def test_spawn_bot_suppressed_by_waiter(monkeypatch, tmp_path):
    monkeypatch.delenv("SETUP_NO_SPAWN", raising=False)
    marker = tmp_path / "m"
    marker.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(bootstrap, "marker_path", lambda: marker)
    popen = MagicMock()
    monkeypatch.setattr(bootstrap.subprocess, "Popen", popen)
    bootstrap.spawn_bot_after_setup()
    popen.assert_not_called()


def test_spawn_bot_spawns(monkeypatch, tmp_path):
    monkeypatch.delenv("SETUP_NO_SPAWN", raising=False)
    monkeypatch.setattr(bootstrap, "marker_path", lambda: tmp_path / "m")
    popen = MagicMock()
    monkeypatch.setattr(bootstrap.subprocess, "Popen", popen)
    bootstrap.spawn_bot_after_setup()
    popen.assert_called_once()
    assert popen.call_args[0][0] == [sys.executable, "-m", "pyUltroid"]


# --------------------------------------------------------------------------
# keep-alive env config (W2-A.5)
# --------------------------------------------------------------------------


def test_keepalive_disabled_without_creds():
    config = KeepAliveConfig.from_env({})
    assert config.enabled is False
    assert config.min_interval == 7200
    assert config.max_interval == 28800


def test_keepalive_env_configuration():
    env = {
        "SPACE_URL": "https://example.com",
        "HF_TOKEN": "hf_x",
        "KEEP_ALIVE_MIN_SECONDS": "120",
        "KEEP_ALIVE_MAX_SECONDS": "240",
    }
    config = KeepAliveConfig.from_env(env)
    assert config.enabled is True
    assert (config.min_interval, config.max_interval) == (120, 240)

    env["KEEP_ALIVE"] = "0"
    assert KeepAliveConfig.from_env(env).enabled is False

    del env["KEEP_ALIVE"]
    env["KEEP_ALIVE_MIN_SECONDS"] = "5"  # clamped to >= 60
    env["KEEP_ALIVE_MAX_SECONDS"] = "10"  # clamped to >= min
    config = KeepAliveConfig.from_env(env)
    assert config.min_interval == 60
    assert config.max_interval == 60


# --------------------------------------------------------------------------
# misc units
# --------------------------------------------------------------------------


def test_rate_limiter_window():
    limiter = RateLimiter(max_attempts=2, window_seconds=60)
    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is False
    assert limiter.allow("other") is True
    limiter.reset()
    assert limiter.allow("ip") is True


def test_login_manager_session_expiry():
    manager = LoginManager(1, "hash", client_factory=FakeTelethonClient, ttl=-1)

    async def run():
        result = await manager.start_phone("+1555")
        with pytest.raises(LoginSessionUnknown):
            await manager.submit_code(result["login_id"], "12345")

    asyncio.run(run())


def test_attach_broker_detaches_cleanly():
    root = logging.getLogger()
    broker, handler = attach_broker()
    assert handler in root.handlers
    broker.publish("hello")
    assert "hello" in broker.history()[-1]
    root.removeHandler(handler)

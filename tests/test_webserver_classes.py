"""The webserver class layer: every module in ``pyUltroid/webserver/`` now
owns purpose-built classes, exported lazily from the package, with the
historical module-level functions kept as aliases for the plugin/server
surface.

These tests are dependency-free on purpose - they must run in an
environment with no fastapi / uvicorn / telethon, which is exactly the
environment the package's import contract promises to support.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import subprocess
import sys
import types

import pytest

from conftest import ROOT


# ── helpers ───────────────────────────────────────────────────────────


def has(module: str) -> bool:
    try:
        importlib.import_module(module)
    except Exception:
        return False
    return True


NEEDS_FASTAPI = pytest.mark.skipif(
    not has("fastapi"), reason="fastapi is not installed"
)
NEEDS_TELETHON = pytest.mark.skipif(
    not has("telethon"), reason="telethon is not installed"
)


@pytest.fixture
def env(tmp_path):
    """An ``.env`` path with every credential removed from the environment."""
    saved = {}
    for key in (
        "API_ID",
        "API_HASH",
        "SESSION",
        "SETUP_TOKEN",
        "PORT",
        "SETUP_BIND",
    ):
        saved[key] = __import__("os").environ.pop(key, None)
    yield tmp_path / ".env"
    for key, value in saved.items():
        if value is not None:
            __import__("os").environ[key] = value


def modules():
    """Only the submodules whose dependencies are actually installed."""
    names = ("security", "envfile", "runner", "logs", "state", "bootstrap", "keepalive")
    if has("telethon"):
        names += ("login",)
    if has("fastapi"):
        names += ("app", "miniapp")
    return {
        name: importlib.import_module(f"pyUltroid.webserver.{name}")
        for name in names
    }


# ── 1. the import contract ────────────────────────────────────────────


def test_package_import_pulls_no_optional_dependency():
    """Importing the package must survive a broken or missing fastapi.

    With the eager ``__init__`` (no PEP 562 hook), the real contract is:
    ``import pyUltroid.webserver`` succeeds on ANY install because the
    fastapi-dependent ASGI layer (``app``/``miniapp``) is capability-gated
    to ``None`` when fastapi cannot import. Telethon and uvicorn are hard
    dependencies of the login/runner modules, so they are allowed to load.
    """
    code = (
        "import pyUltroid.webserver as ws;"
        "print(ws.LoginManager is not None, ws.ThreadedServer is not None,"
        " ws.create_app is None)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("True"), out.stdout


def test_every_advertised_name_resolves():
    ws = importlib.import_module("pyUltroid.webserver")
    unavailable = set()
    if not has("telethon"):
        unavailable |= {
            "LoginManager",
            "LoginOutcome",
            "LoginSession",
            "LoginSessionUnknown",
            "SessionFactory",
        }
    if not has("fastapi"):
        unavailable |= {
            "SseFrame",
            "LoginErrorMapper",
            "SetupApplication",
            "TokenGateMiddleware",
            "MemoryStore",
            "MiniAppApplication",
            "MiniAppServer",
            "MiniAppState",
            "MiniAppStore",
            "SUPERVISOR",
            "create_app",
            "create_miniapp",
            "ensure_running",
            "ensure_token",
            "rotate_token",
        }
    missing = [
        name
        for name in ws.__all__
        if name not in unavailable and not hasattr(ws, name)
    ]
    assert not missing, missing
    assert len(ws.__all__) > 40


def test_unknown_attribute_raises_attributeerror():
    ws = importlib.import_module("pyUltroid.webserver")
    with pytest.raises(AttributeError, match="has no attribute"):
        ws.definitely_not_here


def test_no_webserver_module_imports_pyultroid_at_module_level():
    """No folder file may import ``pyUltroid.*`` at module level.

    Parsed with ``ast`` rather than grepped, so a lazy import inside a
    function body is not a false positive.
    """
    import ast

    offenders = []
    for path in sorted((ROOT / "pyUltroid" / "webserver").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:  # module level only
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
                names += [
                    f"{node.module or ''}.{alias.name}" for alias in node.names
                ]
            for name in names:
                if name == "pyUltroid" or name.startswith("pyUltroid."):
                    offenders.append(f"{path.name}:{node.lineno} {name}")
    assert not offenders, "top-level pyUltroid imports: " + "; ".join(offenders)


def test_submodule_import_is_not_shadowed_by_a_singleton():
    """``bootstrap`` must stay the submodule, not the object."""
    from pyUltroid.webserver import bootstrap

    assert isinstance(bootstrap, types.ModuleType)
    assert isinstance(bootstrap.BOOTSTRAP, bootstrap.SetupBootstrap)


# ── 2. security: three unrelated classes, one shared authority ────────


def test_redactor_refuses_short_values_and_redacts_longest_first():
    security = importlib.import_module("pyUltroid.webserver.security")
    redactor = security.SecretRedactor()
    assert redactor.add("ab") is False
    assert redactor.add("abcd") is True
    redactor.add("abcdefgh")
    # Longest first, otherwise the short secret would corrupt the long one.
    assert redactor.redact("x abcdefgh y abcd z") == "x ********** y ********** z"
    redactor.discard("abcd")
    assert redactor.redact("abcd") == "abcd"
    assert len(redactor) == 1
    assert "abcd" not in repr(redactor)


def test_redactor_is_instance_isolated():
    security = importlib.import_module("pyUltroid.webserver.security")
    a, b = security.SecretRedactor(), security.SecretRedactor()
    a.add("secret-a")
    assert b.redact("secret-a") == "secret-a"


def test_rate_limiter_prunes_idle_keys():
    security = importlib.import_module("pyUltroid.webserver.security")
    limiter = security.RateLimiter(max_attempts=2, window_seconds=60)
    assert limiter.allow("a") is True
    assert limiter.allow("a") is True
    assert limiter.allow("a") is False
    assert limiter.remaining("a") == 0
    limiter.reset()
    assert limiter.allow("a") is True


def test_token_authority_owns_the_shared_redactor():
    security = importlib.import_module("pyUltroid.webserver.security")
    assert security.REDACTOR is security.AUTHORITY.redactor
    token = security.generate_setup_token()
    assert security.token_matches(token, token) is True
    assert security.token_matches(token, token + "x") is False
    assert security.token_matches("", token) is False
    assert security.token_matches(None, None) is False
    # the minted token is registered for redaction
    assert security.REDACTOR.redact(f"url?token={token}").endswith("*" * 10)


def test_token_authority_is_injectable():
    security = importlib.import_module("pyUltroid.webserver.security")
    private = security.TokenAuthority()
    request = types.SimpleNamespace(
        headers={"X-Setup-Token": "header-token"},
        query_params={"token": "query-token"},
    )
    assert private.pull(request) == "header-token"
    assert private.permits(request, "header-token") is True
    assert private.permits(request, "nope") is False


# ── 3. envfile: a path-bound document ─────────────────────────────────


def test_envfile_round_trips_and_quotes(tmp_path):
    envfile = importlib.import_module("pyUltroid.webserver.envfile")
    document = envfile.EnvFile(tmp_path / ".env")
    tricky = "value with #hash and spaces"
    document.write({"TRICKY": tricky, "EMPTY": ""})
    assert document.read()["TRICKY"] == tricky
    assert document.read()["EMPTY"] == ""
    assert (document.path.stat().st_mode & 0o777) == 0o600


def test_envfile_preserves_comments_and_order(tmp_path):
    envfile = importlib.import_module("pyUltroid.webserver.envfile")
    path = tmp_path / ".env"
    path.write_text("# a comment\nFIRST=1\nSECOND=2\n", encoding="utf-8")
    document = envfile.EnvFile(path)
    document.write({"SECOND": "22", "THIRD": "3"})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines == ["# a comment", "FIRST=1", "SECOND=22", "THIRD=3"]


def test_envfile_complete_matches_the_legacy_gate(env):
    envfile = importlib.import_module("pyUltroid.webserver.envfile")
    document = envfile.EnvFile(env)
    assert document.missing() == ("API_ID", "API_HASH", "SESSION")
    assert document.complete() is False
    document.write({"API_ID": "1", "API_HASH": "a", "SESSION": "s"}, apply_to_environ=False)
    assert document.complete() is True
    document.write({"API_ID": "not-an-int"}, apply_to_environ=False)
    assert document.missing() == ("API_ID",)
    assert document.complete() is False


def test_envfile_atomic_failure_leaves_the_original(tmp_path, monkeypatch):
    envfile = importlib.import_module("pyUltroid.webserver.envfile")
    path = tmp_path / ".env"
    document = envfile.EnvFile(path)
    document.write({"KEEP": "yes"}, apply_to_environ=False)

    def boom(*_a, **_k):
        raise OSError("nope")

    monkeypatch.setattr(envfile.os, "replace", boom)
    with pytest.raises(OSError):
        document.write({"KEEP": "no"}, apply_to_environ=False)
    assert document.read()["KEEP"] == "yes"
    assert not list(tmp_path.glob("*.tmp"))


def test_envfile_module_aliases_match_the_class():
    envfile = importlib.import_module("pyUltroid.webserver.envfile")
    document = envfile.EnvFile(envfile.default_env_path())
    document.write({"ALIAS": "1"}, apply_to_environ=False)
    assert envfile.read_env(document.path)["ALIAS"] == "1"
    envfile.write_env_atomic({"ALIAS": "2"}, document.path, apply_to_environ=False)
    assert envfile.read_env(document.path)["ALIAS"] == "2"
    assert envfile.parse_env_text('K="v"') == {"K": "v"}


# ── 4. logs: attach is reversible, subscriptions are scoped ───────────


def test_log_stream_hub_attach_detach_restores_the_level():
    logs = importlib.import_module("pyUltroid.webserver.logs")
    root = logging.getLogger()
    previous_level = root.level
    before = len(root.handlers)
    hub = logs.LogStreamHub()
    broker, handler = hub.attach()
    try:
        assert handler in root.handlers
        assert hub.attached is True
        hub.attach()  # idempotent
        assert len(root.handlers) == before + 1
    finally:
        assert hub.detach() is True
    assert handler not in root.handlers
    assert root.level == previous_level
    assert hub.detach() is False
    broker.publish("x")
    assert broker.history()[-1] == "x"


def test_log_broker_subscription_always_unsubscribes():
    logs = importlib.import_module("pyUltroid.webserver.logs")
    broker = logs.LogBroker()

    async def scenario():
        async with broker.subscription() as queue_:
            assert broker.subscriber_count == 1
            broker.publish("live")
            assert await asyncio.wait_for(queue_.get(), 1) == "live"
        assert broker.subscriber_count == 0

    asyncio.run(scenario())


def test_log_broker_drops_dead_loop_subscribers():
    logs = importlib.import_module("pyUltroid.webserver.logs")
    broker = logs.LogBroker()
    loop = asyncio.new_event_loop()
    queue_ = broker.subscribe(loop)
    loop.close()
    broker.publish("x")
    assert queue_ not in broker._subscribers


def test_redaction_filter_clears_args():
    logs = importlib.import_module("pyUltroid.webserver.logs")
    security = importlib.import_module("pyUltroid.webserver.security")
    redactor = security.SecretRedactor()
    redactor.add("shhh-secret")
    filt = logs.RedactionFilter(redactor)
    record = logging.LogRecord(
        "t", logging.INFO, __file__, 1, "token is %s", ("shhh-secret",), None
    )
    assert filt.filter(record) is True
    assert "shhh-secret" not in record.getMessage()
    assert record.args == ()


# ── 5. login: the factory is standalone, the manager owns the sessions ──


@NEEDS_TELETHON
def test_login_manager_sweeps_and_disposes():
    login = importlib.import_module("pyUltroid.webserver.login")

    class Fake:
        def __init__(self):
            self.disconnects = 0

        async def connect(self):
            return None

        async def disconnect(self):
            self.disconnects += 1

        async def send_code_request(self, _phone):
            return types.SimpleNamespace(phone_code_hash="hash", type=int)

    clients = []

    def factory():
        client = Fake()
        clients.append(client)
        return client

    manager = login.LoginManager(1, "hash", client_factory=factory, ttl=600)

    async def scenario():
        started = await manager.start_phone("+15551234")
        assert manager.peek(started["login_id"]) is True
        assert len(manager) == 1
        assert started["login_id"] in manager
        assert started["code_type"] == "type"
        dropped = await manager.dispose()
        assert dropped == 1
        assert len(manager) == 0

    asyncio.run(scenario())
    assert clients[0].disconnects == 1


@NEEDS_TELETHON
def test_login_manager_sweeps_abandoned_sessions():
    login = importlib.import_module("pyUltroid.webserver.login")
    disconnects = []

    class Fake:
        async def connect(self):
            return None

        async def disconnect(self):
            disconnects.append(1)

        async def send_code_request(self, _phone):
            return types.SimpleNamespace(phone_code_hash="hash", type=int)

    # ttl=0 makes every session instantly expired
    manager = login.LoginManager(1, "hash", client_factory=Fake, ttl=0)

    async def scenario():
        started = await manager.start_phone("+1555")
        assert manager.peek(started["login_id"]) is False
        assert len(manager) == 0
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert disconnects == [1]


@NEEDS_TELETHON
def test_login_session_factory_is_swappable():
    login = importlib.import_module("pyUltroid.webserver.login")
    sentinel = object()
    factory = login.SessionFactory(lambda: sentinel)
    assert factory.custom is True
    assert factory() is sentinel
    # a custom factory ignores credentials entirely
    assert factory.for_credentials(1, "hash")() is sentinel
    assert login.SessionFactory().custom is False


@NEEDS_TELETHON
def test_login_manager_never_leaks_a_client_when_the_code_request_fails():
    login = importlib.import_module("pyUltroid.webserver.login")
    disconnects = []

    class Failing:
        async def connect(self):
            return None

        async def disconnect(self):
            disconnects.append(1)

        async def send_code_request(self, _phone):
            raise RuntimeError("telegram said no")

    manager = login.LoginManager(1, "hash", client_factory=Failing)

    async def scenario():
        with pytest.raises(RuntimeError):
            await manager.start_phone("+1555")

    asyncio.run(scenario())
    assert disconnects == [1]
    assert len(manager) == 0


# ── 6. state: no telethon at import, no manager leak ──────────────────


def test_setup_state_manager_is_cached_then_rebuilt(env):
    state = importlib.import_module("pyUltroid.webserver.state")
    built = []

    def factory(api_id, api_hash, client_factory=None):
        manager = types.SimpleNamespace(api_id=api_id, api_hash=api_hash)
        built.append(manager)
        return manager

    setup = state.SetupState.create(
        token="tok", env_path=env, standalone=False, manager_factory=factory
    )
    setup.api_id, setup.api_hash = 1, "hash"
    first = setup.manager()
    assert setup.manager() is first
    setup.api_id = 2
    assert setup.manager() is not first
    assert len(built) == 2


def test_setup_state_retarget_disposes_the_old_manager():
    state = importlib.import_module("pyUltroid.webserver.state")
    disposed = []

    class Manager:
        def __init__(self, api_id, api_hash):
            self.api_id, self.api_hash = api_id, api_hash

        async def dispose(self):
            disposed.append(self)

    setup = state.SetupState.create(
        token="tok",
        standalone=False,
        manager_factory=lambda i, h, c=None: Manager(i, h),
    )
    async def scenario():
        setup.api_id, setup.api_hash = 1, "hash"
        first = setup.manager()
        setup.api_id = 2
        setup.manager()
        await asyncio.sleep(0)
        assert disposed == [first]

    asyncio.run(scenario())


def test_setup_state_adopts_env_keys(env):
    state = importlib.import_module("pyUltroid.webserver.state")
    envfile = importlib.import_module("pyUltroid.webserver.envfile")
    envfile.EnvFile(env).write(
        {"API_ID": "42", "API_HASH": "hash"}, apply_to_environ=False
    )
    setup = state.SetupState.create(token="tok", env_path=env, standalone=False)
    assert setup.adopt_env_keys() is True
    assert (setup.api_id, setup.api_hash) == (42, "hash")
    # idempotent: nothing left to adopt
    assert setup.adopt_env_keys() is False


def test_setup_state_completion_latch(env):
    state = importlib.import_module("pyUltroid.webserver.state")
    fired = []
    setup = state.SetupState.create(
        token="tok",
        env_path=env,
        standalone=False,
        on_complete=lambda: fired.append(1),
    )
    assert setup.trigger_completion() is True
    assert setup.trigger_completion() is False
    import time

    time.sleep(1.0)
    assert fired == [1]


# ── 7. bootstrap: the gate and the process flow ───────────────────────


def test_config_gate_matches_legacy_config_complete(env):
    bootstrap = importlib.import_module("pyUltroid.webserver.bootstrap")
    # argv is pinned: under pytest `sys.argv` is longer than three entries,
    # which is exactly the credential-passing short circuit.
    gate = bootstrap.ConfigGate(env, argv=["-m"])
    assert gate.complete() is False
    envfile = importlib.import_module("pyUltroid.webserver.envfile")
    envfile.EnvFile(env).write(
        {"API_ID": "1", "API_HASH": "a", "SESSION": "s"}, apply_to_environ=False
    )
    assert gate.complete() is True
    assert bootstrap.config_complete(env) is True


def test_config_gate_argv_short_circuit(env):
    bootstrap = importlib.import_module("pyUltroid.webserver.bootstrap")
    assert bootstrap.ConfigGate(env, argv=["-m"]).argv_supplied() is False
    gate = bootstrap.ConfigGate(env, argv=["-m", "1", "hash", "sess"])
    assert gate.argv_supplied() is True
    assert gate.complete() is True


def test_config_gate_rejects_a_non_numeric_api_id(env):
    bootstrap = importlib.import_module("pyUltroid.webserver.bootstrap")
    envfile = importlib.import_module("pyUltroid.webserver.envfile")
    envfile.EnvFile(env).write(
        {"API_ID": "not-an-int", "API_HASH": "a", "SESSION": "s"},
        apply_to_environ=False,
    )
    gate = bootstrap.ConfigGate(env, argv=["-m"])
    assert gate.complete() is False
    assert gate.env.missing() == ("API_ID",)


def test_marker_path_stays_monkeypatchable(env, monkeypatch):
    bootstrap = importlib.import_module("pyUltroid.webserver.bootstrap")
    monkeypatch.setattr(bootstrap, "marker_path", lambda: env.parent / "marker")
    flow = bootstrap.SetupBootstrap()
    assert flow.marker_file() == env.parent / "marker"


def test_waiter_active_reads_the_pid(env, monkeypatch):
    import os

    bootstrap = importlib.import_module("pyUltroid.webserver.bootstrap")
    marker = env.parent / "marker"
    flow = bootstrap.SetupBootstrap()
    assert flow.waiter_active(marker) is False
    marker.write_text("not-a-pid")
    assert flow.waiter_active(marker) is False
    marker.write_text("999999999")
    assert flow.waiter_active(marker) is False
    marker.write_text(str(os.getpid()))
    assert flow.waiter_active(marker) is True


def test_spawn_is_suppressed_by_env_and_by_a_waiter(env, monkeypatch):
    import os

    bootstrap = importlib.import_module("pyUltroid.webserver.bootstrap")
    flow = bootstrap.SetupBootstrap()
    spawned = []
    marker = env.parent / "marker"
    monkeypatch.setattr(bootstrap, "marker_path", lambda: marker)

    monkeypatch.setenv("SETUP_NO_SPAWN", "1")
    assert flow.spawn_bot(lambda *a, **k: spawned.append(a)) is False
    monkeypatch.delenv("SETUP_NO_SPAWN")

    marker.write_text(str(os.getpid()))
    assert flow.spawn_bot(lambda *a, **k: spawned.append(a)) is False

    marker.unlink()
    assert flow.spawn_bot(lambda *a, **k: spawned.append(a)) is True
    assert spawned[0][0][-1] == "pyUltroid"


def test_host_port_defaults_and_bad_values(env, monkeypatch):
    bootstrap = importlib.import_module("pyUltroid.webserver.bootstrap")
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("SETUP_BIND", raising=False)
    assert bootstrap.server_host_port() == ("0.0.0.0", 7860)
    monkeypatch.setenv("PORT", "not-a-port")
    assert bootstrap.server_host_port()[1] == 7860
    monkeypatch.setenv("PORT", "1234")
    monkeypatch.setenv("SETUP_BIND", "127.0.0.1")
    assert bootstrap.server_host_port() == ("127.0.0.1", 1234)


def test_banner_rewrites_the_wildcard_host():
    bootstrap = importlib.import_module("pyUltroid.webserver.bootstrap")
    lines = []
    bootstrap.print_setup_url("0.0.0.0", 7860, "TOK", log=lines.append)
    assert any("http://127.0.0.1:7860/?token=TOK" in line for line in lines)
    lines.clear()
    bootstrap.print_setup_url("10.0.0.5", 80, "TOK", log=lines.append)
    assert any("http://10.0.0.5:80/?token=TOK" in line for line in lines)


def test_ensure_returns_immediately_when_configured(env):
    bootstrap = importlib.import_module("pyUltroid.webserver.bootstrap")
    envfile = importlib.import_module("pyUltroid.webserver.envfile")
    envfile.EnvFile(env).write(
        {"API_ID": "1", "API_HASH": "a", "SESSION": "s"}, apply_to_environ=False
    )
    assert bootstrap.ensure_config_or_setup(env) is None
    assert bootstrap.SetupBootstrap(env).ensure() is None


# ── 8. runner ─────────────────────────────────────────────────────────


def test_port_probe_answers_the_bind_question():
    runner = importlib.import_module("pyUltroid.webserver.runner")
    assert runner.bind_available("127.0.0.1", 0) is True
    probe = runner.PortProbe()
    assert probe.bindable("127.0.0.1", 0) is True
    # a privileged port is refused for a non-root process
    assert probe.bindable("127.0.0.1", 1) is False


def test_threaded_server_has_a_real_lifecycle():
    runner = importlib.import_module("pyUltroid.webserver.runner")
    server = runner.ThreadedServer(object(), "127.0.0.1", 45991)
    assert server.is_running() is False
    assert "stopped" in repr(server)
    assert server.thread.daemon is True
    assert server.stop() is False  # never started
    assert server.server.should_exit is True


# ── 9. keepalive ──────────────────────────────────────────────────────


def test_keepalive_config_from_env():
    keepalive = importlib.import_module("pyUltroid.webserver.keepalive")
    assert keepalive.KeepAliveConfig.from_env({}).enabled is False
    off = keepalive.KeepAliveConfig.from_env(
        {"SPACE_URL": "u", "HF_TOKEN": "t", "KEEP_ALIVE": "off"}
    )
    assert off.enabled is False
    on = keepalive.KeepAliveConfig.from_env(
        {"SPACE_URL": "u", "HF_TOKEN": "t", "KEEP_ALIVE_MIN_SECONDS": "1"}
    )
    assert on.enabled is True
    assert on.min_interval == 60  # floored
    assert on.headers["authorization"] == "bearer t"


def test_keepalive_module_does_not_import_requests():
    """``requests`` is an optional extra; importing the module must not need it."""
    code = (
        "import sys, pyUltroid.webserver.keepalive as k;"
        "print('requests' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False"


def test_pinger_stops_between_pings():
    keepalive = importlib.import_module("pyUltroid.webserver.keepalive")
    config = keepalive.KeepAliveConfig(
        url="u", token="t", enabled=True, min_interval=60, max_interval=60
    )
    pings, sleeps = [], []
    pinger = keepalive.KeepAlivePinger(
        config,
        sleeper=lambda seconds: (sleeps.append(seconds), pinger._stop.set()),
        ping=lambda _c: pings.append(1),
    )
    pinger.run()
    assert pings == [1] and sleeps == [60]
    assert pinger.running is False


def test_pinger_reports_a_failing_ping_without_raising():
    keepalive = importlib.import_module("pyUltroid.webserver.keepalive")

    def explode(_config):
        raise RuntimeError("network down")

    pinger = keepalive.KeepAlivePinger(
        keepalive.KeepAliveConfig(url="u", token="t", enabled=True),
        sleeper=lambda _s: None,
        ping=explode,
    )
    assert pinger.ping_once() is False


def test_disabled_pinger_does_nothing():
    keepalive = importlib.import_module("pyUltroid.webserver.keepalive")
    pings = []
    pinger = keepalive.KeepAlivePinger(
        keepalive.KeepAliveConfig(url=None, token=None, enabled=False),
        sleeper=lambda _s: None,
        ping=lambda _c: pings.append(1),
    )
    pinger.run()
    assert pings == []


# ── 10. app + miniapp (need the optional deps) ────────────────────────


@NEEDS_FASTAPI
@NEEDS_TELETHON
def test_setup_application_exposes_the_documented_routes(env):
    app = importlib.import_module("pyUltroid.webserver.app")
    setup = app.SetupApplication(token="tok-abc", env_path=env, standalone=False)
    built = setup.build()
    paths = {route.path for route in built.routes if hasattr(route, "path")}
    for expected in (
        "/status",
        "/",
        "/api/state",
        "/api/setup/keys",
        "/api/login/phone",
        "/api/login/otp",
        "/api/login/qr",
        "/api/login/qr/{login_id}/wait",
        "/api/login/qr/{login_id}/password",
        "/api/login/qr/{login_id}/image",
        "/api/logs",
        "/api/setup/complete",
    ):
        assert expected in paths, expected


@NEEDS_FASTAPI
def test_log_stream_bridge_stays_attached_for_the_app_lifetime(env):
    """``build()`` must leave the hub wired up: that IS the log stream feed.

    Regression guard: detaching right after taking the broker looks tidy
    and silently kills every live line on ``/api/logs``.
    """
    import logging

    app_module = importlib.import_module("pyUltroid.webserver.app")
    logs = importlib.import_module("pyUltroid.webserver.logs")
    root = logging.getLogger()
    before = len(root.handlers)
    setup = app_module.SetupApplication(token="tok-log", env_path=env, standalone=False)
    try:
        setup.build()
        assert len(root.handlers) == before + 1
        assert setup.hub.attached is True

        async def scenario():
            async with setup.broker.subscription() as queue_:
                root.info("live-line-for-test")
                return await asyncio.wait_for(queue_.get(), 5)

        assert "live-line-for-test" in asyncio.run(scenario())
        assert setup.broker.subscriber_count == 0
    finally:
        assert setup.close() is True
    assert len(root.handlers) == before
    assert setup.hub.attached is False


@NEEDS_FASTAPI
def test_sse_frame_renders_both_payload_shapes():
    app = importlib.import_module("pyUltroid.webserver.app")
    assert str(app.SseFrame("log", "plain")) == "event: log\ndata: plain\n\n"
    assert str(app.SseFrame("e", {"a": 1})) == 'event: e\ndata: {"a": 1}\n\n'


@NEEDS_FASTAPI
@NEEDS_TELETHON
def test_login_error_mapper_covers_the_documented_statuses():
    app = importlib.import_module("pyUltroid.webserver.app")
    telethon_errors = importlib.import_module("telethon.errors")
    mapper = app.LoginErrorMapper()
    assert mapper.describe(telethon_errors.PhoneNumberInvalidError())[0] == 400
    assert mapper.describe(telethon_errors.PhoneCodeExpiredError())[0] == 400
    assert mapper.describe(telethon_errors.FloodWaitError())[0] == 429
    assert mapper.describe(ValueError())[0] == 502
    assert mapper.response(ValueError()).status_code == 502


@NEEDS_FASTAPI
def test_miniapp_falls_back_instead_of_raising_nameerror():
    """The regression: ``_fallback_kv`` was deleted while still referenced."""
    miniapp = importlib.import_module("pyUltroid.webserver.miniapp")
    store = miniapp.MiniAppStore(None, None)
    assert isinstance(store.db, miniapp.MemoryStore)
    assert store.live_db is False
    token = store.ensure_token()
    assert isinstance(token, str) and len(token) > 20
    assert store.ensure_token() == token
    assert store.rotate_token() != token


@NEEDS_FASTAPI
def test_miniapp_store_validates_and_cycles_preferences():
    miniapp = importlib.import_module("pyUltroid.webserver.miniapp")
    store = miniapp.MiniAppStore(miniapp.MemoryStore(), None)
    assert store.ui_state() == {"theme": "default", "cards": True, "buttons": "classic"}
    assert store.cycle_theme() == "dark"
    assert store.cycle_theme() == "default"
    assert store.cycle_buttons() == "styled"
    assert store.flip_cards() is False
    assert store.flip_cards() is True


@NEEDS_FASTAPI
def test_miniapp_skips_a_malformed_manifest_entry():
    miniapp = importlib.import_module("pyUltroid.webserver.miniapp")

    class Registry:
        _entries = {
            "a.b": {"type": "toggle", "label": "B", "plugin": "p"},
            "c.c": {"type": "command"},
        }

        def get_state(self, key):
            return key == "a.b"

    rows = miniapp.MiniAppStore(miniapp.MemoryStore(), Registry()).toggle_rows()
    assert [row["key"] for row in rows] == ["a.b"]
    assert rows[0]["enabled"] is True


@NEEDS_FASTAPI
def test_miniapp_supervisors_are_independent():
    miniapp = importlib.import_module("pyUltroid.webserver.miniapp")
    a, b = miniapp.MiniAppServer(), miniapp.MiniAppServer()
    assert a is not b
    assert a.server is None and b.server is None
    assert a.ensure(None, "127.0.0.1", 1) is None  # port refused, no raise
    assert a.running() is False


@NEEDS_FASTAPI
def test_memory_store_behaves_like_udb():
    miniapp = importlib.import_module("pyUltroid.webserver.miniapp")
    store = miniapp.MemoryStore()
    assert store.get_key("x") is None
    store.set_key("x", 1)
    assert store.get_key("x") == 1
    store.del_key("x")
    assert store.get_key("x") is None
    store.set_key("y", 2)
    store.clear()
    assert store.get_key("y") is None


# ── 11. parity: every module function is a thin alias over a class ────


def test_module_functions_delegate_to_the_classes():
    mods = modules()
    security = mods["security"]
    assert security.token_matches("a", "a") is True
    assert security.extract_token(
        types.SimpleNamespace(headers={}, query_params={"token": "t"})
    ) == "t"

    runner = mods["runner"]
    assert runner.make_server(object(), "127.0.0.1", 45992) is not None

    logs = mods["logs"]
    broker, handler = logs.attach_broker()
    logging.getLogger().removeHandler(handler)
    assert handler.broker is broker


def test_classes_are_exported_from_the_package():
    ws = importlib.import_module("pyUltroid.webserver")
    for name in (
        "EnvFile",
        "TokenAuthority",
        "SecretRedactor",
        "RateLimiter",
        "PortProbe",
        "ThreadedServer",
        "LogBroker",
        "LogStreamHub",
        "SetupState",
        "ConfigGate",
        "SetupBootstrap",
        "KeepAliveConfig",
        "KeepAlivePinger",
        "AUTHORITY",
        "BOOTSTRAP",
    ):
        assert getattr(ws, name) is not None, name


def test_each_class_has_its_own_shape():
    """No shared base class: every class was designed independently."""
    plan = [
        ("security", "SecretRedactor"),
        ("security", "RateLimiter"),
        ("security", "TokenAuthority"),
        ("envfile", "EnvFile"),
        ("runner", "PortProbe"),
        ("runner", "ThreadedServer"),
        ("logs", "LogBroker"),
        ("logs", "LogStreamHub"),
        ("state", "SetupState"),
        ("bootstrap", "ConfigGate"),
        ("bootstrap", "SetupBootstrap"),
        ("keepalive", "KeepAliveConfig"),
        ("keepalive", "KeepAlivePinger"),
    ]
    if has("telethon"):
        plan += [("login", "LoginManager"), ("login", "SessionFactory")]
    if has("fastapi"):
        plan += [
            ("app", "SseFrame"),
            ("app", "LoginErrorMapper"),
            ("app", "SetupApplication"),
            ("miniapp", "MemoryStore"),
            ("miniapp", "MiniAppStore"),
            ("miniapp", "MiniAppServer"),
            ("miniapp", "MiniAppApplication"),
        ]
    seen: dict[str, type] = {}
    for module_name, class_name in plan:
        module = importlib.import_module(f"pyUltroid.webserver.{module_name}")
        cls = getattr(module, class_name)
        assert class_name not in seen, f"{class_name} is duplicated"
        seen[class_name] = cls
        # no class inherits from another class in the layer
        for other in seen.values():
            if other is cls:
                continue
            assert not issubclass(cls, other), f"{cls} subclasses {other}"

@NEEDS_FASTAPI
def test_miniapp_store_adopts_the_live_udb(monkeypatch):
    """Regression: an alias-case slip made the Mini App *always* fall back.

    ``from pyUltroid import udB as _udB`` binds ``_udB``; the body then read
    ``_udB``. The NameError was swallowed by the surrounding ``except``, so
    the dashboard silently ran on an ephemeral store even inside a running
    bot - and ``live_db`` was reported False.
    """
    import pyUltroid as root
    from pyUltroid.webserver import miniapp

    sentinel = types.SimpleNamespace(sentinel=True)
    monkeypatch.setattr(root, "udB", sentinel, raising=False)
    store = miniapp.MiniAppStore(None, None)
    assert store.db is sentinel
    assert store.live_db is True

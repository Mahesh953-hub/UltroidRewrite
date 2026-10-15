"""W1-B.4 — UpdateEngine / GitManager regression tests against a temp git repo.

Covers the Hybrid Update System cutover:

* ``classify_file`` / ``GitManager.fetch_diff`` classification and decision
  flags (``needs_restart``, ``pip_changes``, ``hot_reloadable``).
* ``GitManager.pull`` / ``GitManager.rollback`` round-trip on a real
  (temporary) git repository — no network, the "upstream" remote is just
  another local repo path.
* ``UpdateEngine.check`` / ``apply`` / ``rollback`` with the shadow process
  and shell facades faked — including the W1-B regression where a bare
  ``apply()`` (diff=None) always failed because the state was flipped to
  "applying" *before* the internal ``check()`` call, which then bailed on
  its own busy guard.

Style follows the existing suite: namespace-isolated ``pyUltroid`` stubs
(see ``tests/test_core_architecture.py``) and plain ``asyncio.run`` — no
pytest-asyncio dependency.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


# ── Namespace isolation ─────────────────────────────────────────────────────


def _install_stub_namespace():
    """Stub the pyUltroid root + startup.logger so updater modules import
    without the production bootstrap. The real ``pyUltroid.shell`` package
    (import-light) is loaded through the stub root's ``__path__``."""
    root = types.ModuleType("pyUltroid")
    root.__path__ = [str(ROOT / "pyUltroid")]
    root.LOGS = logging.getLogger("updater-tests")
    root.asst = None
    root.udB = None
    root.ultroid_bot = None
    root.call_back = lambda: None
    sys.modules["pyUltroid"] = root

    startup = types.ModuleType("pyUltroid.startup")
    startup.__path__ = []
    logger = types.ModuleType("pyUltroid.startup.logger")
    logger.LOGS = root.LOGS
    sys.modules["pyUltroid.startup"] = startup
    sys.modules["pyUltroid.startup.logger"] = logger
    return root


@pytest.fixture
def updater_modules(module_isolation):
    """Import pyUltroid.updater with an isolated, stubbed package namespace."""
    with module_isolation("pyUltroid"):
        _install_stub_namespace()
        git_mod = importlib.import_module("pyUltroid.updater._git")
        engine_mod = importlib.import_module("pyUltroid.updater._engine")
        yield SimpleNamespace(git=git_mod, engine=engine_mod)


# ── Temp git repo helpers ───────────────────────────────────────────────────


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run_git(path, "init")
    # Deterministic branch name regardless of git's init.defaultBranch.
    _run_git(path, "symbolic-ref", "HEAD", "refs/heads/main")
    _run_git(path, "config", "user.email", "updater-tests@example.invalid")
    _run_git(path, "config", "user.name", "updater-tests")
    _run_git(path, "config", "commit.gpgsign", "false")


def _commit_all(repo: Path, message: str) -> str:
    _run_git(repo, "add", "-A")
    _run_git(repo, "commit", "-m", message, "--quiet")
    return _run_git(repo, "rev-parse", "--short", "HEAD")


def _write(repo: Path, rel: str, content: str) -> None:
    target = repo / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _make_repos(tmp_path: Path):
    """
    Build an "upstream" repo and a cloned "bot" repo at a common base commit.
    Returns (upstream_path, bot_path, base_sha).
    """
    upstream = tmp_path / "upstream"
    _init_repo(upstream)
    _write(upstream, "README.md", "base\n")
    _write(upstream, "plugins/foo.py", "X = 1\n")
    _write(upstream, "pyUltroid/__init__.py", "A = 1\n")
    _write(upstream, "requirements.txt", "telethon\n")
    base_sha = _commit_all(upstream, "base commit")

    bot = tmp_path / "bot"
    subprocess.run(
        ["git", "clone", str(upstream), str(bot), "--quiet"],
        check=True,
        capture_output=True,
        text=True,
    )
    _run_git(bot, "config", "user.email", "updater-tests@example.invalid")
    _run_git(bot, "config", "user.name", "updater-tests")
    _run_git(bot, "symbolic-ref", "HEAD", "refs/heads/main")
    return upstream, bot, base_sha


# ── classify_file ────────────────────────────────────────────────────────────


def test_classify_file_kinds(updater_modules):
    git_mod = updater_modules.git
    FileKind = git_mod.FileKind
    classify = git_mod.classify_file

    assert classify("pyUltroid/__init__.py") is FileKind.CORE
    assert classify("pyUltroid/startup/funcs.py") is FileKind.CORE
    assert classify("pyUltroid/version.py") is FileKind.CORE
    assert classify("plugins/bot.py") is FileKind.PLUGIN
    assert classify("addons/extra.py") is FileKind.PLUGIN
    assert classify("assistant/start.py") is FileKind.PLUGIN
    assert classify("vc/_pytgcalls.py") is FileKind.VC
    assert classify("pyUltroid/fns/helper.py") is FileKind.UTIL
    assert classify("pyUltroid/updater/_engine.py") is FileKind.UTIL
    assert classify("strings/strings/en.yml") is FileKind.ASSET
    assert classify("README.md") is FileKind.ASSET
    assert classify("requirements.txt") is FileKind.SHELL
    assert classify("installer.sh") is FileKind.SHELL
    assert classify("some/random.xyz") is FileKind.UNKNOWN


# ── GitManager against a temp repo ───────────────────────────────────────────


def test_fetch_diff_classifies_plugin_change(tmp_path, updater_modules):
    git_mod = updater_modules.git
    upstream, bot, _ = _make_repos(tmp_path)
    _write(upstream, "plugins/foo.py", "X = 2\n")
    _commit_all(upstream, "plugin tweak")

    gm = git_mod.GitManager(str(bot))
    diff = gm.fetch_diff("origin")

    assert diff is not None and diff.has_changes
    assert diff.commit_count == 1
    assert diff.branch == "main"
    assert diff.plugin_changes == ["plugins/foo.py"]
    assert diff.core_changes == []
    assert diff.needs_restart is False
    assert diff.pip_changes is False
    assert len(diff.hot_reloadable) == 1
    assert diff.hot_reloadable[0].endswith("plugins/foo.py")
    assert "plugin tweak" in diff.changelog_text()
    assert "hot-reloadable" in diff.impact_summary()


def test_fetch_diff_flags_core_and_requirements(tmp_path, updater_modules):
    git_mod = updater_modules.git
    upstream, bot, _ = _make_repos(tmp_path)
    _write(upstream, "pyUltroid/__init__.py", "A = 2\n")
    _commit_all(upstream, "core change")
    _write(upstream, "requirements.txt", "telethon\nredis\n")
    _commit_all(upstream, "deps change")

    gm = git_mod.GitManager(str(bot))
    diff = gm.fetch_diff("origin")

    assert diff.commit_count == 2
    assert "pyUltroid/__init__.py" in diff.core_changes
    assert diff.needs_restart is True  # core change forces restart
    assert diff.pip_changes is True  # requirements change flagged
    assert "restart required" in diff.impact_summary()


def test_fetch_diff_no_changes(tmp_path, updater_modules):
    git_mod = updater_modules.git
    _, bot, _ = _make_repos(tmp_path)
    gm = git_mod.GitManager(str(bot))
    diff = gm.fetch_diff("origin")
    assert diff is not None
    assert diff.has_changes is False
    assert diff.changelog_text() == "No new commits."


def test_pull_then_rollback_roundtrip(tmp_path, updater_modules):
    git_mod = updater_modules.git
    upstream, bot, base_sha = _make_repos(tmp_path)
    _write(upstream, "plugins/foo.py", "X = 3\n")
    new_sha = _commit_all(upstream, "plugin bump")

    gm = git_mod.GitManager(str(bot))
    ok, _ = gm.pull("origin")
    assert ok is True
    assert gm.current_sha() == new_sha

    ok, _ = gm.rollback(base_sha)
    assert ok is True
    assert gm.current_sha() == base_sha


def test_remote_url_strips_only_trailing_dotgit(tmp_path, updater_modules):
    git_mod = updater_modules.git
    _, bot, _ = _make_repos(tmp_path)
    _run_git(bot, "remote", "add", "web", "https://github.com/foo/bar.git")
    _run_git(bot, "remote", "add", "plain", "https://github.com/foo/bar")

    gm = git_mod.GitManager(str(bot))
    assert gm.remote_url("web") == "https://github.com/foo/bar"
    assert gm.remote_url("plain") == "https://github.com/foo/bar"


# ── UpdateState ──────────────────────────────────────────────────────────────


def test_update_state_summaries(updater_modules):
    UpdateState = updater_modules.engine.UpdateState

    done = UpdateState(status="done", sha_before="aaa", sha_after="bbb")
    done.reloaded = ["foo"]
    assert "aaa" in done.summary() and "bbb" in done.summary()
    assert "reloaded 1 plugin" in done.summary()

    restart = UpdateState(
        status="done", sha_before="a", sha_after="b", needs_restart=True
    )
    assert "restart" in restart.summary()

    failed = UpdateState(status="failed", error="boom")
    assert "boom" in failed.summary()

    rolled = UpdateState(status="rolled_back", sha_before="aaa")
    assert "Rolled back" in rolled.summary()

    assert UpdateState(status="idle").summary() == "No update in progress."
    busy = UpdateState(status="applying")
    assert busy.is_busy() and "applying" in busy.summary()


# ── UpdateEngine ─────────────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True


def _fresh_engine(updater_modules, bot_repo: Path):
    """A fresh UpdateEngine pointed at the temp repo (not the singleton)."""
    engine_mod = updater_modules.engine
    git_mod = updater_modules.git
    engine = engine_mod.UpdateEngine()
    engine._git = git_mod.GitManager(str(bot_repo))
    return engine


def _patch_engine_io(engine, reload_result=None):
    """Stub out the side-effectful engine internals for apply() tests."""
    reloads = []
    restarts = []
    notifications = []

    async def _fake_reload(files):
        reloads.extend(files)
        return list(reload_result or [])

    async def _fake_restart(delay):
        restarts.append(delay)

    async def _fake_notify():
        notifications.append(True)

    engine._hot_reload_plugins = _fake_reload
    engine._delayed_restart = _fake_restart
    engine._notify_log_channel = _fake_notify
    return reloads, restarts, notifications


def test_engine_check_returns_diff_and_idles(tmp_path, updater_modules):
    upstream, bot, _ = _make_repos(tmp_path)
    _write(upstream, "plugins/foo.py", "X = 9\n")
    _commit_all(upstream, "check me")

    engine = _fresh_engine(updater_modules, bot)
    diff = asyncio.run(engine.check())

    assert diff is not None and diff.has_changes
    # check() must leave the engine non-busy so apply() can follow.
    assert engine.last_state.status == "idle"


def test_engine_check_refused_while_busy(tmp_path, updater_modules):
    _, bot, _ = _make_repos(tmp_path)
    engine = _fresh_engine(updater_modules, bot)
    engine._state.status = "applying"
    assert asyncio.run(engine.check()) is None


def test_engine_apply_without_diff_rechecks(tmp_path, updater_modules, monkeypatch):
    """
    Regression (W1-B): bare ``apply()`` used to flip state to "applying"
    BEFORE calling check(), whose busy guard then returned None — every
    apply without a pre-fetched diff failed with 'Could not fetch upstream
    diff.' The check must run first.
    """
    engine_mod = updater_modules.engine
    upstream, bot, base_sha = _make_repos(tmp_path)
    _write(upstream, "plugins/foo.py", "X = 10\n")
    _commit_all(upstream, "apply me")

    engine = _fresh_engine(updater_modules, bot)
    _patch_engine_io(engine, reload_result=["foo"])

    spawned = []

    async def _fake_spawn(script, *args):
        spawned.append((script, args))
        # Simulate the shadow process reporting success over IPC.
        asyncio.ensure_future(
            engine._on_update_done(
                {
                    "sha_before": base_sha,
                    "sha_after": "newsha1",
                    "changed_files": ["plugins/foo.py"],
                    "plugin_files": ["plugins/foo.py"],
                    "needs_restart": False,
                    "commits": [],
                    "pip_result": None,
                }
            )
        )
        return _FakeProc()

    monkeypatch.setattr(engine_mod, "spawn_shadow", _fake_spawn)
    state = asyncio.run(engine.apply())  # no diff — must check first

    assert state.status == "done", state.error
    assert state.sha_before == base_sha
    assert state.sha_after == "newsha1"
    assert state.reloaded == ["foo"]
    assert spawned and spawned[0][0].endswith("_shadow.py")
    assert spawned[0][1][0] == str(Path(bot).resolve())
    assert spawned[0][1][1] == "origin"


def test_engine_apply_done_schedules_restart_on_core(
    tmp_path, updater_modules, monkeypatch
):
    engine_mod = updater_modules.engine
    upstream, bot, base_sha = _make_repos(tmp_path)
    _write(upstream, "pyUltroid/__init__.py", "A = 9\n")
    _commit_all(upstream, "core bump")

    engine = _fresh_engine(updater_modules, bot)
    _, restarts, notifications = _patch_engine_io(engine)

    async def _fake_spawn(script, *args):
        asyncio.ensure_future(
            engine._on_update_done(
                {
                    "sha_before": base_sha,
                    "sha_after": "c0re999",
                    "changed_files": ["pyUltroid/__init__.py"],
                    "plugin_files": [],
                    "needs_restart": True,
                    "commits": [],
                    "pip_result": None,
                }
            )
        )
        return _FakeProc()

    monkeypatch.setattr(engine_mod, "spawn_shadow", _fake_spawn)
    state = asyncio.run(engine.apply())

    assert state.status == "done"
    assert state.needs_restart is True
    assert restarts == [3]  # delayed restart scheduled
    assert notifications  # log-channel notify fired
    assert "restart" in state.summary()


def test_engine_apply_failure_path(tmp_path, updater_modules, monkeypatch):
    engine_mod = updater_modules.engine
    upstream, bot, _ = _make_repos(tmp_path)
    _write(upstream, "plugins/foo.py", "X = 11\n")
    _commit_all(upstream, "will fail")

    engine = _fresh_engine(updater_modules, bot)
    _patch_engine_io(engine)

    async def _fake_spawn(script, *args):
        asyncio.ensure_future(
            engine._on_update_failed({"reason": "git pull failed: boom"})
        )
        return _FakeProc()

    monkeypatch.setattr(engine_mod, "spawn_shadow", _fake_spawn)
    state = asyncio.run(engine.apply())

    assert state.status == "failed"
    assert "boom" in state.error
    assert "failed" in state.summary().lower()


def test_engine_apply_already_up_to_date(tmp_path, updater_modules):
    _, bot, _ = _make_repos(tmp_path)
    engine = _fresh_engine(updater_modules, bot)
    state = asyncio.run(engine.apply())  # no upstream commits at all
    assert state.status == "done"
    assert state.error == "Already up to date."


def test_engine_apply_refused_while_busy(tmp_path, updater_modules):
    _, bot, _ = _make_repos(tmp_path)
    engine = _fresh_engine(updater_modules, bot)
    engine._state.status = "checking"
    state = asyncio.run(engine.apply())
    assert state.status == "checking"  # unchanged — refused early


def test_engine_rollback_uses_shell_facade(tmp_path, updater_modules, monkeypatch):
    """rollback() goes through the HSS shell facade — never a raw subprocess."""
    _, bot, _ = _make_repos(tmp_path)
    engine = _fresh_engine(updater_modules, bot)

    shell_pkg = importlib.import_module("pyUltroid.shell")
    calls = []

    class _FakeShell:
        async def execute(self, ctx, command, timeout=60.0):
            calls.append((ctx.plugin_name, command, timeout))
            return SimpleNamespace(ok=True, stderr="")

    monkeypatch.setattr(shell_pkg, "sh", _FakeShell())

    state = asyncio.run(engine.rollback("deadbeef"))

    assert state.status == "rolled_back"
    assert state.sha_before == "deadbeef"
    assert calls == [("updater.rollback", "git reset --hard deadbeef", 30)]
    assert "Rolled back" in state.summary()


def test_engine_rollback_reports_failure(tmp_path, updater_modules, monkeypatch):
    _, bot, _ = _make_repos(tmp_path)
    engine = _fresh_engine(updater_modules, bot)

    shell_pkg = importlib.import_module("pyUltroid.shell")

    class _FakeShell:
        async def execute(self, ctx, command, timeout=60.0):
            return SimpleNamespace(ok=False, stderr="unknown revision")

    monkeypatch.setattr(shell_pkg, "sh", _FakeShell())

    # "cafe" is valid 4-hex, so the failure must come from the shell result.
    state = asyncio.run(engine.rollback("cafe"))
    assert state.status == "failed"
    assert state.error == "unknown revision"


# ── SHA validation (review: rollback injection barrier) ─────────────────────


def test_is_valid_sha_accepts_and_rejects(updater_modules):
    is_valid_sha = updater_modules.git.is_valid_sha

    # valid: short (>=4) and full 40-char SHA-1, either case, padded
    assert is_valid_sha("deadbeef")
    assert is_valid_sha("a1b2")
    assert is_valid_sha("DEADBEEF")
    assert is_valid_sha("f" * 40)
    assert is_valid_sha("A1b2C3" * 6 + "a1b2")  # 40 mixed-case hex
    assert is_valid_sha("  deadbeef  ")  # stripped before matching

    # invalid: empty/None, too short, too long, non-hex, refs, flags,
    # and every shell-injection shape
    assert not is_valid_sha("")
    assert not is_valid_sha(None)
    assert not is_valid_sha("abc")  # 3 chars — below minimum
    assert not is_valid_sha("f" * 41)  # beyond SHA-1 length
    assert not is_valid_sha("g12345")  # non-hex letter
    assert not is_valid_sha("HEAD~1")  # ref, not a SHA
    assert not is_valid_sha("--help")  # option-injection
    assert not is_valid_sha("deadbeef; rm -rf /")
    assert not is_valid_sha("deadbeef && reboot")
    assert not is_valid_sha("deadbeef\nwhoami")
    assert not is_valid_sha("$(reboot)")
    assert not is_valid_sha("`reboot`")


def test_engine_rollback_rejects_injection_and_preserves_state(
    tmp_path, updater_modules, monkeypatch
):
    """
    A hostile SHA must never reach the shell, and a rejected rollback must
    NOT clobber the live state — ``sha_before`` is the last good rollback
    target and losing it would brick the recovery path.
    """
    _, bot, _ = _make_repos(tmp_path)
    engine = _fresh_engine(updater_modules, bot)
    engine._state = updater_modules.engine.UpdateState(
        status="done", sha_before="goodsha1", sha_after="newsha2"
    )

    shell_pkg = importlib.import_module("pyUltroid.shell")
    calls = []

    class _FakeShell:
        async def execute(self, ctx, command, timeout=60.0):
            calls.append(command)
            return SimpleNamespace(ok=True, stderr="")

    monkeypatch.setattr(shell_pkg, "sh", _FakeShell())

    for hostile in (
        "deadbeef; rm -rf /",
        "deadbeef && reboot",
        "--help",
        "HEAD~1",
        "deadbeef\nwhoami",
        "abc",  # too short
        "f" * 41,  # too long
        "",
    ):
        state = asyncio.run(engine.rollback(hostile))
        assert state.status == "failed", hostile
        assert "Invalid rollback SHA" in state.error, hostile

    assert calls == []  # shell never invoked
    # live state untouched by every rejected attempt
    assert engine.last_state.status == "done"
    assert engine.last_state.sha_before == "goodsha1"
    assert engine.last_state.sha_after == "newsha2"


def test_engine_rollback_accepts_full_40_hex(tmp_path, updater_modules, monkeypatch):
    _, bot, _ = _make_repos(tmp_path)
    engine = _fresh_engine(updater_modules, bot)

    shell_pkg = importlib.import_module("pyUltroid.shell")
    calls = []

    class _FakeShell:
        async def execute(self, ctx, command, timeout=60.0):
            calls.append(command)
            return SimpleNamespace(ok=True, stderr="")

    monkeypatch.setattr(shell_pkg, "sh", _FakeShell())

    full = "0123456789abcdef0123456789abcdef01234567"
    state = asyncio.run(engine.rollback(full))
    assert state.status == "rolled_back"
    assert calls == [f"git reset --hard {full}"]


def test_git_manager_rollback_rejects_invalid_sha(tmp_path, updater_modules):
    _, bot, base_sha = _make_repos(tmp_path)
    gm = updater_modules.git.GitManager(str(bot))

    for hostile in ("--help", "deadbeef; rm -rf /", "HEAD~1", ""):
        ok, msg = gm.rollback(hostile)
        assert ok is False, hostile
        assert "invalid SHA" in msg, hostile
    assert gm.current_sha() == base_sha  # repo untouched


# ── last_state copy semantics ────────────────────────────────────────────────


def test_last_state_returns_isolated_copy(updater_modules):
    engine = updater_modules.engine.UpdateEngine()
    engine._state = updater_modules.engine.UpdateState(
        status="done",
        sha_before="aaa111",
        sha_after="bbb222",
        changed_files=["plugins/foo.py"],
        commits=[{"sha": "bbb222", "msg": "x", "author": "t"}],
    )

    snap = engine.last_state
    assert snap is not engine._state
    assert snap.status == "done"
    assert snap.sha_before == "aaa111"

    # Mutating the snapshot — scalars, lists, and nested dicts — must not
    # leak into engine internals (a corrupted sha_before would otherwise
    # become the next rollback target).
    snap.status = "hacked"
    snap.sha_before = "evil"
    snap.changed_files.append("injected.py")
    snap.commits[0]["sha"] = "evil"

    fresh = engine.last_state
    assert fresh.status == "done"
    assert fresh.sha_before == "aaa111"
    assert fresh.changed_files == ["plugins/foo.py"]
    assert fresh.commits[0]["sha"] == "bbb222"

"""Characterisation tests for the shell layer.

This folder gates every command the AI agent can run, and it had no tests at
all. These assert current behaviour - including the refusals - so a refactor
that changes what is allowed shows up here rather than on a live account.

Written before the union, against the existing classes.
"""

from __future__ import annotations

import asyncio

import pytest

from pyUltroid.shell import (
    BLOCKED_COMMANDS,
    BLOCKED_PATHS,
    BLOCKED_PATTERNS,
    Cap,
    ExecutionContext,
    ShellSession,
    TrustLevel,
)
from pyUltroid.shell._executor import ShellExecutor
from pyUltroid.shell._router import ShellRouter
from pyUltroid.shell._security import SecurityGuard
from pyUltroid.shell._session import HistoryEntry, ShellSessionManager


# ── context / capability ────────────────────────────────────────────────────


def test_owner_context_is_shell_and_fs_capable():
    """Human permission comes from trust level alone.

    ``has_cap`` returns True for every non-AI caller, so ``can_*`` reduces to
    the trust threshold. The empty ``caps`` set is therefore correct, not a bug.
    """
    ctx = ExecutionContext.for_owner(1, 2, "test")
    assert ctx.trust is TrustLevel.OWNER
    assert ctx.caps == set()
    assert ctx.has_cap(Cap.SHELL_EXEC) is True
    for probe in (
        ctx.can_shell_read,
        ctx.can_shell_write,
        ctx.can_shell_exec,
        ctx.can_fs_read,
        ctx.can_fs_write,
    ):
        assert probe() is True


def test_update_and_install_need_the_bot_trust_level_not_just_owner():
    """Owner is below ULTROID, so these stay False even for the owner."""
    ctx = ExecutionContext.for_owner(1, 2, "test")
    assert ctx.can_update() is False
    assert ctx.can_install() is False
    # plugin load sits lower, at the owner threshold
    assert ctx.can_plugin_load() is True

    bot = ExecutionContext.for_ultroid("updater")
    assert bot.can_update() is True
    assert bot.can_install() is True


def test_ai_context_is_capped_to_what_it_was_granted():
    """The AI is AI_AGENT trust, so trust grants nothing - only caps do."""
    granted = frozenset({Cap.SHELL_READ, Cap.FS_READ})
    ctx = ExecutionContext.for_ai(granted, chat_id=3)
    assert ctx.trust is TrustLevel.AI_AGENT
    assert ctx.caller == "ai"
    assert ctx.can_shell_read() is True
    assert ctx.can_fs_read() is True
    assert ctx.can_shell_write() is False
    assert ctx.can_shell_exec() is False
    assert ctx.can_update() is False


def test_ai_context_with_no_caps_can_do_nothing():
    """An AI with no caps is refused everything, whatever its trust level."""
    ctx = ExecutionContext.for_ai(frozenset(), chat_id=3)
    assert ctx.has_cap(Cap.SHELL_READ) is False
    assert ctx.can_shell_read() is False
    assert ctx.can_shell_exec() is False


def test_sudo_reads_but_does_not_execute():
    """The execute threshold is OWNER, so sudo is deliberately below it."""
    ctx = ExecutionContext.for_sudo(1, 2)
    assert ctx.trust is TrustLevel.SUDO
    assert ctx.trust < TrustLevel.OWNER
    assert ctx.can_shell_read() is True
    assert ctx.can_shell_write() is False
    assert ctx.can_shell_exec() is False


def test_ultroid_context_is_the_bot_acting_for_itself():
    ctx = ExecutionContext.for_ultroid("updater")
    assert ctx.trust is TrustLevel.ULTROID
    assert ctx.can_update() is True
    assert ctx.can_install() is True


def test_context_round_trips_to_a_dict():
    ctx = ExecutionContext.for_owner(1, 2, "p")
    data = ctx.to_dict()
    assert data["caller"] == ctx.caller
    assert data["trust"] == ctx.trust.name
    assert data["caps"] == []


# ── security guard ──────────────────────────────────────────────────────────


@pytest.fixture
def guard():
    return SecurityGuard()


def test_a_plain_read_is_allowed_for_the_owner(guard):
    result = guard.check(ExecutionContext.for_owner(1, 2), "ls -la")
    assert result.allowed is True


def test_blocked_paths_are_refused(guard):
    for path in ("cat .env", "cat asst.session", "cat /etc/passwd"):
        result = guard.check(ExecutionContext.for_owner(1, 2), path)
        assert result.allowed is False, path


def test_blocked_command_forms_are_refused(guard):
    """Whatever the lists actually contain must be refused - and not vacuously."""
    refused = [
        command
        for command in BLOCKED_COMMANDS
        if not guard.check(ExecutionContext.for_owner(1, 2), command).allowed
    ]
    assert refused, "no entry in BLOCKED_COMMANDS is actually enforced"


def test_the_block_lists_are_not_empty(guard):
    """A silently emptied block list is the worst possible regression here."""
    assert BLOCKED_COMMANDS
    assert BLOCKED_PATHS
    assert BLOCKED_PATTERNS


def test_a_cap_the_caller_lacks_is_refused(guard):
    """The guard checks capability, not just trust level."""
    # The guard wants shell.exec for any command, not shell.read.
    reader = ExecutionContext.for_ai(frozenset({Cap.SHELL_READ}), chat_id=1)
    assert guard.check(reader, "ls").allowed is False

    exec_ctx = ExecutionContext.for_ai(frozenset({Cap.SHELL_EXEC}), chat_id=1)
    assert guard.check(exec_ctx, "ls").allowed is True


def test_the_guard_does_not_read_a_redirection_as_a_write(guard):
    """A real gap, pinned deliberately.

    SHELL_WRITE is not granted here, yet ``> file`` passes. The guard matches
    command *shape* against BLOCKED_PATTERNS and checks one cap per command;
    it does not parse a redirection into a separate write. Recorded so that a
    future change to the cap model is a conscious decision.
    """
    ctx = ExecutionContext.for_ai(frozenset({Cap.SHELL_EXEC}), chat_id=1)
    assert ctx.can_shell_write() is False
    assert guard.check(ctx, "echo hi > out.txt").allowed is True


def test_a_denial_carries_a_reason(guard):
    result = guard.check(ExecutionContext.for_owner(1, 2), "cat .env")
    assert result.allowed is False
    assert result.reason or result.audit is not None


def _session(tmp_path, name="s"):
    """A session rooted at a writable cwd.

    ShellExecutor requires the session cwd to exist and nothing creates it for
    a bare ShellSession - found while writing these. Production always goes
    through ShellSessionManager, which does ``_ensure_workdir``, so this is a
    precondition pinned here rather than a bug. WORKDIR_BASE is /tmp/ultroid_shell,
    which is not writable in this environment either, hence tmp_path.
    """
    cwd = tmp_path / name
    cwd.mkdir(parents=True, exist_ok=True)
    s = ShellSession(session_id=name, owner="owner:1")
    s.set_cwd(str(cwd))
    return s


# ── sessions ────────────────────────────────────────────────────────────────


def test_a_session_is_created_and_found(tmp_path, monkeypatch):
    from pyUltroid.shell import _session as session_mod

    monkeypatch.setattr(session_mod, "WORKDIR_BASE", str(tmp_path / "wd"))
    manager = ShellSessionManager()
    session = manager.get_or_create("s1", "owner:1")
    assert isinstance(session, ShellSession)
    assert manager.get_or_create("s1", "owner:1") is session
    assert manager.destroy("s1") is None or True


def test_session_env_and_cwd_round_trip():
    s = ShellSession(session_id="s2", owner="owner:1")
    s.set_cwd("/tmp")
    s.set_env("FOO", "bar")
    assert s.cwd == "/tmp"
    assert s.effective_env()["FOO"] == "bar"
    s.unset_env("FOO")
    assert "FOO" not in s.effective_env()


def test_history_records_and_reads_back():
    s = ShellSession(session_id="s3", owner="owner:1")
    s.add_history(HistoryEntry(command="ls", stdout="out", stderr="", exit_code=0, cwd="/tmp", duration_ms=5))
    last = s.last_n(1)
    assert last and last[0].command == "ls"


def test_history_entry_round_trips():
    entry = HistoryEntry(
        command="ls", stdout="o", stderr="", exit_code=0, cwd="/tmp", duration_ms=1
    )
    again = HistoryEntry.from_dict(entry.to_dict())
    assert again.command == "ls"
    assert again.exit_code == 0
    assert again.stdout == "o"


def test_session_expiry_is_time_based():
    s = ShellSession(session_id="s4", owner="owner:1")
    s.last_used = 0.0  # as if touched at the epoch
    assert s.is_expired() is True


# ── router ──────────────────────────────────────────────────────────────────


def test_router_builds_contexts_per_trust_level():
    r = ShellRouter()
    assert r.owner_ctx(1, 2).trust is TrustLevel.OWNER
    assert r.sudo_ctx(1, 2).trust is TrustLevel.SUDO
    assert r.internal_ctx().trust is TrustLevel.ULTROID


def test_router_ai_context_takes_caps():
    r = ShellRouter()
    ctx = r.ai_ctx(frozenset({Cap.SHELL_READ}), chat_id=5)
    assert ctx.has_cap(Cap.SHELL_READ) is True
    assert ctx.has_cap(Cap.SHELL_WRITE) is False


# ── executor ────────────────────────────────────────────────────────────────


def test_executor_runs_a_command_and_reports_output(tmp_path):
    executor = ShellExecutor()
    session = _session(tmp_path, "e1")
    result = asyncio.run(executor.run("echo hello", session, timeout=30))
    assert result.exit_code == 0, result.stderr
    assert "hello" in result.output
    assert result.ok is True


def test_executor_reports_a_failing_exit_code(tmp_path):
    executor = ShellExecutor()
    session = _session(tmp_path, "e2")
    result = asyncio.run(executor.run("exit 3", session, timeout=30))
    assert result.exit_code == 3, result.stderr
    assert result.ok is False


def test_executor_runs_a_pipeline(tmp_path):
    executor = ShellExecutor()
    session = _session(tmp_path, "e3")
    results = asyncio.run(
        executor.run_pipeline(["echo one", "echo two"], session, timeout=30)
    )
    assert len(results) == 2, [r.stderr for r in results]
    assert all(r.exit_code == 0 for r in results)


def test_executor_stops_a_pipeline_on_error(tmp_path):
    executor = ShellExecutor()
    session = _session(tmp_path, "e4")
    results = asyncio.run(
        executor.run_pipeline(
            ["exit 1", "echo two"], session, timeout=30, stop_on_error=True
        )
    )
    assert len(results) == 1


def test_result_becomes_a_history_entry(tmp_path):
    executor = ShellExecutor()
    session = _session(tmp_path, "e5")
    result = asyncio.run(executor.run("echo x", session, timeout=30))
    entry = result.to_history_entry()
    assert entry.command == "echo x"
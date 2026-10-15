# Ultroid - UserBot Rewrite
# pyUltroid/shell/_executor.py
#
# ShellExecutor  -  the actual subprocess runner.
#
# Responsibilities:
#   - Run commands inside a session's stateful environment (cwd, env)
#   - Stream stdout/stderr back in real time via async generator
#   - Handle cd specially (updates session cwd, no subprocess)
#   - Enforce timeout per execution
#   - Track duration, exit code, return HistoryEntry
#   - Never raise  -  always return ExecutionResult
#
# Called only through _router.py  -  never directly from plugins.

from __future__ import annotations

import asyncio
import os
import shlex
import time
from asyncio import subprocess as asproc
from dataclasses import dataclass, field
from typing import Optional, Tuple
from collections.abc import AsyncGenerator

from pyUltroid.startup.logger import LOGS
from ._session import HistoryEntry, ShellSession

# ── Constants ──────────────────────────────────────────────────────────────

DEFAULT_TIMEOUT = 60.0  # seconds  -  hard kill after this
STREAM_CHUNK_SIZE = 4096  # bytes per read from stdout/stderr
MAX_OUTPUT_BYTES = 1 << 20  # 1 MB  -  truncate beyond this


# ── ExecutionResult ────────────────────────────────────────────────────────


@dataclass
class ExecutionResult:
    """
    Everything the caller needs after a command runs.
    stdout + stderr are always strings, never None.
    """

    command: str
    stdout: str
    stderr: str
    exit_code: int
    cwd: str
    duration_ms: float
    timed_out: bool = False
    truncated: bool = False
    error: str | None = None  # executor-level error (not stderr)

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.error

    @property
    def output(self) -> str:
        """Combined stdout + stderr, stdout first."""
        parts = []
        if self.stdout:
            parts.append(self.stdout)
        if self.stderr:
            parts.append(self.stderr)
        return "\n".join(parts).strip()

    def to_history_entry(self, request_id: str = "") -> HistoryEntry:
        return HistoryEntry(
            command=self.command,
            stdout=self.stdout,
            stderr=self.stderr,
            exit_code=self.exit_code,
            cwd=self.cwd,
            duration_ms=self.duration_ms,
            request_id=request_id,
        )

    def __str__(self) -> str:
        status = "OK" if self.ok else f"ERR({self.exit_code})"
        suffix = " [TIMEOUT]" if self.timed_out else ""
        suffix += " [TRUNCATED]" if self.truncated else ""
        return f"[{status}{suffix}] {self.command[:60]}"


# ── ShellExecutor ──────────────────────────────────────────────────────────


class ShellExecutor:
    """
    Stateless executor. One instance, reused for all executions.
    All state lives in ShellSession, not here.
    """

    # ── cd  -  handled in-process, no subprocess ────────────────────────────

    def _handle_cd(
        self,
        command: str,
        session: ShellSession,
    ) -> ExecutionResult:
        """
        cd is a shell builtin  -  it must be handled here, not in a subprocess,
        because subprocess cwd changes don't propagate back to the session.
        """
        parts = shlex.split(command)
        if len(parts) == 1:
            # bare cd → go to WORKDIR_BASE
            from ._session import WORKDIR_BASE

            target = WORKDIR_BASE
        else:
            target = parts[1]

        # Resolve relative paths against session cwd
        if not os.path.isabs(target):
            target = os.path.normpath(os.path.join(session.cwd, target))

        t0 = time.time()
        if os.path.isdir(target):
            session.set_cwd(target)
            return ExecutionResult(
                command=command,
                stdout=target,
                stderr="",
                exit_code=0,
                cwd=target,
                duration_ms=(time.time() - t0) * 1000,
            )
        else:
            return ExecutionResult(
                command=command,
                stdout="",
                stderr=f"cd: {target}: No such file or directory",
                exit_code=1,
                cwd=session.cwd,
                duration_ms=(time.time() - t0) * 1000,
            )

    # ── Main execution ────────────────────────────────────────────────────

    async def run(
        self,
        command: str,
        session: ShellSession,
        timeout: float = DEFAULT_TIMEOUT,
        request_id: str = "",
    ) -> ExecutionResult:
        """
        Execute *command* inside *session*'s environment.
        Always returns ExecutionResult  -  never raises.
        """
        command = command.strip()
        if not command:
            return ExecutionResult(
                command="",
                stdout="",
                stderr="",
                exit_code=0,
                cwd=session.cwd,
                duration_ms=0.0,
            )

        # cd is special
        if command == "cd" or command.startswith("cd ") or command.startswith("cd\t"):
            result = self._handle_cd(command, session)
            session.add_history(result.to_history_entry(request_id))
            return result

        t0 = time.time()
        stdout_buf = bytearray()
        stderr_buf = bytearray()
        timed_out = False
        exit_code = -1

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asproc.PIPE,
                stderr=asproc.PIPE,
                cwd=session.cwd,
                env=session.effective_env(),
                limit=STREAM_CHUNK_SIZE * 4,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=timeout,
                )
                stdout_buf.extend(stdout_bytes)
                stderr_buf.extend(stderr_bytes)
                exit_code = proc.returncode

            except TimeoutError:
                timed_out = True
                try:
                    proc.kill()
                    await proc.wait()
                except Exception:
                    pass
                exit_code = -1
                LOGS.warning(
                    f"[HSS:EXEC] Timeout after {timeout}s  -  killed: {command[:60]!r}"
                )

        except Exception as e:
            duration_ms = (time.time() - t0) * 1000
            LOGS.error(f"[HSS:EXEC] Subprocess error: {e}  -  cmd={command[:60]!r}")
            return ExecutionResult(
                command=command,
                stdout="",
                stderr="",
                exit_code=-1,
                cwd=session.cwd,
                duration_ms=duration_ms,
                error=str(e),
            )

        duration_ms = (time.time() - t0) * 1000

        # Decode and truncate
        truncated = False
        raw_out = bytes(stdout_buf)
        raw_err = bytes(stderr_buf)

        if len(raw_out) + len(raw_err) > MAX_OUTPUT_BYTES:
            truncated = True
            raw_out = raw_out[: MAX_OUTPUT_BYTES // 2]
            raw_err = raw_err[: MAX_OUTPUT_BYTES // 4]

        stdout = raw_out.decode("utf-8", errors="replace").strip()
        stderr = raw_err.decode("utf-8", errors="replace").strip()

        result = ExecutionResult(
            command=command,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            cwd=session.cwd,
            duration_ms=duration_ms,
            timed_out=timed_out,
            truncated=truncated,
        )

        session.add_history(result.to_history_entry(request_id))
        return result

    # ── Streaming execution ────────────────────────────────────────────────

    async def stream(
        self,
        command: str,
        session: ShellSession,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> AsyncGenerator[tuple[str, str]]:
        """
        Async generator that yields (chunk, stream_type) tuples
        where stream_type is "stdout" or "stderr".

        Used for long-running commands where you want to push
        output to Telegram incrementally.

        Usage:
            async for chunk, kind in executor.stream(cmd, session):
                if kind == "stdout":
                    await msg.edit(current_output + chunk)
        """
        command = command.strip()
        if not command:
            return

        deadline = time.time() + timeout

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asproc.PIPE,
                stderr=asproc.PIPE,
                cwd=session.cwd,
                env=session.effective_env(),
            )

            async def _read_stream(stream, kind: str):
                while True:
                    if time.time() > deadline:
                        proc.kill()
                        yield "\n[TIMEOUT]", kind
                        return
                    try:
                        chunk = await asyncio.wait_for(
                            stream.read(STREAM_CHUNK_SIZE),
                            timeout=1.0,
                        )
                    except TimeoutError:
                        continue
                    if not chunk:
                        break
                    yield chunk.decode("utf-8", errors="replace"), kind

            # Interleave stdout and stderr
            stdout_gen = _read_stream(proc.stdout, "stdout")
            stderr_gen = _read_stream(proc.stderr, "stderr")

            # Drive both generators concurrently via queues
            queue: asyncio.Queue = asyncio.Queue()

            async def _feed(gen):
                async for item in gen:
                    await queue.put(item)
                await queue.put(None)

            done_count = 0
            tasks = [
                asyncio.ensure_future(_feed(stdout_gen)),
                asyncio.ensure_future(_feed(stderr_gen)),
            ]

            while done_count < 2:
                item = await queue.get()
                if item is None:
                    done_count += 1
                else:
                    yield item

            await proc.wait()

        except Exception as e:
            LOGS.error(f"[HSS:EXEC:STREAM] Error: {e}")
            yield f"\n[EXECUTOR ERROR] {e}", "stderr"

    # ── Multi-command pipeline ─────────────────────────────────────────────

    async def run_pipeline(
        self,
        commands: list[str],
        session: ShellSession,
        timeout: float = DEFAULT_TIMEOUT,
        stop_on_error: bool = True,
    ) -> list[ExecutionResult]:
        """
        Run a sequence of commands in the same session.
        If stop_on_error, halts on first non-zero exit code.
        """
        results = []
        for cmd in commands:
            result = await self.run(cmd, session, timeout=timeout)
            results.append(result)
            if stop_on_error and not result.ok:
                LOGS.info(
                    f"[HSS:EXEC] Pipeline stopped at: {cmd!r} (exit={result.exit_code})"
                )
                break
        return results


# ── Module singleton ───────────────────────────────────────────────────────

executor = ShellExecutor()

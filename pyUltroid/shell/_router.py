# Ultroid - UserBot Rewrite
# pyUltroid/shell/_router.py
#
# ShellRouter  -  the single entry point for all shell execution.
#
# Every caller (user, Ultroid internal, AI agent) goes through here.
# The router:
#   1. Resolves or creates the session for this context
#   2. Runs the security check
#   3. Dispatches to executor if allowed
#   4. Persists the audit record
#   5. Saves updated session state
#   6. Returns ExecutionResult to caller
#
# Nothing bypasses the router. If you need shell execution, import
# ShellRouter and call execute() or stream().

from __future__ import annotations

import time
from typing import List, Optional, Tuple
from collections.abc import AsyncGenerator

from pyUltroid.startup.logger import LOGS

from ._context import ExecutionContext, TrustLevel
from ._executor import ExecutionResult, ShellExecutor
from ._security import SecurityGuard, SecurityResult
from ._session import ShellSession, ShellSessionManager


class ShellRouter:
    """
    The unified shell execution gateway.
    Instantiate once  -  module singleton below.
    """

    # ── Primary execute path ───────────────────────────────────────────────

    async def execute(
        self,
        ctx: ExecutionContext,
        command: str,
        timeout: float = 60.0,
    ) -> ExecutionResult:
        """
        Full pipeline: session → security → execute → audit → persist.
        Always returns ExecutionResult. Never raises.
        """
        t0 = time.time()

        # 1. Resolve session
        session = self._resolve_session(ctx)

        # 2. Security check
        sec: SecurityResult = self._guard.check(ctx, command, cwd=session.cwd)

        if not sec.allowed:
            # Return immediately  -  nothing executed
            return ExecutionResult(
                command=command,
                stdout="",
                stderr=f"[BLOCKED] {sec.reason}",
                exit_code=126,  # POSIX: command not executable
                cwd=session.cwd,
                duration_ms=(time.time() - t0) * 1000,
                error=sec.reason,
            )

        # 3. Execute
        result = await self._executor.run(
            command=command,
            session=session,
            timeout=timeout,
            request_id=ctx.request_id,
        )

        # 4. Audit (non-blocking  -  fire and forget)
        if ctx.audit and sec.audit:
            import asyncio

            asyncio.ensure_future(
                self._guard.record(
                    audit=sec.audit,
                    exit_code=result.exit_code,
                    duration_ms=result.duration_ms,
                )
            )

        # 5. Persist updated session
        self._sessions.save(session)

        LOGS.debug(
            f"[HSS:ROUTER] {ctx.caller}({ctx.trust.name}) "
            f"exit={result.exit_code} "
            f"dur={result.duration_ms:.0f}ms "
            f"cmd={command[:60]!r}"
        )

        return result

    # ── Streaming execute path ─────────────────────────────────────────────

    async def stream(
        self,
        ctx: ExecutionContext,
        command: str,
        timeout: float = 120.0,
    ) -> AsyncGenerator[tuple[str, str]]:
        """
        Streaming version  -  yields (chunk, "stdout"|"stderr") tuples.
        Security check runs first; if blocked, yields the block reason
        as a single stderr chunk and returns.

        Use this for long-running commands where you want to push
        incremental output to a Telegram message.
        """
        session = self._resolve_session(ctx)
        sec = self._guard.check(ctx, command, cwd=session.cwd)

        if not sec.allowed:
            yield f"[BLOCKED] {sec.reason}", "stderr"
            return

        async for chunk, kind in self._executor.stream(command, session, timeout=timeout):
            yield chunk, kind

        self._sessions.save(session)

    # ── Pipeline execute path ──────────────────────────────────────────────

    async def execute_pipeline(
        self,
        ctx: ExecutionContext,
        commands: list[str],
        timeout: float = 60.0,
        stop_on_error: bool = True,
    ) -> list[ExecutionResult]:
        """
        Run multiple commands sequentially in the same session.
        Security check runs per command.
        """
        session = self._resolve_session(ctx)
        results = []

        for command in commands:
            sec = self._guard.check(ctx, command, cwd=session.cwd)

            if not sec.allowed:
                results.append(
                    ExecutionResult(
                        command=command,
                        stdout="",
                        stderr=f"[BLOCKED] {sec.reason}",
                        exit_code=126,
                        cwd=session.cwd,
                        duration_ms=0.0,
                        error=sec.reason,
                    )
                )
                if stop_on_error:
                    break
                continue

            result = await self._executor.run(
                command=command,
                session=session,
                timeout=timeout,
                request_id=ctx.request_id,
            )
            results.append(result)

            if ctx.audit and sec.audit:
                import asyncio

                asyncio.ensure_future(
                    self._guard.record(
                        audit=sec.audit,
                        exit_code=result.exit_code,
                        duration_ms=result.duration_ms,
                    )
                )

            if stop_on_error and not result.ok:
                break

        self._sessions.save(session)
        return results

    # ── Session management helpers ─────────────────────────────────────────

    def __init__(
        self,
        executor: ShellExecutor = None,
        guard: SecurityGuard = None,
        sessions: ShellSessionManager = None,
        **kw,
    ) -> None:
        super().__init__(**kw)
        self._executor = executor if executor is not None else ShellExecutor()
        self._guard = guard if guard is not None else SecurityGuard()
        self._sessions = sessions if sessions is not None else ShellSessionManager()

    def get_session(self, ctx: ExecutionContext) -> ShellSession | None:
        """Get the current session for this context, or None."""
        return self._sessions.get(ctx.session_id)

    def destroy_session(self, ctx: ExecutionContext) -> None:
        """Explicitly destroy this context's session."""
        self._sessions.destroy(ctx.session_id)
        LOGS.info(
            f"[HSS:ROUTER] Session destroyed: {ctx.session_id[:8]} ({ctx.caller})"
        )

    def session_history(self, ctx: ExecutionContext, n: int = 10):
        """Return last N history entries for this session."""
        session = self._sessions.get(ctx.session_id)
        if not session:
            return []
        return session.last_n(n)

    # ── Internal ───────────────────────────────────────────────────────────

    def _resolve_session(self, ctx: ExecutionContext) -> ShellSession:
        """
        Get or create a session.
        Owner string format:
          user  → "user:<user_id>"
          AI    → "ai:<session_id[:8]>"
          bot   → "ultroid"
        """
        if ctx.caller == "user":
            owner = f"user:{ctx.user_id}"
        elif ctx.caller == "ai":
            owner = f"ai:{ctx.session_id[:8]}"
        else:
            owner = "ultroid"

        return self._sessions.get_or_create(ctx.session_id, owner)

    # ── Context factory shortcuts ──────────────────────────────────────────
    # Convenience so plugins don't need to import _context directly.

    @staticmethod
    def owner_ctx(user_id: int, chat_id: int, plugin: str = None) -> ExecutionContext:
        return ExecutionContext.for_owner(user_id, chat_id, plugin)

    @staticmethod
    def sudo_ctx(user_id: int, chat_id: int, plugin: str = None) -> ExecutionContext:
        return ExecutionContext.for_sudo(user_id, chat_id, plugin)

    @staticmethod
    def internal_ctx(plugin: str = "internal") -> ExecutionContext:
        return ExecutionContext.for_ultroid(plugin)

    @staticmethod
    def ai_ctx(caps, chat_id: int = 0, plugin: str = "ai") -> ExecutionContext:
        return ExecutionContext.for_ai(caps, chat_id, plugin)



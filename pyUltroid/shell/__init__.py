# Ultroid - UserBot Rewrite
# pyUltroid/shell/__init__.py
#
# Public API for the Hybrid Shell System.
#
# Import surface  -  everything else is internal:
#
#   from pyUltroid.shell import sh, ipc_server, ipc_client
#   from pyUltroid.shell import ExecutionContext, ExecutionResult, Cap, TrustLevel
#
# Internal modules (_context, _session, _security, _executor, _router, _ipc)
# are NOT part of the public API. Import only what's exported here.

from ._context import (
    Cap,
    ExecutionContext,
    TrustLevel,
    BLOCKED_COMMANDS,
    BLOCKED_PATHS,
    BLOCKED_PATTERNS,
)
from ._executor import ExecutionResult
from ._executor import ShellExecutor
from ._ipc import IPCClient, IPCServer, ipc_client, ipc_server, spawn_shadow
from ._router import ShellRouter
from ._security import AuditRecord, SecurityGuard, SecurityResult
from ._session import HistoryEntry, ShellSession, ShellSessionManager

__all__ = [
    # Core types
    "Cap",
    "ExecutionContext",
    "TrustLevel",
    "ExecutionResult",
    "HistoryEntry",
    "ShellSession",
    "ShellRouter",
    "ShellExecutor",
    "SecurityGuard",
    "ShellSessionManager",
    "IPCServer",
    "IPCClient",
    "Shell",
    "AuditRecord",
    "SecurityResult",
    # The layer
    "sh",  # Shell  -  one object over every subsystem
    "ipc_server",  # IPCServer  -  start in main process
    "ipc_client",  # IPCClient  -  use in shadow processes
    "spawn_shadow",  # spawn a shadow subprocess
    # Security constants (read-only reference)
    "BLOCKED_COMMANDS",
    "BLOCKED_PATHS",
    "BLOCKED_PATTERNS",
]


# ── the shell layer, as one object ──────────────────────────────────────────
# Six subsystems with overlapping verbs: ShellRouter.stream and
# ShellExecutor.stream are different things, and ShellSessionManager.get is
# neither. A flat class inheriting all six would have two ``stream`` methods and
# MRO would pick one, so they stay attributes - every verb stays reachable and
# nothing is shadowed.


class Shell:
    """The whole shell layer behind one object.

    ``sh.router.execute(ctx, cmd)``, ``sh.guard.check(ctx, cmd)``,
    ``sh.sessions.get_or_create(...)``, ``sh.executor.run(...)``.

    The IPC pair is deliberately not a member: a server and its client are
    process infrastructure with exactly one instance each, and hiding them
    behind an attribute would imply they were interchangeable.

    No __slots__: tests monkeypatch ``sh.execute`` to observe what the agent
    forwards, and a slotted object refuses that.
    """

    def __init__(
        self,
        *,
        router: ShellRouter = None,
        guard: SecurityGuard = None,
        sessions: ShellSessionManager = None,
        executor: ShellExecutor = None,
    ) -> None:
        self.router = router if router is not None else ShellRouter()
        self.guard = guard if guard is not None else SecurityGuard()
        self.sessions = sessions if sessions is not None else ShellSessionManager()
        self.executor = executor if executor is not None else ShellExecutor()

    # The router is what every caller wants; these keep the common path short.
    async def execute(self, ctx: ExecutionContext, command: str, timeout: float = 60.0):
        return await self.router.execute(ctx, command, timeout)

    async def execute_pipeline(
        self,
        ctx: ExecutionContext,
        commands: list,
        timeout: float = 60.0,
        stop_on_error: bool = True,
    ):
        return await self.router.execute_pipeline(ctx, commands, timeout, stop_on_error)

    def check(self, ctx: ExecutionContext, command: str, cwd: str = ""):
        """Security check, for callers that only want the verdict."""
        return self.guard.check(ctx, command, cwd)

    def components(self) -> dict:
        """Subsystem name -> instance, for diagnostics."""
        return {
            name: getattr(self, name)
            for name in ("router", "guard", "sessions", "executor")
        }


#: The process-wide shell layer.
sh = Shell()

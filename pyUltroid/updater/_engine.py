# UpdateEngine  -  orchestrates the full HSU update flow.
#
# What it does:
#   - check()  → fetch diff, return DiffResult (read-only, no changes)
#   - apply()  → spawn shadow process, register IPC handlers,
#                receive results, hot-reload plugins, signal restart if needed
#   - rollback() → revert to a saved SHA via shadow process
#   - status() → current version info, last update time, pending changes


from __future__ import annotations

import asyncio
import copy
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from pyUltroid.startup.logger import LOGS
from pyUltroid.shell._ipc import ipc_server, spawn_shadow
from ._git import DiffResult, GitManager, is_valid_sha


# ── UpdateState
@dataclass
class UpdateState:
    """Live state of an in-progress or completed update operation."""

    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    status: str = "idle"  # idle|checking|applying|done|failed|rolled_back
    sha_before: str = ""
    sha_after: str = ""
    changed_files: list[str] = field(default_factory=list)
    reloaded: list[str] = field(default_factory=list)
    needs_restart: bool = False
    error: str | None = None
    commits: list[dict] = field(default_factory=list)
    pip_result: dict | None = None

    def is_busy(self) -> bool:
        return self.status in ("checking", "applying")

    def elapsed_ms(self) -> float:
        end = self.finished_at or time.time()
        return (end - self.started_at) * 1000

    def summary(self) -> str:
        if self.status == "done":
            action = (
                "restart"
                if self.needs_restart
                else f"reloaded {len(self.reloaded)} plugin(s)"
            )
            return (
                f"✅ Updated `{self.sha_before}` → `{self.sha_after}` "
                f"in `{self.elapsed_ms():.0f}ms`  -  {action}"
            )
        if self.status == "failed":
            return f"❌ Update failed: `{self.error}`"
        if self.status == "rolled_back":
            return f"↩️ Rolled back to `{self.sha_before}`"
        if self.status == "idle":
            return "No update in progress."
        return f"⏳ {self.status}…"


# ── UpdateEngine
class UpdateEngine:
    """
    The HSU brain. One singleton  -  import `updater` from pyUltroid.updater.

    Usage:
        from pyUltroid.updater import updater

        # Check for updates (non-destructive)
        diff = await updater.check()
        if diff and diff.has_changes:
            LOGS.info(diff.changelog_text())
            await updater.apply(diff)
    """

    def __init__(self, git: GitManager = None, **kw):
        super().__init__(**kw)
        # Not a module singleton: the union owns one GitManager and hands it
        # in, so the engine and a caller poking at up.git see the same repo
        # state instead of two objects drifting apart.
        self._git = git if git is not None else GitManager()
        self._state = UpdateState()
        self._lock = asyncio.Lock()
        self._done_event = asyncio.Event()
        self._ipc_ready = False
        self._restart_notice = None
        self._quiet_notify = False

    # ── IPC handler registration
    def _register_ipc_handlers(self) -> None:
        """
        Register handlers for messages coming from the shadow process.
        Called once  -  safe to call multiple times (idempotent).
        """
        if self._ipc_ready:
            return

        ipc_server.register("update_start", self._on_update_start)
        ipc_server.register("update_done", self._on_update_done)
        ipc_server.register("update_failed", self._on_update_failed)
        self._ipc_ready = True
        LOGS.info("[HSU:ENGINE] IPC handlers registered.")

    async def _on_update_start(self, msg: dict) -> dict:
        self._state.status = "applying"
        self._state.sha_before = msg.get("sha_before", "?")
        LOGS.info(f"[HSU:ENGINE] Shadow started update from {self._state.sha_before}")
        return {"type": "ack"}

    async def _on_update_done(self, msg: dict) -> dict:
        """
        Called when the shadow process finishes successfully.
        Acks immediately (the shadow must not wait out its 30s reply
        timeout while we hot-reload  -  a slow reload previously made the
        shadow drop the connection: "Send error: Connection lost"),
        then finishes the update in a background task: hot-reload
        plugins, update state, signal waiting callers.

        The state/event objects are captured NOW: if this task outlives
        apply()'s timeout and a newer update has since started, writing
        through ``self.`` would corrupt the new update's state and set
        the wrong event.
        """
        asyncio.ensure_future(self._finish_update(msg, self._state, self._done_event))
        return {"type": "ack"}

    async def _finish_update(
        self, msg: dict, state: UpdateState, done_event: asyncio.Event
    ) -> None:
        state.sha_before = msg.get("sha_before", "?")
        state.sha_after = msg.get("sha_after", "?")
        state.changed_files = msg.get("changed_files", [])
        state.needs_restart = msg.get("needs_restart", False)
        state.commits = msg.get("commits", [])
        state.pip_result = msg.get("pip_result")

        plugin_files = msg.get("plugin_files", [])

        try:
            # Hot-reload plugins if no restart needed
            if not state.needs_restart and plugin_files:
                state.reloaded = await self._hot_reload_plugins(plugin_files)
        except Exception as e:
            LOGS.error(f"[HSU:ENGINE] Hot-reload crashed: {e}")
            state.status = "failed"
            state.error = f"Hot-reload crashed: {e}"
            state.finished_at = time.time()
            done_event.set()
            return

        state.status = "done"
        state.finished_at = time.time()
        done_event.set()

        LOGS.info(f"[HSU:ENGINE] Update done: {state.summary()}")

        # Notify LOG_CHANNEL (reads the live state  -  skip if superseded)
        if self._state is state:
            asyncio.ensure_future(self._notify_log_channel())

        # Trigger restart if needed  -  but only when no newer update has
        # started in the meantime (a stale task must never schedule a
        # restart out from under a live update).
        if state.needs_restart:
            if self._state is not state or self._state.is_busy():
                LOGS.warning(
                    "[HSU:ENGINE] Skipping restart from stale update task "
                    "(a newer update is in progress)."
                )
                return
            LOGS.info("[HSU:ENGINE] Core changes detected  -  scheduling restart in 3s…")
            asyncio.ensure_future(self._delayed_restart(3))

    async def _on_update_failed(self, msg: dict) -> dict:
        self._state.status = "failed"
        self._state.error = msg.get("reason", "unknown error")
        self._state.finished_at = time.time()
        self._done_event.set()
        LOGS.error(f"[HSU:ENGINE] Update failed: {self._state.error}")
        asyncio.ensure_future(self._notify_log_channel())
        return {"type": "ack"}

    # ── Hot-reload
    async def _hot_reload_plugins(self, file_paths: list[str]) -> list[str]:
        """
        Hot-reload changed plugin files via the real loader
        (``pyUltroid.loader.reload_plugin_async`` →
        ``_core/loader.py`` AsyncPluginLoader), which owns handler
        registration, the plugin registry, and metadata preservation.

        ``UltroidClient`` has no ``load_plugin``/``unload_plugin``/``_plugins``
        API  -  calling those (as the first HSU draft did) is an AttributeError
        on every file, so the reload must go through the loader singletons.

        Returns list of successfully reloaded plugin stems.
        """
        reloaded = []
        try:
            from pyUltroid._core.loader import reload_plugin_async
            from pyUltroid.tracer import plugin_index
        except ImportError:
            LOGS.error("[HSU:ENGINE] Cannot import loader for hot-reload.")
            return reloaded

        for file_path in file_paths:
            if not file_path.endswith(".py"):
                continue
            try:
                # The shadow reports absolute paths; the loader registry keys
                # plugins by their repo-relative dotted id ("plugins.foo").
                rel = os.path.relpath(file_path, self._git.root)
                name = Path(rel).stem
                result = await reload_plugin_async(name, path=rel)
                if result.get("success"):
                    plugin_index.index_plugin(name, rel)
                    reloaded.append(name)
                    LOGS.info(f"[HSU:ENGINE] Hot-reloaded: {name}")
                else:
                    LOGS.warning(
                        f"[HSU:ENGINE] Hot-reload failed: {name} "
                        f"({result.get('error', 'load not active')})"
                    )
            except Exception as e:
                LOGS.error(f"[HSU:ENGINE] Hot-reload error for {file_path}: {e}")

        return reloaded

    # ── Notify LOG_CHANNEL ─
    async def _notify_log_channel(self) -> None:
        if self._quiet_notify:
            self._quiet_notify = False
            return
        try:
            from pyUltroid import asst, udB

            log_ch = udB.get_key("LOG_CHANNEL")
            if not log_ch or not asst:
                return

            s = self._state
            if s.status == "done":
                commit_lines = "\n".join(
                    f"• `{c['sha']}` {c['msg'][:60]}  -  {c['author']}"
                    for c in s.commits[:5]
                )
                pip_line = ""
                if s.pip_result:
                    ok = "✅" if s.pip_result.get("ok") else "❌"
                    pip_line = f"\n**pip:** {ok} `{s.pip_result.get('msg','')[:80]}`"

                action_line = (
                    "🔄 **Restarting…**"
                    if s.needs_restart
                    else f"♻️ Hot-reloaded: `{'`, `'.join(s.reloaded) or 'none'}`"
                )

                text = (
                    f"**🔼 Ultroid Updated**\n\n"
                    f"`{s.sha_before}` → `{s.sha_after}` "
                    f"in `{s.elapsed_ms():.0f}ms`\n\n"
                    f"**Commits ({len(s.commits)}):**\n{commit_lines or '(none)'}"
                    f"{pip_line}\n\n{action_line}"
                )
            else:
                text = f"**❌ Update Failed**\n\n`{s.error}`"

            await asst.send_message(log_ch, text)
        except Exception as e:
            LOGS.error(f"[HSU:ENGINE] Log channel notify failed: {e}")

    # ── Delayed restart ───────────────────────────────────────────────────

    async def _delayed_restart(self, delay: float) -> None:
        await asyncio.sleep(delay)
        LOGS.info("[HSU:ENGINE] Restarting via os.execl…")
        try:
            from pyUltroid import udB

            # WasItRestart (startup/funcs.py) reads the ``_RESTART`` key in
            # the form "{who}_{chat_id}_{message_id}"  -  the old draft wrote
            # an unread ``RESTART_MSG`` key, so restart confirmation never
            # fired. Seed it when the caller gave us a status message.
            if self._restart_notice:
                who, chat_id, msg_id = self._restart_notice
                udB.set_key("_RESTART", f"{who}_{chat_id}_{msg_id}")
        except Exception:
            pass
        try:
            # Gracefully disconnect all clients before re-exec (same as the
            # manual .restart command path).
            from pyUltroid import call_back

            call_back()
            await asyncio.sleep(1)  # let the disconnect task actually run
        except Exception:
            pass
        os.execl(sys.executable, sys.executable, "-m", "pyUltroid")

    # ── Public API
    @property
    def last_state(self) -> UpdateState:
        """A deep-copied snapshot of the most recent update state.

        ``last_state.sha_before`` is the default rollback target after an
        update  -  it survives in-process until the next apply/rollback.

        Returns a copy (not the live internal object) so callers can never
        mutate engine state  -  e.g. corrupting ``sha_before`` would otherwise
        feed straight into a rollback target.
        """
        return copy.deepcopy(self._state)

    async def check(self, remote: str = "origin") -> DiffResult | None:
        """
        Fetch upstream and return DiffResult.
        Non-destructive  -  does not change working tree.
        Safe to call at any time.
        """
        if self._state.is_busy():
            LOGS.warning("[HSU:ENGINE] Update already in progress.")
            return None

        self._state.status = "checking"
        try:
            loop = asyncio.get_running_loop()
            diff = await loop.run_in_executor(
                None,
                lambda: self._git.fetch_diff(remote),
            )
            return diff
        except Exception as e:
            LOGS.error(f"[HSU:ENGINE] Check failed: {e}")
            return None
        finally:
            if self._state.status == "checking":
                self._state.status = "idle"

    async def apply(
        self,
        diff: DiffResult | None = None,
        remote: str = "origin",
        timeout: float = 300.0,
        restart_notice: tuple | None = None,
        quiet: bool = False,
    ) -> UpdateState:
        """
        Apply the update:
          1. Spawn shadow process
          2. Shadow does git pull + pip install
          3. IPC signals back what changed
          4. Engine hot-reloads plugins or schedules restart
          5. Returns final UpdateState

        If diff is None, runs check() first.

        restart_notice: optional (who, chat_id, message_id) tuple used to
        seed the ``_RESTART`` db key before a core-update reboot, so the
        post-restart ``WasItRestart`` hook edits the right status message
        ("Restarted Successfully."). ``who`` is "bot" or "user".

        quiet: skip the LOG_CHANNEL notify message  -  the caller owns all
        user-facing reporting (single-message flows like `.restart`).
        """
        async with self._lock:
            if self._state.is_busy():
                LOGS.warning("[HSU:ENGINE] Update already in progress.")
                return self._state

            # Check first if no diff provided  -  MUST run before the state
            # flips to "applying", otherwise check() sees is_busy() and
            # returns None, making every bare apply() fail.
            if diff is None:
                diff = await self.check(remote)
                if diff is None:
                    self._state = UpdateState(status="failed")
                    self._state.error = "Could not fetch upstream diff."
                    return self._state

            # Fresh state
            self._state = UpdateState(status="applying")
            self._done_event = asyncio.Event()
            self._restart_notice = restart_notice
            self._quiet_notify = quiet

            # Ensure IPC is ready
            self._register_ipc_handlers()

            if not diff.has_changes:
                self._state.status = "done"
                self._state.error = "Already up to date."
                return self._state

            pre_sha = self._git.current_sha()
            shadow_py = str(Path(__file__).parent / "_shadow.py")
            repo_root = str(self._git.root)
            pip_needed = str(diff.pip_changes)

            LOGS.info(
                f"[HSU:ENGINE] Spawning shadow process: "
                f"{diff.commit_count} commits, "
                f"needs_restart={diff.needs_restart}"
            )

            # Spawn shadow
            try:
                proc = await spawn_shadow(
                    shadow_py,
                    repo_root,
                    remote,
                    pre_sha,
                    pip_needed,
                )
            except Exception as e:
                self._state.status = "failed"
                self._state.error = f"Failed to spawn shadow: {e}"
                return self._state

            # Wait for IPC signal from shadow
            try:
                await asyncio.wait_for(self._done_event.wait(), timeout=timeout)
            except TimeoutError:
                self._state.status = "failed"
                self._state.error = f"Shadow process timed out after {timeout}s."
                LOGS.error("[HSU:ENGINE] Shadow process timeout.")
                try:
                    proc.kill()
                except Exception:
                    pass

            # Reap the shadow so abnormal exits are visible instead of a
            # silent "Connection lost" on the IPC ack. The shadow exits on
            # its own after the update_done handshake; give it a short grace
            # period before reporting.
            try:
                wait = getattr(proc, "wait", None)
                if callable(wait):
                    rc = await asyncio.wait_for(wait(), timeout=10)
                    if rc not in (0, None):
                        # Log-only: a successful update stays "done"  - 
                        # mutating state.error here gave callers mixed
                        # signals (done + error set).
                        LOGS.error(f"[HSU:ENGINE] Shadow exited abnormally: rc={rc}")
            except TimeoutError:
                LOGS.warning(
                    "[HSU:ENGINE] Shadow still running after update  -  leaving it detached."
                )

            return self._state

    async def status(self) -> dict:
        """Current version info and update state."""
        sha = self._git.current_sha()
        branch = self._git.current_branch()
        remote = self._git.remote_url()

        return {
            "sha": sha,
            "branch": branch,
            "remote": remote,
            "update_status": self._state.status,
            "last_update": self._state.finished_at,
            "last_sha": self._state.sha_after or sha,
        }

    async def rollback(self, sha: str, remote: str = "origin") -> UpdateState:
        """
        Roll back to a specific SHA via a shadow rollback process.
        Used when an update breaks something.

        The SHA is validated (4-40 hex) BEFORE anything runs  -  it is
        interpolated into a shell command string below, so an unvalidated
        value would be a command-injection vector at ULTROID trust level.
        On rejection the live state is left untouched (a fresh failed state
        is returned instead) so the last good ``sha_before`` rollback target
        is never clobbered by a bad call.
        """
        sha = (sha or "").strip()
        if not is_valid_sha(sha):
            LOGS.warning(f"[HSU:ENGINE] Rejected invalid rollback SHA: {sha!r}")
            return UpdateState(
                status="failed",
                error=f"Invalid rollback SHA: {sha!r}  -  expected 4-40 hex characters.",
                finished_at=time.time(),
            )

        async with self._lock:
            if self._state.is_busy():
                return self._state

            self._state = UpdateState(status="applying")
            self._done_event = asyncio.Event()
            self._register_ipc_handlers()

            # Use shell system to run rollback in shadow
            from pyUltroid.shell import sh, ExecutionContext

            ctx = ExecutionContext.for_ultroid("updater.rollback")

            result = await sh.execute(ctx, f"git reset --hard {sha}", timeout=30)
            if result.ok:
                self._state.status = "rolled_back"
                self._state.sha_before = sha
                self._state.finished_at = time.time()
                LOGS.info(f"[HSU:ENGINE] Rolled back to {sha}")
            else:
                self._state.status = "failed"
                self._state.error = result.stderr
            return self._state



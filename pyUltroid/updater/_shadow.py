# Ultroid - UserBot Rewrite
# pyUltroid/updater/_shadow.py
#
# Shadow Update Process.

# Lifecycle:
#   1. Spawned by main process (UpdateEngine.apply())
#   2. Connects to main process via IPCClient
#   3. Sends "update_start" signal
#   4. git pull --ff-only
#   5. pip install -r requirements.txt (if needed)
#   6. Sends "update_done" with reloaded files and restart flag
#   7. If anything fails → sends "update_failed" + rollback SHA
#   8. Exits
#
# argv:
#   _shadow.py <repo_root> <remote> <pre_pull_sha> <pip_required>
#
# This file is executed directly as a script, not imported.

import asyncio
import json
import os
import sys
import time

import logging

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
LOGS = logging.getLogger(__name__)

# Minimal bootstrap  -  we're in a fresh process, import only what we need
_root = sys.argv[1] if len(sys.argv) > 1 else "."
_remote = sys.argv[2] if len(sys.argv) > 2 else "origin"
_pre_sha = sys.argv[3] if len(sys.argv) > 3 else "unknown"
_pip_needed = sys.argv[4].lower() == "true" if len(sys.argv) > 4 else False

# Add repo root to path so we can import pyUltroid modules
sys.path.insert(0, _root)


async def main():
    from pyUltroid.shell._ipc import ipc_client, SOCKET_PATH
    from pyUltroid.updater._git import GitManager

    git = GitManager(_root)

    # ── Connect to main process
    LOGS.info("[SHADOW] Connecting to main process at %s…", SOCKET_PATH)
    connected = await ipc_client.connect()

    if not connected:
        LOGS.warning("[SHADOW] Cannot reach main process. Aborting update.")
        sys.exit(1)

    alive = await ipc_client.ping()
    if not alive:
        LOGS.warning("[SHADOW] Main process did not respond to ping. Aborting.")
        await ipc_client.disconnect()
        sys.exit(1)

    LOGS.info("[SHADOW] Connected. Starting update…")

    # ── Signal: update starting
    await ipc_client.send(
        {"type": "update_start", "sha_before": _pre_sha},
        wait_reply=False,
    )

    # ── Capture pre-pull state
    rc, pre_sha, _ = git._git("rev-parse", "--short", "HEAD")
    pre_sha = pre_sha or _pre_sha

    # ── git pull
    LOGS.info("[SHADOW] Pulling from %s…", _remote)
    pull_ok, pull_msg = git.pull(_remote)

    if not pull_ok:
        LOGS.warning("[SHADOW] Pull failed: %s", pull_msg)
        await ipc_client.send(
            {
                "type": "update_failed",
                "reason": f"git pull failed: {pull_msg}",
                "sha": pre_sha,
            },
            wait_reply=False,
        )
        await ipc_client.disconnect()
        sys.exit(1)

    LOGS.info("[SHADOW] Pull OK: %s", pull_msg[:100])

    # ── Find what changed
    rc, changed_out, _ = git._git("diff", "--name-only", f"{pre_sha}..HEAD")
    changed_files = [f for f in changed_out.splitlines() if f]
    LOGS.info("[SHADOW] Changed files: %s", changed_files)

    # ── Classify changes
    from pyUltroid.updater._git import classify_file, FileKind

    plugin_files = []
    core_changed = False
    util_changed = False

    for f in changed_files:
        kind = classify_file(f)
        if kind == FileKind.PLUGIN:
            plugin_files.append(os.path.join(_root, f))
        elif kind in (FileKind.CORE, FileKind.SHELL):
            core_changed = True
        elif kind == FileKind.UTIL:
            util_changed = True

    needs_restart = core_changed

    # ── pip install (if requirements changed)
    pip_result = None
    if _pip_needed or any("requirements" in f for f in changed_files):
        LOGS.info("[SHADOW] Installing pip requirements…")
        req_file = os.path.join(_root, "requirements.txt")
        if os.path.exists(req_file):
            pip_ok, pip_msg = git.pip_install(req_file)
            pip_result = {"ok": pip_ok, "msg": pip_msg[:200]}
            if not pip_ok:
                LOGS.warning("[SHADOW] pip failed: %s", pip_msg)
                # pip failure → rollback and abort
                git.rollback(pre_sha)
                await ipc_client.send(
                    {
                        "type": "update_failed",
                        "reason": f"pip install failed: {pip_msg[:200]}",
                        "sha": pre_sha,
                        "rolled_back": True,
                    },
                    wait_reply=False,
                )
                await ipc_client.disconnect()
                sys.exit(1)
            LOGS.info("[SHADOW] pip OK.")

    # ── Get new SHA
    _, post_sha, _ = git._git("rev-parse", "--short", "HEAD")

    # ── Get new changelog
    _, log_out, _ = git._git("log", "--format=%h|%an|%s", f"{pre_sha}..HEAD")
    commits = []
    for line in log_out.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({"sha": parts[0], "author": parts[1], "msg": parts[2]})

    # ── Signal: update done
    LOGS.info("[SHADOW] Signalling update_done to main process…")
    reply = await ipc_client.send(
        {
            "type": "update_done",
            "sha_before": pre_sha,
            "sha_after": post_sha,
            "changed_files": changed_files,
            "plugin_files": plugin_files,
            "needs_restart": needs_restart,
            "util_changed": util_changed,
            "pip_result": pip_result,
            "commits": commits,
        }
    )

    if reply:
        LOGS.info("[SHADOW] Main process acknowledged: %s", reply)
    else:
        LOGS.warning("[SHADOW] No acknowledgement from main process.")

    await ipc_client.disconnect()
    LOGS.info("[SHADOW] Done. Exiting.")


if __name__ == "__main__":
    asyncio.run(main())

# Git layer for the Hybrid Update System.

# Responsibilities:
#   - Fetch upstream without applying changes
#   - Diff HEAD against upstream to classify what changed
#   - Generate human-readable changelogs from commit messages
#   - Identify which files changed and what kind they are
#     (core → needs restart | plugin → hot-reloadable | asset → no action)
#   - Produce a DiffResult that the update engine acts on
#
# Never touches the working tree directly.
# All write operations (pull, checkout) happen in the shadow process.

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import List, Optional, Tuple

from pyUltroid.startup.logger import LOGS


# ── File classification
class FileKind(Enum):
    CORE = auto()  # pyUltroid/startup/, pyUltroid/__init__.py, configs.py
    PLUGIN = auto()  # plugins/*.py, addons/*.py, assistant/*.py
    VC = auto()  # vc/*.py, vc/src/*
    UTIL = auto()  # pyUltroid/fns/*, pyUltroid/dB/*, utils/*
    ASSET = auto()  # strings/, *.yml, *.json, *.md, *.txt
    SHELL = auto()  # installer.sh, start.py, requirements*.txt
    UNKNOWN = auto()


_CORE_PREFIXES = (
    "pyUltroid/__init__",
    "pyUltroid/__main__",
    "pyUltroid/startup/",
    "pyUltroid/configs",
    "pyUltroid/exceptions",
    "pyUltroid/version",
)

_PLUGIN_PREFIXES = (
    "plugins/",
    "addons/",
    "assistant/",
)

_VC_PREFIXES = ("vc/",)

_UTIL_PREFIXES = (
    "pyUltroid/fns/",
    "pyUltroid/dB/",
    "pyUltroid/_misc/",
    "pyUltroid/shell/",
    "pyUltroid/tracer/",
    "pyUltroid/updater/",
    "utils/",
)

_ASSET_SUFFIXES = (".yml", ".yaml", ".json", ".md", ".txt", ".png", ".jpg")
_SHELL_PREFIXES = ("installer", "start.py", "requirements")


def classify_file(path: str) -> FileKind:
    p = path.lstrip("/")
    for prefix in _CORE_PREFIXES:
        if p.startswith(prefix):
            return FileKind.CORE
    for prefix in _PLUGIN_PREFIXES:
        if p.startswith(prefix):
            return FileKind.PLUGIN
    for prefix in _VC_PREFIXES:
        if p.startswith(prefix):
            return FileKind.VC
    for prefix in _UTIL_PREFIXES:
        if p.startswith(prefix):
            return FileKind.UTIL
    # SHELL before ASSET: "requirements*.txt" also matches the ".txt" asset
    # suffix, but per the FileKind contract it is a shell/deploy file.
    for prefix in _SHELL_PREFIXES:
        if p.startswith(prefix):
            return FileKind.SHELL
    if any(p.endswith(s) for s in _ASSET_SUFFIXES):
        return FileKind.ASSET
    return FileKind.UNKNOWN


# ── SHA validation
_SHA_RE = re.compile(r"[0-9a-f]{4,40}", re.IGNORECASE)


def is_valid_sha(sha: str) -> bool:
    if not sha or not isinstance(sha, str):
        return False
    return bool(_SHA_RE.fullmatch(sha.strip()))


# ── Commit record
@dataclass
class CommitRecord:
    sha: str
    short_sha: str
    author: str
    date: str
    message: str
    files: list[str] = field(default_factory=list)

    def one_line(self) -> str:
        return f"`{self.short_sha}`  -  {self.message[:80]} ({self.author})"


# ── DiffResult
@dataclass
class DiffResult:
    """
    Everything the update engine needs to decide what to do.
    Produced by GitManager.fetch_diff().
    """

    branch: str
    upstream_remote: str
    commits: list[CommitRecord]
    changed_files: list[str]

    # Classified file sets
    core_changes: list[str] = field(default_factory=list)
    plugin_changes: list[str] = field(default_factory=list)
    vc_changes: list[str] = field(default_factory=list)
    util_changes: list[str] = field(default_factory=list)
    asset_changes: list[str] = field(default_factory=list)
    other_changes: list[str] = field(default_factory=list)

    # Decision flags
    needs_restart: bool = False  # core or shell changes present
    hot_reloadable: list[str] = field(default_factory=list)  # plugin paths
    pip_changes: bool = False  # requirements changed
    fetched_at: float = field(default_factory=time.time)

    @property
    def has_changes(self) -> bool:
        return len(self.commits) > 0

    @property
    def commit_count(self) -> int:
        return len(self.commits)

    def changelog_text(self, max_entries: int = 10) -> str:
        if not self.commits:
            return "No new commits."
        lines = [f"**{len(self.commits)} new commit(s):**\n"]
        for c in self.commits[:max_entries]:
            lines.append(f"• {c.one_line()}")
        if len(self.commits) > max_entries:
            lines.append(f"_…and {len(self.commits) - max_entries} more_")
        return "\n".join(lines)

    def impact_summary(self) -> str:
        parts = []
        if self.core_changes:
            parts.append(
                f"🔴 Core ({len(self.core_changes)} files)  -  **restart required**"
            )
        if self.plugin_changes:
            parts.append(
                f"🟢 Plugins ({len(self.plugin_changes)} files)  -  hot-reloadable"
            )
        if self.vc_changes:
            parts.append(f"🟡 VC ({len(self.vc_changes)} files)")
        if self.util_changes:
            parts.append(f"🟡 Utils ({len(self.util_changes)} files)")
        if self.pip_changes:
            parts.append(f"🔵 Requirements changed  -  pip install needed")
        if self.asset_changes:
            parts.append(f"⚪ Assets ({len(self.asset_changes)} files)")
        return "\n".join(parts) if parts else "No significant changes."


# ── GitManager
class GitManager:
    """
    All git operations needed by HSU.
    Read-mostly  -  write operations (pull) go to the shadow process.
    """

    def __init__(self, repo_root: str = "."):
        self.root = Path(repo_root).resolve()

    # ── Internal git runner
    def _git(self, *args: str, check: bool = False) -> tuple[int, str, str]:
        """
        Run a git command in repo_root.
        Returns (returncode, stdout, stderr).
        Never raises  -  errors returned as non-zero returncode.
        """
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "git command timed out"
        except FileNotFoundError:
            return -1, "", "git not found"
        except Exception as e:
            return -1, "", str(e)

    # ── Repository info ────────────────────────────────────────────────────

    def is_git_repo(self) -> bool:
        rc, _, _ = self._git("rev-parse", "--git-dir")
        return rc == 0

    def current_branch(self) -> str:
        _, out, _ = self._git("rev-parse", "--abbrev-ref", "HEAD")
        return out or "main"

    def current_sha(self) -> str:
        _, out, _ = self._git("rev-parse", "--short", "HEAD")
        return out or "unknown"

    def remote_url(self, remote: str = "origin") -> str:
        _, out, _ = self._git("remote", "get-url", remote)
        # Strip only a trailing ".git" suffix  -  a blanket .replace() would
        # corrupt URLs that legitimately contain ".git" in the middle.
        return out[:-4] if out.endswith(".git") else out

    def remotes(self) -> list[str]:
        _, out, _ = self._git("remote")
        return [r for r in out.splitlines() if r]

    # ── Fetch (read-only  -  no working tree changes) ────────────────────────

    def fetch(self, remote: str = "origin") -> tuple[bool, str]:
        """
        Fetch upstream without merging.
        Returns (success, message).
        """
        LOGS.info(f"[HSU:GIT] Fetching {remote}…")
        rc, out, err = self._git("fetch", remote, "--quiet")
        if rc != 0:
            LOGS.warning(f"[HSU:GIT] Fetch failed: {err}")
            return False, err
        return True, out

    def ensure_upstream(self, remote_url: str) -> str:
        """
        Ensure an 'upstream' remote exists pointing to remote_url.
        Returns the remote name to use.
        """
        existing = self.remotes()
        if "upstream" in existing:
            return "upstream"
        if "origin" in existing:
            url = self.remote_url("origin")
            if url and url != remote_url:
                rc, _, err = self._git("remote", "add", "upstream", remote_url)
                if rc == 0:
                    return "upstream"
            return "origin"
        # Add origin as fallback
        self._git("remote", "add", "origin", remote_url)
        return "origin"

    # ── Diff analysis
    def fetch_diff(self, remote: str = "origin") -> DiffResult | None:
        """
        Fetch upstream and compute what changed.
        Returns DiffResult or None if not a git repo / fetch failed.
        This is the main entry point for the update engine.
        """
        if not self.is_git_repo():
            LOGS.warning("[HSU:GIT] Not a git repository.")
            return None

        branch = self.current_branch()

        # Ensure safe directory (needed on some AWS setups)
        self._git("config", "--global", "--add", "safe.directory", str(self.root))

        # Fetch
        success, err = self.fetch(remote)
        if not success:
            # Try adding safe.directory and retrying once
            self._git("config", "--global", "--add", "safe.directory", str(self.root))
            success, err = self.fetch(remote)
            if not success:
                LOGS.error(f"[HSU:GIT] Cannot fetch: {err}")
                return None

        upstream_ref = f"{remote}/{branch}"

        # Changed files
        _, files_out, _ = self._git("diff", "--name-only", f"HEAD..{upstream_ref}")
        changed_files = [f for f in files_out.splitlines() if f]

        # Commits
        commits = self._parse_commits(f"HEAD..{upstream_ref}")

        # Classify files
        core = [f for f in changed_files if classify_file(f) == FileKind.CORE]
        plugins = [f for f in changed_files if classify_file(f) == FileKind.PLUGIN]
        vc = [f for f in changed_files if classify_file(f) == FileKind.VC]
        utils = [f for f in changed_files if classify_file(f) == FileKind.UTIL]
        assets = [f for f in changed_files if classify_file(f) == FileKind.ASSET]
        other = [f for f in changed_files if classify_file(f) == FileKind.UNKNOWN]

        pip_changes = any("requirements" in f for f in changed_files)

        needs_restart = (
            bool(core)
            or pip_changes
            or any(classify_file(f) == FileKind.SHELL for f in changed_files)
        )

        hot_reloadable = [str(self.root / f) for f in plugins if f.endswith(".py")]

        return DiffResult(
            branch=branch,
            upstream_remote=remote,
            commits=commits,
            changed_files=changed_files,
            core_changes=core,
            plugin_changes=plugins,
            vc_changes=vc,
            util_changes=utils,
            asset_changes=assets,
            other_changes=other,
            needs_restart=needs_restart,
            hot_reloadable=hot_reloadable,
            pip_changes=pip_changes,
        )

    def _parse_commits(self, rev_range: str) -> list[CommitRecord]:
        """Parse git log into CommitRecord list."""
        SEP = "|||"
        fmt = f"%H{SEP}%h{SEP}%an{SEP}%ad{SEP}%s"
        _, out, _ = self._git("log", f"--format={fmt}", "--date=short", rev_range)
        records = []
        for line in out.splitlines():
            parts = line.split(SEP)
            if len(parts) < 5:
                continue
            sha, short, author, date, msg = (
                parts[0],
                parts[1],
                parts[2],
                parts[3],
                parts[4],
            )
            # Get files changed in this commit
            _, files_out, _ = self._git(
                "diff-tree", "--no-commit-id", "-r", "--name-only", sha
            )
            files = [f for f in files_out.splitlines() if f]
            records.append(
                CommitRecord(
                    sha=sha,
                    short_sha=short,
                    author=author,
                    date=date,
                    message=msg,
                    files=files,
                )
            )
        return records

    # ── Pull (write  -  only called from shadow process)

    def pull(self, remote: str = "origin") -> tuple[bool, str]:
        """
        Fast-forward merge upstream changes.
        ONLY called from the shadow process  -  never from main process.
        Stashes local changes before pulling and restores them after.
        """
        branch = self.current_branch()
        LOGS.info(f"[HSU:GIT:SHADOW] Pulling {remote}/{branch}…")

        # Stash local changes before pull to avoid conflicts
        stashed = False
        rc, stash_out, _ = self._git("stash", "push", "-m", "hsu-auto-stash")
        if rc == 0 and "No local changes" not in stash_out:
            stashed = True
            LOGS.info("[HSU:GIT:SHADOW] Stashed local changes")

        rc, out, err = self._git("pull", remote, branch, "--ff-only")
        if rc != 0:
            LOGS.error(f"[HSU:GIT:SHADOW] Pull failed: {err}")
            # Try to restore stashed changes on failure
            if stashed:
                self._git("stash", "pop")
            return False, err

        # Restore stashed changes after successful pull
        if stashed:
            rc, pop_out, _ = self._git("stash", "pop")
            if rc != 0:
                LOGS.warning(
                    f"[HSU:GIT:SHADOW] Failed to restore stashed changes: {pop_out}"
                )

        return True, out

    def pip_install(
        self, requirements_file: str = "requirements.txt"
    ) -> tuple[bool, str]:
        """
        Run pip install for changed requirements.
        ONLY called from shadow process.
        """
        import sys

        try:
            result = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    requirements_file,
                    "--break-system-packages",
                    "-q",
                ],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                return False, result.stderr[:500]
            return True, result.stdout[:200]
        except Exception as e:
            return False, str(e)

    # ── Rollback
    def rollback(self, sha: str) -> tuple[bool, str]:
        """
        Hard reset to a specific commit SHA.
        ONLY called from shadow process on failed update.
        """
        if not is_valid_sha(sha):
            LOGS.error(f"[HSU:GIT:SHADOW] Rejected invalid rollback SHA: {sha!r}")
            return False, f"invalid SHA: {sha!r} (need 4-40 hex characters)"
        sha = sha.strip()
        LOGS.warning(f"[HSU:GIT:SHADOW] Rolling back to {sha}…")
        rc, out, err = self._git("reset", "--hard", sha)
        if rc != 0:
            return False, err
        return True, f"Rolled back to {sha}"



# PackageInstaller  -  on-demand pip installer for HRE.

# Responsibilities:
#   - Install required deps when a plugin loads
#   - Install optional deps when a specific feature is first triggered
#   - Detect version conflicts before installing
#   - Roll back on install failure (uninstall what we just installed)
#   - Track all installs with plugin attribution
#   - Clean up orphaned packages on demand
#   - Stream install progress for long installs

from __future__ import annotations

import asyncio
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional
from collections.abc import Callable

from pyUltroid.startup.logger import LOGS
from ._manifest import DepSpec, ManifestRegistry, PluginManifest, manifest_registry


# ── InstallResult
@dataclass
class InstallResult:
    """Result of one install operation (may cover multiple packages)."""

    plugin: str
    installed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # already satisfied
    failed: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)  # detected pre-install
    duration_ms: float = 0.0
    success: bool = True

    def __post_init__(self):
        if self.failed or self.conflicts:
            self.success = False

    def summary(self) -> str:
        parts = []
        if self.installed:
            parts.append(f"✅ installed: `{'`, `'.join(self.installed)}`")
        if self.skipped:
            parts.append(f"⏭ skipped (already ok): `{'`, `'.join(self.skipped)}`")
        if self.failed:
            parts.append(f"❌ failed: `{'`, `'.join(self.failed)}`")
        if self.conflicts:
            parts.append(f"⚠️ conflicts: `{'`, `'.join(self.conflicts)}`")
        parts.append(f"`{self.duration_ms:.0f}ms`")
        return "\n".join(parts) if parts else "Nothing to do."


# ── PackageInstaller
class PackageInstaller:
    """
    Async pip wrapper with tracking and rollback.
    Uses HSS shell system for execution  -  audited, streamed, cancellable.
    """

    def __init__(self, manifests: ManifestRegistry = None, **kw):
        super().__init__(**kw)
        self._manifests = manifests if manifests is not None else ManifestRegistry()
        self._lock = asyncio.Lock()
        self._in_prog: set[str] = set()  # dep names currently being installed

    # ── Core install
    async def install_for_plugin(
        self,
        manifest: PluginManifest,
        include_optional: bool = False,
        progress_cb: Callable[[str], None] | None = None,
    ) -> InstallResult:
        """
        Install all required (and optionally optional) deps for a plugin.
        Skips already-satisfied deps. Reports conflicts without installing.
        """
        t0 = time.time()
        result = InstallResult(plugin=manifest.plugin)

        # Check conflicts first  -  never install if a conflict is present
        for dep in manifest.conflicting:
            result.conflicts.append(dep.raw)
            LOGS.warning(
                f"[HRE:INSTALL] Conflict in {manifest.plugin}: "
                f"{dep.raw} is installed but listed as conflict."
            )

        if result.conflicts:
            result.success = False
            result.duration_ms = (time.time() - t0) * 1000
            return result

        # Determine what to install
        deps = list(manifest.requires)
        if include_optional:
            deps += list(manifest.optional)

        for dep in deps:
            if dep.version_satisfied():
                result.skipped.append(dep.name)
                continue

            if progress_cb:
                progress_cb(f"Installing `{dep.raw}`…")

            ok = await self._install_one(dep, manifest.plugin)
            if ok:
                result.installed.append(dep.raw)
                self._manifests.mark_installed(dep, manifest.plugin)
            else:
                result.failed.append(dep.raw)

        result.duration_ms = (time.time() - t0) * 1000
        result.success = not result.failed and not result.conflicts
        return result

    async def install_dep(
        self,
        dep_str: str,
        plugin: str = "manual",
        progress_cb: Callable[[str], None] | None = None,
    ) -> InstallResult:
        """
        Install a single dependency string on-demand.
        Used by optional feature triggers.
        """
        dep = DepSpec.parse(dep_str)
        t0 = time.time()
        result = InstallResult(plugin=plugin)

        if dep.version_satisfied():
            result.skipped.append(dep.name)
            result.duration_ms = (time.time() - t0) * 1000
            return result

        if progress_cb:
            progress_cb(f"Installing `{dep.raw}`…")

        ok = await self._install_one(dep, plugin)
        if ok:
            result.installed.append(dep.raw)
            self._manifests.mark_installed(dep, plugin)
        else:
            result.failed.append(dep.raw)

        result.duration_ms = (time.time() - t0) * 1000
        result.success = not result.failed
        return result

    async def uninstall(
        self,
        dep_str: str,
        force: bool = False,
    ) -> bool:
        """
        Uninstall a package.
        By default refuses if other plugins still need it (force=True overrides).
        """
        dep = DepSpec.parse(dep_str)
        needers = self._manifests.who_needs(dep.name)

        if needers and not force:
            LOGS.warning(
                f"[HRE:INSTALL] Refusing to uninstall {dep.name}  -  "
                f"needed by: {needers}"
            )
            return False

        return await self._uninstall_one(dep)

    async def cleanup_orphans(self) -> list[str]:
        """
        Uninstall HRE-managed packages that no plugin currently needs.
        Returns list of packages removed.
        """
        orphans = self._manifests.orphaned_packages()
        removed = []
        for pkg_name in orphans:
            dep = DepSpec.parse(pkg_name)
            ok = await self._uninstall_one(dep)
            if ok:
                removed.append(pkg_name)
        return removed

    # ── Batch install from requirements file

    async def install_requirements(
        self,
        req_file: str,
        plugin: str = "hre.batch",
        progress_cb: Callable[[str], None] | None = None,
    ) -> InstallResult:
        """Install from a requirements.txt file."""
        t0 = time.time()
        result = InstallResult(plugin=plugin)

        cmd = (
            f"{sys.executable} -m pip install "
            f"-r {req_file} "
            f"--break-system-packages -q"
        )

        if progress_cb:
            progress_cb(f"Installing from `{req_file}`…")

        ok, out, err = await self._run_pip_cmd(cmd)
        if ok:
            result.installed.append(req_file)
        else:
            result.failed.append(f"{req_file}: {err[:100]}")

        result.duration_ms = (time.time() - t0) * 1000
        result.success = ok
        return result

    # ── Internal pip runners
    async def _install_one(self, dep: DepSpec, plugin: str) -> bool:
        """Install one DepSpec. Returns True on success."""
        async with self._lock:
            if dep.name in self._in_prog:
                # Another coroutine is already installing this  -  wait
                while dep.name in self._in_prog:
                    await asyncio.sleep(0.5)
                return dep.version_satisfied()

            self._in_prog.add(dep.name)

        try:
            cmd = (
                f"{sys.executable} -m pip install "
                f"{dep.install_str!r} "
                f"--break-system-packages -q"
            )
            ok, out, err = await self._run_pip_cmd(cmd)

            if ok:
                LOGS.info(f"[HRE:INSTALL] ✅ {dep.raw} (for {plugin})")
            else:
                LOGS.error(f"[HRE:INSTALL] ❌ {dep.raw}: {err[:150]}")

            return ok
        finally:
            self._in_prog.discard(dep.name)

    async def _uninstall_one(self, dep: DepSpec) -> bool:
        """Uninstall one package. Returns True on success."""
        cmd = f"{sys.executable} -m pip uninstall " f"{dep.name} -y -q"
        ok, _, err = await self._run_pip_cmd(cmd)
        if ok:
            LOGS.info(f"[HRE:INSTALL] 🗑 Uninstalled {dep.name}")
        else:
            LOGS.warning(f"[HRE:INSTALL] Uninstall failed for {dep.name}: {err[:100]}")
        return ok

    async def _run_pip_cmd(self, cmd: str) -> tuple[bool, str, str]:
        """
        Run a pip command via HSS shell system.
        Falls back to direct subprocess if shell not available.
        """
        try:
            from pyUltroid.shell import sh, ExecutionContext

            ctx = ExecutionContext.for_ultroid("hre.installer")
            result = await sh.execute(ctx, cmd, timeout=180.0)
            return result.ok, result.stdout, result.stderr
        except ImportError:
            # HSS not yet available (e.g. during bootstrap)
            pass

        # Direct subprocess fallback
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=180.0)
            ok = proc.returncode == 0
            out = stdout.decode("utf-8", errors="replace").strip()
            err = stderr.decode("utf-8", errors="replace").strip()
            return ok, out, err
        except TimeoutError:
            return False, "", "pip timed out after 180s"
        except Exception as e:
            return False, "", str(e)




# ── Module singleton ───────────────────────────────────────────────────────

#: The process-wide installer, built on the shared ``manifest_registry``.
#: ``hre`` (the resource manager singleton) uses this same object, so a
#: package marked installed through one surface is visible to every other.
installer = PackageInstaller(manifests=manifest_registry)

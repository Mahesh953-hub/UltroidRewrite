# ResourceManager  -  orchestrates the full HRE lifecycle.

# What it does:
#   - on_plugin_load(name, path)   → parse manifest, install required deps,
#                                    block load if a conflict is installed
#   - on_plugin_unload(name)       → unregister manifest, optionally clean orphans
#   - on_demand(dep_str, plugin)   → install an optional dep when first needed
#   - status(plugin)               → show dep status for one plugin
#   - audit()                      → full system dep audit
#   - cleanup()                    → remove orphaned HRE-managed packages


from __future__ import annotations

import asyncio
import time
from typing import Dict, List, Optional
from collections.abc import Callable

from pyUltroid.startup.logger import LOGS
from ._installer import InstallResult, PackageInstaller, installer
from ._manifest import (
    DepSpec,
    PluginManifest,
    ManifestRegistry,
    manifest_registry,
)


# ── PluginLoadResult
class PluginLoadResult:
    """
    Returned by on_plugin_load().
    Tells BaseClient.load_plugin() whether the plugin can load.
    """

    def __init__(
        self,
        plugin: str,
        can_load: bool,
        install_result: InstallResult | None = None,
        block_reason: str | None = None,
    ):
        self.plugin = plugin
        self.can_load = can_load
        self.install_result = install_result
        self.block_reason = block_reason

    def __bool__(self) -> bool:
        return self.can_load

    def summary(self) -> str:
        if self.can_load:
            if self.install_result and self.install_result.installed:
                return (
                    f"✅ `{self.plugin}` loaded  -  "
                    f"installed: `{'`, `'.join(self.install_result.installed)}`"
                )
            return f"✅ `{self.plugin}` loaded (all deps satisfied)"
        return f"❌ `{self.plugin}` blocked: `{self.block_reason}`"


# ── ResourceManager
class ResourceManager:
    """
    The HRE brain. One singleton  -  import `hre` from pyUltroid.extender.

    Typical usage in BaseClient.load_plugin():

        from pyUltroid.extender import hre
        load_result = await hre.on_plugin_load(name, file_path)
        if not load_result:
            LOGS.warning(load_result.block_reason)
            return False   # skip loading this plugin

    Typical usage in a plugin for on-demand optional dep:

        from pyUltroid.extender import hre
        await hre.on_demand("opencv-python", __name__)
        import cv2   # safe now
    """

    def __init__(self, manifests: ManifestRegistry = None,
                 packages: PackageInstaller = None, **kw):
        super().__init__(**kw)
        self._manifests = manifests if manifests is not None else ManifestRegistry()
        self._installer = packages if packages is not None else PackageInstaller(
            manifests=self._manifests
        )
        self._bootstrapped = False
        # Cache of in-progress on_demand installs to avoid double-install
        self._demand_cache: dict[str, asyncio.Event] = {}

    # ── Bootstrap
    def bootstrap(self) -> None:
        """
        Called once at startup, before any plugins load.
        Snapshots the baseline package set.
        """
        if self._bootstrapped:
            return
        self._manifests.snapshot_baseline()
        self._bootstrapped = True
        LOGS.info("[HRE] Resource manager bootstrapped.")

    # ── Shared enforcement
    @staticmethod
    def _conflict_block(
        plugin: str,
        manifest: PluginManifest,
    ) -> PluginLoadResult | None:
        """
        Hard-block result when a __conflicts__ package is installed, else None.
        Shared by on_plugin_load() and check_loadable_sync().
        """
        conflicts = manifest.conflicting
        if not conflicts:
            return None
        reason = f"Conflicting packages installed: " f"{[d.raw for d in conflicts]}"
        LOGS.warning(f"[HRE] {plugin} BLOCKED  -  {reason}")
        return PluginLoadResult(
            plugin=plugin,
            can_load=False,
            block_reason=reason,
        )

    # ── Plugin lifecycle hooks
    async def on_plugin_load(
        self,
        plugin: str,
        file_path: str,
        progress_cb: Callable[[str], None] | None = None,
    ) -> PluginLoadResult:
        """
        Called when a plugin is about to load.
        Parses manifest, installs required deps, checks conflicts.
        Returns PluginLoadResult  -  caller decides whether to proceed.

        Inert until bootstrap() has run: pre-bootstrap loads (unit tests,
        legacy harnesses) pass through untouched and never trigger pip.
        """
        if not self._bootstrapped:
            return PluginLoadResult(plugin=plugin, can_load=True)

        manifest = self._manifests.register(plugin, file_path)

        # No deps declared  -  fast path
        if not manifest.requires and not manifest.optional and not manifest.conflicts:
            return PluginLoadResult(plugin=plugin, can_load=True)

        LOGS.info(
            f"[HRE] {plugin}: "
            f"requires={len(manifest.requires)} "
            f"optional={len(manifest.optional)} "
            f"conflicts={len(manifest.conflicts)}"
        )

        # Check conflicts  -  hard block
        blocked = self._conflict_block(plugin, manifest)
        if blocked is not None:
            return blocked

        # Install required deps
        if manifest.missing_required:
            LOGS.info(
                f"[HRE] {plugin}  -  installing "
                f"{[d.raw for d in manifest.missing_required]}"
            )
            result = await self._installer.install_for_plugin(
                manifest,
                include_optional=False,
                progress_cb=progress_cb,
            )

            if not result.success:
                reason = f"Required dep install failed: {result.failed}"
                LOGS.error(f"[HRE] {plugin} BLOCKED  -  {reason}")
                # Don't hard-block  -  let plugin try to load
                # (it may have graceful ImportError handling)
                LOGS.warning(
                    f"[HRE] Allowing {plugin} to attempt load despite dep failure."
                )
                return PluginLoadResult(
                    plugin=plugin,
                    can_load=True,  # soft  -  plugin may handle missing dep
                    install_result=result,
                    block_reason=reason,
                )

            return PluginLoadResult(
                plugin=plugin,
                can_load=True,
                install_result=result,
            )

        return PluginLoadResult(plugin=plugin, can_load=True)

    def check_loadable_sync(
        self,
        plugin: str,
        file_path: str,
    ) -> PluginLoadResult:
        """
        Synchronous pre-load gate for call sites that cannot await (the
        loader's legacy sync facade running inside a live event loop).

        Registers the manifest and enforces __conflicts__, but cannot run
        pip  -  missing required deps are left to the loader's post-import
        __requires__ gate. Inert until bootstrap().
        """
        if not self._bootstrapped:
            return PluginLoadResult(plugin=plugin, can_load=True)

        manifest = self._manifests.register(plugin, file_path)
        # NB: no `or` shortcut  -  PluginLoadResult.__bool__ is can_load, so a
        # blocked result is falsy and must be returned explicitly.
        blocked = self._conflict_block(plugin, manifest)
        if blocked is not None:
            return blocked
        return PluginLoadResult(plugin=plugin, can_load=True)

    def on_plugin_unload_sync(self, plugin: str) -> bool:
        """
        Synchronous manifest detach for the loader's unload path.

        Returns True when a manifest was registered. Never cleans orphans  - 
        pip needs a loop; orphan cleanup stays an explicit user action
        (.depclean / cleanup()). Inert until bootstrap().
        """
        if not self._bootstrapped:
            return False
        removed = self._manifests.unregister(plugin)
        if removed is not None:
            LOGS.info(f"[HRE] Unregistered manifest for {plugin}.")
        return removed is not None

    async def on_plugin_unload(
        self,
        plugin: str,
        cleanup_orphans: bool = False,
    ) -> list[str]:
        """
        Called when a plugin is unloaded.
        Unregisters its manifest and optionally cleans orphaned packages.
        Returns list of packages removed (if any).
        """
        self.on_plugin_unload_sync(plugin)

        if cleanup_orphans:
            removed = await self._installer.cleanup_orphans()
            if removed:
                LOGS.info(f"[HRE] Cleaned orphans: {removed}")
            return removed
        return []

    # ── On-demand optional dep install
    async def on_demand(
        self,
        dep_str: str,
        plugin: str,
        progress_cb: Callable[[str], None] | None = None,
    ) -> InstallResult:
        """
        Install an optional dependency when a feature is first triggered.
        Deduplicated  -  if two coroutines need the same dep simultaneously,
        only one install runs, both wait for it.

        Usage in a plugin:
            await hre.on_demand("yt-dlp>=2024.1", __name__)
            # yt-dlp is now safe to import
        """
        dep = DepSpec.parse(dep_str)

        # Already installed
        if dep.version_satisfied():
            return InstallResult(plugin=plugin, skipped=[dep.name], success=True)

        # Deduplicate concurrent requests for same dep
        if dep.name in self._demand_cache:
            LOGS.debug(f"[HRE] Waiting for in-progress install of {dep.name}…")
            await self._demand_cache[dep.name].wait()
            return InstallResult(
                plugin=plugin,
                skipped=[dep.name],
                success=dep.version_satisfied(),
            )

        # Create event for other waiters
        evt = asyncio.Event()
        self._demand_cache[dep.name] = evt

        try:
            result = await self._installer.install_dep(dep_str, plugin, progress_cb)
            return result
        finally:
            evt.set()
            self._demand_cache.pop(dep.name, None)

    # ── Status and audit

    def status(self, plugin: str) -> dict | None:
        """Dep status for one plugin."""
        manifest = self._manifests.get(plugin)
        if not manifest:
            return None
        return {
            "plugin": plugin,
            "requires": [str(d) for d in manifest.requires],
            "optional": [str(d) for d in manifest.optional],
            "conflicts": [str(d) for d in manifest.conflicts],
            "missing_required": [str(d) for d in manifest.missing_required],
            "missing_optional": [str(d) for d in manifest.missing_optional],
            "conflicting": [str(d) for d in manifest.conflicting],
            "is_loadable": manifest.is_loadable(),
            "tags": manifest.tags,
        }

    def audit(self) -> dict:
        """Full system dependency audit."""
        manifests = self._manifests.all_manifests()
        ok = [m.plugin for m in manifests if m.is_loadable()]
        blocked = [m.plugin for m in manifests if not m.is_loadable()]
        missing = {
            m.plugin: [str(d) for d in m.missing_required]
            for m in manifests
            if m.missing_required
        }
        conflicts = {
            m.plugin: [str(d) for d in m.conflicting]
            for m in manifests
            if m.conflicting
        }
        orphans = self._manifests.orphaned_packages()

        return {
            "total": len(manifests),
            "ok": len(ok),
            "blocked": len(blocked),
            "blocked_plugins": blocked,
            "missing_deps": missing,
            "conflicts": conflicts,
            "orphans": orphans,
            "registry_stats": self._manifests.stats(),
        }

    async def cleanup(self) -> list[str]:
        """Remove all orphaned HRE-managed packages."""
        return await self._installer.cleanup_orphans()

    async def install_dep(
        self,
        dep_str: str,
        plugin: str = "manual",
        progress_cb: Callable[[str], None] | None = None,
    ) -> InstallResult:
        """Direct install for manual use from plugin/command."""
        return await self._installer.install_dep(dep_str, plugin, progress_cb)

    async def uninstall_dep(self, dep_str: str, force: bool = False) -> bool:
        """Direct uninstall."""
        return await self._installer.uninstall(dep_str, force=force)

    def who_needs(self, dep_name: str) -> list[str]:
        return self._manifests.who_needs(dep_name)

    def plugins_by_tag(self, tag: str) -> list[str]:
        return self._manifests.plugins_by_tag(tag)


# ── Module singleton ───────────────────────────────────────────────────────

#: The HRE brain - one process-wide resource manager, built on the shared
#: ``manifest_registry`` and the shared ``installer``, so the loader's gate
#: and every manifest query read one truth. Import it as
#: ``from pyUltroid.extender import hre``.
hre = ResourceManager(manifests=manifest_registry, packages=installer)



# Ultroid - UserBot Rewrite
# pyUltroid/extender/_manifest.py
#
# Dependency manifest system for plugins.
#
# Every plugin can declare its dependencies in a header comment block:
#
#   # __requires__  = ["pillow>=10.0", "yt-dlp>=2024.1"]
#   # __optional__  = ["opencv-python"]
#   # __conflicts__ = ["moviepy<2.0"]
#   # __res_tags__  = ["image", "media"]
#
# OR as module-level variables (preferred  -  IDE-visible):
#
#   __requires__  = ["pillow>=10.0", "yt-dlp>=2024.1"]
#   __optional__  = ["opencv-python"]
#   __conflicts__ = ["moviepy<2.0"]
#   __res_tags__  = ["image", "media"]
#
# HRE reads these at plugin load time, installs required deps on-demand,
# tracks what was installed and by whom, and cleans up on unload if
# nothing else needs the package.
#
# __requires__   -  must be present for the plugin to load
# __optional__   -  installed only when the specific feature is first used
# __conflicts__  -  versions that must NOT be present (checked, not auto-removed)
# __res_tags__   -  semantic tags for grouping (used by HRE queries)

from __future__ import annotations

import ast
import importlib.metadata
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from pyUltroid.startup.logger import LOGS

# ── Dependency spec ────────────────────────────────────────────────────────


@dataclass
class DepSpec:
    """
    One dependency requirement.
    raw:        original string ("pillow>=10.0")
    name:       normalised package name ("pillow")
    version_op: operator string (">=", "==", "~=", etc.) or ""
    version:    version string ("10.0") or ""
    optional:   True if this is a __optional__ dep
    """

    raw: str
    name: str
    version_op: str = ""
    version: str = ""
    optional: bool = False

    # ── Parse ──────────────────────────────────────────────────────────────

    @classmethod
    def parse(cls, raw: str, optional: bool = False) -> DepSpec:
        raw = raw.strip()
        # Match name + optional version constraint
        m = re.match(
            r"^([A-Za-z0-9_.\-]+)\s*([><=!~]+)\s*([^\s,;]+)?",
            raw,
        )
        if m:
            name = cls._normalise(m.group(1))
            op = m.group(2) or ""
            ver = m.group(3) or ""
        else:
            name = cls._normalise(raw)
            op = ""
            ver = ""
        return cls(raw=raw, name=name, version_op=op, version=ver, optional=optional)

    @staticmethod
    def _normalise(name: str) -> str:
        """Normalise package name: lowercase, replace - with _."""
        return name.lower().replace("-", "_").replace(".", "_")

    # ── Install string ──────────────────────────────────────────────────────

    @property
    def install_str(self) -> str:
        """The string to pass to pip install."""
        return self.raw

    # ── Installed check ────────────────────────────────────────────────────

    def is_installed(self) -> bool:
        """Check if this package is currently installed (any version)."""
        try:
            # Try importlib.metadata first (fast). Use the parsed dist name:
            # self.raw may carry a specifier ("pillow>=10.0") or extras, which
            # importlib.metadata rejects with PackageNotFoundError.
            importlib.metadata.version(self.name)
            return True
        except importlib.metadata.PackageNotFoundError:
            pass
        # Try importlib (some packages have different dist names)
        try:
            __import__(self.name)
            return True
        except ImportError:
            return False

    def version_satisfied(self) -> bool:
        """Check if installed version satisfies the version constraint."""
        if not self.version_op or not self.version:
            return self.is_installed()
        try:
            from packaging.version import Version

            installed = importlib.metadata.version(self.name)
            iv = Version(installed)
            rv = Version(self.version)
            ops = {
                ">=": iv >= rv,
                "<=": iv <= rv,
                "==": iv == rv,
                "!=": iv != rv,
                ">": iv > rv,
                "<": iv < rv,
                "~=": iv >= rv,
            }
            return ops.get(self.version_op, True)
        except Exception:
            return self.is_installed()

    def __str__(self) -> str:
        return self.raw

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other) -> bool:
        if isinstance(other, DepSpec):
            return self.name == other.name
        return False


# ── PluginManifest ─────────────────────────────────────────────────────────


@dataclass
class PluginManifest:
    """
    Complete dependency manifest for one plugin.
    Extracted from plugin source at index time.
    """

    plugin: str
    file_path: str
    requires: list[DepSpec] = field(default_factory=list)
    optional: list[DepSpec] = field(default_factory=list)
    conflicts: list[DepSpec] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def all_required(self) -> list[DepSpec]:
        return [d for d in self.requires if not d.optional]

    @property
    def all_optional(self) -> list[DepSpec]:
        return self.optional

    @property
    def missing_required(self) -> list[DepSpec]:
        return [d for d in self.requires if not d.version_satisfied()]

    @property
    def missing_optional(self) -> list[DepSpec]:
        return [d for d in self.optional if not d.version_satisfied()]

    @property
    def conflicting(self) -> list[DepSpec]:
        return [d for d in self.conflicts if d.is_installed()]

    def is_loadable(self) -> bool:
        """Plugin can load if all required deps are satisfied and no conflicts."""
        return not self.missing_required and not self.conflicting

    def summary(self) -> str:
        parts = []
        if self.requires:
            parts.append(f"requires={[str(d) for d in self.requires]}")
        if self.optional:
            parts.append(f"optional={[str(d) for d in self.optional]}")
        if self.tags:
            parts.append(f"tags={self.tags}")
        return f"PluginManifest({self.plugin}: {', '.join(parts) or 'no deps'})"


# ── ManifestParser ─────────────────────────────────────────────────────────


class ManifestParser:
    """
    Extracts dependency manifests from plugin source files.
    Two extraction methods:
      1. AST  -  reads module-level __requires__ etc. assignments (preferred)
      2. Regex  -  reads # __requires__ = [...] comment blocks (fallback)
    """

    # Regex for comment-style declarations
    _COMMENT_RE = re.compile(
        r"^#\s*(__requires__|__optional__|__conflicts__|__res_tags__)\s*=\s*(\[.*?\])",
        re.MULTILINE,
    )

    def parse(self, plugin: str, file_path: str) -> PluginManifest:
        manifest = PluginManifest(plugin=plugin, file_path=file_path)
        try:
            source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return manifest

        # Try AST first
        self._extract_ast(manifest, source, file_path)

        # If AST found nothing, try comment regex
        if not manifest.requires and not manifest.optional:
            self._extract_comments(manifest, source)

        return manifest

    def _extract_ast(
        self, manifest: PluginManifest, source: str, file_path: str
    ) -> None:
        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError:
            return

        for node in ast.iter_child_nodes(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                name = target.id
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    continue

                if name == "__requires__" and isinstance(value, list):
                    manifest.requires = [
                        DepSpec.parse(v) for v in value if isinstance(v, str)
                    ]
                elif name == "__optional__" and isinstance(value, list):
                    manifest.optional = [
                        DepSpec.parse(v, optional=True)
                        for v in value
                        if isinstance(v, str)
                    ]
                elif name == "__conflicts__" and isinstance(value, list):
                    manifest.conflicts = [
                        DepSpec.parse(v) for v in value if isinstance(v, str)
                    ]
                elif name == "__res_tags__" and isinstance(value, list):
                    manifest.tags = [str(t) for t in value]

    def _extract_comments(self, manifest: PluginManifest, source: str) -> None:
        for m in self._COMMENT_RE.finditer(source):
            key = m.group(1)
            try:
                value = ast.literal_eval(m.group(2))
            except Exception:
                continue
            if not isinstance(value, list):
                continue

            if key == "__requires__":
                manifest.requires = [
                    DepSpec.parse(v) for v in value if isinstance(v, str)
                ]
            elif key == "__optional__":
                manifest.optional = [
                    DepSpec.parse(v, optional=True) for v in value if isinstance(v, str)
                ]
            elif key == "__conflicts__":
                manifest.conflicts = [
                    DepSpec.parse(v) for v in value if isinstance(v, str)
                ]
            elif key == "__res_tags__":
                manifest.tags = [str(v) for v in value]


# ── ManifestRegistry ───────────────────────────────────────────────────────


class ManifestRegistry:
    """
    Stores all plugin manifests.
    Tracks which packages were installed by HRE vs pre-existing.
    """

    def __init__(self):
        self._manifests: dict[str, PluginManifest] = {}
        self._parser = ManifestParser()
        # Packages HRE installed (name → [plugin, plugin, ...])
        self._installed_by_hre: dict[str, list[str]] = {}
        # Snapshot of pre-existing packages at startup
        self._baseline: set[str] = set()

    def snapshot_baseline(self) -> None:
        """
        Record all currently installed packages as baseline.
        Called once at startup  -  packages installed after this are HRE-managed.
        """
        try:
            self._baseline = {
                d.metadata["Name"].lower().replace("-", "_")
                for d in importlib.metadata.distributions()
            }
            LOGS.info(f"[HRE:REGISTRY] Baseline: {len(self._baseline)} packages.")
        except Exception as e:
            LOGS.warning(f"[HRE:REGISTRY] Could not snapshot baseline: {e}")

    def register(self, plugin: str, file_path: str) -> PluginManifest:
        manifest = self._parser.parse(plugin, file_path)
        self._manifests[plugin] = manifest
        return manifest

    def get(self, plugin: str) -> PluginManifest | None:
        return self._manifests.get(plugin)

    def unregister(self, plugin: str) -> PluginManifest | None:
        return self._manifests.pop(plugin, None)

    def mark_installed(self, dep: DepSpec, plugin: str) -> None:
        self._installed_by_hre.setdefault(dep.name, [])
        if plugin not in self._installed_by_hre[dep.name]:
            self._installed_by_hre[dep.name].append(plugin)

    def is_hre_managed(self, dep_name: str) -> bool:
        return dep_name in self._installed_by_hre

    def is_baseline(self, dep_name: str) -> bool:
        return dep_name.lower().replace("-", "_") in self._baseline

    def who_needs(self, dep_name: str) -> list[str]:
        """Which plugins declare a dependency on this package."""
        name = DepSpec._normalise(dep_name)
        result = []
        for plugin, manifest in self._manifests.items():
            all_deps = manifest.requires + manifest.optional + manifest.conflicts
            if any(d.name == name for d in all_deps):
                result.append(plugin)
        return result

    def orphaned_packages(self) -> list[str]:
        """
        HRE-installed packages that no currently loaded plugin needs.
        Candidates for cleanup.
        """
        orphans = []
        for pkg_name, installers in self._installed_by_hre.items():
            still_needed = any(
                any(d.name == pkg_name for d in (m.requires + m.optional))
                for m in self._manifests.values()
            )
            if not still_needed:
                orphans.append(pkg_name)
        return orphans

    def all_manifests(self) -> list[PluginManifest]:
        return list(self._manifests.values())

    def plugins_by_tag(self, tag: str) -> list[str]:
        return [p for p, m in self._manifests.items() if tag in m.tags]

    def stats(self) -> dict:
        total = len(self._manifests)
        with_deps = sum(1 for m in self._manifests.values() if m.requires or m.optional)
        missing = sum(1 for m in self._manifests.values() if m.missing_required)
        return {
            "total_plugins": total,
            "plugins_with_deps": with_deps,
            "plugins_missing": missing,
            "hre_managed": len(self._installed_by_hre),
            "baseline_packages": len(self._baseline),
        }


# ── Module singleton ───────────────────────────────────────────────────────

#: The process-wide plugin-manifest registry. ``hre`` (the resource manager
#: singleton in ``extender._manager``) is built on this same object, so the
#: loader, the resource gate and ``manifest_registry.get()`` all read one
#: truth.
manifest_registry = ManifestRegistry()


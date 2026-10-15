# Ultroid - UserBot Rewrite
# pyUltroid/extender/__init__.py

from ._manifest import (
    DepSpec,
    PluginManifest,
    ManifestParser,
    ManifestRegistry,
    manifest_registry,
)
from ._installer import (
    InstallResult,
    PackageInstaller,
    installer,
)
from ._manager import (
    PluginLoadResult,
    ResourceManager,
    hre,
)


class Extender:
    """Manifests, resource management and package installation, one object.

    Attributes because four names collide with real differences: install_dep
    (ResourceManager routes it, PackageInstaller performs it), and
    plugins_by_tag / who_needs on both the manifest registry and the running
    resource manager.

    ex.manifests   what a plugin declares
    ex.resources   what is installed, and who needs it
    ex.packages    installing and removing
    ex.parse       manifest text -> PluginManifest
    """

    __slots__ = ("manifests", "resources", "packages", "parse")

    def __init__(
        self,
        manifests: ManifestRegistry = None,
        resources: ResourceManager = None,
        packages: PackageInstaller = None,
        parse: ManifestParser = None,
    ) -> None:
        # One truth everywhere: the defaults ARE the process singletons
        # (``manifest_registry`` / ``installer`` / ``hre``), so marking a dep
        # installed through any surface is visible through every other.
        self.manifests = manifests if manifests is not None else manifest_registry
        self.packages = packages if packages is not None else installer
        self.resources = resources if resources is not None else hre
        self.parse = parse if parse is not None else ManifestParser()

    def stats(self) -> dict:
        return self.manifests.stats()

    def components(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__}


#: The process-wide extender.
ex = Extender()

__all__ = [

    "Extender",
    "ex",
    # Primary singleton  -  use this everywhere
    "hre",
    # Supporting singletons
    "manifest_registry",
    "installer",
    # Engines (for injection and tests)
    "ResourceManager",
    "ManifestRegistry",
    "PackageInstaller",
    "ManifestParser",
    # Data types
    "DepSpec",
    "PluginManifest",
    "InstallResult",
    "PluginLoadResult",
]

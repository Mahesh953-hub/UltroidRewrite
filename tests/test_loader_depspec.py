"""Dependency-light regression tests for loader PEP 440 dependency parsing (bug A5).

The loader used to check ``__requires__`` entries with
``find_spec(dependency.replace("-", "_"))`` on the raw string, which fails for
specifier-bearing entries ("pillow>=10.0") and for dist names whose import
name differs ("pillow" -> "PIL"). The check now delegates to
``pyUltroid.extender.DepSpec`` (metadata-based), and ``DepSpec.is_installed``
uses the parsed distribution name instead of the raw specifier string.
"""

from __future__ import annotations

import importlib
import logging
import sys
import tempfile
import types
import unittest
from contextlib import contextmanager
from importlib.util import find_spec
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A package that is genuinely not installed anywhere sane.
MISSING_DEP = "definitely-missing-dep-xyz123"


@contextmanager
def core_namespace():
    """Import core modules without starting the production pyUltroid bootstrap."""
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "pyUltroid" or name.startswith("pyUltroid.")
    }
    for name in list(saved):
        sys.modules.pop(name, None)
    try:
        root = types.ModuleType("pyUltroid")
        root.__path__ = [str(ROOT / "pyUltroid")]
        root.LOGS = logging.getLogger("depspec-tests")
        root.HNDLR = "."
        root.udB = None
        root.asst = None
        root.ultroid_bot = None
        sys.modules["pyUltroid"] = root

        startup = types.ModuleType("pyUltroid.startup")
        startup.__path__ = []
        logger = types.ModuleType("pyUltroid.startup.logger")
        logger.LOGS = root.LOGS
        sys.modules["pyUltroid.startup"] = startup
        sys.modules["pyUltroid.startup.logger"] = logger

        fns = types.ModuleType("pyUltroid.fns")
        fns.__path__ = []
        tools = types.ModuleType("pyUltroid.fns.tools")
        tools.get_all_files = lambda directory, suffix: [
            str(path) for path in Path(directory).rglob(f"*{suffix}")
        ]
        sys.modules["pyUltroid.fns"] = fns
        sys.modules["pyUltroid.fns.tools"] = tools

        database = types.ModuleType("pyUltroid.dB")
        database.__path__ = []
        database_core = types.ModuleType("pyUltroid.dB._core")
        database_core.ADDONS = []
        database_core.HELP = {}
        database_core.LIST = {}
        database_core.LOADED = {}
        sys.modules["pyUltroid.dB"] = database
        sys.modules["pyUltroid.dB._core"] = database_core
        yield root, database_core
    finally:
        for name in list(sys.modules):
            if name == "pyUltroid" or name.startswith("pyUltroid."):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


class LoaderDepSpecTests(unittest.TestCase):
    def setUp(self):
        self.namespace = core_namespace()
        self.root, self.database = self.namespace.__enter__()
        self.loader = importlib.import_module("pyUltroid._core.loader")
        self.manifest_mod = importlib.import_module("pyUltroid.extender._manifest")

    def tearDown(self):
        self.namespace.__exit__(None, None, None)

    def _load_plugin(self, requires, name="fakeplug"):
        """Run the loader's sync dep-check path on a fake plugin file."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / f"{name}.py"
        path.write_text('"""fake plugin"""\n')
        module = types.SimpleNamespace(__doc__="fake plugin", __requires__=requires)
        loader = self.loader.AsyncPluginLoader(directory.name, key="Test")
        return loader._load_sync(str(path), func=lambda _name: module, key="Test")

    def test_specifier_with_installed_package_passes(self):
        # The A5 false negative: plugins/imaging.py declares exactly this and
        # pillow 12.3.0 is installed, yet the raw find_spec check failed.
        with self.assertRaises(ModuleNotFoundError):
            find_spec("pillow>=10.0")  # old behaviour: specifier is not a module
        self.assertIsNone(find_spec("pillow"))  # import name is PIL, not pillow
        manifest = self._load_plugin(["pillow>=10.0"], name="imaging_like")
        self.assertTrue(manifest.is_active, manifest.error_info)

    def test_hyphenated_dist_name_differs_from_import_name(self):
        # python-dotenv is installed; its import name is "dotenv", so neither
        # find_spec("python-dotenv>=1.0") nor find_spec("python_dotenv") works.
        self.assertIsNone(find_spec("python_dotenv"))
        spec = self.manifest_mod.DepSpec.parse("python-dotenv>=1.0")
        self.assertEqual(spec.name, "python_dotenv")
        self.assertTrue(spec.version_satisfied())
        manifest = self._load_plugin(["python-dotenv>=1.0"], name="dotenv_like")
        self.assertTrue(manifest.is_active, manifest.error_info)

    def test_missing_package_still_fails_check(self):
        requirement = f"{MISSING_DEP}>=1.0"
        manifest = self._load_plugin([requirement], name="missing_dep_plug")
        self.assertTrue(manifest.is_errored)
        self.assertIn(
            f"Missing plugin dependencies: {requirement}", manifest.error_info
        )

    def test_unsatisfiable_version_constraint_fails_check(self):
        # Installed (pillow 12.3.0) but the constraint cannot be satisfied.
        manifest = self._load_plugin(["pillow>=999.0"], name="too_new_plug")
        self.assertTrue(manifest.is_errored)
        self.assertIn("pillow>=999.0", manifest.error_info)

    def test_depspec_is_installed_handles_specifier_strings(self):
        DepSpec = self.manifest_mod.DepSpec
        self.assertTrue(DepSpec.parse("pillow>=10.0").is_installed())
        self.assertTrue(DepSpec.parse("pillow").is_installed())
        self.assertFalse(DepSpec.parse(f"{MISSING_DEP}>=1.0").is_installed())
        self.assertFalse(DepSpec.parse(MISSING_DEP).is_installed())

    def test_depspec_version_satisfied_boundaries(self):
        DepSpec = self.manifest_mod.DepSpec
        self.assertTrue(DepSpec.parse("pillow>=10.0").version_satisfied())
        self.assertTrue(DepSpec.parse("pillow<999.0").version_satisfied())
        self.assertFalse(DepSpec.parse("pillow>=999.0").version_satisfied())
        self.assertFalse(DepSpec.parse(f"{MISSING_DEP}>=1.0").version_satisfied())


if __name__ == "__main__":
    unittest.main()

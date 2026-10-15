"""The core union: seven subsystems, one process object, attribute access.

The import-side facts come from ``tests/_core_probe.py`` in a clean
interpreter, because the core modules drag in the loader, the queue and the
webupload chain and the rest of the suite leaves its own stubs behind. The
source-side facts are read with ast, which needs no imports.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CORE_DIR = ROOT / "pyUltroid" / "_core"

SINGLETONS = (
    "_default_loader",
    "plugin_help",
    "registry",
    "manifest_registry",
    "toggle_registry",
    "inline_cache",
    "queue_manager",
)


@pytest.fixture(scope="module")
def probe() -> dict:
    done = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "_core_probe.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_core_owns_seven_constructed_subsystems(probe):
    """The constructed half: the seven plugin-runtime registries."""
    assert probe["components"] == 7
    assert probe["all_distinct"] is True
    assert probe["types"] == [
        "AsyncPluginLoader",
        "InlineResultCache",
        "ManifestRegistry",
        "PluginHelp",
        "PluginRegistry",
        "QueueManager",
        "ToggleRegistry",
    ]


def test_core_is_assembled_from_the_domain_mixins(probe):
    """W9: the union visibly composes - one class from the domain mixins."""
    assert probe["mro"] == [
        "Ultroid",
        "DBMixin",
        "ServiceMixin",
        "ClientMixin",
        "RegistryMixin",
        "RuntimeMixin",
        "object",
    ]


def test_core_slots_cover_constructed_and_bound_halves(probe):
    """W8: the runtime-state slots join the constructed ones."""
    assert probe["attributes"] == sorted(
        (
            # constructed (import time)
            "inline_cache",
            "loader",
            "manifest",
            "plugin_help",
            "queue",
            "registry",
            "toggles",
            # bound (bootstrap time)
            "db",
            "fns",
            "master",
            "misc",
            "var",
            # the client domain
            "_voice",
            # the lifecycle (W9 runtime)
            "_boot_at",
            "_hosted_on",
            "_prefix",
            "_universal_lifecycle",
        )
    )


def test_no_singleton_is_reexported(probe):
    assert probe["lazy_singletons"] == []


@pytest.mark.parametrize(
    "module,indicator",
    [
        ("loader.py", ("_default_loader", "plugin_help", "registry")),
        ("manifest.py", ("manifest_registry",)),
        ("toggles.py", ("toggle_registry",)),
        ("events.py", ("inline_cache",)),
        ("queue.py", ("queue_manager",)),
    ],
)
def test_no_module_singleton_is_assigned(module, indicator):
    tree = ast.parse((CORE_DIR / module).read_text())
    assigned = {
        t.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    assert not assigned & set(indicator), f"{module} still builds a singleton"

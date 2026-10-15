"""The fns union: one flat class, five kits, zero forwarders.

The import-side facts are measured by ``tests/_fns_probe.py`` in a clean
interpreter, because the kit modules pull aiohttp/numpy/telethon/PIL at import
and the rest of the suite leaves its own stubs behind. The source-side facts
are read with ast, which needs no imports at all.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FNS = ROOT / "pyUltroid" / "fns"
KITS = ("admins", "helper", "info", "misc", "tools")
SINGLETONS = ("admin_kit", "helper", "info_kit", "misc_kit", "tool_kit")


@pytest.fixture(scope="module")
def probe() -> dict:
    """What ``pyUltroid.fns`` actually looks like, in a clean interpreter."""
    done = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "_fns_probe.py")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout.strip().splitlines()[-1])


def test_fns_inherits_all_five_kits_flat(probe):
    assert probe["bases"] == ["Helper", "ToolKit", "MiscKit", "AdminKit", "InfoKit"]
    assert probe["kits"] == probe["bases"]


def test_every_kit_method_is_reachable_flat(probe):
    """The point of the flat union: no attribute lookup to reach any method."""
    assert probe["method_count"] > 70
    assert probe["unreachable"] == []


def test_the_kits_genuinely_do_not_collide(probe):
    """The reason a flat union is safe here, asserted rather than assumed."""
    assert probe["clashes"] == []


def test_no_module_singleton_is_exported_anymore(probe):
    assert probe["lazy_singletons"] == []


def test_helper_state_lives_on_the_union_not_a_second_helper(probe):
    """fns owns the one aiohttp session; no per-kit duplicates to drift."""
    assert probe["has_second_helper"] is False
    assert probe["own_attributes"] == ["_session"]


@pytest.mark.parametrize("module", KITS)
def test_no_module_singleton_is_assigned(module):
    """No ``helper = Helper()`` left in any kit module."""
    tree = ast.parse((FNS / f"{module}.py").read_text())
    assigned = {
        t.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for t in node.targets
        if isinstance(t, ast.Name)
    }
    assert not assigned & set(SINGLETONS), f"{module}.py still builds a singleton"


def test_no_pure_forwarders_are_left_in_the_modules():
    """The 80 ``def f(): return helper.f()`` shims are gone for good."""
    survivors = {}
    for path in sorted(FNS.glob("*.py")):
        tree = ast.parse(path.read_text())
        singles = {s for s in SINGLETONS}
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(node):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id in singles
                ):
                    survivors.setdefault(path.name, []).append(node.name)
                    break
    assert survivors == {}, f"forwarders survived: {survivors}"


def test_the_real_module_functions_are_still_there():
    """27 real functions in FastTelethon/ytdl must not have been swept up."""
    total = 0
    for name in ("FastTelethon.py", "ytdl.py"):
        tree = ast.parse((FNS / name).read_text())
        total += sum(
            1
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
    assert total >= 25, f"only {total} module-level functions left"

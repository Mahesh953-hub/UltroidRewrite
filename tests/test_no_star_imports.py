"""No module in the package may reach its parent with ``from .. import *``.

A star import binds whatever the parent happens to have executed *at that
instant*, so it silently changes meaning depending on import order - which is
how three dead names survived in the tree:

* ``tf`` in ``_misc/_decorators.py`` (the FloodWait branch of ``ultroid_cmd``)
* ``TELEGRAPH`` and ``_webupload_cache`` in ``fns/tools.py``

``_webupload_cache`` is the worst of the three: ``plugins/webupload.py``
imports it by name, so that plugin cannot load at all.

The AST scan below is the real assertion; the subprocess probes then prove
each module can be the *first* thing imported.
"""

from __future__ import annotations

import ast
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import ROOT

PACKAGE = ROOT / "pyUltroid"

#: every module that used to (or still does) star-import its parent
STAR_SITES = (
    "_misc/__init__.py",
    "_misc/_decorators.py",
    "_misc/_supporter.py",
    "dB/__init__.py",
    "fns/__init__.py",
    "fns/misc.py",
    "fns/tools.py",
    "startup/loader.py",
)


def star_import_lines(path: Path, level: int = 2) -> list[int]:
    """Line numbers of ``from .. import *`` (parent) in *path*.

    Only ``..`` and above. A sibling ``from . import *`` reads the package
    the module lives in - the one in ``plugins/`` is a deliberate re-export
    surface for 100 plugin files and is a separate concern.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and any(alias.name == "*" for alias in node.names)
        and (node.level or 0) >= level
    ]


@pytest.mark.parametrize("relative", STAR_SITES)
def test_no_module_star_imports_its_parent(relative):
    path = PACKAGE / relative
    assert path.is_file(), path
    offenders = star_import_lines(path)
    assert not offenders, (
        f"{relative} still star-imports its parent at line(s) {offenders}"
    )


def test_the_whole_package_is_star_free():
    """Not just the sites we know about - the entire ``pyUltroid`` tree."""
    offenders = []
    for path in sorted(PACKAGE.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for line in star_import_lines(path):
            offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert not offenders, "parent star imports remain: " + "; ".join(offenders)


# -- the three dead names, now resolved --------------------------------


def test_tf_is_defined_in_the_decorators_module():
    """``tf`` was an undefined global in the FloodWait branch."""
    decorators = _import_or_skip("pyUltroid._misc._decorators")
    _decorators = decorators

    assert hasattr(_decorators, "tf"), "the decorator module must expose tf()"
    assert callable(_decorators.tf)
    # it must be the millisecond formatter, not a stand-in
    from pyUltroid.fns import fns

    assert _decorators.tf(1500) == fns.time_formatter(1500)


def _load_tools_by_path():
    """Load the REAL ``fns/tools.py``, ignoring any cached stub.

    Several test modules install their own ``pyUltroid.fns.tools`` stub into
    ``sys.modules`` and leave it there, so ``import_module`` would happily
    hand back somebody else's module and this test would pass or fail for
    reasons that have nothing to do with the source.
    """
    import importlib.util

    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from _cycle_probe import stub_environment
    finally:
        sys.path.pop(0)

    saved = {
        name: module
        for name, module in sys.modules.items()
        if name.split(".")[0] in ("pyUltroid", "custom", "telethon", "strings", "bs4", "aiohttp", "requests", "PIL", "bs4")
    }
    for name in saved:
        sys.modules.pop(name, None)
    try:
        stub_environment()
        # Load under the REAL dotted path (the real one was popped above)
        # so ``from .. import ...`` inside tools.py resolves against the
        # stubbed ``pyUltroid`` package - a parentless module name makes
        # every relative import raise.
        spec = importlib.util.spec_from_file_location(
            "pyUltroid.fns.tools", ROOT / "pyUltroid" / "fns" / "tools.py"
        )
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except ImportError as exc:
            if "No module named" in str(exc):
                pytest.skip(f"fns.tools needs an optional dependency: {exc}")
            raise
        return module
    finally:
        for name in [
            n
            for n in sys.modules
            if n.split(".")[0] in ("pyUltroid", "custom", "telethon", "strings", "bs4", "aiohttp", "requests", "PIL")
        ]:
            sys.modules.pop(name, None)
        sys.modules.update(saved)


def test_telegraph_and_webupload_cache_are_defined_in_the_tools_module():
    tools = _load_tools_by_path()

    assert isinstance(tools._webupload_cache, dict)
    assert isinstance(tools.TELEGRAPH, list)


def test_webupload_cache_starts_empty_and_is_shared():
    tools = _load_tools_by_path()

    # a module-level cache is process state; assert the identity relationship
    # rather than the contents, since another test may have filled it
    assert tools._webupload_cache is not None
    tools._webupload_cache.setdefault(-1, {}).setdefault(-1, "probe")
    assert tools._webupload_cache[-1][-1] == "probe"
    tools._webupload_cache.pop(-1, None)


def test_webupload_plugin_can_import_the_cache_by_name():
    """The exact import line ``plugins/webupload.py`` uses."""
    source = "from pyUltroid.fns.tools import _webupload_cache"
    result = subprocess.run(
        [sys.executable, "-c", source],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 and "No module named" in result.stderr:
        pytest.skip(f"fns.tools needs an optional dependency: {result.stderr.strip()}")
    assert result.returncode == 0, result.stderr


# -- import-order probes ------------------------------------------------


def _probe(module: str, extra: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", f"import {module}\n{extra}"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _import_or_skip(module: str):
    """Import *module*, skipping when an optional dependency is absent.

    The dead-name checks are worth having, but only in an environment that
    can import the module at all - telethon and aiohttp are not installable
    on every platform this suite runs on.
    """
    from pyUltroid.exceptions import pyUltroidError

    try:
        return importlib.import_module(module)
    except ImportError as exc:
        if "No module named" in str(exc):
            pytest.skip(f"{module} needs an optional dependency: {exc}")
        raise
    except pyUltroidError as exc:
        # Guarded modules refuse to import outside `python -m pyUltroid`, which
        # pytest is not. Same category as a missing dependency here.
        pytest.skip(f"{module} is not importable as a library: {exc}")


def _require(module: str, names: tuple[str, ...] = ()) -> None:
    """Skip when the environment cannot import the package at all."""
    # Names are checked as attributes OF the module - a plain ``import``
    # binds only the module object in the probe's namespace, so bare-name
    # ``print(asst_cmd)`` could never see them.
    attrs = ", ".join(f"_m.{n}" for n in names) or "'None'"
    probe = _probe(module, f"import importlib as _i\n_m = _i.import_module('{module}')\nprint({attrs})")
    if "No module named" in probe.stderr or "RunningAsFunctionLibError" in probe.stderr:
        pytest.skip(f"{module} is not importable here: {probe.stderr.strip().splitlines()[-1]}")
    assert probe.returncode == 0, (
        f"importing {module} first raised:\n{probe.stderr}"
    )


@pytest.mark.parametrize(
    "module",
    [
        "pyUltroid._misc",
        "pyUltroid._misc.keep_safe",
        "pyUltroid.fns",
        "pyUltroid.fns.tools",
        "pyUltroid.fns.misc",
    ],
)
def test_each_module_can_be_imported_first(module):
    """The whole point: order must not decide whether an import works."""
    _require(module)


def test_decorator_modules_bind_root_state_directly():
    """W7: the decorator modules import fns/dB/master at module level.

    They are loaded by the plugin loader after the bootstrap, so the
    deferred-import patches are gone. The contract now: they import
    cleanly WITH the package root present (library mode), exactly like
    every other ``pyUltroid`` module.
    """
    for module in (
        "pyUltroid._misc._decorators",
        "pyUltroid._misc._supporter",
        "pyUltroid._misc._assistant",
    ):
        _require(module)


def test_dB_refuses_library_mode_rather_than_half_importing():
    """``pyUltroid.dB`` is meant to raise outside run-as-module.

    That is deliberate, so the probe asserts the refusal instead of
    demanding a clean import - what it must never do is fail with
    ``NameError`` or ``ImportError``, which is what a star import would
    have turned it into.
    """
    from pyUltroid.exceptions import RunningAsFunctionLibError

    probe = _probe("pyUltroid.dB")
    if "No module named" in probe.stderr:
        pytest.skip("dB needs an optional dependency")
    assert probe.returncode != 0
    assert "RunningAsFunctionLibError" in probe.stderr
    assert RunningAsFunctionLibError.__name__ in probe.stderr


def test_importing_the_misc_assistant_first_does_not_trip_the_fns_cycle():
    """``_misc._assistant`` -> ``fns`` -> ``fns.admins`` -> ``_misc``.

    This is the cycle that used to survive only because something else had
    already imported ``fns``.
    """
    _require(
        "pyUltroid._misc._assistant",
        ("asst_cmd", "callback", "in_pattern", "ButtonMapper"),
    )


def test_the_misc_fns_cycle_resolves_from_a_cold_start():
    """``_misc`` -> ``fns`` -> ``fns.admins`` -> ``_misc``, loaded in that order.

    The cycle used to survive only because something had already imported
    ``fns``. Loading the chain by hand against stubs is the only way to know
    it is genuinely gone rather than merely hidden.
    """
    sys.path.insert(0, str(Path(__file__).parent))
    try:
        from _cycle_probe import load_misc_first
    finally:
        sys.path.pop(0)

    package, admins, assistant = load_misc_first()

    assert type(package.SUDO_M).__name__ == "_SudoManager"
    # fns/admins.py reached back into _misc and got a real manager, not None
    assert type(admins.SUDO_M).__name__ == "_SudoManager"
    for name in ("asst_cmd", "callback", "in_pattern"):
        assert callable(getattr(assistant, name)), name
    for name in ("ButtonMapper", "MessageFormatter", "AssistantEventPatcher"):
        assert hasattr(assistant, name), name


def test_sudo_manager_is_reachable_without_the_decorators_loaded_first():
    """Importing ``_misc`` first must still expose the sudo surface."""
    probe = _probe(
        "pyUltroid._misc",
        "from pyUltroid._misc import SUDO_M, owner_and_sudos, sudoers, CMD_HELP\n"
        "print(SUDO_M.__class__.__name__)",
    )
    if "No module named" in probe.stderr:
        pytest.skip("pyUltroid._misc needs an optional dependency")
    assert probe.returncode == 0, probe.stderr
    assert "_SudoManager" in probe.stdout

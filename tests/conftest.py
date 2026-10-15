"""Shared pytest helpers for the SarahUB suite.

Deliberately minimal: a repo-root ``sys.path`` bootstrap plus generic
``sys.modules`` namespace-isolation helpers. The plugin-load harness
(``tests/test_plugin_load.py``) uses the bootstrap; the isolation helper is
available to any future test that imports ``pyUltroid`` modules without
starting the production bootstrap (the same pattern
``tests/test_core_architecture.py`` hand-rolls today).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# ``sys.path`` bootstrap: make the repo root importable no matter which
# directory pytest was invoked from.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path of the repository checkout under test."""
    return ROOT


@contextmanager
def isolated_sys_modules(*prefixes: str):
    """Snapshot then restore ``sys.modules`` entries matching *prefixes*.

    A prefix matches itself exactly and any dotted child, so
    ``"pyUltroid"`` also covers ``"pyUltroid.universal.tl"``. Modules added
    inside the context are removed on exit; pre-existing entries are
    restored untouched. Yields the dict of saved modules.
    """

    def _matches(name: str) -> bool:
        return any(
            name == prefix or name.startswith(prefix + ".") for prefix in prefixes
        )

    saved = {name: module for name, module in sys.modules.items() if _matches(name)}
    for name in saved:
        sys.modules.pop(name, None)
    try:
        yield saved
    finally:
        for name in list(sys.modules):
            if _matches(name):
                sys.modules.pop(name, None)
        sys.modules.update(saved)


@pytest.fixture
def module_isolation():
    """Function-scoped accessor for :func:`isolated_sys_modules`."""
    return isolated_sys_modules

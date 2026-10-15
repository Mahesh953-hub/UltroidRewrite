"""Why ``assistant/``'s star imports are safe, and what would break them.

``assistant/`` is a deliberate facade: ``assistant/__init__.py`` imports the
bot globals and the decorator surface once, and the twelve submodules pull
them in with ``from . import *``. ``pyflakes`` reports 443 names in that
folder as "may be undefined" - every one of them a false positive, and the
count is not the problem.

The property that makes it correct is narrow and mechanical:

**``assistant/__init__.py`` must not import any submodule of itself.**

Python guarantees a package's ``__init__`` is fully executed before any
submodule body runs, so ``from . import *`` always sees a complete facade.
Add one ``from .callbackstuffs import ...`` to ``__init__`` and that
submodule's own ``from . import *`` runs while ``__init__`` is still
executing - a real cycle, and the 443 warnings stop being theoretical.

That is what these tests pin. They are AST-only, so they need neither
Telethon nor a running bot.

There is also a real fragility worth naming rather than pretending away: the
facade breaks if anything registers ``assistant`` in ``sys.modules``
*without* executing ``__init__`` (a loader that pre-seeds the package, or a
test harness that stubs it). The same failure is what
``tests/test_no_star_imports.py`` reproduces deliberately for the
``_misc``/``fns`` chain, which is why that test can load its chain by hand.
"""

from __future__ import annotations

import ast

import pytest

from conftest import ROOT

ASSISTANT = ROOT / "assistant"


def _module_files() -> list:
    files = sorted(ASSISTANT.glob("*.py")) + sorted((ASSISTANT / "manager").glob("*.py"))
    return [path for path in files if "__pycache__" not in path.parts]


def _imports_own_submodule(path) -> list[str]:
    """Sibling-submodule imports: ``from .foo import x`` / ``from . import foo``.

    Deliberately excludes ``from . import *`` (the package's own facade, which
    is exactly what we want) and ``from .. import *`` from ``manager/`` (which
    reads the ``assistant`` facade one level up). Only a *sibling* import
    turns a submodule body into something that runs while its own ``__init__``
    is still executing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    stem = path.parent.name
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.level:
            continue
        module = node.module or ""
        if node.level == 1 and module and module != stem:
            found.append(f".{module}")
        elif node.level == 1 and not module:
            found.extend(f".{a.name}" for a in node.names)
    return found


def test_assistant_init_imports_no_submodule_of_itself():
    """The invariant the whole facade rests on."""
    offenders = _imports_own_submodule(ASSISTANT / "__init__.py")
    assert not offenders, (
        "assistant/__init__.py must not import its own submodules - that makes "
        f"the facade order-dependent: {offenders}"
    )


def test_assistant_manager_init_imports_no_submodule_of_itself():
    """Same invariant one level down: manager/ has its own facade."""
    offenders = _imports_own_submodule(ASSISTANT / "manager" / "__init__.py")
    assert not offenders, f"assistant/manager/__init__.py: {offenders}"


def test_every_star_importing_module_is_under_a_facade():
    """``from . import *`` is only safe under a package ``__init__``.

    Catches someone adding a bare module that star-imports a sibling
    outside the facade, which is what the 443 warnings would then mean.
    """
    offenders = []
    for path in _module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and any(alias.name == "*" for alias in node.names)
                and (node.level or 0) > 0
            ):
                package_init = path.parent / "__init__.py"
                if not package_init.is_file():
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, (
        "a module star-imports a sibling but its package has no __init__: "
        + "; ".join(offenders)
    )


def test_no_assistant_module_calls_a_free_name_that_is_not_a_facade_name():
    """``eventedit`` was one: it exists nowhere in the repo.

    ``assistant/callbackstuffs.py`` had ``await eventedit(...)`` while its
    three sibling calls in the same function all used ``event.edit(...)``.
    Pinned by asserting the facade does not grow a name that nothing
    defines.
    """
    init = (ASSISTANT / "__init__.py").read_text(encoding="utf-8")
    for ghost in ("eventedit", "nimbus_bot", "naam"):
        assert ghost not in init, f"{ghost} looks like another free-name typo"


def test_the_facade_actually_exports_the_names_submodules_consume():
    """Spot-check the handful the submodules lean on hardest.

    Not every exported name - that is what ``from . import *`` is for - but
    the ones whose removal would break several files at once and would only
    show up as an ImportError at bot start. ``pyflakes`` calls all of these
    "imported but unused"; they are the facade.
    """
    init = (ASSISTANT / "__init__.py").read_text(encoding="utf-8")
    for name in (
        "asst_cmd", "callback", "in_pattern",  # the assistant decorators
        "get_string", "owner_and_sudos",      # message + sudo helpers
        "Button", "custom", "_ult_cache",     # used by 5+ submodules each
        "ATRA_COL", "InlinePlugin",           # via the facade
    ):
        assert name in init, f"assistant/__init__.py no longer provides {name}"


def test_assistant_star_imports_only_ever_target_their_own_facade():
    """``assistant`` star-imports itself, never the framework.

    ``from .. import *`` inside ``assistant/manager/`` resolves to the
    ``assistant`` facade, which is safe and expected. Anything at that depth
    reaching ``pyUltroid`` by star would be the framework hazard the
    ``_misc`` pass removed.
    """
    offenders = []
    for path in _module_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not (
                isinstance(node, ast.ImportFrom)
                and any(alias.name == "*" for alias in node.names)
            ):
                continue
            depth = node.level or 0
            module = node.module or ""
            if depth == 0 and module == "pyUltroid":
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, "framework star imports remain: " + "; ".join(offenders)


@pytest.mark.parametrize("relative", ["__init__.py", "manager/__init__.py"])
def test_the_facade_defines_its_own_ast_plugin_registry(relative):
    """``AST_PLUGINS`` is the one piece of real state the facade owns."""
    source = (ASSISTANT / relative).read_text(encoding="utf-8")
    assert "AST_PLUGINS" in source or relative != "__init__.py"
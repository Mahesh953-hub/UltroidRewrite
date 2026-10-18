"""Import ``pyUltroid.fns`` in a clean interpreter and report its shape.

The kit modules pull aiohttp, numpy, telethon and PIL at module scope, and the
rest of the suite installs its own stubs for those and for ``pyUltroid`` itself.
Asserting on the union from inside the suite therefore measures whatever the
previous test module happened to leave behind. A subprocess has none of that.

Run as ``python tests/_fns_probe.py``; prints one JSON object.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class _Var:
    DEVLIST_IDS: list = []
    OWNER_ID = 1
    SUDOS: list = []
    SUDO_M = 1
    LOG_CHANNEL = -100
    CHANNEL_ID = -100
    STRING_SESSION = ""
    HEROKU_API = None
    REDIS_HOST = ""
    REDIS_PORT = 6379
    REDIS_PASSWORD = ""
    DUAL_MODE = False


def load():
    # pyUltroid.configs reads credentials positionally out of sys.argv (argv[1]
    # is int()-cast as the API id); a full positional argv satisfies it.
    sys.argv = ["pyUltroid", "1", "hash", "session", "", "", ""]
    for key in ("API_ID", "API_HASH", "BOT_TOKEN"):
        os.environ.setdefault(key, "1")

    configs = types.ModuleType("pyUltroid.configs")
    configs.Var = _Var
    sys.modules["pyUltroid.configs"] = configs

    # numpy has no ARM wheel here and tools.py imports it at module scope.
    np = types.ModuleType("numpy")
    np.ndarray = type("ndarray", (), {})
    sys.modules["numpy"] = np

    # helper.py imports four formatters off `custom`, an optional dependency.
    custom = types.ModuleType("custom")
    custom.__path__ = [str(ROOT / "custom")]
    for name in ("fmtbar", "fmtbyte", "fmtmsg", "fmtsec"):
        setattr(custom, name, lambda *a, **k: "")
    sys.modules["custom"] = custom

    root = types.ModuleType("pyUltroid")
    root.__path__ = [str(ROOT / "pyUltroid")]
    root.Var = _Var
    root.udB = types.SimpleNamespace(
        get_key=lambda *a: None, set_key=lambda *a: None, del_key=lambda *a: None
    )
    root.LOGS = types.SimpleNamespace(
        info=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        error=lambda *a, **k: None,
        exception=lambda *a, **k: None,
    )
    # The kits read bot globals at module scope (asst, HNDLR, ultroid_bot, eor,
    # ...). Answer None for anything not set so a new global is not an
    # ImportError here.
    root.__getattr__ = lambda name: None
    sys.modules["pyUltroid"] = root

    spec = importlib.util.spec_from_file_location(
        "pyUltroid.fns",
        ROOT / "pyUltroid" / "fns" / "__init__.py",
        submodule_search_locations=[str(ROOT / "pyUltroid" / "fns")],
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules["pyUltroid.fns"] = package
    spec.loader.exec_module(package)
    return package


def kit_methods(kit) -> set:
    return {n for n, v in vars(kit).items() if callable(v) and not n.startswith("_")}


def main() -> int:
    pkg = load()
    kits = (pkg.Helper, pkg.ToolKit, pkg.MiscKit, pkg.AdminKit, pkg.InfoKit)

    owners: dict = {}
    clashes = []
    names: set = set()
    for kit in kits:
        for name in kit_methods(kit):
            names.add(name)
            if name in owners:
                clashes.append([name, owners[name].__name__, kit.__name__])
            owners[name] = kit.__name__

    report = {
        "bases": [k.__name__ for k in pkg.Fns.__bases__],
        "kits": [k.__name__ for k in pkg.fns.kits],
        "method_count": len(names),
        "unreachable": sorted(
            n for n in names if not callable(getattr(pkg.fns, n, None))
        ),
        "clashes": clashes,
        "lazy_singletons": sorted(
            n
            for n in ("helper", "tool_kit", "misc_kit", "admin_kit", "info_kit")
            # ``helper`` is also a submodule name: the import machinery binds
            # pyUltroid.fns.helper (a module) on the package, which is not a
            # singleton instance and must not trip this check.
            if n in vars(pkg) and not isinstance(vars(pkg)[n], types.ModuleType)
        ),
        "has_second_helper": hasattr(pkg.fns, "helper"),
        "own_attributes": sorted(
            n for n in vars(pkg.fns) if not n.startswith("__")
        ),
    }
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

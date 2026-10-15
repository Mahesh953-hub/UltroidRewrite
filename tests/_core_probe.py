"""Import the composition root (``pyUltroid.ultroid``) in a clean interpreter.

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
        "pyUltroid.ultroid",
        ROOT / "pyUltroid" / "ultroid.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["pyUltroid.ultroid"] = module
    spec.loader.exec_module(module)
    return module


def kit_methods(kit) -> set:
    return {n for n, v in vars(kit).items() if callable(v) and not n.startswith("_")}


def main() -> int:
    pkg = load()
    core = pkg.core

    report = {
        "mro": [cls.__name__ for cls in type(core).__mro__],
        "attributes": sorted(core.__slots__),
        "components": len(core.components()),
        "all_distinct": len({id(c) for c in core.components()}) == len(core.components()),
        "types": sorted(type(c).__name__ for c in core.components()),
        "lazy_singletons": sorted(
            n
            for n in (
                "queue_manager",
                "manifest_registry",
            )
            if hasattr(pkg, n)
        ),
    }
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

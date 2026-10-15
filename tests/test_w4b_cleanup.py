"""W4-B "dedup-cleanup" regression tests."""

import asyncio
import importlib
import os
import sys
import warnings
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@contextmanager
def _isolated_sys_modules(*prefixes: str):
    """Snapshot then restore ``sys.modules`` entries matching *prefixes*."""

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


ROOT = Path(__file__).resolve().parents[1]

SAFE_ENV = {
    "API_ID": "12345",
    "API_HASH": "0" * 32,
    "SESSION": "1" + "A" * 352,
    "BOT_TOKEN": "123:abc",
    "LOG_CHANNEL": "-10012345",
}


def _fake_udb():
    return SimpleNamespace(get_key=lambda key: None, set_key=lambda *a, **k: None)


def _import_fns_tools():
    with _isolated_sys_modules("pyUltroid"):
        with patch.dict(os.environ, SAFE_ENV), patch.object(sys, "argv", ["pytest"]):
            import pyUltroid

            pyUltroid.udB = _fake_udb()
            from pyUltroid.fns import tools

            return tools


def test_advice_returns_quote():
    tools = _import_fns_tools()
    tools.async_searcher = AsyncMock(return_value=[{"q": "Test quote", "a": "Author"}])
    result = asyncio.run(tools.advice())
    assert result == '"Test quote" — Author'


def test_advice_fallback_on_failure():
    tools = _import_fns_tools()
    tools.async_searcher = AsyncMock(side_effect=RuntimeError("network"))
    result = asyncio.run(tools.advice())
    assert "Stay focused" in result or "Unknown" in result


def test_import_library_imports_known_module():
    tools = _import_fns_tools()
    assert tools.import_library("json") is __import__("json")


def test_import_library_raises_on_missing():
    tools = _import_fns_tools()
    with pytest.raises(tools.DependencyMissingError):
        tools.import_library("definitely_not_a_real_module_12345")


def test_sudo_specials_use_config():
    with _isolated_sys_modules("pyUltroid"):
        with (
            patch.dict(os.environ, {**SAFE_ENV, "SUDO_SPECIAL_IDS": "111,222"}),
            patch.object(sys, "argv", ["pytest"]),
        ):
            import pyUltroid

            pyUltroid.udB = _fake_udb()
            import pyUltroid._misc as misc

    misc.Var = SimpleNamespace(SUDO_SPECIAL_IDS=[111, 222])
    m = misc._SudoManager()
    m.db = _fake_udb()
    assert m.get_specials() == [111, 222]


def test_devlist_config_parses_env():
    with _isolated_sys_modules("pyUltroid"):
        with (
            patch.dict(os.environ, {**SAFE_ENV, "DEVLIST_IDS": "123,456"}),
            patch.object(sys, "argv", ["pytest"]),
        ):
            from pyUltroid.configs import Var

    assert Var.DEVLIST_IDS == [123, 456]
    assert Var.SUDO_SPECIAL_IDS == []


def test_utils_directory_removed():
    assert not (ROOT / "utils").exists()
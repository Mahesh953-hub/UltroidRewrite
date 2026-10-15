"""Tests for W3-D startup hygiene changes."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class TestStartupCleanups:
    def test_startup_init_no_fix_logging_shim(self):
        source = (ROOT / "pyUltroid" / "startup" / "__init__.py").read_text()
        assert (
            "_fix_logging" not in source
        ), "startup/__init__.py still references the obsolete _fix_logging shim"

    def test_base_client_uses_safe_loop_helper(self):
        source = (ROOT / "pyUltroid" / "startup" / "BaseClient.py").read_text()
        assert (
            "from ._loops import ensure_running_loop" in source
        ), "BaseClient.py does not use _loops.ensure_running_loop"
        assert (
            "get_event_loop_policy().get_event_loop" not in source
        ), "BaseClient.py still uses the deprecated policy.get_event_loop idiom"

    def test_main_module_no_deprecated_loop(self):
        source = (ROOT / "pyUltroid" / "__main__.py").read_text()
        assert (
            "get_event_loop()" not in source or "from .startup._loops import" in source
        ), "pyUltroid/__main__.py uses asyncio.get_event_loop() without the safe helper"

    def test_base_client_http_uses_tls_verification(self):
        source = (ROOT / "pyUltroid" / "startup" / "BaseClient.py").read_text()
        # Extract the body after the docstring to avoid matching the explanatory text.
        marker = "self._http_session = aiohttp.ClientSession"
        assert (
            marker in source
        ), "UltroidClient.http must create an aiohttp.ClientSession"
        start = source.find(marker)
        http_block = source[start : source.find("\n\n", start)]
        assert (
            "ssl=False" not in http_block
        ), "UltroidClient.http disables TLS verification; authenticated plugin traffic must be verified"

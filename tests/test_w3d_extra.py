"""Tests for pyUltroid.startup._extra (W3-D)."""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


class TestExtraModule:
    def test_no_obsolete_logging_shim(self):
        source = (ROOT / "pyUltroid" / "startup" / "_extra.py").read_text()
        assert (
            "_fix_logging" not in source
        ), "_extra.py still contains the obsolete _fix_logging logging shim"
        assert (
            "FileHandler" not in source
        ), "_extra.py still references the FileHandler logging workaround"

    def test_ask_input_replaces_input(self):
        from pyUltroid.startup._extra import _ask_input

        # _ask_input is a no-op that monkey-patches builtins.input to raise EOFError.
        _ask_input()
        assert __builtins__.get("input") is not None
        with pytest.raises(EOFError):
            __builtins__["input"]("prompt")

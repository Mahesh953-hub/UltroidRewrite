"""Regression tests for Wave-0 W0-C client-hygiene fixes.

Covers:
- A1: UltroidClient.full_info() channel branch, fall-through, and
  exception-as-data return (mocked client, no network).
- A4: pyUltroid/fns/misc.py must not catch the nonexistent lowercase
  ``exception`` (pure AST parse check, no module import).
"""

import ast
import asyncio
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]

# pyUltroid.configs.Var reads sys.argv[1..] and mandatory env vars at import
# time, so imports of the real package must happen with a benign argv/env
# (same approach as smoke_test.py).
_SAFE_ENV = {
    "API_ID": "12345",
    "API_HASH": "0" * 32,
    "SESSION": "1" + "A" * 352,
    "BOT_TOKEN": "123:abc",
    "LOG_CHANNEL": "-10012345",
}


def _import_base_client():
    with patch.dict(os.environ, _SAFE_ENV), patch.object(sys, "argv", ["pytest"]):
        from pyUltroid.startup.BaseClient import UltroidClient
    return UltroidClient


# --------------------------------------------------------------------- A1 --


def test_full_info_channel_branch_uses_channel_request_only():
    """channel=True must issue GetFullChannelRequest exactly once and never
    fall through to GetFullUserRequest."""
    UltroidClient = _import_base_client()
    from telethon.tl.functions.channels import GetFullChannelRequest

    client = AsyncMock()
    sent = []

    async def fake_call(request):
        sent.append(request)
        return {"full": True}

    client.side_effect = fake_call

    result = asyncio.run(UltroidClient.full_info(client, entity=123, channel=True))

    assert result == {"full": True}
    assert len(sent) == 1
    assert isinstance(sent[0], GetFullChannelRequest)


def test_full_info_user_branch_uses_user_request():
    """channel=False must issue GetFullUserRequest with id=entity."""
    UltroidClient = _import_base_client()
    from telethon.tl.functions.users import GetFullUserRequest

    client = AsyncMock()
    sent = []

    async def fake_call(request):
        sent.append(request)
        return {"full": "user"}

    client.side_effect = fake_call

    result = asyncio.run(UltroidClient.full_info(client, entity=456))

    assert result == {"full": "user"}
    assert len(sent) == 1
    assert isinstance(sent[0], GetFullUserRequest)


def test_full_info_returns_none_not_exception_object_on_failure():
    """On request failure full_info must log and return None, never hand the
    exception object back to the caller as if it were data."""
    UltroidClient = _import_base_client()

    client = AsyncMock()
    client.logger = MagicMock()
    client.side_effect = ValueError("boom")

    result = asyncio.run(UltroidClient.full_info(client, entity=789))

    assert result is None
    assert not isinstance(result, Exception)
    assert client.logger.error.call_count == 1


# --------------------------------------------------------------------- A2 --


@pytest.mark.parametrize("method", ["get_channel_difference", "fetch_channel_updates"])
def test_removed_channel_difference_methods_are_not_exposed(method):
    """A2: unsupported channel-difference APIs are absent from the current client."""
    UltroidClient = _import_base_client()
    assert not hasattr(UltroidClient, method)


# --------------------------------------------------------------------- A4 --


def test_misc_py_has_no_lowercase_exception_handler():
    """A4: `except exception` is a NameError waiting to happen; only the
    builtin `Exception` (or real exception classes) may be caught."""
    tree = ast.parse((ROOT / "pyUltroid" / "fns" / "misc.py").read_text())
    offenders = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "exception"
    ]
    assert offenders == [], f"lowercase `except exception` at lines {offenders}"

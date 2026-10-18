"""Regression tests for graceful handling of Telethon dialog resolution errors."""

import asyncio
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "_telethon_compat_under_test", ROOT / "pyUltroid" / "_core" / "telethon_compat.py"
)
_COMPAT = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_COMPAT)


class FakeEvent:
    def __init__(self, sender=None, error=None):
        self.sender = sender
        self.error = error
        self.calls = 0

    async def get_sender(self):
        self.calls += 1
        if self.error:
            raise self.error
        return "resolved"


def test_cached_sender_avoids_network_resolution():
    sender = object()
    event = FakeEvent(sender=sender)

    result = asyncio.run(_COMPAT.get_sender_if_resolvable(event))

    assert result is sender
    assert event.calls == 0


def test_community_peer_and_integer_casts_are_contained():
    for target in ("Peer", "int"):
        event = FakeEvent(
            error=TypeError(f"Cannot cast Community to any kind of {target}.")
        )

        assert asyncio.run(_COMPAT.get_sender_if_resolvable(event)) is None
        assert event.calls == 1


def test_dialog_request_retry_exhaustion_is_contained():
    event = FakeEvent(error=ValueError("Request was unsuccessful 6 time(s)"))

    assert asyncio.run(_COMPAT.get_sender_if_resolvable(event)) is None


def test_unrelated_lookup_errors_still_propagate():
    event = FakeEvent(error=ValueError("invalid entity"))

    try:
        asyncio.run(_COMPAT.get_sender_if_resolvable(event))
    except ValueError as error:
        assert str(error) == "invalid entity"
    else:
        raise AssertionError("unrelated lookup errors must not be swallowed")

"""Tests for pyUltroid.fns.FastTelethon (W3-D)."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.run(coro)


class _FakeSession:
    dc_id = 1
    auth_key = b"x" * 256


class _FakeClient:
    session = _FakeSession()
    _log = {"telethon.network.mtprotosender": None}

    def __init__(self, has_exported_sender=True, main_sender=None):
        self._has_exported_sender = has_exported_sender
        self._sender = main_sender if main_sender is not None else types.SimpleNamespace(tag="main")
        self.exported_calls: list = []

    async def _create_exported_sender(self, dc_id):
        # Track that the cross-DC export-authorization path was taken.
        sender = types.SimpleNamespace()
        sender.dc_id = dc_id
        sender.disconnect = lambda: asyncio.sleep(0)
        self.exported_calls.append(dc_id)
        return sender


class TestFastTelethon:
    def test_no_deprecated_client_loop_access(self):
        source = (ROOT / "pyUltroid" / "fns" / "FastTelethon.py").read_text()
        assert (
            ".client.loop" not in source
        ), "FastTelethon.py still accesses the deprecated client.loop property"
        assert (
            "get_event_loop_policy" not in source
        ), "FastTelethon.py uses the deprecated event loop policy API"

    def test_uses_main_sender_for_own_dc_no_export(self):
        """For the DC the session is already on, exporting authorization is
        invalid (Telethon raises DcIdInvalidError), so the parallel transfer
        must reuse the client's live ``_sender`` without calling
        ``_create_exported_sender``."""
        from pyUltroid.fns.FastTelethon import (
            ParallelTransferrer,
            _BorrowedSender,
        )

        client = _FakeClient(has_exported_sender=True)
        # ParallelTransferrer defaults dc_id to session.dc_id (the own DC).
        assert client.session.dc_id == 1

        async def main():
            pt = ParallelTransferrer(client)
            assert pt.dc_id == client.session.dc_id
            sender = await pt._create_sender()
            # Same-DC: reuse the client's live _sender via a no-op-disconnect
            # wrapper (see _BorrowedSender) instead of export-authorization.
            assert isinstance(sender, _BorrowedSender)
            assert sender._sender is client._sender
            # No export-authorization attempted for the own DC.
            assert client.exported_calls == []

        _run(main())

    def test_own_dc_sender_does_not_disconnect_client(self):
        """Borrowed same-DC senders must be no-op-disconnect wrappers.

        The transfer lifecycle calls ``sender.disconnect()`` on cleanup; for a
        sender borrowed from ``client._sender`` that must be a no-op so the
        bot's primary MTProto connection is not torn down.
        """
        from pyUltroid.fns.FastTelethon import ParallelTransferrer

        client = _FakeClient(has_exported_sender=True)
        client._sender.disconnect_calls = 0

        async def _counted_disconnect():
            client._sender.disconnect_calls += 1

        client._sender.disconnect = _counted_disconnect

        async def main():
            pt = ParallelTransferrer(client)
            sender = await pt._create_sender()
            await sender.disconnect()  # must NOT propagate to client._sender
            assert client._sender.disconnect_calls == 0

        _run(main())

    def test_own_dc_is_pinned_to_one_connection(self):
        """A single borrowed sender cannot be parallelised, so same-DC
        transfers force connection_count to 1 and never register the live
        sender multiple times."""
        from pyUltroid.fns.FastTelethon import ParallelTransferrer

        client = _FakeClient(has_exported_sender=True)

        async def main():
            pt = ParallelTransferrer(client)
            assert pt._is_current_dc is True
            # init_upload would otherwise pick up to 40 connections for a 200MB
            # file; same-DC must collapse to exactly one sender.
            await pt.init_upload(file_id=1, file_size=200 * (1024**2))
            assert len(pt.senders) == 1
            # ... and it must NOT have attempted export-authorization.
            assert client.exported_calls == []
            await pt.finish_upload()

        _run(main())

    def test_uses_create_exported_sender_for_other_dc(self):
        """A genuinely different DC must still go through the export path."""
        from pyUltroid.fns.FastTelethon import ParallelTransferrer

        client = _FakeClient(has_exported_sender=True)

        async def main():
            pt = ParallelTransferrer(client, dc_id=2)
            assert pt.dc_id == 2 and pt.dc_id != client.session.dc_id
            sender = await pt._create_sender()
            assert sender.dc_id == 2
            assert client.exported_calls == [2]

        _run(main())

    def test_capability_error_when_create_exported_sender_missing(self):
        from pyUltroid.fns.FastTelethon import (
            FastTelethonCapabilityError,
            ParallelTransferrer,
        )

        class _LegacyClient:
            session = _FakeSession()
            _log = {"telethon.network.mtprotosender": None}

        client = _LegacyClient()

        async def main():
            pt = ParallelTransferrer(client)
            with pytest.raises(FastTelethonCapabilityError):
                await pt._create_sender()

        _run(main())

    def test_module_imports_without_error(self):
        from pyUltroid.fns import FastTelethon

        assert FastTelethon.download_file is not None
        assert FastTelethon.upload_file is not None

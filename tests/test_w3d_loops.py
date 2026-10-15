"""Tests for pyUltroid.startup._loops (W3-D)."""

from __future__ import annotations

import asyncio
import sys
import warnings

import pytest


class TestLoopHelpers:
    def test_get_event_loop_returns_running_loop(self):
        from pyUltroid.startup._loops import get_event_loop

        async def inner():
            return get_event_loop()

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            assert loop.run_until_complete(inner()) is loop
        finally:
            loop.close()

    def test_get_event_loop_reuses_set_loop(self):
        from pyUltroid.startup._loops import get_event_loop

        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            assert get_event_loop() is loop
        finally:
            loop.close()

    def test_get_event_loop_creates_and_sets_loop_when_none(self):
        from pyUltroid.startup._loops import get_event_loop

        original = asyncio.get_event_loop()
        original.close()
        asyncio.set_event_loop(None)
        try:
            loop = get_event_loop()
            assert isinstance(loop, asyncio.AbstractEventLoop)
            assert asyncio.get_event_loop() is loop
        finally:
            if not loop.is_closed():
                loop.close()

    def test_no_deprecated_get_event_loop_policy_calls(self):
        from pyUltroid.startup._loops import ensure_running_loop

        with warnings.catch_warnings(record=True) as captured:
            warnings.simplefilter("always", DeprecationWarning)
            ensure_running_loop()

        deprecated = [
            w
            for w in captured
            if issubclass(w.category, DeprecationWarning)
            and "event_loop_policy" in str(w.message).lower()
        ]
        assert not deprecated, (
            "ensure_running_loop triggered a policy deprecation warning: "
            f"{deprecated}"
        )

    def test_uvloop_install_is_idempotent(self):
        try:
            import uvloop
        except ImportError:
            pytest.skip("uvloop not installed")

        from pyUltroid.startup._loops import install_uvloop_policy

        # Installing twice should not raise and should leave the policy as uvloop.
        install_uvloop_policy()
        install_uvloop_policy()
        assert isinstance(asyncio.get_event_loop_policy(), uvloop.EventLoopPolicy)

    def test_temporary_loop_closes_on_exit(self):
        from pyUltroid.startup._loops import temporary_loop

        with temporary_loop() as loop:
            assert not loop.is_closed()
            assert isinstance(loop, asyncio.AbstractEventLoop)
        assert loop.is_closed()

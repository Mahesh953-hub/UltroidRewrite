"""Regression tests for the raw-update dispatch pipeline.

Production symptom: every raw update raised

    TypeError: isinstance() arg 2 must be a type, a tuple of types, or a union
    File "pyUltroid/startup/dispatcher.py", line 340, in _dispatch

and Telethon logged "Unhandled exception on _dispatch" once per update,
forever. Cause: a non-class key had been accepted into ``_listeners`` (a
mistyped ``listen="UpdateNewMessage"`` string, a typing alias, or None), and
``_dispatch`` iterated every key with ``isinstance(update, key)``.
"""

from __future__ import annotations

import asyncio

import pytest
from telethon.tl.types import UpdateNewMessage

from pyUltroid.startup.dispatcher import CommandDispatcher


class FakeClient:
    """Minimal stand-in for a Telethon client."""

    def __init__(self):
        self.dispatcher = None
        self.handlers = []

    def add_event_handler(self, cb, event=None):
        self.handlers.append(cb)

    def remove_event_handler(self, cb):
        if cb in self.handlers:
            self.handlers.remove(cb)


async def _noop(update):
    return None


def make_dispatcher():
    client = FakeClient()
    client.dispatcher = CommandDispatcher(client=client)
    return client.dispatcher


# ── registration rejects non-types ─────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "UpdateNewMessage",          # the classic: a bare string
        None,
        123,
        ["UpdateNewMessage"],        # a list, not a class
        (UpdateNewMessage,),         # a tuple, not a class
    ],
)
def test_register_listener_rejects_non_type(bad):
    d = make_dispatcher()
    with pytest.raises(TypeError, match="must be a class"):
        d.register_listener(bad, _noop)
    # Critically: nothing was stored, so dispatch stays usable.
    assert d._listeners == {}


def test_register_listener_accepts_a_real_type():
    d = make_dispatcher()
    d.register_listener(UpdateNewMessage, _noop, name="ok")
    assert list(d._listeners) == [UpdateNewMessage]


def test_error_message_names_the_listener():
    d = make_dispatcher()
    with pytest.raises(TypeError) as ei:
        d.register_listener("UpdateNewMessage", _noop, name="my.listener")
    assert "my.listener" in str(ei.value)
    assert "str" in str(ei.value)  # the offending type is named


def test_subclasses_are_accepted():
    """A listener may be registered against a parent TL class."""
    from telethon.tl.types import UpdateNewChannelMessage

    d = make_dispatcher()
    d.register_listener(UpdateNewMessage, _noop)
    d.register_listener(UpdateNewChannelMessage, _noop)
    assert len(d._listeners) == 2


# ── dispatch containment: one bad key must not kill the pipeline ───────────


def test_dispatch_ignores_a_pre_existing_bad_key():
    """Defence in depth for keys stored before the guard existed."""
    d = make_dispatcher()
    d.register_listener(UpdateNewMessage, _noop)
    # Simulate the poisoned state the old code allowed.
    d._listeners["UpdateNewMessage"] = []

    # A bare object() update matches nothing, but dispatch must not raise.
    asyncio.run(d._dispatch(object()))

    # And the bad key is evicted so it cannot fail again on every update.
    assert "UpdateNewMessage" not in d._listeners
    assert list(d._listeners) == [UpdateNewMessage] or not d._listeners


def test_dispatch_still_runs_good_listeners_when_a_bad_key_exists():
    d = make_dispatcher()
    d.register_listener(UpdateNewMessage, _noop)
    d._listeners["poison"] = []

    asyncio.run(d._dispatch(object()))  # must not raise

    # The genuine listener survived the cleanup.
    assert UpdateNewMessage in d._listeners
    assert "poison" not in d._listeners


def test_dispatch_clears_registered_ids_for_the_dropped_key():
    """The dedup set must not retain the evicted key forever."""
    d = make_dispatcher()
    d._listeners["poison"] = []
    d._registered_ids.add((id(_noop), "poison"))
    asyncio.run(d._dispatch(object()))
    assert not any(i[1] == "poison" for i in d._registered_ids)


# ── normal dispatch still works ────────────────────────────────────────────


def test_matching_listener_runs():
    d = make_dispatcher()
    seen = []

    async def rec(update):
        seen.append(update)

    d.register_listener(UpdateNewMessage, rec)
    update = UpdateNewMessage(message=1, pts=1, pts_count=1)
    asyncio.run(d._dispatch(update))
    assert seen == [update]


def test_non_matching_update_does_not_fire_listener():
    d = make_dispatcher()
    fired = []
    d.register_listener(UpdateNewMessage, lambda u: fired.append(u) or None)
    asyncio.run(d._dispatch(object()))
    assert fired == []


def test_the_reported_traceback_line_no_longer_raises():
    """Exact shape from production: a poisoned key, then a real update."""
    d = make_dispatcher()
    d.register_listener(UpdateNewMessage, _noop)
    d._listeners.update({"BadString": [], None: []})
    # Would previously raise on the first isinstance() with these keys.
    asyncio.run(d._dispatch(UpdateNewMessage(message=1, pts=1, pts_count=1)))

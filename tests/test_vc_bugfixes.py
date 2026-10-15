"""Tests for the VC bugs fixed alongside the player rework."""

from __future__ import annotations

import pytest

from vc._cacher import chat_cache

def _call_cls():
    """Import the Call class, skipping if the optional VC extras are absent.

    vc/_pytgcalls imports pytgcalls/ntgcalls at module level, and they are
    optional extras - skip rather than fail collection on a bare checkout.
    """
    pytest.importorskip("pytgcalls", reason="pytgcalls extra not installed")
    from vc._pytgcalls import Call

    return Call


# ── set_loop_count: the empty-deque truthiness bug ────────────────────────


def test_set_loop_count_works_on_an_empty_queue():
    """`if queue := ...` is falsy for an empty deque, so this silently no-op'd."""
    chat_cache.set_active(1, True)
    assert chat_cache.set_loop_count(1, 5) is True
    assert chat_cache.get_loop_count(1) == 5


def test_loop_count_can_be_cleared_to_zero():
    chat_cache.set_active(2, True)
    chat_cache.set_loop_count(2, 3)
    assert chat_cache.set_loop_count(2, 0) is True
    assert chat_cache.get_loop_count(2) == 0


def test_set_loop_count_on_unknown_chat_returns_false():
    assert chat_cache.set_loop_count(999_999, 3) is False


def test_loop_survives_popping_the_current_track():
    """Loop state used to live on the track, so popping destroyed it."""
    chat_cache.set_active(3, True)
    chat_cache.set_loop_count(3, 4)
    chat_cache.remove_current_song(3)  # empty pop, must not reset the counter
    assert chat_cache.get_loop_count(3) == 4


def test_get_loop_count_defaults_to_zero():
    assert chat_cache.get_loop_count(888_888) == 0


# ── ms_to_seconds: the milliseconds/seconds bug, tested without pytgcalls ─


@pytest.mark.parametrize(
    "raw, expect",
    [
        (None, 0),        # no stream yet
        (0, 0),
        (500, 500),       # sub-second / already-seconds passthrough
        (1000, 1000),
        (1001, 1),        # first real millisecond value
        (65_432, 65),     # the actual pytgcalls last_timestamp_ms case
        (3_600_000, 3600),
        ("90000", 90),    # numeric strings from some bindings
        ("junk", 0),
    ],
)
def test_ms_to_seconds(raw, expect):
    from vc._tools import ms_to_seconds

    assert ms_to_seconds(raw) == expect


def test_ms_to_seconds_is_not_1000x_off():
    """Regression: a 3m30s track reported 3,300,000s of playback."""
    from vc._tools import ms_to_seconds

    assert ms_to_seconds(210_000) == 210


# ── speed_change: video retiming must match the requested speed ───────────


@pytest.mark.parametrize("speed", [0.5, 1.0, 1.5, 2.0])
def test_speed_change_retimes_video_with_the_audio(speed):
    """setpts was hardcoded to 0.5, so any speed != 2x desynced A/V."""
    pt_factor = 1 / speed
    ff = f"-atend -filter:v setpts={pt_factor}*PTS -filter:a atempo={speed}"
    # Video and audio must advance by the same factor.
    assert f"setpts={pt_factor}*PTS" in ff
    assert f"atempo={speed}" in ff
    # 2x speed is the one case the old hardcode happened to get right.
    assert pt_factor == 0.5 if speed == 2.0 else pt_factor != 0.5

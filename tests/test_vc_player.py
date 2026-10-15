"""Behavioural tests for the VC player state and the fmtmsg notices."""

from __future__ import annotations

import asyncio
import html

import pytest

from vc._dataclass import CachedTrack, ChannelPlay
from vc._messages import (
    error_msg,
    finished_msg,
    fmt_duration,
    loop_msg,
    nothing_playing_msg,
    now_playing_msg,
    queue_list_msg,
    queued_msg,
    safe,
)
from vc._player import (
    PlaybackState,
    PlayerRegistry,
    PlayerState,
    Queue,
    Track,
    TrackStatus,
    track_from_cached,
)

CH = ChannelPlay(chat_id=-100123, is_channel=False)


def make(name="Song", artist="Artist", duration=210, **kw):
    params = dict(
        url="https://example/x",
        name=name,
        artist=artist,
        loop=0,
        user="alice",
        file_path="/tmp/f.mp3",
        thumbnail="https://img",
        track_id="tid",
        duration=duration,
        is_video=False,
        platform="youtube",
        channel=CH,
    )
    params.update(kw)
    return CachedTrack(**params)


# ── Track ──────────────────────────────────────────────────────────────────


def test_track_exposes_metadata():
    t = Track(cached=make("Title", "Who", 90))
    assert (t.title, t.artist, t.duration) == ("Title", "Who", 90)
    assert t.requester == "alice"
    assert t.platform == "youtube"
    assert not t.is_live


@pytest.mark.parametrize("bad", [0, -1, None])
def test_track_with_no_duration_is_live(bad):
    assert Track(cached=make(duration=bad or 0)).is_live


def test_track_missing_artist_degrades_gracefully():
    t = Track(cached=make(artist=""))
    assert t.artist == "Unknown artist"


# ── Queue ──────────────────────────────────────────────────────────────────


def test_queue_head_and_tail():
    q = Queue([track_from_cached(make(f"t{i}")) for i in range(3)])
    assert q.current.title == "t0"
    assert q.next.title == "t1"
    assert [t.title for t in q.upcoming] == ["t1", "t2"]
    assert len(q) == 3 and bool(q)


def test_empty_queue_is_falsy_and_safe():
    q = Queue()
    assert not q
    assert q.current is None and q.next is None
    assert q.pop_current() is None
    assert q.upcoming == []


def test_promote_next_marks_new_head_playing():
    q = Queue([track_from_cached(make(f"t{i}")) for i in range(3)])
    nxt = q.promote_next()
    assert nxt.title == "t1"
    assert nxt.status is TrackStatus.PLAYING
    assert q.current.title == "t1"


def test_rotate_moves_head_to_back():
    q = Queue([track_from_cached(make(f"t{i}")) for i in range(3)])
    nxt = q.rotate()
    assert nxt.title == "t1"
    assert [t.title for t in q] == ["t1", "t2", "t0"]
    assert q[2].status is TrackStatus.PENDING


def test_remove_at_is_one_based():
    q = Queue([track_from_cached(make(f"t{i}")) for i in range(4)])
    gone = q.remove_at(1)
    assert gone.title == "t0"
    assert [t.title for t in q] == ["t1", "t2", "t3"]


@pytest.mark.parametrize("bad", [0, -1, "2", None, 1.0, True])
def test_remove_at_rejects_invalid_indices(bad):
    q = Queue([track_from_cached(make("t0")), track_from_cached(make("t1"))])
    assert q.remove_at(bad) is None
    assert len(q) == 2


def test_remove_at_out_of_range_returns_none():
    q = Queue([track_from_cached(make("t0"))])
    assert q.remove_at(9) is None


def test_move_reorders():
    q = Queue([track_from_cached(make(f"t{i}")) for i in range(4)])
    assert q.move(4, 1)
    assert [t.title for t in q] == ["t3", "t0", "t1", "t2"]


def test_move_rejects_bad_indices():
    q = Queue([track_from_cached(make("t0"))])
    assert q.move(0, 1) is False
    assert q.move(1, 5) is False
    assert len(q) == 1


def test_clear_returns_removed():
    q = Queue([track_from_cached(make(f"t{i}")) for i in range(3)])
    assert len(q.clear()) == 3
    assert len(q) == 0


def test_requeue_puts_back_at_head():
    q = Queue([track_from_cached(make("t1"))])
    bad = track_from_cached(make("t0"))
    q.requeue(bad)
    assert q.current is bad


# ── PlayerState ────────────────────────────────────────────────────────────


def test_start_marks_head_playing_and_starts_clock():
    p = PlayerState(queue=Queue([track_from_cached(make())]))
    cur = p.start(now=100.0)
    assert cur.status is TrackStatus.PLAYING
    assert p.is_playing
    assert p.position(now=110.0) == pytest.approx(10.0)


def test_start_on_empty_queue_stays_stopped():
    p = PlayerState()
    assert p.start() is None
    assert p.is_stopped


def test_pause_freezes_position():
    p = PlayerState(queue=Queue([track_from_cached(make())]))
    p.start(now=100.0)
    p.pause()
    assert p.is_paused
    # Position must not advance while paused.
    assert p.position(now=999.0) == 0.0


def test_resume_does_not_count_paused_time():
    p = PlayerState(queue=Queue([track_from_cached(make())]))
    p.start(now=100.0)
    p.pause()
    p.resume(now=200.0)
    assert p.is_playing
    assert p.position(now=210.0) == pytest.approx(10.0)


def test_progress_is_a_fraction():
    p = PlayerState(queue=Queue([track_from_cached(make(duration=100))]))
    p.start(now=0.0)
    assert p.progress(now=50.0) == pytest.approx(0.5)


def test_progress_is_zero_for_live_streams():
    p = PlayerState(queue=Queue([track_from_cached(make(duration=0))]))
    p.start(now=0.0)
    assert p.progress(now=500.0) == 0.0


def test_finish_advances_and_archives_history():
    p = PlayerState(queue=Queue([track_from_cached(make("a")), track_from_cached(make("b"))]))
    p.start()
    nxt = p.finish_current()
    assert nxt.title == "b"
    assert p.history[0].title == "a"
    assert p.history[0].status is TrackStatus.FINISHED


def test_finish_on_last_track_stops():
    p = PlayerState(queue=Queue([track_from_cached(make("only"))]))
    p.start()
    assert p.finish_current() is None
    assert p.is_stopped
    assert p.started_at is None


def test_finish_honours_loop_count():
    p = PlayerState(queue=Queue([track_from_cached(make("a")), track_from_cached(make("b"))]))
    p.loop = 1
    p.start()
    nxt = p.finish_current()
    # 'a' rotated to the back, so 'b' becomes the head.
    assert nxt.title == "b"
    assert p.loop == 0


def test_fail_current_drops_the_bad_track():
    p = PlayerState(queue=Queue([track_from_cached(make("bad")), track_from_cached(make("ok"))]))
    p.start()
    nxt = p.fail_current("403 Forbidden")
    assert nxt.title == "ok"
    assert p.history == []  # failures are not archived as played


def test_history_is_bounded():
    p = PlayerState()
    for i in range(80):
        p.queue.add(track_from_cached(make(f"t{i}")))
        p.start()
        p.finish_current()
    assert len(p.history) <= 50


def test_stop_clears_everything():
    p = PlayerState(queue=Queue([track_from_cached(make())]))
    p.loop = 3
    p.start()
    p.stop()
    assert len(p.queue) == 0 and p.loop == 0 and p.is_stopped


def test_snapshot_is_json_safe():
    import json

    p = PlayerState(queue=Queue([track_from_cached(make(duration=100))]))
    p.start()
    json.dumps(p.snapshot())  # must not raise


# ── PlayerRegistry ─────────────────────────────────────────────────────────


def test_registry_creates_state_lazily():
    r = PlayerRegistry()
    assert 5 not in r
    assert r.get(5) is r.get(5)  # same object
    assert 5 in r


def test_registry_lock_is_per_chat():
    r = PlayerRegistry()
    assert r.lock(1) is r.lock(1)
    assert r.lock(1) is not r.lock(2)


def test_registry_locks_are_mutually_exclusive():
    """Two mutations on one chat must not interleave."""
    r = PlayerRegistry()
    order = []

    async def worker(n):
        async with r.lock(1):
            order.append(f"enter{n}")
            await asyncio.sleep(0)
            order.append(f"exit{n}")

    async def main():
        await asyncio.gather(worker(1), worker(2))

    asyncio.run(main())
    # No interleaving: each enter is followed by its own exit.
    assert order[0].startswith("enter") and order[1].startswith("exit")
    assert order[2].startswith("enter") and order[3].startswith("exit")


def test_registry_active_chats():
    r = PlayerRegistry()
    a = r.get(1)
    a.state = PlaybackState.PLAYING
    r.get(2)
    assert r.active_chats() == [1]


def test_registry_drop_and_clear():
    r = PlayerRegistry()
    r.get(1)
    r.drop(1)
    assert 1 not in r
    r.get(2)
    r.clear()
    assert len(r) == 0


# ── duration formatting ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "secs, expect",
    [(0, "LIVE"), (5, "0:05"), (65, "1:05"), (210, "3:30"), (3661, "1:01:01")],
)
def test_fmt_duration(secs, expect):
    assert fmt_duration(secs) == expect


def test_fmt_duration_survives_junk():
    assert fmt_duration(None) == "LIVE"
    assert fmt_duration("abc") == "LIVE"


# ── messages: the gibberish regression ─────────────────────────────────────


NASTY = 'Tom & Jerry "Theme" <live> — 100%'


def _unescaped(text: str) -> list[str]:
    """Values that would reach Telegram as literal text if unescaped."""
    return [NASTY]


def test_now_playing_escapes_nasty_titles():
    """The whole notice must survive the real v2 parser byte-for-byte.

    Asserting on entity spellings would be brittle; what the user actually
    sees is the parser's output, so that is what is asserted.
    """
    from telethon.extensions.markdownv2 import Markdown as V2

    t = track_from_cached(make(NASTY, NASTY, 100))
    out = now_playing_msg(t, PlayerState(queue=Queue([t])))
    rendered, _ = V2().parse(out)
    # The hostile title is present verbatim, entity intact, nothing dropped.
    assert NASTY in rendered
    assert "<live>" in rendered
    assert "&amp;" not in rendered


@pytest.mark.parametrize(
    "builder",
    [now_playing_msg, queued_msg, finished_msg],
)
def test_every_notice_escapes_hostile_titles(builder):
    t = track_from_cached(make(NASTY, NASTY, 100))
    from telethon.extensions.markdownv2 import Markdown as V2

    out = builder(t, PlayerState(queue=Queue([t])))
    rendered, _ = V2().parse(out)
    # The value survives the parser instead of being dropped or mangled.
    assert NASTY in rendered


def test_safe_escapes_and_truncates():
    """``safe`` now escapes for the fork's v2 parse mode, not for HTML.

    The contract is "the value round-trips through the real parser
    unchanged", which is stronger than any string comparison - see
    test_now_playing_escapes_nasty_titles.
    """
    from telethon.extensions.markdownv2 import Markdown as V2

    assert V2().parse(safe(NASTY))[0] == NASTY
    assert len(safe("x" * 100, limit=10)) == 10
    assert safe(None) == ""


def test_now_playing_shows_position_for_queued_track():
    a, b = track_from_cached(make("A", duration=100)), track_from_cached(make("B", duration=100))
    p = PlayerState(queue=Queue([a, b]))
    p.start()
    out = now_playing_msg(a, p)
    assert "queued 1" in out
    # Elapsed/total line, both sides bounded by the 100s track duration.
    assert "▶" in out and " / 1:40" in out


def test_queued_msg_reports_position():
    a, b, c = (track_from_cached(make(x, duration=100)) for x in "ABC")
    p = PlayerState(queue=Queue([a, b, c]))
    p.start()
    out = queued_msg(c, p)
    assert "position 2" in out


def test_queue_list_marks_current_with_play_arrow():
    a, b = track_from_cached(make("A", duration=100)), track_from_cached(make("B", duration=100))
    p = PlayerState(queue=Queue([a, b]))
    p.start()
    out = queue_list_msg(p)
    assert "▶" in out
    assert "A" in out and "B" in out


def test_queue_list_on_empty_queue():
    assert "Nothing Playing" in queue_list_msg(PlayerState())


def test_nothing_playing_when_paused():
    p = PlayerState()
    p.state = PlaybackState.PAUSED
    assert "Paused" in nothing_playing_msg(p)


def test_error_msg_escapes_the_error_text():
    out = error_msg("play <track>", '403 & "forbidden"')
    assert "&amp;" in out
    assert "403" in out


def test_finished_msg_names_the_next_track():
    a, b = track_from_cached(make("A")), track_from_cached(make("B"))
    p = PlayerState(queue=Queue([a, b]))
    p.start()
    p.finish_current()  # advances the queue, so 'b' is now current
    out = finished_msg(a, p)
    assert "Next up" in out and "B" in out


def test_loop_msg_off_and_on():
    t = track_from_cached(make("A"))
    assert "Loop Off" in loop_msg(t, 0)
    assert "Looping 3x" in loop_msg(t, 3)
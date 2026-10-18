"""Offline rule tests for the pure Ludo engine."""
import importlib.util
import random
import sys
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("ludo_arcade", Path(__file__).parents[1] / "pyUltroid/_core/ludo_arcade.py")
ludo = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ludo
spec.loader.exec_module(ludo)
Ludo = ludo.Ludo


def game(count=2):
    g = Ludo(1, now=0)
    for uid in range(2, count + 1): g.apply("join", uid, 0)
    g.apply("start", 1, 0)
    return g


def test_lobby_host_capacity_cancel_and_host_property():
    g = Ludo(1)
    assert g.host == 1 and all(hasattr(p, "user") for p in g.players)
    for uid in (2, 3, 4): g.apply("join", uid, 1)
    try: g.apply("join", 5, 2)
    except ValueError: pass
    else: assert False
    g.apply("cancel", 1, 3)
    assert g.status == "cancelled"
    g = game(); g.apply("cancel", 1, 4)
    assert g.status == "cancelled"


def test_six_enters_and_extra_turn_then_non_six_advances():
    g = game()
    g.apply("roll", 1, 1, lambda: 6)
    assert g.legal_moves() == [0, 1, 2, 3]
    g.apply("move:0", 1, 2)
    assert g.players[0].tokens[0] == 0 and g.current_player == 1
    g.apply("roll", 1, 3, lambda: 2); g.apply("move:0", 1, 4)
    assert g.players[0].tokens[0] == 2 and g.current_player == 2


def test_three_sixes_forfeit_third_roll_not_prior_moves_and_reset_between_turns():
    g = game()
    for i in range(3):
        g.apply("roll", 1, i + 1, lambda: 6)
        if i < 2: g.apply("move:0", 1, i + 1.1)
    assert g.current_player == 2 and g.players[0].tokens[0] == 6
    assert "third roll forfeited" in g.events[-1]
    # The chain is cleared by turn rotation, so three later sixes count afresh.
    g.apply("roll", 2, 5, lambda: 6); g.apply("move:0", 2, 5.1)
    assert g.sixes == 1


def test_blockade_blocks_crossing_landing_and_yard_entry():
    g = game(); a, b = g.players
    # Opponent stack at physical 5 bars crossing it despite open destination 7.
    b.tokens[:2] = [44, 44]
    a.tokens[0] = 3
    g.apply("roll", 1, 1, lambda: 4)
    assert 0 not in g.legal_moves()
    g._dice = None
    # Start square is also blocked; entering from yard is forbidden.
    b.tokens[:2] = [-1, -1]; b.tokens[0:2] = [39, 39]
    a.tokens[0] = -1
    g.active = 0
    g.apply("roll", 1, 2, lambda: 6)
    assert 0 not in g.legal_moves()


def test_blockade_does_not_block_owner_and_home_transition_checks_track_only():
    g = game(); a, b = g.players
    a.tokens[:2] = [4, 4]
    g.apply("roll", 1, 1, lambda: 2)
    assert 0 in g.legal_moves()  # owner may traverse own blockade
    g._dice = None
    # Progress 50 to 56 traverses physical 51, then private home lane; no wrap.
    a.tokens[0] = 50; b.tokens[:2] = [0, 0]
    g.apply("roll", 1, 2, lambda: 6)
    assert 0 in g.legal_moves()
    # But an enemy blockade on the traversed physical track square bars it.
    b.tokens[:2] = [38, 38]
    assert 0 not in g.legal_moves()


def test_safe_square_prevents_capture_and_unsafe_capture_returns_yard():
    g = game(); a, b = g.players
    a.tokens[0] = 13
    g.apply("roll", 1, 1, lambda: 6); g.apply("move:0", 1, 2)
    assert a.tokens[0] == 19
    a.tokens[0] = 22; b.tokens[0] = 3; g.active = 1
    g.apply("roll", 2, 3, lambda: 6); g.apply("move:0", 2, 4)
    assert a.tokens[0] == -1 and "captured" in g.events[-1]
    # Landing on safe square does not capture.
    g.players[0].tokens[0] = 8; g.players[1].tokens[0] = 3; g.active = 1
    g.apply("roll", 2, 5, lambda: 5); g.apply("move:0", 2, 6)
    assert g.players[0].tokens[0] == 8


def test_resignation_from_any_seat_continues_until_one_unfinished():
    g = game(4)
    g.apply("resign", 3, 1)
    assert g.status == "playing" and g.current_player == 1
    assert g.players[2].tokens == [-1] * 4 and g.players[2].resigned
    g.apply("resign", 4, 2)
    assert g.status == "playing" and g.current_player == 1
    g.apply("resign", 2, 3)
    assert g.status == "finished" and g.winners == [1]


def test_afk_any_player_skips_current_since_latest_valid_activity():
    g = game()
    try: g.apply("skip", 2, 89)
    except ValueError: pass
    else: assert False
    # A non-current seated player may skip the inactive current turn.
    g.apply("skip", 2, 90)
    assert g.current_player == 2
    # Valid roll resets the AFK clock; even a pending unplayed roll is activity.
    g.apply("roll", 2, 91, lambda: 6)
    try: g.apply("skip", 1, 180)
    except ValueError: pass
    else: assert False
    g.apply("skip", 1, 181)
    assert g.current_player == 1 and g.dice is None


def test_invalid_move_wrong_turn_rejected_and_bounded_roll_history():
    g = game()
    try: g.apply("roll", 2, 1, lambda: 5)
    except ValueError: pass
    else: assert False
    g.apply("roll", 1, 1, lambda: 2)
    try: g.apply("move:0", 1, 2)
    except ValueError: pass
    else: assert False
    # Directly complete repeated extra turns to exercise bounded audit history.
    g._dice = None; g.players[0].tokens[0] = 0
    for i in range(150):
        g.active = 0; g._dice = None; g.sixes = 0; g.players[0].tokens = [0, -1, -1, -1]
        g.apply("roll", 1, i + 10, lambda: 6)
        g.apply("move:0", 1, i + 10.1)
    assert len(g.rolls) == ludo.ROLL_LIMIT
    for i in range(150): g._event(str(i))
    assert len(g.events) == ludo.EVENT_LIMIT


def test_exact_finish_distinct_ranking_and_host_rematch_requires_rejoin():
    g = game(3)
    g.players[0].tokens = [57, 57, 57, 51]
    g.apply("roll", 1, 1, lambda: 6); g.apply("move:3", 1, 2)
    assert g.winners == [1] and g.status == "playing" and g.current_player == 2
    g.apply("resign", 2, 3)
    assert g.status == "finished" and g.winners == [1, 3]
    g.apply("rematch", 1, 4)
    assert g.status == "lobby" and g.host == 1 and len(g.players) == 1
    g.apply("join", 2, 5)
    assert len(g.players) == 2 and not g.players[1].finished


def test_deterministic_full_match_invariants():
    g = game(3)
    # Resign two seats; final active seat is ranked and match terminates.
    g.apply("resign", 2, 1); g.apply("resign", 3, 2)
    assert g.status == "finished" and g.winners == [1]
    assert len(g.winners) == len(set(g.winners))
    assert all(-1 <= x <= ludo.FINISH for p in g.players for x in p.tokens)
    assert len(g.board_text()) < 2000
    assert "rankings" in g.status_text()


def test_off_turn_resign_preserves_pending_die_and_clock():
    g = game(4)
    g.apply("roll", 1, 1, lambda: 6)
    g.apply("resign", 3, 2)
    assert g.current_player == 1 and g.dice == 6 and g.sixes == 1
    assert g.last_activity == 1
    g.apply("move0", 1, 3)
    assert g.players[0].tokens[0] == 0


def test_afk_skip_cannot_cascade_without_a_fresh_idle_period():
    g = game(3)
    g.apply("skip", 2, 90)
    assert g.current_player == 2
    with pytest.raises(ValueError):
        g.apply("skip", 3, 91)
    g.apply("skip", 3, 180)
    assert g.current_player == 3


def test_capture_on_six_does_not_erase_consecutive_six_counter():
    g = game()
    a, b = g.players
    a.tokens[0], b.tokens[0] = 3, 48  # Physical 9: unsafe destination.
    g.apply("roll", 1, 1, lambda: 6)
    g.apply("move0", 1, 2)
    assert b.tokens[0] == -1 and g.sixes == 1
    g.apply("roll", 1, 3, lambda: 6)
    g.apply("move0", 1, 4)
    g.apply("roll", 1, 5, lambda: 6)
    assert g.current_player == 2 and a.tokens[0] == 15


def test_exact_home_overshoot_and_final_rankings():
    g = game()
    g.players[0].tokens = [57, 57, 57, 56]
    g.apply("roll", 1, 1, lambda: 2)
    assert g.current_player == 2 and g.dice is None
    g.active = 0
    g.apply("roll", 1, 2, lambda: 1)
    assert g.legal_moves() == [3]
    g.apply("move3", 1, 3)
    assert g.status == "finished" and g.winners == [1, 2]
    assert g.players[0].finished and not g.players[1].finished


def test_cancelled_table_can_reopen_only_by_host_with_fresh_consent():
    g = game(4)
    g.apply("cancel", 1, 1)
    with pytest.raises(ValueError):
        g.apply("rematch", 2, 2)
    g.apply("rematch", 1, 2)
    assert g.status == "lobby" and [p.user for p in g.players] == [1]
    with pytest.raises(ValueError):
        g.apply("start", 1, 3)


@pytest.mark.parametrize("count", (2, 3, 4))
@pytest.mark.parametrize("seed", (2, 7, 230))
def test_complete_seeded_matches_without_resignation(count, seed):
    rng = random.Random(seed)
    g = game(count)
    for tick in range(20000):
        if g.status != "playing":
            break
        current = g.current_player
        if g.dice is None:
            g.apply("roll", current, tick, lambda: rng.randint(1, 6))
        else:
            legal = g.legal_moves()
            assert legal
            player = g.players[g.active]
            token = max(legal, key=lambda i: player.tokens[i])
            g.apply(f"move{token}", current, tick)
        assert all(-1 <= pos <= ludo.FINISH for player in g.players for pos in player.tokens)
        assert len(g.winners) == len(set(g.winners))
        assert len(g.events) <= ludo.EVENT_LIMIT and len(g.rolls) <= ludo.ROLL_LIMIT
        assert 0 <= g.sixes < 3
        if g.status == "playing":
            assert not g.players[g.active].finished and not g.players[g.active].resigned
    assert g.status == "finished"
    assert len(g.winners) == count and len(set(g.winners)) == count
    board = g.board_text()
    assert len(board) < 2000 and max(map(len, board.splitlines())) <= 45

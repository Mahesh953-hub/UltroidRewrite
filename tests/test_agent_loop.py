"""Loop guards: the layer that decides a turn is not converging.

A guard bug is worse than no guard, so these tests pin the thresholds at their
edges and prove a healthy long turn is never blocked.
"""

from __future__ import annotations

import pytest

from pyUltroid.agent._loop import (
    Decision,
    LoopGuard,
    is_failure,
    signature_of,
)


def _guard(**kw):
    return LoopGuard(**kw)


def _fail(guard, tool, args, rounds=1):
    for _ in range(rounds):
        guard.before_call(tool, args)
        guard.observe(tool, args, {"error": "boom"})
        guard.begin_round(progressed=False)


# ── signature identity ──────────────────────────────────────────────────────


def test_signature_ignores_argument_key_order():
    a = signature_of("t", {"x": 1, "y": 2})
    b = signature_of("t", {"y": 2, "x": 1})
    assert a == b


def test_signature_separates_different_calls():
    assert signature_of("t", {"x": 1}) != signature_of("t", {"x": 2})
    assert signature_of("a", {"x": 1}) != signature_of("b", {"x": 1})


def test_signature_survives_unserialisable_arguments():
    assert isinstance(signature_of("t", {"o": object()}), str)


# ── failure classification ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "result",
    [
        {"error": "x"},
        {"denied": True},
        {"blocked": True},
        {"ok": False},
        {"exit_code": 1},
        "Error: nope",
    ],
)
def test_failures_are_recognised(result):
    assert is_failure(result) is True


@pytest.mark.parametrize(
    "result",
    [{}, {"ok": True}, {"exit_code": 0}, {"stdout": "x"}, "plain text", None, 0],
)
def test_non_failures_are_not_flagged(result):
    assert is_failure(result) is False


# ── thresholds ──────────────────────────────────────────────────────────────


def test_first_call_is_allowed():
    assert _guard().before_call("t", {}).decision is Decision.ALLOW


def test_repeat_failure_warns_at_two():
    g = _guard()
    _fail(g, "t", {"a": 1}, rounds=2)
    verdict = g.before_call("t", {"a": 1})
    assert verdict.decision is Decision.WARN
    assert verdict.code == "repeated_failure"


def test_repeat_failure_blocks_at_five():
    g = _guard()
    _fail(g, "t", {"a": 1}, rounds=5)
    assert g.before_call("t", {"a": 1}).decision is Decision.BLOCK


def test_a_different_call_is_not_punished_for_the_first_one_failing():
    g = _guard()
    _fail(g, "t", {"a": 1}, rounds=5)
    assert g.before_call("t", {"a": 2}).decision is not Decision.BLOCK


def test_a_success_resets_progress_but_not_the_failure_tally():
    g = _guard()
    _fail(g, "t", {"a": 1}, rounds=3)
    g.observe("t", {"b": 2}, {"ok": True})
    g.begin_round(progressed=True)
    # the exact-failure count is per-signature, so a different arg is clean
    assert g.before_call("t", {"b": 2}).decision is not Decision.BLOCK


def test_same_tool_failures_halt():
    g = _guard()
    for i in range(8):
        g.observe("flaky", {"i": i}, {"error": "x"})
    verdict = g.before_call("flaky", {"i": 99})
    assert verdict.decision is Decision.HALT
    assert verdict.halt is True


def test_same_tool_warns_before_halting():
    g = _guard()
    for i in range(3):
        g.observe("flaky", {"i": i}, {"error": "x"})
    assert g.before_call("flaky", {"i": 99}).decision is Decision.WARN


def test_no_progress_blocks():
    """A stall is re-running a bad call, not merely elapsed rounds."""
    g = _guard()
    for _ in range(5):
        g.before_call("t", {"a": 1})  # same call, already failed -> repeat
        g.observe("t", {"a": 1}, {"error": "x"})
        g.begin_round(progressed=False)
    assert g.before_call("t", {"a": 1}).decision is Decision.BLOCK


def test_trying_new_calls_is_not_a_stall():
    """A model investigating with fresh calls must not be cut off."""
    g = _guard()
    for i in range(12):
        verdict = g.before_call("t", {"a": i})
        assert verdict.decision is not Decision.BLOCK
        g.observe("t", {"a": i}, {"error": "x"})
        g.begin_round(progressed=False)


def test_progress_interrupts_the_stall_counter():
    g = _guard()
    for _ in range(4):
        g.before_call("t", {"a": 1})
        g.observe("t", {"a": 1}, {"error": "x"})
        g.begin_round(progressed=False)
    g.begin_round(progressed=True)
    assert g.before_call("t", {"a": 1}).decision is not Decision.BLOCK


def test_a_healthy_long_turn_is_never_blocked():
    """Six successful rounds must not trip anything - the deep-research case."""
    g = _guard()
    for i in range(20):
        verdict = g.before_call("search", {"q": i})
        assert verdict.decision is Decision.ALLOW
        g.observe("search", {"q": i}, {"ok": True})
        g.begin_round(progressed=True)


def test_alternating_success_and_failure_is_allowed():
    g = _guard()
    for i in range(10):
        g.before_call("t", {"i": i})
        g.observe("t", {"i": i}, {"ok": True} if i % 2 else {"error": "x"})
        g.begin_round(progressed=True)
    assert g.before_call("t", {"i": 99}).decision is not Decision.BLOCK


# ── blocked results ─────────────────────────────────────────────────────────


def test_blocked_result_is_shaped_like_a_tool_result():
    g = _guard()
    _fail(g, "t", {"a": 1}, rounds=6)
    payload = g.before_call("t", {"a": 1}).as_result()
    assert payload["blocked"] is True
    assert payload["guardrail"] == "repeated_failure"
    assert payload["tool"] == "t"
    assert payload["occurrences"] == 6
    assert payload["error"]


def test_blocked_result_tells_the_model_not_to_retry():
    g = _guard()
    _fail(g, "t", {"a": 1}, rounds=6)
    assert "will not help" in g.before_call("t", {"a": 1}).as_result()["error"]


# ── halt / telemetry ────────────────────────────────────────────────────────


def test_halt_is_sticky():
    g = _guard()
    assert g.halted is False
    g.halt("tool_misbehaving")
    assert g.halted is True
    assert g.halted_reason == "tool_misbehaving"


def test_snapshot_reports_state():
    g = _guard()
    g.observe("t", {"a": 1}, {"error": "x"})
    g.observe("t", {"a": 2}, {"ok": True})
    snap = g.snapshot()
    assert snap["tool_calls"] == {"t": 2}
    assert snap["tool_fail_counts"] == {"t": 1}
    assert snap["stale_rounds"] == 0


def test_thresholds_are_overridable():
    g = LoopGuard(exact_fail_warn=1, exact_fail_block=2)
    _fail(g, "t", {"a": 1}, rounds=1)
    assert g.before_call("t", {"a": 1}).decision is Decision.WARN
    _fail(g, "t", {"a": 1}, rounds=1)
    assert g.before_call("t", {"a": 1}).decision is Decision.BLOCK

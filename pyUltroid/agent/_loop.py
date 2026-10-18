"""Loop guards: the layer that stops a turn from spinning.

The agentic loop is correct about *what* to do each round and has no opinion
about *how many* rounds are sensible. A tool that fails deterministically, a
model that keeps re-issuing the same call, or a turn that makes no progress
between rounds all burn rounds until the cap - and a cap is a blunt
instrument, because it also fires on the legitimate deep-research turn that
needs 6 calls.

This is the same idea as Hermes' ``ToolCallGuardrailController``: a decision
per call, made *before* dispatch, that either allows, warns, blocks or halts
the turn. The thresholds are deliberate - warn early enough to be useful
guidance the model can act on, block once repeating is pointless, halt only
when the turn is provably not converging.

A blocked call is not dropped. It comes back to the model as a synthetic tool
result carrying the reason, so the model gets to choose what to do instead of
silently losing the result it asked for.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable

__all__ = [
    "Decision",
    "GuardVerdict",
    "LoopGuard",
    "NO_PROGRESS_BLOCK",
    "is_failure",
    "signature_of",
]


class Decision(str, Enum):
    """What to do about one tool call."""

    ALLOW = "allow"
    WARN = "warn"
    BLOCK = "block"
    HALT = "halt"


#: Identical failing signature: past this the repetition is the bug.
_WARN_AT: dict[str, int] = {
    "exact_fail_warn": 2,
    "exact_fail_block": 5,
    "same_tool_warn": 3,
    "same_tool_halt": 8,
    "no_progress_warn": 2,
    "no_progress_block": 5,
}
#: Alias kept for callers that only care about the stall thresholds.
NO_PROGRESS_BLOCK = _WARN_AT["no_progress_block"]


@dataclass(frozen=True, slots=True)
class GuardVerdict:
    """The decision for one call, plus the reason the model is told."""

    decision: Decision
    tool_name: str = ""
    code: str = ""
    message: str = ""
    count: int = 0
    signature: str = ""

    @property
    def blocked(self) -> bool:
        return self.decision in (Decision.BLOCK, Decision.HALT)

    @property
    def halt(self) -> bool:
        return self.decision is Decision.HALT

    def as_result(self) -> dict[str, Any]:
        """The synthetic tool result a blocked call produces.

        Shaped like a real tool result so the provider serializers treat it
        as one; the model is told plainly not to retry the same call, because
        the whole point is that repeating it is what got blocked.
        """
        return {
            "error": self.message or "blocked by loop guard",
            "guardrail": self.code,
            "blocked": True,
            "tool": self.tool_name,
            "occurrences": self.count,
        }


def signature_of(tool_name: str, arguments: Any) -> str:
    """A stable identity for one call, so repeats are recognisable.

    Key order in the arguments must not change the signature, so the payload
    is sorted before hashing.
    """
    try:
        payload = json.dumps(arguments, sort_keys=True, default=str)
    except (TypeError, ValueError):
        payload = repr(arguments)
    digest = hashlib.sha256(f"{tool_name}:{payload}".encode()).hexdigest()
    return digest[:16]


def is_failure(result: Any) -> bool:
    """Whether a tool result counts as a failure.

    Anything the executor marked as an error, a denial, a guardrail block, or
    a bare exception string. A result that merely *looks* alarming is not a
    failure - only an explicit signal is.
    """
    if not isinstance(result, dict):
        return isinstance(result, str) and result.startswith("Error")
    if result.get("error"):
        return True
    if result.get("denied") or result.get("blocked"):
        return True
    ok = result.get("ok")
    if isinstance(ok, bool):
        return not ok
    exit_code = result.get("exit_code")
    if isinstance(exit_code, int):
        return exit_code != 0
    return False


class LoopGuard:
    """Per-turn stall detection.

    One instance per turn. It is not thread-safe and does not need to be: the
    loop drives it from a single coroutine, and a shared instance across turns
    would leak one turn's failures into the next.
    """

    def __init__(
        self,
        *,
        exact_fail_warn: int = None,
        exact_fail_block: int = None,
        same_tool_warn: int = None,
        same_tool_halt: int = None,
        no_progress_warn: int = None,
        no_progress_block: int = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.exact_fail_warn = exact_fail_warn or _WARN_AT["exact_fail_warn"]
        self.exact_fail_block = exact_fail_block or _WARN_AT["exact_fail_block"]
        self.same_tool_warn = same_tool_warn or _WARN_AT["same_tool_warn"]
        self.same_tool_halt = same_tool_halt or _WARN_AT["same_tool_halt"]
        self.no_progress_warn = no_progress_warn or _WARN_AT["no_progress_warn"]
        self.no_progress_block = no_progress_block or _WARN_AT["no_progress_block"]
        self._clock = clock

        self._fail_counts: dict[str, int] = {}
        self._tool_fail_counts: dict[str, int] = {}
        self._tool_calls: dict[str, int] = {}
        self._pending_signatures: set[str] = set()
        self._last_progress: float = self._clock()
        self._stale_rounds: int = 0
        self._round_repeated: bool = False
        self.halted_reason: str = ""

    # ── pre-dispatch ──────────────────────────────────────────────────────

    def before_call(self, tool_name: str, arguments: Any) -> GuardVerdict:
        """Decide whether *tool_name* may run at all.

        The no-progress check lives here rather than after the fact, because
        it is the only way to stop the round that would otherwise be the
        sixth identical one.
        """
        signature = signature_of(tool_name, arguments)
        if signature in self._pending_signatures:
            return GuardVerdict(
                decision=Decision.BLOCK,
                tool_name=tool_name,
                code="duplicate_in_flight",
                message="An identical call is already running; wait for its result.",
                count=1,
                signature=signature,
            )
        failures = self._fail_counts.get(signature, 0)
        if failures >= self.exact_fail_block:
            self._note_repeat()
            return GuardVerdict(
                decision=Decision.BLOCK,
                tool_name=tool_name,
                code="repeated_failure",
                message=(
                    f"This exact call has failed {failures} times. Repeating it "
                    "will not help - change the arguments, use a different "
                    "tool, or report the failure."
                ),
                count=failures,
                signature=signature,
            )
        if failures >= self.exact_fail_warn:
            self._note_repeat()
            self._pending_signatures.add(signature)
            return GuardVerdict(
                decision=Decision.WARN,
                tool_name=tool_name,
                code="repeated_failure",
                message=(f"This exact call has already failed {failures} times."),
                count=failures,
                signature=signature,
            )

        if self.stalled:
            self._note_repeat()
            return GuardVerdict(
                decision=Decision.BLOCK,
                tool_name=tool_name,
                code="no_progress",
                message=(
                    f"{self.no_progress_block} consecutive rounds re-ran a call "
                    "that had already failed. Stop calling tools and answer "
                    "with what you already know, or say what is missing."
                ),
                count=self._stale_rounds,
                signature=signature,
            )

        tool_failures = self._tool_fail_counts.get(tool_name, 0)
        if tool_failures >= self.same_tool_halt:
            return GuardVerdict(
                decision=Decision.HALT,
                tool_name=tool_name,
                code="tool_misbehaving",
                message=(
                    f"'{tool_name}' has failed {tool_failures} times. This turn "
                    "is being stopped."
                ),
                count=tool_failures,
            )
        if tool_failures >= self.same_tool_warn:
            self._pending_signatures.add(signature)
            return GuardVerdict(
                decision=Decision.WARN,
                tool_name=tool_name,
                code="tool_failing",
                message=f"'{tool_name}' has failed {tool_failures} times so far.",
                count=tool_failures,
                signature=signature,
            )
        self._pending_signatures.add(signature)
        return GuardVerdict(
            decision=Decision.ALLOW, tool_name=tool_name, signature=signature
        )

    # ── post-dispatch ─────────────────────────────────────────────────────

    def observe(self, tool_name: str, arguments: Any, result: Any) -> None:
        """Record one completed call. A success resets the stall timer."""
        signature = signature_of(tool_name, arguments)
        self._pending_signatures.discard(signature)
        self._tool_calls[tool_name] = self._tool_calls.get(tool_name, 0) + 1
        if is_failure(result):
            self._fail_counts[signature] = self._fail_counts.get(signature, 0) + 1
            self._tool_fail_counts[tool_name] = (
                self._tool_fail_counts.get(tool_name, 0) + 1
            )
            return
        # Any success is progress, whatever else happened this round.
        self._last_progress = self._clock()

    def note_progress(self) -> None:
        """Reset the stall timer explicitly (e.g. after a successful round)."""
        self._last_progress = self._clock()

    def _note_repeat(self) -> None:
        """Mark the in-flight round as re-running something already failed."""
        self._round_repeated = True

    @property
    def stalled(self) -> bool:
        """Whether the turn is re-running a call that already failed.

        Deliberately not "rounds elapsed": a model that tries five different
        commands and has them all fail is investigating, not looping, and
        blocking it would stop it from finding the one that works. Only a
        repeat of a known-bad call counts.
        """
        return self._stale_rounds >= self.no_progress_block

    def begin_round(self, *, progressed: bool) -> None:
        """Open a round, tracking consecutive rounds that re-ran a bad call.

        A success clears the tally. A round that only tried *new* calls also
        clears it - nothing has been proven wrong twice yet.
        """
        if progressed or not self._round_repeated:
            self._stale_rounds = 0
            if progressed:
                self.note_progress()
            return
        self._stale_rounds += 1
        self._round_repeated = False

    # ── diagnostics ───────────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """State for telemetry and for asserting on in tests."""
        return {
            "fail_counts": dict(self._fail_counts),
            "tool_fail_counts": dict(self._tool_fail_counts),
            "tool_calls": dict(self._tool_calls),
            "stale_rounds": self._stale_rounds,
            "halted_reason": self.halted_reason,
        }

    def halt(self, reason: str) -> None:
        """Stop the turn after the current round."""
        self.halted_reason = reason

    @property
    def halted(self) -> bool:
        """Whether the turn was stopped outright."""
        return bool(self.halted_reason)

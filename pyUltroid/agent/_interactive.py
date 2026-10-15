"""Interactive tools: the agent can ask, confirm and collect.

These are the Hermes ``clarify`` idea in SarahUB terms. The loop does not
exit for a question - asking is an ordinary tool call whose handler blocks on
a future until the user taps a styled button or types a reply, and the answer
arrives as a normal tool result. That is what makes "ask my name" work: the
model calls ``ask_user``, the turn continues, and the name is in context by
the time it produces its final answer.

Two rules keep this from becoming a foot-gun:

* A question that is never answered resolves as ``timed_out`` with a reason,
  never an exception. A silent user is a normal outcome the model can reason
  about, not a crash.
* Every ask needs :data:`Cap.AGENT_ASK`, so a privileged grant is what buys
  the ability to interrupt a human.
"""

from __future__ import annotations

from typing import Any

from ._bridge import AskBridge

__all__ = [
    "INTERACTIVE_SCHEMAS",
    "InteractiveTools",
    "MAX_CHOICES",
    "MAX_QUESTIONS",
    "clamp_ttl",
]

#: A keyboard wider than this stops being usable on a phone.
MAX_CHOICES = 12
#: One turn may not become an interrogation.
MAX_QUESTIONS = 5
_TTL_MIN = 5.0
_TTL_MAX = 900.0
_TTL_DEFAULT = 120.0


def clamp_ttl(value: Any, default: float = _TTL_DEFAULT) -> float:
    """Coerce an LLM-supplied TTL into a sane range; junk becomes *default*."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        return default
    return max(_TTL_MIN, min(seconds, _TTL_MAX))


INTERACTIVE_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": (
                "Ask the user a question and wait for their answer. Use this "
                "when you genuinely cannot continue without something only "
                "they know - their name, a preference, a missing path, which "
                "of two options they meant. This blocks the turn until they "
                "answer or the TTL expires, so ask only what you need. If you "
                "supply choices, they get a tap-to-answer keyboard; otherwise "
                "they can just type. On expiry you get timed_out=true and must "
                "decide how to proceed - do not retry the same question."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {
                        "type": "string",
                        "description": "The question, phrased for a human.",
                    },
                    "choices": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Optional tap-to-answer options, at most "
                            f"{MAX_CHOICES}. Omit to let them type freely."
                        ),
                    },
                    "note": {
                        "type": "string",
                        "description": "Optional extra context under the question.",
                    },
                    "ttl": {
                        "type": "number",
                        "description": (
                            "Seconds to wait, clamped to "
                            f"[{int(_TTL_MIN)}, {int(_TTL_MAX)}]. Default "
                            f"{int(_TTL_DEFAULT)}."
                        ),
                    },
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "confirm_action",
            "description": (
                "Ask the user to confirm something you are about to do that "
                "cannot be undone - deleting, force-updating, sending on their "
                "behalf. Returns their yes/no. Use it before a destructive "
                "shell command or a bulk send, not for routine work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "what": {
                        "type": "string",
                        "description": "What you are about to do, concretely.",
                    },
                    "yes_label": {"type": "string", "description": "Confirm button text."},
                    "no_label": {"type": "string", "description": "Cancel button text."},
                    "ttl": {"type": "number", "description": "Seconds to wait."},
                },
                "required": ["what"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "collect_fields",
            "description": (
                "Collect several answers in one go - a short form. Each field "
                "is asked in turn with its own keyboard. Use it instead of "
                "repeating ask_user when you need more than one thing, so the "
                "user is interrupted once rather than five times."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Field label."},
                                "question": {"type": "string"},
                                "choices": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                            },
                            "required": ["name", "question"],
                        },
                        "description": (
                            f"Between 1 and {MAX_QUESTIONS} fields, asked in order."
                        ),
                    },
                    "ttl": {"type": "number", "description": "Seconds per field."},
                },
                "required": ["fields"],
            },
        },
    },
]


class InteractiveTools:
    """The three interactive tools, over one shared :class:`AskBridge`.

    The bridge is injected rather than created, so a chat that already has one
    keeps a single TTL story and the pending-question map stays inspectable
    from one place.
    """

    def __init__(self, bridge: AskBridge | None = None, *, chat_id: int = 0) -> None:
        self.bridge = bridge if bridge is not None else AskBridge()
        self.chat_id = int(chat_id)

    def bind(self, chat_id: int) -> None:
        """Point the tools at another chat (the runtime is per-request)."""
        self.chat_id = int(chat_id)

    # ── ask_user ──────────────────────────────────────────────────────────

    async def ask_user(
        self,
        question: str,
        choices: list[str] | None = None,
        note: str = "",
        ttl: float | None = None,
    ) -> dict:
        """Put one question to the user and return their answer.

        A model-supplied ``ttl`` overrides the bridge default, but only after
        clamping. Supplying nothing leaves the bridge's own TTL in charge, so
        a bridge configured short (tests, a chat that should not nag) is not
        silently overridden by a hardcoded default.
        """
        options = _clean_choices(choices)
        outcome = await self.bridge.ask(
            self.chat_id,
            str(question),
            options,
            note=str(note or ""),
            ttl=None if ttl is None else clamp_ttl(ttl),
        )
        payload = outcome.as_dict()
        if options:
            payload["choices_offered"] = list(options)
        return payload

    # ── confirm_action ────────────────────────────────────────────────────

    async def confirm_action(
        self,
        what: str,
        yes_label: str = "Yes, do it",
        no_label: str = "No",
        ttl: float | None = None,
    ) -> dict:
        """Ask for a yes/no before something irreversible."""
        yes = str(yes_label or "Yes")
        no = str(no_label or "No")
        outcome = await self.bridge.ask(
            self.chat_id,
            f"{what}\n\nProceed?",
            (yes, no),
            ttl=None if ttl is None else clamp_ttl(ttl),
            style="primary",
        )
        payload = outcome.as_dict()
        if payload.get("answered"):
            payload["confirmed"] = payload["user_response"] == yes
        else:
            payload["confirmed"] = False
        return payload

    # ── collect_fields ────────────────────────────────────────────────────

    async def collect_fields(
        self, fields: list[dict] | None, ttl: float | None = None
    ) -> dict:
        """Ask a short form of questions, one keyboard each."""
        specs = _clean_fields(fields)
        if not specs:
            return {"error": "collect_fields needs at least one field"}

        answers: dict[str, Any] = {}
        skipped: list[str] = []
        per_field = None if ttl is None else clamp_ttl(ttl)

        for spec in specs:
            outcome = await self.bridge.ask(
                self.chat_id,
                spec["question"],
                spec["choices"],
                ttl=per_field,
            )
            if outcome.answered:
                answers[spec["name"]] = outcome.value
            else:
                # One unanswered field ends the form: continuing would nag.
                skipped.append(spec["name"])
                answers[spec["name"]] = None
                break

        return {
            "answers": answers,
            "skipped": skipped,
            "complete": not skipped,
            "timed_out": bool(skipped),
        }

    # ── diagnostics ───────────────────────────────────────────────────────

    @property
    def pending(self) -> int:
        """Outstanding questions. A non-zero value after a turn is a bug."""
        return self.bridge.pending


def _clean_choices(choices: Any) -> tuple[str, ...]:
    """Normalise and bound a model-supplied choice list."""
    if not choices:
        return ()
    if isinstance(choices, str):
        choices = [choices]
    cleaned = [str(c).strip() for c in choices if str(c).strip()]
    # Duplicates would produce two identical buttons resolving one future.
    seen: set[str] = set()
    unique: list[str] = []
    for choice in cleaned:
        if choice.lower() in seen:
            continue
        seen.add(choice.lower())
        unique.append(choice)
    return tuple(unique[:MAX_CHOICES])


def _clean_fields(fields: Any) -> list[dict]:
    """Normalise and bound a model-supplied form."""
    if not isinstance(fields, (list, tuple)):
        return []
    specs: list[dict] = []
    for entry in fields[:MAX_QUESTIONS]:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        question = str(entry.get("question") or "").strip()
        if not name or not question:
            continue
        specs.append(
            {"name": name, "question": question, "choices": _clean_choices(entry.get("choices"))}
        )
    return specs

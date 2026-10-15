"""Threaded delivery of an agent response into a conversation thread.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

from typing import Any, Optional

from pyUltroid.startup.logger import LOGS as _LOGS

__all__ = ["DeliveryStrategy", "DeliveryError"]


class DeliveryError(Exception):
    """A delivery phase failed after every retry was exhausted.

    Carries the phase name so the caller can map it to a stable error code
    rather than leaking a raw Telethon exception into the session card.
    """

    __slots__ = ("phase", "_cause")

    def __init__(self, phase: str, cause: BaseException) -> None:
        super().__init__(f"delivery failed at phase {phase!r}: {cause!r}")
        self.phase: str = phase
        self._cause: BaseException = cause
        self.__cause__ = cause

    @property
    def cause(self) -> BaseException:
        """The underlying exception that triggered the failure."""
        return self._cause


class DeliveryStrategy:
    """Owns emitting an agent response so it always threads to its trigger.

    The trigger is the user's message; ``placeholder`` replies to it with the
    ellipsis, and both :meth:`render` (the in-place edit path) and
    :meth:`_reply` (the fallback path) are anchored to it. A reply is only
    ever a *new* reply to the trigger - never a standalone send - which is the
    invariant the "send both as standalone" bug violated.
    """

    __slots__ = ("_trigger",)

    def __init__(self, trigger: Any) -> None:
        self._trigger: Any = trigger

    @property
    def trigger(self) -> Any:
        return self._trigger

    async def placeholder(self) -> Any:
        """Send the typing indicator, threaded to the triggering message."""
        try:
            return await self._trigger.reply("…")
        except Exception as exc:  # noqa: BLE001 - never crash the handler
            raise DeliveryError("placeholder", exc) from exc

    async def render(self, status: Any, text: str) -> Any:
        """Replace the placeholder with *text*, or fall back to a reply.

        Returns the message that ultimately carries *text* so the caller can
        record its id for the next turn's edit path. When ``status`` is
        ``None`` (placeholder never sent) the render skips straight to the
        fallback reply.
        """
        if status is not None:
            try:
                await status.edit(text)
                return status
            except Exception as exc:  # noqa: BLE001 - try the fallback path
                _LOGS.debug("status.edit failed: %r", exc)
        return await self.reply(text)

    async def reply(self, text: str) -> Any:
        """Send a fresh reply threaded to the trigger message."""
        try:
            return await self._trigger.reply(text)
        except Exception as exc:  # noqa: BLE001 - surface as structured error
            raise DeliveryError("fallback", exc) from exc

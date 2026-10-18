"""Rooted agent delivery with native nested actions and complete long answers."""

from __future__ import annotations

from io import BytesIO
from typing import Any

from ._presentation import answer_plain, answer_text, native_answer

__all__ = ["DeliveryError", "DeliveryStrategy"]


class DeliveryError(Exception):
    """Identify the failed delivery phase without exposing underlying credentials."""

    __slots__ = ("_cause", "phase")

    def __init__(self, phase: str, cause: BaseException) -> None:
        super().__init__(f"delivery failed at phase {phase!r}")
        self.phase = phase
        self._cause = cause
        self.__cause__ = cause

    @property
    def cause(self) -> BaseException:
        """Return the underlying error for sanitized diagnostic handling."""
        return self._cause


class DeliveryStrategy:
    """Edit one placeholder in its original thread without duplicate fallback replies."""

    __slots__ = ("_root", "_trigger")

    def __init__(self, trigger: Any, *, anchor: int | None = None) -> None:
        self._trigger = trigger
        self._root = anchor

    @property
    def trigger(self) -> Any:
        """Return the originating event."""
        return self._trigger

    @property
    def reply_to(self) -> Any:
        """Return the persistent session root or the initiating event."""
        return self._root if self._root is not None else self._trigger

    async def placeholder(self) -> Any:
        """Create a placeholder replying directly to the session's anchor."""
        try:
            client = getattr(self._trigger, "client", None)
            if self._root is not None and client is not None:
                return await client.send_message(
                    self._trigger.chat_id, "…", reply_to=self._root, parse_mode=None
                )
            return await self._trigger.reply("…")
        except Exception as exc:
            raise DeliveryError("placeholder", exc) from exc

    async def _attach(self, status: Any, response: Any) -> Any:
        client = getattr(self._trigger, "client", None)
        if client is None:
            raise DeliveryError(
                "attachment", ValueError("No attachment-capable client")
            )
        try:
            with BytesIO(answer_text(response).encode("utf-8")) as document:
                document.name = "answer.txt"
                await client.send_file(
                    self._trigger.chat_id,
                    document,
                    reply_to=self.reply_to,
                    caption=None,
                    parse_mode=None,
                )
            return await self.render(
                status,
                "The complete answer is attached as answer.txt.\n\nPreview:\n"
                + answer_plain(response)[:1500],
            )
        except DeliveryError:
            raise
        except Exception as exc:
            raise DeliveryError("attachment", exc) from exc

    async def render(self, status: Any, response: Any) -> Any:
        """Replace the placeholder with a native result or a complete answer attachment."""
        from pyUltroid._core.richmsg import client_can_send_rich_message, edit_rich

        client = getattr(self._trigger, "client", None)
        peer = getattr(self._trigger, "chat_id", None)
        rich_allowed = await client_can_send_rich_message(client)
        plain = answer_plain(response)
        exceeds_budget = (
            len(answer_text(response)) > 12000
            if rich_allowed
            else len(plain.encode("utf-16-le")) // 2 > 4000
        )
        if exceeds_budget:
            return await self._attach(status, response)
        if not rich_allowed:
            if status is None:
                return await self._trigger.reply(plain)
            try:
                await status.edit(plain, parse_mode=None)
                return status
            except Exception as exc:
                raise DeliveryError("plain_edit", exc) from exc
        try:
            payload = native_answer(response)
        except ValueError:
            # Native nesting/block/text budgets cannot justify losing the answer.
            return await self._attach(status, response)
        if status is None:
            return await self.reply(response)
        try:
            await edit_rich(client, peer, status, payload)
            return status
        except Exception as exc:
            raise DeliveryError("rich_edit", exc) from exc

    async def reply(self, response: Any) -> Any:
        """Send a fresh native result rooted in the same session or trigger."""
        from pyUltroid._core.richmsg import client_can_send_rich_message, send_rich

        client = getattr(self._trigger, "client", None)
        peer = getattr(self._trigger, "chat_id", None)
        try:
            if client is not None and peer is not None:
                if await client_can_send_rich_message(client):
                    return await send_rich(
                        client, peer, native_answer(response), reply_to=self.reply_to
                    )
                return await client.send_message(
                    peer,
                    answer_plain(response),
                    reply_to=self.reply_to,
                    parse_mode=None,
                )
            return await self._trigger.reply(answer_plain(response))
        except Exception as exc:
            raise DeliveryError("fallback", exc) from exc

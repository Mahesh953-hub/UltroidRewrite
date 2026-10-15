"""Raw-update routing for the business and guest executors.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import struct
from typing import Any, ClassVar

from telethon import Button
from telethon.events import Raw, register
from telethon.types import (
    InputBotInlineMessageID,
    InputBotInlineMessageID64,
    MessageEntityPre,
    UpdateBotGuestChatQuery,
    UpdateBotNewBusinessMessage,
    UpdateBusinessBotCallbackQuery,
    UpdateInlineBotCallbackQuery,
)

__all__ = ["UpdateRouter", "EXEC_SUFFIX"]

LOGS = logging.getLogger("pyUltLogs")

#: A business message ending in this is Python to run.
EXEC_SUFFIX = "#"

#: Raw update types this router consumes.
HANDLED: tuple[type, ...] = (
    UpdateBotGuestChatQuery,
    UpdateBotNewBusinessMessage,
    UpdateBusinessBotCallbackQuery,
    UpdateInlineBotCallbackQuery,
)


class UpdateRouter:
    """Turns raw Telethon updates into executor calls.

    Dispatch is a per-type table rather than an ``isinstance`` ladder, so a new
    update type is one line. Business messages fall through two handlers,
    self-destructing replies first, because the same update carries both a
    forwarded reply and a possible command.

    Owner and connection resolution come from :class:`BusinessAddons` and the
    executor from :class:`Executor`; the union class supplies both, so this
    class imports neither.
    """

    #: update type -> (method name, argument index) within the handler.
    DISPATCH: ClassVar[dict[type, str]] = {
        UpdateBotGuestChatQuery: "guest",
        UpdateBotNewBusinessMessage: "business",
        UpdateBusinessBotCallbackQuery: "business_callback",
        UpdateInlineBotCallbackQuery: "inline_callback",
    }

    def __init__(self) -> None:
        super().__init__()
        self._routes: dict[type, str] = {}

    # ── dispatch ─────────────────────────────────────────────────────────

    def _handler_for(self, update: Any) -> str | None:
        """The handler name for *update*.

        The concrete type is remembered after the first sighting, so the
        common path is one dict hit. Subclasses still resolve through
        ``isinstance``, which a bare type lookup would miss.
        """
        kind = self._routes.get(type(update))
        if kind is None:
            for candidate, name in self.DISPATCH.items():
                if isinstance(update, candidate):
                    kind = self._routes[type(update)] = name
                    break
        return kind

    async def route(self, update: Any) -> bool:
        """Hand *update* to its handler. True when a branch claimed it."""
        kind = self._handler_for(update)
        if kind is None:
            return False

        client = getattr(update, "_client", None)
        me = self.owner_id(client)

        if kind == "business":
            return await self.business(update, client, me)
        return await getattr(self, kind)(update, client, me)

    async def business(
        self, update: UpdateBotNewBusinessMessage, client: Any, me: int | None
    ) -> bool:
        """Self-destructing replies first, then the ``#`` command."""
        if await self.expired_media(update, client, me):
            return True
        return await self.run_command(update, client, me)

    @register(Raw(HANDLED))
    async def on_update(self, update: Any) -> None:
        """The Telethon raw handler. Never lets a branch kill the loop."""
        try:
            await self.route(update)
        except Exception:
            LOGS.exception("business/guest route failed")

    # ── handlers ─────────────────────────────────────────────────────────

    async def guest(
        self, update: UpdateBotGuestChatQuery, client: Any, me: int | None
    ) -> bool:
        """Answer the query with an inline article, then execute."""
        from telethon.types import PeerChannel, PeerChat

        if not isinstance(update.message.peer_id, (PeerChat, PeerChannel)):
            return False
        if not self._is_owner(update.message, me):
            return False

        msg_id = await update.answer(
            "article",
            "Guest Chat Result",
            text="<pre><code class='language-'python''>...</code></pre>",
            parse_mode="v2",
        )
        await self.execute(update, msg_id)
        return True

    async def expired_media(
        self, update: UpdateBotNewBusinessMessage, client: Any, me: int | None
    ) -> bool:
        """Forward self-destructing replies to Saved Messages."""
        if not self._is_owner(update.message, me):
            return False
        reply = getattr(update, "reply_to_message", None)
        if reply is None:
            return False
        ttl = getattr(reply.media, "ttl_seconds", None) if reply.media else None
        if not ttl:
            return False
        sender = getattr(reply.peer_id, "user_id", None)
        if not isinstance(sender, int):
            return False

        file = await client.download_media(reply.media)
        await client.send_file(
            me,
            file,
            buttons=Button.url(
                "Open Message",
                f"tg://openmessage?user_id={sender}&message_id={reply.id}",
            ),
        )
        return True

    async def run_command(
        self, update: UpdateBotNewBusinessMessage, client: Any, me: int | None
    ) -> bool:
        """A business message ending in ``#`` is Python to run."""
        message = update.message
        if not (getattr(message, "message", "") or "").rstrip().endswith(EXEC_SUFFIX):
            return False
        if not self._is_owner(message, me):
            return False

        posted = await update.respond(
            "...", entities=[MessageEntityPre(0, 3, "python")], reply_to=message.id
        )
        await self.execute(update, posted.updates[0].message.id)
        return True

    async def business_callback(
        self, update: UpdateBusinessBotCallbackQuery, client: Any, me: int | None
    ) -> bool:
        """The inline "Raise" button: cancel the running Exec task."""
        if update.data != b"0":
            return False
        if me is not None and update.user_id != me:
            return False
        conn = self.connection_id(client)
        if conn and update.connection_id != conn:
            return False

        if self._cancel(f"Exec-{update.message.id}"):
            return True
        await update.delete()
        return True

    async def inline_callback(self, update: Any, client: Any, me: int | None) -> bool:
        """The same "Raise" button on the guest/inline result path."""
        if update.data != b"0":
            return False
        if me is not None and update.user_id != me:
            return False

        msg_id = update.msg_id
        if self._cancel(f"Exec-{msg_id.access_hash}"):
            return True

        message_id, chat_id = self._inline_coordinates(msg_id)
        pts = 0
        with contextlib.suppress(Exception):
            peer = await client.get_input_entity(chat_id)
            affects = await client.delete_messages(peer, message_id)
            if affects:
                pts = affects[0].pts_count

        if not pts:
            await client.edit_message(
                msg_id, "\u2060", buttons=Button.switch_inline("Call", "\n", True)
            )
        return True

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _is_owner(message: Any, me: int | None) -> bool:
        """Whether *message* came from the owner, when that is knowable."""
        if me is None:
            return True
        return UpdateRouter.sender_id(message) == me

    @staticmethod
    def sender_id(message: Any) -> int | None:
        """Numeric sender of *message*, or None when unreadable."""
        from_id = getattr(message, "from_id", None)
        user_id = getattr(from_id, "user_id", None)
        return user_id if isinstance(user_id, int) else None

    @staticmethod
    def _cancel(task_name: str) -> bool:
        """Cancel the named task if it is running. True when one was found."""
        for task in asyncio.all_tasks():
            if task.get_name() == task_name:
                task.cancel()
                return True
        return False

    @staticmethod
    def _inline_coordinates(
        msg_id: InputBotInlineMessageID | InputBotInlineMessageID64,
    ) -> tuple[int, int]:
        """Recover (message_id, chat_id) from an inline message id.

        The 64-bit variant packs both into one ``.id``; the classic one spells
        them out.
        """
        if isinstance(msg_id, InputBotInlineMessageID):
            return struct.unpack("<ii", struct.pack("<q", msg_id.id))
        return msg_id.id, msg_id.owner_id
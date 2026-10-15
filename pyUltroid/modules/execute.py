"""The selfbot code executor, as one class.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import asyncio
import contextlib
import io
from typing import Any

import telethon
from telethon import Button
from telethon.types import (
    InputBotInlineMessageID,
    InputBotInlineMessageID64,
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityCode,
    MessageEntityPre,
    UpdateBotGuestChatQuery,
    UpdateBotNewBusinessMessage,
)

from custom import aexec, shell, usecs

__all__ = ["Executor"]

#: Output up to this many characters stays in the message.
SHORT_LIMIT = 2048


class Executor:
    """Runs Python sent to a guest bot or a business bot.

    Owns the exec namespace and the whole output path: compose, deliver, and
    fall back to a paste site when the answer outgrows a message.
    """

    def __init__(self) -> None:
        super().__init__()
        self.namespace: dict[str, Any] = {
            "asyncio": asyncio,
            "io": io,
            "tl": telethon,
            "Button": Button,
            "types": telethon.types,
            "utils": telethon.utils,
            "custom": telethon.custom,
            "events": telethon.events,
            "functions": telethon.functions,
            "shell": shell,
        }

    # ── command extraction ───────────────────────────────────────────────

    def command_of(
        self, update: UpdateBotGuestChatQuery | UpdateBotNewBusinessMessage
    ) -> str:
        """The Python the sender asked for, with routing noise stripped."""
        if isinstance(update, UpdateBotGuestChatQuery):
            command = update.message.message
            username = getattr(getattr(update._client, "me", None), "username", None)
            if username:
                command = command.removeprefix(f"@{username}").lstrip()
            return command
        return update.message.message.rstrip("#").rstrip()

    def task_key(self, update: Any, msg_id: Any) -> str:
        """The name the running task carries, used to cancel it again."""
        if isinstance(update, UpdateBotGuestChatQuery):
            return f"Exec-{msg_id.access_hash}"
        return f"Exec-{msg_id}"

    # ── execution ────────────────────────────────────────────────────────

    async def execute(
        self,
        update: UpdateBotGuestChatQuery | UpdateBotNewBusinessMessage,
        msg_id: int | InputBotInlineMessageID | InputBotInlineMessageID64,
    ) -> None:
        """Run the sender's Python and post the result back to them."""
        command = self.command_of(update)

        if not command:
            await self._offer_inline(update, msg_id)
            return

        replied = None
        if isinstance(update, UpdateBotNewBusinessMessage):
            replied = getattr(update, "reply_to_message", None)
            await update.respond(
                update.message.id,
                command,
                entities=[MessageEntityCode(0, len(command))],
            )
        else:
            replies = getattr(update, "reference_messages", None)
            replied = replies[0] if replies else None

        out, now, end = await self._run(update, msg_id, command, replied)
        if command.rstrip().endswith("return"):
            return

        await self.deliver(update, msg_id, command, now, out, end)

    async def _offer_inline(
        self,
        update: UpdateBotGuestChatQuery | UpdateBotNewBusinessMessage,
        msg_id: int | InputBotInlineMessageID | InputBotInlineMessageID64,
    ) -> None:
        """An empty command is a request to hand control back to the user."""
        buttons = Button.switch_inline("Call", "\n", True)
        if isinstance(update, UpdateBotGuestChatQuery):
            await update._client.edit_message(msg_id, "\u2060", buttons=buttons)
        else:
            await update.respond(msg_id, "\u2060")

    async def _run(
        self,
        update: UpdateBotGuestChatQuery | UpdateBotNewBusinessMessage,
        msg_id: int | InputBotInlineMessageID | InputBotInlineMessageID64,
        command: str,
        replied: Any,
    ) -> tuple[str, float, float]:
        """Run *command* as a cancellable task, capturing its stdout."""
        client = update._client
        kwargs = dict(self.namespace)
        kwargs.update(
            {
                "cached": self.namespace,
                "bot": client,
                "app": client.app,
                "http": client.http,
                "client": client._client,
                "update": update,
                "msg_id": msg_id,
                "message": update.message,
                "replied": replied,
            }
        )

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            raise_button = Button.inline("Raise", b"0", "danger")
            if isinstance(update, UpdateBotGuestChatQuery):
                await client.edit_message(msg_id, buttons=raise_button)
            else:
                await update.respond(msg_id, buttons=raise_button)

            task = asyncio.create_task(
                aexec(command, kwargs), name=self.task_key(update, msg_id)
            )
            now = client.loop.time()
            try:
                result = await task
            except (asyncio.CancelledError, Exception) as exc:
                out = exc.__class__.__name__
                if str(exc):
                    out += f": {exc}"
            else:
                out = (buf.getvalue() or str(result)).rstrip()
            finally:
                end = client.loop.time()

        return out, now, end

    # ── output ───────────────────────────────────────────────────────────

    @staticmethod
    def fmtout(output: str, length: int = None) -> tuple[str, int]:
        """Truncate *output* and report its UTF-16 length, the unit TL uses."""
        if length:
            output = f"{output[:length]}..."
        return output, len(output.encode("utf-16-le")) // 2

    def compose(
        self, out: str, elapsed: str, truncate: int = None
    ) -> tuple[str, list]:
        """Build the message body and its entities in one place."""
        output, length = self.fmtout(out, truncate)
        entities = [
            MessageEntityPre(0, length, "python"),
            MessageEntityBold(length + 1, len(elapsed)),
            MessageEntityBlockquote(length + 1, len(elapsed)),
        ]
        return f"{output}\n{elapsed}", entities

    async def deliver(
        self,
        update: UpdateBotGuestChatQuery | UpdateBotNewBusinessMessage,
        msg_id: int | InputBotInlineMessageID | InputBotInlineMessageID64,
        cmd: str,
        now: float,
        out: str,
        end: float,
    ) -> None:
        """Post *out*, upgrading to a paste link or a file when it is long."""
        elapsed = usecs(end - now)
        buttons = [
            [Button.switch_inline("Call", f"\n{cmd}", True), Button.inline("Hide", b"0")]
        ]

        if len(out) <= SHORT_LIMIT:
            body, entities = self.compose(out, elapsed)
            await self._send(update, msg_id, body, entities, buttons=buttons)
            return

        data = out.encode()
        link = await self._paste(update._client, data)

        if link is None:
            body, entities = self.compose(out, elapsed, 512)
            with io.BytesIO(data) as file:
                file.name = "Output.TXT"
                if isinstance(update, UpdateBotGuestChatQuery):
                    await self._send(
                        update, msg_id, body, entities, buttons=buttons, file=file
                    )
                else:
                    await self._send(
                        update, msg_id, body, entities, buttons=buttons, media=file
                    )
            return

        buttons.insert(0, [Button.url("Output", link)])
        body, entities = self.compose(out, elapsed, 1024)
        await self._send(update, msg_id, body, entities, buttons=buttons)

    @staticmethod
    async def _paste(client: Any, data: bytes) -> str | None:
        """Upload *data* to the paste site, or None when that does not work."""
        base = "https://paste.rs"
        try:
            resp = await client.http.post(base, data=data)
            if resp.status != 201:
                return None
            text = await resp.text()
            if not text.startswith(base):
                return None
            return text.strip()
        except Exception:
            return None

    @staticmethod
    async def _send(
        update: UpdateBotGuestChatQuery | UpdateBotNewBusinessMessage,
        msg_id: int | InputBotInlineMessageID | InputBotInlineMessageID64,
        body: str,
        entities: list,
        *,
        buttons: list = None,
        file: Any = None,
        media: Any = None,
    ) -> None:
        """Edit on the guest channel, respond on the business one.

        The two channels spell the same idea differently: the guest client is
        addressed directly and takes ``formatting_entities`` plus ``file``,
        while the business update answers itself and takes ``entities`` plus
        ``media`` and a single button row.
        """
        if isinstance(update, UpdateBotGuestChatQuery):
            extra: dict[str, Any] = {}
            if buttons is not None:
                extra["buttons"] = buttons
            if file is not None:
                extra["file"] = file
            await update._client.edit_message(
                msg_id, body, formatting_entities=entities, **extra
            )
            return

        extra = {}
        if buttons is not None:
            extra["buttons"] = buttons[0]
        if media is not None:
            extra["media"] = media
        await update.respond(msg_id, body, entities=entities, **extra)
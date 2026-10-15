"""Business-update listener primitive (selfbot-style ``listen``).

Waits for ``UpdateBotNewBusinessMessage`` updates (plus business edits when
the fork provides them) matching a rich filter set, without a permanent
handler. Dependency-free: only stdlib + Telethon.
"""

from __future__ import annotations

import asyncio
import contextlib
import enum
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from telethon import TelegramClient
from telethon.events import Raw
from telethon import types as _types

_UPDATE_TYPES = tuple(
    t
    for t in (
        getattr(_types, "UpdateBotNewBusinessMessage", None),
        getattr(_types, "UpdateBotEditBusinessMessage", None),
    )
    if t is not None
)


@dataclass
class ListenResult:
    message: Any
    edit: bool = False
    reply_to: Any | None = None
    connection_id: str | None = None
    raw: Any | None = None

    @property
    def text(self) -> str:
        return getattr(self.message, "text", "") or getattr(
            self.message, "raw_text", ""
        )

    @property
    def from_id(self) -> int | None:
        peer = getattr(self.message, "from_id", None)
        return getattr(peer, "user_id", None) if peer else None

    @property
    def chat_id(self) -> int | None:
        peer = getattr(self.message, "peer_id", None)
        if not peer:
            return None
        return (
            getattr(peer, "user_id", None)
            or getattr(peer, "channel_id", None)
            or getattr(peer, "chat_id", None)
        )


class MatchBy(enum.Enum):
    ANY = enum.auto()
    TEXT_EXACT = enum.auto()
    TEXT_REGEX = enum.auto()
    CALLBACK = enum.auto()


@dataclass
class ListenFilter:
    match_by: MatchBy = MatchBy.ANY
    pattern: str | re.Pattern | None = None
    callback: Callable[[ListenResult], Awaitable[bool]] | None = None
    edits: bool = False
    business_only: bool = True
    connection_id: str | None = None
    user_id: int | None = None
    chat_id: int | None = None
    max_messages: int = 0
    _count: int = field(default=0, repr=False)

    async def __call__(self, result: ListenResult) -> bool:
        if self.max_messages and self._count >= self.max_messages:
            return False
        if result.edit and not self.edits:
            return False
        if self.business_only and result.connection_id is None:
            return False
        if self.connection_id and result.connection_id != self.connection_id:
            return False
        if self.user_id and result.from_id != self.user_id:
            return False
        if self.chat_id and result.chat_id != self.chat_id:
            return False
        if self.match_by == MatchBy.TEXT_EXACT and result.text != self.pattern:
            return False
        if self.match_by == MatchBy.TEXT_REGEX and not re.search(
            self.pattern, result.text
        ):
            return False
        if (
            self.match_by == MatchBy.CALLBACK
            and self.callback
            and not await self.callback(result)
        ):
            return False
        self._count += 1
        return True


async def listen_business(
    client: TelegramClient,
    *,
    user_id: int | str | None = None,
    chat_id: int | str | None = None,
    timeout: float = 15,
    edits: bool = False,
    connection_id: str | None = None,
    pattern: str | re.Pattern | None = None,
    callback: Callable[[ListenResult], Awaitable[bool]] | None = None,
    max_messages: int = 0,
    once: bool = True,
    on_timeout: Callable[[list[ListenResult]], Awaitable[None]] | None = None,
) -> ListenResult | list[ListenResult] | None:
    uid = int(user_id) if user_id else None
    cid = int(chat_id) if chat_id else None

    if pattern and callback:
        raise ValueError("Cannot specify both pattern= and callback=")

    if pattern:
        match_by = (
            MatchBy.TEXT_REGEX
            if isinstance(pattern, re.Pattern)
            else MatchBy.TEXT_EXACT
        )
    elif callback:
        match_by = MatchBy.CALLBACK
    else:
        match_by = MatchBy.ANY

    filt = ListenFilter(
        match_by=match_by,
        pattern=pattern,
        callback=callback,
        edits=edits,
        business_only=True,
        connection_id=connection_id,
        user_id=uid,
        chat_id=cid,
        max_messages=max_messages,
    )

    loop = asyncio.get_running_loop()
    results: list[ListenResult] = []
    fut = loop.create_future() if once else None
    queue: asyncio.Queue | None = asyncio.Queue() if not once else None

    def _make_result(update) -> ListenResult | None:
        msg = getattr(update, "message", None)
        if msg is None:
            return None
        return ListenResult(
            message=msg,
            edit=type(update).__name__ == "UpdateBotEditBusinessMessage",
            reply_to=getattr(update, "reply_to_message", None),
            connection_id=getattr(update, "connection_id", None),
            raw=update,
        )

    async def _handler(update):
        result = _make_result(update)
        if result is None or not await filt(result):
            return
        if once and not fut.done():
            fut.set_result(result)
        elif queue is not None:
            await queue.put(result)

    for ut in _UPDATE_TYPES:
        client.add_event_handler(_handler, Raw(ut))

    try:
        if once:
            if timeout > 0:
                try:
                    return await asyncio.wait_for(fut, timeout=timeout)
                except TimeoutError:
                    return None
            return await fut
        deadline = loop.time() + timeout if timeout > 0 else float("inf")
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            try:
                r = await asyncio.wait_for(queue.get(), timeout=remaining)
                results.append(r)
                if filt.max_messages and len(results) >= filt.max_messages:
                    break
            except TimeoutError:
                break
        if on_timeout and results:
            await on_timeout(results)
        return results
    finally:
        for ut in _UPDATE_TYPES:
            with contextlib.suppress(Exception):
                client.remove_event_handler(_handler)

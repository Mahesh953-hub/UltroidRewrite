"""The User-to-Bot bridge that lets the agent ask the user something.

The agent runs on the *User* client but Telegram will not deliver a
``CallbackQuery`` for a message the User account sent itself. So a keyboard
the agent draws has to be sent by the *Bot* client, and the answer has to come
back to the User side. That round trip is this module.

A pending question is an :class:`AskSession`: one random token, one
:class:`asyncio.Future`, one TTL watchdog. The token goes into the callback
data, so a tap resolves exactly the future that asked, and nothing else. The
watchdog is what stops a question that is never answered from leaking a
future - the thing that would otherwise pin a tool call open forever.

Nothing here imports the agent package. This is the transport; the tool that
uses it lives in :mod:`pyUltroid.agent._interactive`.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Final

from ._telegram import inline_rows, text_of

__all__ = [
    "AskBridge",
    "shared_bridge",
    "AskOutcome",
    "AskSession",
    "NoAnswer",
    "answer_prefix",
]

LOGS: Final = logging.getLogger("pyUltLogs")

#: Callback data is capped at 64 bytes by Telegram, so the prefix is short and
#: the token carries the rest. A per-chat salt is folded in so a token minted
#: for one chat cannot be replayed against another.
_PREFIX: Final = "ask:"
_TTL: Final = 120.0


def answer_prefix(chat_id: int) -> bytes:
    """The callback-data prefix identifying this bridge's asks in *chat_id*."""
    return f"{_PREFIX}{chat_id}:".encode()


@dataclass(slots=True)
class NoAnswer:
    """A question that ended without the user answering.

    Returned instead of raising so the calling tool can hand the model a
    structured "nobody answered" fact and let it decide what to do, instead of
    the loop dying on a timeout.
    """

    reason: str = "timeout"
    waited: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"answered": False, "reason": self.reason, "waited": round(self.waited, 3)}


@dataclass(slots=True)
class AskOutcome:
    """The result of one question: answered, or explicitly not answered."""

    question: str
    value: str | None = None
    timed_out: bool = False
    reason: str = ""
    waited: float = 0.0
    answer_id: int = 0

    @property
    def answered(self) -> bool:
        return self.value is not None and not self.timed_out

    def as_dict(self) -> dict[str, Any]:
        """The shape the model sees. Mirrors Hermes' ``clarify`` result."""
        payload: dict[str, Any] = {
            "question": self.question,
            "answered": self.answered,
            "user_response": self.value if self.answered else None,
            "waited": round(self.waited, 3),
        }
        if not self.answered:
            payload["timed_out"] = True
            payload["reason"] = self.reason or "timeout"
        return payload


@dataclass(slots=True)
class AskSession:
    """One outstanding question and the future that will carry its answer."""

    token: str
    question: str
    chat_id: int
    future: asyncio.Future
    created: float = field(default_factory=time.monotonic)
    ttl: float = _TTL
    watchdog: asyncio.TimerHandle | None = None
    message_id: int = 0
    answer_id: int = 0
    choices: tuple[str, ...] = ()
    notify: Callable[[str], Any] | None = None

    @property
    def data_prefix(self) -> bytes:
        return f"{_PREFIX}{self.chat_id}:{self.token}:".encode()

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.created

    def expire(self, reason: str = "timeout") -> None:
        """Resolve the future as unanswered, exactly once."""
        if self.watchdog is not None:
            self.watchdog.cancel()
            self.watchdog = None
        if not self.future.done():
            self.future.set_result(
                AskOutcome(
                    question=self.question,
                    timed_out=True,
                    reason=reason,
                    waited=self.elapsed,
                )
            )

    def resolve(self, value: str) -> bool:
        """Resolve the future with *value*. False when already settled."""
        if self.watchdog is not None:
            self.watchdog.cancel()
            self.watchdog = None
        if self.future.done():
            return False
        self.future.set_result(
            AskOutcome(
                question=self.question,
                value=value,
                waited=self.elapsed,
                answer_id=self.answer_id,
            )
        )
        return True

    def callback_for(self, value: str) -> bytes:
        """The callback data a button carrying *value* must send."""
        return f"{self.data_prefix.decode()}{self.answer_id}:{value}".encode()[:64]

    def arm(self) -> None:
        """Start the TTL watchdog."""
        if self.watchdog is not None:
            self.watchdog.cancel()
        loop = asyncio.get_event_loop()
        self.watchdog = loop.call_later(self.ttl, self.expire, "timeout")


class AskBridge:
    """Sends a question on the Bot, resolves the answer on the User.

    One bridge per chat is enough; the pending map is keyed by token, so
    concurrent questions in different chats never collide, and two questions
    in the same chat are distinguished by their token.
    """

    def __init__(
        self,
        *,
        bot: Any = None,
        user: Any = None,
        ttl: float = _TTL,
        default_style: str = "primary",
    ) -> None:
        self._bot = bot
        self._user = user
        self._pending: dict[str, AskSession] = {}
        self.ttl = float(ttl)
        self.default_style = default_style

    # ── wiring ────────────────────────────────────────────────────────────

    @property
    def bot(self) -> Any:
        if self._bot is None:
            self._bot = _first_client("bot")
        return self._bot

    @property
    def user(self) -> Any:
        if self._user is None:
            self._user = _first_client("user")
        return self._user

    @property
    def pending(self) -> int:
        """How many questions are outstanding. Diagnostics and leak checks."""
        return len(self._pending)

    def attach(self, *, bot: Any = None, user: Any = None) -> None:
        """Bind the clients explicitly, once they exist."""
        if bot is not None:
            self._bot = bot
        if user is not None:
            self._user = user

    # ── asking ────────────────────────────────────────────────────────────

    async def ask(
        self,
        chat_id: int,
        question: str,
        choices: tuple[str, ...] | list[str] = (),
        *,
        note: str = "",
        ttl: float | None = None,
        style: str | None = None,
        icon: str | None = None,
        columns: int = 2,
        send: bool = True,
    ) -> AskOutcome:
        """Put *question* to the user and wait for the answer.

        Returns an :class:`AskOutcome`; on expiry ``answered`` is False rather
        than an exception, because a silent user is a normal outcome the model
        must be able to reason about. A free-text reply is handled by
        :meth:`submit_text` - the caller forwards the next DM in the chat, so
        the tool can offer buttons *and* accept typing.
        """
        choices = tuple(str(c) for c in choices)
        session = self._new_session(chat_id, question, choices, ttl)
        self._pending[session.token] = session

        try:
            if send:
                await self._send_keyboard(session, note=note, style=style, icon=icon, columns=columns)
            session.arm()
            return await session.future
        except asyncio.CancelledError:
            # The tool was cancelled (deadline, interrupt). Retiring the
            # session here is what keeps a cancelled ask from leaking.
            session.expire("cancelled")
            raise
        finally:
            self._retire(session)

    def _new_session(
        self,
        chat_id: int,
        question: str,
        choices: tuple[str, ...],
        ttl: float | None,
    ) -> AskSession:
        loop = asyncio.get_event_loop()
        token = secrets.token_urlsafe(9)
        while token in self._pending:  # pragma: no cover - astronomically rare
            token = secrets.token_urlsafe(9)
        return AskSession(
            token=token,
            question=question,
            chat_id=int(chat_id),
            future=loop.create_future(),
            ttl=float(ttl if ttl is not None else self.ttl),
            choices=choices,
        )

    async def _send_keyboard(
        self,
        session: AskSession,
        *,
        note: str,
        style: str | None,
        icon: str | None,
        columns: int,
    ) -> None:
        """Draw the question with a styled keyboard and send it on the Bot."""
        bot = self.bot
        if bot is None:
            raise RuntimeError("AskBridge has no bot client; call attach() first")

        body = session.question if not note else f"{session.question}\n\n{note}"
        buttons: list[list[Any]] = []

        if session.choices:
            rows = _chunk(list(session.choices), max(1, columns))
            cells: list[list[Any]] = []
            for index, row in enumerate(rows):
                cells.append(
                    [
                        {"text": label, "data": session.callback_for(label)}
                        for label in row
                    ]
                )
            buttons = inline_rows(
                cells, default_style=style or self.default_style, default_icon=icon
            )

        sent = await bot.send_message(
            session.chat_id, body, buttons=buttons or None
        )
        session.message_id = getattr(sent, "id", 0) or 0

    # ── answering ─────────────────────────────────────────────────────────

    def resolve_callback(self, data: bytes | str, *, edit: bool = False) -> AskOutcome | None:
        """Resolve the session named by a button's callback data.

        Returns the outcome when this bridge owned the tap, ``None`` when the
        data belongs to something else - which is the common case, since every
        other inline button in the bot shares the same dispatcher.
        """
        if isinstance(data, str):
            data = data.encode()
        session = self._session_for(data)
        if session is None:
            return None
        value = data.decode(errors="replace").rsplit(":", 1)[-1]
        session.resolve(value)
        self._retire(session)
        return session.future.result() if session.future.done() else None

    def submit_text(self, chat_id: int, text: str) -> AskOutcome | None:
        """Resolve the oldest open question in *chat_id* with typed text.

        Button taps arrive in order through :meth:`resolve_callback`; a typed
        reply has no token, so the oldest question in that chat is the one it
        answers. Returns ``None`` when the chat has no open question.
        """
        session = self._oldest_in(chat_id)
        if session is None:
            return None
        session.resolve(text_of(text).strip())
        self._retire(session)
        return session.future.result() if session.future.done() else None

    def _session_for(self, data: bytes) -> AskSession | None:
        """Find the session whose token is embedded in *data*."""
        try:
            text = data.decode(errors="replace")
        except Exception:  # pragma: no cover - bytes always decode
            return None
        parts = text.split(":")
        # ask:<chat>:<token>[:<answer>:<value>]
        if len(parts) < 3 or parts[0] != _PREFIX.strip(":"):
            return None
        token = parts[2]
        session = self._pending.get(token)
        if session is None:
            return None
        if not text.startswith(session.data_prefix.decode()):
            return None
        return session

    def _oldest_in(self, chat_id: int) -> AskSession | None:
        candidates = [
            s for s in self._pending.values() if s.chat_id == int(chat_id)
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda s: s.created)

    def _retire(self, session: AskSession) -> None:
        """Drop a settled session, cancelling its watchdog."""
        if session.watchdog is not None:
            session.watchdog.cancel()
            session.watchdog = None
        self._pending.pop(session.token, None)

    # ── housekeeping ──────────────────────────────────────────────────────

    def cancel_all(self, reason: str = "shutdown") -> int:
        """Expire every open question. Returns how many were settled."""
        count = 0
        for session in list(self._pending.values()):
            if not session.future.done():
                session.expire(reason)
                count += 1
            self._retire(session)
        return count

    def owns(self, data: bytes | str) -> bool:
        """Whether *data* is one of this bridge's buttons."""
        return self._session_for(
            data.encode() if isinstance(data, str) else data
        ) is not None


def _chunk(items: list[Any], size: int) -> list[list[Any]]:
    """Split *items* into rows of at most *size*."""
    return [items[i : i + size] for i in range(0, len(items), size)]


def _first_client(kind: str) -> Any:
    """Find a client of *kind* on the root package, once it is populated."""
    try:
        import pyUltroid
    except Exception:  # pragma: no cover - during import
        return None
    return getattr(pyUltroid, "asst" if kind == "bot" else "app", None)


# ── the process-wide transport ──────────────────────────────────────────────
# One bridge, because the half that asks and the half that resolves are in
# different modules: a tool handler on the agent side, a callback handler on
# the assistant side. Two instances would mean two pending maps and a tap that
# answers nothing.
#
# This is shared mutable state, and deliberately so - it is a transport keyed
# by token, the same trade InstantInline's session registry already makes, not
# a service locator hiding configuration. The clients are attached explicitly
# once they exist.
_BRIDGE: AskBridge | None = None


def shared_bridge(**kw: Any) -> AskBridge:
    """The process-wide :class:`AskBridge`, created on first use."""
    global _BRIDGE
    if _BRIDGE is None:
        _BRIDGE = AskBridge(**kw)
    return _BRIDGE

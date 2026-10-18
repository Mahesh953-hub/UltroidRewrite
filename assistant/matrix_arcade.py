"""Owner-only native Matrix arcade with bounded, ephemeral callback sessions."""
from __future__ import annotations

import asyncio
import copy
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path

from telethon.errors import FloodWaitError, MessageNotModifiedError, RPCError
from telethon.tl import types

from pyUltroid._core.matrix_arcade import Arcade, paragraph, payload, title
from pyUltroid._core.richdebug import rpc_identifier, trace_rich_rpc
from pyUltroid._core.richmsg import edit_rich, send_rich
from pyUltroid._misc._assistant import asst_cmd, callback

CALLBACK_PATTERN = re.compile(r"^mx:(?:new|[a-f0-9]{12}:[0-9]{1,9}:(?:menu|rain|ttt|lights|memory|lab|visuals|reset|step|play|stop|download|cell[0-9]{1,2}))$")
SESSION_TTL = 20 * 60
MAX_SESSIONS = 64
FRAME_COUNT = 4
FRAME_INTERVAL = 2.0
EDIT_TIMEOUT = 8.0
COMPANION = Path(__file__).resolve().parents[1] / "resources" / "rich" / "matrix_lab.html"


@dataclass
class Session:
    """Bind an arcade to one owner, chat and bot-authored message."""
    state: Arcade
    owner: int
    chat: int
    message: int | None = None
    touched: float = field(default_factory=time.monotonic)
    downloaded: float | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    stop: bool = False


class SessionStore:
    """Bound idle session memory without timers, database writes or startup work."""
    def __init__(self):
        self.sessions = {}

    def prune(self):
        """Drop expired idle sessions without interrupting active callbacks."""
        now = time.monotonic()
        for token, session in list(self.sessions.items()):
            if now - session.touched >= SESSION_TTL and not session.lock.locked():
                del self.sessions[token]

    def create(self, owner, chat):
        """Reserve a session and evict the oldest idle one at capacity."""
        self.prune()
        if len(self.sessions) >= MAX_SESSIONS:
            idle = [(s.touched, token) for token, s in self.sessions.items() if not s.lock.locked()]
            if not idle:
                raise ValueError("All arcade screens are busy; try again shortly")
            del self.sessions[min(idle)[1]]
        token = secrets.token_hex(6)
        session = Session(Arcade(token, seed=secrets.randbits(32)), owner, chat)
        self.sessions[token] = session
        return session

    def resolve(self, token, owner, chat, message):
        """Reject expired, copied, forwarded or cross-user callback controls."""
        self.prune()
        session = self.sessions.get(token)
        if session is None:
            raise ValueError("This screen expired or the bot restarted. Send /matrix again")
        if (session.owner, session.chat, session.message) != (owner, chat, message):
            raise ValueError("This control belongs to a different owner, chat or message")
        return session


STORE = SessionStore()


async def launch(event):
    """Send a new native arcade without editing the incoming command."""
    session = STORE.create(event.sender_id, event.chat_id)
    try:
        async with session.lock:
            result = await send_rich(event.client, event.chat_id, payload(session.state))
            message = result if isinstance(result, int) else getattr(result, "id", None)
            if not isinstance(message, int) or not message:
                raise ValueError("Native send returned no message reference")
            session.message = message
            return result
    except BaseException:
        STORE.sessions.pop(session.state.token, None)
        raise


@asst_cmd(pattern="(?:matrix|arcade)", owner=True)
async def matrix(event):
    """Open a fresh owner-only native arcade session."""
    return await launch(event)


async def _commit(event, session, candidate):
    """Commit game state only after Telegram accepts the candidate message edit."""
    candidate.revision = session.state.revision + 1
    try:
        await asyncio.wait_for(
            edit_rich(event.client, session.chat, session.message, payload(candidate)),
            timeout=EDIT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        # A cancelled request may have been accepted: expire, never replay an uncertain move.
        STORE.sessions.pop(session.state.token, None)
        raise ValueError("Telegram did not confirm this update in time. The screen expired; send /matrix again") from None
    except MessageNotModifiedError:
        # Do not commit an unseen revision: its controls would not match Telegram.
        return False
    session.state = candidate
    session.touched = time.monotonic()
    return True


async def _play(event, session):
    """Play at most four native message edits, then restore idle controls."""
    session.stop = False
    for index in range(FRAME_COUNT):
        if session.stop:
            break
        candidate = copy.deepcopy(session.state)
        candidate.frame += 1
        candidate.running = index < FRAME_COUNT - 1
        if not await _commit(event, session, candidate):
            break
        if candidate.running:
            await asyncio.sleep(FRAME_INTERVAL)
    if session.state.running:
        candidate = copy.deepcopy(session.state)
        candidate.running = False
        await _commit(event, session, candidate)


async def _failure(event, message, acknowledged):
    """Report errors natively even when Telethon has already cached the query answer."""
    if not acknowledged:
        return await event.answer(message, alert=True)
    notice = types.InputRichMessage([
        title("Matrix playback stopped"), paragraph(message),
        paragraph("No move was replayed. The last accepted frame may remain visible."),
    ])
    try:
        return await asyncio.wait_for(send_rich(event.client, event.chat_id, notice), timeout=EDIT_TIMEOUT)
    except (RPCError, TypeError, ValueError, asyncio.TimeoutError) as error:
        # One bounded native notice attempt; never retry an uncertain send or downgrade.
        trace_rich_rpc("assistant.matrix.notice", error=error, request_kind="send", target_kind="peer", outcome="rejected")


@callback(CALLBACK_PATTERN, owner=True)
async def matrix_callback(event):
    """Dispatch native arcade controls with ownership, revision and lock guards."""
    try:
        data = event.data.decode("ascii", "strict")
    except (AttributeError, UnicodeDecodeError):
        return
    if CALLBACK_PATTERN.fullmatch(data) is None:
        return
    if data == "mx:new":
        await event.answer()
        return await launch(event)
    _, token, revision, action = data.split(":")
    acknowledged = False
    try:
        session = STORE.resolve(token, event.sender_id, event.chat_id, event.message_id)
        # Stop remains available across the finite playback's changing frame revisions.
        if action == "stop" and session.lock.locked() and session.state.mode == "rain":
            session.stop = True
            return await event.answer("Stopping after the current frame")
        if session.lock.locked():
            return await event.answer("This screen is updating. Try again in a moment")
        async with session.lock:
            if int(revision) != session.state.revision:
                return await event.answer("That frame is old. Use the newest controls")
            if action == "play":
                if session.state.mode != "rain":
                    raise ValueError("Playback belongs to digital rain")
                await event.answer("Requesting up to four native MTProto frames")
                acknowledged = True
                await _play(event, session)
                return
            if action == "download":
                if session.state.mode != "lab":
                    raise ValueError("Open Source lab to download the companion")
                now = time.monotonic()
                if session.downloaded is not None and now - session.downloaded < 10:
                    raise ValueError("The browser companion was just sent; wait ten seconds")
                if not COMPANION.is_file():
                    raise ValueError("The browser companion is unavailable on this deployment")
                await event.client.send_file(
                    session.chat, str(COMPANION), force_document=True,
                    caption="Matrix browser companion. Download and open in a browser. JavaScript, canvas and SVG run there, not inside Telegram messages.",
                    parse_mode=None,
                )
                session.downloaded = time.monotonic()
                session.touched = time.monotonic()
                return await event.answer("Browser companion sent as an HTML document")
            candidate = copy.deepcopy(session.state)
            if action == "stop":
                if candidate.mode != "rain":
                    raise ValueError("Stop belongs to digital rain")
                candidate.running = False
                candidate.frame += 1
            else:
                candidate.act(action)
            await _commit(event, session, candidate)
            return await event.answer()
    except ValueError as error:
        # These messages are local fixed validation strings, never remote error bodies.
        return await _failure(event, str(error), acknowledged)
    except (RPCError, TypeError) as error:
        identifier = rpc_identifier(error) if isinstance(error, RPCError) else "LOCAL_VALIDATION_ERROR"
        trace_rich_rpc("assistant.matrix", error=error, request_kind="edit", target_kind="peer", outcome="rejected")
        if isinstance(error, FloodWaitError):
            return await _failure(event, "Telegram rate-limited this screen. Playback stopped; try later", acknowledged)
        return await _failure(event, f"Native update rejected ({identifier}). No fallback was sent", acknowledged)

"""Shared native group Ludo with locked, revisioned, ephemeral table sessions."""
from __future__ import annotations

import asyncio
import copy
import re
import secrets
import time
from dataclasses import dataclass, field

from telethon.errors import FloodWaitError, MessageNotModifiedError, RPCError
from telethon.tl import types as t

from pyUltroid._core.ludo_arcade import Ludo
from pyUltroid._core.ludo_visuals import (
    board_block,
    legend_block,
    player_blocks,
    seat_label,
    status_blocks,
)
from pyUltroid._core.matrix_arcade import button, paragraph, row, text, title
from pyUltroid._core.richdebug import rpc_identifier, trace_rich_rpc
from pyUltroid._core.richmsg import edit_rich, send_rich
from pyUltroid._misc._assistant import asst_cmd, callback

PATTERN = re.compile(r"^lu:(?:new|[a-f0-9]{12}:[0-9]{1,9}:(?:join|leave|start|cancel|roll|move[0-3]|resign|skip|rematch|cancelok|resignok|back))$")
TTL = 60 * 60
CAPACITY = 64
EDIT_TIMEOUT = 8.0


@dataclass
class Table:
    """Bind one shared game to its exact group and bot message."""
    token: str
    chat: int
    game: Ludo
    message: int | None = None
    revision: int = 0
    touched: float = field(default_factory=time.monotonic)
    confirmation: tuple[int, str, float] | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def control(self, label, action):
        """Build an exact-revision native callback control."""
        control = button(label, f"lu:{self.token}:{self.revision}:{action}")
        if action in {"start", "join", "rematch"}:
            control.style = t.RichButtonStyle(bg_success=True)
        elif action in {"cancelok", "resignok"}:
            control.style = t.RichButtonStyle(bg_danger=True)
        elif action == "roll" or action.startswith("move"):
            control.style = t.RichButtonStyle(bg_primary=True)
        return control


TABLES: dict[str, Table] = {}


def prune():
    """Expire idle tables without evicting locked edits or touching persistence."""
    now = time.monotonic()
    for token, table in list(TABLES.items()):
        if now - table.touched >= TTL and not table.lock.locked():
            del TABLES[token]


def render(table):
    """Compose native board cells, readable player cards and nested controls."""
    game, ctl = table.game, table.control
    blocks = [title("🎲 LUDO / THE COLOUR COURT"), *status_blocks(game)]
    if game.status == "lobby":
        blocks += [paragraph("2–4 humans · four tokens each · no AI opponent"),
                   row(ctl("＋ Join table", "join"), ctl("− Leave seat", "leave")),
                   row(ctl("▶ Start · host", "start"))]
    elif game.status == "playing":
        moves = game.legal_moves()
        seat = game.active
        if game.dice is None:
            blocks.append(row(ctl(f"🎲 Roll · {seat_label(seat)}", "roll")))
        elif moves:
            # Two controls per row keeps legal token labels legible on phones.
            for start in range(0, len(moves), 2):
                blocks.append(row(*(ctl(f"{seat_label(seat).split()[0]} {chr(65 + seat)}{i + 1} · Move", f"move{i}")
                                    for i in moves[start:start + 2])))
    else:
        blocks.append(row(ctl("↺ Rematch lobby · host", "rematch")))
    blocks += [paragraph("Track positions use shared squares 0–51. Your token cards below stay readable even if the full board scrolls sideways."),
               t.PageBlockDetails([board_block(game), legend_block()], text("✦ Open the cross board / 15 × 15 cells"), open=False),
               *player_blocks(game)]
    management = []
    if game.status == "playing":
        management += [row(ctl("◷ Skip AFK turn", "skip")),
                       t.PageBlockDetails([paragraph("Resigning removes your tokens. Closing ends the table for everyone. Both require confirmation."),
                                           row(ctl("⚑ Resign", "resign")),
                                           row(ctl("■ Close · host", "cancel"))],
                                          text("⚑ Leave / close table"), open=False)]
    elif game.status == "lobby":
        management.append(row(ctl("■ Close table · host", "cancel")))
    if management:
        blocks.append(t.PageBlockDetails(management, text("Table controls / more"), open=False))
    if table.confirmation:
        actor, action, _deadline = table.confirmation
        blocks += [paragraph(f"Player {actor}: confirm {action}. This confirmation expires in 20 seconds."),
                   row(ctl(f"Confirm {action}", action + "ok"), ctl("Keep playing", "back"))]
    if game.events:
        blocks.append(t.PageBlockDetails([paragraph(item) for item in game.events[-6:]],
                                        text("Arena feed / last six events"), open=False))
    blocks += [t.PageBlockDetails([
        t.PageBlockDetails([paragraph("Six releases a token; roll then choose one legal token. Four tokens must reach home exactly. Six, capture or a token finishing grants a bonus roll. Three consecutive sixes forfeit the third roll (earlier moves stand)."),
        paragraph("Safe star squares cannot be captured. Two allied tokens form a blockade: opponents cannot cross or land on it. No legal move passes automatically. Home lanes are private. This is the arcade's documented Ludo variant, not every regional ruleset.")], text("🎲 Movement / captures / home"), open=False),
        t.PageBlockDetails([paragraph("Only the current player may roll or move. Seated players can skip a turn after 90 seconds with no successful turn action; there is no background timer. Resigning removes that seat's tokens. The host can close a table only after confirmation."),
        paragraph("One table per group. New controls supersede old controls. Idle tables expire after one hour; restart clears all games. Rematches reopen a lobby and everyone must join again. No stakes, prizes, database writes or external inference.")], text("🛡 Turn access / table safety"), open=False),
    ], text("📖 Rules / safety / controls"), open=False),
        t.PageBlockFooter(text(f"Native Layer 230 · table {table.token} · revision {table.revision} · /ludo"))]
    return t.InputRichMessage(blocks)


async def launch(event):
    """Reserve one new table in a group without editing any existing message."""
    if not getattr(event, "is_group", False) or not event.sender_id or event.sender_id <= 0:
        raise ValueError("Ludo is for human players in group chats. Add the assistant to your group and send /ludo there")
    prune()
    if any(table.chat == event.chat_id for table in TABLES.values()):
        raise ValueError("This group already has a Ludo table. Use its current controls, or let it expire after one hour idle")
    if len(TABLES) >= CAPACITY:
        raise ValueError("All Ludo tables are occupied. Try later")
    table = Table(secrets.token_hex(6), event.chat_id, Ludo(event.sender_id, now=time.monotonic()))
    TABLES[table.token] = table
    try:
        async with table.lock:
            result = await asyncio.wait_for(send_rich(event.client, table.chat, render(table)), EDIT_TIMEOUT)
            table.message = result if isinstance(result, int) else getattr(result, "id", None)
            if not isinstance(table.message, int) or not table.message:
                raise ValueError("Telegram returned no table message reference; do not replay the send")
        return result
    except BaseException:
        TABLES.pop(table.token, None)
        raise


@asst_cmd(pattern="ludo")
async def ludo(event):
    """Open a native shared Ludo lobby for this group's human players."""
    try:
        return await launch(event)
    except ValueError as error:
        return await send_rich(event.client, event.chat_id, t.InputRichMessage([title("Ludo table"), paragraph(str(error))]))


async def commit(event, table, candidate):
    """Publish the candidate before committing state, expiring ambiguous edits."""
    candidate.revision = table.revision + 1
    try:
        await asyncio.wait_for(edit_rich(event.client, table.chat, table.message, render(candidate)), EDIT_TIMEOUT)
    except asyncio.TimeoutError:
        TABLES.pop(table.token, None)
        raise ValueError("Telegram did not confirm the edit. This table expired rather than replaying an uncertain turn") from None
    except MessageNotModifiedError:
        return False
    table.game, table.revision, table.confirmation = candidate.game, candidate.revision, candidate.confirmation
    table.touched = time.monotonic()
    return True


def candidate_for(table):
    """Copy mutable game state while retaining the live table's single lock."""
    return Table(table.token, table.chat, copy.deepcopy(table.game), table.message,
                 table.revision, table.touched, table.confirmation, table.lock)


@callback(PATTERN)
async def ludo_callback(event):
    """Allow group participants while rejecting stale, copied and illegal turns."""
    try:
        data = event.data.decode("ascii", "strict")
    except (AttributeError, UnicodeDecodeError):
        return
    if not PATTERN.fullmatch(data):
        return
    try:
        if not getattr(event, "is_group", False) or not event.sender_id or event.sender_id <= 0:
            raise ValueError("Use Ludo controls as a human player inside the original group")
        if data == "lu:new":
            await launch(event)
            return await event.answer("Ludo lobby opened")
        _, token, revision, action = data.split(":")
        prune()
        table = TABLES.get(token)
        if table is None:
            raise ValueError("Table expired or the bot restarted. Send /ludo for a new lobby")
        if (table.chat, table.message) != (event.chat_id, event.message_id):
            raise ValueError("These controls belong to another group or message")
        if table.lock.locked():
            return await event.answer("A turn is updating; use the newest controls in a moment")
        async with table.lock:
            if int(revision) != table.revision:
                raise ValueError("Old board. Use the newest controls on this table")
            candidate = candidate_for(table)
            now = time.monotonic()
            if action in {"cancel", "resign"}:
                if action == "cancel" and event.sender_id != candidate.game.host:
                    raise ValueError("Only the host may close this table")
                if action == "resign" and (candidate.game.status != "playing" or not any(p.user == event.sender_id and not p.resigned and not p.finished for p in candidate.game.players)):
                    raise ValueError("Only a seated player in a running game can resign")
                candidate.confirmation = (event.sender_id, action, now + 20)
            elif action.endswith("ok"):
                expected = (event.sender_id, action[:-2])
                pending = candidate.confirmation
                if not pending or pending[:2] != expected or now > pending[2]:
                    raise ValueError("That confirmation expired or belongs to another player")
                candidate.game.apply(action[:-2], event.sender_id, now=now)
                candidate.confirmation = None
            elif action == "back":
                if not candidate.confirmation or candidate.confirmation[0] != event.sender_id:
                    raise ValueError("Only the player who requested confirmation may dismiss it")
                candidate.confirmation = None
            else:
                candidate.game.apply(action, event.sender_id, now=now)
                candidate.confirmation = None
            await commit(event, table, candidate)
        return await event.answer()
    except ValueError as error:
        return await event.answer(str(error), alert=True)
    except (RPCError, TypeError, asyncio.TimeoutError) as error:
        trace_rich_rpc("assistant.ludo", error=error, request_kind="edit", target_kind="peer", outcome="rejected")
        detail = "Telegram rate-limited this table; try later" if isinstance(error, FloodWaitError) else f"Native update rejected ({rpc_identifier(error) if isinstance(error, RPCError) else 'LOCAL_VALIDATION_ERROR'}). No move was replayed"
        return await event.answer(detail, alert=True)

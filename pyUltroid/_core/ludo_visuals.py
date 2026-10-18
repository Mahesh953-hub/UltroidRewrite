"""Render Ludo state as accessible native Layer-230 presentation blocks."""
from __future__ import annotations

from telethon.tl import types as t

from pyUltroid._core.ludo_arcade import FINISH, SAFE_SQUARES, TRACK, Ludo
from pyUltroid._core.matrix_arcade import paragraph, text, title

SEATS = (("A", "Red", "🔴"), ("B", "Green", "🟢"), ("C", "Yellow", "🟡"), ("D", "Blue", "🔵"))


def seat_label(seat):
    """Return an accessible emoji, seat letter, and color name."""
    letter, color, emoji = SEATS[seat]
    return f"{emoji} {letter} · {color}"


def _cell(value):
    return t.PageTableCell(text=text(value), align_center=True, valign_middle=True)


def _table(rows, heading):
    return t.PageBlockTable(text(heading), rows, bordered=True, compact=True)


def board_block(game: Ludo):
    """Build the engine-aligned fifteen-by-fifteen cross board."""
    path = (
        (6, 1), (6, 2), (6, 3), (6, 4), (6, 5), (5, 6), (4, 6),
        (3, 6), (2, 6), (1, 6), (0, 6), (0, 7), (0, 8),
        (1, 8), (2, 8), (3, 8), (4, 8), (5, 8), (6, 9),
        (6, 10), (6, 11), (6, 12), (6, 13), (6, 14), (7, 14), (8, 14),
        (8, 13), (8, 12), (8, 11), (8, 10), (8, 9), (9, 8), (10, 8),
        (11, 8), (12, 8), (13, 8), (14, 8), (14, 7), (14, 6),
        (13, 6), (12, 6), (11, 6), (10, 6), (9, 6), (8, 5),
        (8, 4), (8, 3), (8, 2), (8, 1), (8, 0), (7, 0), (6, 0),
    )
    lanes = ([(7, c) for c in range(1, 7)], [(r, 7) for r in range(1, 7)],
             [(7, c) for c in range(13, 7, -1)], [(r, 7) for r in range(13, 7, -1)])
    yards = (((2, 2), (2, 4), (4, 2), (4, 4)), ((2, 10), (2, 12), (4, 10), (4, 12)),
             ((10, 10), (10, 12), (12, 10), (12, 12)), ((10, 2), (10, 4), (12, 2), (12, 4)))
    grid = [["" for _ in range(15)] for _ in range(15)]
    for square, (r, c) in enumerate(path):
        grid[r][c] = "✦" if square in SAFE_SQUARES else "·"
    for seat, lane in enumerate(lanes):
        emoji = SEATS[seat][2]
        for r, c in lane:
            grid[r][c] = f"{emoji} ◆"
    positions = {}
    for seat, player in enumerate(game.players):
        if player.resigned:
            continue
        for number, pos in enumerate(player.tokens, 1):
            location = yards[seat][number - 1] if pos == -1 else path[(player.offset + pos) % TRACK] if pos < TRACK else lanes[seat][pos - TRACK]
            positions.setdefault(location, []).append((seat, number))
    for (r, c), tokens in positions.items():
        if len(tokens) == 1:
            seat, number = tokens[0]
            grid[r][c] = f"{SEATS[seat][2]}{SEATS[seat][0]}{number}"
        else:
            # Exact occupants and positions remain available in each seat ledger.
            colors = {seat for seat, _ in tokens}
            glyph = SEATS[tokens[0][0]][2] if len(colors) == 1 else "⚑"
            grid[r][c] = f"{glyph}×{len(tokens)}"
    grid[7][7] = "🏁 Home"
    rows = [t.PageTableRow([_cell(value) for value in line]) for line in grid]
    return _table(rows, "Ludo board · rows and columns follow the game path")


def player_blocks(game: Ludo):
    """Return open current-player details with a four-token status ledger."""
    current = game.current_player
    legal = set(game.legal_moves())
    result = []
    for seat, player in enumerate(game.players):
        label = seat_label(seat)
        state = "Resigned" if player.resigned else "Finished" if player.finished else "Current player" if player.user == current else "Seated" if game.status == "lobby" else "Waiting" if game.status == "playing" else "Closed"
        mention = t.TextMentionName(text(str(player.user)), player.user)
        result.append(t.PageBlockDetails([
            paragraph(f"{label} · player "),
            t.PageBlockParagraph(mention),
            _table([
                t.PageTableRow([t.PageTableCell(text=text("Token"), header=True), t.PageTableCell(text=text("Position"), header=True), t.PageTableCell(text=text("State"), header=True)]),
                *[t.PageTableRow([_cell(f"{SEATS[seat][2]} {SEATS[seat][0]}{number}"), _cell(_position(player, pos)), _cell(_token_state(game, player, number - 1, legal))]) for number, pos in enumerate(player.tokens, 1)],
            ], f"{SEATS[seat][0]} · four tokens"),
        ], text(f"{label} · {state}"), open=player.user == current))
    return result


def _position(player, pos):
    if pos == -1:
        return "Yard"
    if pos == FINISH:
        return "Finished"
    if pos >= TRACK:
        return f"Home {pos - TRACK + 1}"
    return f"Track {(player.offset + pos) % TRACK}"


def _token_state(game, player, token, legal):
    if player.resigned:
        return "Resigned"
    if player.tokens[token] == FINISH:
        return "Finished"
    if player.finished:
        return "Finished"
    if player.user == game.current_player and token in legal:
        return "✓ Legal move"
    if game.status == "playing" and player.user == game.current_player:
        return "Roll first" if game.dice is None else "Not legal this roll"
    return "Waiting"


def status_blocks(game: Ludo):
    """Describe lobby, active turn, or ranked finished outcome natively."""
    if game.status == "lobby":
        names = ", ".join(f"{seat_label(i)}" for i in range(len(game.players)))
        return [title("🎲 Ludo · lobby"), paragraph(f"{len(game.players)}/4 seats · {names} · waiting for players and host start.")]
    if game.status == "playing":
        seat = next((i for i, player in enumerate(game.players) if player.user == game.current_player), 0)
        player = game.players[seat]
        face = f"{'⚀⚁⚂⚃⚄⚅'[game.dice - 1]} Die: {game.dice}" if game.dice is not None else "🎲 Roll to begin your turn"
        if game.dice is None:
            action = "Roll the die."
        else:
            moves = game.legal_moves()
            action = "Choose token " + ", ".join(str(i + 1) for i in moves) + "." if moves else "No legal token; the turn passes."
        return [title(f"🎲 Ludo · {seat_label(seat)} to play"), paragraph(f"Player {player.user} · {face} · {action}")]
    rankings = []
    for rank, (user, state) in enumerate(game.scoreboard, 1):
        seat = next((i for i, player in enumerate(game.players) if player.user == user), 0)
        rankings.append(paragraph(f"{rank}. {seat_label(seat)} · player {user} · {state}"))
    return [title(f"🏁 Ludo · {game.status}"), paragraph("Final standings; remaining and resigned seats are labeled:") , *rankings]


def legend_block():
    """Provide a nested accessible key for the board's Unicode markers."""
    return t.PageBlockDetails([
        paragraph("🔴 A · Red   🟢 B · Green   🟡 C · Yellow   🔵 D · Blue"),
        paragraph("Colored numbered pieces identify tokens 1–4. ✦ Safe marks a safe track square; colored ◆ marks a private home lane."),
        paragraph("⚑×N or a color×N marks a shared stack of N pieces. Each player’s token card lists each token and exact position."),
        paragraph("🏁 Home is the center. Yard, Track 0–51, Home 1–5, and Finished are written out in the token ledger."),
    ], text("Board legend · colors and words"), open=False)

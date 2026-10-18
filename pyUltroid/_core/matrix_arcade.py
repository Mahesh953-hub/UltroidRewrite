"""Native Matrix arcade models, rules, and Layer-230 message layouts."""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from functools import lru_cache

from telethon.tl import types as t

MODES = ("menu", "rain", "ttt", "lights", "memory", "lab", "visuals")
LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8), (0, 3, 6), (1, 4, 7), (2, 5, 8), (0, 4, 8), (2, 4, 6))
GLYPHS = "01アイウエオカキクケコサシスセソ⊕∆⋮│"
SYMBOLS = ("◆", "●", "▲", "■", "✦", "⬡")


def text(value):
    return t.TextPlain(value)


def paragraph(value):
    return t.PageBlockParagraph(text(value))


def title(value):
    return t.PageBlockParagraph(t.TextBold(text(value)))


def button(label, data):
    return t.PageButton(text(label), t.InlineButtonTypeCallback(data.encode("ascii")))


def row(*buttons):
    return t.PageBlockButtonRow(list(buttons))


@dataclass
class Arcade:
    """Hold one ephemeral arcade screen without persistence or Telegram objects."""
    token: str
    mode: str = "menu"
    revision: int = 0
    seed: int = 0
    frame: int = 0
    board: list[int] = field(default_factory=lambda: [0] * 9)
    deck: list[int] = field(default_factory=list)
    revealed: list[int] = field(default_factory=list)
    matched: list[int] = field(default_factory=list)
    moves: int = 0
    status: str = ""
    running: bool = False

    def control(self, label, action):
        """Bind a native callback to this screen's exact revision."""
        return button(label, f"mx:{self.token}:{self.revision}:{action}")

    def enter(self, mode):
        """Initialize a fresh playable scene using a reproducible local seed."""
        if mode not in MODES:
            raise ValueError("Unknown arcade scene")
        self.mode, self.moves, self.status = mode, 0, ""
        self.frame, self.running = 0, False
        self.board = [0] * 9
        self.revealed, self.matched = [], []
        rng = random.Random(self.seed + self.revision)
        if mode == "lights":
            # Construct by legal moves from the solved board: always solvable.
            for cell in rng.sample(range(9), 5):
                toggle(self.board, cell)
            if not any(self.board):
                toggle(self.board, 4)
        if mode == "memory":
            self.deck = list(range(6)) * 2
            rng.shuffle(self.deck)

    def act(self, action):
        """Apply a validated game action; reject illegal or completed moves."""
        if action in MODES:
            self.enter(action)
            return
        if action == "reset":
            self.enter(self.mode)
            return
        if action == "step" and self.mode == "rain":
            self.frame += 1
            return
        if not action.startswith("cell") or not action[4:].isdigit():
            raise ValueError("That control is not available on this screen")
        cell = int(action[4:])
        if self.mode == "ttt":
            if not 0 <= cell < 9 or self.board[cell] or winner(self.board) or all(self.board):
                raise ValueError("Choose an empty cell in an unfinished game")
            self.board[cell] = 1
            self.moves += 1
            if not winner(self.board) and not all(self.board):
                best = max((i for i, v in enumerate(self.board) if not v), key=lambda i: _score_move(tuple(self.board), i, 2))
                self.board[best] = 2
        elif self.mode == "lights":
            if not 0 <= cell < 9 or not any(self.board):
                raise ValueError("Puzzle complete; start a new grid")
            toggle(self.board, cell)
            self.moves += 1
        elif self.mode == "memory":
            if not 0 <= cell < 12 or cell in self.matched or cell in self.revealed:
                raise ValueError("Choose an unrevealed tile")
            if len(self.revealed) == 2:
                self.revealed = []
            self.revealed.append(cell)
            if len(self.revealed) == 2:
                self.moves += 1
                a, b = self.revealed
                if self.deck[a] == self.deck[b]:
                    self.matched.extend((a, b))
                    self.revealed = []
        else:
            raise ValueError("No board on this screen")


def toggle(board, cell):
    """Toggle a Lights Out cell and its orthogonal neighbors without wrapping."""
    if not 0 <= cell < 9:
        raise ValueError("Cell outside grid")
    r, c = divmod(cell, 3)
    for nr, nc in ((r, c), (r-1, c), (r+1, c), (r, c-1), (r, c+1)):
        if 0 <= nr < 3 and 0 <= nc < 3:
            board[nr * 3 + nc] ^= 1


def winner(board):
    """Return the winning player or zero for a draw or unfinished board."""
    return next((board[a] for a, b, c in LINES if board[a] and board[a] == board[b] == board[c]), 0)


@lru_cache(maxsize=20000)
def _minimax(board, player):
    won = winner(board)
    if won:
        return 1 if won == 2 else -1
    if all(board):
        return 0
    scores = [_score_move(board, i, player) for i, v in enumerate(board) if not v]
    return (max if player == 2 else min)(scores)


def _score_move(board, cell, player):
    next_board = list(board)
    next_board[cell] = player
    return _minimax(tuple(next_board), 3 - player)


def rain_frame(seed, frame):
    """Render a bounded deterministic terminal-art frame, not an image or video."""
    lines = []
    for y in range(9):
        chars = []
        for x in range(22):
            rng = random.Random(seed + x * 1009)
            head = (rng.randrange(15) + frame * (1 + x % 2)) % 15
            age = (head - y) % 15
            glyph = GLYPHS[(x * 7 + y * 13 + frame) % len(GLYPHS)]
            chars.append(glyph if age < 5 else " ")
        lines.append("".join(chars))
    return "\n".join(lines)


def lobby_payload():
    """Expose a stateless launch card for the existing rich showcase index."""
    return t.InputRichMessage([
        title("⌘ MATRIX / NATIVE ARCADE"),
        paragraph("A playable pocket terminal. Group Ludo, three solo games, digital rain and a native visual forge."),
        row(button("▶ Enter the construct", "mx:new")),
        paragraph("Native MTProto controls. The separate browser companion runs JavaScript; Telegram messages do not."),
    ])


def payload(state):
    """Compose native nested blocks and real callback controls for one scene."""
    blocks = [title("⌘ MATRIX / " + {"menu": "THE CONSTRUCT", "rain": "DIGITAL RAIN", "ttt": "NEURAL DUEL", "lights": "BLACKOUT", "memory": "MEMORY VAULT", "lab": "SOURCE LAB", "visuals": "VISUAL FORGE"}[state.mode])]
    ctl = state.control
    if state.mode == "menu":
        blocks += [
            paragraph("Wake up. The message is the interface."),
            t.PageBlockPreformatted(text("┌──────────────────────┐\n│  SARAH // CONSTRUCT   │\n│  [ native link: ON ]  │\n└──────────────────────┘"), "text"),
            row(ctl("≋ Digital rain", "rain"), ctl("✕ Neural duel", "ttt")),
            row(ctl("◈ Blackout", "lights"), ctl("◆ Memory vault", "memory")),
            row(button("♜ Human Ludo · group", "lu:new")),
            row(ctl("✦ Visual forge", "visuals"), ctl("⌘ Source lab", "lab")),
            t.PageBlockDetails([
                paragraph("Neural duel: you are X against a perfect local O opponent."),
                paragraph("Blackout: switch off a solvable 3×3 cross-toggle grid."),
                paragraph("Memory vault: uncover six matching symbol pairs."),
                t.PageBlockDetails([
                    paragraph("Every move edits this message through native Layer-230 MTProto. Sessions are owner/chat/message-bound, expire after 20 minutes idle, and disappear on restart."),
                    paragraph("Symbols are ordinary Unicode, not animated custom emoji. Digital rain is terminal text updated by bounded server edits, not JS, CSS or video."),
                ], text("Under the glass"), open=False),
            ], text("Choose your reality / rules"), open=False),
        ]
    elif state.mode == "rain":
        blocks += [
            paragraph(f"SIGNAL {state.frame:03d} · " + ("4-frame transmission in progress" if state.running else "Transmission idle")),
            t.PageBlockPreformatted(text(rain_frame(state.seed, state.frame)), "text"),
            row(ctl("▸ Next frame", "step"), ctl("▶ Play 4 frames", "play"), ctl("■ Stop", "stop")),
            paragraph("A brief sequence of message edits, about two seconds apart. Telegram may throttle updates; a timed-out screen expires rather than replaying uncertain moves. Full canvas animation is in the browser companion."),
        ]
    elif state.mode in {"ttt", "lights", "memory"}:
        if state.mode == "ttt":
            won = winner(state.board)
            status = "O wins. Reset to try again." if won == 2 else "X wins." if won == 1 else "Draw. You held the line." if all(state.board) else "Your move: X. The local engine plays O."
            labels = ["·" if not v else "✕" if v == 1 else "◯" for v in state.board]
            width = 3
        elif state.mode == "lights":
            status = "BLACKOUT COMPLETE. You broke the circuit." if not any(state.board) else "Turn every light off. A tap flips that cell and its orthogonal neighbors."
            labels = ["●" if v else "○" for v in state.board]
            width = 3
        else:
            status = "VAULT OPEN. All six pairs recovered." if len(state.matched) == 12 else "Find six pairs. A mismatch stays visible until your next pick."
            labels = [SYMBOLS[state.deck[i]] if i in state.revealed or i in state.matched else "?" for i in range(12)]
            width = 4
        blocks += [paragraph(status), paragraph(f"Moves: {state.moves}")]
        for start in range(0, len(labels), width):
            blocks.append(row(*(ctl(f"{labels[i]} {i + 1}", f"cell{i}") for i in range(start, start + width))))
        blocks.append(row(ctl("↺ New game", "reset")))
    elif state.mode == "visuals":
        blocks += visual_blocks()
    else:
        blocks += [
            paragraph("Code is displayed as code. The downloadable companion is an offline browser page, not an embedded Telegram app."),
            t.PageBlockDetails([
                t.PageBlockPreformatted(text('<details><summary>Follow the white rabbit</summary>\n  <p><b>Wake up.</b> Native rich HTML is a document format.</p>\n</details>'), "html"),
                t.PageBlockDetails([
                    t.PageBlockPreformatted(text('const canvas = document.querySelector("canvas");\nconst ctx = canvas.getContext("2d");\n// requestAnimationFrame(draw): browser companion only'), "javascript"),
                    t.PageBlockPreformatted(text('<svg viewBox="0 0 64 64">\n  <path d="M32 4 60 32 32 60 4 32Z" fill="#00ff9d"/>\n</svg>'), "xml"),
                    paragraph("No claim that Telegram executes scripts, paints SVG, supports arbitrary CSS or accepts unuploaded media references."),
                ], text("JavaScript + vector source"), open=False),
            ], text("Nested source capsule"), open=False),
            t.PageBlockPreformatted(text("       ◇\n      ╱ ╲\n     ◇───◇\n      ╲ ╱\n       ◇\n    CONSTRUCT"), "text"),
            row(ctl("⇩ Get browser companion", "download")),
            t.PageBlockButtonRow([
                t.PageButton(text("Copy HTML sample"), t.InlineButtonTypeCopy("<p><b>Wake up.</b></p>")),
                t.PageButton(text("Native schema docs ↗"), t.InlineButtonTypeUrl("https://core.telegram.org/schema")),
            ]),
        ]
    if state.mode != "menu":
        blocks.append(row(ctl("⌂ Construct", "menu")))
    blocks.append(t.PageBlockFooter(text("Native MTProto • ephemeral arcade • browser features labelled separately")))
    return t.InputRichMessage(blocks)


def visual_blocks():
    """Show supported native visual ingredients without inventing web rendering."""
    palettes = [
        ("Geometry", "◆ ◇ ● ○ ▲ △ ■ □ ✦ ⬡"),
        ("Signals", "▁ ▂ ▃ ▄ ▅ ▆ ▇ █  ▰▰▰▱▱"),
        ("Circuit", "┌─┬─┐  ╔═╦═╗  ╱╲ ╳"),
        ("Braille", "⠁ ⠃ ⠇ ⠏ ⠟ ⠿ ⣿"),
        ("Unicode emoji", "🎲 🏁 🛡 🎮 ⚡"),
    ]
    return [
        paragraph("Different materials, one native message. These are real text and structured blocks, not a web-icon canvas."),
        t.PageBlockTable(text("GLYPH PALETTE / client fonts decide the artwork"), [
            t.PageTableRow([t.PageTableCell(text=text(name)), t.PageTableCell(text=text(glyphs))])
            for name, glyphs in palettes
        ], bordered=True, striped=True, compact=True),
        t.PageBlockDivider(),
        t.PageBlockPreformatted(text("RADAR     ╱│╲      CORE  ▰▰▰▰▱ 80%\n        ───◆───    LINK  ▰▰▰▱▱ 60%\n          ╲│╱      SYNC  [ ONLINE ]"), "text"),
        t.PageBlockDetails([
            paragraph("Native: Unicode labels, monospaced art, tables, nested details, emphasis, copy/URL/callback controls, and uploaded media. Custom emoji need real Telegram document IDs and applicable account/bot permissions; no invented IDs are sent."),
            paragraph("Browser only: Font Awesome/Lucide web fonts, arbitrary CSS, inline SVG rendering, HTML5 canvas, WebGL and running JavaScript. Exporting web artwork to an uploaded PNG makes a native image, not a running webpage."),
            paragraph("38 visual methods are documented in docs/ARCADE_VISUAL_METHODS.md. The offline companion stays a downloadable local file; nothing is publicly deployed."),
        ], text("What is actually native?"), open=False),
    ]

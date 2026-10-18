"""Pure deterministic state machine for a multiplayer Ludo game."""
from __future__ import annotations

import secrets
from dataclasses import dataclass, field

TRACK = 52
HOME = 6
TOKENS = 4
SAFE_SQUARES = frozenset((0, 8, 13, 21, 26, 34, 39, 47))
AFK_SECONDS = 90.0
EVENT_LIMIT = 100
ROLL_LIMIT = 100
FINISH = TRACK + HOME - 1


@dataclass
class Player:
    """Store one player's seat and token progress."""
    user: int
    offset: int
    tokens: list[int] = field(default_factory=lambda: [-1] * TOKENS)
    resigned: bool = False
    finished: bool = False


class Ludo:
    """Run a 2-4 player Ludo match with explicit actions and injected dice."""
    def __init__(self, host: int, now: float = 0):
        self.players = [Player(int(host), 0)]
        self.status = "lobby"
        self.active = 0
        self.winners: list[int] = []
        self.events: list[str] = []
        self.last_activity = float(now)
        self.turn_started = float(now)
        self.rolls: list[int] = []
        self.sixes = 0
        self._dice: int | None = None
        self._event(f"{host} opened a lobby")

    @property
    def host(self):
        """Return the lobby host's user ID."""
        return self.players[0].user

    @property
    def dice(self):
        """Return the pending die value, or None before a roll."""
        return self._dice

    @property
    def log(self):
        """Expose the bounded event history."""
        return self.events

    @property
    def turn(self):
        """Return the current player ID, or None when no turn is active."""
        return self.current_player

    @property
    def current_player(self) -> int | None:
        """Return the current player's user ID, if a game is active."""
        return self.players[self.active].user if self.status == "playing" else None

    @property
    def scoreboard(self):
        """Return distinct finish rankings followed by unfinished seats."""
        ranked = [next((p for p in self.players if p.user == uid), None) for uid in self.winners]
        rest = [p for p in self.players if p.user not in self.winners]
        return [(p.user, "finished" if p.finished else "resigned" if p.resigned else "remaining" if self.status == "finished" else "playing") for p in ranked + rest if p]

    def _event(self, text: str) -> str:
        self.events.append(text)
        del self.events[:-EVENT_LIMIT]
        return text

    def apply(self, action: str, user: int, now: float, roll=None):
        """Apply a lobby or turn action and return its event description."""
        user, now = int(user), float(now)
        if action == "join":
            if self.status != "lobby" or any(p.user == user for p in self.players) or len(self.players) >= 4:
                raise ValueError("Cannot join this lobby")
            self.players.append(Player(user, len(self.players) * 13))
            return self._event(f"{user} joined")
        if action == "leave":
            if self.status != "lobby" or user == self.host or not any(p.user == user for p in self.players):
                raise ValueError("Only a guest can leave a lobby")
            self.players = [p for p in self.players if p.user != user]
            for i, p in enumerate(self.players): p.offset = i * 13
            return self._event(f"{user} left")
        if action == "cancel":
            self._require_host(user)
            if self.status not in ("lobby", "playing"):
                raise ValueError("Cannot cancel this game")
            self.status = "cancelled"
            self._dice = None
            return self._event("Game cancelled by host")
        if action == "start":
            self._require_host(user)
            if self.status != "lobby" or len(self.players) < 2:
                raise ValueError("At least two players are required")
            self.status, self.active = "playing", 0
            self.last_activity = self.turn_started = now
            return self._event("Game started")
        if action == "rematch":
            if self.status not in {"finished", "cancelled"} or user != self.host:
                raise ValueError("Only the host can reopen a finished or closed table")
            self.players = [Player(self.host, 0)]
            self.active = 0
            self.winners = []
            self.status = "lobby"
            self._dice = None
            self.sixes = 0
            self.rolls = []
            self.last_activity = self.turn_started = now
            return self._event("Rematch lobby opened; players must rejoin")
        if self.status != "playing": raise ValueError("Game is not active")
        if action in ("resign", "skip"):
            player = self._seated(user)
            if action == "skip":
                if now - self.last_activity < AFK_SECONDS: raise ValueError("AFK skip threshold has not elapsed")
                self._dice = None
                return self._advance(f"{user} skipped inactive turn", now, force=True)
            was_current = player.user == self.current_player
            player.resigned = True
            player.tokens[:] = [-1] * TOKENS
            if self._remaining() <= 1 or was_current:
                self._dice = None
                self.sixes = 0
                return self._remove_active(f"{user} resigned", now)
            return self._event(f"{user} resigned; current turn continues")
        if action == "roll":
            self._check_turn(user)
            if self._dice is not None: raise ValueError("Move a token before rolling again")
            die = int(roll() if roll else secrets.randbelow(6) + 1)
            if not 1 <= die <= 6: raise ValueError("Dice must be in 1..6")
            self.rolls.append(die)
            del self.rolls[:-ROLL_LIMIT]
            self._dice = die
            self.last_activity = now
            if die == 6:
                self.sixes += 1
                if self.sixes == 3:
                    self._dice = None
                    self.sixes = 0
                    return self._advance("Three sixes: third roll forfeited", now, force=True)
            else: self.sixes = 0
            if not self.legal_moves():
                self._dice = None
                if die != 6: self.sixes = 0
                return self._advance("No legal move; turn passed", now, extra=die == 6)
            return self._event(f"{user} rolled {die}")
        if action.startswith("move"):
            self._check_turn(user)
            if self._dice is None: raise ValueError("Roll first")
            try: token = int(action.split(":", 1)[1] if ":" in action else action[4:])
            except ValueError as exc: raise ValueError("Use move:0 through move:3") from exc
            moves = self.legal_moves()
            if token not in moves: raise ValueError("That token cannot make this move")
            p, die = self.players[self.active], self._dice
            old = p.tokens[token]
            p.tokens[token] = 0 if old == -1 else old + die
            destination = p.tokens[token]
            captured = []
            if destination < TRACK:
                square = (p.offset + destination) % TRACK
                if square not in SAFE_SQUARES:
                    for other in self.players:
                        if other is p or other.resigned or other.finished: continue
                        for i, pos in enumerate(other.tokens):
                            if 0 <= pos < TRACK and (other.offset + pos) % TRACK == square:
                                other.tokens[i] = -1
                                captured.append(other.user)
            self._dice = None
            self.last_activity = now
            if all(pos == FINISH for pos in p.tokens):
                p.finished = True
                self.winners.append(p.user)
                return self._remove_active(f"{p.user} finished", now)
            if captured:
                self.turn_started = now
                return self._event(f"{p.user} captured {', '.join(map(str, captured))}; bonus turn")
            if destination == FINISH:
                self.turn_started = now
                return self._event(f"{p.user} finished a token; bonus turn")
            if die == 6:
                self.turn_started = now
                return self._event(f"{p.user} moved token {token}; bonus turn")
            self.sixes = 0
            return self._advance(f"{p.user} moved token {token}", now, force=True)
        raise ValueError("Unknown action")

    def _remaining(self):
        return sum(not p.resigned and not p.finished for p in self.players)

    def _remove_active(self, message, now):
        if self._remaining() <= 1:
            self.status = "finished"
            # A lone remaining player is the final ranking, unless nobody remains.
            for p in self.players:
                if not p.resigned and not p.finished and p.user not in self.winners:
                    self.winners.append(p.user)
            return self._event(message + "; match complete")
        return self._advance(message, now, force=True)

    def _require_host(self, user):
        if user != self.host: raise ValueError("Only the host can manage this game")

    def _seated(self, user):
        for p in self.players:
            if p.user == user and not p.resigned and not p.finished: return p
        raise ValueError("Player is not an active seated player")

    def _check_turn(self, user):
        if self.status != "playing" or self.current_player != user:
            raise ValueError("Not your turn")
        self._seated(user)

    def _advance(self, message, now, extra=False, force=False):
        if self.status == "playing":
            if not extra or force:
                for step in range(1, len(self.players) + 1):
                    idx = (self.active + step) % len(self.players)
                    if not self.players[idx].resigned and not self.players[idx].finished:
                        self.active = idx
                        break
                self.sixes = 0
            self.turn_started = float(now)
            self.last_activity = float(now)
        return self._event(message)

    def _blocked(self, player, progress):
        square = (player.offset + progress) % TRACK
        return any(sum(1 for pos in other.tokens if 0 <= pos < TRACK and (other.offset + pos) % TRACK == square) >= 2
                   for other in self.players if other is not player and not other.resigned and not other.finished)

    def legal_moves(self) -> list[int]:
        """Return token indexes whose complete path avoids enemy blockades."""
        if self.status != "playing" or self._dice is None: return []
        player, die = self.players[self.active], self._dice
        result = []
        for i, pos in enumerate(player.tokens):
            if pos == -1:
                if die == 6 and not self._blocked(player, 0): result.append(i)
            elif (0 <= pos < FINISH and pos + die <= FINISH
                  and all(not self._blocked(player, step) for step in range(pos + 1, min(pos + die, TRACK - 1) + 1))):
                result.append(i)
        return result

    def board_text(self) -> str:
        """Render a phone-width cross board and an unambiguous numbered token ledger."""
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
        grid = [["  "] * 15 for _ in range(15)]
        for square, (r, c) in enumerate(path):
            grid[r][c] = "* " if square in SAFE_SQUARES else ". "
        for seat, lane in enumerate(lanes):
            for r, c in lane:
                grid[r][c] = "abcd"[seat] + " "
        occupied = {}
        for seat, p in enumerate(self.players):
            if p.resigned:
                continue
            for token, pos in enumerate(p.tokens):
                cell = yards[seat][token] if pos == -1 else path[(p.offset + pos) % TRACK] if pos < TRACK else lanes[seat][pos - TRACK]
                occupied.setdefault(cell, []).append("ABCD"[seat] + str(token + 1))
        for (r, c), tokens in occupied.items():
            grid[r][c] = tokens[0] if len(tokens) == 1 else tokens[0][0] + "+" if len({token[0] for token in tokens}) == 1 else "++"
        grid[7][7] = "HH"
        rows = ["".join(line).rstrip() for line in grid]
        rows.append("* safe | + stack | HH home")
        for seat, p in enumerate(self.players):
            positions = ["Y" if x == -1 else "F" if x == FINISH else f"H{x - TRACK + 1}" if x >= TRACK else str((p.offset + x) % TRACK) for x in p.tokens]
            rows.append("ABCD"[seat] + ": " + " ".join(f"{i + 1}={value}" for i, value in enumerate(positions)))
        return "\n".join(rows)

    def status_text(self) -> str:
        """Describe lobby, current turn, or outcome and rankings."""
        if self.status == "lobby": return f"Ludo lobby: {len(self.players)}/4; host {self.host}"
        if self.status == "playing": return f"Ludo: {self.current_player}'s turn" + (f"; rolled {self._dice}" if self._dice else "") + f"; rankings {self.winners}"
        return f"Ludo {self.status}: rankings {self.winners}; seats {self.scoreboard}"

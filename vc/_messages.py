"""Player chat messages in the fork's ``v2`` markdown parse mode.

Replaces the HTML-based notices. Two properties of this fork's v2 parser are
non-obvious and drove the design here.

**Entities, not backslashes.** The parser drops ``<``, ``&`` and backtick
from its input, and backslash-escaping does not survive it - ``a\\&b``
parses to ``a``. The escape that works is an HTML entity, which the parser
decodes back to the literal character:

    a&amp;b  ->  a&b
    a&lt;b   ->  a<b
    a&#96;b  ->  a`b

**The send path is lenient.** ``TelegramClient._parse_message_text`` calls
``parse_mode.parse(message)`` with no ``strict`` argument, so the strict
branch is never taken. Everything must be escaped explicitly before it
reaches the text - a track titled ``Tom & Jerry "Theme" <live>`` otherwise
loses its ``<live>`` entirely.

Delimiters, verified against ``telethon.extensions.markdownv2``:

    **bold**   __italic__   --underline--   ~~strike~~
    `code`     ||spoiler||  **>blockquote   ```pre```
"""

from __future__ import annotations

import re
from typing import Any, Optional

from ._player import PlayerState, Track

__all__ = [
    "PARSEMODE",
    "safe",
    "esc",
    "fmt_duration",
    "status_msg",
    "now_playing_msg",
    "queued_msg",
    "finished_msg",
    "queue_list_msg",
    "nothing_playing_msg",
    "error_msg",
    "paused_msg",
    "resumed_msg",
    "loop_msg",
    "cleared_msg",
    "mute_msg",
]

#: ``'v2'`` maps to ``telethon.extensions.markdownv2.Markdown`` in
#: ``telethon/utils.py`` - the fork's modern markdown parser.
PARSEMODE = "v2"

#: Characters the v2 parser would drop, or use to open a formatting span.
#: Escaped as entities: harmless to read, and they decode back to the
#: literal character rather than disappearing.
#:
#: ``>`` is deliberately absent. It is safe unescaped except at the start of
#: a line, where the parser reads it as a blockquote delimiter - and
#: ``&gt;`` does not fix that, because the ``&`` is itself escaped and the
#: literal text ``&gt;`` reaches Telegram. ``_leading_gt`` below handles the
#: line-start case with a numeric entity, which does survive.
_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    "`": "&#96;",
    "*": "&#42;",
    "_": "&#95;",
    "~": "&#126;",
    "|": "&#124;",
    "[": "&#91;",
    "]": "&#93;",
}


def _leading_gt(text: str) -> str:
    """Neutralise blockquote delimiters at the start of any line.

    The parser scans each line for a leading ``>`` after entity decoding.
    A numeric entity is used because it decodes to ``>`` *after* the
    delimiter scan, so the span never opens.
    """
    if ">" not in text:
        return text
    return re.sub(r"^(\s*)>", lambda m: m.group(1) + "&#62;", text, flags=re.M)


def esc(value: Any, limit: int = 0) -> str:
    """Escape *value* for the v2 parse mode."""
    text = "" if value is None else str(value)
    if limit and len(text) > limit:
        text = text[: max(0, limit - 1)] + "…"
    text = "".join(_ESCAPES.get(ch, ch) for ch in text)
    # After the per-character pass, '&' has been doubled, so a numeric
    # entity inserted here is safe to use for the line-leading '>' case.
    return _leading_gt(text)


#: Backwards-compatible alias. Both names existed; ``esc`` is preferred.
safe = esc


def fmt_duration(seconds: float) -> str:
    """Render seconds as ``M:SS`` (or ``H:MM:SS`` past an hour)."""
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return "LIVE"
    if total <= 0:
        return "LIVE"
    hours, rem = divmod(total, 3600)
    mins, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins}:{secs:02d}"


def _position_line(state: Optional[PlayerState]) -> str:
    """The ``▶ 1:23 / 4:56`` elapsed/total line, omitted for live streams."""
    if state is None or state.current is None or state.current.is_live:
        return ""
    return f"▶ {fmt_duration(state.position())} / {fmt_duration(state.current.duration)}"


def _rows(track: Track) -> dict:
    """Metadata rows. Every value is escaped - they are all user-derived."""
    rows: dict = {"Song": track.title}
    if track.artist and track.artist.lower() != "unknown artist":
        rows["By"] = track.artist
    rows["Type"] = (
        "Live stream" if track.is_live else fmt_duration(track.duration)
    )
    if track.platform:
        rows["Platform"] = track.platform.capitalize()
    rows["Requested by"] = track.requester
    return rows


def _kv(rows: dict) -> str:
    """Aligned ``key : value`` block using the fork's code-span delimiter."""
    if not rows:
        return ""
    return "\n".join(f"  `{esc(k)}` : `{esc(v)}`" for k, v in rows.items())


def status_msg(head: str, rows: dict | None = None, foot: str = "") -> str:
    """A bold head, an optional ``key : value`` block, an optional footnote.

    Used for the transient search/fetch status too, so the whole command
    surface shares one look under ``v2``.
    """
    parts = [f"**{esc(head)}**"]
    block = _kv(rows or {})
    if block:
        parts.append(block)
    if foot:
        parts.append(f"**>{esc(foot)}**")
    return "\n\n".join(parts)


def now_playing_msg(track: Track, state: Optional[PlayerState] = None) -> str:
    """The 'now playing' notice, with elapsed time and queue position."""
    if state is not None and len(state.queue) > 1:
        head = f"Now Playing · queued {len(state.queue) - 1}"
    else:
        head = "Now Playing"

    foot = _position_line(state)
    if not foot and state is not None and state.loop:
        foot = f"🔁 looping {state.loop}x"
    return status_msg(head, _rows(track), foot)


def queued_msg(track: Track, state: Optional[PlayerState] = None) -> str:
    """The 'added to queue' notice, stating the position it landed at."""
    pending = (len(state.queue) - 1) if state is not None else 0
    head = f"Queued · position {pending}" if pending else "Queued"
    foot = "Will play after the current track" if pending else "Playing next"
    return status_msg(head, _rows(track), foot)


def finished_msg(track: Track, state: Optional[PlayerState] = None) -> str:
    """The 'finished playing' notice, with the next track if there is one."""
    parts = [f"**{esc('Finished')}**", "", f"  `Song` : `{esc(track.title)}`"]
    if state is not None and state.current is not None:
        nxt = f"Next up: {state.current.title} - {state.current.artist}"
        parts += ["", f"**>{esc(nxt)}**"]
    return "\n".join(parts)


def nothing_playing_msg(state: Optional[PlayerState] = None) -> str:
    """Reply when a playback command is used with an empty queue."""
    if state is not None and state.is_paused:
        return status_msg("Paused", {"Status": "Playback is paused"})
    return status_msg(
        "Nothing Playing",
        {"Queue": "empty"},
        "Add a track with .vplay, then use .vskip to advance.",
    )


def queue_list_msg(state: PlayerState) -> str:
    """The full queue listing, current track first."""
    tracks = list(state.queue)
    if not tracks:
        return nothing_playing_msg(state)

    def line(i: int, t: Track) -> str:
        mark = "▶" if i == 0 else f"{i}."
        dur = "LIVE" if t.is_live else fmt_duration(t.duration)
        # The artist is external metadata exactly like the title, and this
        # fork's v2 parser silently drops unescaped backticks and angle
        # brackets. Interpolating it raw mangled artist names in the queue
        # while the title survived.
        artist = (
            f" — {esc(t.artist)}"
            if t.artist and t.artist.lower() != "unknown artist"
            else ""
        )
        return f"{mark} {esc(t.title)}{artist} [{dur}]"

    parts = [f"**{esc('Queue')}**", "", f"  `Now` : `{line(0, tracks[0])}`"]
    if len(tracks) > 1:
        up = "\n".join(line(i, t) for i, t in enumerate(tracks[1:], start=1))
        parts += ["", f"  `Up next` :", f"___\n{up}\n___"]
    elif state.loop:
        parts += ["", f"  `Loop` : `{state.loop}x`"]
    return "\n".join(parts)


def error_msg(what: str, error: str = "") -> str:
    """A player failure notice, with the reason in the expandable block."""
    return status_msg(f"Couldn't {what}", None, error or "unknown error")


def paused_msg(track: Optional[Track]) -> str:
    return status_msg(
        "Paused",
        {"Song": track.title} if track else None,
        "Use .vresume to continue",
    )


def resumed_msg(track: Optional[Track]) -> str:
    return status_msg("Resumed", {"Song": track.title} if track else None)


def loop_msg(track: Optional[Track], count: int) -> str:
    rows = {"Song": track.title} if track else None
    if count <= 0:
        return status_msg("Loop Off", rows)
    return status_msg(
        f"Looping {count}x", rows, "Resets when the queue ends"
    )


def cleared_msg() -> str:
    return status_msg("Queue cleared", foot="Playback stopped")


def mute_msg(muted: bool) -> str:
    return status_msg("Stream muted" if muted else "Stream unmuted")

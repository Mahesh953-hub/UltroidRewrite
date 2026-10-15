# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid >
# PLease read the GNU Affero General Public License in
# <https://www.gnu.org/licenses/agpl-3.0.html>.
"""
✘ Commands Available -

• `{i}vplay <query or url>`
    Play a track in the group voice chat. Adds to the queue if something
    is already playing. Accepts a YouTube/Saavn URL, a bare video id, or a
    free-text search.

    A status message is sent immediately - before the search or extraction
    round-trip - and edited in place as the work advances. The wording
    reflects the query: "Searching" for free text, "Fetching" for a bare id
    or a link, because those two skip the search step entirely. All player
    notices use the fork's `v2` markdown parse mode.

• `{i}vpause` / `{i}vresume`
    Pause or resume playback.

• `{i}vskip`
    Skip the current track.

• `{i}vqueue`
    Show the queue.

• `{i}vclear`
    Clear the queue and stop playback.

• `{i}vloop <n>`
    Loop the current track n times. `0` or `off` disables looping.

• `{i}vcmute` / `{i}vcunmute`
    Mute or unmute the stream in the group call.

• `{i}vnow`
    Show the current track with elapsed time.
"""

import asyncio
import re
from typing import Optional

from telethon.tl.functions.channels import (
    GetFullChannelRequest as getchat,
)
from telethon.tl.functions.phone import (
    CreateGroupCallRequest as startvc,
    DiscardGroupCallRequest as stopvc,
)
from telethon.tl.functions.phone import (
    EditGroupCallTitleRequest as settitle,
    GetGroupCallRequest as getvc,
)
from telethon.tl.functions.phone import InviteToGroupCallRequest as invitetovc

from . import get_string, ultroid_cmd
from vc._messages import (
    PARSEMODE,
    cleared_msg,
    error_msg,
    finished_msg,
    loop_msg,
    mute_msg,
    nothing_playing_msg,
    now_playing_msg,
    paused_msg,
    queue_list_msg,
    queued_msg,
    resumed_msg,
    status_msg,
)
from vc._player import PlaybackState, track_from_cached
from vc._pytgcalls import Error, Ok, call
from vc._messages import esc
from vc._query import classify, status_head, status_rows
from vc._resolver import ExtractionError, resolve_audio

_YT_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LOOP_OFF = {"0", "off", "none", "no", "disable", "stop"}


# ── group-call plumbing ────────────────────────────────────────────────────


async def get_call(event):
    mm = await event.client(getchat(event.chat_id))
    xx = await event.client(getvc(mm.full_chat.call, limit=1))
    return xx.call


def user_list(l, n):
    "Generate a list of users in a group, formatted for invites."
    for i in l:
        if i not in n:
            n.append(i)
            yield f"@{i}"


# ── helpers ────────────────────────────────────────────────────────────────


def _requester(event) -> str:
    """A display name for the user who queued the track.

    Returned raw: the message layer escapes every value exactly once. Escaping
    here as well would double-encode, showing a user named "Tom & Jerry" as
    "Tom &amp; Jerry".
    """
    try:
        return event.get_display_name()
    except Exception:
        try:
            return f"@{event.sender_id}"
        except Exception:
            return "unknown"


async def _ensure_call(event) -> bool:
    """Make sure a group call exists, starting one if needed.

    Returns False (after replying) when the call cannot be started.
    """
    try:
        mm = await event.client(getchat(event.chat_id))
    except Exception as e:
        await _say(event, error_msg("read the group", str(e)), time=15)
        return False
    if mm.full_chat.call:
        return True
    try:
        await event.client(startvc(event.chat_id))
    except Exception as e:
        await _say(event, error_msg("start the voice chat", str(e)), time=15)
        return False
    return True


async def _say(e, text: str, *, time: Optional[int] = None, **kw):
    """Send a player notice in the fork's v2 parse mode.

    ``eor`` forwards **kwargs to the client but sets no default parse mode,
    so v2 markup would otherwise render as literal ``**`` and `````.
    """
    return await e.eor(text, time=time, parse_mode=PARSEMODE, **kw)


async def _resolve_to_track(event, query: str, status=None):
    """Turn a user query into a CachedTrack, or None after replying.

    *status* is the message that already shows the searching/fetching notice.
    It is edited in place as the work advances, so the user watches one
    message progress rather than seeing a burst of separate notices.
    """
    from vc._dataclass import CachedTrack, ChannelPlay
    from vc._diagnose import diagnose
    from vc._youtube import YouTubeData

    kind = classify(query)

    async def stage(head: str, rows: dict | None = None) -> None:
        """Update the in-flight status message, tolerating a lost message."""
        if status is None:
            return
        try:
            await status.edit(
                status_msg(head, rows), parse_mode=PARSEMODE
            )
        except Exception:
            pass

    yt = YouTubeData(query)
    try:
        info = await yt.get_track()
    except Exception as e:
        # Surface the real cause, not eight copies of the same transport
        # error. TLS interception is a network property, not a bad query.
        headline, detail, _ = diagnose([str(e)], query)
        await _say(
            event,
            status_msg(headline, None, detail),
            time=20,
        )
        return None

    if not info:
        # A None result is ambiguous on its own: it means either "nothing
        # matched" or "extraction failed". YouTubeData records the reason, so
        # a TLS interception is diagnosed rather than reported as an empty
        # search - which told the user to try a different query when the
        # actual problem was the network.
        if getattr(yt, "last_extract_error", ""):
            headline, detail, _ = diagnose([yt.last_extract_error], query)
            await _say(event, status_msg(headline, None, detail), time=20)
            return None

        await _say(
            event,
            status_msg(
                "No results",
                {"Query": query},
                "Try a YouTube URL, a video id, or a different search.",
            ),
            time=15,
        )
        return None

    await stage(
        f"Found `{info.name or 'Unknown'}`"[:200],
        {"By": info.artist or "Unknown artist", "Next": "adding to the queue"},
    )

    return CachedTrack(
        url=info.url or query,
        name=info.name or "Unknown",
        artist=info.artist or "Unknown artist",
        loop=0,
        user=_requester(event),
        file_path="",
        thumbnail=info.cover or "",
        track_id=info.tc or "",
        duration=int(info.duration or 0),
        is_video=False,
        platform=info.platform or "youtube",
        channel=ChannelPlay(chat_id=event.chat_id, is_channel=event.is_channel),
    )


async def _start_current(event, chat_id: int) -> None:
    """Begin playback of the head of the queue."""
    player = call.players.get(chat_id)
    cur = player.current
    if cur is None:
        return
    player.start()
    # _play_song sends its own "now playing" notice via the bot client, so
    # do not post a second one here.
    await call._play_song(chat_id, cur.to_cached())


# ── playback commands ──────────────────────────────────────────────────────


@ultroid_cmd(
    pattern="vplay(?: |$)(.*)",
    groups_only=True,
)
async def vplay(e):
    """Play a track, or queue it if something is already playing."""
    query = e.pattern_match.group(1).strip()
    if not query:
        return await _say(
            e,
            status_msg("Usage", None, f"{e.text.split()[0]} <query or url>"),
            time=15,
        )

    if not await _ensure_call(e):
        return

    # Acknowledge immediately, before the search/extraction round-trip. The
    # wording reflects what the query actually is: a bare id or a link skips
    # the search step entirely, so saying "Searching" for one would misreport
    # a fast fetch as a slow search.
    kind = classify(query)
    status = await e.client.send_message(
        e.chat_id,
        status_msg(status_head(kind, query), status_rows(kind)),
        parse_mode=PARSEMODE,
        reply_to=e.id,
    )

    chat_id = e.chat_id
    async with call.players.lock(chat_id):
        track = await _resolve_to_track(e, query, status)
        if track is None:
            return

        player = call.players.get(chat_id)
        was_idle = not player.is_playing
        player.queue.add(track_from_cached(track))

    if was_idle:
        await _start_current(e, chat_id)
    else:
        player = call.players.get(chat_id)
        await _say(e, queued_msg(track_from_cached(track), player))


@ultroid_cmd(
    pattern="vskip$",
    groups_only=True,
)
async def vskip(e):
    """Skip the current track."""
    chat_id = e.chat_id
    player = call.players.get(chat_id)
    if player.current is None:
        return await _say(e, nothing_playing_msg(player), time=15)

    done = player.current
    async with call.players.lock(chat_id):
        await call.play_next(chat_id)

    after = call.players.get(chat_id)
    if after.current is None:
        await _say(e, finished_msg(done, after))
    else:
        await _say(e, finished_msg(done, after))


@ultroid_cmd(
    pattern="vpause$",
    groups_only=True,
)
async def vpause(e):
    """Pause playback."""
    chat_id = e.chat_id
    player = call.players.get(chat_id)
    if not player.is_playing:
        return await _say(e, nothing_playing_msg(player), time=15)
    res = await call.pause(chat_id)
    if isinstance(res, Error):
        return await _say(e, error_msg("pause", res.message), time=15)
    player.pause()
    await _say(e, paused_msg(player.current))


@ultroid_cmd(
    pattern="vresume$",
    groups_only=True,
)
async def vresume(e):
    """Resume playback."""
    chat_id = e.chat_id
    player = call.players.get(chat_id)
    if not player.is_paused:
        return await _say(e, nothing_playing_msg(player), time=15)
    res = await call.resume(chat_id)
    if isinstance(res, Error):
        return await _say(e, error_msg("resume", res.message), time=15)
    player.resume()
    await _say(e, resumed_msg(player.current))


@ultroid_cmd(
    pattern="vqueue$",
    groups_only=True,
)
async def vqueue(e):
    """Show the queue."""
    await _say(e, queue_list_msg(call.players.get(e.chat_id)), time=20)


@ultroid_cmd(
    pattern="vnow$",
    groups_only=True,
)
async def vnow(e):
    """Show the current track with elapsed time."""
    player = call.players.get(e.chat_id)
    if player.current is None:
        return await _say(e, nothing_playing_msg(player), time=15)
    await _say(e, now_playing_msg(player.current, player))


@ultroid_cmd(
    pattern="vclear$",
    groups_only=True,
)
async def vclear(e):
    """Clear the queue and stop playback."""
    chat_id = e.chat_id
    async with call.players.lock(chat_id):
        player = call.players.get(chat_id)
        if player.current is None:
            return await _say(e, nothing_playing_msg(player), time=15)
        player.stop()
        try:
            await call.end(chat_id)
        except Exception:
            pass
    await _say(e, cleared_msg())


@ultroid_cmd(
    pattern=r"vloop(?: |$)(.*)",
    groups_only=True,
)
async def vloop(e):
    """Set the loop count for the current track."""
    arg = e.pattern_match.group(1).strip().lower()
    chat_id = e.chat_id
    player = call.players.get(chat_id)
    if player.current is None:
        return await _say(e, nothing_playing_msg(player), time=15)

    if not arg or arg in _LOOP_OFF:
        player.loop = 0
    elif arg.isdigit():
        player.loop = int(arg)
    else:
        return await _say(
            e,
            status_msg("Usage", None, f"{e.text.split()[0]} <n|off>"),
            time=15,
        )
    await _say(e, loop_msg(player.current, player.loop))


@ultroid_cmd(
    pattern=r"vcmute$",
    groups_only=True,
)
async def vcmute(e):
    """Mute the bot's stream in the group call."""
    res = await call.mute(e.chat_id)
    if isinstance(res, Error):
        return await _say(e, error_msg("mute", res.message), time=15)
    await _say(e, mute_msg(True))


@ultroid_cmd(
    pattern=r"vcunmute$",
    groups_only=True,
)
async def vcunmute(e):
    """Unmute the bot's stream in the group call."""
    res = await call.unmute(e.chat_id)
    if isinstance(res, Error):
        return await _say(e, error_msg("unmute", res.message), time=15)
    await _say(e, mute_msg(False))


# ── existing group-call commands (unchanged) ───────────────────────────────


@ultroid_cmd(
    pattern="stopvc$",
    admins_only=True,
    groups_only=True,
)
async def stopvc(e):
    try:
        await e.client(stopvc(await get_call(e)))
        call.players.drop(e.chat_id)
        await e.eor(get_string("vct_4"))
    except Exception as ex:
        await _say(e, f"**Error**\n\n  `{esc(ex)}`")


@ultroid_cmd(
    pattern="vcinvite$",
    groups_only=True,
)
async def vcinvite(e):
    ok = await e.eor(get_string("vct_3"))
    users = []
    z = 0
    async for x in e.client.iter_participants(e.chat_id):
        if not x.bot:
            users.append(x.id)
    hmm = list(user_list(users, 6))
    for p in hmm:
        try:
            await e.client(invitetovc(call=await get_call(e), users=p))
            z += 6
        except Exception:
            pass
    await ok.edit(get_string("vct_5").format(z))


@ultroid_cmd(
    pattern="startvc$",
    groups_only=True,
)
async def startvc(e):
    try:
        await e.client(startvc(e.chat_id))
        await e.eor(get_string("vct_1"))
    except Exception as ex:
        await _say(e, f"**Error**\n\n  `{esc(ex)}`")


@ultroid_cmd(
    pattern="vctitle(?: |$)(.*)",
    admins_only=True,
    groups_only=True,
)
async def vctitle(e):
    title = e.pattern_match.group(1).strip()
    if not title:
        return await e.eor(get_string("vct_6"), time=5)
    try:
        await e.client(settitle(call=await get_call(e), title=title.strip()))
        await e.eor(get_string("vct_2").format(title))
    except Exception as ex:
        await _say(e, f"**Error**\n\n  `{esc(ex)}`")
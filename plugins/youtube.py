# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}yta <(youtube/any) link>`
   Download audio from the link.

• `{i}ytv <(youtube/any) link>`
   Download video  from the link.

• `{i}ytsa <(youtube) search query>`
   Search and download audio from youtube.

• `{i}ytsv <(youtube) search query>`
   Search and download video from youtube.

• `{i}ytdl <youtube link | search query>`
   Smart downloader with inline quality buttons
   (Audio Only / 360p / 720p / 1080p / Best).
   Supports direct links, search queries and playlists.
   Falls back to cookie-less backends (Invidious / Piped) on failure.

• `{i}ytpl <youtube playlist link>`
   Download a full playlist with per-video progress tracking.
"""

import glob
import os
import re
from typing import Any, List, Optional

from telethon import Button

from pyUltroid.fns import fns
from pyUltroid.fns import fns
from pyUltroid.fns.ytdl import (
    SearchResult,
    YtdlConfig,
    download_playlist,
    download_yt,
    download_yt_fallback,
    dler,
    get_yt_link,
    search_videos,
)

from custom.tg import ikm

from . import LOGS, asst, callback, get_string, ultroid_cmd

_YT_CB = "uyt"
_YT_RE = re.compile(
    r"youtube\.com/watch\?v=([\w-]{11})"
    r"|youtu\.be/([\w-]{11})"
    r"|youtube\.com/embed/([\w-]{11})"
    r"|youtube\.com/shorts/([\w-]{11})"
)


def _extract_yt_id(url: str) -> str | None:
    match = _YT_RE.search(url or "")
    if not match:
        return None
    return next((g for g in match.groups() if g), None)


def _quality_buttons(vid: str) -> Any:
    return ikm(
        [
            [
                ("Audio Only 🎧", f"{_YT_CB}:d:{vid}:audio"),
                ("360p", f"{_YT_CB}:d:{vid}:360p"),
            ],
            [
                ("720p", f"{_YT_CB}:d:{vid}:720p"),
                ("1080p", f"{_YT_CB}:d:{vid}:1080p"),
            ],
            [("Best ⚡", f"{_YT_CB}:d:{vid}:best")],
        ]
    )


async def _show_quality(event: Any, vid: str) -> None:
    """Show the quality-selection keyboard, with the video thumbnail when possible."""
    text = "`Select quality to download:`"
    buttons = _quality_buttons(vid)
    thumb = f"{vid}_q.jpg"
    thumb_ok = False
    try:
        await fns.download_file(f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", thumb)
        thumb_ok = os.path.exists(thumb)
    except Exception:
        thumb_ok = False

    delivered = False
    if not event.client._bot and asst is not None:
        try:
            if thumb_ok:
                await asst.send_file(
                    event.chat_id, file=thumb, caption=text, buttons=buttons
                )
            else:
                await asst.send_message(event.chat_id, text, buttons=buttons)
            try:
                await event.delete()
            except Exception:
                pass
            delivered = True
        except Exception as ex:
            LOGS.debug(f"asst quality send failed, falling back to client: {ex}")

    if not delivered and thumb_ok:
        try:
            await event.client.send_file(
                event.chat_id, file=thumb, caption=text, buttons=buttons
            )
            try:
                await event.delete()
            except Exception:
                pass
            delivered = True
        except Exception as ex:
            LOGS.debug(f"thumbnail send failed, falling back to text: {ex}")

    if not delivered:
        await event.edit(text, buttons=buttons)

    if thumb_ok:
        try:
            os.remove(thumb)
        except Exception:
            pass


async def _show_search_results(
    event: Any, query: str, results: list[SearchResult]
) -> None:
    buttons = []
    for r in results:
        title = r.title or r.id or "video"
        if len(title) > 35:
            title = title[:32] + "..."
        label = f"{title} ({r.duration or '0:00'})"
        buttons.append([(label, f"{_YT_CB}:q:{r.id}")])
    buttons.append(
        [Button.switch_inline("Search Again 🔍", query="yt ", same_peer=True)]
    )
    text = f"`YouTube search: {query}\nTap a result to choose quality:`"
    if not event.client._bot and asst is not None:
        try:
            await asst.send_message(event.chat_id, text, buttons=ikm(buttons))
            try:
                await event.delete()
            except Exception:
                pass
            return
        except Exception as ex:
            LOGS.debug(f"asst search send failed, falling back to edit: {ex}")
    await event.edit(text, buttons=ikm(buttons))


async def _download_quality(event: Any, vid: str, quality: str) -> None:
    """Download a single video at *quality* (callback-safe: uses message id)."""
    link = f"https://www.youtube.com/watch?v={vid}"
    if quality == "audio":
        opts = YtdlConfig.audio_extract("m4a", "192")
    else:
        fmt = YtdlConfig.QUALITY_FORMATS.get(quality, "best")
        opts = YtdlConfig.video(fmt=fmt)

    info = await dler(event, link, opts, download=True)
    if not info:
        kind = "audio" if quality == "audio" else "video"
        await download_yt_fallback(event, vid, kind)
        return

    title = info.get("title") or vid
    disp_title = title[:17] + "..." if len(title) > 20 else title
    id_ = info.get("id") or vid
    thumb = f"{id_}.jpg"
    try:
        await fns.download_file(
            info.get("thumbnail") or f"https://i.ytimg.com/vi/{id_}/hqdefault.jpg",
            thumb,
        )
    except Exception:
        thumb = None

    downloaded_file = None
    for x in glob.glob(f"{id_}*"):
        if not x.endswith(".jpg") and not x.endswith(".part"):
            downloaded_file = x
            break
    if not downloaded_file:
        if thumb:
            try:
                os.remove(thumb)
            except Exception:
                pass
        kind = "audio" if quality == "audio" else "video"
        await download_yt_fallback(event, vid, kind)
        return

    existing_ext = os.path.splitext(downloaded_file)[-1]
    file_renamed = disp_title + existing_ext
    try:
        os.rename(downloaded_file, file_renamed)
    except FileNotFoundError:
        try:
            os.rename(downloaded_file + existing_ext, file_renamed)
        except Exception:
            file_renamed = downloaded_file

    attributes = await fns.set_attributes(file_renamed)
    sent = False
    try:
        res, _ = await event.client.fast_uploader(
            file_renamed, show_progress=True, event=event, to_delete=True
        )
        await event.client.send_file(
            event.chat_id,
            file=res,
            caption=f"`{title}`",
            attributes=attributes,
            supports_streaming=True,
            thumb=thumb,
        )
        sent = True
    except Exception as ex:
        LOGS.error(f"yt quality upload failed: {ex}")
        await event.edit("`Upload failed.`")
    finally:
        if thumb:
            try:
                os.remove(thumb)
            except Exception:
                pass
    if sent:
        try:
            await event.delete()
        except Exception:
            pass


@ultroid_cmd(
    pattern="yt(a|v|sa|sv) ?(.*)",
)
async def download_from_youtube_(event: Any) -> None:
    opt = event.pattern_match.group(1).strip()
    xx = await event.eor(get_string("com_1"))
    if opt == "a":
        ytd = YtdlConfig.audio()
        url = event.pattern_match.group(2)
        if not url:
            return await xx.eor(get_string("youtube_1"))
        try:
            await fns.async_searcher(url, timeout=5)
        except Exception:
            return await xx.eor(get_string("youtube_2"))
    elif opt == "v":
        ytd = YtdlConfig.video()
        url = event.pattern_match.group(2)
        if not url:
            return await xx.eor(get_string("youtube_3"))
        try:
            await fns.async_searcher(url, timeout=5)
        except Exception:
            return await xx.eor(get_string("youtube_4"))
    elif opt == "sa":
        ytd = YtdlConfig.audio()
        try:
            query = event.text.split(" ", 1)[1]
        except IndexError:
            return await xx.eor(get_string("youtube_5"))
        url = get_yt_link(query)
        if not url:
            return await xx.edit(get_string("unspl_1"))
        await xx.eor(get_string("youtube_6"))
    elif opt == "sv":
        ytd = YtdlConfig.video()
        try:
            query = event.text.split(" ", 1)[1]
        except IndexError:
            return await xx.eor(get_string("youtube_7"))
        url = get_yt_link(query)
        if not url:
            return await xx.edit(get_string("unspl_1"))
        await xx.eor(get_string("youtube_8"))
    else:
        return
    await download_yt(xx, url, ytd)


@ultroid_cmd(pattern="ytdl ?(.*)")
async def ytdl_smart(event: Any) -> None:
    """Smart YouTube downloader with inline quality selection."""
    input_text = (event.pattern_match.group(1) or "").strip()
    xx = await event.eor(get_string("com_1"))
    if not input_text:
        return await xx.eor(
            "`Give me a YouTube link or a search query.\n"
            "Usage: .ytdl <link|query>  |  .ytdl <playlist link>`"
        )
    first = input_text.split()[0]
    if "list=" in first:
        # Playlist → full download with progress.
        await xx.eor("`Fetching playlist...`")
        await download_playlist(xx, first)
        return
    if "youtu" in first:
        vid = _extract_yt_id(first)
        if not vid:
            return await xx.eor("`Could not parse the YouTube link.`")
        await _show_quality(xx, vid)
        return

    query = input_text
    await xx.eor(f"`Searching YouTube for: {query}...`")
    results = await search_videos(query, limit=10)
    if not results:
        return await xx.eor(get_string("unspl_1"))
    await _show_search_results(xx, query, results)


@ultroid_cmd(pattern="ytpl ?(.*)")
async def ytdl_playlist_cmd(event: Any) -> None:
    """Download a full YouTube playlist with progress tracking."""
    link = (event.pattern_match.group(1) or "").strip()
    xx = await event.eor(get_string("com_1"))
    if not link:
        return await xx.eor("`Give me a YouTube playlist link.`")
    if "list=" not in link:
        return await xx.eor("`That doesn't look like a YouTube playlist link.`")
    await xx.eor("`Fetching playlist...`")
    await download_playlist(xx, link)


@callback(re.compile("uyt:(.*)"))
async def _yt_callback(event: Any) -> None:
    try:
        data = event.data.decode("utf-8")
    except Exception:
        return
    if data.startswith(f"{_YT_CB}:q:"):
        vid = data.split(":", 2)[2]
        await _show_quality(event, vid)
        return
    if data.startswith(f"{_YT_CB}:d:"):
        parts = data.split(":")
        vid = parts[2] if len(parts) > 2 else None
        quality = parts[3] if len(parts) > 3 else "best"
        if not vid:
            await event.answer("Invalid selection.", alert=True)
            return
        await _download_quality(event, vid, quality)
        return
    await event.answer("Expired — please retry.", alert=True)

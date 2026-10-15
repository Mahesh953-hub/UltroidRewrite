# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
# Modifed for cookies and fixed by @TrueSaiyan

import asyncio
import glob
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote

try:
    from youtubesearchpython import Playlist, VideosSearch
except ImportError:
    Playlist = VideosSearch = None

try:
    from youtube_search import YoutubeSearch
except ImportError:
    YoutubeSearch = None

try:
    from yt_dlp import YoutubeDL
    from yt_dlp.utils import DownloadError
except ImportError:
    YoutubeDL = None
    DownloadError = Exception  # type: ignore[assignment,misc]

from .. import LOGS, udB

from ..fns import fns


#  YouTube Engine
@dataclass
class SearchResult:
    id: str
    title: str
    url: str
    duration: str = "0:00"
    views: Any = None
    channel: str | None = None
    thumbnail: str | None = None


@dataclass
class FormatInfo:
    ytid: str
    type: str  # "audio" | "video"
    id: str  # quality id / yt-dlp format-selector token
    quality: str  # human label, e.g. "128KBPS" or "1280×720"
    size: int = 0
    ext: str | None = None


@dataclass
class VideoInfo:
    id: str
    title: str
    url: str = ""
    duration: int = 0
    thumbnail: str | None = None
    channel: str | None = None
    views: int | None = None
    formats: list[FormatInfo] = field(default_factory=list)
    extractor: str | None = None
    is_playlist: bool = False
    playlist_count: int | None = None
    entries: list["VideoInfo"] = field(default_factory=list)

    @classmethod
    def from_info(cls, info: dict[str, Any] | None) -> "VideoInfo":
        if not info:
            return cls(id="", title="", url="")
        is_playlist = info.get("_type") == "playlist"
        return cls(
            id=info.get("id") or "",
            title=info.get("title") or "",
            url=info.get("webpage_url") or info.get("url") or "",
            duration=int(info.get("duration") or 0),
            thumbnail=info.get("thumbnail"),
            channel=(
                info.get("channel") or info.get("uploader") or info.get("creator")
            ),
            views=info.get("view_count"),
            extractor=info.get("extractor"),
            is_playlist=is_playlist,
            playlist_count=info.get("playlist_count"),
            entries=(
                [cls.from_info(e) for e in (info.get("entries") or [])]
                if is_playlist
                else []
            ),
        )


#  YtdlConfig  -  common yt-dlp options with sensible defaults
class YtdlConfig:
    prefer_ffmpeg = True
    addmetadata = True
    geo_bypass = True
    nocheck_certificate = True
    quiet = True
    cookiefile = "cookies.txt"

    QUALITY_FORMATS: dict[str, str] = {
        "audio": "bestaudio/best",
        "360p": "best[height<=360]/best",
        "720p": "best[height<=720]/best",
        "1080p": "best[height<=1080]/best",
        "best": "best",
    }

    @classmethod
    def base(cls) -> dict[str, Any]:
        return {
            "prefer_ffmpeg": cls.prefer_ffmpeg,
            "addmetadata": cls.addmetadata,
            "geo_bypass": cls.geo_bypass,
            "nocheck_certificate": cls.nocheck_certificate,
            "quiet": cls.quiet,
        }

    @classmethod
    def audio(cls, ext: str = "m4a") -> dict[str, Any]:
        """Best-audio preset matching the legacy ``.yta``/``.ytsa`` behaviour."""
        opts = cls.base()
        opts.update({"format": "bestaudio", "outtmpl": f"%(id)s.{ext}"})
        return opts

    @classmethod
    def audio_extract(cls, ext: str = "m4a", quality: str = "192") -> dict[str, Any]:
        """Best-audio preset that re-encodes via FFmpeg (inline-button flow)."""
        opts = cls.base()
        opts.update(
            {
                "format": "bestaudio/best",
                "outtmpl": f"%(id)s.{ext}",
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": ext,
                        "preferredquality": str(quality),
                    },
                    {"key": "FFmpegMetadata"},
                ],
            }
        )
        return opts

    @classmethod
    def video(cls, fmt: str = "best", ext: str = "mp4") -> dict[str, Any]:
        opts = cls.base()
        opts.update(
            {
                "format": fmt,
                "outtmpl": f"%(id)s.{ext}",
                "postprocessors": [{"key": "FFmpegMetadata"}],
            }
        )
        return opts

    @classmethod
    def playlist(cls, fmt: str = "best", ext: str = "mp4") -> dict[str, Any]:
        """Playlist preset  -  keeps ``%(id)s`` so the upload glob still matches."""
        opts = cls.base()
        opts.update(
            {
                "format": fmt,
                "outtmpl": f"%(id)s.{ext}",
                "noplaylist": False,
                "postprocessors": [{"key": "FFmpegMetadata"}],
            }
        )
        return opts


#  Friendly error classification
def _friendly_ytdl_error(exc: BaseException) -> str:
    """Map common yt-dlp failures to a short, user-friendly message."""
    raw = str(exc)
    if isinstance(exc, DownloadError):
        cause = getattr(exc, "exc_info", None)
        if cause and isinstance(cause, tuple) and cause[1]:
            raw = str(cause[1]) or raw
    msg = raw.lower()
    if "sign in to confirm" in msg or "use cookies" in msg or "login required" in msg:
        return "`YouTube requires sign-in. Add a cookies.txt or YT credentials.`"
    if "private video" in msg or "is private" in msg:
        return "`This video is private and cannot be downloaded.`"
    if "video unavailable" in msg or "has been deleted" in msg or "deleted" in msg:
        return "`This video is unavailable or has been deleted.`"
    if "age" in msg and ("restricted" in msg or "confirm" in msg):
        return "`Age-restricted content  -  sign-in/cookies required to download.`"
    if "geo" in msg and ("not available" in msg or "restricted" in msg):
        return "`Geo-restricted content  -  not available in this region.`"
    if ("rate" in msg and "limit" in msg) or "too many requests" in msg or "429" in msg:
        return "`YouTube is rate-limiting downloads. Please retry shortly.`"
    if (
        "members-only" in msg
        or "members only" in msg
        or ("join this channel" in msg and "members" in msg)
    ):
        return "`This video is members-only and cannot be downloaded.`"
    if "premium" in msg and ("source" in msg or "only" in msg):
        return "`This video is Premium-only and cannot be downloaded.`"
    return f"`{type(exc).__name__}: {raw[:200]}`"


#  Download progress (ETA / speed / percentage in a single clean message)
# will be replaced with fmt.prog
def _progress_text(state: dict[str, Any], start_time: float) -> str:
    total = state.get("total_bytes") or 0
    done = state.get("downloaded_bytes") or 0
    speed = state.get("speed") or 0
    eta = state.get("eta") or 0
    pct = (done / total * 100) if total else 0
    bar_len = 12
    filled = int(pct / (100 / bar_len))
    bar = "█" * filled + "░" * (bar_len - filled)
    name = os.path.basename(state.get("filename") or "")
    if len(name) > 28:
        name = name[:25] + "..."
    elapsed = fns.time_formatter((time.time() - start_time) * 1000)
    return (
        f"`Downloading: {name or '...'}\n"
        f"{bar} {pct:.1f}%\n"
        f"Size: {fns.humanbytes(total)} | Done: {fns.humanbytes(done)}\n"
        f"Speed: {fns.humanbytes(speed)}/s | ETA: {fns.time_formatter(eta * 1000)}\n"
        f"Elapsed: {elapsed}`"
    )


class _ProgressState:
    """Shared state written by yt-dlp's ``progress_hooks`` (worker thread)."""

    def __init__(self):
        self.data = {
            "status": "idle",
            "filename": "",
            "total_bytes": 0,
            "downloaded_bytes": 0,
            "speed": 0,
            "eta": 0,
        }
        self.done = False

    def hook(self, d):
        status = d.get("status")
        if status:
            self.data["status"] = status
        self.data["filename"] = d.get("filename", self.data["filename"])
        self.data["total_bytes"] = (
            d.get("total_bytes") or d.get("total_bytes_estimate") or 0
        )
        self.data["downloaded_bytes"] = d.get("downloaded_bytes") or 0
        self.data["speed"] = d.get("speed") or 0
        self.data["eta"] = d.get("eta") or 0

    def finish(self):
        self.done = True


async def _progress_updater(event: Any, state: "_ProgressState") -> None:
    """Poll the shared progress state and edit *event* every few seconds."""
    start = time.time()
    while not state.done:
        try:
            if state.data.get("status") == "downloading":
                await event.edit(_progress_text(state.data, start))
        except Exception as ex:
            LOGS.debug(f"ytdl progress edit skipped: {ex}")
        await asyncio.sleep(4)
    # Leave the message as the last progress frame; the caller owns the next
    # edit (upload progress / final caption / friendly error).


async def ytdl_progress(k: dict[str, Any], start_time: float, event: Any) -> None:
    if k.get("status") == "error":
        return await event.edit("`error`")
    if k.get("status") == "downloading":
        try:
            await event.edit(_progress_text(k, start_time))
        except Exception as ex:
            LOGS.error(f"ytdl_progress: {ex}")


INVIDIOUS_INSTANCES = [
    "https://invidious.nerdvpn.de",
    "https://inv.nadeko.net",
    "https://invidious.tiekoetter.com",
    "https://invidious.f5.si",
]
PIPED_INSTANCES = [
    "https://pipedapi.kavin.rocks",
    "https://api.piped.private.coffee",
    "https://pipedapi.leptov.in",
]


def _fmt_duration(seconds: Any) -> str:
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        return "0:00"
    minutes, sec = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{sec:02d}" if hours else f"{minutes}:{sec:02d}"


async def _try_instances(
    instances: list[str], path: str, timeout: int = 8
) -> dict[str, Any] | None:
    """GET *path* from the first instance that responds with JSON."""
    for base in instances:
        try:
            data = await fns.async_searcher(base + path, re_json=True, timeout=timeout)
            if data:
                return data
        except Exception as ex:
            LOGS.debug(f"backend {base} failed: {ex}")
            continue
    return None


async def invidious_search(query: str, limit: int = 10) -> list[SearchResult]:
    data = await _try_instances(
        INVIDIOUS_INSTANCES, f"/api/v1/search?q={quote(query)}&type=video"
    )
    if not data:
        return []
    results: list[SearchResult] = []
    for item in data:
        if item.get("type") != "video":
            continue
        vid = item.get("videoId") or ""
        thumbs = item.get("videoThumbnails") or [{}]
        results.append(
            SearchResult(
                id=vid,
                title=item.get("title") or "",
                url=f"https://www.youtube.com/watch?v={vid}",
                duration=_fmt_duration(item.get("lengthSeconds")),
                views=item.get("viewCount"),
                channel=item.get("author"),
                thumbnail=thumbs[0].get("url"),
            )
        )
        if len(results) >= limit:
            break
    return results


async def piped_search(query: str, limit: int = 10) -> list[SearchResult]:
    data = await _try_instances(
        PIPED_INSTANCES, f"/search?q={quote(query)}&filter=videos"
    )
    if not data or not data.get("items"):
        return []
    results: list[SearchResult] = []
    for item in data["items"]:
        vid = (item.get("url") or "").split("v=")[-1]
        if not vid:
            continue
        results.append(
            SearchResult(
                id=vid,
                title=item.get("title") or "",
                url=f"https://www.youtube.com/watch?v={vid}",
                duration=_fmt_duration(item.get("duration")),
                views=item.get("views"),
                channel=item.get("uploaderName"),
                thumbnail=item.get("thumbnail"),
            )
        )
        if len(results) >= limit:
            break
    return results


async def search_videos(query: str, limit: int = 10) -> list[SearchResult]:
    """Unified YouTube search with graceful backend fallbacks.

    Tries, in order: ``youtubesearchpython`` → ``youtube_search`` → Invidious
    → Piped.  Returns a normalised list of :class:`SearchResult` models
    (each ``.to_dict()`` yields the legacy ``{id, title, duration, views,
    channel, thumb, link}`` shape).
    """
    # 1. youtubesearchpython (sync library → run in executor)
    if VideosSearch:
        try:
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(
                None, lambda: VideosSearch(query, limit=limit).result()
            )
            out: list[SearchResult] = []
            for v in raw.get("result", [])[:limit]:
                vid = v.get("id") or ""
                out.append(
                    SearchResult(
                        id=vid,
                        title=v.get("title") or "",
                        url=f"https://www.youtube.com/watch?v={vid}",
                        duration=v.get("duration") or "0:00",
                        views=(v.get("viewCount") or {}).get("text"),
                        channel=(v.get("channel") or {}).get("name"),
                        thumbnail=f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    )
                )
            if out:
                return out
        except Exception as ex:
            LOGS.debug(f"youtubesearchpython failed: {ex}")

    # 2. youtube_search (sync library → run in executor)
    if YoutubeSearch:
        try:
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(
                None, lambda: YoutubeSearch(query, max_results=limit).to_dict()
            )
            out = []
            for v in raw[:limit]:
                vid = v.get("id") or ""
                out.append(
                    SearchResult(
                        id=vid,
                        title=v.get("title") or "",
                        url=f"https://www.youtube.com/watch?v={vid}",
                        duration=(v.get("duration") or "0:00").strip(),
                        views=v.get("views"),
                        channel=(v.get("channel") or {}).get("name"),
                        thumbnail=(v.get("thumbnails") or [None])[0],
                    )
                )
            if out:
                return out
        except Exception as ex:
            LOGS.debug(f"youtube_search failed: {ex}")

    # 3. Invidious
    inv = await invidious_search(query, limit=limit)
    if inv:
        return inv

    # 4. Piped
    return await piped_search(query, limit=limit)


#  Cookie-less stream fetchers (Invidious / Piped)
async def invidious_video(video_id: str) -> dict[str, Any] | None:
    return await _try_instances(INVIDIOUS_INSTANCES, f"/api/v1/videos/{video_id}")


async def piped_streams(video_id: str) -> dict[str, Any] | None:
    return await _try_instances(PIPED_INSTANCES, f"/streams/{video_id}")


async def cookieless_streams(video_id: str) -> dict[str, Any] | None:
    """Return a best-effort ``{title, video, audio, thumb, duration, source}``.

    ``video`` is a muxed (A/V) URL when available, ``audio`` a separate audio
    URL.  Either may be ``None`` if the backend does not expose it.
    """
    inv = await invidious_video(video_id)
    if inv and (inv.get("formatStreams") or inv.get("adaptiveFormats")):
        video = None
        for fmt in inv.get("formatStreams") or []:
            if (fmt.get("container") or "") == "mp4" and fmt.get("url"):
                video = fmt["url"]  # highest muxed mp4 wins
        audio = None
        for fmt in inv.get("adaptiveFormats") or []:
            if (fmt.get("type") or "").startswith("audio") and fmt.get("url"):
                audio = fmt["url"]
                break
        thumbs = inv.get("videoThumbnails") or [{}]
        return {
            "title": inv.get("title"),
            "video": video,
            "audio": audio,
            "thumb": thumbs[0].get("url"),
            "duration": inv.get("lengthSeconds"),
            "source": "invidious",
        }

    piped = await piped_streams(video_id)
    if piped:
        video = None
        for stream in piped.get("videoStreams") or []:
            if stream.get("format") == "MPEG_4" and not stream.get("videoOnly"):
                video = stream.get("url")  # highest muxed mp4 wins
        audio = None
        for stream in piped.get("audioStreams") or []:
            if "mp4" in (stream.get("mimeType") or ""):
                audio = stream.get("url")
                break
        return {
            "title": piped.get("title"),
            "video": video,
            "audio": audio,
            "thumb": piped.get("thumbnailUrl"),
            "duration": piped.get("duration"),
            "source": "piped",
        }
    return None


async def download_yt_fallback(event: Any, video_id: str, kind: str = "video") -> bool:
    """Cookie-less fallback: pull a direct stream URL and ship the file.

    Used when yt-dlp itself fails (rate limits / sign-in walls).  Returns
    ``True`` on success so callers can decide whether to show their own error.
    """
    streams = await cookieless_streams(video_id)
    if not streams:
        await event.edit("`All backends failed. Try again later or configure cookies.`")
        return False
    url = streams["video"] if kind == "video" else streams["audio"]
    if not url:
        await event.edit(f"`{kind} stream unavailable from fallback backend.`")
        return False
    title = streams.get("title") or video_id
    ext = "mp4" if kind == "video" else "m4a"
    fname = f"{video_id}.{ext}"
    await event.edit(f"`Downloading via {streams['source']} (cookie-less)...`")
    try:
        await fns.download_file(url, fname)
    except Exception as ex:
        LOGS.error(f"cookieless download failed: {ex}")
        await event.edit("`Fallback download failed.`")
        return False
    if not os.path.exists(fname):
        await event.edit("`Fallback download failed: file not found.`")
        return False
    try:
        attributes = await fns.set_attributes(fname)
        res, _ = await event.client.fast_uploader(
            fname, show_progress=True, event=event, to_delete=True
        )
        await event.client.send_file(
            event.chat_id,
            file=res,
            caption=f"`{title}`\n\n`via {streams['source']} (cookie-less)`",
            attributes=attributes,
            supports_streaming=True,
        )
    except Exception as ex:
        LOGS.error(f"cookieless upload failed: {ex}")
        await event.edit("`Fallback upload failed.`")
        return False
    finally:
        try:
            os.remove(fname)
        except Exception:
            pass
    try:
        await event.delete()
    except Exception:
        pass
    return True


def get_yt_link(query: str) -> str | None:
    if VideosSearch is None:
        return None
    search = VideosSearch(query, limit=1).result()
    try:
        return search["result"][0]["link"]
    except IndexError:
        return None


async def download_yt(event: Any, link: str, ytd: dict[str, Any]) -> bool:
    reply_to = event.reply_to_msg_id or event
    info = await dler(event, link, ytd, download=True)
    if not info:
        return False

    if info.get("_type", None) == "playlist":
        total = info["playlist_count"]
        for num, file in enumerate(info["entries"]):
            num += 1
            id_ = file["id"]
            thumb = id_ + ".jpg"
            title = file["title"]
            await fns.download_file(
                file.get("thumbnail", None) or file["thumbnails"][-1]["url"], thumb
            )

            # Find the downloaded file
            downloaded_file = None
            for x in glob.glob(f"{id_}*"):
                if not x.endswith(".jpg") and not x.endswith(".part"):
                    downloaded_file = x
                    break
            if not downloaded_file:
                return True

            # Extract the existing extension
            existing_ext = os.path.splitext(downloaded_file)[-1]

            file_renamed = title + existing_ext

            try:
                os.rename(downloaded_file, file_renamed)
            except FileNotFoundError:
                os.rename(downloaded_file + existing_ext, file_renamed)

            if file_renamed.endswith(".part"):
                os.remove(file_renamed)
                os.remove(thumb)
                await event.client.send_message(
                    event.chat_id,
                    f"`[{num}/{total}]` `Invalid Video format.\nIgnoring that...`",
                )
                continue  # Skip to next file in the playlist

            attributes = await fns.set_attributes(file_renamed)
            res, _ = await event.client.fast_uploader(
                file_renamed, show_progress=True, event=event, to_delete=True
            )
            from_ = info["extractor"].split(":")[0]
            caption = f"`[{num}/{total}]` `{title}`\n\n`from {from_}`"
            await event.client.send_file(
                event.chat_id,
                file=res,
                caption=caption,
                attributes=attributes,
                supports_streaming=True,
                thumb=thumb,
                reply_to=reply_to,
            )
            os.remove(thumb)

        try:
            await event.delete()
        except Exception:
            pass
        return True

    # Handle single video download
    vi = VideoInfo.from_info(info)
    title = vi.title
    if len(title) > 20:
        title = title[:17] + "..."

    id_ = vi.id
    thumb = id_ + ".jpg"
    await fns.download_file(
        vi.thumbnail or f"https://i.ytimg.com/vi/{id_}/hqdefault.jpg",
        thumb,
    )

    # Find the downloaded file
    downloaded_file = None
    for x in glob.glob(f"{id_}*"):
        if not x.endswith(".jpg") and not x.endswith(".part"):
            downloaded_file = x
            break
    if not downloaded_file:
        return True

    existing_ext = os.path.splitext(downloaded_file)[-1]

    file_renamed = title + existing_ext

    try:
        os.rename(downloaded_file, file_renamed)
    except FileNotFoundError:
        os.rename(downloaded_file + existing_ext, file_renamed)

    attributes = await fns.set_attributes(file_renamed)
    res, _ = await event.client.fast_uploader(
        file_renamed, show_progress=True, event=event, to_delete=True
    )
    caption = f"`{vi.title}`"
    await event.client.send_file(
        event.chat_id,
        file=res,
        caption=caption,
        attributes=attributes,
        supports_streaming=True,
        thumb=thumb,
        reply_to=reply_to,
    )
    os.remove(thumb)
    try:
        await event.delete()
    except Exception:
        pass
    return True


async def download_playlist(
    event: Any, link: str, opts: dict[str, Any] | None = None
) -> bool:
    if opts is None:
        opts = YtdlConfig.playlist()
    return await download_yt(event, link, opts)


# ---------------YouTube Downloader Inline---------------
# @New-Dev0 @buddhhu @1danish-00 @TrueSaiyan


def get_formats(type: str, id: str, data: dict[str, Any]) -> list[FormatInfo]:
    if type == "audio":
        return [
            FormatInfo(ytid=id, type="audio", id=quality, quality=f"{quality}KBPS")
            for quality in ["64", "128", "256", "320"]
        ]
    if type == "video":
        video: list[FormatInfo] = []
        size = 0
        for vid in (data or {}).get("formats") or []:
            if vid.get("format_id") == "251":
                size += vid.get("filesize") or 0
            if vid.get("vcodec") != "none":
                _id = int(vid["format_id"])
                _quality = f"{vid['width']}×{vid['height']}"
                _size = size + (vid.get("filesize") or 0)
                _ext = "mkv" if vid["ext"] == "webm" else "mp4"
                if _size < 2147483648:  # Telegram's Limit of 2GB
                    video.append(
                        FormatInfo(
                            ytid=id,
                            type="video",
                            id=f"{_id}+251",
                            quality=_quality,
                            size=_size,
                            ext=_ext,
                        )
                    )
        return video
    return []


def get_buttons(listt: list[FormatInfo]) -> Any:
    from custom.tg import ikm

    id = listt[0].ytid

    unique_resolutions: dict[str, FormatInfo] = {}
    for x in listt:
        resolution = x.quality
        size = x.size
        if (
            resolution not in unique_resolutions
            or size > unique_resolutions[resolution].size
        ):
            unique_resolutions[resolution] = x

    butts = [
        (
            f"[{x.quality}" + (f" {fns.humanbytes(x.size)}]" if x.size else "]"),
            f"ytdownload:{x.type}:{x.id}:{x.ytid}" + (f":{x.ext}" if x.ext else ""),
        )
        for x in unique_resolutions.values()
    ]

    buttons = [list(row) for row in zip(butts[::2], butts[1::2])]
    if len(butts) % 2 == 1:
        buttons.append([butts[-1]])

    buttons.append([("« Back", f"ytdl_back:{id}", "danger")])

    return ikm(buttons)


async def dler(
    event: Any,
    url: str,
    opts: dict[str, Any] | None = None,
    download: bool = False,
) -> dict[str, Any] | None:
    if opts is None:
        opts = {}
    # Copy so progress_hooks / credentials never leak into a caller's dict.
    opts = {**opts}
    if YoutubeDL is None:
        await event.edit("`yt-dlp is not installed. Install it to download.`")
        return None
    await event.edit("`Getting Data...`")
    if "quiet" not in opts:
        opts["quiet"] = True
    opts["username"] = udB.get_key("YT_USERNAME")
    opts["password"] = udB.get_key("YT_PASSWORD")
    opts["cookiefile"] = "cookies.txt"

    download_error: BaseException | None = None
    if download:
        progress = _ProgressState()
        opts["progress_hooks"] = [progress.hook]
        updater = asyncio.create_task(_progress_updater(event, progress))
        try:
            await ytdownload(url, opts)
        except Exception as ex:
            LOGS.error(f"ytdl download error: {ex}")
            download_error = ex
        progress.finish()
        await updater

    try:
        return await extract_info(url, opts)
    except Exception as e:
        LOGS.error(f"ytdl extract error: {e}")
        # Prefer the (more specific) download error when the download itself
        # failed; otherwise report the extract error.
        await event.edit(_friendly_ytdl_error(download_error or e))
        return None


@fns.run_async
def ytdownload(url: str, opts: dict[str, Any]) -> Any:
    return YoutubeDL(opts).download([url])


@fns.run_async
def extract_info(url: str, opts: dict[str, Any]) -> Any:
    return YoutubeDL(opts).extract_info(url=url, download=False)


@fns.run_async
def get_videos_link(url: str) -> list[str]:
    to_return: list[str] = []
    if Playlist is None:
        return to_return
    regex = re.search(r"\?list=([(\w+)\-]*)", url)
    if not regex:
        return to_return
    playlist_id = regex.group(1)
    videos = Playlist(playlist_id)
    for vid in videos.videos:
        link = re.search(r"\?v=([(\w+)\-]*)", vid["link"]).group(1)
        to_return.append(f"https://youtube.com/watch?v={link}")
    return to_return

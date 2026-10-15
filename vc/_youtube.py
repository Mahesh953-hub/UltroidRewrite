import asyncio
import glob
import os
import re
from pathlib import Path
from typing import Optional, Union

import yt_dlp
from youtubesearchpython.__future__ import VideosSearch

from ._dataclass import MusicTrack, PlatformTracks, TrackInfo
from ._downloader import MusicService
from ._tools import DOWNLOADS_DIR, LOGGER


def time_to_seconds(time):
    stringt = str(time)
    return sum(int(x) * 60**i for i, x in enumerate(reversed(stringt.split(":"))))


async def shell_cmd(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, errorz = await proc.communicate()
    if errorz:
        if "unavailable videos are hidden" in (errorz.decode("utf-8")).lower():
            return out.decode("utf-8")
        else:
            return errorz.decode("utf-8")
    return out.decode("utf-8")


class YouTube:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.reg = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    async def exists(self, link: str, videoid: bool | str = None):
        if videoid:
            link = self.base + link
        if re.search(self.regex, link):
            return True
        else:
            return False

    async def details(self, link: str, videoid: bool | str = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        if "?si=" in link:
            link = link.split("?si=")[0]

        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result["duration"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            vidid = result["id"]
            if str(duration_min) == "None":
                duration_sec = 0
            else:
                duration_sec = int(time_to_seconds(duration_min))
        return title, duration_min, duration_sec, thumbnail, vidid

    async def title(self, link: str, videoid: bool | str = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        if "?si=" in link:
            link = link.split("?si=")[0]

        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
        return title

    async def duration(self, link: str, videoid: bool | str = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        if "?si=" in link:
            link = link.split("?si=")[0]

        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            duration = result["duration"]
        return duration

    async def thumbnail(self, link: str, videoid: bool | str = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        if "?si=" in link:
            link = link.split("?si=")[0]

        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
        return thumbnail

    async def video(self, link: str, videoid: bool | str = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        if "?si=" in link:
            link = link.split("?si=")[0]
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "-g",
            "-f",
            "best[height<=?720][width<=?1280]",
            f"{link}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout:
            return 1, stdout.decode().split("\n")[0]
        else:
            return 0, stderr.decode()

    async def playlist(self, link, limit, user_id, videoid: bool | str = None):
        if videoid:
            link = self.listbase + link
        if "&" in link:
            link = link.split("&")[0]
        if "?si=" in link:
            link = link.split("?si=")[0]
        playlist = await shell_cmd(
            f"yt-dlp -i --get-id --flat-playlist --playlist-end {limit} --skip-download {link}"
        )
        try:
            result = playlist.split("\n")
            for key in result:
                if key == "":
                    result.remove(key)
        except:
            result = []
        return result

    async def track(self, link: str, videoid: bool | str = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        if "?si=" in link:
            link = link.split("?si=")[0]

        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result["duration"]
            vidid = result["id"]
            yturl = result["link"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
        track_details = {
            "title": title,
            "link": yturl,
            "vidid": vidid,
            "duration_min": duration_min,
            "thumb": thumbnail,
        }
        return track_details, vidid

    async def formats(self, link: str, videoid: bool | str = None):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        if "?si=" in link:
            link = link.split("?si=")[0]
        ytdl_opts = {"quiet": True, "cookiefile": None}
        ydl = yt_dlp.YoutubeDL(ytdl_opts)
        with ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for format in r["formats"]:
                try:
                    str(format["format"])
                except:
                    continue
                if not "dash" in str(format["format"]).lower():
                    try:
                        format["format"]
                        format["filesize"]
                        format["format_id"]
                        format["ext"]
                        format["format_note"]
                    except:
                        continue
                    formats_available.append(
                        {
                            "format": format["format"],
                            "filesize": format["filesize"],
                            "format_id": format["format_id"],
                            "ext": format["ext"],
                            "format_note": format["format_note"],
                            "yturl": link,
                        }
                    )
        return formats_available, link

    async def slider(
        self,
        link: str,
        query_type: int,
        videoid: bool | str = None,
    ):
        if videoid:
            link = self.base + link
        if "&" in link:
            link = link.split("&")[0]
        if "?si=" in link:
            link = link.split("?si=")[0]

        a = VideosSearch(link, limit=10)
        result = (await a.next()).get("result")
        title = result[query_type]["title"]
        duration_min = result[query_type]["duration"]
        vidid = result[query_type]["id"]
        thumbnail = result[query_type]["thumbnails"][0]["url"].split("?")[0]

        return title, duration_min, thumbnail, vidid

    async def download(
        self,
        link: str,
        video: bool | str = None,
        videoid: bool | str = None,
        songaudio: bool | str = None,
        songvideo: bool | str = None,
        format_id: bool | str = None,
        title: bool | str = None,
    ) -> str:
        if videoid:
            link = self.base + link
        loop = asyncio.get_running_loop()

        def audio_dl():
            ydl_optssx = {
                "format": "bestaudio/best",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "cookiefile": None,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                return xyz
            x.download([link])
            return xyz

        def video_dl():
            ydl_optssx = {
                "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "cookiefile": None,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                return xyz
            x.download([link])
            return xyz

        def song_video_dl():
            formats = f"{format_id}+140"
            fpath = f"downloads/{title}"
            ydl_optssx = {
                "format": formats,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "prefer_ffmpeg": True,
                "merge_output_format": "mp4",
                "cookiefile": None,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        def song_audio_dl():
            fpath = f"downloads/{title}.%(ext)s"
            ydl_optssx = {
                "format": format_id,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "prefer_ffmpeg": True,
                "cookiefile": None,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        if songvideo:
            await loop.run_in_executor(None, song_video_dl)
            fpath = f"downloads/{title}.mp4"
            return fpath
        elif songaudio:
            await loop.run_in_executor(None, song_audio_dl)
            fpath = f"downloads/{title}.mp3"
            return fpath
        elif video:
            # if await is_on_off(1):
            # direct = True
            # downloaded_file = await loop.run_in_executor(None, video_dl)
            # else:
            proc = await asyncio.create_subprocess_exec(
                "yt-dlp",
                "-g",
                "-f",
                "best[height<=?720][width<=?1280]",
                f"{link}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if stdout:
                downloaded_file = stdout.decode().split("\n")[0]
                direct = None
            else:
                return
        else:
            direct = True
            downloaded_file = await loop.run_in_executor(None, audio_dl)
        return downloaded_file, direct


# ──────────────────────────────────────────────────────────────────────────
# YouTubeData — MusicService-conformant YouTube provider (W1-A)
# ──────────────────────────────────────────────────────────────────────────
# vc/_pytgcalls.py and vc/_downloader.py import YouTubeData, but this module
# historically only defined the low-level ``YouTube`` helper — a hard import
# error that kept the whole vc tree from loading. This class is the real
# provider: same query/URL contract as ApiData/JiosaavnData.

_YT_WATCH_BASE = "https://www.youtube.com/watch?v="
_YT_URL_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?.*v=|shorts/)|youtu\.be/)([\w-]{11})",
    re.IGNORECASE,
)
_YT_ID_RE = re.compile(r"^[\w-]{11}$")


def _duration_to_seconds(value) -> int:
    """Parse a 'H:MM:SS'/'MM:SS' duration string; 0 for None/unparseable."""
    if not value:
        return 0
    try:
        return time_to_seconds(value)
    except (TypeError, ValueError):
        return 0


class YouTubeData(MusicService):
    """YouTube music service implementing the :class:`MusicService` interface.

    ``query`` may be a watch/share URL, a bare 11-char video id, or a free
    text search. ``Call.song_download`` instantiates this with a video id and
    calls :meth:`get_track` / :meth:`download_track`;
    :class:`MusicServiceWrapper` instantiates it with no argument (``is_valid``
    probe) or with the raw user query.
    """

    def __init__(self, query: str | None = None):
        self.query = (query or "").strip()

    # ── helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _watch_url(video_id: str) -> str:
        return f"{_YT_WATCH_BASE}{video_id}"

    @staticmethod
    def _extract_id(query: str) -> str | None:
        match = _YT_URL_RE.search(query or "")
        if match:
            return match.group(1)
        if query and _YT_ID_RE.match(query):
            return query
        return None

    @staticmethod
    def _row_to_track(row: dict) -> MusicTrack:
        thumbnails = row.get("thumbnails") or [{}]
        return MusicTrack(
            url=row.get("link", ""),
            name=row.get("title", "Unknown"),
            artist=(row.get("channel") or {}).get("name", "") or "YouTube",
            id=str(row.get("id", "")),
            year=0,
            cover=str(thumbnails[0].get("url", "")).split("?")[0],
            duration=_duration_to_seconds(row.get("duration")),
            platform="youtube",
        )

    async def _search_rows(self, query: str, limit: int) -> list:
        try:
            results = VideosSearch(query, limit=limit)
            return (await results.next()).get("result", []) or []
        except Exception as e:
            LOGGER.error("YouTube search failed for %r: %s", query, e)
            return []

    #: Last extraction failure, kept so callers can classify it instead of
    #: seeing a bare ``None``. Populated by :meth:`_ydl_extract`; cleared on
    #: success. ``.vplay`` reads this to tell a TLS interception apart from a
    #: genuinely empty result - both used to surface as "No results", which
    #: told the user to try a different query when the real problem was the
    #: network.
    last_extract_error: str = ""

    @staticmethod
    def _ydl_extract(url: str) -> dict | None:
        opts = {
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "noplaylist": True,
            "socket_timeout": 20,
        }
        # Cookieless by contract - see vc/_resolver.py for the client policy.
        opts["cookiefile"] = None
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as e:
            LOGGER.error("yt-dlp metadata extraction failed for %r: %s", url, e)
            # Record rather than discard: the caller needs the reason to
            # produce a useful diagnosis.
            YouTubeData.last_extract_error = f"{type(e).__name__}: {e}"
            return None
        YouTubeData.last_extract_error = ""
        return info

    # ── MusicService interface ────────────────────────────────────────────
    def is_valid(self, url: str | None) -> bool:
        """True when *url* is a YouTube watch/share/shorts URL."""
        return bool(url) and bool(_YT_URL_RE.search(url))

    async def get_info(self) -> PlatformTracks | None:
        """Resolve a YouTube URL query to a single-track PlatformTracks."""
        if not self.query or not self.is_valid(self.query):
            return None
        rows = await self._search_rows(self.query, limit=1)
        if not rows:
            return None
        return PlatformTracks(tracks=[self._row_to_track(rows[0])])

    async def search(self) -> PlatformTracks | None:
        """Search YouTube for the query (URLs resolve via get_info)."""
        if not self.query:
            return None
        if self.is_valid(self.query):
            return await self.get_info()
        rows = await self._search_rows(self.query, limit=10)
        tracks = [self._row_to_track(row) for row in rows if row.get("id")]
        return PlatformTracks(tracks=tracks) if tracks else None

    async def get_recommendations(self) -> PlatformTracks | None:
        """YouTube has no recommendations endpoint here; API-backed services
        (ApiData) own recommendation duty. Always None by design."""
        return None

    async def get_track(self) -> TrackInfo | None:
        """Resolve the query (URL / video id / search text) to a TrackInfo."""
        if not self.query:
            return None
        video_id = self._extract_id(self.query)
        if video_id is None:
            rows = await self._search_rows(self.query, limit=1)
            if not rows or not rows[0].get("id"):
                return None
            video_id = str(rows[0]["id"])

        loop = asyncio.get_running_loop()
        info = await loop.run_in_executor(
            None, self._ydl_extract, self._watch_url(video_id)
        )
        if not info:
            return None
        return TrackInfo(
            url=self._watch_url(info.get("id") or video_id),
            cdnurl="",  # YouTube downloads via yt-dlp, not a CDN URL
            key="",  # no decryption key needed (unlike Spotify)
            name=info.get("title") or "Unknown",
            artist=info.get("uploader") or info.get("channel") or "YouTube",
            album="",
            tc=str(info.get("id") or video_id),
            cover=info.get("thumbnail") or "",
            lyrics="",
            duration=int(info.get("duration") or 0),
            year=0,
            platform="youtube",
        )

    async def download_track(
        self, track: TrackInfo, video: bool = False
    ) -> Path | None:
        """Download a track with yt-dlp into DOWNLOADS_DIR.

        Returns the downloaded file path, or None on failure. Existing files
        are re-used (keyed by the 11-char video id in ``track.tc``).
        """
        if not track or not track.tc:
            return None
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        url = track.url or self._watch_url(track.tc)

        existing = sorted(glob.glob(os.path.join(DOWNLOADS_DIR, f"{track.tc}.*")))
        if existing:
            return Path(existing[0])

        opts = {
            "format": (
                "(bestvideo[height<=?720][ext=mp4]+bestaudio[ext=m4a])/best"
                if video
                else "bestaudio/best"
            ),
            "outtmpl": os.path.join(DOWNLOADS_DIR, f"{track.tc}.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "nocheckcertificate": True,
            "geo_bypass": True,
            "noplaylist": True,
            "socket_timeout": 30,
        }
        if video:
            opts["merge_output_format"] = "mp4"
        # Cookieless by contract - see vc/_resolver.py for the client policy.
        opts["cookiefile"] = None

        def _dl() -> None:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, _dl)
        except Exception as e:
            LOGGER.error("YouTube download failed for %s: %s", track.tc, e)
            return None

        matches = sorted(glob.glob(os.path.join(DOWNLOADS_DIR, f"{track.tc}.*")))
        return Path(matches[0]) if matches else None

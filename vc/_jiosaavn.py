#  Copyright (c) 2025 AshokShau
#  Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
#  Part of the TgMusicBot project. All rights reserved where applicable.

"""JioSaavn music service.

The low-level :class:`JioSaavn` helper talks to the JioSaavn API and was moved
here from ``_saavn.py``. :class:`JiosaavnData` wraps it and implements the
:class:`MusicService` interface used by :class:`MusicServiceWrapper`.
"""

import asyncio
import os
import re
from typing import Any, Dict, List, Optional

import aiofiles
import httpx
from mutagen.mp4 import MP4, MP4Cover

from ._dataclass import MusicTrack, PlatformTracks, TrackInfo
from ._downloader import MusicService
from ._tools import DOWNLOADS_DIR, LOGGER

_BASE_URL = "https://jiosaavn-api-privatecvc2.vercel.app"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}
_SAAVN_URL_RE = re.compile(r"(jiosaavn\.com|saavn\.com)", re.IGNORECASE)


def _last_link(items: list[dict[str, Any]] | None) -> str:
    """Return the ``link`` of the last item in a list (``""`` if unavailable)."""
    if not items:
        return ""
    try:
        return items[-1].get("link", "") or ""
    except (AttributeError, IndexError):
        return ""


class JioSaavn:
    """Low-level JioSaavn API helper."""

    def __init__(self):
        self.base_url = _BASE_URL
        self.headers = _HEADERS
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    async def search_songs(
        self, query: str, limit: int = 1, page: int = 1
    ) -> list[dict[str, Any]]:
        """Search for songs and return the results list."""
        params = {"query": query, "limit": limit, "page": page}
        async with httpx.AsyncClient(headers=self.headers, timeout=20.0) as client:
            response = await client.get(f"{self.base_url}/search/songs", params=params)
            response.raise_for_status()
            data = response.json()
            return data.get("data", {}).get("results", [])

    async def _download_bytes(self, url: str) -> bytes:
        """Helper to download raw bytes (for audio and images)."""
        async with httpx.AsyncClient(headers=self.headers, timeout=60.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.content

    def _apply_metadata(
        self, file_path: str, song_data: dict[str, Any], image_data: bytes
    ):
        """Internal method to fix metadata using Mutagen."""
        audio = MP4(file_path)

        # Mapping tags for MP4/M4A container
        audio["\xa9nam"] = song_data.get("name", "Unknown")
        audio["\xa9ART"] = song_data.get("primaryArtists", "Unknown")
        audio["\xa9alb"] = song_data.get("album", {}).get("name", "Unknown")
        audio["\xa9day"] = song_data.get("year", "Unknown")
        audio["cprt"] = song_data.get("copyright", "• @AgainOwner")

        # Add Album Art
        audio["covr"] = [MP4Cover(image_data, imageformat=MP4Cover.FORMAT_JPEG)]

        audio.save()

    async def download_song(
        self, song_data: dict[str, Any], output_folder: str = DOWNLOADS_DIR
    ):
        """Downloads the highest quality version and applies metadata."""

        download_url = song_data["downloadUrl"][-1]["link"]
        image_url = song_data["image"][-1]["link"]
        file_name = f"{song_data['name']} - {song_data['primaryArtists']}.m4a".replace(
            "/", "-"
        )
        file_path = os.path.join(output_folder, file_name)

        LOGGER.info(f"[JioSavn] Downloading: {song_data['name']}...")

        audio_task = self._download_bytes(download_url)
        image_task = self._download_bytes(image_url)
        audio_bytes, image_bytes = await asyncio.gather(audio_task, image_task)

        async with aiofiles.open(file_path, mode="wb") as f:
            await f.write(audio_bytes)

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, self._apply_metadata, file_path, song_data, image_bytes
        )

        LOGGER.info(f"[JioSavn] Saved and Tagged: {file_path}")
        return file_path


class JiosaavnData(MusicService):
    """JioSaavn service implementing the :class:`MusicService` interface."""

    def __init__(self, query: str | None = None):
        self.query = query or ""
        self._saavn = JioSaavn()

    def is_valid(self, url: str | None) -> bool:
        if not url:
            return False
        return bool(_SAAVN_URL_RE.search(url))

    @staticmethod
    def _album_name(song: dict[str, Any]) -> str:
        album = song.get("album")
        if isinstance(album, dict):
            return album.get("name", "Unknown")
        if isinstance(album, str) and album:
            return album
        return "Unknown"

    def _song_to_track(self, song: dict[str, Any]) -> MusicTrack:
        return MusicTrack(
            url=song.get("url", ""),
            name=song.get("name", "Unknown"),
            artist=song.get("primaryArtists", "Unknown"),
            id=str(song.get("id", "")),
            year=int(song.get("year") or 0),
            cover=_last_link(song.get("image")),
            duration=int(song.get("duration") or 0),
            platform="jiosaavn",
        )

    async def get_info(self) -> PlatformTracks | None:
        if not self.query or not self.is_valid(self.query):
            return None
        try:
            results = await self._saavn.search_songs(self.query, limit=1)
            if not results:
                return None
            tracks = [self._song_to_track(s) for s in results]
            return PlatformTracks(tracks=tracks) if tracks else None
        except Exception as e:
            LOGGER.error("JioSaavn get_info failed: %s", e)
            return None

    async def search(self) -> PlatformTracks | None:
        if not self.query:
            return None
        if self.is_valid(self.query):
            return await self.get_info()
        try:
            results = await self._saavn.search_songs(self.query, limit=5)
            if not results:
                return None
            tracks = [self._song_to_track(s) for s in results]
            return PlatformTracks(tracks=tracks) if tracks else None
        except Exception as e:
            LOGGER.error("JioSaavn search failed: %s", e)
            return None

    async def get_recommendations(self) -> PlatformTracks | None:
        # The JioSaavn API used here exposes no recommendations endpoint.
        return None

    async def get_track(self) -> TrackInfo | None:
        if not self.query:
            return None
        try:
            results = await self._saavn.search_songs(self.query, limit=1)
            if not results:
                return None
            song = results[0]
            return TrackInfo(
                url=song.get("url", ""),
                cdnurl=_last_link(song.get("downloadUrl")),
                key="",
                name=song.get("name", "Unknown"),
                artist=song.get("primaryArtists", "Unknown"),
                album=self._album_name(song),
                tc=str(song.get("id", "")),
                cover=_last_link(song.get("image")),
                lyrics="",
                duration=int(song.get("duration") or 0),
                year=int(song.get("year") or 0),
                platform="jiosaavn",
            )
        except Exception as e:
            LOGGER.error("JioSaavn get_track failed: %s", e)
            return None

    async def download_track(
        self, track_info: TrackInfo, video: bool = False
    ) -> str | None:
        if not track_info:
            return None
        try:
            # Reconstruct the song payload from TrackInfo so the helper can
            # stream-download the audio and tag it without an extra API call.
            song_data = {
                "name": track_info.name,
                "primaryArtists": track_info.artist,
                "album": {"name": track_info.album},
                "year": str(track_info.year),
                "copyright": "",
                "downloadUrl": [{"link": track_info.cdnurl}],
                "image": [{"link": track_info.cover}],
            }
            return await self._saavn.download_song(song_data)
        except Exception as e:
            LOGGER.error("JioSaavn download_track failed: %s", e)
            return None

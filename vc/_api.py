#  Copyright (c) 2025 AshokShau
#  Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
#  Part of the TgMusicBot project. All rights reserved where applicable.

import re
from pathlib import Path
from typing import Optional, Union


from ._tools import LOGGER, DOWNLOADS_DIR, API_URL, API_KEY
from ._dataclass import MusicTrack, PlatformTracks, TrackInfo
from ._dl_helper import SpotifyDownload
from ._downloader import MusicService
from ._httpx import HttpxClient


class ApiData(MusicService):
    """Handles music data from various streaming platforms through API integration."""

    # URL patterns for supported music services
    URL_PATTERNS = {
        "apple_music": re.compile(
            r"^(https?://)?(music\.apple\.com/([a-z]{2}/)?(album|playlist|song)/[a-zA-Z0-9\-_]+/[0-9]+)(\?.*)?$",
            re.IGNORECASE,
        ),
        "spotify": re.compile(
            r"^(https?://)?(open\.spotify\.com/(track|playlist|album|artist)/[a-zA-Z0-9]+)(\?.*)?$",
            re.IGNORECASE,
        ),
        "soundcloud": re.compile(
            r"^(https?://)?(soundcloud\.com/[a-zA-Z0-9\-_]+/[a-zA-Z0-9\-_]+)(\?.*)?$",
            re.IGNORECASE,
        ),
    }

    def __init__(self, query: str | None = None) -> None:
        """
        Initialize ApiData with an optional query.

        Args:
            query: URL or search query to process
        """
        self.query = self._sanitize_query(query) if query else None
        self.client = HttpxClient()
        self.api_url = API_URL.rstrip("/") if API_URL else None
        self.api_key = API_KEY

    @staticmethod
    def _sanitize_query(query: str) -> str:
        """Clean and normalize the input query."""
        return query.strip().split("?")[0].split("#")[0]

    def is_valid(self, url: str | None) -> bool:
        """
        Check if the URL is from a supported music service.

        Args:
            url: The URL to validate

        Returns:
            bool: True if URL is valid, False otherwise
        """
        if not url or not self.api_url or not self.api_key:
            return False

        return any(pattern.match(url) for pattern in self.URL_PATTERNS.values())

    async def _make_api_request(
        self, endpoint: str, params: dict | None = None
    ) -> dict | None:
        """
        Make authenticated API requests with proper error handling.

        Args:
            endpoint: API endpoint to call
            params: Optional query parameters

        Returns:
            dict: API response or None if failed
        """
        if not self.api_url or not self.api_key:
            LOGGER.error("API configuration missing")
            return None

        url = f"{self.api_url}/{endpoint.lstrip('/')}"
        return await self.client.make_request(url, params=params)

    async def get_recommendations(self, limit: int = 4) -> PlatformTracks | None:
        """
        Get recommended tracks.

        Args:
            limit: Number of recommendations to fetch

        Returns:
            PlatformTracks: Contains recommended tracks or None if failed
        """
        data = await self._make_api_request("recommend_songs", {"lim": limit})
        return self._parse_tracks_response(data) if data else None

    async def get_info(self) -> PlatformTracks | None:
        """
        Get track information from a URL.

        Returns:
            PlatformTracks: Contains track info or None if failed
        """
        if not self.query or not self.is_valid(self.query):
            return None

        data = await self._make_api_request("get_url", {"url": self.query})
        return self._parse_tracks_response(data) if data else None

    async def search(self) -> PlatformTracks | None:
        """
        Search for tracks across platforms.

        Returns:
            PlatformTracks: Contains search results or None if failed
        """
        if not self.query:
            return None

        # If query is a URL, get info instead of searching
        if self.is_valid(self.query):
            return await self.get_info()

        data = await self._make_api_request("search_track", {"q": self.query})
        return self._parse_tracks_response(data) if data else None

    async def get_track(self) -> TrackInfo | None:
        """
        Get detailed information about a specific track.

        Returns:
            TrackInfo: Detailed track information or None if failed
        """
        if not self.query:
            return None

        data = await self._make_api_request("get_track", {"id": self.query})
        return TrackInfo(**data) if data else None

    async def download_track(
        self, track: TrackInfo, video: bool = False
    ) -> str | Path | None:
        """
        Download a track based on its platform.

        Args:
            track: TrackInfo object containing track details
            video: Whether to download video (currently unused for API tracks)

        Returns:
            Path/str: Path to a downloaded file or None if failed
        """
        if not track:
            return None

        try:
            if track.platform.lower() == "spotify":
                return await SpotifyDownload(track).process()

            if not track.cdnurl:
                LOGGER.error("No download URL available for track %s", track.tc)
                return None

            download_path = Path(DOWNLOADS_DIR) / f"{track.tc}.mp3"
            result = await self.client.download_file(track.cdnurl, download_path)

            if not result.success:
                LOGGER.error("Download failed for track %s", track.tc)
                return None

            return result.file_path

        except Exception as e:
            LOGGER.error(
                "Error downloading track %s: %s",
                getattr(track, "tc", "unknown"),
                str(e),
                exc_info=True,
            )
            return None

    @staticmethod
    def _parse_tracks_response(data: dict) -> PlatformTracks | None:
        """
        Parse API response into PlatformTracks object.

        Args:
            data: API response data

        Returns:
            PlatformTracks: Contains parsed tracks or None if invalid
        """
        if not data or not isinstance(data, dict) or "results" not in data:
            return None

        valid_tracks = [
            MusicTrack(**track)
            for track in data["results"]
            if track and isinstance(track, dict)
        ]
        return PlatformTracks(tracks=valid_tracks) if valid_tracks else None

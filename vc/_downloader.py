#  Copyright (c) 2025 AshokShau
#  Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
#  Part of the TgMusicBot project. All rights reserved where applicable.

from abc import ABC, abstractmethod
from typing import Optional

try:
    from pyUltroid import udB
except ImportError:  # pragma: no cover - pyUltroid is the runtime dependency
    udB = None

try:
    from pyUltroid import Var
except ImportError:  # pragma: no cover - Var is only set when run as a module
    Var = None

from ._tools import API_KEY, API_URL, LOGGER
from ._dataclass import PlatformTracks, TrackInfo

# Default music service: prefer the database, then Var/env, else "youtube".
DEFAULT_SERVICE = (
    (udB.get_key("DEFAULT_SERVICE") if udB else None)
    or (getattr(Var, "DEFAULT_SERVICE", None) if Var else None)
    or "youtube"
)


class MusicService(ABC):
    @abstractmethod
    def is_valid(self, url: str) -> bool:
        """
        Determine the validity of a given URL for the music service.

        Args:
            url (str): The URL to be checked for validity.

        Returns:
            bool: True if the URL is valid for the music service, False otherwise.
        """
        pass

    @abstractmethod
    async def get_info(self) -> PlatformTracks | None:
        """
        Get music track information from a given URL.

        Returns:
            Optional[PlatformTracks]: Track information or None if failed
        """
        pass

    @abstractmethod
    async def search(self) -> PlatformTracks | None:
        """
        Search for tracks on the music service.

        Returns:
            Optional[PlatformTracks]: Search results or None if failed
        """
        pass

    @abstractmethod
    async def get_recommendations(self) -> PlatformTracks | None:
        """
        Get recommended tracks from the music service.

        Returns:
            Optional[PlatformTracks]: Recommended tracks or None if failed
        """
        pass

    @abstractmethod
    async def get_track(self) -> TrackInfo | None:
        """
        Retrieve detailed information about a specific track.

        Returns:
            Optional[TrackInfo]: Track information if successful, None otherwise.
        """
        pass

    @abstractmethod
    async def download_track(
        self, track_info: TrackInfo, video: bool = False
    ) -> str | None:
        """
        Download a track from the music service.

        Args:
            track_info: Track information containing the track ID or URL to download.
            video: Whether to download video or audio (default: False)

        Returns:
            Optional[str]: Path to the downloaded file if successful, or None if download fails.
        """
        pass


class MusicServiceWrapper(MusicService):
    def __init__(self, query: str = ""):
        """
        Initialize a MusicServiceWrapper object.

        Args:
            query: The track or playlist URL/query to use for downloading or retrieving information.
        """
        self.query = query
        self.service = self._get_service()

    def _get_service(self) -> MusicService:
        """
        Determine and return the appropriate music service handler based on the query.

        This method checks the validity of the query against different music services
        (YouTube, JioSaavn, and various API-powered services) and returns an instance
        of the corresponding service handler. If the query is not directly valid for
        any service,
        It falls back to the default service specified in the configuration.

        Returns:
            MusicService: An instance of a class implementing the MusicService interface,
            corresponding to the service identified for the query.
        """
        from ._api import ApiData
        from ._jiosaavn import JiosaavnData
        from ._youtube import YouTubeData

        query = self.query
        if YouTubeData().is_valid(query):
            return YouTubeData(query)
        elif JiosaavnData().is_valid(query):
            return JiosaavnData(query)
        elif ApiData().is_valid(query):
            return ApiData(query)
        elif DEFAULT_SERVICE == "youtube":
            return YouTubeData(query)
        elif DEFAULT_SERVICE == "spotify":
            return ApiData(query)
        elif DEFAULT_SERVICE == "jiosaavn":
            return JiosaavnData(query)
        return ApiData(query) if API_URL and API_KEY else YouTubeData(query)

    def is_valid(self, url: str) -> bool:
        return self.service.is_valid(url)

    async def get_info(self) -> PlatformTracks | None:
        return await self.service.get_info()

    async def search(self) -> PlatformTracks | None:
        return await self.service.search()

    async def get_recommendations(self) -> PlatformTracks | None:
        return await self.service.get_recommendations()

    async def get_track(self) -> TrackInfo | None:
        return await self.service.get_track()

    async def download_track(
        self, track_info: TrackInfo, video: bool = False
    ) -> str | None:
        return await self.service.download_track(track_info)

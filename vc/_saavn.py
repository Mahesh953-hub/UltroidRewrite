import os
from datetime import timedelta

import aiohttp
import aiofiles

from ._tools import LOGGER
from pyUltroid.fns import fns


def _inline_mention(user):
    """Lazily resolve ``inline_mention`` from the plugins package.

    A top-level ``from plugins import inline_mention`` makes importing the
    ``vc`` package pull in the entire plugins/pyUltroid runtime chain, which
    breaks standalone/credential-free imports of vc.* (e.g. tests). The
    plugins package is fully initialized by the time any MeloAPI method runs,
    so the import stays safe at call time. Falls back to the user's
    ``first_name`` when available, else a plain string.
    """
    try:
        from plugins import inline_mention

        return fns.inline_mention(user)
    except Exception:
        return getattr(user, "first_name", None) or str(user)


MELO_API_BASE = "https://meloapi.vercel.app"
TEMP_DIR = "./temp/"


def cleanup_file(path: str):
    """Safely deletes a file if it exists."""
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError as e:
            LOGGER.warning("Error cleaning up file %s: %s", path, e)


class MeloAPI:
    """Handles interaction with the meloapi.vercel.app API."""

    def __init__(self):
        os.makedirs(TEMP_DIR, exist_ok=True)

    async def _get_best_image_url(self, image_list: list) -> str:
        """Finds the URL for the highest quality image."""
        if not image_list:
            return "https://storage.googleapis.com/hardstone_img_us/umagic_crop/upload/2024-11-17/5f3acc86-e0bb-40ae-8c56-468a9a83a1a1.jpg"
        for image in image_list:
            if image.get("quality") == "500x500":
                return image.get("url")
        return image_list[0].get("url")

    async def download_file(self, url: str, filename: str) -> str or None:
        """Asynchronously downloads a file from a URL."""
        file_path = os.path.join(TEMP_DIR, filename)
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(url) as response:
                    response.raise_for_status()
                    async with aiofiles.open(file_path, "wb") as f:
                        while True:
                            chunk = await response.content.read(8192)
                            if not chunk:
                                break
                            await f.write(chunk)
                return file_path
            except aiohttp.ClientError as e:
                LOGGER.error("Failed to download %s: %s", url, e)
                return None

    async def prepare_song(
        self,
        requester,
        query: str,
    ):
        """Searches, fetches details, and downloads the song, returning a dictionary."""
        try:
            search_url = f"{MELO_API_BASE}/api/search?query={query}"
            async with aiohttp.ClientSession() as session:
                async with session.get(search_url) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    if not data.get("success") or not data.get("data", {}).get(
                        "songs", {}
                    ).get("results"):
                        return {"error": "No results found."}
                    song_data = data["data"]["songs"]["results"][0]

            song_id = song_data.get("id")
            if not song_id:
                return {"error": "Could not identify the song."}

            details_url = f"{MELO_API_BASE}/api/songs/{song_id}?lyrics=null"
            download_url = None
            async with aiohttp.ClientSession() as session:
                async with session.get(details_url) as resp:
                    resp.raise_for_status()
                    details_data = await resp.json()
                    if (
                        details_data.get("success")
                        and "data" in details_data
                        and details_data["data"]
                    ):
                        song_info = details_data["data"][0]
                        if "downloadUrl" in song_info and song_info["downloadUrl"]:
                            download_url = song_info["downloadUrl"][-1].get("url")

            if not download_url:
                return {"error": "Could not find a download link."}

            song_title = song_data.get("title", "Unknown Title").replace("/", "-")
            audio_path = await self.download_file(download_url, f"{song_id}.mp3")
            if not audio_path:
                return {"error": "Failed to download the song."}

            return {
                "id": song_id,
                "title": song_title,
                "artist": song_data.get("primaryArtists", "Unknown Artist"),
                "description": song_data.get("description"),
                "image_url": await self._get_best_image_url(song_data.get("image", [])),
                "duration": str(timedelta(seconds=int(song_data.get("duration", 0)))),
                "path": audio_path,
                "requester": _inline_mention(requester),
                "error": None,
            }
        except aiohttp.ClientError as e:
            return {"error": f"API request failed: {e}"}
        except Exception as e:
            return {"error": f"An unexpected error occurred: {e}"}

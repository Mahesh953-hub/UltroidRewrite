import os
import re
import base64

import httpx
from mutagen.mp3 import MP3
from mutagen.id3 import ID3, APIC, TIT2, TPE1, TALB

try:
    from pyUltroid import Var
except ImportError:  # pragma: no cover - Var is only set when run as a module
    Var = None

# Spotify credentials are sourced from pyUltroid.Var or environment variables
# instead of being hardcoded.
SPOTIFY_CLIENT_ID = (
    getattr(Var, "SPOTIFY_CLIENT_ID", None) or os.environ.get("SPOTIFY_CLIENT_ID") or ""
)
SPOTIFY_CLIENT_SECRET = (
    getattr(Var, "SPOTIFY_CLIENT_SECRET", None)
    or os.environ.get("SPOTIFY_CLIENT_SECRET")
    or ""
)
BASE_MATE = "https://spotmate.online"
TEMP_DIR = "./temp"


class Spotify:
    """
    A spotify class.
    """

    def __init__(self):
        os.makedirs(TEMP_DIR, exist_ok=True)

    async def get_spotify_token(self):
        """Fetches a Spotify API access token."""
        try:
            auth_url = "https://accounts.spotify.com/api/token"
            creds = base64.b64encode(
                f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()
            ).decode()
            headers = {
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            }
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    auth_url,
                    headers=headers,
                    data={"grant_type": "client_credentials"},
                )
                res.raise_for_status()
                return res.json().get("access_token")
        except (httpx.HTTPError, KeyError):
            return None

    async def get_track_id(self, query, token):
        """Gets Spotify track ID from a search query or URL."""
        match = re.search(r"track/([a-zA-Z0-9]+)", query)
        if match:
            return match.group(1)

        # If not a URL, search for the track and return the first result
        try:
            search_url = (
                f"https://api.spotify.com/v1/search?q={query}&type=track&limit=1"
            )
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    search_url, headers={"Authorization": f"Bearer {token}"}
                )
                res.raise_for_status()
                items = res.json()["tracks"]["items"]
                return items[0]["id"] if items else None
        except (httpx.HTTPError, KeyError, IndexError):
            return None

    async def get_track_details(self, song_id, token):
        """Gets detailed metadata for a given Spotify track ID."""
        try:
            details_url = f"https://api.spotify.com/v1/tracks/{song_id}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(
                    details_url, headers={"Authorization": f"Bearer {token}"}
                )
                res.raise_for_status()
                return res.json()
        except (httpx.HTTPError, KeyError):
            return None

    def add_mp3_metadata(self, file_path, details, cover_path):
        """Adds ID3 tags (title, artist, album, cover art) to the MP3 file."""
        try:
            audio = MP3(file_path, ID3=ID3)
            if audio.tags is None:
                audio.add_tags()

            audio.tags.add(TIT2(encoding=3, text=details["name"]))
            audio.tags.add(
                TPE1(
                    encoding=3,
                    text=", ".join(artist["name"] for artist in details["artists"]),
                )
            )
            audio.tags.add(TALB(encoding=3, text=details["album"]["name"]))

            with open(cover_path, "rb") as art:
                audio.tags.add(
                    APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="Cover",
                        data=art.read(),
                    )
                )
            audio.save()
        except Exception:
            # If metadata fails, we can still send the song.
            pass

    def cleanup_files(self, *files):
        """Deletes temporary files after they've been used."""
        for file in files:
            if file and os.path.exists(file):
                os.remove(file)

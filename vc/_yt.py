import os
import httpx
import asyncio
import aiofiles
import json
from typing import List, Dict, Any, Optional

TEMP_DIR = "./temp"


class YouTubeAPI:
    def __init__(self):
        self.base_url = "https://honox-five.vercel.app"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    async def search(self, query: str) -> list[dict[str, Any]]:
        """
        Searches for videos based on a query.
        Returns a list of results containing title, id, duration, etc.
        """
        async with httpx.AsyncClient(headers=self.headers, timeout=20.0) as client:
            response = await client.get(f"{self.base_url}/search", params={"q": query})
            response.raise_for_status()
            data = response.json()
            return data.get("results", [])

    async def get_download_info(self, video_id: str) -> dict[str, Any]:
        """
        Retrieves the download metadata and direct URL for a specific video ID.
        """
        async with httpx.AsyncClient(headers=self.headers, timeout=20.0) as client:
            response = await client.get(
                f"{self.base_url}/download", params={"id": video_id}
            )
            response.raise_for_status()
            return response.json()

    async def download(
        self, download_url: str, filename: str, output_folder: str = TEMP_DIR
    ) -> str:
        """
        Downloads the audio file from the provided direct URL.
        """
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        # Clean filename to prevent OS errors
        clean_name = "".join(
            [c for c in filename if c.isalnum() or c in (" ", ".", "_")]
        ).rstrip()
        file_path = os.path.join(output_folder, f"{clean_name}.mp3")

        print(f"[Status] Starting download: {filename}...")

        async with httpx.AsyncClient(headers=self.headers, timeout=None) as client:
            async with client.stream("GET", download_url) as response:
                response.raise_for_status()

                async with aiofiles.open(file_path, mode="wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        await f.write(chunk)

        print(f"[Success] Downloaded to: {file_path}")
        return file_path

    async def quick_download(self, query: str):
        """
        A helper method that searches for the first result and downloads it immediately.
        """
        # 1. Search
        results = await self.search(query)
        if not results:
            print("No results found.")
            return

        video = results[0]
        video_id = video["id"]
        title = video["title"]

        # 2. Get Direct Link
        info = await self.get_download_info(video_id)
        dl_url = info.get("downloadUrl")

        if dl_url:
            # 3. Download
            await self.download(dl_url, title)
        else:
            print("Failed to retrieve download URL.")


# async def main():
# yt = YouTubeDL()
# r = await yt.search("mehrama")
# print(json.dumps(r, indent=2))

# if __name__ == "__main__":
# asyncio.run(main())

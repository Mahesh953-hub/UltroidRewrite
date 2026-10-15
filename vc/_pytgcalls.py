#  Copyright (c) 2025 AshokShau
#  Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
#  Part of the TgMusicBot project. All rights reserved where applicable.

import asyncio
import os
import random
import re
from pathlib import Path
from typing import Optional, Union, Any, Tuple

from telethon import TelegramClient, errors
from pytgcalls import PyTgCalls, exceptions
from pytgcalls.types import (
    AudioQuality,
    ChatUpdate,
    MediaStream,
    Update,
    UpdatedGroupCallParticipant,
    VideoQuality,
    stream,
    GroupCallConfig,
    CallConfig,
)
from ntgcalls import TelegramServerError, ConnectionNotFound

try:
    from pyUltroid import HNDLR
except ImportError:  # pragma: no cover - HNDLR is only set when run as a module
    HNDLR = "."

from ._tools import (
    get_audio_duration,
    sec_to_min,
    cleanup_downloads_dir,
    ms_to_seconds,
    LOGGER,
)
from ._api import ApiData
from ._cacher import chat_cache
from ._player import PlayerRegistry, track_from_cached
from ._messages import PARSEMODE, now_playing_msg
from ._database import db
from ._dataclass import CachedTrack
from ._downloader import MusicServiceWrapper
from ._jiosaavn import JiosaavnData
from ._youtube import YouTubeData


class Ok:
    pass


class Error:
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message

    def __repr__(self):
        return f"Error(code={self.code}, message={self.message!r})"


class Call:
    def __init__(self):
        self.calls: dict[str, PyTgCalls] = {}
        self.client_counter: int = 1
        self.available_clients: list[str] = []
        self.bot: TelegramClient | None = None
        self._client_assignment_lock = asyncio.Lock()
        # Per-chat locks serialise concurrent play requests in the same chat so
        # that two simultaneous /play commands don't race on the call client.
        self._play_locks: dict[int, asyncio.Lock] = {}
        # Ids of PyTgCalls instances that already have update handlers bound,
        # so re-wiring (plugin reloads, double startup paths) never stacks
        # duplicate handlers (which would fire play_next twice per track).
        self._decorated: set[int] = set()
        # Player state: per-chat current track, playing/paused/stopped,
        # queue position and elapsed time. Commands read this instead of
        # recomputing state from the raw cache on every reply.
        self.players = PlayerRegistry()

    def _get_play_lock(self, chat_id: int) -> asyncio.Lock:
        """Return (lazily creating) the asyncio.Lock for ``chat_id``."""
        if chat_id not in self._play_locks:
            self._play_locks[chat_id] = asyncio.Lock()
        return self._play_locks[chat_id]

    async def add_bot(self, client: TelegramClient) -> Ok:
        """Register the main bot client.

        Args:
            client: The main bot client instance

        Returns:
            Ok on success
        """
        self.bot = client
        return Ok()

    async def _get_client_name(self, chat_id: int) -> str | Error:
        """Get an available client session for a chat."""
        if not self.available_clients:
            return Error(code=500, message="No clients available\nReport this issue")

        if chat_id == 1:
            return random.choice(self.available_clients)

        async with self._client_assignment_lock:
            assistant = await db.get_assistant(chat_id)
            if assistant and assistant in self.available_clients:
                return assistant

            new_client = random.choice(self.available_clients)
            await db.set_assistant(chat_id, assistant=new_client)
            LOGGER.info("Set assistant for %s to %s", chat_id, new_client)
            return new_client

    async def group_assistant(self, chat_id: int) -> PyTgCalls | Error:
        client_name = await self._get_client_name(chat_id)
        if isinstance(client_name, Error):
            return client_name

        return self.calls[client_name]

    def _register_handlers_for(self, _call: PyTgCalls) -> None:
        """Bind the update handler to one PyTgCalls instance, exactly once."""
        if id(_call) in self._decorated:
            return
        self._decorated.add(id(_call))

        @_call.on_update()
        async def general_handler(_, update: Update, _call=_call):
            try:
                # LOGGER.debug("Received update from call %s: %s", _call, update)
                if isinstance(update, stream.StreamEnded):
                    await self.play_next(update.chat_id)
                elif isinstance(update, UpdatedGroupCallParticipant):
                    return
                elif isinstance(update, ChatUpdate) and (
                    update.status.KICKED or update.status.LEFT_GROUP
                ):
                    LOGGER.debug("Cleaning up chat %s after leaving", update.chat_id)
                    chat_cache.clear_chat(update.chat_id)
            except Exception as e:
                LOGGER.error("Error in general handler: %s", e, exc_info=True)

    async def register_decorators(self) -> None:
        """Register pytgcalls event handlers on every attached client."""
        for _call in self.calls.values():
            self._register_handlers_for(_call)

    async def play_media(
        self,
        chat_id: int,
        file_path: str | Path,
        video: bool = False,
        ffmpeg_parameters: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Ok | Error:
        """Play media in a voice chat.

        Args:
            chat_id: Target chat ID
            file_path: Path to a local file, or a direct stream URL.
            video: Whether to stream video
            ffmpeg_parameters: Custom FFmpeg parameters
            headers: HTTP headers for a remote stream. Required for googlevideo
                URLs, which return 403 to ffmpeg without the matching headers
                that were used to obtain the URL.

        Returns:
            Ok on success or Error on failure
        """
        LOGGER.info(
            "Playing media for chat %s: %s (video=%s)", chat_id, file_path, video
        )

        client = await self.group_assistant(chat_id)
        if isinstance(client, Error):
            return client

        # Validate media file exists if not URL
        is_url = bool(re.match("^https?://", str(file_path)))
        if not is_url and not os.path.exists(file_path):
            return Error(
                code=404, message="Media file not found. It may have been deleted."
            )

        _stream = MediaStream(
            audio_path=file_path,
            media_path=file_path,
            audio_parameters=AudioQuality.HIGH if video else AudioQuality.STUDIO,
            video_parameters=VideoQuality.FHD_1080p if video else VideoQuality.SD_360p,
            audio_flags=MediaStream.Flags.REQUIRED,
            video_flags=(
                MediaStream.Flags.AUTO_DETECT if video else MediaStream.Flags.IGNORE
            ),
            ffmpeg_parameters=ffmpeg_parameters,
            headers=headers or None,
        )

        call_config = (
            GroupCallConfig(auto_start=False) if chat_id < 0 else CallConfig(timeout=50)
        )
        # Serialise concurrent play requests for the same chat to avoid
        # overlapping client.play() calls racing on the group call.
        async with self._get_play_lock(chat_id):
            try:
                await client.play(chat_id, _stream, call_config)
                return Ok()
            except Exception as e:
                err_str = str(e)
                # py-tgcalls may hit ExportAuthorizationRequest when
                # uploading the file to Telegram's CDN — the internal file
                # handle can be at a stale position. Retry once with a
                # fresh BytesIO copy so the upload starts at byte 0.
                if "ExportAuthorization" in err_str or "exported" in err_str:
                    LOGGER.warning(
                        "ExportAuthorization error for %s — retrying with BytesIO copy",
                        file_path,
                    )
                    try:
                        from io import BytesIO

                        with open(file_path, "rb") as fh:
                            _fresh_data = BytesIO(fh.read())
                        _retry_stream = MediaStream(
                            audio_path=_fresh_data,
                            media_path=_fresh_data,
                            audio_parameters=(
                                AudioQuality.HIGH if video else AudioQuality.STUDIO
                            ),
                            video_parameters=(
                                VideoQuality.FHD_1080p
                                if video
                                else VideoQuality.SD_360p
                            ),
                            audio_flags=MediaStream.Flags.REQUIRED,
                            video_flags=(
                                MediaStream.Flags.AUTO_DETECT
                                if video
                                else MediaStream.Flags.IGNORE
                            ),
                            ffmpeg_parameters=ffmpeg_parameters,
                            headers=headers or None,
                        )
                        await client.play(chat_id, _retry_stream, call_config)
                        return Ok()
                    except Exception as retry_err:
                        LOGGER.error("Retry also failed: %s", retry_err)
                        return Error(
                            code=500,
                            message=f"Upload failed after retry: {retry_err}",
                        )
                raise
            except (exceptions.NoActiveGroupCall, ConnectionNotFound):
                return Error(
                    code=404,
                    message="No active voice chat found.\n\n"
                    "Please start a voice chat and try again.",
                )
            except TelegramServerError:
                LOGGER.warning("Telegram server error during playback")
                return Error(
                    code=502,
                    message="Telegram server issues detected. Please try again later.",
                )
            except exceptions.NoAudioSourceFound as e:
                LOGGER.error("Audio source not found in chat %s: %s", chat_id, str(e))
                return Error(code=404, message="Audio source not found.")
            except errors.RPCError as e:
                LOGGER.error("Playback failed in chat %s: %s", chat_id, str(e))
                return Error(code=e.code or 500, message=f"Playback error: {str(e)}")
            except Exception as e:
                LOGGER.error(
                    "Playback failed in chat %s: %s", chat_id, str(e), exc_info=True
                )
                return Error(code=500, message=f"Playback error: {str(e)}")

    async def play_next(self, chat_id: int) -> None:
        """Handle playback of next track in queue.

        Args:
            chat_id: Target chat ID

        Handles:
            - Loop counts
            - Queue management
            - Empty queue scenarios
        """
        LOGGER.info("Playing next song for chat %s", chat_id)
        try:
            # Drive the player state first: it is the single source of truth
            # for what is playing, and the commands read from it.
            player = self.players.get(chat_id)
            nxt = player.finish_current()

            if nxt is not None:
                await self._play_song(chat_id, nxt.to_cached())
            else:
                # Keep the legacy deque in step so existing command handlers
                # that still read chat_cache see the same head.
                chat_cache.remove_current_song(chat_id)
                await self._handle_no_songs(chat_id)

        except Exception as e:
            LOGGER.error(
                "Error in play_next for chat %s: %s", chat_id, str(e), exc_info=True
            )

    async def _play_song(self, chat_id: int, song: CachedTrack) -> None:
        """Internal method to play a specific song.

        Args:
            chat_id: Target chat ID
            song: CachedTrack object containing song data
        """
        LOGGER.info("Playing song for chat %s: %s", chat_id, song.name)

        try:
            if not self.bot:
                LOGGER.error("No bot client configured in Call")
                return

            # Send an initial loading message
            try:
                reply = await self.bot.send_message(
                    chat_id, "⏳ Loading... Please wait."
                )
            except Exception as e:
                LOGGER.error("Failed to send message: %s", e)
                return

            # Download song if isn't downloaded
            file_path = song.file_path or await self.song_download(song)
            if not file_path:
                try:
                    await reply.edit(
                        "⚠️ Failed to download the song.\nSkipping to next track..."
                    )
                except Exception as e:
                    LOGGER.warning("Failed to edit reply: %s", e)
                await self.play_next(chat_id)
                return

            # Start playback
            play_result = await self.play_media(chat_id, file_path, video=song.is_video)
            if isinstance(play_result, Error):
                try:
                    await reply.edit(play_result.message)
                except Exception as e:
                    LOGGER.warning("Failed to edit reply: %s", e)
                return

            # Get duration if not available
            duration = song.duration or await get_audio_duration(file_path)
            song.duration = duration

            # Prepare a playback message. Built through the v2 message
            # helpers so the title, artist and requester are entity-escaped -
            # this fork's v2 parser drops raw '<', '&' and backtick, which is
            # what produced mangled/gibberish notices.
            text = now_playing_msg(track_from_cached(song), self.players.get(chat_id))

            # Update a message with text
            try:
                await reply.edit(text, parse_mode=PARSEMODE, link_preview=False)
            except Exception as e:
                LOGGER.warning("Failed to edit reply: %s", e)

        except Exception as e:
            LOGGER.error(
                "Error in _play_song for chat %s: %s", chat_id, str(e), exc_info=True
            )

    @staticmethod
    async def song_download(song: CachedTrack) -> Path | None:
        """Download a song from its source platform.

        Args:
            song: CachedTrack object containing song metadata

        Returns:
            Path to downloaded file or None if failed
        """
        platform_handlers = {
            "youtube": YouTubeData(song.track_id),
            "jiosaavn": JiosaavnData(song.url),
            "spotify": ApiData(song.track_id),
            "apple_music": ApiData(song.url),
            "soundcloud": ApiData(song.url),
        }

        handler = platform_handlers.get(song.platform.lower())
        if not handler:
            LOGGER.warning(
                "Unsupported platform: %s for track: %s", song.platform, song.track_id
            )
            return None

        try:
            track = await handler.get_track()
            return await handler.download_track(track, song.is_video) if track else None
        except Exception as e:
            LOGGER.error(
                "Download failed for %s: %s", song.track_id, str(e), exc_info=True
            )
            return None

    async def _handle_no_songs(self, chat_id: int) -> None:
        """Handle an empty queue scenario.

        Args:
            chat_id: Target chat ID
        """
        from custom.tg import ikm

        try:
            await self.end(chat_id)
            _chat_id = await db.get_chat_id_by_channel(chat_id) or chat_id
            # Try to get recommendations
            try:
                recommendations = await MusicServiceWrapper().get_recommendations()
                if recommendations and recommendations.tracks:
                    buttons = ikm(
                        [
                            [
                                (
                                    f"{track.name[:18]} - {track.artist}",
                                    f"play_{track.platform}_{track.id}".encode(),
                                )
                            ]
                            for track in recommendations.tracks
                        ]
                    )

                    await self.bot.send_message(
                        _chat_id,
                        "🎵 Queue finished. Try these recommendations:\n",
                        buttons=buttons,
                    )
                    return
            except Exception as e:
                LOGGER.warning("Failed to get recommendations: %s", e)

            # Fallback message
            await self.bot.send_message(
                _chat_id, f"🎵 Queue finished.\nUse {HNDLR}play to add more songs!"
            )

        except Exception as e:
            LOGGER.error(
                "Error in _handle_no_songs for chat %s: %s",
                chat_id,
                str(e),
                exc_info=True,
            )

    async def end(self, chat_id: int) -> Ok | Error:
        """End playback and clean up for a chat.

        Args:
            chat_id: Target chat ID

        Returns:
            Ok on success or Error on failure
        """
        LOGGER.info("Ending playback for chat %s", chat_id)
        try:
            client = await self.group_assistant(chat_id)
            if isinstance(client, Error):
                return client

            chat_cache.clear_chat(chat_id)

            try:
                await client.leave_call(chat_id)
            except Exception:
                pass  # Already not in call or error leaving

            # Clean up stale temporary files left over from playback.
            try:
                cleanup_downloads_dir()
            except Exception as cleanup_err:
                LOGGER.warning("Stale file cleanup failed: %s", cleanup_err)

            return Ok()
        except Exception as e:
            LOGGER.error(
                "Error ending call for chat %s: %s", chat_id, str(e), exc_info=True
            )
            return Error(code=500, message=f"Failed to end call: {str(e)}")

    async def seek_stream(
        self,
        chat_id: int,
        file_path_or_url: str | Path,
        to_seek: int,
        duration: int,
        is_video: bool,
    ) -> Ok | Error:
        """Seek to a position in the current stream.

        Args:
            chat_id: Target chat ID
            file_path_or_url: Media file path or URL
            to_seek: Position to seek to (seconds)
            duration: Total duration to play (seconds)
            is_video: Whether the stream is video

        Returns:
            Ok on success or Error on failure
        """
        if to_seek < 0 or duration <= 0:
            return Error(
                code=400,
                message="Invalid seek position or duration.\n"
                "Position must be positive and duration must be greater than 0.",
            )

        try:
            is_url = bool(re.match(r"https?://", str(file_path_or_url)))
            ffmpeg_params = (
                f"-ss {to_seek} -i {file_path_or_url} -to {duration}"
                if is_url or not os.path.isfile(file_path_or_url)
                else f"-ss {to_seek} -to {duration}"
            )

            return await self.play_media(
                chat_id, file_path_or_url, is_video, ffmpeg_params
            )
        except Exception as e:
            LOGGER.error("Seek failed for chat %s: %s", chat_id, str(e), exc_info=True)
            return Error(code=500, message=f"Seek operation failed: {str(e)}")

    async def speed_change(self, chat_id: int, speed: float = 1.0) -> Ok | Error:
        """Change playback speed.

        Args:
            chat_id: Target chat ID
            speed: Playback speed (0.5-4.0)

        Returns:
            Ok on success or Error on failure
        """
        if not 0.5 <= speed <= 4.0:
            return Error(
                code=400, message="Invalid speed value.\nMust be between 0.5 and 4.0"
            )

        curr_song = chat_cache.get_current_song(chat_id)
        if not curr_song or not curr_song.file_path:
            return Error(code=404, message="No track currently playing")

        return await self.play_media(
            chat_id,
            curr_song.file_path,
            curr_song.is_video,
            # Video is retimed by the SAME factor as the audio, so a "2x"
            # speed change does not desync picture from sound. This was
            # hardcoded to 0.5 regardless of the requested speed.
            ffmpeg_parameters=(
                f"-atend -filter:v setpts={1 / speed}*PTS"
                f" -filter:a atempo={speed}"
            ),
        )

    async def change_volume(self, chat_id: int, volume: int) -> None | Error:
        """Change playback volume.

        Args:
            chat_id: Target chat ID
            volume: Volume level (1-200)

        Returns:
            None on success or Error on failure
        """
        try:
            client = await self.group_assistant(chat_id)
            if isinstance(client, Error):
                return client

            if volume < 1 or volume > 200:
                return Error(code=400, message="Volume must be between 1 and 200")

            await client.change_volume_call(chat_id, volume)
            return None
        except Exception as e:
            LOGGER.error(
                "Volume change failed for chat %s: %s", chat_id, str(e), exc_info=True
            )
            return Error(code=500, message=f"Volume change failed: {str(e)}")

    async def mute(self, chat_id: int) -> Ok | Error:
        """Mute the current stream.

        Args:
            chat_id: Target chat ID

        Returns:
            Ok on success or Error on failure
        """
        try:
            client = await self.group_assistant(chat_id)
            if isinstance(client, Error):
                return client

            await client.mute(chat_id)
            return Ok()
        except Exception as e:
            LOGGER.error("Mute failed for chat %s: %s", chat_id, str(e), exc_info=True)
            return Error(code=500, message=f"Mute operation failed: {str(e)}")

    async def unmute(self, chat_id: int) -> Ok | Error:
        """Unmute the current stream.

        Args:
            chat_id: Target chat ID

        Returns:
            Ok on success or Error on failure
        """
        try:
            client = await self.group_assistant(chat_id)
            if isinstance(client, Error):
                return client

            await client.unmute(chat_id)
            return Ok()
        except Exception as e:
            LOGGER.error(
                "Unmute failed for chat %s: %s", chat_id, str(e), exc_info=True
            )
            return Error(code=500, message=f"Unmute operation failed: {str(e)}")

    async def resume(self, chat_id: int) -> Ok | Error:
        """Resume a paused stream.

        Args:
            chat_id: Target chat ID

        Returns:
            Ok on success or Error on failure
        """
        try:
            client = await self.group_assistant(chat_id)
            if isinstance(client, Error):
                return client

            await client.resume(chat_id)
            return Ok()
        except Exception as e:
            LOGGER.error(
                "Resume failed for chat %s: %s", chat_id, str(e), exc_info=True
            )
            return Error(code=500, message=f"Resume operation failed: {str(e)}")

    async def pause(self, chat_id: int) -> Ok | Error:
        """Pause the current stream.

        Args:
            chat_id: Target chat ID

        Returns:
            Ok on success or Error on failure
        """
        try:
            client = await self.group_assistant(chat_id)
            if isinstance(client, Error):
                return client

            await client.pause(chat_id)
            return Ok()
        except Exception as e:
            LOGGER.error("Pause failed for chat %s: %s", chat_id, str(e), exc_info=True)
            return Error(code=500, message=f"Pause operation failed: {str(e)}")

    async def played_time(self, chat_id: int) -> int | Error:
        """Get the current playback position in SECONDS.

        pytgcalls' ``time()`` returns the raw group-call stream timestamp,
        which is ``channels[0].last_timestamp_ms`` - milliseconds. Returning
        it raw made every progress calculation 1000x too large.

        Args:
            chat_id: Target chat ID

        Returns:
            Current position in seconds, or Error on failure.
        """
        try:
            client = await self.group_assistant(chat_id)
            if isinstance(client, Error):
                return client

            raw = await client.time(chat_id)
            # Guard against a bound client that already returns seconds.
            if raw is None:
                return 0
            return ms_to_seconds(raw)
        except exceptions.NotInCallError:
            chat_cache.clear_chat(chat_id)
            return 0
        except Exception as e:
            LOGGER.error(
                "Time check failed for chat %s: %s", chat_id, str(e), exc_info=True
            )
            return Error(code=500, message=f"Failed to get playback time: {str(e)}")

    async def vc_users(self, chat_id: int) -> list | Error:
        """Get a list of participants in voice chat.

        Args:
            chat_id: Target chat ID

        Returns:
            List of participants or Error on failure
        """
        try:
            client = await self.group_assistant(chat_id)
            if isinstance(client, Error):
                return client

            return await client.get_participants(chat_id)
        except exceptions.UnsupportedMethod:
            return Error(code=501, message="This method is not supported by the server")
        except Exception as e:
            LOGGER.error(
                "Participant list failed for chat %s: %s",
                chat_id,
                str(e),
                exc_info=True,
            )
            return Error(code=500, message=f"Failed to get participants: {str(e)}")

    async def stats_call(self, chat_id: int) -> tuple[float, float] | Error:
        """Get call statistics.

        Args:
            chat_id: Target chat ID

        Returns:
            Tuple of (ping, cpu_usage) or Error on failure
        """
        try:
            client = await self.group_assistant(chat_id)
            if isinstance(client, Error):
                return client

            return (
                client.ping,
                await client.cpu_usage,
            )
        except Exception as e:
            LOGGER.error(
                "Stats check failed for chat %s: %s", chat_id, str(e), exc_info=True
            )
            return Error(code=500, message=f"Failed to get stats: {str(e)}")


call: Call = Call()


def wire_call(
    app: PyTgCalls,
    bot_client: TelegramClient | None,
    name: str = "self",
    engine: Call | None = None,
) -> bool:
    """Idempotently attach a PyTgCalls *app* to the :data:`call` engine (W1-A).

    Populates ``engine.calls``/``engine.available_clients`` (the pools
    :meth:`Call.group_assistant` picks from), stores *bot_client* as the
    messaging client, and binds the pytgcalls update handlers to *app*.

    This is deliberately synchronous: it only mutates in-memory state and
    registers decorators, so it can run while the plugin loader holds the
    client loop (a ``run_until_complete`` here would deadlock).

    Args:
        app: The PyTgCalls instance (``pyUltroid.app``).
        bot_client: Telegram client used for engine-originated messages
            (loading/now-playing texts, queue-finished notices).
        name: Pool key for *app*; repeat wiring with the same name is a no-op.
        engine: Engine to wire into; defaults to the module singleton
            (injectable for tests).

    Returns:
        True when *app* was newly attached, False when already wired.
    """
    engine = engine if engine is not None else call
    if name in engine.calls:
        if bot_client is not None and engine.bot is None:
            engine.bot = bot_client
        return False
    engine.calls[name] = app
    engine.available_clients.append(name)
    # First wired bot wins: the messaging client must stay stable across
    # later pool additions (multi-client setups share one reporter).
    if bot_client is not None and engine.bot is None:
        engine.bot = bot_client
    engine._register_handlers_for(app)
    LOGGER.info(
        "VC engine wired to PyTgCalls app %r (pool: %s)", name, engine.available_clients
    )
    return True
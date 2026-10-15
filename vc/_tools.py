import asyncio
import json
import os
import time

try:
    from pyUltroid import LOGS as LOGGER, udB, Var
except ImportError:
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s - %(levelname)s] - %(name)s - %(filename)s:%(lineno)d - %(message)s",
        datefmt="%d-%b-%y %H:%M:%S",
        handlers=[logging.StreamHandler(), logging.FileHandler("vc.log")],
    )

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    # logging.getLogger('pytgcalls').setLevel(logging.DEBUG)
    # logging.getLogger('ffmpeg').setLevel(logging.DEBUG)
    # logging.getLogger('ntgcalls').setLevel(logging.DEBUG)
    # logging.getLogger('webrtc').setLevel(logging.DEBUG)

    LOGGER = logging.getLogger("VC")
    udB = None
    Var = None

# mutagen is a declared dependency (requirements.txt) and is imported directly
# by the vc modules that need it (vc/_spotify.py, vc/_jiosaavn.py). The old
# utils.tools.import_library("mutagen") call pip-installed at import time and
# dragged utils/tools.py's heavy import chain (psutil, PIL, requests) into
# every vc import — both removed deliberately (W1-A). The canonical helper
# now lives in pyUltroid.fns.tools.import_library and does not pip-install at
# runtime (W4-B).

DOWNLOADS_DIR = "./temp"
# API credentials are sourced from pyUltroid.Var / udB / environment variables
# instead of being hardcoded.
API_KEY = (
    (udB.get_key("MUSIC_API_KEY") if udB else None)
    or (getattr(Var, "MUSIC_API_KEY", None) if Var else None)
    or os.environ.get("MUSIC_API_KEY", "")
)
API_URL = (
    (udB.get_key("MUSIC_API_URL") if udB else None)
    or (getattr(Var, "MUSIC_API_URL", None) if Var else None)
    or os.environ.get("MUSIC_API_URL", "")
)


def sec_to_min(seconds):
    """
    Convert seconds to minutes:second format.
    """
    try:
        minutes = int(seconds // 60)
        remaining_seconds = int(seconds % 60)
        return f"{minutes}:{remaining_seconds:02}"
    except Exception as e:
        LOGGER.warning("Failed to convert seconds to minutes:seconds format: %s", e)
        return None


async def get_audio_duration(file_path):
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            file_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        data = json.loads(stdout)
        duration = float(data["format"]["duration"])
        return int(duration)
    except Exception as e:
        LOGGER.warning("Failed to get audio duration using ffprobe: %s", e)
        return 0


def cleanup_downloads_dir(
    directory: str = DOWNLOADS_DIR, older_than_seconds: int = 3600
) -> int:
    """Remove stale temporary files from the downloads directory.

    Only files older than ``older_than_seconds`` (default 1 hour) are deleted so
    that tracks currently being streamed in other chats are never removed.
    Returns the number of files removed.
    """
    removed = 0
    if not directory or not os.path.isdir(directory):
        return removed
    now = time.time()
    try:
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            try:
                if now - os.path.getmtime(path) > older_than_seconds:
                    os.remove(path)
                    removed += 1
            except OSError:
                continue
    except OSError as e:
        LOGGER.warning("cleanup_downloads_dir failed: %s", e)
    return removed


def ms_to_seconds(value):
    """Convert a pytgcalls group-call timestamp to whole seconds.

    ``PyTgCalls.time()`` returns the raw stream timestamp
    (``channels[0].last_timestamp_ms``), which is milliseconds, even though
    it is usually consumed as if it were seconds. Returning it raw made every
    progress calculation 1000x too large.

    Values at or below 1000 pass through, so a bound client that already
    reports seconds is not mangled.
    """
    if value is None:
        return 0
    try:
        value = int(value)
    except (TypeError, ValueError):
        return 0
    if value <= 1000:
        return value
    return value // 1000

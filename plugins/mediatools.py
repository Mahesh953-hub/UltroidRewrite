# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}mediainfo [-m] [-a] <reply to media>/<file path>/<url>`
   To get info about it.
   Flags:
   `-m`  : show extended metadata(EXIF for images, codec info for video/audio)
   `-a`  : when replying to an album, show info for ALL files (default: first only)

• `{i}rotate <degree/angle> <reply to media>`
   Rotate any video/photo/media..
   Note : for video it should be angle of 90's
"""

import asyncio
import json
import mimetypes
import os
import shutil
import time
from datetime import datetime as dt

from pyUltroid.fns import fns
from pyUltroid.fns.misc import CatboxUploader
from pyUltroid.fns import fns
from pyUltroid.fns.misc import CatboxUploader

from . import (
    LOGS,
    Telegraph,
    get_string,
    ultroid_cmd,
)

try:
    import cv2
except ImportError:
    LOGS.info("WARNING: 'cv2' not found!")
    cv2 = None

try:
    from PIL import Image
    from PIL.ExifTags import TAGS
except ImportError:
    Image = None
    TAGS = {}

uploader = CatboxUploader(userhash="")

# ffprobe binary (used for codec/bitrate/duration metadata)
_FFPROBE = shutil.which("ffprobe")

# EXIF tag name -> id, for quick lookups
_TAG_IDS = {v: k for k, v in TAGS.items()} if TAGS else {}

# Extension fallbacks used when the MIME type is missing or generic.
_EXT_IMAGE = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tiff",
    ".tif",
    ".heic",
    ".heif",
    ".avif",
}
_EXT_VIDEO = {
    ".mp4",
    ".mkv",
    ".avi",
    ".mov",
    ".webm",
    ".flv",
    ".wmv",
    ".m4v",
    ".3gp",
    ".mpeg",
    ".mpg",
    ".ts",
}
_EXT_AUDIO = {
    ".mp3",
    ".wav",
    ".flac",
    ".ogg",
    ".aac",
    ".m4a",
    ".opus",
    ".wma",
    ".alac",
    ".aiff",
}


# ──────────────────────────────────────────────────────────────────────────
# Media-type detection (MIME first, extension fallback)
# ──────────────────────────────────────────────────────────────────────────


def _detect_media_type(msg):
    """Robust media-type detection for a Telethon message.

    Uses the document MIME type first, then falls back to the file
    extension and finally the stdlib ``mimetypes`` database.  Returns a
    short human-readable label such as ``pic``, ``video``, ``audio``,
    ``sticker``, ``gif`` or ``document``.
    """
    media = getattr(msg, "media", None)
    if media is None:
        return "document"
    klass = str(media).split("(", maxsplit=1)[0]
    if klass == "MessageMediaPhoto":
        return "pic"
    if klass == "MessageMediaWebPage":
        return "web"
    if klass == "MessageMediaDocument" and hasattr(media, "document"):
        doc = media.document
        mime = (getattr(doc, "mime_type", "") or "").lower()
        try:
            name = msg.file.name or ""
        except Exception:
            name = ""
        ext = os.path.splitext(name or "")[-1].lower()
        attrs = str(media)
        if mime == "application/x-tgsticker":
            return "sticker animated"
        if mime == "image/webp":
            return "sticker"
        if "image" in mime:
            return "gif as doc" if mime == "image/gif" else "pic as doc"
        if "video" in mime:
            if "DocumentAttributeAnimated" in attrs:
                return "gif"
            if "DocumentAttributeVideo" in attrs:
                return "video as doc"
            return "video"
        if "audio" in mime:
            return "audio"
        # MIME missing/generic → extension fallback
        if ext in _EXT_IMAGE:
            return "pic as doc"
        if ext in _EXT_VIDEO:
            return "video"
        if ext in _EXT_AUDIO:
            return "audio"
        guessed = mimetypes.guess_type(name)[0] or ""
        if "image" in guessed:
            return "pic as doc"
        if "video" in guessed:
            return "video"
        if "audio" in guessed:
            return "audio"
        return "document"
    return "document"


def _detect_type_from_path(path):
    """Detect a media type from a filename/path/URL using extensions."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXT_IMAGE:
        return "image"
    if ext in _EXT_VIDEO:
        return "video"
    if ext in _EXT_AUDIO:
        return "audio"
    guessed = mimetypes.guess_type(path)[0] or ""
    if "image" in guessed:
        return "image"
    if "video" in guessed:
        return "video"
    if "audio" in guessed:
        return "audio"
    return None


# ──────────────────────────────────────────────────────────────────────────
# Basic info (no download required)
# ──────────────────────────────────────────────────────────────────────────


def _basic_info(msg):
    """Build a dict of basic, always-available info from a Telethon message."""
    info = {"type": _detect_media_type(msg)}
    f = getattr(msg, "file", None)
    if f is not None:
        info["name"] = f.name
        info["size"] = f.size
        info["mime"] = f.mime_type
        info["width"] = f.width
        info["height"] = f.height
        info["duration"] = f.duration
    return info


def _fmt_duration(seconds):
    try:
        ms = int(float(seconds) * 1000)
        return fns.time_formatter(ms) or f"{float(seconds):.1f}s"
    except Exception:
        return str(seconds)


def _fmt_basic(info):
    lines = [f"**Type:** {info.get('type') or 'unknown'}"]
    if info.get("name"):
        lines.append(f"**Name:** `{info['name']}`")
    if info.get("size"):
        lines.append(f"**Size:** {fns.humanbytes(info['size'])}")
    if info.get("mime"):
        lines.append(f"**MIME:** {info['mime']}")
    if info.get("width") and info.get("height"):
        lines.append(f"**Dimensions:** {info['width']}x{info['height']}")
    if info.get("duration"):
        lines.append(f"**Duration:** {_fmt_duration(info['duration'])}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# Extended metadata (EXIF + ffprobe codec info)
# ──────────────────────────────────────────────────────────────────────────


def _gps_to_decimal(values, ref):
    """Convert EXIF GPS DMS rationals to decimal degrees."""
    if not values:
        return None
    try:

        def _rat(v):
            if isinstance(v, (tuple, list)):
                if len(v) == 2:
                    return v[0] / v[1] if v[1] else 0.0
                return float(v[0]) if v else 0.0
            return float(v)

        if len(values) >= 3:
            d, m, s = _rat(values[0]), _rat(values[1]), _rat(values[2])
        elif len(values) == 2:
            d, m, s = _rat(values[0]), _rat(values[1]), 0.0
        else:
            return None
        dec = d + m / 60.0 + s / 3600.0
        if isinstance(ref, str) and ref.upper() in ("S", "W"):
            dec = -dec
        return dec
    except Exception:
        return None


def _extract_exif(path):
    """Return EXIF metadata for an image file via PIL, or {} on failure."""
    if not Image or not os.path.exists(path):
        return {}
    try:
        im = Image.open(path)
        im.load()
    except Exception as exc:
        LOGS.debug("[mediatools] PIL open failed: %s", exc)
        return {}
    meta = {"format": getattr(im, "format", None)}
    try:
        exif = im.getexif()
    except Exception:
        exif = None
    if exif:

        def _get(name):
            tid = _TAG_IDS.get(name)
            if tid is None:
                return None
            try:
                return exif.get(tid)
            except Exception:
                return None

        make = _get("Make")
        model = _get("Model")
        if make or model:
            camera = " ".join(filter(None, [make, model])).strip()
            if camera:
                meta["camera"] = camera
        if _get("DateTime"):
            meta["date_taken"] = _get("DateTime")
        if _get("Software"):
            meta["software"] = _get("Software")
        try:
            gps_ifd = exif.get_ifd(0x8825) if hasattr(exif, "get_ifd") else {}
        except Exception:
            gps_ifd = {}
        lat = _gps_to_decimal(gps_ifd.get(2), gps_ifd.get(1))
        lon = _gps_to_decimal(gps_ifd.get(4), gps_ifd.get(3))
        if lat is not None and lon is not None:
            meta["gps"] = f"{lat:.6f}, {lon:.6f}"
    return {k: v for k, v in meta.items() if v}


def _parse_fps(rate):
    if not rate or rate == "0/0":
        return None
    try:
        num, den = rate.split("/")
        den = float(den) or 1
        return round(float(num) / den, 2)
    except Exception:
        return None


def _codec_info(probe):
    """Parse an ffprobe JSON dict into a flat metadata dict."""
    info = {}
    fmt = probe.get("format") or {}
    if fmt.get("format_name"):
        info["container"] = fmt["format_name"]
    if fmt.get("duration"):
        info["duration"] = fmt["duration"]
    if fmt.get("bit_rate"):
        info["bitrate"] = fmt["bit_rate"]
    streams = probe.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if v:
        if v.get("codec_name"):
            info["video_codec"] = v["codec_name"]
        if v.get("width") and v.get("height"):
            info["width"] = v["width"]
            info["height"] = v["height"]
        fps = _parse_fps(v.get("avg_frame_rate") or v.get("r_frame_rate"))
        if fps:
            info["fps"] = fps
        if v.get("bit_rate"):
            info["video_bitrate"] = v["bit_rate"]
    if a:
        if a.get("codec_name"):
            info["audio_codec"] = a["codec_name"]
        if a.get("sample_rate"):
            info["sample_rate"] = a["sample_rate"]
        if a.get("channels"):
            info["channels"] = a["channels"]
        if a.get("bit_rate"):
            info["audio_bitrate"] = a["bit_rate"]
    return info


async def _ffprobe(path):
    """Run ffprobe on a local file and return parsed JSON, or {} if unavailable."""
    if not _FFPROBE or not os.path.exists(path):
        return {}
    try:
        proc = await asyncio.create_subprocess_exec(
            _FFPROBE,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        text = stdout.decode().strip()
        return json.loads(text) if text else {}
    except Exception as exc:
        LOGS.debug("[mediatools] ffprobe failed: %s", exc)
        return {}


async def _extract_metadata(path, mtype):
    """Collect extended metadata (EXIF for images, codec info for A/V)."""
    meta = {}
    mtype = (mtype or "").lower()
    is_image = any(k in mtype for k in ("image", "pic", "photo")) and "gif" not in mtype
    is_av = any(k in mtype for k in ("video", "audio", "gif"))
    if is_image:
        meta.update(_extract_exif(path))
    if is_av or not is_image:
        probe = await _ffprobe(path)
        if probe:
            meta.update(_codec_info(probe))
    return meta


def _fmt_metadata(meta):
    if not meta:
        return "_No extended metadata available._"
    lines = []
    if meta.get("format"):
        lines.append(f"  • Format: {meta['format']}")
    if meta.get("container"):
        lines.append(f"  • Container: {meta['container']}")
    if meta.get("camera"):
        lines.append(f"  • Camera: {meta['camera']}")
    if meta.get("date_taken"):
        lines.append(f"  • Date Taken: {meta['date_taken']}")
    if meta.get("software"):
        lines.append(f"  • Software: {meta['software']}")
    if meta.get("gps"):
        lines.append(f"  • GPS: {meta['gps']}")
    if meta.get("video_codec"):
        lines.append(f"  • Video Codec: {meta['video_codec']}")
    if meta.get("width") and meta.get("height"):
        lines.append(f"  • Resolution: {meta['width']}x{meta['height']}")
    if meta.get("fps"):
        lines.append(f"  • FPS: {meta['fps']}")
    if meta.get("audio_codec"):
        lines.append(f"  • Audio Codec: {meta['audio_codec']}")
    if meta.get("sample_rate"):
        lines.append(f"  • Sample Rate: {meta['sample_rate']} Hz")
    if meta.get("channels"):
        ch = {1: "Mono", 2: "Stereo"}.get(meta["channels"], f"{meta['channels']}ch")
        lines.append(f"  • Channels: {ch}")
    if meta.get("duration"):
        lines.append(f"  • Duration: {_fmt_duration(meta['duration'])}")
    if meta.get("bitrate"):
        lines.append(f"  • Bitrate: {fns.humanbytes(int(meta['bitrate']) // 8)}/s")
    if meta.get("video_bitrate"):
        lines.append(
            f"  • Video Bitrate: {fns.humanbytes(int(meta['video_bitrate']) // 8)}/s"
        )
    if meta.get("audio_bitrate"):
        lines.append(
            f"  • Audio Bitrate: {fns.humanbytes(int(meta['audio_bitrate']) // 8)}/s"
        )
    if not lines:
        return "_No extended metadata available._"
    return "**Metadata:**\n" + "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# Album / flag helpers
# ──────────────────────────────────────────────────────────────────────────


def _parse_flags(raw):
    """Split raw args into (-m, -a) flags and the leftover match string."""
    show_meta = False
    show_all = False
    remaining = []
    for token in (raw or "").split():
        if token in ("-m", "--meta", "--metadata"):
            show_meta = True
        elif token in ("-a", "--all"):
            show_all = True
        else:
            remaining.append(token)
    return show_meta, show_all, " ".join(remaining).strip()


async def _resolve_album(event, replied):
    """Expand a grouped-media reply into the full album (ordered by id)."""
    grouped_id = getattr(replied, "grouped_id", None)
    if not grouped_id:
        return [replied]
    try:
        window = list(range(replied.id - 9, replied.id + 10))
        messages = await event.client.get_messages(event.chat_id, ids=window)
        album = [
            m
            for m in (messages or [])
            if m and getattr(m, "grouped_id", None) == grouped_id and m.media
        ]
        album.sort(key=lambda m: m.id)
        return album or [replied]
    except Exception as exc:
        LOGS.debug("[mediatools] album resolve failed: %s", exc)
        return [replied]


async def _download_url(url):
    """Download a URL to resources/downloads and return the local path."""
    try:
        from urllib.parse import unquote

        from pyUltroid.fns import fns
        from pyUltroid.fns.misc import CatboxUploader

        name = os.path.join(
            "resources/downloads",
            f"mi_{int(time.time() * 1000)}_{unquote(url.rsplit('/', 1)[-1] or 'file')}",
        )
        path, _ = await fns.download_file(url, name)
        return path
    except Exception as exc:
        LOGS.debug("[mediatools] url download failed: %s", exc)
        return None


# ──────────────────────────────────────────────────────────────────────────
# Output formatters
# ──────────────────────────────────────────────────────────────────────────


def _fmt_summary_table(results):
    rows = [" #  Type            Size         Dimensions/Duration"]
    for idx, info, _, _ in results:
        dim = ""
        if info.get("width") and info.get("height"):
            dim = f"{info['width']}x{info['height']}"
        elif info.get("duration"):
            dim = _fmt_duration(info["duration"])
        size = fns.humanbytes(info.get("size") or 0)
        rows.append(f" {idx:<2} {info.get('type', 'document'):<15} {size:<12} {dim}")
    return "```\n" + "\n".join(rows) + "\n```"


def _album_html(results, count):
    html = f"<h2>Album Mediainfo — {count} files</h2>"
    for idx, info, _, meta in results:
        html += f"<h3>File {idx}/{count}</h3>"
        html += f"<p><b>Type:</b> {info.get('type')}</p>"
        if info.get("name"):
            html += f"<p><b>Name:</b> {info['name']}</p>"
        if info.get("size"):
            html += f"<p><b>Size:</b> {fns.humanbytes(info['size'])}</p>"
        if info.get("mime"):
            html += f"<p><b>MIME:</b> {info['mime']}</p>"
        if info.get("width") and info.get("height"):
            html += f"<p><b>Dimensions:</b> {info['width']}x{info['height']}</p>"
        if info.get("duration"):
            html += f"<p><b>Duration:</b> {_fmt_duration(info['duration'])}</p>"
        if meta:
            html += "<br><b>Metadata:</b>"
            for key, val in meta.items():
                html += f"<p>{key}: {val}</p>"
    return html


# ──────────────────────────────────────────────────────────────────────────
# Commands
# ──────────────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern="mediainfo( (.*)|$)")
async def mi(e):
    raw = e.pattern_match.group(1).strip()
    show_meta, show_all, match = _parse_flags(raw)
    taime = time.time()
    r = await e.get_reply_message()

    if r and r.media:
        if show_all:
            album = await _resolve_album(e, r)
            if len(album) > 1:
                return await _mediainfo_album(e, album, show_meta, taime)
        return await _mediainfo_single(e, r, show_meta, taime, match)

    if match and (
        os.path.isfile(match)
        or (match.startswith("https://") and (await fns.is_url_ok(match)))
    ):
        return await _mediainfo_path(e, match, show_meta)

    return await e.eor(get_string("cvt_3"), time=5)


async def _mediainfo_single(e, r, show_meta, taime, match):
    """Single replied media — preserves the legacy telegraph output."""
    xx = _detect_media_type(r)
    murl = r.media.stringify()
    try:
        url = await fns.make_html_telegraph("Mediainfo", f"<pre>{murl}</pre>")
    except Exception as er:
        LOGS.exception(er)
        url = None
    extra = f"**[{xx}]({url})**\n\n" if url else f"**{xx}**\n\n"
    e = await e.eor(f"{extra}`Loading More...`", link_preview=False)

    if hasattr(r.media, "document"):
        file = r.media.document
        filename = r.file.name
        if not filename:
            mime_type = getattr(file, "mime_type", "") or ""
            if "audio" in mime_type:
                filename = "audio_" + dt.now().isoformat("_", "seconds") + ".ogg"
            elif "video" in mime_type:
                filename = "video_" + dt.now().isoformat("_", "seconds") + ".mp4"
            else:
                filename = f"file_{int(time.time())}"
        dl = await fns.downloader(
            f"resources/downloads/{filename}",
            file,
            e,
            taime,
            f"{extra}`Loading More...`",
        )
        naam = dl.name
    else:
        naam = await r.download_media()

    out, er = await fns.bash(f"mediainfo '{naam}'")
    if er:
        LOGS.info(er)
        out = extra or str(er)
        return await e.edit(out, link_preview=False)

    makehtml = ""
    if naam.endswith((".jpg", ".png")):
        if os.path.exists(naam):
            med = uploader.upload_file(naam, timeout=10)
        else:
            med = match
        makehtml += f"<img src='{med}'><br>"
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            makehtml += "<br>"
        elif ":" not in line:
            makehtml += f"<h3>{line}</h3>"
        else:
            makehtml += f"<p>{line}</p>"
    try:
        urll = await fns.make_html_telegraph("Mediainfo", makehtml)
    except Exception as er:
        LOGS.exception(er)
        return

    body = f"{extra}{_fmt_basic(_basic_info(r))}"
    if show_meta and naam and os.path.exists(naam):
        meta_block = _fmt_metadata(await _extract_metadata(naam, xx))
        body += f"\n\n{meta_block}"
    body += f"\n\n[{get_string('mdi_1')}]({urll})"
    await e.eor(body, link_preview=False)
    if not match:
        try:
            os.remove(naam)
        except OSError:
            pass


async def _mediainfo_path(e, match, show_meta):
    """Local file path or direct URL — preserves the legacy telegraph output."""
    naam = match
    xx = _detect_type_from_path(match) or "file"
    basic = f"**Type:** {xx}\n**Source:** `{match}`"
    if os.path.isfile(match):
        basic += f"\n**Size:** {fns.humanbytes(os.path.getsize(match))}"
    msg = await e.eor(f"{basic}\n\n`Loading More...`", link_preview=False)

    meta_block = ""
    local = match
    if show_meta:
        if match.startswith("https://"):
            local = await _download_url(match)
        if local and os.path.exists(local):
            meta_block = _fmt_metadata(await _extract_metadata(local, xx))

    out, er = await fns.bash(f"mediainfo '{naam}'")
    if er:
        LOGS.info(er)
        return await msg.edit(str(er), link_preview=False)

    makehtml = ""
    if naam.endswith((".jpg", ".png")):
        if os.path.exists(naam):
            med = uploader.upload_file(naam, timeout=10)
        else:
            med = match
        makehtml += f"<img src='{med}'><br>"
    for line in out.split("\n"):
        line = line.strip()
        if not line:
            makehtml += "<br>"
        elif ":" not in line:
            makehtml += f"<h3>{line}</h3>"
        else:
            makehtml += f"<p>{line}</p>"
    try:
        urll = await fns.make_html_telegraph("Mediainfo", makehtml)
    except Exception as er:
        LOGS.exception(er)
        return

    body = basic
    if meta_block:
        body += f"\n\n{meta_block}"
    body += f"\n\n[{get_string('mdi_1')}]({urll})"
    await msg.eor(body, link_preview=False)
    if show_meta and match.startswith("https://") and local and local != match:
        try:
            os.remove(local)
        except OSError:
            pass


async def _mediainfo_album(e, album, show_meta, taime):
    """Album (grouped media) — info for every file, structured output."""
    count = len(album)
    msg = await e.eor(
        f"**Album detected — {count} files**\n`Processing...`", link_preview=False
    )
    results = []
    for idx, m in enumerate(album, 1):
        info = _basic_info(m)
        naam = None
        try:
            naam = await m.download_media()
        except Exception as exc:
            LOGS.debug("[mediatools] album download failed: %s", exc)
        meta = {}
        if show_meta and naam and os.path.exists(naam):
            meta = await _extract_metadata(naam, info.get("type"))
        results.append((idx, info, naam, meta))

    text = f"**Album Info — {count} files**\n\n{_fmt_summary_table(results)}\n"
    for idx, info, _, meta in results:
        text += f"\n**━━ File {idx}/{count} ━━**\n{_fmt_basic(info)}\n"
        if show_meta:
            text += f"{_fmt_metadata(meta)}\n"

    if len(text) > 4090:
        summary = _fmt_summary_table(results)
        try:
            url = await fns.make_html_telegraph(
                f"Album Mediainfo ({count})", _album_html(results, count)
            )
            await msg.eor(
                f"**Album Info — {count} files**\n\n{summary}\n\n"
                f"**[Full Details]({url})**",
                link_preview=False,
            )
        except Exception as er:
            LOGS.exception(er)
            await msg.eor(
                f"**Album Info — {count} files**\n\n{summary}", link_preview=False
            )
    else:
        await msg.eor(text, link_preview=False)

    for _, _, naam, _ in results:
        if naam and os.path.exists(naam):
            try:
                os.remove(naam)
            except OSError:
                pass


@ultroid_cmd(pattern="rotate( (.*)|$)")
async def rotate_(ult):
    match = ult.pattern_match.group(1).strip()
    if not ult.is_reply:
        return await ult.eor("`Reply to a media...`")
    if match:
        try:
            match = int(match)
        except ValueError:
            match = None
    if not match:
        return await ult.eor("`Please provide a valid angle to rotate media..`")
    reply = await ult.get_reply_message()
    msg = await ult.eor(get_string("com_1"))
    photo = reply.game.photo if reply.game else None
    if reply.video:
        media = await reply.download_media()
        file = f"{media}.mp4"
        await fns.bash(
            f'ffmpeg -i "{media}" -c copy -metadata:s:v:0 rotate={match} "{file}" -y'
        )
    elif photo or reply.photo or reply.sticker:
        media = await ult.client.download_media(photo or reply)
        img = cv2.imread(media)
        new_ = fns.rotate_image(img, match)
        file = "ult.png"
        cv2.imwrite(file, new_)
    else:
        return await msg.edit("`Unsupported Media..\nReply to Photo/Video`")
    if os.path.exists(file):
        await ult.client.send_file(
            ult.chat_id, file=file, video_note=bool(reply.video_note), reply_to=reply.id
        )
    os.remove(media)
    await msg.try_delete()

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""
✘ Commands Available

• ``{i}compress <reply to video> [crf]`` — Compress video using x265.
  ``{i}compress <crf> [stream]`` — Compress with CRF value.
  Add `stream` to upload as video stream.

• ``{i}compressaudio <reply to audio> [bitrate]`` — Compress audio.
• ``{i}compressimage <reply to image> [quality]`` — Compress image.
• ``{i}compressinfo`` — Show compression details.

Note: For zip compression, use `ziptools` plugin instead.

✘ Assistant-bot commands (owner-only):
* ``/compress``, ``/compressaudio``, ``/compressimage`` — same surface.

✘ Inline mode:
* ``@<assistant> compress`` — tap to compress.
"""

import os
import time
import re
import math
import asyncio

from telethon.tl.types import (
    DocumentAttributeVideo,
    UpdateNewMessage,
    UpdateNewChannelMessage,
)

from pyUltroid.fns import fns
from pyUltroid._core._cbs import cb, cb_pattern
from pyUltroid._core.events import InlineResultBuilder
from pyUltroid import ultroid_bot
from custom.tg import ikm
from custom.fmt import fmtmsg

from . import (
    ULTConfig,
    get_string,
    ultroid_cmd,
    asst_cmd,
    eor,
    eod,
    callback,
    in_pattern,
)

# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #


async def _download_for_compress(reply, event, msg):
    """Download a video/audio/image file for compression."""
    if reply.media and hasattr(reply.media, "document"):
        return await fns.downloader(
            reply.file.name or "file.mp4",
            reply.media.document,
            msg,
            time.time(),
            get_string("com_5"),
        )
    return None


async def _compress_upload(file_path, event, msg, to_stream=False):
    """Compress file and upload it."""
    out_msg = None
    try:
        out = await fns.uploader(
            file_path,
            os.path.basename(file_path),
            time.time(),
            msg,
            get_string("com_6"),
        )
        if to_stream:
            data = await fns.metadata(file_path)
            width = data["width"]
            height = data["height"]
            duration = data["duration"]
            attributes = [
                DocumentAttributeVideo(
                    duration=duration,
                    w=width,
                    h=height,
                    supports_streaming=True,
                )
            ]
            await event.client.send_file(
                event.chat_id,
                out,
                thumb=ULTConfig.thumb,
                force_document=False,
                attributes=attributes,
                reply_to=event.reply_to_msg_id,
            )
        else:
            await event.client.send_file(
                event.chat_id,
                out,
                thumb=ULTConfig.thumb,
                force_document=True,
                reply_to=event.reply_to_msg_id,
            )
        out_msg = out
    except Exception:
        try:
            await event.client.send_file(
                event.chat_id,
                file_path,
                thumb=ULTConfig.thumb,
                force_document=True,
                reply_to=event.reply_to_msg_id,
            )
        except Exception:
            pass
    return out_msg


# --------------------------------------------------------------------------- #
#  Video Compression                                                          #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"compress(?:\s+(.*))?$")
async def compress(e):
    """Compress a video using x265 codec."""
    args = (e.pattern_match.group(1) or "").strip()
    crf = 27
    to_stream = False

    if args:
        k = args.split()
        if len(k) >= 1 and k[0].isdigit():
            crf = int(k[0])
        if len(k) >= 2 and k[-1] == "stream":
            to_stream = True

    vido = await e.get_reply_message()
    if not (vido and vido.media and "video" in fns.mediainfo(vido.media)):
        return await eor(
            e,
            fmtmsg("Compress", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    xxx = await e.eor(fmtmsg("Compress", {"Status": "Downloading..."}))
    c_time = time.time()
    file = await _download_for_compress(vido, e, xxx)
    if not file:
        return

    o_size = os.path.getsize(file.name)
    file_name = os.path.basename(file.name)
    out = file_name.rsplit(".", 1)[0] + "_compressed.mkv"

    await xxx.edit(fmtmsg("Compress", {"Status": f"Compressing with CRF {crf}..."}))
    x, y = await fns.bash(f'mediainfo --fullscan "{file.name}" | grep "Frame count"')
    if y and y.endswith("NOT_FOUND"):
        os.remove(file.name)
        return await xxx.edit(
            fmtmsg("Compress", {"Error": y}), parse_mode="html", time=10
        )

    total_frames = x.split(":")[1].split("\n")[0]
    progress_file = f"progress-{c_time}.txt"
    with open(progress_file, "w"):
        pass

    proce = await asyncio.create_subprocess_shell(
        f'ffmpeg -hide_banner -loglevel quiet -progress {progress_file} -i "{file.name}" -preset ultrafast -vcodec libx265 -crf {crf} -c:a copy "{out}" -y',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    while proce.returncode != 0:
        await asyncio.sleep(3)
        with open(progress_file, "r+") as fil:
            text = fil.read()
            frames = re.findall("frame=(\\d+)", text)
            size = re.findall("total_size=(\\d+)", text)
            speed = 0
            elapse = int(frames[-1]) if frames else 0
            if size:
                size = int(size[-1])
                per = elapse * 100 / int(total_frames)
                time_diff = time.time() - c_time
                speed = round(elapse / time_diff, 2) if time_diff > 0 else 0
            if speed != 0:
                some_eta = ((int(total_frames) - elapse) / speed) * 1000
                progress_str = "`[{}{}] {}%\n\n`".format(
                    "".join("●" for _ in range(math.floor(per / 5))),
                    "".join("" for _ in range(20 - math.floor(per / 5))),
                    round(per, 2),
                )
                e_size = f"{fns.humanbytes(size)} of ~{fns.humanbytes((size / per) * 100)}"
                eta = f"~{fns.time_formatter(some_eta)}"
                try:
                    await xxx.edit(
                        fmtmsg(
                            "Compress",
                            {
                                "Progress": f"`{e_size}`",
                                "Bar": progress_str,
                                "ETA": f"`{eta}`",
                            },
                        ),
                        parse_mode="html",
                    )
                except Exception:
                    pass

    os.remove(file.name)
    c_size = os.path.getsize(out)
    f_time = time.time()
    difff = fns.time_formatter((f_time - c_time) * 1000)
    differ = 100 - ((c_size / o_size) * 100)

    caption = f"**Original Size:** `{fns.humanbytes(o_size)}`\n"
    caption += f"**Compressed Size:** `{fns.humanbytes(c_size)}`\n"
    caption += f"**Compression Ratio:** `{differ:.2f}%`\n"
    caption += f"**Time Taken:** `{difff}`"

    await xxx.edit(fmtmsg("Compress", {"Status": "Uploading..."}))
    try:
        await _compress_upload(out, e, xxx, to_stream=to_stream)
    except Exception:
        pass
    os.remove(out)
    try:
        os.remove(progress_file)
    except Exception:
        pass
    await xxx.delete()


# --------------------------------------------------------------------------- #
#  Audio Compression                                                          #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"compressaudio(?:\s+(.*))?$")
async def compressaudio(e):
    """Compress audio file."""
    bitrate = (e.pattern_match.group(1) or "").strip() or "128k"
    reply = await e.get_reply_message()
    if not (
        reply and reply.media and fns.mediainfo(reply.media).startswith(("audio", "video"))
    ):
        return await eor(
            e,
            fmtmsg("Compress Audio", {"Error": "Reply to audio/video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Compress Audio", {"Status": "Downloading..."}))
    file = await _download_for_compress(reply, e, msg)
    if not file:
        return

    o_size = os.path.getsize(file.name)
    file_name = os.path.basename(file.name)
    out = file_name.rsplit(".", 1)[0] + f"_compressed.mp3"

    await msg.edit(fmtmsg("Compress Audio", {"Status": f"Compressing at {bitrate}..."}))
    await fns.bash(f'ffmpeg -i "{file.name}" -b:a {bitrate} -vn "{out}" -y')
    os.remove(file.name)

    c_size = os.path.getsize(out)
    differ = 100 - ((c_size / o_size) * 100)
    try:
        out_msg = await _compress_upload(out, e, msg)
    except Exception:
        pass
    os.remove(out)
    await msg.edit(
        fmtmsg(
            "Compress Audio",
            {
                "Status": "✅ Done",
                "Original": fns.humanbytes(o_size),
                "Compressed": fns.humanbytes(c_size),
                "Ratio": f"{differ:.2f}%",
            },
        ),
        parse_mode="html",
    )


# --------------------------------------------------------------------------- #
#  Image Compression                                                          #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"compressimage(?:\s+(.*))?$")
async def compressimage(e):
    """Compress an image."""
    quality = (e.pattern_match.group(1) or "").strip() or "85"
    try:
        quality = int(quality)
    except ValueError:
        return await eor(
            e,
            fmtmsg("Compress Image", {"Error": "Quality must be a number (1-100)"}),
            parse_mode="html",
            time=10,
        )
    quality = max(1, min(100, quality))

    reply = await e.get_reply_message()
    if not (reply and reply.media):
        return await eor(
            e,
            fmtmsg("Compress Image", {"Error": "Reply to an image"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Compress Image", {"Status": "Downloading..."}))
    file = await _download_for_compress(reply, e, msg)
    if not file:
        return

    o_size = os.path.getsize(file.name)
    file_name = os.path.basename(file.name)
    out = file_name.rsplit(".", 1)[0] + f"_compressed{os.path.splitext(file_name)[1]}"

    await msg.edit(
        fmtmsg("Compress Image", {"Status": f"Compressing at quality {quality}..."})
    )
    await fns.bash(
        f'ffmpeg -i "{file.name}" -vf "scale=iw*0.5:ih*0.5" -q:v {quality} "{out}" -y'
    )
    os.remove(file.name)

    if not os.path.exists(out):
        return await eor(
            msg,
            fmtmsg("Compress Image", {"Error": "Compression failed"}),
            parse_mode="html",
            time=10,
        )

    c_size = os.path.getsize(out)
    differ = 100 - ((c_size / o_size) * 100)
    try:
        await _compress_upload(out, e, msg)
    except Exception:
        pass
    os.remove(out)
    await msg.edit(
        fmtmsg(
            "Compress Image",
            {
                "Status": "✅ Done",
                "Original": fns.humanbytes(o_size),
                "Compressed": fns.humanbytes(c_size),
                "Ratio": f"{differ:.2f}%",
                "Quality": f"{quality}%",
            },
        ),
        parse_mode="html",
    )


# --------------------------------------------------------------------------- #
#  Compress Info                                                              #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern="compressinfo$")
async def compressinfo(e):
    """Show compression info and available formats."""
    await e.eor(
        fmtmsg(
            "Compress Info",
            {
                "Video": "x265 CRF (default 27, range 0-51)",
                "Audio": "Bitrate based (default 128k)",
                "Image": "Quality based (default 85%, range 1-100)",
                "Zip": "Use ziptools plugin",
                "Stream": "Add 'stream' flag for video streaming",
            },
        ),
        parse_mode="html",
        time=10,
    )


# --------------------------------------------------------------------------- #
#  Assistant-bot commands (owner-only)                                      #
# --------------------------------------------------------------------------- #


@asst_cmd(pattern=r"compress(?:\s+(.*))?", owner=True)
async def asst_compress(e):
    await e.reply(
        fmtmsg("Compress", {"Note": "Use /compress on user account"}), parse_mode="html"
    )


@asst_cmd(pattern="compressaudio$", owner=True)
async def asst_compressaudio(e):
    await e.reply(
        fmtmsg("Compress Audio", {"Note": "Use /compressaudio on user account"}),
        parse_mode="html",
    )


@asst_cmd(pattern="compressimage$", owner=True)
async def asst_compressimage(e):
    await e.reply(
        fmtmsg("Compress Image", {"Note": "Use /compressimage on user account"}),
        parse_mode="html",
    )


# --------------------------------------------------------------------------- #
#  Inline mode                                                              #
# --------------------------------------------------------------------------- #


@in_pattern(r"compress(.*)", owner=True)
async def compress_inline(event):
    query = (event.pattern_match.group(1) or "").strip()
    builder = InlineResultBuilder(event)
    if not query:
        article = await builder.article(
            title="Compress",
            text="Reply to a file and tap to compress.",
            description="Compress video/audio/image",
        )
        return await event.answer([article] if article else [])
    key = _store_pending({"text": query, "receiver": None})
    buttons = ikm([[("📦 Compress", cb("assistant", "compress", key), "primary")]])
    article = await builder.article(
        title="Compress",
        text="Tap to compress.",
        description="Compress media",
        buttons=buttons,
    )
    await event.answer([article] if article else [])


@callback(data=cb_pattern("assistant", "compress"), owner=True)
async def compress_inline_send(event):
    await _inline_deliver(event, action="compress")


async def _inline_deliver(event, *, action):
    key = _pending_tail(event)
    pending = _PENDING.pop(key, None)
    if not pending or time.time() > pending.get("exp", 0):
        return await event.answer("Expired — send the inline query again.", alert=True)
    text = pending.get("text", "")
    try:
        if action == "compress":
            await event.answer("Use /compress on your account.", alert=True)
    except Exception as er:
        return await event.answer(f"Failed: {er}", alert=True)


_PENDING = {}
_PENDING_TTL = 300
_SEQ = 0


def _prune_pending():
    now = time.time()
    for k in list(_PENDING.keys()):
        if now > _PENDING[k].get("exp", 0):
            _PENDING.pop(k, None)


def _store_pending(payload):
    global _SEQ
    _prune_pending()
    _SEQ += 1
    key = str(_SEQ)
    payload["exp"] = time.time() + _PENDING_TTL
    _PENDING[key] = payload
    return key


def _pending_tail(event):
    tail = event.data_match.group(1)
    return tail.decode("utf-8") if isinstance(tail, bytes) else str(tail)


# --------------------------------------------------------------------------- #
#  Session listener (non-intrusive)                                         #
# --------------------------------------------------------------------------- #


async def _on_compressor_update(update):
    pass


def _register_listeners():
    from pyUltroid.startup.dispatcher import register_listen

    register_listen(
        _on_compressor_update,
        client=ultroid_bot,
        listen=(UpdateNewMessage, UpdateNewChannelMessage),
        once=False,
        name="compressor_updates",
    )


_register_listeners()

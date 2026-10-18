# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""
✘ Commands Available

• ``{i}sample <duration>`` — Create a short sample of a video (reply to video).
• ``{i}vshots <count>`` — Create screenshots of a video (reply to video).
• ``{i}vtrim <start>-<end>`` — Trim a video (reply to video).
• ``{i}vmerge`` — Merge videos (reply to multiple videos).
• ``{i}vtext <text>`` — Add text overlay to a video (reply to video).
• ``{i}vbg <color>`` — Change background color of a video (reply to video).
• ``{i}vconvert <format>`` — Convert video to another format (reply to video).
• ``{i}vspeed <speed>`` — Change playback speed (reply to video).
• ``{i}vreverse`` — Reverse a video (reply to video).
• ``{i}vextract`` — Extract audio from a video (reply to video).
• ``{i}vwatermark <text>`` — Add watermark to a video (reply to video).
• ``{i}vrotate <degrees>`` — Rotate a video (reply to video).
• ``{i}vresize <width>x<height>`` — Resize a video (reply to video).
• ``{i}vformat`` — Get detailed format info of a video (reply to video).
• ``{i}vconcat`` — Concatenate multiple videos (reply to videos).

✘ Assistant-bot commands (owner-only):
* ``/sample``, ``/vtrim``, ``/vmerge``, ``/vtext``, etc. — same surface.

✘ Inline mode (selection via assistant, delivery via user account):
* ``@<assistant> vtrim 0-10`` — tap to trim.
"""

import os
import time
import glob
import subprocess

from pyUltroid.fns import fns

from . import (
    ULTConfig,
    eod,
    eor,
    get_string,
    ultroid_cmd,
    asst_cmd,
)

from custom.fmt import fmtmsg, fmtbyte
from custom.entities import parse_rich

# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #


def _video_check(reply):
    """Return (is_video, info) for a reply message."""
    if not (reply and reply.media):
        return False, None
    info = fns.mediainfo(reply.media)
    if info == "video" or info == "video as doc" or info == "gif":
        return True, info
    return False, info


async def _download_video(reply, event):
    """Download a video file from a reply message."""
    try:
        file, _ = await event.client.fast_downloader(
            reply.document, show_progress=True, event=event
        )
        return file
    except Exception as er:
        raise Exception(f"Download failed: {er}")


async def _upload_file(file_path, event, name=None, thumb=ULTConfig.thumb):
    """Upload a file with progress tracking."""
    try:
        attributes = await fns.set_attributes(file_path)
        out, _ = await event.client.fast_uploader(
            file_path, show_progress=True, event=event, to_delete=True
        )
        return out, attributes
    except Exception as er:
        raise Exception(f"Upload failed: {er}")


async def _run_ffmpeg(cmd, progress_msg=None):
    """Run an ffmpeg command with proper error handling."""
    try:
        out, err = await fns.bash(cmd)
        if err and "Error" in err:
            return False, err
        return True, out
    except Exception as er:
        return False, str(er)


def _get_video_info(file_path):
    """Get detailed metadata about a video file."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        import json as _json

        data = _json.loads(result.stdout)
        streams = data.get("streams", [])
        video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), {})
        fmt = data.get("format", {})
        return {
            "filename": os.path.basename(file_path),
            "duration": float(fmt.get("duration", 0)),
            "size": int(fmt.get("size", 0)),
            "format_name": fmt.get("format_name", "unknown"),
            "video_codec": video_stream.get("codec_name", "none"),
            "video_width": video_stream.get("width", 0),
            "video_height": video_stream.get("height", 0),
            "video_frame_rate": video_stream.get("r_frame_rate", "0/0"),
            "audio_codec": audio_stream.get("codec_name", "none"),
            "audio_sample_rate": audio_stream.get("sample_rate", "0"),
            "audio_channels": audio_stream.get("channels", "0"),
        }
    except Exception:
        return {
            "filename": os.path.basename(file_path),
            "error": "Could not read metadata",
        }


def _fmt_video_info(info):
    """Format video info as an HTML string for legacy rich display."""
    if "error" in info:
        return f"<b>Video Info</b>\n<code>{info['error']}</code>"
    rows = {
        "File": info["filename"],
        "Duration": f"{info['duration']:.2f}s",
        "Size": fmtbyte(info["size"]),
        "Format": info["format_name"],
        "Video Codec": info["video_codec"],
        "Resolution": f"{info['video_width']}x{info['video_height']}",
        "Frame Rate": info["video_frame_rate"],
        "Audio Codec": info["audio_codec"] if info["audio_codec"] != "none" else "N/A",
        "Audio Rate": (
            info["audio_sample_rate"] if info["audio_sample_rate"] != "0" else "N/A"
        ),
        "Channels": info["audio_channels"] if info["audio_channels"] != "0" else "N/A",
    }
    padd = max(len(str(k)) for k in rows)
    body = "\n".join(
        f"  <code>{k.ljust(padd)}</code> : <b>{v}</b>" for k, v in rows.items()
    )
    return f"<b>🎬 Video Format</b>\n{body}"


def _fmt_duration(duration):
    """Format duration in seconds to HH:MM:SS."""
    t = int(duration)
    h, m, s = divmod(t, 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


# --------------------------------------------------------------------------- #
#  Sample                                                                     #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern="sample( (.*)|$)")
async def gen_sample(e):
    sec = e.pattern_match.group(1).strip() if e.pattern_match.group(1) else "30"
    stime = int(sec) if sec.isdigit() else 30
    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Sample", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(get_string("com_1"))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Sample", {"Error": str(er)}), parse_mode="html", time=10
        )

    file_name = os.path.basename(file.name if hasattr(file, "name") else file)
    out = file_name.rsplit(".", 1)[0] + "_sample.mkv"
    await msg.edit(fmtmsg("Sample", {"Status": f"Generating {stime}s sample..."}))

    ok, result = await _run_ffmpeg(
        f'ffmpeg -i "{file}" -preset ultrafast -ss 0 -t {stime} -c copy -map 0 "{out}" -y'
    )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Sample", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg(
            "Sample",
            {
                "Status": f"Sample of {stime}s created",
                "File": f"`{os.path.basename(out)}`",
            },
        ),
        attributes=attributes,
        force_document=False,
        reply_to=e.reply_to_msg_id,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Screenshots                                                                #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern="vshots( (.*)|$)")
async def gen_shots(e):
    ss = e.pattern_match.group(1).strip() if e.pattern_match.group(1) else "5"
    shot = int(ss) if ss.isdigit() else 5
    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Screenshots", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(get_string("com_1"))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Screenshots", {"Error": str(er)}), parse_mode="html", time=10
        )

    await msg.edit(
        fmtmsg("Screenshots", {"Status": f"Generating {shot} screenshots..."})
    )
    await fns.bash("rm -rf ss && mkdir ss")
    ok, result = await _run_ffmpeg(
        f'ffmpeg -i "{file}" -vf fps=0.009 -vframes {shot} "ss/pic%01d.png"'
    )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Screenshots", {"Error": result}), parse_mode="html", time=10
        )

    pics = glob.glob("ss/*")
    text = (
        f"✅ Uploaded {len(pics)}/{shot} screenshots"
        if pics
        else "❌ Failed to take screenshots"
    )
    if pics:
        await e.respond(text, file=pics)
    else:
        await eor(
            e, fmtmsg("Screenshots", {"Status": text}), parse_mode="html", time=10
        )
    await fns.bash("rm -rf ss")
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Trim                                                                       #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern="vtrim( (.*)|$)")
async def vtrim(e):
    sec = e.pattern_match.group(1).strip() if e.pattern_match.group(1) else ""
    if not sec or "-" not in sec:
        return await eod(
            e,
            fmtmsg(
                "Trim", {"Usage": f"{e.pattern_match.string.split()[0]} <start>-<end>"}
            ),
            parse_mode="html",
            time=10,
        )
    a, b = sec.split("-", 1)
    try:
        a, b = int(a), int(b)
    except ValueError:
        return await eod(
            e,
            fmtmsg("Trim", {"Error": "Start/end must be numbers"}),
            parse_mode="html",
            time=10,
        )
    if a >= b:
        return await eod(
            e,
            fmtmsg("Trim", {"Error": "Start must be less than end"}),
            parse_mode="html",
            time=10,
        )

    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e, fmtmsg("Trim", {"Error": "Reply to a video"}), parse_mode="html", time=10
        )

    msg = await e.eor(fmtmsg("Trim", {"Status": "Downloading..."}))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Trim", {"Error": str(er)}), parse_mode="html", time=10
        )

    file_name = os.path.basename(file.name if hasattr(file, "name") else file)
    out = file_name.rsplit(".", 1)[0] + "_trimmed.mkv"
    dur = await fns.genss(file)
    if b > dur:
        os.remove(file)
        return await eod(
            msg,
            fmtmsg("Trim", {"Error": "End time exceeds video duration"}),
            parse_mode="html",
            time=10,
        )

    ss, dd = fns.stdr(a), fns.stdr(b)
    await msg.edit(fmtmsg("Trim", {"Status": f"Trimming {ss} → {dd}..."}))
    ok, result = await _run_ffmpeg(
        f'ffmpeg -i "{file}" -preset ultrafast -ss {ss} -to {dd} -c copy -map 0 "{out}" -y'
    )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Trim", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg(
            "Trim",
            {
                "Status": "Trimmed",
                "Range": f"{ss} → {dd}",
                "File": f"`{os.path.basename(out)}`",
            },
        ),
        attributes=attributes,
        force_document=False,
        reply_to=e.reply_to_msg_id,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Merge                                                                      #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern="vmerge$")
async def vmerge(e):
    vidos = []
    async for m in e.client.iter_messages(e.chat_id, from_user="me", limit=10):
        if m.media and fns.mediainfo(m.media) in ("video", "video as doc", "gif"):
            vidos.append(m)
    if not vidos:
        return await eor(
            e,
            fmtmsg("Merge", {"Error": "Reply to multiple videos to merge"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Merge", {"Status": "Downloading videos..."}))
    files = []
    for vido in vidos:
        try:
            f = await _download_video(vido, msg)
            files.append(f)
        except Exception as er:
            await msg.edit(fmtmsg("Merge", {"Error": f"Failed to download: {er}"}))
            for f in files:
                os.remove(f)
            return

    await msg.edit(fmtmsg("Merge", {"Status": f"Merging {len(files)} videos..."}))
    list_file = "merge_list.txt"
    with open(list_file, "w") as f:
        for fi in files:
            f.write(f"file '{fi}'\n")
    out = "merged.mkv"
    ok, result = await _run_ffmpeg(
        f'ffmpeg -f concat -safe 0 -i {list_file} -c copy "{out}" -y'
    )
    for f in files:
        os.remove(f)
    os.remove(list_file)
    if not ok:
        return await eor(
            e, fmtmsg("Merge", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg(
            "Merge",
            {
                "Status": "Merged",
                "Files": f"{len(files)} videos",
                "File": f"`{os.path.basename(out)}`",
            },
        ),
        attributes=attributes,
        force_document=False,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Text Overlay                                                               #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"vtext(?:\s+(.*))?$")
async def vtext(e):
    text = (e.pattern_match.group(1) or "").strip() if e.pattern_match.group(1) else ""
    if not text:
        return await eor(
            e,
            fmtmsg(
                "Text Overlay", {"Usage": f"{e.pattern_match.string.split()[0]} <text>"}
            ),
            parse_mode="html",
            time=10,
        )

    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Text Overlay", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Text Overlay", {"Status": "Downloading..."}))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Text Overlay", {"Error": str(er)}), parse_mode="html", time=10
        )

    file_name = os.path.basename(file.name if hasattr(file, "name") else file)
    out = file_name.rsplit(".", 1)[0] + "_text.mkv"
    safe_text = text.replace("'", "'\\''")
    await msg.edit(fmtmsg("Text Overlay", {"Status": f"Adding text: '{text}'..."}))
    ok, result = await _run_ffmpeg(
        f'ffmpeg -i "{file}" -vf "drawtext=text=\'{safe_text}\':fontcolor=white:fontsize=24:x=(w-text_w)/2:y=(h-text_h)/2" -c:a copy "{out}" -y'
    )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Text Overlay", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg("Text Overlay", {"Status": "Text added", "Text": f"`{text}`"}),
        attributes=attributes,
        force_document=False,
        reply_to=e.reply_to_msg_id,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Background Change                                                          #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"vbg(?:\s+(.*))?$")
async def vbg(e):
    color = (
        (e.pattern_match.group(1) or "black").strip()
        if e.pattern_match.group(1)
        else "black"
    )
    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Background", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Background", {"Status": "Downloading..."}))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Background", {"Error": str(er)}), parse_mode="html", time=10
        )

    file_name = os.path.basename(file.name if hasattr(file, "name") else file)
    out = file_name.rsplit(".", 1)[0] + "_bg.mkv"
    await msg.edit(
        fmtmsg("Background", {"Status": f"Changing background to {color}..."})
    )
    ok, result = await _run_ffmpeg(
        f'ffmpeg -i "{file}" -vf "color={color}:s=1920x1080[bg];[0:v][bg]overlay=(W-w)/2:(H-h)/2" -c:a copy "{out}" -y'
    )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Background", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg(
            "Background", {"Status": "Background changed", "Color": f"`{color}`"}
        ),
        attributes=attributes,
        force_document=False,
        reply_to=e.reply_to_msg_id,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Convert                                                                    #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"vconvert(?:\s+(.*))?$")
async def vconvert(e):
    fmt = (e.pattern_match.group(1) or "").strip() if e.pattern_match.group(1) else ""
    if not fmt:
        return await eor(
            e,
            fmtmsg(
                "Convert",
                {
                    "Usage": f"{e.pattern_match.string.split()[0]} <mp4|mkv|avi|mov|webm>"
                },
            ),
            parse_mode="html",
            time=10,
        )

    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Convert", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Convert", {"Status": "Downloading..."}))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Convert", {"Error": str(er)}), parse_mode="html", time=10
        )

    file_name = os.path.basename(file.name if hasattr(file, "name") else file)
    out = file_name.rsplit(".", 1)[0] + f".{fmt}"
    await msg.edit(fmtmsg("Convert", {"Status": f"Converting to {fmt.upper()}..."}))
    ok, result = await _run_ffmpeg(
        f'ffmpeg -i "{file}" -c:v libx264 -preset fast -c:a aac "{out}" -y'
    )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Convert", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg(
            "Convert",
            {
                "Status": f"Converted to {fmt.upper()}",
                "File": f"`{os.path.basename(out)}`",
            },
        ),
        attributes=attributes,
        force_document=False,
        reply_to=e.reply_to_msg_id,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Speed Change                                                               #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"vspeed(?:\s+(.*))?$")
async def vspeed(e):
    speed = (
        (e.pattern_match.group(1) or "").strip() if e.pattern_match.group(1) else "1.0"
    )
    try:
        speed_val = float(speed)
    except ValueError:
        return await eor(
            e,
            fmtmsg("Speed", {"Error": "Speed must be a number"}),
            parse_mode="html",
            time=10,
        )
    if speed_val <= 0:
        return await eor(
            e,
            fmtmsg("Speed", {"Error": "Speed must be positive"}),
            parse_mode="html",
            time=10,
        )

    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Speed", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Speed", {"Status": f"Setting speed to {speed_val}x..."}))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Speed", {"Error": str(er)}), parse_mode="html", time=10
        )

    file_name = os.path.basename(file.name if hasattr(file, "name") else file)
    out = file_name.rsplit(".", 1)[0] + f"_speed{speed}.mkv"
    await msg.edit(fmtmsg("Speed", {"Status": f"Changing speed to {speed_val}x..."}))
    if speed_val == 1.0:
        ok, result = await _run_ffmpeg(f'ffmpeg -i "{file}" -c copy "{out}" -y')
    elif speed_val < 1.0:
        ok, result = await _run_ffmpeg(
            f'ffmpeg -i "{file}" -filter:v "setpts={speed_val}*PTS" -filter:a "atempo={speed_val}" -c:a copy "{out}" -y'
        )
    else:
        ok, result = await _run_ffmpeg(
            f'ffmpeg -i "{file}" -filter:v "setpts={1/speed_val}*PTS" -filter:a "atempo={speed_val}" -c:a copy "{out}" -y'
        )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Speed", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg(
            "Speed",
            {
                "Status": f"Speed changed to {speed_val}x",
                "File": f"`{os.path.basename(out)}`",
            },
        ),
        attributes=attributes,
        force_document=False,
        reply_to=e.reply_to_msg_id,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Reverse                                                                    #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern="vreverse$")
async def vreverse(e):
    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Reverse", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Reverse", {"Status": "Downloading..."}))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Reverse", {"Error": str(er)}), parse_mode="html", time=10
        )

    file_name = os.path.basename(file.name if hasattr(file, "name") else file)
    out = file_name.rsplit(".", 1)[0] + "_reverse.mkv"
    await msg.edit(fmtmsg("Reverse", {"Status": "Reversing video..."}))
    ok, result = await _run_ffmpeg(
        f'ffmpeg -i "{file}" -vf "reverse" -af "areverse" -c:v libx264 -preset fast -c:a aac "{out}" -y'
    )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Reverse", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg(
            "Reverse",
            {"Status": "Video reversed", "File": f"`{os.path.basename(out)}`"},
        ),
        attributes=attributes,
        force_document=False,
        reply_to=e.reply_to_msg_id,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Extract Audio                                                              #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern="vextract$")
async def vextract(e):
    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Extract Audio", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Extract Audio", {"Status": "Downloading..."}))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Extract Audio", {"Error": str(er)}), parse_mode="html", time=10
        )

    file_name = os.path.basename(file.name if hasattr(file, "name") else file)
    out = file_name.rsplit(".", 1)[0] + ".mp3"
    await msg.edit(fmtmsg("Extract Audio", {"Status": "Extracting audio..."}))
    ok, result = await _run_ffmpeg(
        f'ffmpeg -i "{file}" -vn -acodec libmp3lame -q:a 2 "{out}" -y'
    )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Extract Audio", {"Error": result}), parse_mode="html", time=10
        )

    try:
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg(
            "Extract Audio",
            {"Status": "Audio extracted", "File": f"`{os.path.basename(out)}`"},
        ),
        force_document=False,
        reply_to=e.reply_to_msg_id,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Watermark                                                                  #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"vwatermark(?:\s+(.*))?$")
async def vwatermark(e):
    text = (
        (e.pattern_match.group(1) or "").strip()
        if e.pattern_match.group(1)
        else "Ultroid"
    )
    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Watermark", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Watermark", {"Status": "Downloading..."}))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Watermark", {"Error": str(er)}), parse_mode="html", time=10
        )

    file_name = os.path.basename(file.name if hasattr(file, "name") else file)
    out = file_name.rsplit(".", 1)[0] + "_watermark.mkv"
    await msg.edit(fmtmsg("Watermark", {"Status": f"Adding watermark: '{text}'..."}))
    safe_text = text.replace("'", "'\\''")
    ok, result = await _run_ffmpeg(
        f'ffmpeg -i "{file}" -vf "drawtext=text=\'{safe_text}\':fontcolor=white:fontsize=20:x=10:y=10:enable=\'between(t\\\\,0\\\\,30)\'" -c:a copy "{out}" -y'
    )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Watermark", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg("Watermark", {"Status": "Watermark added", "Text": f"`{text}`"}),
        attributes=attributes,
        force_document=False,
        reply_to=e.reply_to_msg_id,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Rotate                                                                     #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"vrotate(?:\s+(.*))?$")
async def vrotate(e):
    deg = (
        (e.pattern_match.group(1) or "90").strip() if e.pattern_match.group(1) else "90"
    )
    try:
        deg_val = int(deg)
    except ValueError:
        return await eor(
            e,
            fmtmsg("Rotate", {"Error": "Degrees must be a number"}),
            parse_mode="html",
            time=10,
        )

    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Rotate", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Rotate", {"Status": "Downloading..."}))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Rotate", {"Error": str(er)}), parse_mode="html", time=10
        )

    file_name = os.path.basename(file.name if hasattr(file, "name") else file)
    out = file_name.rsplit(".", 1)[0] + f"_rot{deg}.mkv"
    await msg.edit(fmtmsg("Rotate", {"Status": f"Rotating {deg}°..."}))
    ok, result = await _run_ffmpeg(
        f'ffmpeg -i "{file}" -vf "transpose={deg_val // 90 % 4}" -c:a copy "{out}" -y'
    )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Rotate", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg(
            "Rotate",
            {"Status": f"Rotated {deg}°", "File": f"`{os.path.basename(out)}`"},
        ),
        attributes=attributes,
        force_document=False,
        reply_to=e.reply_to_msg_id,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Resize                                                                     #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"vresize(?:\s+(.*))?$")
async def vresize(e):
    dims = (e.pattern_match.group(1) or "").strip() if e.pattern_match.group(1) else ""
    if not dims or "x" not in dims:
        return await eor(
            e,
            fmtmsg(
                "Resize",
                {"Usage": f"{e.pattern_match.string.split()[0]} <width>x<height>"},
            ),
            parse_mode="html",
            time=10,
        )

    w, h = dims.split("x", 1)
    try:
        w, h = int(w), int(h)
    except ValueError:
        return await eor(
            e,
            fmtmsg("Resize", {"Error": "Dimensions must be numbers"}),
            parse_mode="html",
            time=10,
        )

    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Resize", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Resize", {"Status": "Downloading..."}))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Resize", {"Error": str(er)}), parse_mode="html", time=10
        )

    file_name = os.path.basename(file.name if hasattr(file, "name") else file)
    out = file_name.rsplit(".", 1)[0] + f"_resized{w}x{h}.mkv"
    await msg.edit(fmtmsg("Resize", {"Status": f"Resizing to {w}x{h}..."}))
    ok, result = await _run_ffmpeg(
        f'ffmpeg -i "{file}" -vf "scale={w}:{h}" -c:a copy "{out}" -y'
    )
    os.remove(file)
    if not ok:
        return await eor(
            e, fmtmsg("Resize", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg(
            "Resize",
            {"Status": f"Resized to {w}x{h}", "File": f"`{os.path.basename(out)}`"},
        ),
        attributes=attributes,
        force_document=False,
        reply_to=e.reply_to_msg_id,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Format Info                                                                #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern="vformat$")
async def vformat(e):
    vido = await e.get_reply_message()
    is_video, info = _video_check(vido)
    if not is_video:
        return await eor(
            e,
            fmtmsg("Format", {"Error": "Reply to a video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(get_string("com_1"))
    try:
        file = await _download_video(vido, msg)
    except Exception as er:
        return await eor(
            e, fmtmsg("Format", {"Error": str(er)}), parse_mode="html", time=10
        )

    vid_info = _get_video_info(file)
    html = _fmt_video_info(vid_info)
    await eor(e, fmtmsg("Video Format", {"Info": html}), parse_mode="html", time=12)
    os.remove(file)


# --------------------------------------------------------------------------- #
#  Concatenate                                                                #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern="vconcat$")
async def vconcat(e):
    vidos = []
    async for m in e.client.iter_messages(e.chat_id, from_user="me", limit=10):
        if m.media and fns.mediainfo(m.media) in ("video", "video as doc", "gif"):
            vidos.append(m)
    if not vidos:
        return await eor(
            e,
            fmtmsg("Concat", {"Error": "Reply to multiple videos to concatenate"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Concat", {"Status": "Downloading videos..."}))
    files = []
    for vido in vidos:
        try:
            f = await _download_video(vido, msg)
            files.append(f)
        except Exception as er:
            await msg.edit(fmtmsg("Concat", {"Error": f"Failed: {er}"}))
            for f in files:
                os.remove(f)
            return

    await msg.edit(
        fmtmsg("Concat", {"Status": f"Concatenating {len(files)} videos..."})
    )
    list_file = "concat_list.txt"
    with open(list_file, "w") as f:
        for fi in files:
            f.write(f"file '{fi}'\n")
    out = "concatenated.mkv"
    ok, result = await _run_ffmpeg(
        f'ffmpeg -f concat -safe 0 -i {list_file} -c copy "{out}" -y'
    )
    for f in files:
        os.remove(f)
    os.remove(list_file)
    if not ok:
        return await eor(
            e, fmtmsg("Concat", {"Error": result}), parse_mode="html", time=10
        )

    try:
        attributes = await fns.set_attributes(out)
        out_msg, _ = await e.client.fast_uploader(
            out, show_progress=True, event=msg, to_delete=True
        )
    except Exception:
        out_msg = out

    await e.client.send_file(
        e.chat_id,
        out_msg,
        thumb=ULTConfig.thumb,
        caption=fmtmsg(
            "Concat",
            {
                "Status": "Concatenated",
                "Videos": f"{len(files)}",
                "File": f"`{os.path.basename(out)}`",
            },
        ),
        attributes=attributes,
        force_document=False,
    )
    os.remove(out)
    await msg.delete()


# --------------------------------------------------------------------------- #
#  Assistant-bot commands (owner-only)                                        #
# --------------------------------------------------------------------------- #


@asst_cmd(pattern=r"sample(?:\s+(.*))?", owner=True)
async def asst_sample(e):
    await e.reply(fmtmsg("Sample", {"Note": "Use on user account"}), parse_mode="html")


@asst_cmd(pattern=r"vtrim(?:\s+(.*))?", owner=True)
async def asst_vtrim(e):
    await e.reply(fmtmsg("Trim", {"Note": "Use on user account"}), parse_mode="html")


@asst_cmd(pattern=r"vmerge$", owner=True)
async def asst_merge(e):
    await e.reply(fmtmsg("Merge", {"Note": "Use on user account"}), parse_mode="html")


@asst_cmd(pattern=r"vtext(?:\s+(.*))?", owner=True)
async def asst_vtext(e):
    await e.reply(
        fmtmsg("Text Overlay", {"Note": "Use on user account"}), parse_mode="html"
    )


@asst_cmd(pattern=r"vbg(?:\s+(.*))?", owner=True)
async def asst_vbg(e):
    await e.reply(
        fmtmsg("Background", {"Note": "Use on user account"}), parse_mode="html"
    )


@asst_cmd(pattern=r"vconvert(?:\s+(.*))?", owner=True)
async def asst_vconvert(e):
    await e.reply(fmtmsg("Convert", {"Note": "Use on user account"}), parse_mode="html")


@asst_cmd(pattern=r"vspeed(?:\s+(.*))?", owner=True)
async def asst_vspeed(e):
    await e.reply(fmtmsg("Speed", {"Note": "Use on user account"}), parse_mode="html")


@asst_cmd(pattern=r"vreverse$", owner=True)
async def asst_vreverse(e):
    await e.reply(fmtmsg("Reverse", {"Note": "Use on user account"}), parse_mode="html")


@asst_cmd(pattern=r"vextract$", owner=True)
async def asst_vextract(e):
    await e.reply(
        fmtmsg("Extract Audio", {"Note": "Use on user account"}), parse_mode="html"
    )


@asst_cmd(pattern=r"vwatermark(?:\s+(.*))?", owner=True)
async def asst_vwatermark(e):
    await e.reply(
        fmtmsg("Watermark", {"Note": "Use on user account"}), parse_mode="html"
    )


@asst_cmd(pattern=r"vrotate(?:\s+(.*))?", owner=True)
async def asst_vrotate(e):
    await e.reply(fmtmsg("Rotate", {"Note": "Use on user account"}), parse_mode="html")


@asst_cmd(pattern=r"vresize(?:\s+(.*))?", owner=True)
async def asst_vresize(e):
    await e.reply(fmtmsg("Resize", {"Note": "Use on user account"}), parse_mode="html")


@asst_cmd(pattern=r"vformat$", owner=True)
async def asst_vformat(e):
    await e.reply(fmtmsg("Format", {"Note": "Use on user account"}), parse_mode="html")


@asst_cmd(pattern=r"vconcat$", owner=True)
async def asst_vconcat(e):
    await e.reply(fmtmsg("Concat", {"Note": "Use on user account"}), parse_mode="html")

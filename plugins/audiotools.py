# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""
✘ Commands Available

• `{i}makevoice`
        Create a voice note from audio/video (reply).

• `{i}atrim <start>-<end>`
        Trim audio (reply).

• `{i}extractaudio`
        Extract audio from video (reply).

• `{i}audioinfo`
        Show detailed audio information.

• `{i}audioconvert <format>`
        Convert audio format (mp3, wav, ogg, aac, flac).

• `{i}audiosplit <timestamp>`
        Split audio at timestamp.

• `{i}audioreverse`
        Reverse the audio.

• `{i}audiobpm`
        Show BPM of audio.

• `{i}audiomerge`
        Merge multiple audio files.

• `{i}audionormalize`
        Normalize audio volume.

• `{i}audioch <channel>`
        Change audio channels (mono/stereo).
"""

import os
import time
import subprocess
import json

from pyUltroid.fns import fns

from . import (
    HNDLR,
    ULTConfig,
    eor,
    eod,
    get_string,
    ultroid_cmd,
)

from custom.fmt import fmtmsg


async def _download_audio(reply, event, msg):
    """Download audio/video file from reply."""
    if reply.media and hasattr(reply.media, "document"):
        return await fns.downloader(
            reply.file.name or "audio.mp3",
            reply.media.document,
            msg,
            time.time(),
            get_string("com_5"),
        )
    return None


async def _upload_audio(file_path, event, msg):
    """Upload audio file and send it."""
    try:
        out = await fns.uploader(
            file_path, file_path, time.time(), msg, get_string("com_6")
        )
        attributes = await fns.set_attributes(file_path)
        await event.client.send_file(
            event.chat_id,
            out,
            thumb=ULTConfig.thumb,
            caption=f"`{out.name}`",
            attributes=attributes,
            force_document=False,
            reply_to=event.reply_to_msg_id,
        )
        return out
    except Exception as er:
        raise Exception(str(er))


@ultroid_cmd(pattern="makevoice$")
async def makevoice(e):
    """Create a voice note from audio/video."""
    r = await e.get_reply_message()
    if not r or not fns.mediainfo(r.media).startswith(("audio", "video")):
        return await eod(
            e,
            fmtmsg("Make Voice", {"Error": "Reply to audio or video"}),
            parse_mode="html",
            time=10,
        )

    xxx = await e.eor(fmtmsg("Make Voice", {"Status": "Downloading..."}))
    file, _ = await e.client.fast_downloader(r.document)
    await xxx.edit(fmtmsg("Make Voice", {"Status": "Converting to voice..."}))

    try:
        await fns.bash(
            f"ffmpeg -i '{file.name}' -map 0:a -codec:a libopus -b:a 100k -vbr on out.opus"
        )
        await e.client.send_message(
            e.chat_id, file="out.opus", force_document=False, reply_to=r
        )
    except Exception as er:
        await xxx.edit(
            fmtmsg("Make Voice", {"Error": "Failed to convert to voice"}),
            parse_mode="html",
        )
        return

    await xxx.delete()
    os.remove(file.name)
    try:
        os.remove("out.opus")
    except Exception:
        pass


@ultroid_cmd(pattern=r"atrim(?:\s+(.*))?$")
async def atrim(e):
    """Trim audio between start and end times."""
    sec = (e.pattern_match.group(1) or "").strip()
    if not sec or "-" not in sec:
        return await eod(
            e,
            fmtmsg("Audio Trim", {"Usage": f"{HNDLR}atrim <start>-<end>"}),
            parse_mode="html",
            time=10,
        )
    a, b = sec.split("-", 1)
    try:
        a, b = int(a), int(b)
    except ValueError:
        return await eod(
            e,
            fmtmsg("Audio Trim", {"Error": "Start/end must be numbers"}),
            parse_mode="html",
            time=10,
        )
    if a >= b:
        return await eod(
            e,
            fmtmsg("Audio Trim", {"Error": "Start must be less than end"}),
            parse_mode="html",
            time=10,
        )

    vido = await e.get_reply_message()
    if not (
        vido and vido.media and fns.mediainfo(vido.media).startswith(("audio", "video"))
    ):
        return await eor(
            e,
            fmtmsg("Audio Trim", {"Error": "Reply to audio/video"}),
            parse_mode="html",
            time=10,
        )

    name = vido.file.name or "audio.mp3"
    xxx = await e.eor(fmtmsg("Audio Trim", {"Status": "Downloading..."}))
    file = await _download_audio(vido, e, xxx)
    if not file:
        return

    o_size = os.path.getsize(file.name)
    d_time = time.time()
    diff = fns.time_formatter((d_time - d_time) * 1000)
    file_name = os.path.basename(file.name)
    out = file_name.rsplit(".", 1)[0] + "_trimmed.aac"

    if b > await fns.genss(file.name):
        os.remove(file.name)
        return await eod(
            xxx,
            fmtmsg("Audio Trim", {"Error": "End exceeds audio duration"}),
            parse_mode="html",
            time=10,
        )

    ss, dd = fns.stdr(a), fns.stdr(b)
    await xxx.edit(fmtmsg("Audio Trim", {"Status": f"Trimming {ss} → {dd}..."}))
    await fns.bash(
        f'ffmpeg -i "{file.name}" -preset ultrafast -ss {ss} -to {dd} -vn -acodec copy "{out}" -y'
    )
    os.remove(file.name)

    f_time = time.time()
    try:
        mmmm = await fns.uploader(out, out, f_time, xxx, f"Uploading {out}...")
        attributes = await fns.set_attributes(out)
        await e.client.send_file(
            e.chat_id,
            mmmm,
            thumb=ULTConfig.thumb,
            caption=get_string("audiotools_7").format(ss, dd),
            attributes=attributes,
            force_document=False,
            reply_to=e.reply_to_msg_id,
        )
    except Exception:
        pass
    os.remove(out)
    await xxx.delete()


@ultroid_cmd(pattern="extractaudio$")
async def extractaudio(e):
    """Extract audio from a video."""
    reply = await e.get_reply_message()
    if not (reply and reply.media and fns.mediainfo(reply.media).startswith("video")):
        return await eor(
            e,
            fmtmsg("Extract Audio", {"Error": get_string("audiotools_8")}),
            parse_mode="html",
            time=10,
        )

    name = reply.file.name or "video.mp4"
    msg = await e.eor(fmtmsg("Extract Audio", {"Status": "Downloading..."}))
    file = await _download_audio(reply, e, msg)
    if not file:
        return

    out_file = f"{file.name}.aac"
    await fns.bash(f"ffmpeg -i {file.name} -vn -acodec copy {out_file}")
    os.remove(file.name)

    try:
        attributes = await fns.set_attributes(out_file)
        await _upload_audio(out_file, e, msg)
    except Exception:
        pass
    os.remove(out_file)
    await msg.delete()


@ultroid_cmd(pattern="audioinfo$")
async def audioinfo(e):
    """Show detailed audio information."""
    reply = await e.get_reply_message()
    if not (reply and reply.media):
        return await eor(
            e,
            fmtmsg("Audio Info", {"Error": "Reply to audio"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Audio Info", {"Status": "Fetching info..."}))
    file = await _download_audio(reply, e, msg)
    if not file:
        return

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
                file.name,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        data = json.loads(result.stdout)
        fmt_data = data.get("format", {})
        audio_stream = next(
            (s for s in data.get("streams", []) if s.get("codec_type") == "audio"), {}
        )

        rows = {
            "File": os.path.basename(file.name),
            "Duration": f"{float(fmt_data.get('duration', 0)):.2f}s",
            "Size": f"{int(fmt_data.get('size', 0)) / 1024 / 1024:.2f} MB",
            "Format": fmt_data.get("format_name", "unknown"),
            "Codec": audio_stream.get("codec_name", "none"),
            "Sample Rate": f"{audio_stream.get('sample_rate', '0')} Hz",
            "Channels": audio_stream.get("channels", "0"),
            "Bit Rate": f"{int(fmt_data.get('bit_rate', 0)) / 1000:.0f} kbps",
        }
        padd = max(len(str(k)) for k in rows)
        body = "\n".join(
            f"  <code>{k.ljust(padd)}</code> : <b>{v}</b>" for k, v in rows.items()
        )
        await msg.edit(fmtmsg("Audio Info", {"Details": body}), parse_mode="html")
    except Exception as er:
        await msg.edit(fmtmsg("Audio Info", {"Error": str(er)}), parse_mode="html")

    os.remove(file.name)
    await msg.delete()


AUDIO_CONVERT_FORMATS = ["mp3", "wav", "ogg", "aac", "flac", "opus"]


@ultroid_cmd(pattern=r"audioconvert(?:\s+(.*))?$")
async def audioconvert(e):
    """Convert audio to another format."""
    fmt = (
        (e.pattern_match.group(1) or "").strip().lower()
        if e.pattern_match.group(1)
        else ""
    )
    if fmt not in AUDIO_CONVERT_FORMATS:
        return await eor(
            e,
            fmtmsg(
                "Audio Convert",
                {
                    "Usage": f"{HNDLR}audioconvert <format>",
                    "Formats": ", ".join(AUDIO_CONVERT_FORMATS),
                },
            ),
            parse_mode="html",
            time=10,
        )

    reply = await e.get_reply_message()
    if not (
        reply and reply.media and fns.mediainfo(reply.media).startswith(("audio", "video"))
    ):
        return await eor(
            e,
            fmtmsg("Audio Convert", {"Error": "Reply to audio/video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Audio Convert", {"Status": "Downloading..."}))
    file = await _download_audio(reply, e, msg)
    if not file:
        return

    file_name = os.path.basename(file.name)
    out = file_name.rsplit(".", 1)[0] + f".{fmt}"
    await msg.edit(
        fmtmsg("Audio Convert", {"Status": f"Converting to {fmt.upper()}..."})
    )

    codec_map = {
        "mp3": "libmp3lame",
        "wav": "pcm_s16le",
        "ogg": "libvorbis",
        "aac": "aac",
        "flac": "flac",
        "opus": "libopus",
    }
    codec = codec_map.get(fmt, "libmp3lame")
    await fns.bash(f'ffmpeg -i "{file.name}" -codec:a {codec} "{out}" -y')
    os.remove(file.name)

    try:
        out_msg = await _upload_audio(out, e, msg)
    except Exception:
        pass
    os.remove(out)
    await msg.delete()


@ultroid_cmd(pattern=r"audiosplit(?:\s+(.*))?$")
async def audiosplit(e):
    """Split audio at a timestamp."""
    ts = (e.pattern_match.group(1) or "").strip() if e.pattern_match.group(1) else ""
    if not ts or ":" not in ts:
        return await eod(
            e,
            fmtmsg("Audio Split", {"Usage": f"{HNDLR}audiosplit <mm:ss>"}),
            parse_mode="html",
            time=10,
        )

    reply = await e.get_reply_message()
    if not (
        reply and reply.media and fns.mediainfo(reply.media).startswith(("audio", "video"))
    ):
        return await eor(
            e,
            fmtmsg("Audio Split", {"Error": "Reply to audio"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Audio Split", {"Status": "Downloading..."}))
    file = await _download_audio(reply, e, msg)
    if not file:
        return

    parts = ts.split(":", 1)
    try:
        minutes, seconds = int(parts[0]), int(parts[1])
        split_at = minutes * 60 + seconds
    except ValueError:
        os.remove(file.name)
        return await eor(
            msg,
            fmtmsg("Audio Split", {"Error": "Invalid time format"}),
            parse_mode="html",
            time=10,
        )

    dur = await fns.genss(file.name)
    if split_at >= dur:
        os.remove(file.name)
        return await eor(
            msg,
            fmtmsg("Audio Split", {"Error": "Split point exceeds duration"}),
            parse_mode="html",
            time=10,
        )

    file_name = os.path.basename(file.name)
    out1 = file_name.rsplit(".", 1)[0] + f"_part1.{file_name.rsplit('.',1)[-1]}"
    out2 = file_name.rsplit(".", 1)[0] + f"_part2.{file_name.rsplit('.',1)[-1]}"

    await msg.edit(fmtmsg("Audio Split", {"Status": f"Splitting at {ts}..."}))
    await fns.bash(f'ffmpeg -i "{file.name}" -to {split_at} -c copy "{out1}" -y')
    await fns.bash(f'ffmpeg -i "{file.name}" -ss {split_at} -c copy "{out2}" -y')
    os.remove(file.name)

    try:
        for out_f in [out1, out2]:
            if os.path.exists(out_f):
                await _upload_audio(out_f, e, msg)
    except Exception:
        pass

    for out_f in [out1, out2]:
        try:
            os.remove(out_f)
        except Exception:
            pass
    await msg.delete()


@ultroid_cmd(pattern="audioreverse$")
async def audioreverse(e):
    """Reverse the audio."""
    reply = await e.get_reply_message()
    if not (
        reply and reply.media and fns.mediainfo(reply.media).startswith(("audio", "video"))
    ):
        return await eor(
            e,
            fmtmsg("Audio Reverse", {"Error": "Reply to audio/video"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Audio Reverse", {"Status": "Downloading..."}))
    file = await _download_audio(reply, e, msg)
    if not file:
        return

    file_name = os.path.basename(file.name)
    out = file_name.rsplit(".", 1)[0] + "_reverse.aac"
    await msg.edit(fmtmsg("Audio Reverse", {"Status": "Reversing audio..."}))
    await fns.bash(f'ffmpeg -i "{file.name}" -filter:a "areverse" -c:a copy "{out}" -y')
    os.remove(file.name)

    try:
        await _upload_audio(out, e, msg)
    except Exception:
        pass
    os.remove(out)
    await msg.delete()


@ultroid_cmd(pattern="audiomerge$")
async def audiomerge(e):
    """Merge multiple audio files."""
    audios = []
    async for m in e.client.iter_messages(e.chat_id, from_user="me", limit=10):
        if m.media and fns.mediainfo(m.media).startswith("audio"):
            audios.append(m)
    if not audios:
        return await eor(
            e,
            fmtmsg("Audio Merge", {"Error": "Reply to multiple audio files to merge"}),
            parse_mode="html",
            time=10,
        )

    msg = await e.eor(fmtmsg("Audio Merge", {"Status": "Downloading..."}))
    files = []
    for a in audios:
        try:
            f = await _download_audio(a, e, msg)
            files.append(f)
        except Exception:
            pass

    if len(files) < 2:
        await msg.edit(
            fmtmsg("Audio Merge", {"Error": "Need at least 2 audio files"}),
            parse_mode="html",
            time=10,
        )
        for f in files:
            os.remove(f)
        return

    await msg.edit(fmtmsg("Audio Merge", {"Status": f"Merging {len(files)} files..."}))
    list_file = "audio_list.txt"
    with open(list_file, "w") as f:
        for fi in files:
            f.write(f"file '{fi}'\n")
    out = "merged_audio.mp3"
    await fns.bash(f'ffmpeg -f concat -safe 0 -i {list_file} -c copy "{out}" -y')
    for fi in files:
        os.remove(fi)
    os.remove(list_file)

    try:
        await _upload_audio(out, e, msg)
    except Exception:
        pass
    os.remove(out)
    await msg.delete()

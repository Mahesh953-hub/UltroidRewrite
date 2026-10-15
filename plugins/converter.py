# Ultroid - UserBot
# Copyright (C) 2021-2026 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""
✘ Commands Available

• `{i}thumbnail <reply to image>`
    Set custom thumbnail.

• `{i}rename <new_name>`
    Rename a replied file.

• `{i}convert <format>`
    Convert media format (png, webp, gif, webm, mp4, mp3, wav, ogg, avi, mov).

• `{i}doc <filename>`
    Save a text message into a file.

• `{i}open <file_id>`
    Open and display a file's text content.

• `{i}mediainfo <file_id>`
    Show detailed media information.

• `{i}thumbset`
    Set uploaded thumbnail as bot's custom thumbnail.
"""

import os
import time

from telegraph import upload_file as uf

from . import (
    HNDLR,
    ULTConfig,
    get_string,
    ultroid_cmd,
    eor,
    eod,
)

from custom.fmt import fmtmsg, rawhtml
from . import udB
from pyUltroid.fns import fns


async def _download_media(reply, event, msg):
    """Download media from a reply message."""
    if reply.photo:
        return await reply.download_media()
    elif reply.document and reply.document.thumbs:
        return await reply.download_media(thumb=-1)
    elif reply.media and hasattr(reply.media, "document"):
        return await fns.downloader(
            reply.file.name or str(time.time()),
            reply.media.document,
            msg,
            time.time(),
            get_string("com_5"),
        )
    return None


async def _upload_and_send(file_path, event, msg, thumb=ULTConfig.thumb):
    """Upload a file and send it."""
    try:
        out = await fns.uploader(
            file_path, file_path, time.time(), msg, get_string("com_6")
        )
        attributes = None
        try:

            attributes = await fns.set_attributes(file_path)
        except Exception:
            pass
        await event.client.send_file(
            event.chat_id,
            out,
            force_document=True,
            thumb=thumb,
            caption=f"`{out.name}`",
            attributes=attributes,
            reply_to=event.reply_to_msg_id,
        )
        return out
    except Exception as er:
        raise Exception(str(er))


@ultroid_cmd(pattern="thumbnail$")
async def thumbnail(e):
    """Set custom thumbnail from a replied image."""
    r = await e.get_reply_message()
    if not r or not r.photo and not (r.document and r.document.thumbs):
        return await eor(
            e,
            fmtmsg("Thumbnail", {"Error": "Reply to a photo or media with thumb"}),
            parse_mode="v2",
            time=10,
        )

    dl = await _download_media(r, e, e)
    if not dl:
        return await eor(
            e,
            fmtmsg("Thumbnail", {"Error": "Download failed"}),
            parse_mode="v2",
            time=10,
        )

    try:
        variable = uf(dl)
        nn = f"https://graph.org{variable[0]}"
        e.client.udb.set_key("CUSTOM_THUMBNAIL", str(nn))
        await fns.bash(f"wget {nn} -O resources/extras/ultroid.jpg")
        await e.eor(
            fmtmsg(
                "Thumbnail",
                {
                    "Status": "✅ Thumbnail set",
                    "Link": rawhtml(f"<a href='{nn}'>{nn}</a>"),
                },
            ),
            parse_mode="v2",
        )
    except Exception as er:
        await eor(e, fmtmsg("Thumbnail", {"Error": str(er)}), parse_mode="v2", time=10)
    finally:
        try:
            os.remove(dl)
        except Exception:
            pass


@ultroid_cmd(pattern=r"rename(?:\s+(.*))?$")
async def rename_file(e):
    """Rename a replied file."""
    inp = (e.pattern_match.group(1) or "").strip() if e.pattern_match.group(1) else ""
    reply = await e.get_reply_message()
    if not reply:
        return await eod(
            e,
            fmtmsg("Rename", {"Usage": f"{HNDLR}rename <new_name>"}),
            parse_mode="v2",
            time=10,
        )
    if not inp:
        return await eod(
            e,
            fmtmsg("Rename", {"Error": "Provide a new name"}),
            parse_mode="v2",
            time=10,
        )

    xx = await e.eor(fmtmsg("Rename", {"Status": "Downloading..."}))
    t = time.time()
    if reply.media and hasattr(reply.media, "document"):
        file = await fns.downloader(
            reply.file.name or str(t), reply.media.document, xx, t, get_string("com_5")
        )
        file = file.name
    else:
        file = await e.client.download_media(reply)

    if not file or not os.path.exists(file):
        return await eor(
            xx, fmtmsg("Rename", {"Error": "Download failed"}), parse_mode="v2", time=10
        )

    dir_name = os.path.dirname(file)
    new_path = os.path.join(dir_name, inp)
    try:
        os.rename(file, new_path)
    except Exception:
        os.rename(file, inp)
        new_path = inp

    if not os.path.exists(new_path):
        return await eor(
            xx, fmtmsg("Rename", {"Error": "Rename failed"}), parse_mode="v2", time=10
        )

    out = await fns.uploader(new_path, inp, time.time(), xx, get_string("com_6"))
    await e.client.send_file(
        e.chat_id,
        out,
        force_document=True,
        thumb=ULTConfig.thumb,
        caption=f"`{out.name}`",
        reply_to=reply.reply_to_msg_id,
    )
    os.remove(new_path)
    await xx.delete()


CONV_KEYS = {
    "png": "png",
    "jpg": "png",
    "jpeg": "png",
    "img": "png",
    "webp": "webp",
    "sticker": "webp",
    "image": "png",
    "gif": "gif",
    "webm": "webm",
    "json": "json",
    "tgs": "tgs",
    "mp4": "mp4",
    "avi": "avi",
    "mov": "mov",
    "mkv": "mkv",
    "mp3": "mp3",
    "wav": "wav",
    "ogg": "ogg",
    "aac": "aac",
}


@ultroid_cmd(pattern=r"convert(?:\s+(.*))?$")
async def uconverter(e):
    """Convert media to another format."""
    fmt = (
        (e.pattern_match.group(1) or "").strip().lower()
        if e.pattern_match.group(1)
        else ""
    )
    if fmt not in CONV_KEYS:
        return await eor(
            e,
            fmtmsg(
                "Convert",
                {
                    "Usage": f"{HNDLR}convert <format>",
                    "Formats": ", ".join(sorted(CONV_KEYS.keys())),
                },
            ),
            parse_mode="v2",
            time=10,
        )

    a = await e.get_reply_message()
    if not a:
        return await eor(
            e, fmtmsg("Convert", {"Error": "Reply to media"}), parse_mode="v2", time=10
        )

    xx = await e.eor(fmtmsg("Convert", {"Status": "Downloading..."}))
    b = await a.download_media("resources/downloads/")
    if not b and a.document and a.document.thumbs:
        b = await a.download_media(thumb=-1)
    if not b:
        return await xx.edit(
            fmtmsg("Convert", {"Error": get_string("cvt_3")}), parse_mode="v2"
        )

    convert_to = CONV_KEYS[fmt]
    file_name = os.path.basename(b)
    out_name = file_name.rsplit(".", 1)[0] + f".{convert_to}"

    await xx.edit(
        fmtmsg("Convert", {"Status": f"Converting to {convert_to.upper()}..."})
    )

    try:
        await fns.bash(f'ffmpeg -i "{b}" -y "{out_name}"')
    except Exception as er:
        os.remove(b)
        return await xx.edit(fmtmsg("Convert", {"Error": str(er)}), parse_mode="v2")

    if not os.path.exists(out_name):
        os.remove(b)
        return await xx.edit(
            fmtmsg("Convert", {"Error": "Conversion failed"}), parse_mode="v2"
        )

    try:
        out = await fns.uploader(
            out_name, os.path.basename(out_name), time.time(), xx, get_string("com_6")
        )
        await e.client.send_file(
            e.chat_id,
            out,
            force_document=True,
            thumb=ULTConfig.thumb,
            caption=f"`{out.name}`",
            reply_to=a.reply_to_msg_id,
        )
    except Exception:
        pass

    os.remove(b)
    try:
        os.remove(out_name)
    except Exception:
        pass
    await xx.delete()


@ultroid_cmd(pattern=r"doc(?:\s+(.*))?$")
async def doc_cmd(e):
    """Save a text message into a file."""
    input_str = (
        (e.pattern_match.group(1) or "").strip() if e.pattern_match.group(1) else ""
    )
    a = await e.get_reply_message()
    if not (input_str and a):
        return await eod(
            e,
            fmtmsg("Doc", {"Usage": f"{HNDLR}doc <filename> | reply to text"}),
            parse_mode="v2",
            time=10,
        )

    xx = await e.eor(fmtmsg("Doc", {"Status": "Packing..."}))
    if not input_str:
        input_str = a.message or ""
    with open(input_str, "w") as f:
        f.write(str(a.message) if a.message else input_str)

    await xx.edit(fmtmsg("Doc", {"Status": f"Packing into `{input_str}`"}))
    try:
        await e.reply(file=input_str, thumb=ULTConfig.thumb)
    except Exception:
        pass
    await xx.delete()
    try:
        os.remove(input_str)
    except Exception:
        pass


@ultroid_cmd(pattern=r"open(?:\s+(.*))?$")
async def open_cmd(e):
    """Open and display a file's text content."""
    a = await e.get_reply_message()
    b = (e.pattern_match.group(1) or "").strip() if e.pattern_match.group(1) else ""
    if not ((a and a.media) or (b and os.path.exists(b))):
        return await eor(
            e, fmtmsg("Open", {"Error": get_string("cvt_7")}), parse_mode="v2", time=10
        )

    xx = await e.eor(fmtmsg("Open", {"Status": "Reading..."}))
    rem = None
    if not b:
        b = await a.download_media()
        rem = True
    try:
        with open(b) as c:
            d = c.read()
    except UnicodeDecodeError:
        return await xx.eor(
            fmtmsg("Open", {"Error": get_string("cvt_8")}), parse_mode="v2", time=10
        )

    try:
        await xx.edit(f"```{d[:4090]}```")
    except Exception:
        try:

            what, key = await fns.get_paste(d)
            await xx.edit(
                fmtmsg(
                    "Open",
                    {
                        "Status": "Message exceeds limits",
                        "Paste": rawhtml(
                            f"<a href='https://spaceb.in/{key}'>SPACEBIN</a>"
                        ),
                    },
                ),
                parse_mode="v2",
            )
        except Exception:
            await xx.edit(
                fmtmsg("Open", {"Error": "Too large"}), parse_mode="v2", time=10
            )

    if rem:
        try:
            os.remove(b)
        except Exception:
            pass
    await xx.delete()


@ultroid_cmd(pattern=r"mediainfo(?:\s+(.*))?$")
async def mediainfo(e):
    """Show detailed media information."""
    file_id = (
        (e.pattern_match.group(1) or "").strip() if e.pattern_match.group(1) else ""
    )
    a = await e.get_reply_message()
    file_path = None

    if not file_id and a and a.media:
        if a.document:
            file_path = a.document
        elif a.photo:
            file_path = a.photo
    elif file_id:
        try:
            msg = await e.client.get_messages("me", ids=int(file_id))
            if msg and msg.media:
                file_path = msg.media
        except Exception:
            pass

    if not file_path:
        return await eor(
            e,
            fmtmsg("Media Info", {"Error": "Reply to media or provide file ID"}),
            parse_mode="v2",
            time=10,
        )

    xx = await e.eor(fmtmsg("Media Info", {"Status": "Fetching info..."}))

    if hasattr(file_path, "document"):
        doc = file_path.document
        mime = doc.mime_type
        size = doc.size
        name = doc.file_name or "Unknown"
        dur = getattr(doc, "duration", 0)
        w = getattr(doc, "width", 0)
        h = getattr(doc, "height", 0)
    elif hasattr(file_path, "photo"):
        photo = file_path.photo
        mime = "image"
        size = 0
        name = "Photo"
        dur = 0
        w = photo.width
        h = photo.height
    else:
        return await xx.edit(
            fmtmsg("Media Info", {"Error": "Unsupported media type"}), parse_mode="v2"
        )

    icon = (
        "🖼️"
        if "image" in mime
        else "🎬" if "video" in mime else "🎵" if "audio" in mime else "📎"
    )

    rows = {
        "Name": f"{icon} <code>{name}</code>",
        "Type": f"<code>{mime}</code>",
        "Size": size and f"{size / 1024 / 1024:.2f} MB" or "N/A",
        "Duration": f"{dur}s" if dur else "N/A",
        "Width": str(w) if w else "N/A",
        "Height": str(h) if h else "N/A",
    }
    padd = max(len(str(k)) for k in rows)
    body = "\n".join(f"  <code>{k.ljust(padd)}</code> : {v}" for k, v in rows.items())

    await xx.edit(fmtmsg("Media Info", {"Details": body}), parse_mode="v2")
    await xx.delete()


@ultroid_cmd(pattern="thumbset$")
async def thumbset(e):
    """Set uploaded thumbnail as custom thumbnail."""
    thumb = udB.get_key("CUSTOM_THUMBNAIL")
    if thumb:
        await e.eor(
            fmtmsg(
                "Thumbset",
                {
                    "Status": "✅ Already set",
                    "Link": rawhtml(f"<a href='{thumb}'>View</a>"),
                },
            ),
            parse_mode="v2",
        )
    else:
        await e.eor(
            fmtmsg(
                "Thumbset", {"Info": "No thumbnail set. Use `thumbnail` command first."}
            ),
            parse_mode="v2",
        )

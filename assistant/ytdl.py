# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
# Modifed for cookies and fixed by @TrueSaiyan


import html
import os
import re
from typing import Any

try:
    from PIL import Image
except ImportError:
    Image = None
from telethon import Button
from telethon.errors.rpcerrorlist import FilePartLengthInvalidError, MediaEmptyError
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo
from telethon.tl.types import InputWebDocument as wb

from custom.fmt import fmtmsg, rawhtml
from custom.tg import ikm
from pyUltroid._core.events import InlineResultBuilder
from pyUltroid.fns import fns
from pyUltroid.fns.ytdl import FormatInfo, dler, get_formats

from . import LOGS, asst, callback, in_pattern, udB

try:
    from youtubesearchpython import VideosSearch
except ImportError:
    LOGS.info("'youtubesearchpython' not installed!")
    VideosSearch = None


ytt = "https://graph.org/file/afd04510c13914a06dd03.jpg"
_yt_base_url = "https://www.youtube.com/watch?"
BACK_BUTTON = {}


def _yt_format_buttons(listt: list[FormatInfo]) -> Any:
    """Build the format-selection keyboard for a ytdl:(.*) result.

    Local reimplementation of pyUltroid.fns.ytdl.get_buttons() (core,
    out of scope for this rewrite) so the buttons go through :func:ikm
    for consistent styling instead of core's raw, unstyled
    Button.inline calls. Deliberately keeps the same literal
    ytdownload:<type>:<id>:<ytid>[:<ext>] / ytdl_back:<id> token
    format as core (not migrated to the assistant: colon model   see
    the ytdl: / ytdl_back: note in the module docstring above).
    listt is the :class:pyUltroid.fns.ytdl.FormatInfo list returned by
    :func:get_formats.
    """
    id_ = listt[0].ytid

    unique_resolutions: dict[str, FormatInfo] = {}
    for x in listt:
        resolution = x.quality
        size = x.size
        if (
            resolution not in unique_resolutions
            or size > unique_resolutions[resolution].size
        ):
            unique_resolutions[resolution] = x

    rows = []
    row = []
    for x in unique_resolutions.values():
        text = f"[{x.quality}" + (f" {fns.humanbytes(x.size)}]" if x.size else "]")
        data = f"ytdownload:{x.type}:{x.id}:{x.ytid}" + (f":{x.ext}" if x.ext else "")
        row.append((text, data, "primary"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([("Back", f"ytdl_back:{id_}", None)])

    return ikm(rows, icon=False)


@in_pattern("yt", owner=True)
async def _(event: Any) -> None:
    try:
        string = event.text.split(" ", maxsplit=1)[1]
    except IndexError:
        fuk = await InlineResultBuilder(event).article(
            title="Search Something",
            thumb=wb(ytt, 0, "image/jpeg", []),
            text=fmtmsg("YouTube Search ", msgs="You didn't search anything."),
            buttons=ikm(
                [[Button.switch_inline("Search Again ", query="yt ", same_peer=True)]],
                icon=False,
            ),
        )
        await event.answer([fuk])
        return
    results = []
    search = VideosSearch(string, limit=50)
    nub = search.result()
    nibba = nub["result"]
    for v in nibba:
        ids = v["id"]
        link = f"https://www.youtube.com/watch?v={ids}"
        title = v["title"]
        duration = v["duration"]
        views = v["viewCount"]["short"]
        publisher = v["channel"]["name"]
        published_on = v["publishedTime"]
        description = (
            v["descriptionSnippet"][0]["text"]
            if v.get("descriptionSnippet")
            and len(v["descriptionSnippet"][0]["text"]) < 500
            else "None"
        )
        thumb = f"https://i.ytimg.com/vi/{ids}/hqdefault.jpg"

        text = fmtmsg(
            "YouTube Downloader ",
            {
                "Title": rawhtml(f"<a href='{link}'>{html.escape(str(title))}</a>"),
                "Duration": duration,
                "Views": views,
                "Publisher": publisher,
                "Published": published_on,
            },
            msgs=description,
        )

        desc = f"{title}\n{duration}"
        file = wb(thumb, 0, "image/jpeg", [])
        buttons = ikm(
            [
                [
                    ("Audio ", f"ytdl:audio:{ids}", "primary"),
                    ("Video ", f"ytdl:video:{ids}", "primary"),
                ],
                [
                    Button.switch_inline("Search Again ", query="yt ", same_peer=True),
                    Button.switch_inline(
                        "Share ", query=f"yt {string}", same_peer=False
                    ),
                ],
            ],
            icon=False,
        )
        BACK_BUTTON.update({ids: {"text": text, "buttons": buttons}})
        results.append(
            await event.builder.article(
                title=title,
                description=desc,
                thumb=file,
                content=file,
                text=text,
                include_media=True,
                buttons=buttons,
            ),
        )
    await event.answer(results[:50])


@callback(
    re.compile(
        "ytdl:(.*)",
    ),
    owner=True,
)
async def _(e: Any) -> None:
    _e = e.pattern_match.group(1).strip().decode("UTF-8")
    _lets_split = _e.split(":")
    _ytdl_data = await dler(e, f"https://www.youtube.com/watch?v={_lets_split[1]}")
    _data = get_formats(_lets_split[0], _lets_split[1], _ytdl_data)
    _buttons = _yt_format_buttons(_data) if _data else None

    if _buttons:
        _text = fmtmsg("YouTube Downloader ", msgs="Select your preferred format:")
    else:
        _text = fmtmsg(
            "Download Error ",
            msgs="Error downloading from YouTube.\nTry restarting your bot.",
        )

    await e.edit(_text, buttons=_buttons, parse_mode="html")


@callback(
    re.compile(
        "ytdownload:(.*)",
    ),
    owner=True,
)
async def _(event: Any) -> None:
    url = event.pattern_match.group(1).strip().decode("UTF-8")
    lets_split = url.split(":")
    vid_id = lets_split[2]
    link = f"https://www.youtube.com/watch?v={vid_id}"
    format = lets_split[1]
    try:
        ext = lets_split[3]
    except IndexError:
        ext = "mp3"

    if lets_split[0] == "audio":
        opts = {
            "format": "bestaudio",
            "addmetadata": True,
            "key": "FFmpegMetadata",
            "prefer_ffmpeg": True,
            "geo_bypass": True,
            "outtmpl": f"%(id)s.{ext}",
            "logtostderr": False,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": ext,
                    "preferredquality": format,
                },
                {"key": "FFmpegMetadata"},
            ],
            "cookiefile": "cookies.txt",
        }

        ytdl_data = await dler(event, link, opts, True)
        title = ytdl_data["title"]
        artist = (
            ytdl_data.get("artist")
            or ytdl_data.get("creator")
            or ytdl_data.get("channel")
        )
        views = fns.numerize(ytdl_data.get("view_count")) or 0
        thumb, _ = await fns.fast_download(
            ytdl_data["thumbnail"], filename=f"{vid_id}.jpg"
        )

        likes = fns.numerize(ytdl_data.get("like_count")) or 0
        duration = ytdl_data.get("duration") or 0
        description = (
            ytdl_data["description"][:100]
            if ytdl_data["description"] and len(ytdl_data["description"]) >= 100
            else ytdl_data.get("description", "None")
        )

        filepath = f"{vid_id}.{ext}"
        if not os.path.exists(filepath):
            filepath = f"{filepath}.{ext}"
        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
        else:
            LOGS.error(f"File not found: {filepath}")
            await event.edit(
                fmtmsg("Download Error ", msgs="Error: File not found after download."),
                parse_mode="html",
            )
            return

        file, _ = await event.client.fast_uploader(
            filepath,
            filename=f"{title}.{ext}",
            show_progress=True,
            event=event,
            to_delete=True,
        )

        attributes = [
            DocumentAttributeAudio(
                duration=int(duration),
                title=title,
                performer=artist,
            ),
        ]

    elif lets_split[0] == "video":
        opts = {
            "format": str(format),
            "addmetadata": True,
            "key": "FFmpegMetadata",
            "prefer_ffmpeg": True,
            "geo_bypass": True,
            "outtmpl": f"%(id)s.{ext}",
            "logtostderr": False,
            "postprocessors": [{"key": "FFmpegMetadata"}],
            "cookiefile": "cookies.txt",
        }

        ytdl_data = await dler(event, link, opts, True)
        title = ytdl_data["title"]
        artist = (
            ytdl_data.get("artist")
            or ytdl_data.get("creator")
            or ytdl_data.get("channel")
        )
        views = fns.numerize(ytdl_data.get("view_count")) or 0
        thumb, _ = await fns.fast_download(
            ytdl_data["thumbnail"], filename=f"{vid_id}.jpg"
        )
        description = (
            ytdl_data.get("description", "None")
            if ytdl_data.get("description") and len(ytdl_data["description"]) < 100
            else (
                ytdl_data["description"][:100]
                if ytdl_data.get("description")
                else "None"
            )
        )

        try:
            Image.open(thumb).save(thumb, "JPEG")
        except Exception as er:
            LOGS.exception(er)
            thumb = None

        likes = fns.numerize(ytdl_data.get("like_count")) or 0
        hi, wi = ytdl_data.get("height") or 720, ytdl_data.get("width") or 1280
        duration = ytdl_data.get("duration") or 0
        filepath = f"{vid_id}.mkv"
        if not os.path.exists(filepath):
            filepath = f"{filepath}.webm"
        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
        if not os.path.exists(filepath):
            filepath = f"{vid_id}.mp4.webm"
        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
        if not os.path.exists(filepath):
            filepath = f"{vid_id}.mp4.mkv"
        if os.path.exists(filepath):
            size = os.path.getsize(filepath)
        else:
            LOGS.error(f"File not found: {filepath}")
            await event.edit(
                fmtmsg("Download Error ", msgs="Error: File not found after download."),
                parse_mode="html",
            )
            return

        file, _ = await event.client.fast_uploader(
            filepath,
            filename=f"{title}.mkv",
            show_progress=True,
            event=event,
            to_delete=True,
        )

        attributes = [
            DocumentAttributeVideo(
                duration=int(duration),
                w=wi,
                h=hi,
                supports_streaming=True,
            ),
        ]

    description = description if description != "" else "None"
    text = fmtmsg(
        "YouTube Downloader ",
        {
            "Title": rawhtml(
                f"<a href='https://www.youtube.com/watch?v={vid_id}'>{html.escape(str(title))}</a>"
            ),
            "Artist": artist,
            "Duration": fns.time_formatter(int(duration) * 1000),
            "Views": views,
            "Likes": likes,
            "Size": fns.humanbytes(size),
        },
        msgs=description,
    )
    button = ikm(
        [[Button.switch_inline("Search More ", query="yt ", same_peer=True)]],
        icon=False,
    )

    try:
        await event.edit(
            text,
            file=file,
            buttons=button,
            attributes=attributes,
            thumb=thumb,
            parse_mode="html",
        )
    except (FilePartLengthInvalidError, MediaEmptyError):
        file = await asst.send_message(
            udB.get_key("LOG_CHANNEL"),
            text,
            file=file,
            buttons=button,
            attributes=attributes,
            thumb=thumb,
            parse_mode="html",
        )
        await event.edit(text, file=file.media, buttons=button, parse_mode="html")

    await fns.bash(f"rm {vid_id}.jpg")


@callback(re.compile("ytdl_back:(.*)"), owner=True)
async def ytdl_back(event: Any) -> None:
    id_ = event.data_match.group(1).decode("utf-8")
    if not BACK_BUTTON.get(id_):
        return await event.answer("Query Expired! Search again ")

    text_data = BACK_BUTTON[id_]["text"]
    btns = BACK_BUTTON[id_]["buttons"]
    await event.edit(text_data, buttons=btns, parse_mode="html")

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""
✘ Commands Available -

• `{i}webupload` | `{i}wul`
    Upload files on another server.

    Reply to a media message or pass a file path.
    Use `-s <service>` to upload directly without the menu.

    Services: catbox, litterbox, file.io, 0x0.st, transfer.sh, uguu, filebin, siasky
    Example: `{i}wul -s catbox` (reply to a file)
"""

import os
import re
import time

from pyUltroid._core.webupload import (
    MENU_SERVICES,
    SERVICE_LABELS,
    UPLOAD_SERVICES,
    UploadError,
    resolve_service,
    upload_to,
)
from pyUltroid.fns import fns
from pyUltroid.fns.tools import _webupload_cache
from pyUltroid.fns import fns
from pyUltroid.fns.tools import _webupload_cache

from custom.tg import ikm

from . import Button, asst, get_string, ultroid_cmd

# `-s <service>` anywhere in the argument string.
_SERVICE_RE = re.compile(r"(?:^|\s)-s\s+(\S+)")


def _service_buttons(cache_key):
    """Build the 2-column inline server-selection menu."""
    rows = []
    row = []
    for key in MENU_SERVICES:
        row.append((SERVICE_LABELS.get(key, key), f"fl{key}//{cache_key}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return ikm(rows)


def _progress_bar(pct):
    filled = max(0, min(10, pct // 10))
    return "[" + "■" * filled + "□" * (10 - filled) + "]"


async def _obtain_file(event, xx, path):
    """Resolve the file to upload from a path arg or a replied media message.

    Returns ``(file_path, is_temp)`` where *is_temp* marks files we downloaded
    and therefore may clean up afterwards, or ``(None, False)`` on failure.
    """
    if path and os.path.exists(path):
        return path, False
    if not event.reply_to_msg_id:
        return None, False
    reply = await event.get_reply_message()
    if not reply or not (reply.photo or reply.document):
        return None, False
    if reply.photo:
        file = await reply.download_media("resources/downloads/")
        return file, True
    file, _ = await event.client.fast_downloader(
        reply.document, show_progress=True, event=xx
    )
    return file.name, True


def _format_result(result, filename):
    """Render an :class:`UploadResult` as ``(text, buttons)``."""
    text = f"**Uploaded** `{filename}` **on** `{result.service}`."
    buttons = [[Button.url("View", result.url)]]
    if result.delete_url:
        text += f"\n🗑 Delete: `{result.delete_url}`"
        if result.delete_note:
            text += f" ({result.delete_note})"
    if result.expires:
        text += f"\n⏳ Expires: {result.expires}"
    return text, buttons


async def _do_direct_upload(event, xx, key, file, is_temp):
    """Upload *file* to *key* directly with live progress and a result card."""
    label = SERVICE_LABELS.get(key, key)
    disp = os.path.basename(file)
    total = os.path.getsize(file)
    state = {"pct": -1, "t": 0.0}

    async def on_progress(sent, tot):
        pct = round(sent / tot * 100) if tot else 0
        now = time.time()
        if pct - state["pct"] >= 5 or now - state["t"] >= 1.5 or sent >= tot:
            state["pct"] = pct
            state["t"] = now
            bar = _progress_bar(pct)
            try:
                await xx.edit(
                    f"📤 Uploading `{disp}` on {label}...\n{bar} {pct}%  "
                    f"({fns.humanbytes(sent)}/{fns.humanbytes(tot or total)})"
                )
            except Exception:
                pass

    await xx.edit(f"📤 Uploading `{disp}` on {label}...")
    session = getattr(event.client, "http", None)
    try:
        result = await upload_to(key, file, on_progress=on_progress, session=session)
    except UploadError as exc:
        await xx.eor(f"❌ Failed to upload `{disp}` on {label}: `{exc}`")
        return
    text, buttons = _format_result(result, disp)
    await xx.edit(text, buttons=buttons, link_preview=False)
    if is_temp:
        try:
            os.remove(file)
        except FileNotFoundError:
            pass


@ultroid_cmd(pattern="webupload( (.*)|$)")
@ultroid_cmd(pattern="wul( (.*)|$)")
async def webupload_cmd(event):
    xx = await event.eor(get_string("com_1"))
    arg = (event.pattern_match.group(1) or "").strip()

    match = _SERVICE_RE.search(arg)
    if match:
        raw_service = match.group(1)
        path = (arg[: match.start()] + arg[match.end() :]).strip()
    else:
        raw_service = None
        path = arg

    file, is_temp = await _obtain_file(event, xx, path)
    if not file:
        if raw_service:
            await xx.eor("Reply to a file or give a file path to upload.")
        else:
            await xx.eor("Reply to file or give file path...")
        return

    # Direct upload via the ``-s`` flag.
    if raw_service:
        key = resolve_service(raw_service)
        if not key:
            avail = ", ".join(SERVICE_LABELS.get(k, k) for k in UPLOAD_SERVICES)
            await xx.eor(f"Unknown service `{raw_service}`.\nAvailable: {avail}")
            return
        await _do_direct_upload(event, xx, key, file, is_temp)
        return

    # Menu path (existing behaviour): cache the file, then offer the menu.
    if event.chat_id not in _webupload_cache:
        _webupload_cache.update({int(event.chat_id): {}})
    _webupload_cache[int(event.chat_id)][int(event.id)] = file

    if not event.client._bot:
        results = await event.client.inline_query(
            asst.me.username, f"fl2lnk {event.chat_id}:{event.id}"
        )
        await results[0].click(event.chat_id, reply_to=event.reply_to_msg_id)
        await xx.delete()
    else:
        __cache = f"{event.chat_id}:{event.id}"
        await xx.edit(
            "Choose Server to Upload File...", buttons=_service_buttons(__cache)
        )

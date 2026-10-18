# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""
✘ Commands Available

• ``{i}gdul <reply/file>`` — Upload file/folder to Google Drive.
  ``{i}gdul -f <folder_id> <file>`` — Upload to specific folder.

• ``{i}gdown <file_id/link>`` — Download from Google Drive.
  ``{i}gdown <id> | <filename>`` — Download with custom filename.

• ``{i}gdsearch <query>`` — Search files on Google Drive.

• ``{i}gdlist`` — List all Drive files in a rich table.
  ``{i}gdlist -d`` — List with details (size, type, modified).
  ``{i}gdlist -f <folder_id>`` — List files in specific folder.

• ``{i}gdinfo <file_id>`` — Show detailed file info.
• ``{i}gdmove <file_id> <dest_folder_id>`` — Move file to folder.
• ``{i}gdcp <file_id> <dest_folder_id>`` — Copy file to folder.
• ``{i}gdtrash <file_id>`` — Move file to trash.
• ``{i}gddelete <file_id>`` — Permanently delete file.
• ``{i}gdshare <file_id> <email>`` — Share file with email.
• ``{i}gdfolder`` — Show Drive folder link.
• ``{i}gdnewfolder <name>`` — Create new folder.
• ``{i}gdrefresh`` — Refresh file cache and list.

✘ Assistant-bot commands (owner-only):
* ``/gdul``, ``/gdown``, ``/gdsearch``, ``/gdlist`` — same surface.

✘ Inline mode:
* ``@<assistant> gdsearch <query>`` — search and tap to download.
"""

import os
import time
import logging
import glob as globmod

from telethon.tl.types import KeyboardButtonRow, ReplyInlineMarkup

from pyUltroid.fns.gDrive import GDriveManager
from pyUltroid.fns import fns
from pyUltroid._core.richmsg import RichMarkup, send_rich

from . import (
    HNDLR,
    eor,
    eod,
    in_pattern,
    ultroid_bot,
    ultroid_cmd,
    asst_cmd,
    callback,
)

from custom.fmt import fmtmsg, fmtbyte, rawhtml
from custom.tg import ikm
from pyUltroid._core._cbs import cb, cb_pattern
from pyUltroid._core.events import InlineResultBuilder

# --------------------------------------------------------------------------- #
#  Helpers                                                                    #
# --------------------------------------------------------------------------- #

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


def _fmt_mime(mime_type):
    """Format MIME type to human-readable label."""
    icons = {
        "application/pdf": "📄",
        "text/plain": "📃",
        "image/jpeg": "🖼️",
        "image/png": "🖼️",
        "image/gif": "🎞️",
        "video/mp4": "🎬",
        "video/avi": "🎬",
        "audio/mp3": "🎵",
        "audio/mpeg": "🎵",
        "application/vnd.google-apps.folder": "📁",
        "application/zip": "📦",
        "application/x-rar-compressed": "📦",
    }
    return icons.get(mime_type, "📎")


def _build_file_table(files, show_details=False):
    """Build an HTML table of Drive files."""
    if not files:
        return "<b>📭 No files found.</b>"

    rows = []
    for idx, (file_id, file_info) in enumerate(files.items(), 1):
        if show_details:
            name = file_info.get("name", file_info.get("title", "Unknown"))
            size = fmtbyte(file_info.get("size", 0))
            mime = file_info.get("mimeType", "unknown")
            modified = file_info.get("modifiedTime", "N/A")
            icon = _fmt_mime(mime)
            rows.append(
                f"<tr><td>{idx}</td><td>{icon} <code>{name}</code></td>"
                f"<td>{size}</td><td><code>{mime}</code></td>"
                f"<td>{modified}</td></tr>"
            )
        else:
            name = file_info.get("name", file_info.get("title", "Unknown"))
            size = fmtbyte(file_info.get("size", 0))
            icon = _fmt_mime(file_info.get("mimeType", "unknown"))
            rows.append(
                f"<tr><td>{idx}</td><td>{icon} <code>{name}</code></td>"
                f"<td>{size}</td><td>{file_id}</td></tr>"
            )

    if show_details:
        header = (
            "<table border='1' cellpadding='5' cellspacing='0' "
            "style='border-collapse:collapse;width:100%'>"
            "<tr style='background:#2ECC71'>"
            "<th>#</th><th>File</th><th>Size</th><th>Type</th><th>Modified</th>"
            "</tr>"
        )
    else:
        header = (
            "<table border='1' cellpadding='5' cellspacing='0' "
            "style='border-collapse:collapse;width:100%'>"
            "<tr style='background:#2ECC71'>"
            "<th>#</th><th>File</th><th>Size</th><th>ID</th>"
            "</tr>"
        )

    return header + "".join(rows) + "</table>"


def _build_drive_keyboard(files, page=0, per_page=10):
    """Build pagination keyboard for Drive files."""
    total = len(files)
    if total <= per_page:
        return None

    pages = []
    start = 0
    while start < total:
        end = min(start + per_page, total)
        pages.append((start, end))
        start += per_page

    rows = []
    current_row = []
    for i, (s, e) in enumerate(pages):
        text = f"{'◀️' if i > 0 else ''} {s // per_page + 1} {'▶️' if i < len(pages) - 1 else ''}"
        current_row.append(
            {"text": str(i + 1), "data": f"gd_list_{i}", "preset": "primary"}
        )
        if len(current_row) >= 4:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    if rows:
        return ReplyInlineMarkup(
            rows=[
                KeyboardButtonRow(
                    buttons=[
                        {"text": r["text"], "data": r["data"], "preset": r["preset"]}
                        for r in row
                    ]
                )
                for row in rows
            ]
        )
    return None


# --------------------------------------------------------------------------- #
#  GDrive Operations                                                          #
# --------------------------------------------------------------------------- #


async def _get_gdrive():
    """Get GDriveManager instance with token validation."""
    gd = GDriveManager()
    if not os.path.exists(gd.token_file):
        return None, "No credentials found. Set up GDrive auth first."
    return gd, None


async def _gd_upload(e, file_path, folder_id=None):
    """Upload a file to GDrive with progress tracking."""
    gd, err = await _get_gdrive()
    if err:
        return None, err

    try:
        link = await gd._upload_file(e, file_path, folder_id=folder_id)
        return link, None
    except Exception as er:
        return None, str(er)


async def _gd_download(e, file_id, filename=None):
    """Download a file from GDrive."""
    gd, err = await _get_gdrive()
    if err:
        return None, err

    try:
        start = time.time()
        status, response = await gd._download_file(e, file_id, filename)
        elapsed = fns.time_formatter((time.time() - start) * 1000)
        if status:
            return response, None
        return None, response
    except Exception as er:
        return None, str(er)


async def _gd_list(gd, folder_id=None):
    """List all Drive files."""
    try:
        items = gd._list_files
        files = {}
        for link, name in items.items():
            file_id = link.split("id=")[-1] if "id=" in link else link.split("/")[-1]
            files[file_id] = {"name": name, "id": file_id}
        return files, None
    except Exception as er:
        return {}, str(er)


async def _gd_get_details(gd, file_id):
    """Get detailed info about a file."""
    try:
        service = gd._build
        result = (
            service.files()
            .get(fileId=file_id, supportsAllDrives=True, fields="*")
            .execute()
        )
        return result, None
    except Exception as er:
        return None, str(er)


async def _gd_move(gd, file_id, dest_folder_id):
    """Move a file to a different folder."""
    try:
        service = gd._build
        file = service.files().get(fileId=file_id, supportsAllDrives=True).execute()
        old_parents = ",".join(parent["id"] for parent in file.get("parents", []))
        file = (
            service.files()
            .update(
                fileId=file_id,
                addParents=dest_folder_id,
                removeParents=old_parents,
                fields="id, parents",
                supportsAllDrives=True,
            )
            .execute()
        )
        return file, None
    except Exception as er:
        return None, str(er)


async def _gd_copy(gd, file_id, dest_folder_id):
    """Copy a file to a different folder."""
    try:
        service = gd._build
        body = {"name": file_id, "parents": [dest_folder_id]}
        file = (
            service.files()
            .copy(
                fileId=file_id,
                body=body,
                supportsAllDrives=True,
            )
            .execute()
        )
        return file, None
    except Exception as er:
        return None, str(er)


async def _gd_trash(gd, file_id):
    """Move file to trash."""
    try:
        service = gd._build
        file = (
            service.files()
            .update(
                fileId=file_id,
                trashed=True,
                supportsAllDrives=True,
            )
            .execute()
        )
        return file, None
    except Exception as er:
        return None, str(er)


async def _gd_delete(gd, file_id):
    """Permanently delete file."""
    try:
        service = gd._build
        service.files().delete(
            fileId=file_id,
            supportsAllDrives=True,
        ).execute()
        return True, None
    except Exception as er:
        return None, str(er)


async def _gd_share(gd, file_id, email, role="reader"):
    """Share file with an email address."""
    try:
        service = gd._build
        body = {
            "role": role,
            "type": "user",
            "value": email,
        }
        permission = (
            service.permissions()
            .create(
                fileId=file_id,
                body=body,
                supportsAllDrives=True,
            )
            .execute()
        )
        return permission, None
    except Exception as er:
        return None, str(er)


async def _gd_create_folder(gd, name, parent_id=None):
    """Create a new folder."""
    try:
        body = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            body["parents"] = [parent_id]
        folder = gd._build.files().insert(body=body, supportsAllDrives=True).execute()
        return folder, None
    except Exception as er:
        return None, str(er)


# --------------------------------------------------------------------------- #
#  User commands                                                              #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"gdul(?:\s+(.*))?$")
async def gdul(e):
    """Upload file/folder to Google Drive."""
    args = (e.pattern_match.group(1) or "").strip()
    reply = await e.get_reply_message()

    folder_id = None
    file_path = None

    if args.startswith("-f "):
        parts = args.split(None, 2)
        if len(parts) >= 3:
            folder_id = parts[1]
            file_path = parts[2]
        else:
            return await eod(
                e,
                fmtmsg("GDrive", {"Usage": f"gdul -f <folder_id> <file>"}),
                parse_mode="html",
                time=10,
            )
    elif args:
        file_path = args

    if not file_path and reply and reply.media:
        file_path = reply

    if not file_path:
        return await eod(
            e,
            fmtmsg(
                "GDrive",
                {"Usage": f"{HNDLR}gdul [-f <folder_id>] <file> | reply to file"},
            ),
            parse_mode="html",
            time=10,
        )

    mone = await e.eor(fmtmsg("GDrive", {"Status": "Uploading..."}))
    t = time.time()

    if isinstance(file_path, type(reply)):
        if reply.media:
            if hasattr(reply.media, "document"):
                file_path = await mone.client.fast_downloader(
                    reply.document, show_progress=True, event=mone
                )
            else:
                file_path = await e.client.download_media(reply)
        if not file_path:
            return await eor(
                mone,
                fmtmsg("GDrive", {"Error": "Download failed"}),
                parse_mode="html",
                time=10,
            )

    if isinstance(file_path, str) and os.path.isdir(file_path):
        # Upload folder
        files = globmod.glob(f"{file_path}/*")
        if not files:
            return await eor(
                mone,
                fmtmsg("GDrive", {"Error": "Empty directory"}),
                parse_mode="html",
                time=10,
            )
        folder_id = await _gd_upload(mone, file_path)
        if not folder_id or isinstance(folder_id, str) and "Error" in str(folder_id):
            return await eor(
                mone,
                fmtmsg("GDrive", {"Error": str(folder_id)}),
                parse_mode="html",
                time=10,
            )
        c = 0
        for f in sorted(files):
            if os.path.isfile(f):
                try:
                    await _gd_upload(mone, f, folder_id=folder_id)
                    c += 1
                except Exception as er:
                    logging.getLogger("gdrive").exception("folder upload: %s", er)
        await mone.edit(
            fmtmsg(
                "GDrive",
                {
                    "Status": "✅ Folder uploaded",
                    "Files": c,
                    "Link": rawhtml(
                        f"<a href='https://drive.google.com/folderview?id={folder_id}'>View Folder</a>"
                    ),
                },
            ),
            parse_mode="html",
        )
        return

    link, err = await _gd_upload(mone, file_path, folder_id=folder_id)
    if err:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    await mone.edit(
        fmtmsg(
            "GDrive",
            {
                "Status": "✅ Uploaded",
                "Link": rawhtml(f"<a href='{link}'>View on Drive</a>"),
                "Time": fns.time_formatter((time.time() - t) * 1000),
            },
        ),
        parse_mode="html",
    )


@ultroid_cmd(pattern=r"gdown(?:\s+(.*))?$", fullsudo=True)
async def gdown(e):
    """Download file from Google Drive."""
    args = (e.pattern_match.group(1) or "").strip()
    if not args:
        return await eod(
            e,
            fmtmsg("GDrive", {"Usage": f"{HNDLR}gdown <file_id/link>"}),
            parse_mode="html",
            time=10,
        )

    # Parse filename if provided
    filename = None
    if " | " in args:
        parts = args.split(" | ", 1)
        args = parts[0]
        filename = parts[1].strip()

    eve = await e.eor(fmtmsg("GDrive", {"Status": "Downloading..."}))
    start = time.time()
    result, err = await _gd_download(eve, args, filename)

    if err:
        return await eor(
            eve, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    elapsed = fns.time_formatter((time.time() - start) * 1000)
    await eor(
        eve,
        fmtmsg(
            "GDrive",
            {
                "Status": "✅ Downloaded",
                "File": f"`{result}`",
                "Time": elapsed,
            },
        ),
        parse_mode="html",
        time=5,
    )


@ultroid_cmd(pattern=r"gdsearch(?:\s+(.*))?$")
async def gdsearch(e):
    """Search files on Google Drive."""
    query = (e.pattern_match.group(1) or "").strip()
    if not query:
        return await eod(
            e,
            fmtmsg("GDrive", {"Usage": f"{HNDLR}gdsearch <query>"}),
            parse_mode="html",
            time=10,
        )

    gd, err = await _get_gdrive()
    if err:
        return await eor(
            e, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    mone = await e.eor(
        fmtmsg("GDrive", {"Status": f"Searching: <code>{query}</code>..."})
    )
    try:
        files = gd.search(query)
    except Exception as er:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": str(er)}), parse_mode="html", time=10
        )

    if not files:
        return await eor(
            mone,
            fmtmsg("GDrive", {"Status": "📭 No results"}),
            parse_mode="html",
            time=5,
        )

    file_list = {}
    for link, name in files.items():
        file_id = link.split("id=")[-1] if "id=" in link else link.split("/")[-1]
        file_list[file_id] = {"name": name, "id": file_id}

    html = _build_file_table(file_list)
    await mone.edit(
        fmtmsg(
            "GDrive Search",
            {
                "Query": f"`{query}`",
                "Results": len(file_list),
                "Table": html,
            },
        ),
        parse_mode="html",
    )


@ultroid_cmd(pattern=r"gdlist(?:\s+(.*))?$")
async def gdlist(e):
    """List all Google Drive files."""
    args = (e.pattern_match.group(1) or "").strip()
    details = args.startswith("-d ") if args else False
    folder_id = None
    if "-f " in args:
        parts = args.split("-f ", 1)
        if len(parts) > 1:
            folder_id = parts[1].strip()

    gd, err = await _get_gdrive()
    if err:
        return await eor(
            e, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    mone = await e.eor(fmtmsg("GDrive", {"Status": "Fetching files..."}))
    try:
        files, err = await _gd_list(gd, folder_id)
    except Exception as er:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": str(er)}), parse_mode="html", time=10
        )

    if err:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    table = _build_file_table(files, show_details=details)

    buttons = None
    if len(files) > 10:
        buttons = _build_drive_keyboard(files)

    await mone.edit(
        fmtmsg(
            "GDrive Files",
            {
                "Count": len(files),
                "Details": "✅" if details else "❌",
                "Table": table,
            },
        ),
        parse_mode="html",
    )
    # Add keyboard if too many files
    if buttons:
        await send_rich(
            mone.client,
            e.chat_id,
            RichMarkup("<b>📋 Navigate pages:</b>", "html"),
            buttons=buttons,
        )


@ultroid_cmd(pattern=r"gdinfo(?:\s+(.*))?$")
async def gdinfo(e):
    """Show detailed info about a Drive file."""
    file_id = (e.pattern_match.group(1) or "").strip()
    if not file_id:
        return await eod(
            e,
            fmtmsg("GDrive", {"Usage": f"{HNDLR}gdinfo <file_id>"}),
            parse_mode="html",
            time=10,
        )

    gd, err = await _get_gdrive()
    if err:
        return await eor(
            e, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    mone = await e.eor(fmtmsg("GDrive", {"Status": "Fetching details..."}))
    try:
        details, err = await _gd_get_details(gd, file_id)
    except Exception as er:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": str(er)}), parse_mode="html", time=10
        )

    if err or not details:
        return await eor(
            mone,
            fmtmsg("GDrive", {"Error": "File not found"}),
            parse_mode="html",
            time=10,
        )

    name = details.get("name", "Unknown")
    mime = details.get("mimeType", "unknown")
    size = fmtbyte(details.get("size", 0))
    modified = details.get("modifiedTime", "N/A")
    created = details.get("createdTime", "N/A")
    owner = details.get("ownedByMe", True)
    trashed = details.get("trashed", False)
    viewers = details.get("viewersCanCopyContent", True)
    icon = _fmt_mime(mime)

    rows = {
        "Name": f"{icon} <b>{name}</b>",
        "Type": f"<code>{mime}</code>",
        "Size": size,
        "Modified": modified,
        "Created": created,
        "Owner": "✅ You" if owner else "❌ Shared",
        "Trashed": "✅ Yes" if trashed else "❌ No",
        "Copyable": "✅" if viewers else "❌",
    }
    padd = max(len(str(k)) for k in rows)
    body = "\n".join(f"  <code>{k.ljust(padd)}</code> : {v}" for k, v in rows.items())

    await eor(
        mone, fmtmsg("GDrive File", {"Details": body}), parse_mode="html", time=12
    )


@ultroid_cmd(pattern=r"gdmove(?:\s+(.*))?$")
async def gdmove(e):
    """Move a file to a different folder."""
    args = (e.pattern_match.group(1) or "").strip()
    if not args or " " not in args:
        return await eod(
            e,
            fmtmsg("GDrive", {"Usage": f"{HNDLR}gdmove <file_id> <dest_folder_id>"}),
            parse_mode="html",
            time=10,
        )

    file_id, dest_folder = args.split(None, 1)

    gd, err = await _get_gdrive()
    if err:
        return await eor(
            e, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    mone = await e.eor(fmtmsg("GDrive", {"Status": "Moving..."}))
    result, err = await _gd_move(gd, file_id, dest_folder)
    if err:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    await eor(
        mone,
        fmtmsg(
            "GDrive",
            {"Status": "✅ Moved", "File": f"`{file_id}`", "To": f"`{dest_folder}`"},
        ),
        parse_mode="html",
        time=5,
    )


@ultroid_cmd(pattern=r"gdcp(?:\s+(.*))?$")
async def gdcp(e):
    """Copy a file to a different folder."""
    args = (e.pattern_match.group(1) or "").strip()
    if not args or " " not in args:
        return await eod(
            e,
            fmtmsg("GDrive", {"Usage": f"{HNDLR}gdcp <file_id> <dest_folder_id>"}),
            parse_mode="html",
            time=10,
        )

    file_id, dest_folder = args.split(None, 1)

    gd, err = await _get_gdrive()
    if err:
        return await eor(
            e, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    mone = await e.eor(fmtmsg("GDrive", {"Status": "Copying..."}))
    result, err = await _gd_copy(gd, file_id, dest_folder)
    if err:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    await eor(
        mone,
        fmtmsg(
            "GDrive",
            {"Status": "✅ Copied", "File": f"`{file_id}`", "To": f"`{dest_folder}`"},
        ),
        parse_mode="html",
        time=5,
    )


@ultroid_cmd(pattern=r"gdtrash(?:\s+(.*))?$", fullsudo=True)
async def gdtrash(e):
    """Move file to trash."""
    file_id = (e.pattern_match.group(1) or "").strip()
    if not file_id:
        return await eod(
            e,
            fmtmsg("GDrive", {"Usage": f"{HNDLR}gdtrash <file_id>"}),
            parse_mode="html",
            time=10,
        )

    gd, err = await _get_gdrive()
    if err:
        return await eor(
            e, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    mone = await e.eor(fmtmsg("GDrive", {"Status": "Trashing..."}))
    result, err = await _gd_trash(gd, file_id)
    if err:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    await eor(
        mone,
        fmtmsg("GDrive", {"Status": "🗑️ Trashed", "File": f"`{file_id}`"}),
        parse_mode="html",
        time=5,
    )


@ultroid_cmd(pattern=r"gddelete(?:\s+(.*))?$", fullsudo=True)
async def gddelete(e):
    """Permanently delete a file."""
    file_id = (e.pattern_match.group(1) or "").strip()
    if not file_id:
        return await eod(
            e,
            fmtmsg("GDrive", {"Usage": f"{HNDLR}gddelete <file_id>"}),
            parse_mode="html",
            time=10,
        )

    gd, err = await _get_gdrive()
    if err:
        return await eor(
            e, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    mone = await e.eor(fmtmsg("GDrive", {"Status": "Deleting..."}))
    result, err = await _gd_delete(gd, file_id)
    if err:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    await eor(
        mone,
        fmtmsg("GDrive", {"Status": "❌ Deleted permanently", "File": f"`{file_id}`"}),
        parse_mode="html",
        time=5,
    )


@ultroid_cmd(pattern=r"gdshare(?:\s+(.*))?$")
async def gdshare(e):
    """Share a file with an email address."""
    args = (e.pattern_match.group(1) or "").strip()
    if not args or " " not in args:
        return await eod(
            e,
            fmtmsg("GDrive", {"Usage": f"{HNDLR}gdshare <file_id> <email>"}),
            parse_mode="html",
            time=10,
        )

    file_id, email = args.split(None, 1)

    gd, err = await _get_gdrive()
    if err:
        return await eor(
            e, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    mone = await e.eor(fmtmsg("GDrive", {"Status": f"Sharing with {email}..."}))
    result, err = await _gd_share(gd, file_id, email)
    if err:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    await eor(
        mone,
        fmtmsg(
            "GDrive",
            {"Status": "✅ Shared", "File": f"`{file_id}`", "With": f"`{email}`"},
        ),
        parse_mode="html",
        time=5,
    )


@ultroid_cmd(pattern=r"gdnewfolder(?:\s+(.*))?$")
async def gdnewfolder(e):
    """Create a new folder on Google Drive."""
    name = (e.pattern_match.group(1) or "").strip()
    if not name:
        return await eod(
            e,
            fmtmsg("GDrive", {"Usage": f"{HNDLR}gdnewfolder <name>"}),
            parse_mode="html",
            time=10,
        )

    gd, err = await _get_gdrive()
    if err:
        return await eor(
            e, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    mone = await e.eor(
        fmtmsg("GDrive", {"Status": f"Creating folder: <code>{name}</code>..."})
    )
    result, err = await _gd_create_folder(gd, name)
    if err:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    folder_id = result.get("id", "unknown")
    link = f"https://drive.google.com/folderview?id={folder_id}"
    await eor(
        mone,
        fmtmsg(
            "GDrive",
            {
                "Status": "✅ Folder created",
                "Name": f"`{name}`",
                "ID": f"`{folder_id}`",
                "Link": rawhtml(f"<a href='{link}'>Open Folder</a>"),
            },
        ),
        parse_mode="html",
        time=5,
    )


@ultroid_cmd(pattern="gdfolder$")
async def gdfolder(e):
    """Show Drive folder link."""
    gd, err = await _get_gdrive()
    if err:
        return await eor(
            e, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    folder_id = gd.folder_id
    if folder_id:
        link = gd._create_folder_link(folder_id)
        await eor(
            e,
            fmtmsg(
                "GDrive",
                {
                    "Status": "📁 Folder link",
                    "Link": rawhtml(f"<a href='{link}'>{link}</a>"),
                    "ID": f"`{folder_id}`",
                },
            ),
            parse_mode="html",
            time=5,
        )
    else:
        await eor(
            e,
            fmtmsg("GDrive", {"Info": "No folder ID set. Set GDRIVE_FOLDER_ID"}),
            parse_mode="html",
            time=5,
        )


@ultroid_cmd(pattern="gdrefresh$")
async def gdrefresh(e):
    """Refresh Drive file cache and list."""
    gd, err = await _get_gdrive()
    if err:
        return await eor(
            e, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    mone = await e.eor(fmtmsg("GDrive", {"Status": "Refreshing..."}))
    files, err = await _gd_list(gd)
    if err:
        return await eor(
            mone, fmtmsg("GDrive", {"Error": err}), parse_mode="html", time=10
        )

    table = _build_file_table(files)
    await mone.edit(
        fmtmsg(
            "GDrive Files",
            {
                "Status": "✅ Refreshed",
                "Count": len(files),
                "Table": table,
            },
        ),
        parse_mode="html",
    )


# --------------------------------------------------------------------------- #
#  Assistant-bot commands (owner-only)                                      #
# --------------------------------------------------------------------------- #


@asst_cmd(pattern=r"gdul(?:\s+(.*))?$", owner=True)
async def asst_gdul(e):
    await e.reply(
        fmtmsg("GDrive", {"Note": "Use /gdul on user account"}), parse_mode="html"
    )


@asst_cmd(pattern=r"gdown(?:\s+(.*))?$", owner=True)
async def asst_gdown(e):
    await e.reply(
        fmtmsg("GDrive", {"Note": "Use /gdown on user account"}), parse_mode="html"
    )


@asst_cmd(pattern=r"gdsearch(?:\s+(.*))?$", owner=True)
async def asst_gdsearch(e):
    await e.reply(
        fmtmsg("GDrive", {"Note": "Use /gdsearch on user account"}), parse_mode="html"
    )


@asst_cmd(pattern=r"gdlist(?:\s+(.*))?$", owner=True)
async def asst_gdlist(e):
    await e.reply(
        fmtmsg("GDrive", {"Note": "Use /gdlist on user account"}), parse_mode="html"
    )


@asst_cmd(pattern=r"gdinfo(?:\s+(.*))?$", owner=True)
async def asst_gdinfo(e):
    await e.reply(
        fmtmsg("GDrive", {"Note": "Use /gdinfo on user account"}), parse_mode="html"
    )


# --------------------------------------------------------------------------- #
#  Inline mode                                                              #
# --------------------------------------------------------------------------- #


@in_pattern(r"gdsearch(.*)", owner=True)
async def gdsearch_inline(event):
    query = (event.pattern_match.group(1) or "").strip()
    builder = InlineResultBuilder(event)
    if not query:
        article = await builder.article(
            title="GDrive Search",
            text="Usage: @bot gdsearch <query>",
            description="Search Google Drive files",
        )
        return await event.answer([article] if article else [])
    key = _store_pending({"text": query, "receiver": None})
    buttons = ikm([[("🔍 Search", cb("assistant", "gdsearch", key), "info")]])
    article = await builder.article(
        title=f"Search: {query}",
        text="Tap to search Google Drive.",
        description="Drive search",
        buttons=buttons,
    )
    await event.answer([article] if article else [])


@callback(data=cb_pattern("assistant", "gdsearch"), owner=True)
async def gdsearch_inline_send(event):
    await _inline_deliver(event, action="gdsearch")


@in_pattern(r"gdlist(.*)", owner=True)
async def gdlist_inline(event):
    builder = InlineResultBuilder(event)
    article = await builder.article(
        title="GDrive List",
        text="Tap to list Drive files.",
        description="List all Drive files",
    )
    await event.answer([article] if article else [])


@callback(data=cb_pattern("assistant", "gdlist"), owner=True)
async def gdlist_inline_send(event):
    await _inline_deliver(event, action="gdlist")


async def _inline_deliver(event, *, action):
    key = _pending_tail(event)
    pending = _PENDING.pop(key, None)
    if not pending or time.time() > pending.get("exp", 0):
        return await event.answer("Expired — send the inline query again.", alert=True)
    text = pending.get("text", "")
    try:
        if action == "gdsearch":
            await event.answer("Use /gdsearch <query> on your account.", alert=True)
        elif action == "gdlist":
            await event.answer("Use /gdlist on your account.", alert=True)
    except Exception as er:
        return await event.answer(f"Failed: {er}", alert=True)


# --------------------------------------------------------------------------- #
#  Session listener (non-intrusive)                                         #
# --------------------------------------------------------------------------- #


async def _on_gdrive_update(update):
    pass


def _register_listeners():
    from pyUltroid.startup.dispatcher import register_listen
    from telethon.tl.types import UpdateNewChannelMessage, UpdateNewMessage

    register_listen(
        _on_gdrive_update,
        client=ultroid_bot,
        listen=(UpdateNewMessage, UpdateNewChannelMessage),
        once=False,
        name="gdrive_updates",
    )


_register_listeners()

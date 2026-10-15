# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""
✘ Commands Available

• ``{i}zip <text>`` — create a zip archive from text content
  ``{i}zip <file_id>`` — zip by file ID
  ``{i}zip -p <password> <text>`` — password-protected zip
  ``{i}zip -l <level> <text>`` — compression level 0-9 (default 6)
  ``{i}zip -m <method> <text>`` — method: deflate, bz2, lzma, store (default deflate)

• ``{i}unzip <file_id>`` — extract zip archive
  ``{i}unzip -p <file_id>`` — preview contents without extracting
  ``{i}unzip -d <file_id>`` — show details (sizes, ratios, CRC)
  ``{i}unzip -o <dir> <file_id>`` — extract to specific directory
  ``{i}unzip -t <file_id>`` — test archive integrity

• ``{i}list <file_id>`` — list archive contents in a table
  ``{i}list -d <file_id>`` — list with details

• ``{i}azip <reply to file>`` — add file to batch for batch zip
• ``{i}dozip <password>`` — create batch zip (optional password)
• ``{i}dozip -c <count>`` — compress batch with compression level

✘ Assistant-bot commands (owner-only):
* ``/zip``, ``/unzip``, ``/list``, ``/azip``, ``/dozip`` — same surface.

✘ Inline mode (selection via assistant, delivery via user account):
* ``@<assistant> zip <text>`` or ``@<assistant> unzip <file_id>`` —
  the assistant returns a coloured action button. Tapping it delivers
  the zip/unzip from your user account. Text is kept in-memory only.
"""

import os
import time
import zipfile

from custom.fmt import fmtmsg
from custom.rich import parse_rich
from pyUltroid.fns import fns
from . import (
    HNDLR,
    ULTConfig,
    get_string,
    ultroid_cmd,
    eor,
)

# --------------------------------------------------------------------------- #
#  Constants & Helpers                                                        #
# --------------------------------------------------------------------------- #

COMP_METHODS = {
    "deflate": zipfile.ZIP_DEFLATED,
    "bz2": zipfile.ZIP_BZIP2,
    "lzma": zipfile.ZIP_LZMA,
    "store": zipfile.ZIP_STORED,
}

COMP_METHOD_NAMES = {
    zipfile.ZIP_DEFLATED: "deflate",
    zipfile.ZIP_BZIP2: "bz2",
    zipfile.ZIP_LZMA: "lzma",
    zipfile.ZIP_STORED: "store",
}

_BATCH_DIR = "zipbatch"
_ZIP_CACHE_DIR = "zips"
_PENDING = {}
_PENDING_TTL = 300
_SEQ = 0


def _prune_pending():
    now = time.time()
    for key in [k for k, v in _PENDING.items() if now > v.get("exp", 0)]:
        _PENDING.pop(key, None)


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


def _fmt_size(nbytes):
    """Format bytes to human-readable size."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(nbytes) < 1024.0:
            return f"{nbytes:.1f}{unit}"
        nbytes /= 1024.0
    return f"{nbytes:.1f}PB"


def _file_info(info):
    """Extract detailed info from a ZipInfo object."""
    return {
        "filename": info.filename,
        "size": info.file_size,
        "compressed": info.compress_size,
        "ratio": (
            (1 - info.compress_size / info.file_size) * 100 if info.file_size else 0
        ),
        "method": COMP_METHOD_NAMES.get(info.compress_type, "unknown"),
        "crc": f"{info.CRC:08x}",
        "date": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(info.date_time)),
    }


def _build_table(files_info, show_details=False):
    """Build an HTML table from file info list."""
    if not files_info:
        return "<b>No files in archive.</b>"

    if show_details:
        header = (
            "<table border='1' cellpadding='6' cellspacing='0' "
            "style='border-collapse:collapse;width:100%'>"
            "<tr style='background:#2ECC71'>"
            "<th>File</th><th>Size</th><th>Compressed</th>"
            "<th>Ratio</th><th>Method</th><th>CRC32</th><th>Date</th>"
            "</tr>"
        )
        rows = ""
        for f in files_info:
            rows += (
                f"<tr><td>{f['filename']}</td>"
                f"<td>{_fmt_size(f['size'])}</td>"
                f"<td>{_fmt_size(f['compressed'])}</td>"
                f"<td>{f['ratio']:.1f}%</td>"
                f"<td>{f['method']}</td>"
                f"<td><code>{f['crc']}</code></td>"
                f"<td>{f['date']}</td></tr>"
            )
    else:
        header = (
            "<table border='1' cellpadding='6' cellspacing='0' "
            "style='border-collapse:collapse;width:100%'>"
            "<tr style='background:#2ECC71'>"
            "<th>#</th><th>File</th><th>Size</th>"
            "</tr>"
        )
        rows = ""
        for i, f in enumerate(files_info, 1):
            rows += (
                f"<tr><td>{i}</td><td>{f['filename']}</td>"
                f"<td>{_fmt_size(f['size'])}</td></tr>"
            )

    return header + rows + "</table>"





#  Core zip/unzip primitives (Python zipfile — fast, no shell)             #
async def _create_zip(
    source_paths,
    output_path,
    password=None,
    level=6,
    method="deflate",
):
    """Create a zip archive from a list of source file paths."""
    method_val = COMP_METHODS.get(method, zipfile.ZIP_DEFLATED)
    compression = (
        zipfile.ZIP_DEFLATED if method == "deflate" and level < 9 else method_val
    )
    kwargs = {
        "compression": compression,
    }
    if compression == zipfile.ZIP_DEFLATED:
        kwargs["compresslevel"] = level
    if password:
        kwargs["password"] = password.encode()

    with zipfile.ZipFile(output_path, "w", **kwargs) as zf:
        for src in source_paths:
            if os.path.isfile(src):
                arcname = os.path.basename(src)
                zf.write(src, arcname)
            elif os.path.isdir(src):
                for root, _, files in os.walk(src):
                    for fname in files:
                        full = os.path.join(root, fname)
                        arcname = os.path.relpath(full, src)
                        zf.write(full, arcname)


async def _extract_zip(zip_path, dest_dir, password=None):
    """Extract a zip archive to dest_dir using Python zipfile."""
    os.makedirs(dest_dir, exist_ok=True)
    kwargs = {}
    if password:
        kwargs["pwd"] = password.encode()
    with zipfile.ZipFile(zip_path, "r", **kwargs) as zf:
        bad = zf.testzip()
        if bad:
            return False, bad
        zf.extractall(dest_dir)
    return True, None


async def _list_zip(zip_path):
    """List all files in a zip archive."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        return zf.infolist()


async def _test_zip(zip_path):
    """Test zip archive integrity. Returns (ok, bad_file)."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        bad = zf.testzip()
        return bad is None, bad


def _get_zip_info(zip_path):
    """Get detailed info about a zip archive."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        total_size = sum(f.file_size for f in zf.infolist())
        total_compressed = sum(f.compress_size for f in zf.infolist())
        method = COMP_METHOD_NAMES.get(
            zf.infolist()[0].compress_type if zf.infolist() else zipfile.ZIP_DEFLATED,
            "unknown",
        )
        return {
            "file_count": len(zf.infolist()),
            "total_size": total_size,
            "total_compressed": total_compressed,
            "ratio": (1 - total_compressed / total_size) * 100 if total_size else 0,
            "method": method,
            "comment": zf.comment.decode() if zf.comment else "",
            "zip_version": zf.version,
        }


async def _resolve_file(client, file_id_or_path):
    """Resolve a file from file_id or path."""
    if os.path.isfile(file_id_or_path):
        return file_id_or_path
    if os.path.isfile(os.path.join(_ZIP_CACHE_DIR, file_id_or_path)):
        return os.path.join(_ZIP_CACHE_DIR, file_id_or_path)
    if os.path.isfile(os.path.join("unzip", file_id_or_path)):
        return os.path.join("unzip", file_id_or_path)
    return None


# --------------------------------------------------------------------------- #
#  User commands                                                               #
# --------------------------------------------------------------------------- #


@ultroid_cmd(pattern=r"zip(?:\s+(.*))?$")
async def zip_cmd(event):
    """Create a zip archive. Supports -p (password), -l (level), -m (method)."""
    args = (event.pattern_match.group(1) or "").strip()
    reply = await event.get_reply_message()

    # Parse flags
    password = None
    level = 6
    method = "deflate"
    text = args

    if args.startswith("-p "):
        parts = args.split(None, 2)
        if len(parts) >= 3:
            password = parts[1]
            text = parts[2] if len(parts) > 2 else ""
        else:
            return await eor(
                event,
                fmtmsg("Zip", {"Error": "Usage: zip -p <password> <text>"}),
                parse_mode="v2",
                time=10,
            )
    elif args.startswith("-l "):
        parts = args.split(None, 2)
        if len(parts) >= 3:
            try:
                level = int(parts[1])
                if not 0 <= level <= 9:
                    raise ValueError
            except ValueError:
                return await eor(
                    event,
                    fmtmsg("Zip", {"Error": "Level must be 0-9"}),
                    parse_mode="v2",
                    time=10,
                )
            text = parts[2] if len(parts) > 2 else ""
        else:
            return await eor(
                event,
                fmtmsg("Zip", {"Error": "Usage: zip -l <0-9> <text>"}),
                parse_mode="v2",
                time=10,
            )
    elif args.startswith("-m "):
        parts = args.split(None, 2)
        if len(parts) >= 3:
            method = parts[1].lower()
            if method not in COMP_METHODS:
                return await eor(
                    event,
                    fmtmsg(
                        "Zip", {"Error": f"Method: {', '.join(COMP_METHODS.keys())}"}
                    ),
                    parse_mode="v2",
                    time=10,
                )
            text = parts[2] if len(parts) > 2 else ""
        else:
            return await eor(
                event,
                fmtmsg(
                    "Zip", {"Error": "Usage: zip -m <deflate|bz2|lzma|store> <text>"}
                ),
                parse_mode="v2",
                time=10,
            )

    if not text and not reply:
        return await eor(
            event,
            fmtmsg(
                "Zip",
                {
                    "Usage": f"{HNDLR}zip -p <pw> <text> | reply to file | use -l/-m flags"
                },
            ),
            parse_mode="v2",
            time=10,
        )

    x = await eor(event, get_string("com_1"))
    t = time.time()

    if text and not reply:
        # Create zip from text content
        output_path = os.path.join(_ZIP_CACHE_DIR, f"ultroid_{int(t)}.zip")
        os.makedirs(_ZIP_CACHE_DIR, exist_ok=True)
        tmp_file = os.path.join(_ZIP_CACHE_DIR, f"tmp_{int(t)}.txt")
        clean, _ = parse_rich(text, "mdv3") if "{<b>" not in text else (text, None)
        with open(tmp_file, "w") as f:
            f.write(clean)
        await _create_zip(
            [tmp_file], output_path, password=password, level=level, method=method
        )
        os.remove(tmp_file)
    elif reply and reply.media:
        # Download and zip the replied file
        if hasattr(reply.media, "document"):
            file = await fns.downloader(
                reply.file.name, reply.media.document, x, t, get_string("com_5")
            )
        else:
            file = await event.download_media(reply)
        if not file:
            return await eor(
                event,
                fmtmsg("Zip", {"Error": "Failed to download file"}),
                parse_mode="v2",
                time=10,
            )
        output_path = os.path.join(_ZIP_CACHE_DIR, f"ultroid_{int(t)}.zip")
        os.makedirs(_ZIP_CACHE_DIR, exist_ok=True)
        await _create_zip(
            [file], output_path, password=password, level=level, method=method
        )
    else:
        return await eor(
            event,
            fmtmsg("Zip", {"Error": "Reply to a file or provide text content"}),
            parse_mode="v2",
            time=10,
        )

    k = time.time()
    await fns.uploader(
        output_path, os.path.basename(output_path), k, x, get_string("com_6")
    )
    await event.client.send_file(
        event.chat_id,
        output_path,
        force_document=True,
        thumb=ULTConfig.thumb,
        caption=f"`{os.path.basename(output_path)}`",
        reply_to=reply,
    )
    os.remove(output_path)
    await x.delete()


@ultroid_cmd(pattern=r"unzip(?:\s+(.*))?$")
async def unzip_cmd(event):
    """Extract a zip archive. Supports -p (preview), -d (details), -o (output dir), -t (test)."""
    args = (event.pattern_match.group(1) or "").strip()
    reply = await event.get_reply_message()

    preview = False
    details = False
    output_dir = "unzip"
    test_only = False
    file_id = None

    # Parse flags
    parts = args.split(None, 1)
    if parts and parts[0].startswith("-p"):
        preview = True
        file_id = parts[1].strip() if len(parts) > 1 else ""
    elif parts and parts[0].startswith("-d"):
        details = True
        file_id = parts[1].strip() if len(parts) > 1 else ""
    elif parts and parts[0].startswith("-o"):
        output_dir = parts[1].strip() if len(parts) > 1 else "unzip"
        file_id = ""
    elif parts and parts[0].startswith("-t"):
        test_only = True
        file_id = parts[1].strip() if len(parts) > 1 else ""
    else:
        file_id = args

    if not file_id and reply and reply.media:
        if hasattr(reply.media, "document"):
            if not reply.file.name.endswith(".zip"):
                return await eor(
                    event,
                    fmtmsg("Zip", {"Error": get_string("zip_3")}),
                    parse_mode="v2",
                    time=10,
                )
            x = await eor(event, get_string("com_1"))
            t = time.time()
            file_path = await fns.downloader(
                reply.file.name, reply.media.document, x, t, get_string("com_5")
            )
        else:
            return await eor(
                event,
                fmtmsg("Zip", {"Error": "Reply to a zip file"}),
                parse_mode="v2",
                time=10,
            )
    elif file_id:
        file_path = await _resolve_file(event.client, file_id)
        if not file_path:
            return await eor(
                event,
                fmtmsg("Zip", {"Error": "File not found"}),
                parse_mode="v2",
                time=10,
            )
        x = await eor(event, get_string("com_1"))
    else:
        return await eor(
            event,
            fmtmsg(
                "Zip",
                {"Usage": f"{HNDLR}unzip <file_id> | reply to zip | -p/-d/-o/-t flags"},
            ),
            parse_mode="v2",
            time=10,
        )

    if not os.path.isfile(file_path):
        return await eor(
            event,
            fmtmsg("Zip", {"Error": "Zip file not found"}),
            parse_mode="v2",
            time=10,
        )

    if test_only:
        ok, bad = await _test_zip(file_path)
        if ok:
            await eor(
                event,
                fmtmsg("Zip Test", {"Status": "✅ Archive is valid"}),
                parse_mode="v2",
                time=8,
            )
        else:
            await eor(
                event,
                fmtmsg("Zip Test", {"Status": "❌", "Bad file": bad}),
                parse_mode="v2",
                time=8,
            )
        return

    if preview:
        infolist = await _list_zip(file_path)
        files_info = [_file_info(info) for info in infolist]
        table = _build_table(files_info)
        count = len(infolist)
        await eor(
            event,
            fmtmsg("Zip Preview", {"Files": f"{count} entries", "Table": table}),
            parse_mode="v2",
            time=12,
        )
        return

    if details:
        info = _get_zip_info(file_path)
        infolist = await _list_zip(file_path)
        files_info = [_file_info(info) for info in infolist]
        table = _build_table(files_info, show_details=True)
        await eor(
            event,
            fmtmsg(
                "Zip Details",
                {
                    "Count": info["file_count"],
                    "Size": _fmt_size(info["total_size"]),
                    "Compressed": _fmt_size(info["total_compressed"]),
                    "Ratio": f"{info['ratio']:.1f}%",
                    "Method": info["method"],
                    "Version": info["zip_version"],
                    "Table": table,
                },
            ),
            parse_mode="v2",
            time=12,
        )
        return

    # Regular extract
    if os.path.isdir(output_dir):
        await fns.bash(f"rm -rf {output_dir}")
    ok, bad = await _extract_zip(file_path, output_dir)
    if not ok:
        return await eor(
            event,
            fmtmsg("Zip", {"Error": f"Corrupted archive: {bad}"}),
            parse_mode="v2",
            time=10,
        )

    extracted = fns.get_all_files(output_dir)
    files_info = []
    for f in extracted:
        stat = os.stat(f)
        files_info.append(
            {"filename": os.path.relpath(f, output_dir), "size": stat.st_size}
        )

    table = _build_table(files_info)
    await eor(
        event,
        fmtmsg(
            "Zip Extracted",
            {
                "Count": len(extracted),
                "Table": table,
                "Dir": f"`{output_dir}/`",
            },
        ),
        parse_mode="v2",
        time=12,
    )
    if file_path and "unzip" in os.path.dirname(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass


@ultroid_cmd(pattern=r"list(?:\s+(.*))?$")
async def list_cmd(event):
    """List archive contents in a table. Supports -d for details."""
    args = (event.pattern_match.group(1) or "").strip()
    reply = await event.get_reply_message()
    details_flag = False
    file_id = args

    if args.startswith("-d "):
        details_flag = True
        file_id = args[3:].strip()

    if not file_id and reply and reply.media:
        if hasattr(reply.media, "document") and reply.file.name.endswith(".zip"):
            x = await eor(event, get_string("com_1"))
            t = time.time()
            file_path = await fns.downloader(
                reply.file.name, reply.media.document, x, t, get_string("com_5")
            )
        else:
            return await eor(
                event,
                fmtmsg("Zip", {"Error": "Reply to a zip file"}),
                parse_mode="v2",
                time=10,
            )
    elif file_id:
        file_path = await _resolve_file(event.client, file_id)
        if not file_path:
            return await eor(
                event,
                fmtmsg("Zip", {"Error": "File not found"}),
                parse_mode="v2",
                time=10,
            )
        x = await eor(event, get_string("com_1"))
    else:
        return await eor(
            event,
            fmtmsg("Zip", {"Usage": f"{HNDLR}list <file_id> | reply to zip | -d flag"}),
            parse_mode="v2",
            time=10,
        )

    infolist = await _list_zip(file_path)
    files_info = [_file_info(info) for info in infolist]
    table = _build_table(files_info, show_details=details_flag)
    await eor(
        event,
        fmtmsg(
            "Archive Contents",
            {
                "Count": len(infolist),
                "Details": "✅" if details_flag else "❌",
                "Table": table,
            },
        ),
        parse_mode="v2",
        time=12,
    )
    if file_path and "unzip" in os.path.dirname(file_path):
        try:
            os.remove(file_path)
        except Exception:
            pass


@ultroid_cmd(pattern="azip$")
async def azipp(event):
    """Add a file to the batch for batch zip upload."""
    reply = await event.get_reply_message()
    t = time.time()
    if not (reply and reply.media):
        return await eor(
            event,
            fmtmsg("Zip", {"Error": get_string("zip_1")}),
            parse_mode="v2",
            time=10,
        )

    x = await eor(event, get_string("com_1"))
    os.makedirs(_BATCH_DIR, exist_ok=True)

    if hasattr(reply.media, "document"):
        file = await fns.downloader(
            f"{_BATCH_DIR}/{reply.file.name}",
            reply.media.document,
            x,
            t,
            get_string("com_5"),
        )
    else:
        file = await event.download_media(reply.media, _BATCH_DIR)

    await eor(
        event,
        fmtmsg(
            "Batch",
            {
                "Status": "Added",
                "File": f"`{file}`",
                "Hint": "Reply to other files then use `dozip`",
            },
        ),
        parse_mode="v2",
        time=8,
    )


@ultroid_cmd(pattern=r"dozip(?:\s+(.*))?$")
async def do_zip(event):
    """Create batch zip from files added via `azip`. Supports password and compression level."""
    args = (event.pattern_match.group(1) or "").strip()
    password = None
    level = 6
    method = "deflate"

    if args.startswith("-p "):
        parts = args.split(None, 2)
        if len(parts) >= 3:
            password = parts[1]
        level = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 6
    elif args.startswith("-c "):
        parts = args.split(None, 2)
        if len(parts) >= 3:
            level = int(parts[1]) if parts[1].isdigit() else 6
            method = parts[2].lower() if len(parts) > 2 else "deflate"

    if not os.path.isdir(_BATCH_DIR):
        return await eor(
            event,
            fmtmsg("Zip", {"Error": get_string("zip_2").format(HNDLR)}),
            parse_mode="v2",
            time=10,
        )

    x = await eor(event, get_string("com_1"))
    files = fns.get_all_files(_BATCH_DIR)
    if not files:
        return await eor(
            event,
            fmtmsg("Zip", {"Error": "No files in batch"}),
            parse_mode="v2",
            time=10,
        )

    output_path = os.path.join(_ZIP_CACHE_DIR, "ultroid.zip")
    os.makedirs(_ZIP_CACHE_DIR, exist_ok=True)
    await _create_zip(files, output_path, password=password, level=level, method=method)

    k = time.time()
    await fns.uploader(output_path, "ultroid.zip", k, x, get_string("com_6"))
    await event.client.send_file(
        event.chat_id,
        output_path,
        force_document=True,
        thumb=ULTConfig.thumb,
    )
    await fns.bash(f"rm -rf {_BATCH_DIR}")
    os.remove(output_path)
    await x.delete()


# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""Queue-aware download/upload plugin (SarahUB Phase 2.1 rewrite).

Commands
--------

``.dl``
    Download Telegram media by replying to a message (single file or a whole
    album/grouped media) or by passing a ``t.me`` link (single message or a
    ``start-end`` bulk range, e.g. ``https://t.me/c/123/1-10``).

    Flags: ``-t`` (temporary, auto-delete via ``TEMP_TTL``), ``-ttl <seconds>``
    (explicit per-file TTL), ``-n <name>`` (rename; extension is preserved or
    taken as-is), ``-d <dir>`` (move into directory, created if missing),
    ``-del`` (delete the original replied message after download).

``.ul``
    Upload a local file or directory. Glob patterns are supported. A directory
    is shown as an ascending size table (via ``list_directory_files``) and then
    every file is uploaded as its own message.

    Flags: ``-n <name>`` (rename; directories use the ``[index]{name}`` pattern),
    ``-del`` / ``--delete`` (delete local file after a successful upload),
    ``--stream`` (streamable upload), ``--no-thumb`` (no thumbnail).

``.download``
    Download a single direct URL, or reply to a ``.txt`` file containing one URL
    per line for a multi-URL bulk download. Uses ``fast_download`` with a live
    progress bar. Same ``-n``, ``-t``, ``-ttl``, ``-d`` flags as ``.dl``.

``.fcancel``
    Cancel a queued batch (``.fcancel <GID>``) or a single file within a batch
    (``.fcancel <FID>``). Owner/sudo only, works from any chat.

Every bulk ``.dl``, directory ``.ul`` and multi-URL ``.download`` is tracked
through :class:`pyUltroid._core.queue.QueueManager`: a live status table is
edited in the trigger chat and an owner PM notification
(``{mention} triggered {n} file(s) {verb} {mode} process — GID: {gid}``) is
sent on start.
"""

from . import get_help

__doc__ = get_help("help_downloadupload")

import asyncio
import glob
import mimetypes
import os
import re
import shlex
import time
from datetime import datetime as _dt

from telethon.errors.rpcerrorlist import MessageNotModifiedError

from pyUltroid._core.queue import (
    QueueTask,
    list_directory_files,
)
from pyUltroid._misc import owner_and_sudos
from pyUltroid.fns import fns
from pyUltroid.fns import fns
from pyUltroid.ultroid import core

from . import (
    LOGS,
    OWNER_ID,
    ULTConfig,
    get_string,
    udB,
    ultroid_bot,
    ultroid_cmd,
)

# ──────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────

DOWNLOAD_DIR = "resources/downloads/"
TEMP_TTL_DEFAULT = 3600.0
MAX_BULK = 100  # safety cap for range / multi-URL operations

# Per-command flag tables.  Value flags consume the following token; boolean
# flags are stand-alone switches.
_DL_VALUE_FLAGS = {"-n", "-ttl", "-d"}
_DL_BOOL_FLAGS = {"-t", "-del"}
_UL_VALUE_FLAGS = {"-n"}
_UL_BOOL_FLAGS = {"--stream", "--no-thumb", "-del", "--delete"}
_DW_VALUE_FLAGS = {"-n", "-ttl", "-d"}
_DW_BOOL_FLAGS = {"-t"}

# t.me link parser: optional "c/" prefix, chat slug/id, message id or range.
_TG_LINK_RE = re.compile(r"https?://t\.me/(c/)?([^/\s]+)/(?:pg/)?(\d+)(?:-(\d+))?")


# ──────────────────────────────────────────────────────────────────────────
# Argument parsing
# ──────────────────────────────────────────────────────────────────────────


def _parse_flags(text, value_flags, bool_flags):
    """Tokenise *text* with :func:`shlex.split` and split into flags/positionals.

    Returns ``(flags, positionals, unknowns)`` where ``flags`` maps value flags
    to their token and boolean flags to ``True``.  Unknown ``-``-prefixed tokens
    (and value flags missing their value) are collected in ``unknowns`` for the
    caller to reject.  Raises ``ValueError`` on a shlex quoting error.
    """
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise ValueError(f"Quoting error: {exc}") from exc

    flags, positionals, unknowns = {}, [], []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in value_flags:
            if i + 1 < len(tokens):
                flags[tok] = tokens[i + 1]
                i += 2
            else:
                unknowns.append(f"{tok} (missing value)")
                i += 1
        elif tok in bool_flags:
            flags[tok] = True
            i += 1
        elif tok.startswith("-"):
            unknowns.append(tok)
            i += 1
        else:
            positionals.append(tok)
            i += 1
    return flags, positionals, unknowns


def _flag_error_text(cmd, unknowns, valid):
    """Render a compact "unrecognized argument" message with valid flags."""
    return (
        f"`Unrecognized argument(s):` {', '.join(unknowns)}\n"
        f"`Valid flags for .{cmd}:` {', '.join(valid) or 'none'}\n"
        '`Tip:` quote names/paths with spaces, e.g. `.dl -n "My File"`'
    )


def _temp_ttl(flags):
    """Resolve the temp-file TTL (seconds) from ``-ttl`` or the ``TEMP_TTL`` key.

    Returns ``None`` when an explicit ``-ttl`` value is not a valid number so the
    caller can reject it; falls back to ``TEMP_TTL_DEFAULT`` when unset.
    """
    raw = flags.get("-ttl")
    if raw is not None:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if value > 0 else None
    default = udB.get_key("TEMP_TTL")
    try:
        return float(default) if default else TEMP_TTL_DEFAULT
    except (TypeError, ValueError):
        return TEMP_TTL_DEFAULT


# ──────────────────────────────────────────────────────────────────────────
# Link / name / file helpers
# ──────────────────────────────────────────────────────────────────────────


def _parse_tg_link(link):
    """Return ``(chat, ids)`` for a t.me link, where ``ids`` is a list of ints.

    ``chat`` is the ``-100``-prefixed int for private channels or the username
    string for public handles.  Returns ``(None, [])`` on an unrecognised link.
    """
    match = _TG_LINK_RE.search(link)
    if not match:
        return None, []
    is_private, chat, start, end = match.groups()
    if is_private:
        try:
            chat = int("-100" + chat)
        except ValueError:
            return None, []
    start = int(start)
    if end:
        end = int(end)
        if end < start:
            start, end = end, start
        ids = list(range(start, end + 1))
    else:
        ids = [start]
    return chat, ids


def _resolve_name(custom_name, original_name=None, mime=None):
    """Build a download filename from a ``-n`` name and the original file.

    A custom name carrying an extension is used verbatim; a bare name gets the
    original file's extension appended (derived from ``original_name`` or
    ``mime``).  Surrounding spaces are stripped.
    """
    if not custom_name:
        return original_name
    name = custom_name.strip()
    if not name:
        return original_name
    _, ext = os.path.splitext(name)
    if ext:
        return name
    if original_name:
        _, oext = os.path.splitext(original_name)
    elif mime:
        oext = mimetypes.guess_extension(mime.split(";")[0]) or ""
    else:
        oext = ""
    return f"{name}{oext}" if oext else name


def _humanbytes(size):
    """Readable byte size (local copy to avoid an extra import dependency)."""
    if not size:
        return "0 B"
    for unit in ("", "K", "M", "G", "T"):
        if size < 1024:
            break
        size /= 1024
    return f"{size}{unit}B" if isinstance(size, int) else f"{size:.2f}{unit}B"


def _filesize(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _msg_size(message):
    try:
        return (
            int(getattr(message.file, "size", 0) or 0)
            if message and getattr(message, "file", None)
            else 0
        )
    except Exception:
        return 0


# ──────────────────────────────────────────────────────────────────────────
# Temp-file auto-delete
# ──────────────────────────────────────────────────────────────────────────


async def _delete_after(path, delay):
    try:
        await asyncio.sleep(delay)
        if path and os.path.exists(path):
            os.remove(path)
    except Exception as exc:
        LOGS.debug("[downloadupload] temp delete failed for %s: %s", path, exc)


def _mark_temp(path, ttl):
    """Schedule *path* for deletion after *ttl* seconds (best-effort)."""
    if not (path and ttl):
        return
    try:
        asyncio.get_running_loop().create_task(_delete_after(path, ttl))
    except RuntimeError:
        pass


def _post_download(path, flags):
    """Apply ``-d`` (move into directory) and ``-t``/``-ttl`` (temp) to *path*."""
    if not path:
        return path
    dest = flags.get("-d")
    if dest:
        try:
            os.makedirs(dest, exist_ok=True)
            final = os.path.join(dest, os.path.basename(path))
            if os.path.abspath(final) != os.path.abspath(path):
                os.rename(path, final)
                path = final
        except OSError as exc:
            LOGS.debug("[downloadupload] move-to-dir failed: %s", exc)
    if flags.get("-t") or flags.get("-ttl"):
        _mark_temp(path, _temp_ttl(flags))
    return path


# ──────────────────────────────────────────────────────────────────────────
# Telegram media download
# ──────────────────────────────────────────────────────────────────────────


async def _download_tg_media(
    client, message, custom_name=None, prog_event=None, index=None
):
    """Download one Telegram message's media into ``DOWNLOAD_DIR``.

    Returns the saved file path, or ``None`` when the message has no media.
    When *index* is given together with *custom_name* the file is saved as
    ``[index]{name}`` so parallel bulk downloads never collide.
    """
    if not (message and message.media):
        return None
    start = time.time()
    if hasattr(message.media, "document") and message.media.document:
        file = message.media.document
        mime = getattr(file, "mime_type", "") or ""
        original = message.file.name if getattr(message, "file", None) else None
        filename = _resolve_name(custom_name, original, mime)
        if not filename:
            if "audio" in mime:
                filename = "audio_" + _dt.now().isoformat("_", "seconds") + ".ogg"
            elif "video" in mime:
                filename = "video_" + _dt.now().isoformat("_", "seconds") + ".mp4"
            else:
                filename = f"file_{int(time.time())}"
        if index is not None and custom_name:
            base, ext = os.path.splitext(filename)
            filename = f"[{index}]{base}{ext}" if ext else f"[{index}]{filename}"
        path = os.path.join(DOWNLOAD_DIR, filename)
        try:
            result = await fns.downloader(
                path, file, prog_event, start, f"Downloading {filename}..."
            )
        except MessageNotModifiedError:
            raise
        return getattr(result, "name", path)

    # Photo / other non-document media.
    progress_cb = None
    if prog_event is not None:

        def _cb(done, total, _e=prog_event, _s=start):
            return asyncio.get_running_loop().create_task(
                fns.progress(done, total, _e, _s, get_string("com_5"))
            )

        progress_cb = _cb
    saved = await client.download_media(
        message, DOWNLOAD_DIR, progress_callback=progress_cb
    )
    if saved and custom_name:
        base = custom_name.strip()
        if index is not None:
            base = f"[{index}]{base}"
        final = os.path.join(DOWNLOAD_DIR, f"{base}.jpg")
        try:
            if os.path.abspath(saved) != os.path.abspath(final):
                os.rename(saved, final)
            saved = final
        except OSError as exc:
            LOGS.debug("[downloadupload] photo rename failed: %s", exc)
    return saved


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
        LOGS.debug("[downloadupload] album resolve failed: %s", exc)
        return [replied]


# ──────────────────────────────────────────────────────────────────────────
# Queue tracker + owner PM notification
# ──────────────────────────────────────────────────────────────────────────

_TRACKER_LAST = {}  # gid -> last refresh timestamp (flood guard)


def _op_words(operation):
    op = str(operation or "").lower()
    if op in ("ul", "upload", "up", "u"):
        return "upload", "batch"
    return "download", "bulk"


def _pm_notification(user, operation, count, gid):
    """Build the owner PM text using :func:`inline_mention` for the user."""
    verb, mode = _op_words(operation)
    if isinstance(user, int):
        mention = f"[user](tg://user?id={user})"
    else:
        mention = fns.inline_mention(user)
    return f"{mention} triggered {count} file(s) {verb} {mode} process — GID: {gid}"


async def _pm_owner(text):
    """Best-effort PM delivery to the owner."""
    try:
        owner = udB.get_key("OWNER_ID") or OWNER_ID
        await ultroid_bot.send_message(int(owner), text, link_preview=False)
    except Exception as exc:
        LOGS.debug("[downloadupload] owner PM failed: %s", exc)


async def _safe_sender(event):
    try:
        return await event.get_sender()
    except Exception:
        return event.sender_id


def _format_tracker(info, operation):
    """Render the live queue status as a monospace table."""
    verb, mode = _op_words(operation)
    gid = info.get("gid", "?")
    total = info.get("total_files", 0)
    done = info.get("done", 0)
    failed = info.get("failed", 0)
    cancelled = info.get("cancelled", 0)
    active = info.get("active", 0)
    progress = info.get("progress", 0.0)
    lines = [
        f"{verb.title()} {mode} — GID: {gid}",
        f"Progress: {done}/{total} ({progress}%)",
        f"Done: {done} | Active: {active} | Failed: {failed} | Cancelled: {cancelled}",
        "",
        f"{'FID':<14} {'Status':<12} {'Size'}",
    ]
    for f in info.get("files", []):
        fid = str(f.get("fid", "?"))
        st = str(f.get("status", "?"))
        size = _humanbytes(f.get("size_bytes", 0))
        lines.append(f"{fid:<14} {st:<12} {size}")
    return "```\n" + "\n".join(lines) + "\n```"


async def _refresh_tracker(msg, gid, operation, force=False):
    """Edit *msg* with the current queue table (flood-guarded)."""
    now = time.time()
    if not force and (now - _TRACKER_LAST.get(gid, 0.0)) < 1.5:
        return
    _TRACKER_LAST[gid] = now
    try:
        info = core.queue.status(gid)
        if not info.get("found"):
            return
        await msg.edit(_format_tracker(info, operation))
    except MessageNotModifiedError:
        pass
    except Exception as exc:
        LOGS.debug("[downloadupload] tracker edit skipped: %s", exc)


async def _enqueue_and_notify(event, msg, operation, tasks):
    """Enqueue *tasks*, PM the owner and show the initial tracker table."""
    gid = core.queue.enqueue(tasks)
    user = await _safe_sender(event)
    count = sum(len(t.files) for t in tasks) or len(tasks)
    await _pm_owner(_pm_notification(user, operation, count, gid))
    await _refresh_tracker(msg, gid, operation, force=True)
    return gid


def _bulk_summary(gid, operation, total):
    info = core.queue.status(gid)
    verb, mode = _op_words(operation)
    done = info.get("done", 0)
    failed = info.get("failed", 0)
    cancelled = info.get("cancelled", 0)
    return (
        f"{verb.title()} {mode} finished — GID `{gid}`\n"
        f"Done: {done}/{total} | Failed: {failed} | Cancelled: {cancelled}"
    )


# ──────────────────────────────────────────────────────────────────────────
# .download — direct URL / multi-URL bulk
# ──────────────────────────────────────────────────────────────────────────


async def _download_one_url(event, msg, url, flags, custom_name):
    """Single direct-URL download with a live progress bar (no queue)."""
    smart = await fns.get_smart_name(url)
    filename = _resolve_name(custom_name, smart) if custom_name else smart
    start = time.time()
    try:
        filename, elapsed = await fns.fast_download(
            url,
            filename,
            progress_callback=lambda done, total: asyncio.get_running_loop().create_task(
                fns.progress(done, total, msg, start, f"Downloading: `{filename}`")
            ),
        )
    except Exception as exc:
        return await msg.eor(f"`Error: {exc}`")
    _post_download(filename, flags)
    await msg.eor(f"`{filename}` downloaded in {fns.time_formatter(elapsed * 1000)}.")


async def _bulk_url_download(event, msg, urls, flags, custom_name):
    """Multi-URL bulk download, tracked through the queue (sequential)."""
    operation = "dl"
    tasks = [
        QueueTask(
            operation=operation,
            files=[u],
            chat_id=event.chat_id,
            user_id=event.sender_id,
        )
        for u in urls
    ]
    gid = await _enqueue_and_notify(event, msg, operation, tasks)
    smart_names = await asyncio.gather(*(fns.get_smart_name(u) for u in urls))
    for index, url in enumerate(urls):
        fid = f"{gid}-{index}"
        if core.queue.status(fid).get("status") == "cancelled":
            await _refresh_tracker(msg, gid, operation)
            continue
        core.queue.update_status(fid, "downloading")
        await _refresh_tracker(msg, gid, operation)
        try:
            name = (
                _resolve_name(custom_name, smart_names[index])
                if custom_name
                else smart_names[index]
            )
            start = time.time()
            saved, _ = await fns.fast_download(
                url,
                name,
                progress_callback=lambda done, total, _m=msg, _s=start, _n=name: (
                    asyncio.get_running_loop().create_task(
                        fns.progress(done, total, _m, _s, f"Downloading: `{_n}`")
                    )
                ),
            )
            _post_download(saved, flags)
            core.queue.update_status(fid, "done")
        except Exception as exc:
            LOGS.exception(exc)
            core.queue.update_status(fid, "failed")
        await _refresh_tracker(msg, gid, operation)
    await _refresh_tracker(msg, gid, operation, force=True)
    await msg.eor(_bulk_summary(gid, operation, len(urls)))


@ultroid_cmd(pattern="download( (.*)|$)")
async def down(event):
    """Download a direct URL, or reply to a ``.txt`` of URLs for a bulk run."""
    raw = (event.pattern_match.group(1) or "").strip()
    try:
        flags, positionals, unknowns = _parse_flags(
            raw, _DW_VALUE_FLAGS, _DW_BOOL_FLAGS
        )
    except ValueError as exc:
        return await event.eor(f"`{exc}`")
    if unknowns:
        return await event.eor(
            _flag_error_text(
                "download", unknowns, sorted(_DW_VALUE_FLAGS | _DW_BOOL_FLAGS)
            )
        )
    if flags.get("-ttl") and _temp_ttl(flags) is None:
        return await event.eor("`Invalid -ttl value, expected a number of seconds.`")

    custom_name = flags.get("-n")
    url = next((p for p in positionals if p.startswith(("http://", "https://"))), None)
    urls = [url] if url else []

    # Fall back to a replied .txt file containing one URL per line.
    if not urls and event.reply_to_msg_id:
        replied = await event.get_reply_message()
        if (
            replied
            and replied.file
            and (replied.file.name or "").lower().endswith(".txt")
        ):
            txt_path = await event.client.download_media(replied, DOWNLOAD_DIR)
            try:
                with open(txt_path, encoding="utf-8", errors="ignore") as handle:
                    urls = [
                        line.strip()
                        for line in handle
                        if line.strip() and not line.strip().startswith("#")
                    ]
            except Exception as exc:
                LOGS.exception(exc)
            finally:
                try:
                    os.remove(txt_path)
                except OSError:
                    pass

    msg = await event.eor(get_string("com_1"))
    if not urls:
        return await msg.eor(
            "`Give a direct link to download, or reply to a .txt file with URLs.`"
        )
    if len(urls) == 1:
        await _download_one_url(event, msg, urls[0], flags, custom_name)
    else:
        await _bulk_url_download(event, msg, urls, flags, custom_name)


# ──────────────────────────────────────────────────────────────────────────
# .dl — Telegram media download (reply / link / bulk / album)
# ──────────────────────────────────────────────────────────────────────────


async def _bulk_tg_download(
    event, msg, messages, flags, custom_name, delete_originals=False, parallel=False
):
    """Queue-tracked bulk Telegram download (parallel for ranges, sequential for albums)."""
    operation = "dl"
    tasks = [
        QueueTask(
            operation=operation,
            files=[str(getattr(m, "id", index))],
            chat_id=event.chat_id,
            user_id=event.sender_id,
            size_bytes=_msg_size(m),
        )
        for index, m in enumerate(messages)
    ]
    gid = await _enqueue_and_notify(event, msg, operation, tasks)

    async def _one(index, message):
        fid = f"{gid}-{index}"
        if core.queue.status(fid).get("status") == "cancelled":
            return
        core.queue.update_status(fid, "downloading")
        prog = None if parallel else msg
        try:
            path = await _download_tg_media(
                event.client, message, custom_name, prog, index=index
            )
            _post_download(path, flags)
            core.queue.update_status(fid, "done")
        except MessageNotModifiedError:
            core.queue.update_status(fid, "failed")
        except Exception as exc:
            LOGS.exception(exc)
            core.queue.update_status(fid, "failed")
        finally:
            await _refresh_tracker(msg, gid, operation)

    if parallel:
        await asyncio.gather(*(_one(i, m) for i, m in enumerate(messages)))
    else:
        for i, m in enumerate(messages):
            await _one(i, m)

    await _refresh_tracker(msg, gid, operation, force=True)
    if delete_originals:
        for message in messages:
            try:
                await message.delete()
            except Exception as exc:
                LOGS.debug("[downloadupload] delete original failed: %s", exc)
    await msg.eor(_bulk_summary(gid, operation, len(messages)))


@ultroid_cmd(pattern="dl( (.*)|$)")
async def download(event):
    """Download Telegram media via reply or t.me link, with flags and bulk support."""
    raw = (event.pattern_match.group(1) or "").strip()
    try:
        flags, positionals, unknowns = _parse_flags(
            raw, _DL_VALUE_FLAGS, _DL_BOOL_FLAGS
        )
    except ValueError as exc:
        return await event.eor(f"`{exc}`")
    if unknowns:
        return await event.eor(
            _flag_error_text("dl", unknowns, sorted(_DL_VALUE_FLAGS | _DL_BOOL_FLAGS))
        )
    if flags.get("-ttl") and _temp_ttl(flags) is None:
        return await event.eor("`Invalid -ttl value, expected a number of seconds.`")

    link = next((p for p in positionals if "t.me/" in p), None)
    legacy_name = next((p for p in positionals if "t.me/" not in p), None)
    custom_name = flags.get("-n") or legacy_name
    delete_originals = bool(flags.get("-del"))

    msg = await event.eor(get_string("com_1"))

    # --- t.me link path (single message or bulk range) ---
    if link:
        chat, ids = _parse_tg_link(link)
        if not (chat and ids):
            return await msg.eor(get_string("gms_1"))
        if len(ids) > MAX_BULK:
            return await msg.eor(
                f"`Range too large: {len(ids)} messages (max {MAX_BULK}).`"
            )
        messages = await event.client.get_messages(chat, ids=ids)
        messages = [m for m in (messages or []) if m and m.media]
        if not messages:
            return await msg.eor(get_string("udl_1"), time=5)
        if len(messages) == 1:
            try:
                path = await _download_tg_media(
                    event.client, messages[0], custom_name, msg
                )
                _post_download(path, flags)
            except MessageNotModifiedError as err:
                return await msg.edit(str(err), parse_mode="v2")
            except Exception as exc:
                LOGS.exception(exc)
                return await msg.eor(f"`Error: {exc}`")
            return await msg.eor(
                get_string("udl_2").format(path, "0s"), parse_mode="v2"
            )
        return await _bulk_tg_download(
            event, msg, messages, flags, custom_name, parallel=True
        )

    # --- reply path (single media or full album) ---
    if not event.reply_to_msg_id:
        return await msg.eor(get_string("cvt_3"), time=8)
    replied = await event.get_reply_message()
    if not (replied and replied.media):
        return await msg.eor(get_string("udl_1"), time=5)
    album = await _resolve_album(event, replied)
    if len(album) == 1:
        try:
            path = await _download_tg_media(event.client, album[0], custom_name, msg)
            _post_download(path, flags)
        except MessageNotModifiedError as err:
            return await msg.edit(str(err), parse_mode="v2")
        except Exception as exc:
            LOGS.exception(exc)
            return await msg.eor(f"`Error: {exc}`")
        if delete_originals:
            try:
                await replied.delete()
            except Exception as exc:
                LOGS.debug("[downloadupload] delete original failed: %s", exc)
        return await msg.eor(get_string("udl_2").format(path, "0s"), parse_mode="v2")
    return await _bulk_tg_download(
        event,
        msg,
        album,
        flags,
        custom_name,
        delete_originals=delete_originals,
        parallel=False,
    )


# ──────────────────────────────────────────────────────────────────────────
# .ul — local file / directory upload
# ──────────────────────────────────────────────────────────────────────────


def _upload_name(custom_name, path, index=None):
    """Resolve the displayed upload filename for ``-n`` (with ``[index]`` for dirs)."""
    if not custom_name:
        return os.path.basename(path)
    base = custom_name.strip()
    _, ext = os.path.splitext(os.path.basename(path))
    if index is not None:
        return f"[{index}]{base}{ext}"
    _, cext = os.path.splitext(base)
    return base if cext else (f"{base}{ext}" if ext else base)


def _format_dir_table(rows):
    """Render the ascending directory listing as a monospace table."""
    lines = ["```", f"{'File':<28} {'Size':>10}", "-" * 40]
    for name, size in rows:
        lines.append(f"{name[:28]:<28} {_humanbytes(size):>10}")
    return "\n".join(lines) + "\n```"


async def _upload_single_file(
    event,
    msg,
    path,
    custom_name,
    stream,
    force_doc,
    thumb,
    delete,
    reply_to=None,
    index=None,
):
    """Upload one file as its own message with a caption (name + elapsed)."""
    attributes = None
    if stream:
        try:
            attributes = await fns.set_attributes(path)
        except KeyError as exc:
            LOGS.exception(exc)
    filename = _upload_name(custom_name, path, index)
    try:
        file, elapsed = await event.client.fast_uploader(
            path, show_progress=True, event=msg, to_delete=delete, filename=filename
        )
    except (ValueError, IsADirectoryError):
        raise
    await event.client.send_file(
        event.chat_id,
        file,
        supports_streaming=stream,
        force_document=force_doc,
        thumb=thumb,
        attributes=attributes,
        caption=f"`Uploaded` `{filename or path}` `in {fns.time_formatter(elapsed * 1000)}`",
        reply_to=reply_to,
    )


async def _upload_directory(
    event, msg, directory, flags, custom_name, stream, force_doc, thumb, delete
):
    """List a directory as a size table, then queue-track an upload of every file."""
    rows = list_directory_files(directory)
    if not rows:
        return await msg.eor(f"`No files found in` `{directory}`")
    await msg.eor(f"`Uploading directory:` `{directory}`\n{_format_dir_table(rows)}")

    files = fns.get_all_files(directory)
    if not files:
        return
    operation = "ul"
    tasks = [
        QueueTask(
            operation=operation,
            files=[os.path.basename(f)],
            chat_id=event.chat_id,
            user_id=event.sender_id,
            size_bytes=_filesize(f),
        )
        for f in files
    ]
    gid = await _enqueue_and_notify(event, msg, operation, tasks)
    reply_to = event.reply_to_msg_id or event
    for index, fpath in enumerate(files):
        fid = f"{gid}-{index}"
        if core.queue.status(fid).get("status") == "cancelled":
            await _refresh_tracker(msg, gid, operation)
            continue
        core.queue.update_status(fid, "uploading")
        await _refresh_tracker(msg, gid, operation)
        try:
            await _upload_single_file(
                event,
                msg,
                fpath,
                custom_name,
                stream,
                force_doc,
                thumb,
                delete,
                reply_to=reply_to,
                index=index,
            )
            core.queue.update_status(fid, "done")
        except (ValueError, IsADirectoryError):
            core.queue.update_status(fid, "failed")
        except Exception as exc:
            LOGS.exception(exc)
            core.queue.update_status(fid, "failed")
        await _refresh_tracker(msg, gid, operation)
    await _refresh_tracker(msg, gid, operation, force=True)
    await msg.eor(_bulk_summary(gid, operation, len(files)))


@ultroid_cmd(pattern="ul( (.*)|$)")
async def upload(event):
    """Upload a local file or directory, with glob, rename, delete and stream flags."""
    raw = (event.pattern_match.group(1) or "").strip()
    try:
        flags, positionals, unknowns = _parse_flags(
            raw, _UL_VALUE_FLAGS, _UL_BOOL_FLAGS
        )
    except ValueError as exc:
        return await event.eor(f"`{exc}`")
    if unknowns:
        return await event.eor(
            _flag_error_text("ul", unknowns, sorted(_UL_VALUE_FLAGS | _UL_BOOL_FLAGS))
        )

    custom_name = flags.get("-n")
    stream = bool(flags.get("--stream"))
    no_thumb = bool(flags.get("--no-thumb"))
    delete = bool(flags.get("-del") or flags.get("--delete"))
    force_doc = not stream
    thumb = None if no_thumb else ULTConfig.thumb
    # Reconstruct the path (a single quoted token, or unquoted spaced fragments).
    path = " ".join(positionals).strip()

    msg = await event.eor(get_string("com_1"))
    if not path:
        return await msg.eor("`Give a file/directory path to upload.`")
    if not event.out and path == ".env":
        return await event.reply("`You can't do this...`")

    if path.endswith("/"):
        path += "*"
    results = glob.glob(path)
    if not results and os.path.exists(path):
        results = [path]
    if not results:
        try:
            await event.reply(file=path)
            return await event.try_delete()
        except Exception as exc:
            LOGS.exception(exc)
        return await msg.eor(get_string("ls1"))

    reply_to = event.reply_to_msg_id or event
    for result in results:
        if os.path.isdir(result):
            await _upload_directory(
                event, msg, result, flags, custom_name, stream, force_doc, thumb, delete
            )
            continue
        try:
            await _upload_single_file(
                event,
                msg,
                result,
                custom_name,
                stream,
                force_doc,
                thumb,
                delete,
                reply_to=reply_to,
            )
        except (ValueError, IsADirectoryError):
            continue
        except Exception as exc:
            LOGS.exception(exc)
    await msg.try_delete()


# ──────────────────────────────────────────────────────────────────────────
# .fcancel — cancel a queued batch (GID) or single file (FID)
# ──────────────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern="fcancel( (.*)|$)")
async def fcancel(event):
    """Cancel a queued download/upload batch (GID) or a single file (FID)."""
    target = (event.pattern_match.group(1) or "").strip().split()
    if not target:
        return await event.eor("`Usage: .fcancel <GID>  or  .fcancel <FID>`")
    if event.sender_id not in owner_and_sudos():
        return await event.eor("`Only the owner/sudo can cancel queued tasks.`")

    target = target[0]
    ok = core.queue.cancel(target)
    info = core.queue.status(target)
    if not ok and not info.get("found"):
        return await event.eor(f"`No active task found for` `{target}`")

    if "-" in target and info.get("found"):
        # FID-level cancellation.
        return await event.eor(
            f"`Cancelled file` `{target}` `— status:` `{info.get('status')}`"
        )
    if info.get("found"):
        return await event.eor(
            f"`Cancelled batch` `{info.get('gid', target)}`\n"
            f"Done: {info.get('done', 0)} | Cancelled: {info.get('cancelled', 0)} | "
            f"Failed: {info.get('failed', 0)} | Active: {info.get('active', 0)}"
        )
    await event.eor(f"`Cancelled` `{target}`")

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

import asyncio
import atexit
import mimetypes
import os
import random
import re
import sys
import time
import time as _time
import traceback
from concurrent.futures import ThreadPoolExecutor
from functools import partial, wraps
from traceback import format_exc
from typing import Any, List, Optional
from urllib.parse import quote, unquote, urlparse
from aiohttp import ClientSession as aiohttp_client
import multiprocessing
import heroku3

from telethon.helpers import _maybe_await
from telethon.tl import types
from telethon.tl.custom import Message
from telethon.utils import get_display_name
from telethon.errors.rpcerrorlist import AuthBytesInvalidError, MediaCaptionTooLongError

from .. import LOGS, udB

if udB is not None:
    # The runtime has started (the fns chain imports during ``bind_union_state``,
    # after ``open_database`` bound udB): the plugin bookkeeping lists are
    # importable. A bare library import skips them exactly as before.
    from ..configs import Var
    from ..dB._core import ADDONS, HELP, LIST, LOADED

from .._misc import CMD_HELP
from .._misc._wrappers import eod, eor
from .._misc.keep_safe import KEEP_SAFE
from ..exceptions import DependencyMissingError
from . import *
from ..version import ultroid_version
from .FastTelethon import download_file as downloadable
from .FastTelethon import upload_file as uploadable
from custom import fmtbar, fmtbyte, fmtmsg, fmtsec

HUGGING_TOKEN = os.getenv("HF_TOKEN")

class Helper:
    """The helper toolkit: networking, transfers, media info, and process utils.

    The process-wide instance (``fns``) owns the shared aiohttp session.
    """

    #: The addon install log. A class attribute, not instance state, so the
    #: module-level ``installed`` name below is the very same dict.
    installed: dict = {}

    def __init__(self):
        self._session = None

    def _get_http_session(self):
        """Return a cached, process-wide aiohttp ClientSession.

        Creating a session per request is expensive and leaks connectors.  This
        helper lazily creates one session and reuses it until it is closed.
        """
        if aiohttp_client is None:
            raise DependencyMissingError("install 'aiohttp' to use this.")
        if self._session is None or self._session.closed:
            self._session = aiohttp_client()
        return self._session

    async def _close_http_session(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None

    def _close_http_session_sync(self):
        """Best-effort synchronous cleanup at process exit."""
        try:
            from ..startup._loops import get_event_loop

            loop = get_event_loop()
            if loop.is_closed():
                return
            if loop.is_running():
                loop.create_task(self._close_http_session())
            else:
                try:
                    loop.run_until_complete(self._close_http_session())
                except Exception:
                    pass
        except Exception:
            pass

    async def get_smart_name(self, url, custom_name=None):
        if custom_name:
            return custom_name

        try:
            session = self._get_http_session()
            async with session.head(url, allow_redirects=True, timeout=5) as resp:
                cd = resp.headers.get("Content-Disposition")
                if cd and "filename=" in cd:
                    filenames = re.findall("filename=(.+)", cd)
                    if len(filenames) > 0:
                        return filenames[0].strip(' ";')

                ct = resp.headers.get("Content-Type")
                if ct:
                    ext = mimetypes.guess_extension(ct.split(";")[0])
                    if ext:
                        path = urlparse(url).path
                        base = os.path.basename(path) or "file"
                        name_without_ext = os.path.splitext(base)[0]
                        return f"{name_without_ext}{ext}"

        except Exception:
            pass

        path = urlparse(url).path
        fname = os.path.basename(path)
        if not fname or "." not in fname:
            return f"file_{int(time.time())}.bin"

        return unquote(fname)

    @staticmethod
    def run_async(function):
        @wraps(function)
        async def wrapper(*args, **kwargs):
            return await asyncio.get_running_loop().run_in_executor(
                ThreadPoolExecutor(max_workers=multiprocessing.cpu_count() * 5),
                partial(function, *args, **kwargs),
            )

        return wrapper

    def ErrInfo(self, exception, full=False):
        exception_message = str(exception)
        exception_type, exception_object, exception_traceback = sys.exc_info()
        filename = os.path.split(exception_traceback.tb_frame.f_code.co_filename)[1]
        try:
            from pyUltroid.startup import LOGS
        except ImportError:
            import logging

            LOGS = logging.getLogger(__name__)

        if full:
            detailed_info = traceback.format_exception(
                exception_type, exception_object, exception_traceback
            )
            detailed_info_str = "".join(detailed_info)
            LOGS.info(
                f"{exception_message}\n{detailed_info_str}\nFile: {filename}, Line {exception_traceback.tb_lineno}"
            )
        else:
            LOGS.info(
                f"{exception_message} {exception_type} {filename}, Line {exception_traceback.tb_lineno}"
            )

    # ~~~~~~~~~~~~~~~~~~~~ small funcs ~~~~~~~~~~~~~~~~~~~~ #

    def make_mention(self, user, custom=None):
        if user.username:
            return f"@{user.username}"
        return self.inline_mention(user, custom=custom)

    def inline_mention(self, user, custom=None, html=False):
        mention_text = (
            get_display_name(user) or "Deleted Account" if not custom else custom
        )
        if isinstance(user, types.User):
            if html:
                return f"<a href=tg://user?id={user.id}>{mention_text}</a>"
            return f"[{mention_text}](tg://user?id={user.id})"
        if isinstance(user, types.Channel) and user.username:
            if html:
                return f"[{mention_text}](https://t.me/{user.username})"
            return f"[{mention_text}](https://t.me/{user.username})"
        return mention_text

    async def check_reply_to(self, event):
        truai = [event.client.me.id]

        if (event.is_private and event.is_reply) or (
            event.is_reply and event.reply_to_msg_id
        ):
            try:
                replied_message = await event.client.get_messages(
                    event.chat_id, ids=event.reply_to_msg_id
                )
                if replied_message.from_id:
                    user_id = replied_message.from_id.user_id
                    if user_id in truai:
                        return True
                elif replied_message.peer_id and not replied_message.from_id:
                    channel_id = replied_message.peer_id.channel_id
                    if channel_id in truai:
                        return True
                # If neither user_id nor channel_id is in truai, return False
                return False
            except Exception as e:
                self.ErrInfo(e)
                return False
        return False

    # ----------------- Load \\ Unloader ---------------- #
    def un_plug(self, shortname):
        from pyUltroid._core.loader import unload_plugin

        unload_plugin(shortname)

    # --------------------------------------------------------------------- #
    @staticmethod
    async def bash(cmd, run_code=0):
        """
        run any command in subprocess and get output or error."""
        try:
            from pyUltroid.tracer import get_current_trace

            trace = get_current_trace()
            if trace:
                trace.shell_calls.append(str(cmd))
        except Exception:
            pass
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        err = stderr.decode().strip() or None
        out = stdout.decode().strip()
        if not run_code and err:
            if match := re.match(r"\/bin\/sh: (.*): ?(\w+): not found", err):
                return out, f"{match.group(2).upper()}_NOT_FOUND"
        return out, err

    # ---------------------------UPDATER-------------------------------- #

    async def updamter(self):
        from .. import LOGS

        try:
            from ..updater import updater as _engine

            diff = await _engine.check()
            return bool(diff and diff.has_changes)
        except Exception as er:
            LOGS.warning(f"Update check skipped: {er}")
            return False

    # ----------------Fast Upload/Download----------------
    # @1danish_00 @new-dev0 @buddhhu
    async def uploader(self, file, name, taime, event, msg):
        with open(file, "rb") as f:
            result = await uploadable(
                client=event.client,
                file=f,
                filename=name,
                progress_callback=lambda d, t: asyncio.create_task(
                    self.progress(
                        d,
                        t,
                        event,
                        taime,
                        msg,
                    ),
                ),
            )
        return result

    async def downloader(self, filename, file, event, taime, msg):
        if not os.path.exists(filename):
            filename = f"temp_{int(time.time())}"
        try:
            with open(filename, "wb") as fk:
                return await downloadable(
                    client=event.client,
                    location=file,
                    out=fk,
                    progress_callback=lambda d, t: asyncio.create_task(
                        self.progress(
                            d,
                            t,
                            event,
                            taime,
                            msg,
                        ),
                    ),
                )
        except AuthBytesInvalidError:
            # FastTelethon opens an exported sender on the media's DC; bot accounts
            # and sessions with stale/expired auth cannot export/import DC auth, so
            # parallel transfer raises AuthBytesInvalidError.  Fall back to the
            # standard single-connection client.download_media path instead of
            # crashing (and avoid the half-open-sender "Task was destroyed" cascade).
            LOGS.warning(
                "FastTelethon parallel download failed (auth export); "
                "falling back to standard download_media."
            )
            await event.client.download_media(file, file=filename)

            class _DownloadedFile:
                def __init__(self, name):
                    self.name = name

            return _DownloadedFile(filename)

    # ~~~~~~~~~~~~~~~Async Searcher~~~~~~~~~~~~~~~
    # @buddhhu
    async def async_searcher(
        self,
        url: str,
        post: bool = False,
        head: bool = False,
        headers: dict = None,
        evaluate=None,
        object: bool = False,
        re_json: bool = False,
        re_content: bool = False,
        *args,
        session=None,
        **kwargs,
    ):
        """Make an async HTTP request using a shared aiohttp session.

        A caller may pass an existing ``aiohttp.ClientSession`` via *session* to
        reuse a connection pool (e.g. ``event.client.http``).  Otherwise a
        process-wide shared session is used.
        """
        client = session or self._get_http_session()
        method = client.head if head else (client.post if post else client.get)
        data = await method(url, headers=headers, *args, **kwargs)
        if evaluate:
            return await evaluate(data)
        if re_json:
            return await data.json()
        if re_content:
            return await data.read()
        if head or object:
            return data
        return await data.text()

    # ~~~~~~~~~~~~~~~~~~~~DDL Downloader~~~~~~~~~~~~~~~~~~~~
    # @buddhhu @new-dev0
    async def download_file(self, link, name, validate=False):
        """for files, without progress callback with aiohttp"""

        async def _download(content):
            if validate and "application/json" in content.headers.get("Content-Type"):
                return None, await content.json()
            with open(name, "wb") as file:
                file.write(await content.read())
            return name, ""

        return await self.async_searcher(link, evaluate=_download)

    async def fast_download(self, download_url, filename=None, progress_callback=None):
        session = self._get_http_session()
        async with session.get(download_url, timeout=None) as response:
            if not filename:
                filename = unquote(download_url.rpartition("/")[-1])
            total_size = int(response.headers.get("content-length", 0)) or None
            downloaded_size = 0
            start_time = time.time()
            with open(filename, "wb") as f:
                async for chunk in response.content.iter_chunked(1024):
                    if chunk:
                        f.write(chunk)
                        downloaded_size += len(chunk)
                    if progress_callback and total_size:
                        await _maybe_await(
                            progress_callback(downloaded_size, total_size)
                        )
            return filename, time.time() - start_time

    # --------------------------Media Funcs-------------------------------- #
    def mediainfo(self, media):
        xx = str((str(media)).split("(", maxsplit=1)[0])
        m = ""
        if xx == "MessageMediaDocument":
            mim = media.document.mime_type
            if mim == "application/x-tgsticker":
                m = "sticker animated"
            elif "image" in mim:
                if mim == "image/webp":
                    m = "sticker"
                elif mim == "image/gif":
                    m = "gif as doc"
                else:
                    m = "pic as doc"
            elif "video" in mim:
                if "DocumentAttributeAnimated" in str(media):
                    m = "gif"
                elif "DocumentAttributeVideo" in str(media):
                    i = str(media.document.attributes[0])
                    if "supports_streaming=True" in i:
                        m = "video"
                    m = "video as doc"
                else:
                    m = "video"
            elif "audio" in mim:
                m = "audio"
            else:
                m = "document"
        elif xx == "MessageMediaPhoto":
            m = "pic"
        elif xx == "MessageMediaWebPage":
            m = "web"
        return m

    # ------------------Some Small Funcs----------------
    def time_formatter(self, milliseconds):
        minutes, seconds = divmod(int(milliseconds / 1000), 60)
        hours, minutes = divmod(minutes, 60)
        days, hours = divmod(hours, 24)
        weeks, days = divmod(days, 7)
        tmp = (
            ((str(weeks) + "w:") if weeks else "")
            + ((str(days) + "d:") if days else "")
            + ((str(hours) + "h:") if hours else "")
            + ((str(minutes) + "m:") if minutes else "")
            + ((str(seconds) + "s") if seconds else "")
        )
        if not tmp:
            return "0s"

        if tmp.endswith(":"):
            return tmp[:-1]
        return tmp

    def humanbytes(self, size):
        if not size:
            return "0 B"
        for unit in ["", "K", "M", "G", "T"]:
            if size < 1024:
                break
            size /= 1024
        if isinstance(size, int):
            size = f"{size}{unit}B"
        elif isinstance(size, float):
            size = f"{size:.2f}{unit}B"
        return size

    def numerize(self, number):
        if not number:
            return None
        unit = ""
        for unit in ["", "K", "M", "B", "T"]:
            if number < 1000:
                break
            number /= 1000
        if isinstance(number, int):
            number = f"{number}{unit}"
        elif isinstance(number, float):
            number = f"{number:.2f}{unit}"
        return number

    async def progress(
        self,
        current: int,
        total: int,
        event,
        start: float,
        type_of_ps: str,
        file_name: str = None,
        update_interval: float = 3.5,
        **kwargs,
    ):
        """
        A robust, backward-compatible progress bar function.
        Args:
            current (int): The current progress value.
            total (int): The total value.
            event: The message event object from the library (e.g., Telethon/Pyrogram).
            start (float): The timestamp when the process started.
            type_of_ps (str): A description of the process (e.g., "Uploading").
            file_name (str, optional): The name of the file being processed. Defaults to None.
            update_interval (float, optional): Time in seconds between updates. Defaults to 2.5.
        """
        try:
            # --- Graceful Handling of Zero Total ---
            if total == 0:
                # Avoids ZeroDivisionError and pointless updates.
                return
            now = _time.time()
            # --- State Management: Initialize start/last time if not present ---
            # This makes it self-managing like V2, but respects the passed 'start' time.
            if not hasattr(event, "_start_time"):
                event._start_time = start
            if not hasattr(event, "_last_update_time"):
                event._last_update_time = event._start_time
            # --- Flood Control: Only update if enough time has passed ---
            if (now - event._last_update_time) < update_interval and current != total:
                return
            # --- Calculations ---
            event._last_update_time = now
            elapsed = now - event._start_time
            speed = current / elapsed if elapsed > 0 else 0
            eta_seconds = (total - current) / speed if speed > 0 else 0
            # --- Message Construction ---
            # We build a dictionary of data to keep it clean and readable.
            progress_data = {
                "Current": f"{fmtbyte(current)}",
                "Total": f"{fmtbyte(total)}\n",  # The newline adds nice spacing
                "Speed": f"{fmtbyte(speed)}/s\n",
                "Elapsed": fmtsec(elapsed, human=True),
                "Estimated": fmtsec(eta_seconds, human=True),
            }
            # Conditionally add the file name if it exists.
            if file_name:
                progress_data["File Name"] = file_name

            # Format the final message using our helper.
            text_to_send = fmtmsg(
                head=f"{type_of_ps.title()}",
                data=progress_data,
                foot=fmtbar(current, total),
            )
            # --- Robust Message Editing ---
            # This is the most likely point of failure. We wrap it in its own
            # try/except block to ensure that a Telegram API error (e.g., message
            # deleted by user) does not crash the entire upload/download loop.
            try:
                await event.edit(text_to_send, parse_mode="v2")
            except Exception:
                # You could add logging here if you wanted.
                # For now, we'll just ignore the error and continue.
                pass
        except Exception as e:
            # A top-level catch to prevent any unexpected error from crashing the app.
            LOGS.exception("[PROGRESS BAR ERROR] An unexpected error occurred: %s", e)

    # ------------------System\\Heroku stuff----------------
    # @xditya @sppidy @techierror
    async def restart(self, ult=None):
        from ..configs import Var

        if Var.HEROKU_APP_NAME and Var.HEROKU_API:
            try:
                Heroku = heroku3.from_key(Var.HEROKU_API)
                app = Heroku.apps()[Var.HEROKU_APP_NAME]
                if ult:
                    await ult.edit("`Restarting your app, please wait for a minute!`")
                app.restart()
            except Exception as er:
                if ult:
                    return await eor(
                        ult,
                        "`HEROKU_API` or `HEROKU_APP_NAME` is wrong! Kindly re-check in config vars.",
                    )
                LOGS.exception(er)
        else:
            if len(sys.argv) == 1:
                os.execl(sys.executable, sys.executable, "-m", "pyUltroid")
            else:
                os.execl(
                    sys.executable,
                    sys.executable,
                    "-m",
                    "pyUltroid",
                    sys.argv[1],
                    sys.argv[2],
                    sys.argv[3],
                    sys.argv[4],
                    sys.argv[5],
                    sys.argv[6],
                )

    async def shutdown(self, ult):
        from .. import HOSTED_ON, LOGS
        from ..configs import Var

        ult = await eor(ult, "Shutting Down")
        if HOSTED_ON == "heroku":
            if not (Var.HEROKU_APP_NAME and Var.HEROKU_API):
                return await ult.edit("Please Fill `HEROKU_APP_NAME` and `HEROKU_API`")
            dynotype = os.getenv("DYNO").split(".")[0]
            try:
                Heroku = heroku3.from_key(Var.HEROKU_API)
                app = Heroku.apps()[Var.HEROKU_APP_NAME]
                await ult.edit("`Shutting Down your app, please wait for a minute!`")
                app.process_formation()[dynotype].scale(0)
            except Exception as e:
                LOGS.exception(e)
                return await ult.edit(
                    "`HEROKU_API` and `HEROKU_APP_NAME` is wrong! Kindly re-check in config vars."
                )
        else:
            sys.exit()

    async def hfv(self, api_url, timeout=10):
        try:
            headers = {
                "Authorization": f"Bearer {HUGGING_TOKEN}",
                "Content-Type": "application/json",
            }
            response = await self.async_searcher(api_url, headers=headers, re_json=True)
            stat = response.get("message", "ded")
            return stat
        except Exception as e:
            LOGS.error("Error occurred: %s", e)
            return False

    # ------------------User Extraction----------------#
    async def extract_user(self, message: Message, args: list[str]) -> int | None:
        prev_message = await message.get_reply_message()
        return prev_message.sender_id if prev_message else None

    # -- install / log delivery (class methods since W7: the old
    # module-level function aliases are gone; the union is the one surface)

    async def safeinstall(self, event):
        from .. import HNDLR
        from ..startup.utils import load_addons

        if not event.reply_to:
            return await eod(
                event, f"Please use `{HNDLR}install` as reply to a .py file."
            )
        ok = await eor(event, "`Installing...`")
        reply = await event.get_reply_message()
        if not (
            reply.media
            and hasattr(reply.media, "document")
            and reply.file.name
            and reply.file.name.endswith(".py")
        ):
            return await eod(ok, "`Please reply to any python plugin`")
        plug = reply.file.name.replace(".py", "")
        if plug in list(LOADED):
            return await eod(ok, f"Plugin `{plug}` is already installed.")
        sm = reply.file.name.replace("_", "-").replace("|", "-")
        dl = await reply.download_media(f"addons/{sm}")
        if event.text[9:] != "f":
            read = open(dl).read()
            for dan in KEEP_SAFE().All:
                if re.search(dan, read):
                    os.remove(dl)
                    return await ok.edit(
                        f"**Installation Aborted.**\n**Reason:** Occurance of `{dan}` in `{reply.file.name}`.\n\nIf you trust the provider and/or know what you're doing, use `{HNDLR}install f` to force install.",
                    )
        try:
            load_addons(dl)  # dl.split("/")[-1].replace(".py", ""))
        except Exception:
            os.remove(dl)
            return await eor(ok, f"**ERROR**\n\n`{format_exc()}`", time=30)
        _ttt = time.asctime()
        _time = f"```Installed on: {_ttt}```"
        plug = sm.replace(".py", "")
        if plug in HELP:
            output = f"**Plugin** - `{plug}`\n"
            for i in HELP[plug]:
                output += i
            # output += f"\n {_time}"
            output += "\n© @TeamUltroid"
            installed[plug] = _ttt
            await eod(ok, f"✓ `Ultroid - Installed`: `{plug}` ✓\n\n{output}\n {_time}")
        elif plug in CMD_HELP:
            output = f"Plugin Name-{plug}\n\n✘ Commands Available-\n\n"
            output += f"\n {_time}"
            output += str(CMD_HELP[plug])

            installed[plug] = _ttt
            await eod(ok, f"✓ `Ultroid - Installed`: `{plug}` ✓\n\n{output}")
        else:
            try:
                x = f"Plugin Name-{plug}\n\n✘ Commands Available-\n\n"
                for d in LIST[plug]:
                    x += HNDLR + d + "\n"
                # x += f"\n {_time}"
                installed[plug] = _ttt
                await eod(ok, f"✓ `Ultroid - Installed`: `{plug}` ✓\n\n`{x}`\n {_time}")
            except Exception:
                installed[plug] = _ttt

                await eod(ok, f"✓ `Ultroid - Installed`: `{plug}` ✓\n\n {_time}")

    async def heroku_logs(self, event):
        """
        post heroku logs
        """
        from .. import LOGS

        xx = await eor(event, "`Processing...`")
        if not (Var.HEROKU_API and Var.HEROKU_APP_NAME):
            return await xx.edit(
                "Please set `HEROKU_APP_NAME` and `HEROKU_API` in vars."
            )
        try:
            app = (heroku3.from_key(Var.HEROKU_API)).app(Var.HEROKU_APP_NAME)
        except Exception as se:
            LOGS.info(se)
            return await xx.edit(
                "`HEROKU_API` and `HEROKU_APP_NAME` is wrong! Kindly re-check in config vars."
            )
        await xx.edit("`Downloading Logs...`")
        ok = app.get_log()
        with open("ultroid-heroku.log", "w") as log:
            log.write(ok)
        await event.client.send_file(
            event.chat_id,
            file="ultroid-heroku.log",
            thumb=ULTConfig.thumb,
            caption="**Ultroid Heroku Logs.**",
        )

        os.remove("ultroid-heroku.log")
        await xx.delete()

    async def def_logs(self, ult, file):
        _sex = await ult.eor("🚀")
        try:
            from .tools import get_paste

            with open(file) as f:
                content = f.read()
            _, _id = await get_paste(content)
            cap = f"**Ultroid Logs:**\n```{content[:-400]}```"
            cap += f"\n**[Log pasted here](https://spaceb.in/{_id})**"
            await _sex.edit(
                cap,
                file=file,
            )
        except MediaCaptionTooLongError:
            _lines = open(file).readlines()
            _lines.reverse()
            xx = "".join(line[:100] for line in _lines[:20])
            await _sex.edit(f"**Ultroid logs**\n```{xx[:800]}...```", file=file)
        except:
            _lines = open(file).readlines()
            _lines.reverse()
            xx = "".join(line for line in _lines[:10])
            await _sex.edit(f"**Ultroid logs**\n```{xx[:800]}...```", file=file)


No_Flood = {}

# The addon install log is the class's state; the module-level name is the
# same dict, so the conditional handlers below see every addon they record.


installed = Helper.installed

def _close_http_session_at_exit():
    """Close the shared session on the process-wide union, not a fresh Helper.

    Imported lazily: _fns imports this module, so the union only exists once
    this module has finished loading.
    """
    try:
        from ._fns import fns
    except Exception:
        return

    fns._close_http_session_sync()


atexit.register(_close_http_session_at_exit)


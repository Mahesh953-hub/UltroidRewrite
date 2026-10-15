# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
import inspect
import random
import sys
import time
from logging import Logger

import aiofiles

from telethon import TelegramClient
from telethon import utils as telethon_utils
from telethon.errors import (
    AccessTokenExpiredError,
    AccessTokenInvalidError,
    ApiIdInvalidError,
    AuthKeyDuplicatedError,
)
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.channels import GetFullChannelRequest

from ..configs import Var
from . import *

# ``LOGS`` is the shared process logger defined in ``startup/__init__``.
# Import it directly so class definition cannot fail on the name, exactly
# as every ``_core`` module does.
from .logger import LOGS

from ._loops import ensure_running_loop

loop = ensure_running_loop()


# --
def exponential_backoff(retry_count):
    return min(60, (2**retry_count) + (random.randint(0, 1000) / 1000))


class UltroidClient(TelegramClient):
    def __new__(cls, session, *args, bot_token=None, **kwargs):
        if cls is UltroidClient and bot_token is not None:
            business_cls = globals().get("BusinessCapableClient")
            if business_cls is not None:
                return super().__new__(business_cls)
        return super().__new__(cls)

    def __init__(
        self,
        session,
        api_id=None,
        api_hash=None,
        bot_token=None,
        udB=None,
        logger: Logger = LOGS,
        log_attempt=True,
        exit_on_error=True,
        *args,
        **kwargs,
    ):
        self._cache = {}
        self._dialogs = []
        self._handle_error = exit_on_error
        self._log_at = log_attempt
        self.logger = logger
        self.udB = udB
        self.parse_mode = "v2"
        kwargs["api_id"] = api_id or Var.API_ID
        kwargs["api_hash"] = api_hash or Var.API_HASH
        kwargs["base_logger"] = LOGS
        super().__init__(session, **kwargs)
        self.run_in_loop(self.start_client(bot_token=bot_token))
        self.dc_id = self.session.dc_id

    def __repr__(self):
        return f"<Ultroid.Client :\n self: {self.full_name}\n bot: {bool(self._bot)}\n>"

    async def start_client(self, **kwargs):
        """function to start client"""
        if self._log_at:
            self.logger.info("Trying to login.")
        try:
            await self.start(**kwargs)
        except ApiIdInvalidError:
            self.logger.critical("API ID and API_HASH combination does not match!")

            sys.exit()
        except (AuthKeyDuplicatedError, EOFError) as er:
            import traceback

            self.logger.critical(f"Connection failed: {er} ({type(er)})")
            traceback.print_exc()
            if self._handle_error:
                self.logger.critical("String session expired. Create new!")
                return sys.exit()
            self.logger.critical("String session expired.")
        except (AccessTokenExpiredError, AccessTokenInvalidError):
            self.udB.del_key("BOT_TOKEN")
            self.logger.critical(
                "Bot token is expired or invalid. Create new from @Botfather and add in BOT_TOKEN env variable!"
            )
            sys.exit()
        self.me = await self.get_me()
        if self.me.bot:
            me = f"@{self.me.username}"
        else:
            setattr(self.me, "phone", None)
            me = self.full_name
        if self._log_at:
            self.logger.info(f"Logged in as {me}")
        self._bot = await self.is_bot()

    async def fast_uploader(self, file, **kwargs):
        """Upload files in a faster way"""

        import os
        from pathlib import Path

        start_time = time.time()
        path = Path(file)
        filename = kwargs.get("filename", path.name)
        # Set to True and pass event to show progress bar.
        show_progress = kwargs.get("show_progress", False)
        if show_progress:
            event = kwargs["event"]
        # Whether to use cached file for uploading or not
        use_cache = kwargs.get("use_cache", True)
        # Delete original file after uploading
        to_delete = kwargs.get("to_delete", False)
        message = kwargs.get("message", f"Uploading {filename}...")
        if kwargs.get("parse_mode"):
            parse_mode = kwargs.get("parse_mode", "v2")
        by_bot = self._bot
        size = os.path.getsize(file)
        # Don't show progress bar when file size is less than 5MB.
        if size < 5 * 2**20:
            show_progress = False
        if use_cache and self._cache and self._cache.get("upload_cache"):
            for files in self._cache["upload_cache"]:
                if (
                    files["size"] == size
                    and files["path"] == path
                    and files["name"] == filename
                    and files["by_bot"] == by_bot
                ):
                    if to_delete:
                        try:
                            os.remove(file)
                        except FileNotFoundError:
                            pass
                    return files["raw_file"], time.time() - start_time
        from pyUltroid.fns.FastTelethon import upload_file
        from pyUltroid.fns import fns

        raw_file = None
        while not raw_file:
            with open(file, "rb") as f:
                raw_file = await fns.upload_file(
                    client=self,
                    file=f,
                    filename=filename,
                    progress_callback=(
                        (
                            lambda completed, total: self.loop.create_task(
                                _helper.progress(
                                    completed, total, event, start_time, message
                                )
                            )
                        )
                        if show_progress
                        else None
                    ),
                )
        cache = {
            "by_bot": by_bot,
            "size": size,
            "path": path,
            "name": filename,
            "raw_file": raw_file,
        }
        if self._cache.get("upload_cache"):
            self._cache["upload_cache"].append(cache)
        else:
            self._cache.update({"upload_cache": [cache]})
        if to_delete:
            try:
                os.remove(file)
            except FileNotFoundError:
                pass
        return raw_file, time.time() - start_time

    async def fast_downloader(self, file, **kwargs):
        """Download files in a faster way"""
        # Set to True and pass event to show progress bar.
        show_progress = kwargs.get("show_progress", False)
        filename = kwargs.get("filename", "")
        if show_progress:
            event = kwargs["event"]
        # Don't show progress bar when file size is less than 10MB.
        if file.size < 10 * 2**20:
            show_progress = False
        import mimetypes

        from telethon.tl.types import DocumentAttributeFilename

        from pyUltroid.fns.FastTelethon import download_file
        from pyUltroid.fns import fns

        start_time = time.time()
        # Auto-generate Filename
        if not filename:
            try:
                if isinstance(file.attributes[-1], DocumentAttributeFilename):
                    filename = file.attributes[-1].file_name
            except IndexError:
                mimetype = file.mime_type
                filename = (
                    mimetype.split("/")[0]
                    + "-"
                    + str(round(start_time))
                    + mimetypes.guess_extension(mimetype)
                )
        message = kwargs.get("message", f"Downloading {filename}...")

        async with aiofiles.open(filename, "wb") as f:
            raw_file = await fns.download_file(
                client=self,
                location=file,
                out=f,
                progress_callback=(
                    (
                        lambda completed, total: self.loop.create_task(
                            _helper.progress(
                                completed, total, event, start_time, message
                            )
                        )
                    )
                    if show_progress
                    else None
                ),
            )
        return raw_file, time.time() - start_time

    def run_in_loop(self, function):
        """run inside asyncio loop"""
        return self.loop.run_until_complete(function)

    def run(self):
        """run asyncio loop"""
        self.run_until_disconnected()

    def add_handler(self, func, *args, **kwargs):
        """Add new event handler, ignoring if exists"""
        if func in [_[0] for _ in self.list_event_handlers()]:
            return
        self.add_event_handler(func, *args, **kwargs)

    @property
    def utils(self):
        return telethon_utils

    @property
    def full_name(self):
        """full name of Client"""
        return self.utils.get_display_name(self.me)

    @property
    def uid(self):
        """Client's user id"""
        return self.me.id

    def to_dict(self):
        return dict(inspect.getmembers(self))

    async def parse_id(self, text):
        try:
            text = int(text)
        except ValueError:
            pass
        return await self.get_peer_id(text)
        # return await self.get_peer_id(text)

    async def full_info(self, entity, channel=False):
        try:
            if channel:
                return await self(GetFullChannelRequest(entity))
            return await self(GetFullUserRequest(id=entity))
        except Exception as fe:
            self.logger.error(
                f"full_info(entity={entity!r}, channel={channel}) failed: {fe}"
            )
            return None

    async def listen(self, chat_id, event_builder, timeout=None):
        """Listen for a specific event in a chat and return it."""
        import asyncio

        loop = asyncio.get_running_loop()
        future = loop.create_future()
        target_peer_id = await self.get_peer_id(chat_id)

        async def callback(event):
            try:
                if hasattr(event, "chat_id") and event.chat_id:
                    event_chat_id = await self.get_peer_id(event.chat_id)
                    if event_chat_id == target_peer_id:
                        if not future.done():
                            future.set_result(event)
            except Exception as e:
                if not future.done():
                    future.set_exception(e)

        self.add_event_handler(callback, event_builder)
        try:
            if timeout:
                return await asyncio.wait_for(future, timeout)
            return await future
        finally:
            self.remove_event_handler(callback, event_builder)

    async def listen_business(self, **kwargs):
        """Wait for business updates matching rich filters (see startup.listen)."""
        from .listen import listen_business

        return await listen_business(self, **kwargs)

    @property
    def http(self):
        if (
            not hasattr(self, "_http_session")
            or self._http_session is None
            or self._http_session.closed
        ):
            import aiohttp

            self._http_session = aiohttp.ClientSession()
        return self._http_session


class BusinessBotsClient(UltroidClient):
    """Assistant/bot client with subclass-level business-connection wrapping.

    the business ``InvokeWithBusinessConnectionRequest`` wrapping lives
    here, on a subclass of :class:`UltroidClient`, instead of being monkey-patched
    onto ``TelegramClient.__call__`` globally. User-account clients are plain
    ``UltroidClient`` instances and are never affected by the business context
    variable.
    """

    async def __call__(self, query, *args, **kwargs):
        from .business import (
            business_connection_id,
            _BUSINESS_WRAPPABLE_REQUESTS,
        )

        conn_id = business_connection_id.get()
        if conn_id:
            request_name = query.__class__.__name__
            if request_name in _BUSINESS_WRAPPABLE_REQUESTS:
                from telethon.functions import InvokeWithBusinessConnectionRequest

                query = InvokeWithBusinessConnectionRequest(conn_id, query)
        return await super().__call__(query, *args, **kwargs)


class PyTgCallsClient(UltroidClient):
    """PyTgCalls-facing subclass of :class:`UltroidClient`.

    py-tgcalls gates client support on the client's *module name*:
    ``BridgedClient.package_name(client)`` returns
    ``client.__class__.__module__.split('.')[0]`` and ``MtProtoClient`` only
    binds a bridge for ``"pyrogram"`` / ``"telethon"``  -  anything else raises
    ``InvalidMTProtoClient`` (pytgcalls/mtproto/mtproto_client.py). A genuine
    ``UltroidClient`` reports ``"pyUltroid"`` and is rejected even though it
    is a real ``TelegramClient`` subclass.

    Re-rooting ``__module__`` here (a class-body attribute, NOT a mutation of
    a live instance's ``__class__``) makes the gate see ``"telethon"`` while
    ``isinstance(client, (UltroidClient, TelegramClient))`` keeps working.
    ``UltroidClient`` itself stays truthful.

    Pickle caveat: pickle resolves classes by ``__module__`` + ``__qualname__``
    → ``telethon.client.telegramclient.PyTgCallsClient`` does not exist,
    so instances are unpicklable. The client holds live sockets and must never
    be pickled anyway.
    """

    __module__ = "telethon.client.telegramclient"


# endregion

# copied from https://github.com/tulir/mautrix-telegram/blob/master/mautrix_telegram/util/parallel_file_transfer.py
# Copyright (C) 2021-2023 Tulir Asokan

import asyncio
import hashlib
import logging
import math
import os
from collections import defaultdict
from typing import (
    Any,
    BinaryIO,
    DefaultDict,
    List,
    Optional,
    Tuple,
    Union,
)
from collections.abc import AsyncGenerator, Awaitable

from telethon import TelegramClient, helpers, utils
from telethon.helpers import _maybe_await
from telethon.network import MTProtoSender
from telethon.tl.functions.upload import (
    GetFileRequest,
    SaveBigFilePartRequest,
    SaveFilePartRequest,
)
from telethon.tl.types import (
    Document,
    InputDocumentFileLocation,
    InputFile,
    InputFileBig,
    InputFileLocation,
    InputPeerPhotoFileLocation,
    InputPhotoFileLocation,
    TypeInputFile,
)

log: logging.Logger = logging.getLogger("_FastTelethon")

TypeLocation = Union[
    Document,
    InputDocumentFileLocation,
    InputPeerPhotoFileLocation,
    InputFileLocation,
    InputPhotoFileLocation,
]


class FastTelethonCapabilityError(NotImplementedError):
    """Raised when the pinned Telethon fork no longer exposes the internal
    sender/connection APIs that the parallel transfer helper depends on.

    Callers should fall back to the normal Telethon upload/download methods
    (``client.upload_file`` / ``client.download_file``) instead.
    """


class DownloadSender:
    client: TelegramClient
    sender: MTProtoSender
    request: GetFileRequest
    remaining: int
    stride: int

    def __init__(
        self,
        client: TelegramClient,
        sender: MTProtoSender,
        file: TypeLocation,
        offset: int,
        limit: int,
        stride: int,
        count: int,
    ) -> None:
        self.sender = sender
        self.client = client
        self.request = GetFileRequest(file, offset=offset, limit=limit)
        self.stride = stride
        self.remaining = count

    async def next(self) -> bytes | None:
        if not self.remaining:
            return None
        result = await self.client._call(self.sender, self.request)
        self.remaining -= 1
        self.request.offset += self.stride
        return result.bytes

    def disconnect(self) -> Awaitable[None]:
        return self.sender.disconnect()


class UploadSender:
    client: TelegramClient
    sender: MTProtoSender
    request: SaveFilePartRequest | SaveBigFilePartRequest
    part_count: int
    stride: int

    def __init__(
        self,
        client: TelegramClient,
        sender: MTProtoSender,
        file_id: int,
        part_count: int,
        big: bool,
        index: int,
        stride: int,
        pipeline_depth: int = 3,
    ) -> None:
        self.client = client
        self.sender = sender
        self.part_count = part_count
        if big:
            self.request = SaveBigFilePartRequest(file_id, index, part_count, b"")
        else:
            self.request = SaveFilePartRequest(file_id, index, b"")
        self.stride = stride
        self.pipeline_depth = pipeline_depth
        self.inflight: list[asyncio.Task] = []

    async def next(self, data: bytes) -> None:
        task = asyncio.create_task(self._next(data))
        self.inflight.append(task)
        if len(self.inflight) > self.pipeline_depth:
            await self.inflight.pop(0)

    async def _next(self, data: bytes) -> None:
        self.request.bytes = data
        await self.client._call(self.sender, self.request)
        self.request.file_part += self.stride

    async def disconnect(self) -> None:
        if self.inflight:
            await asyncio.gather(*self.inflight)
        return await self.sender.disconnect()


class _BorrowedSender:
    """A no-op-disconnect wrapper around the client's live ``MTProtoSender``.

    FastTelethon borrows the main sender for same-DC transfers instead of
    exporting an authorization (which Telethon rejects with
    ``DcIdInvalidError``).  The transfer lifecycle, however, treats every
    sender as *owned*: ``_cleanup`` -> ``UploadSender``/``DownloadSender``
    -> ``sender.disconnect()``, and ``disconnect`` is always invoked.  Borrowing
    ``client._sender`` verbatim would therefore tear down the bot's primary
    MTProto connection when the transfer finishes, and with multiple
    connections it would be handed out and disconnected repeatedly.

    This wrapper forwards every attribute to the real sender (so
    ``client._call(sender, request)`` and the live send path still work
    unchanged) but neutralises ``disconnect`` - a borrowed sender is never
    closed by the transfer that borrowed it.
    """

    __slots__ = ("_sender",)

    def __init__(self, sender: MTProtoSender) -> None:
        self._sender = sender

    def __getattr__(self, name: str) -> Any:
        # Forward everything else (send, connection, dc_id, ...) to the live
        # sender.  Real methods are looked up on the class first, so the
        # overridden `disconnect` below always wins.
        return getattr(self._sender, name)

    async def disconnect(self) -> None:
        # Intentionally a no-op.  We borrowed the client's live sender and
        # must not tear it down when this transfer finishes.
        return None


class ParallelTransferrer:
    client: TelegramClient
    dc_id: int
    senders: list[DownloadSender | UploadSender] | None
    upload_ticker: int

    def __init__(self, client: TelegramClient, dc_id: int | None = None) -> None:
        self.client = client
        self.dc_id = dc_id or self.client.session.dc_id
        self.senders = None
        self.upload_ticker = 0

    @property
    def _is_current_dc(self) -> bool:
        """True when this transfer targets the DC the client is already on.

        Same-DC transfers reuse the client's live sender (wrapped, see
        ``_create_sender``) rather than spinning up exported DC channels.  A
        single borrowed ``MTProtoSender`` cannot be parallelised safely, so
        same-DC transfers are pinned to one connection.
        """
        return self.dc_id == self.client.session.dc_id

    async def _cleanup(self) -> None:
        await asyncio.gather(*[sender.disconnect() for sender in self.senders])
        self.senders = None

    @staticmethod
    def _get_connection_count(file_size: int) -> int:
        full_size = 100 * (1024**2)
        if file_size > full_size:
            return 40
        return math.ceil((file_size / full_size) * 20)

    async def _init_download(
        self, connections: int, file: TypeLocation, part_count: int, part_size: int
    ) -> None:
        minimum, remainder = divmod(part_count, connections)

        def get_part_count() -> int:
            nonlocal remainder
            if remainder > 0:
                remainder -= 1
                return minimum + 1
            return minimum

        self.senders = [
            await self._create_download_sender(
                file, 0, part_size, connections * part_size, get_part_count()
            ),
            *await asyncio.gather(
                *[
                    self._create_download_sender(
                        file, i, part_size, connections * part_size, get_part_count()
                    )
                    for i in range(1, connections)
                ]
            ),
        ]

    async def _create_download_sender(
        self,
        file: TypeLocation,
        index: int,
        part_size: int,
        stride: int,
        part_count: int,
    ) -> DownloadSender:
        return DownloadSender(
            self.client,
            await self._create_sender(),
            file,
            index * part_size,
            part_size,
            stride,
            part_count,
        )

    async def _init_upload(
        self, connections: int, file_id: int, part_count: int, big: bool
    ) -> None:
        self.senders = [
            await self._create_upload_sender(file_id, part_count, big, 0, connections),
            *await asyncio.gather(
                *[
                    self._create_upload_sender(file_id, part_count, big, i, connections)
                    for i in range(1, connections)
                ]
            ),
        ]

    async def _create_upload_sender(
        self, file_id: int, part_count: int, big: bool, index: int, stride: int
    ) -> UploadSender:
        return UploadSender(
            self.client,
            await self._create_sender(),
            file_id,
            part_count,
            big,
            index,
            stride,
        )

    async def _create_sender(self) -> Any:
        # W3-D: the original code hand-rolled MTProtoSender construction and the
        # export-authorization flow.  The pinned Telethon fork already exposes
        # that logic in ``_create_exported_sender``.  Reuse it so we stay in
        # sync with the fork's connection parameters (local_addr, proxy, etc.).
        if not hasattr(self.client, "_create_exported_sender"):
            raise FastTelethonCapabilityError(
                "The pinned Telethon fork no longer exposes _create_exported_sender; "
                "parallel FastTelethon transfers cannot be created. "
                "Use the standard Telethon upload_file/download_file methods instead."
            )
        # Telethon raises DcIdInvalidError ("authorization tried to be
        # exported for the same data center one is currently connected to")
        # when _create_exported_sender is asked for the *current* DC, because
        # you cannot export-authorization for a DC you already hold a session
        # on.  The upload/download path therefore reuses the client's live
        # main sender for its own DC and only spins up an exported sender for
        # a *different* DC (the cross-DC parallel case).  This keeps large
        # single-DC uploads like `.rename paste.txt` working instead of
        # crashing mid-transfer.
        #
        # The live sender is wrapped in a no-op-disconnect adapter so the
        # transfer lifecycle cannot tear down the bot's primary MTProto
        # connection on cleanup (see _BorrowedSender).
        if self.dc_id == self.client.session.dc_id:
            return _BorrowedSender(self.client._sender)
        return await self.client._create_exported_sender(self.dc_id)

    async def init_upload(
        self,
        file_id: int,
        file_size: int,
        part_size_kb: float | None = None,
        connection_count: int | None = None,
    ) -> tuple[int, int, bool]:
        connection_count = connection_count or self._get_connection_count(file_size)
        # A borrowed (same-DC) MTProtoSender is a single live connection and
        # cannot be parallelised; pin to one channel so we never register the
        # bot's primary sender N times and disconnect it N times on cleanup.
        if self._is_current_dc:
            connection_count = 1
        part_size = min(
            (part_size_kb or utils.get_appropriated_part_size(file_size)) * 1024,
            512 * 1024,
        )
        part_count = (file_size + part_size - 1) // part_size
        is_large = file_size > 10 * (1024**2)
        await self._init_upload(connection_count, file_id, part_count, is_large)
        return part_size, part_count, is_large

    async def upload(self, part: bytes) -> None:
        await self.senders[self.upload_ticker].next(part)
        self.upload_ticker = (self.upload_ticker + 1) % len(self.senders)

    async def finish_upload(self) -> None:
        await self._cleanup()

    async def download(
        self,
        file: TypeLocation,
        file_size: int,
        part_size_kb: float | None = None,
        connection_count: int | None = None,
    ) -> AsyncGenerator[bytes, None]:
        connection_count = connection_count or self._get_connection_count(file_size)
        # Same-DC: borrow the main sender (one channel), see init_upload.
        if self._is_current_dc:
            connection_count = 1
        part_size = min(
            (part_size_kb or utils.get_appropriated_part_size(file_size)) * 1024,
            512 * 1024,
        )
        part_count = math.ceil(file_size / part_size)
        await self._init_download(connection_count, file, part_count, part_size)

        part = 0
        while part < part_count:
            tasks = [sender.next() for sender in self.senders]
            results = await asyncio.gather(*tasks)
            for data in results:
                if not data:
                    break
                yield data
                part += 1
        await self._cleanup()


parallel_transfer_locks: DefaultDict[int, asyncio.Lock] = defaultdict(
    lambda: asyncio.Lock()
)


def stream_file(file_to_stream: BinaryIO, chunk_size=1024):
    while True:
        data_read = file_to_stream.read(chunk_size)
        if not data_read:
            break
        yield data_read


async def _internal_transfer_to_telegram(
    client: TelegramClient,
    response: BinaryIO,
    filename: str,
    progress_callback: callable,
) -> tuple[TypeInputFile, int]:
    file_id = helpers.generate_random_long()
    file_size = os.path.getsize(response.name)

    hash_md5 = hashlib.md5()
    uploader = ParallelTransferrer(client)
    part_size, part_count, is_large = await uploader.init_upload(file_id, file_size)
    buffer = bytearray()
    for data in stream_file(response):
        if progress_callback:
            try:
                await _maybe_await(progress_callback(response.tell(), file_size))
            except Exception:
                pass
        if not is_large:
            hash_md5.update(data)
        if len(buffer) == 0 and len(data) == part_size:
            await uploader.upload(data)
            continue
        new_len = len(buffer) + len(data)
        if new_len >= part_size:
            cutoff = part_size - len(buffer)
            buffer.extend(data[:cutoff])
            await uploader.upload(bytes(buffer))
            buffer.clear()
            buffer.extend(data[cutoff:])
        else:
            buffer.extend(data)
    if len(buffer) > 0:
        await uploader.upload(bytes(buffer))
    await uploader.finish_upload()
    if is_large:
        return InputFileBig(file_id, part_count, filename), file_size
    return InputFile(file_id, part_count, filename, hash_md5.hexdigest()), file_size


async def download_file(
    client: TelegramClient,
    location: TypeLocation,
    out: BinaryIO,
    progress_callback: callable = None,
) -> BinaryIO:
    size = location.size
    dc_id, location = utils.get_input_location(location)
    # We lock the transfers because telegram has connection count limits
    downloader = ParallelTransferrer(client, dc_id)
    downloaded = downloader.download(location, size)
    async for x in downloaded:
        out.write(x)
        if progress_callback:
            try:
                await _maybe_await(progress_callback(out.tell(), size))
            except Exception:
                pass
    return out


async def upload_file(
    client: TelegramClient,
    file: BinaryIO,
    filename: str,
    progress_callback: callable = None,
) -> TypeInputFile:
    return (
        await _internal_transfer_to_telegram(client, file, filename, progress_callback)
    )[0]

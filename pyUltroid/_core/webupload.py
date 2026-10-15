# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""Web upload backbone"""

from __future__ import annotations

import json as _json
import mimetypes
import os
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional, Tuple
from collections.abc import Awaitable, Callable
from urllib.parse import quote as _urlquote

import aiohttp

__all__ = [
    "WebUploader",
    "UploadResult",
    "UploadError",
    "get_uploader",
    "upload_to",
    "resolve_service",
    "UPLOAD_SERVICES",
    "MENU_SERVICES",
    "SERVICE_LABELS",
    "SERVICE_ALIASES",
    "CatboxUploader",
    "LitterboxUploader",
    "FileIoUploader",
    "NullStUploader",
    "TransferShUploader",
    "UguuUploader",
    "FilebinUploader",
    "SiaskyUploader",
]


_CHUNK = 1 << 16
_CRLF = b"\r\n"

ProgressFn = Optional[Callable[[int, int], Awaitable[None]]]

_UA = "Mozilla/5.0 (X11; Linux x86_64) Ultroid-WebUpload/1.0"


class UploadError(Exception):
    """Raised when a web upload fails (network, HTTP, or service-level)."""

    def __init__(self, message: str, *, service: str = "", status: int = 0) -> None:
        super().__init__(message)
        self.service = service
        self.status = status


@dataclass
class UploadResult:
    """Outcome of a successful web upload."""

    url: str
    service: str = ""
    delete_url: str | None = None
    delete_note: str | None = None
    expires: str | None = None
    raw: Any = None

    def __str__(self) -> str:
        return self.url


# Multipart helpers
def _guess_ctype(path: str) -> str:
    ctype, _ = mimetypes.guess_type(path)
    return ctype or "application/octet-stream"


def _field_part(boundary: bytes, name: str, value: Any) -> bytes:
    return (
        b"--"
        + boundary
        + _CRLF
        + b'Content-Disposition: form-data; name="'
        + str(name).encode()
        + b'"'
        + _CRLF
        + _CRLF
        + str(value).encode()
        + _CRLF
    )


def _file_header(boundary: bytes, field: str, filename: str, ctype: str) -> bytes:
    disp = f'Content-Disposition: form-data; name="{field}"; filename="{filename}"'
    return (
        b"--"
        + boundary
        + _CRLF
        + disp.encode()
        + _CRLF
        + f"Content-Type: {ctype}".encode()
        + _CRLF
        + _CRLF
    )


def _closing(boundary: bytes) -> bytes:
    return _CRLF + b"--" + boundary + b"--" + _CRLF


# Base uploader
class WebUploader:
    """Base class for single-file uploads to a web hosting service.

    Args:
        session: Optional shared ``aiohttp.ClientSession`` (e.g.
            ``event.client.http``).  When ``None`` a short-lived session is
            created and closed per upload.
        timeout: Per-request timeout in seconds.
    """

    name = "base"
    endpoint = ""
    method = "POST"
    multipart = True
    file_field = "file"
    json_response = False

    def __init__(self, *, session: Any = None, timeout: float = 300) -> None:
        self._session = session
        self.timeout = float(timeout)

    # ── overridable hooks
    def fields(self, file_path: str) -> list[tuple[str, Any]]:
        """Extra text form fields (multipart services) as ``(name, value)``."""
        return []

    def url(self, file_path: str) -> str:
        """Upload endpoint URL (may depend on the file name)."""
        return self.endpoint

    def headers(self, file_path: str) -> dict:
        """Extra request headers (e.g. ``User-Agent``)."""
        return {}

    def parse_response(
        self, status: int, text: str, json_data: Any, file_path: str
    ) -> UploadResult:
        """Turn a successful (2xx) HTTP response into an :class:`UploadResult`."""
        raise NotImplementedError

    # ── transport
    def _build_multipart(self, file_path: str, total: int, on_progress: ProgressFn):
        """Return ``(async generator, content_type, content_length)``."""
        boundary = uuid.uuid4().hex.encode()
        field_blob = b"".join(
            _field_part(boundary, n, v) for n, v in self.fields(file_path)
        )
        head = _file_header(
            boundary,
            self.file_field,
            os.path.basename(file_path),
            _guess_ctype(file_path),
        )
        tail = _closing(boundary)
        content_type = f"multipart/form-data; boundary={boundary.decode()}"
        content_length = len(field_blob) + len(head) + total + len(tail)

        async def stream():
            yield field_blob
            yield head
            sent = 0
            with open(file_path, "rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    yield chunk
                    sent += len(chunk)
                    if on_progress:
                        await on_progress(sent, total)
            yield tail

        return stream(), content_type, content_length

    def _build_raw(self, file_path: str, total: int, on_progress: ProgressFn):
        """Return ``(async generator, content_type, content_length)`` for raw body."""
        content_length = total

        async def stream():
            sent = 0
            with open(file_path, "rb") as fh:
                while True:
                    chunk = fh.read(_CHUNK)
                    if not chunk:
                        break
                    yield chunk
                    sent += len(chunk)
                    if on_progress:
                        await on_progress(sent, total)
            if on_progress:
                await on_progress(sent, total)

        return stream(), "application/octet-stream", content_length

    async def _send(self, session, timeout, file_path, body, headers):
        async with session.request(
            self.method,
            self.url(file_path),
            headers=headers,
            data=body,
            timeout=timeout,
        ) as resp:
            text = await resp.text()
            json_data: Any = None
            if self.json_response:
                try:
                    json_data = await resp.json(content_type=None)
                except Exception:
                    if text:
                        try:
                            json_data = _json.loads(text)
                        except Exception:
                            json_data = None
            if resp.status >= 400:
                raise UploadError(
                    f"{self.name} returned HTTP {resp.status}: {text[:300]}",
                    service=self.name,
                    status=resp.status,
                )
            result = self.parse_response(resp.status, text, json_data, file_path)
            if not result.service:
                result.service = self.name
            return result

    async def upload(
        self, file_path: str, *, on_progress: ProgressFn = None
    ) -> UploadResult:
        """Upload *file_path* and return an :class:`UploadResult`.

        Raises :class:`UploadError` on any failure (missing file, network
        error, non-2xx response, or a parse failure).
        """
        if not os.path.isfile(file_path):
            raise UploadError(f"File not found: {file_path}", service=self.name)
        total = os.path.getsize(file_path)

        own = self._session is None
        session = self._session or aiohttp.ClientSession()
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        try:
            if self.multipart:
                body, ctype, clen = self._build_multipart(file_path, total, on_progress)
            else:
                body, ctype, clen = self._build_raw(file_path, total, on_progress)
            headers = dict(self.headers(file_path))
            headers.setdefault("Content-Type", ctype)
            headers.setdefault("Content-Length", str(clen))
            return await self._send(session, timeout, file_path, body, headers)
        except UploadError:
            raise
        except aiohttp.ClientError as exc:
            raise UploadError(
                f"{self.name} upload failed: {exc}", service=self.name
            ) from exc
        except Exception as exc:  # noqa: BLE001 - surface any failure uniformly
            raise UploadError(
                f"{self.name} upload failed: {exc}", service=self.name
            ) from exc
        finally:
            if own:
                await session.close()


# Service subclasses
class CatboxUploader(WebUploader):
    """catbox.moe  -  permanent anonymous host (≤ 200 MB). Returns a plain URL."""

    name = "catbox"
    endpoint = "https://catbox.moe/user/api.php"
    file_field = "fileToUpload"
    json_response = False

    def fields(self, file_path):
        return [("reqtype", "fileupload")]

    def headers(self, file_path):
        return {"User-Agent": _UA}

    def parse_response(self, status, text, json_data, file_path):
        url = ((text or "").strip().splitlines() or [""])[0].strip()
        if not url.startswith("http"):
            raise UploadError(
                f"catbox did not return a URL: {text[:200]}",
                service=self.name,
                status=status,
            )
        return UploadResult(url=url, service=self.name, raw=text)


class LitterboxUploader(WebUploader):
    """litterbox.catbox.moe  -  temporary catbox host. Expires after ``expire``."""

    name = "litterbox"
    endpoint = "https://litterbox.catbox.moe/resources/internals/api.php"
    file_field = "fileToUpload"
    json_response = False
    expire = "72h"  # one of 1h / 12h / 24h / 72h

    def fields(self, file_path):
        return [("reqtype", "fileupload"), ("time", self.expire)]

    def headers(self, file_path):
        return {"User-Agent": _UA}

    def parse_response(self, status, text, json_data, file_path):
        url = ((text or "").strip().splitlines() or [""])[0].strip()
        if not url.startswith("http"):
            raise UploadError(
                f"litterbox did not return a URL: {text[:200]}",
                service=self.name,
                status=status,
            )
        return UploadResult(url=url, service=self.name, expires=self.expire, raw=text)


class FileIoUploader(WebUploader):
    """file.io  -  temporary host (default 14-day expiry). Returns JSON."""

    name = "file.io"
    endpoint = "https://file.io"
    file_field = "file"
    json_response = True

    def headers(self, file_path):
        return {"User-Agent": _UA}

    def parse_response(self, status, text, json_data, file_path):
        data = json_data if isinstance(json_data, dict) else {}
        link = data.get("link") or ""
        key = data.get("key")
        if not link and key:
            link = f"https://file.io/{key}"
        if not str(link).startswith("http"):
            raise UploadError(
                f"file.io did not return a link: {text[:200]}",
                service=self.name,
                status=status,
            )
        delete_url = f"https://file.io/{key}" if key else None
        return UploadResult(
            url=link,
            service=self.name,
            delete_url=delete_url,
            delete_note="DELETE request" if delete_url else None,
            expires=data.get("expiry"),
            raw=data,
        )


class NullStUploader(WebUploader):
    """0x0.st  -  anonymous host (≤ 512 MiB, ≥30-day retention). Returns a URL."""

    name = "0x0.st"
    endpoint = "https://0x0.st"
    file_field = "file"
    json_response = False

    def headers(self, file_path):
        return {"User-Agent": _UA}

    def parse_response(self, status, text, json_data, file_path):
        url = ((text or "").strip().splitlines() or [""])[0].strip()
        if not url.startswith("http"):
            raise UploadError(
                f"0x0.st did not return a URL: {text[:200]}",
                service=self.name,
                status=status,
            )
        return UploadResult(url=url, service=self.name, raw=text)


class TransferShUploader(WebUploader):
    """transfer.sh  -  temporary host. Uses HTTP PUT with the filename in the path."""

    name = "transfer"
    endpoint = "https://transfer.sh"
    method = "PUT"
    multipart = False
    json_response = False

    def url(self, file_path):
        return f"{self.endpoint}/{_urlquote(os.path.basename(file_path))}"

    def headers(self, file_path):
        return {"User-Agent": _UA, "Max-Days": "14"}

    def parse_response(self, status, text, json_data, file_path):
        url = ((text or "").strip().splitlines() or [""])[0].strip()
        if not url.startswith("http"):
            raise UploadError(
                f"transfer.sh did not return a URL: {text[:200]}",
                service=self.name,
                status=status,
            )
        return UploadResult(url=url, service=self.name, expires="14 days", raw=text)


class UguuUploader(WebUploader):
    """uguu.se  -  temporary host (3-hour expiry, ≤ 128 MiB). Returns JSON."""

    name = "uguu"
    endpoint = "https://uguu.se/upload"
    file_field = "files[]"
    json_response = True

    def headers(self, file_path):
        return {"User-Agent": _UA}

    def parse_response(self, status, text, json_data, file_path):
        data = json_data if isinstance(json_data, dict) else {}
        files = data.get("files") or []
        url = ""
        if isinstance(files, list) and files:
            first = files[0]
            url = (first.get("url") if isinstance(first, dict) else "") or ""
        if not url:
            url = data.get("url") or ""
        if not str(url).startswith("http"):
            raise UploadError(
                f"uguu did not return a URL: {text[:200]}",
                service=self.name,
                status=status,
            )
        return UploadResult(url=url, service=self.name, expires="3 hours", raw=data)


class FilebinUploader(WebUploader):
    """filebin.net  -  bin-based host. Path upload ``POST /<bin>/<filename>``."""

    name = "filebin"
    endpoint = "https://filebin.net"
    method = "POST"
    multipart = False
    json_response = True

    def _bin(self, file_path):
        return f"ultroid-{uuid.uuid4().hex[:10]}"

    def url(self, file_path):
        return f"{self.endpoint}/{self._bin(file_path)}/{_urlquote(os.path.basename(file_path))}"

    def headers(self, file_path):
        return {"User-Agent": _UA}

    def parse_response(self, status, text, json_data, file_path):
        data = json_data if isinstance(json_data, dict) else {}
        bin_id = None
        bin_field = data.get("bin")
        if isinstance(bin_field, dict):
            bin_id = bin_field.get("id") or bin_field.get("bin")

        elif isinstance(bin_field, str):
            bin_id = bin_field

        url = self.url(file_path)
        delete_url = f"https://filebin.net/{bin_id}" if bin_id else None
        return UploadResult(
            url=url,
            service=self.name,
            delete_url=delete_url,
            delete_note="DELETE request" if delete_url else None,
            raw=data,
        )


class SiaskyUploader(WebUploader):
    """Sia Skynet host (legacy endpoint; may be unavailable). Returns JSON."""

    name = "siasky"
    endpoint = "https://siasky.net/skynet/skyfile"
    file_field = "file"
    json_response = True

    def headers(self, file_path):
        return {"User-Agent": _UA}

    def parse_response(self, status, text, json_data, file_path):
        data = json_data if isinstance(json_data, dict) else {}
        skylink = data.get("skylink") or ""
        if not skylink:
            raise UploadError(
                f"siasky did not return a skylink: {text[:200]}",
                service=self.name,
                status=status,
            )
        return UploadResult(
            url=f"https://siasky.net/{skylink}", service=self.name, raw=data
        )


# Registry / public helpers
_UPLOADERS = {
    "catbox": CatboxUploader,
    "litterbox": LitterboxUploader,
    "file.io": FileIoUploader,
    "0x0.st": NullStUploader,
    "transfer": TransferShUploader,
    "uguu": UguuUploader,
    "filebin": FilebinUploader,
    "siasky": SiaskyUploader,
}


UPLOAD_SERVICES = tuple(_UPLOADERS.keys())

MENU_SERVICES = (
    "catbox",
    "litterbox",
    "file.io",
    "0x0.st",
    "transfer",
    "uguu",
    "filebin",
)

SERVICE_LABELS = {
    "catbox": "catbox",
    "litterbox": "litterbox",
    "file.io": "file.io",
    "0x0.st": "0x0.st",
    "transfer": "transfer.sh",
    "uguu": "uguu",
    "filebin": "filebin",
    "siasky": "siasky",
}

SERVICE_ALIASES = {
    "catbox.moe": "catbox",
    "litterbox.catbox.moe": "litterbox",
    "fileio": "file.io",
    "file_io": "file.io",
    "0x0": "0x0.st",
    "0x0st": "0x0.st",
    "nullpointer": "0x0.st",
    "transfer.sh": "transfer",
    "transfersh": "transfer",
    "uguu.se": "uguu",
}

_ALIAS_MAP = {k.lower(): v for k, v in SERVICE_ALIASES.items()}


def resolve_service(name: str) -> str | None:
    """Normalise a user-typed service name to a registry key, or ``None``."""
    if name is None:
        return None
    key = str(name).strip().lower()
    if not key:
        return None
    if key in _UPLOADERS:
        return key
    return _ALIAS_MAP.get(key)


def get_uploader(name: str, *, session: Any = None, timeout: float = 300):
    """Return an uploader instance for *name* (key or alias), or ``None``."""
    key = resolve_service(name)
    if key is None:
        return None
    return _UPLOADERS[key](session=session, timeout=timeout)


async def upload_to(
    name: str,
    file_path: str,
    *,
    on_progress: ProgressFn = None,
    session: Any = None,
    timeout: float = 300,
) -> UploadResult:
    """Upload *file_path* to service *name* (key or alias) in one call."""
    uploader = get_uploader(name, session=session, timeout=timeout)
    if uploader is None:
        raise UploadError(f"Unknown upload service: {name}", service=str(name))
    return await uploader.upload(file_path, on_progress=on_progress)

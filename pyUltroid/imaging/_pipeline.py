# Ultroid - UserBot Rewrite
# pyUltroid/imaging/_pipeline.py
#
# ImagePipeline  -  the binary and format layer of HIP.
#
# Responsibilities:
#   - Receive any image input: bytes, file path, URL, PIL Image, BytesIO,
#     base64 string, Telegram media  -  and normalise to a working PIL Image
#   - Detect format, validate, convert between formats
#   - Route operations to PIL or OpenCV depending on what's available
#     (on AWS both are always available  -  routing is for capability, not fallback)
#   - Return results as bytes, PIL Image, or BytesIO ready for Telegram upload
#   - Track operation history for AI direction
#
# Design:
#   Every operation returns an ImageResult  -  never raises.
#   Errors are captured in ImageResult.error.
#   The AI agent reads ImageResult and decides what to do next.

from __future__ import annotations

import base64
import hashlib
import io
import os
import time
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from pyUltroid.startup.logger import LOGS

# ── Type alias ──────────────────────────────────────────────────────────────

ImageInput = Union[bytes, str, "Path", "BytesIO", Any]  # Any = PIL.Image
MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000


# ── ImageResult ────────────────────────────────────────────────────────────


@dataclass
class ImageResult:
    """
    Unified result from every pipeline operation.
    Always returned  -  never raises.
    """

    image: Any | None = None  # PIL.Image object
    data: bytes | None = None  # raw bytes (populated by to_bytes())
    format: str = "PNG"  # output format
    width: int = 0
    height: int = 0
    mode: str = "RGB"
    duration_ms: float = 0.0
    op_chain: list[str] = field(default_factory=list)  # operations applied
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and (self.image is not None or self.data is not None)

    @property
    def size(self) -> tuple[int, int]:
        return (self.width, self.height)

    def to_bytes(self, fmt: str = None) -> bytes | None:
        """Export as bytes in the specified format."""
        fmt = (fmt or self.format).upper()
        if self.data and not self.image:
            return self.data
        if self.image is None:
            return None
        try:
            buf = BytesIO()
            img = self.image
            if fmt == "JPEG" and img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            img.save(buf, format=fmt, optimize=True)
            self.data = buf.getvalue()
            return self.data
        except Exception as e:
            LOGS.error(f"[HIP] to_bytes error: {e}")
            return None

    def to_bytesio(
        self, fmt: str = None, filename: str = "image.png"
    ) -> BytesIO | None:
        """Export as BytesIO  -  ready for Telegram send_file."""
        data = self.to_bytes(fmt)
        if data is None:
            return None
        buf = BytesIO(data)
        buf.name = filename
        return buf

    def thumbnail(self, max_size: int = 320) -> BytesIO | None:
        """Generate a small thumbnail BytesIO for preview."""
        if self.image is None:
            return None
        try:
            thumb = self.image.copy()
            thumb.thumbnail((max_size, max_size))
            buf = BytesIO()
            thumb.save(buf, format="JPEG", quality=70)
            buf.name = "thumb.jpg"
            buf.seek(0)
            return buf
        except Exception:
            return None

    def __repr__(self) -> str:
        status = "OK" if self.ok else f"ERR({self.error[:30]})"
        return f"ImageResult({status} {self.width}x{self.height} {self.format} ops={self.op_chain})"


# ── ImagePipeline ──────────────────────────────────────────────────────────


class ImagePipeline:
    """
    The binary and format routing layer.
    Normalises any input to PIL, dispatches to processors.

    Usage:
        from pyUltroid.imaging import imaging

        result = imaging.load(image_bytes)
        result = imaging.load_url("https://example.com/img.jpg")
        result = imaging.load_tg(event)   # from Telegram message
    """

    # ── PIL / CV2 availability ─────────────────────────────────────────────

    @staticmethod
    def _pil():
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

        return Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageEnhance

    @staticmethod
    def _cv2():
        try:
            import cv2
            import numpy as np

            return cv2, np
        except ImportError:
            return None, None

    # ── Load from any source ───────────────────────────────────────────────

    def load(self, src: ImageInput, format_hint: str = None) -> ImageResult:
        """
        Normalise any image input to a PIL Image.
        src can be: bytes, str (path or base64), BytesIO, PIL.Image, file path.
        """
        t0 = time.time()
        Image, *_ = self._pil()

        try:
            if isinstance(src, Image.Image):
                img = src

            elif isinstance(src, (bytes, bytearray)):
                if len(src) > MAX_IMAGE_BYTES:
                    raise ValueError(f"Image input exceeds {MAX_IMAGE_BYTES} bytes")
                img = Image.open(BytesIO(src))

            elif isinstance(src, BytesIO):
                if src.getbuffer().nbytes > MAX_IMAGE_BYTES:
                    raise ValueError(f"Image input exceeds {MAX_IMAGE_BYTES} bytes")
                src.seek(0)
                img = Image.open(src)

            elif isinstance(src, (str, Path)):
                src = str(src)
                if src.startswith("data:"):
                    # data URI: data:image/png;base64,....
                    _, data = src.split(",", 1)
                    if len(data) > (MAX_IMAGE_BYTES * 4 // 3) + 4:
                        raise ValueError(f"Image input exceeds {MAX_IMAGE_BYTES} bytes")
                    raw = base64.b64decode(data)
                    if len(raw) > MAX_IMAGE_BYTES:
                        raise ValueError(f"Image input exceeds {MAX_IMAGE_BYTES} bytes")
                    img = Image.open(BytesIO(raw))
                elif os.path.exists(src):
                    if os.path.getsize(src) > MAX_IMAGE_BYTES:
                        raise ValueError(f"Image input exceeds {MAX_IMAGE_BYTES} bytes")
                    img = Image.open(src)
                else:
                    return ImageResult(
                        error=f"File not found: {src}",
                        duration_ms=(time.time() - t0) * 1000,
                    )
            else:
                return ImageResult(
                    error=f"Unknown input type: {type(src).__name__}",
                    duration_ms=(time.time() - t0) * 1000,
                )

            width, height = img.size
            if width * height > MAX_IMAGE_PIXELS:
                raise ValueError(
                    f"Image dimensions exceed the {MAX_IMAGE_PIXELS}-pixel limit"
                )
            img.load()
            return wrap_result(img, t0, ["load"])

        except Exception as e:
            LOGS.error(f"[HIP:PIPELINE] load error: {e}")
            return ImageResult(
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )

    async def load_url(self, url: str) -> ImageResult:
        """Download an image from a URL and load it."""
        t0 = time.time()
        try:
            import aiohttp

            async with aiohttp.ClientSession() as sess:
                async with sess.get(
                    url, timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status != 200:
                        return ImageResult(
                            error=f"HTTP {resp.status} from {url}",
                            duration_ms=(time.time() - t0) * 1000,
                        )
                    if (resp.content_length or 0) > MAX_IMAGE_BYTES:
                        return ImageResult(
                            error=f"Image input exceeds {MAX_IMAGE_BYTES} bytes",
                            duration_ms=(time.time() - t0) * 1000,
                        )
                    data = await resp.content.read(MAX_IMAGE_BYTES + 1)
                    if len(data) > MAX_IMAGE_BYTES:
                        return ImageResult(
                            error=f"Image input exceeds {MAX_IMAGE_BYTES} bytes",
                            duration_ms=(time.time() - t0) * 1000,
                        )
            result = self.load(data)
            result.op_chain = ["load_url", url[:50]]
            return result
        except Exception as e:
            return ImageResult(
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )

    async def load_tg(self, event_or_msg) -> ImageResult:
        """
        Download and load an image from a Telegram message/event.
        Handles: photo, document (image), sticker (webp/tgs), reply.
        """
        t0 = time.time()
        try:
            msg = event_or_msg
            # If it's an event with reply
            if hasattr(msg, "get_reply_message"):
                reply = await msg.get_reply_message()
                if reply and reply.media:
                    msg = reply

            if not (hasattr(msg, "media") and msg.media):
                return ImageResult(
                    error="No media in message",
                    duration_ms=(time.time() - t0) * 1000,
                )

            file_info = getattr(msg, "file", None)
            file_size = getattr(file_info, "size", 0) or 0
            if file_size > MAX_IMAGE_BYTES:
                return ImageResult(
                    error=f"Image input exceeds {MAX_IMAGE_BYTES} bytes",
                    duration_ms=(time.time() - t0) * 1000,
                )

            path = await msg.download_media()
            if not path:
                return ImageResult(
                    error="Download failed",
                    duration_ms=(time.time() - t0) * 1000,
                )

            result = self.load(path)
            result.op_chain = ["load_tg"]

            # Cleanup temp file
            try:
                os.unlink(path)
            except Exception:
                pass

            return result
        except Exception as e:
            LOGS.error(f"[HIP:PIPELINE] load_tg error: {e}")
            return ImageResult(
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )

    # ── Format conversion ──────────────────────────────────────────────────

    def convert_format(self, result: ImageResult, fmt: str) -> ImageResult:
        """Convert an ImageResult to a different format."""
        if not result.ok or result.image is None:
            return result
        t0 = time.time()
        try:
            Image, *_ = self._pil()
            img = result.image
            fmt = fmt.upper()
            if fmt == "JPEG" and img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, format=fmt)
            buf.seek(0)
            new_img = Image.open(buf)
            new_img.load()
            new_result = wrap_result(new_img, t0, result.op_chain + [f"convert→{fmt}"])
            new_result.format = fmt
            return new_result
        except Exception as e:
            return ImageResult(
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
                op_chain=result.op_chain,
            )

    def convert_mode(self, result: ImageResult, mode: str) -> ImageResult:
        """Convert image colour mode (RGB, RGBA, L, etc.)."""
        if not result.ok or result.image is None:
            return result
        t0 = time.time()
        try:
            new_img = result.image.convert(mode)
            return wrap_result(new_img, t0, result.op_chain + [f"mode→{mode}"])
        except Exception as e:
            return ImageResult(
                error=str(e),
                duration_ms=(time.time() - t0) * 1000,
            )

    # ── Resize / crop ──────────────────────────────────────────────────────

    def resize(
        self,
        result: ImageResult,
        width: int,
        height: int,
        keep_aspect: bool = True,
    ) -> ImageResult:
        if not result.ok or result.image is None:
            return result
        t0 = time.time()
        try:
            Image, _, _, _, ImageOps, _ = self._pil()
            img = result.image
            if keep_aspect:
                img.thumbnail((width, height), Image.LANCZOS)
                new_img = img
            else:
                new_img = img.resize((width, height), Image.LANCZOS)
            return wrap_result(
                new_img, t0, result.op_chain + [f"resize→{width}x{height}"]
            )
        except Exception as e:
            return ImageResult(error=str(e), duration_ms=(time.time() - t0) * 1000)

    def crop(
        self,
        result: ImageResult,
        left: int,
        top: int,
        right: int,
        bottom: int,
    ) -> ImageResult:
        if not result.ok or result.image is None:
            return result
        t0 = time.time()
        try:
            new_img = result.image.crop((left, top, right, bottom))
            return wrap_result(
                new_img, t0, result.op_chain + [f"crop→{left},{top},{right},{bottom}"]
            )
        except Exception as e:
            return ImageResult(error=str(e), duration_ms=(time.time() - t0) * 1000)

    def pad_to_square(
        self, result: ImageResult, color: tuple = (0, 0, 0)
    ) -> ImageResult:
        """Pad image to square with given background color."""
        if not result.ok or result.image is None:
            return result
        t0 = time.time()
        try:
            Image, ImageDraw, *_ = self._pil()
            img = result.image
            size = max(img.width, img.height)
            new = Image.new(img.mode, (size, size), color)
            offset = ((size - img.width) // 2, (size - img.height) // 2)
            new.paste(img, offset)
            return wrap_result(new, t0, result.op_chain + ["pad_square"])
        except Exception as e:
            return ImageResult(error=str(e), duration_ms=(time.time() - t0) * 1000)

    # ── Internal ───────────────────────────────────────────────────────────


# ── Shared result helpers ───────────────────────────────────────────────────
# Stateless, so every class in the package uses these directly instead of
# reaching into an ImagePipeline instance.


def wrap_result(img, t0: float, ops: list[str]) -> ImageResult:
    """Wrap a PIL Image into an ImageResult."""
    return ImageResult(
        image=img,
        format=img.format or "PNG",
        width=img.width,
        height=img.height,
        mode=img.mode,
        duration_ms=(time.time() - t0) * 1000,
        op_chain=ops,
    )


def to_cv2(img):
    """Convert a PIL Image to an OpenCV numpy array."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    return cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)


def from_cv2(arr, t0: float, ops: list[str]) -> ImageResult:
    """Convert an OpenCV numpy array back to an ImageResult."""
    Image, *_ = ImagePipeline._pil()
    try:
        import cv2
    except ImportError:
        return ImageResult(
            error="cv2 not available", duration_ms=(time.time() - t0) * 1000
        )
    return wrap_result(Image.fromarray(cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)), t0, ops)

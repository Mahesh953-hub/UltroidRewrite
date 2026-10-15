# Ultroid - UserBot Rewrite
# pyUltroid/imaging/_processor.py
#
# ImageProcessor  -  every transformation, filter, effect, and aesthetic op.
#
# Organised by layer:
#   Basic       -  rotate, flip, mirror, grayscale, invert, brightness, contrast
#   Filters     -  blur, sharpen, edge detect, emboss, sketch, cartoon/toon
#   Color       -  colorize, channel shift, danger, blue-channel, negative
#   Aesthetic   -  border, vignette, glitch, pixelate, noise, vintage
#   Text        -  caption, watermark, multi-line text with custom fonts
#   Composite   -  collage, overlay, alpha blend, border frame
#   Advanced    -  face detection (cv2), background removal, depth-of-field
#
# Every method takes an ImageResult and returns an ImageResult.
# Chain calls: result = imaging.blur(imaging.grayscale(result))
# AI agent chains operations to express complex image edits.

from __future__ import annotations

import io
import os
import random
import time
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Tuple, Union

from pyUltroid.startup.logger import LOGS
from ._pipeline import ImageResult, from_cv2, to_cv2, wrap_result

# Font directory (reuse existing from Ultroid)
FONTS_DIR = Path(__file__).parent.parent.parent / "utils" / "fonts"
FALLBACK_FONT = None  # set lazily


def _get_font(size: int = 36, bold: bool = False):
    """Load a font  -  falls back to PIL default if none found."""
    try:
        from PIL import ImageFont

        candidates = []
        if FONTS_DIR.exists():
            candidates += list(FONTS_DIR.glob("*.ttf")) + list(FONTS_DIR.glob("*.otf"))
        # System fonts
        for sys_path in ["/usr/share/fonts", "/usr/local/share/fonts"]:
            p = Path(sys_path)
            if p.exists():
                candidates += list(p.rglob("*.ttf"))[:5]

        if candidates:
            return ImageFont.truetype(str(candidates[0]), size)
        return ImageFont.load_default()
    except Exception:
        try:
            from PIL import ImageFont

            return ImageFont.load_default()
        except Exception:
            return None


class ImageProcessor:
    """
    All image transformation operations.
    Each method: ImageResult → ImageResult.
    Never raises  -  errors captured in result.error.
    """

    def _pil(self):
        from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageEnhance

        return Image, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageEnhance

    def _cv2(self):
        try:
            import cv2, numpy as np

            return cv2, np
        except ImportError:
            return None, None

    def _guard(self, result: ImageResult, op: str) -> ImageResult | None:
        if not result.ok or result.image is None:
            return ImageResult(
                error=f"Cannot apply {op}: input is invalid ({result.error})",
            )
        return None

    # ── Basic operations ───────────────────────────────────────────────────

    def rotate(
        self, result: ImageResult, degrees: float, expand: bool = True
    ) -> ImageResult:
        guard = self._guard(result, "rotate")
        if guard:
            return guard
        t0 = time.time()
        try:
            new_img = result.image.rotate(degrees, expand=expand)
            return wrap_result(new_img, t0, result.op_chain + [f"rotate({degrees})"])
        except Exception as e:
            return ImageResult(error=str(e))

    def flip(
        self, result: ImageResult, horizontal: bool = False, vertical: bool = False
    ) -> ImageResult:
        guard = self._guard(result, "flip")
        if guard:
            return guard
        t0 = time.time()
        try:
            _, _, _, _, ImageOps, _ = self._pil()
            img = result.image
            if horizontal:
                img = ImageOps.mirror(img)
            if vertical:
                img = ImageOps.flip(img)
            ops = []
            if horizontal:
                ops.append("mirror")
            if vertical:
                ops.append("flip")
            return wrap_result(img, t0, result.op_chain + ops)
        except Exception as e:
            return ImageResult(error=str(e))

    def grayscale(self, result: ImageResult) -> ImageResult:
        guard = self._guard(result, "grayscale")
        if guard:
            return guard
        t0 = time.time()
        try:
            _, _, _, _, ImageOps, _ = self._pil()
            new_img = ImageOps.grayscale(result.image).convert("RGB")
            return wrap_result(new_img, t0, result.op_chain + ["grayscale"])
        except Exception as e:
            return ImageResult(error=str(e))

    def invert(self, result: ImageResult) -> ImageResult:
        guard = self._guard(result, "invert")
        if guard:
            return guard
        t0 = time.time()
        try:
            _, _, _, _, ImageOps, _ = self._pil()
            img = result.image
            if img.mode == "RGBA":
                r, g, b, a = img.split()
                rgb = ImageOps.invert(ImageOps.merge("RGB", (r, g, b)))
                new_img = ImageOps.merge("RGBA", (*rgb.split(), a))
            else:
                new_img = ImageOps.invert(img.convert("RGB"))
            return wrap_result(new_img, t0, result.op_chain + ["invert"])
        except Exception as e:
            return ImageResult(error=str(e))

    def brightness(self, result: ImageResult, factor: float) -> ImageResult:
        """factor > 1 = brighter, < 1 = darker"""
        guard = self._guard(result, "brightness")
        if guard:
            return guard
        t0 = time.time()
        try:
            _, _, _, _, _, ImageEnhance = self._pil()
            new_img = ImageEnhance.Brightness(result.image).enhance(factor)
            return wrap_result(
                new_img, t0, result.op_chain + [f"brightness({factor})"]
            )
        except Exception as e:
            return ImageResult(error=str(e))

    def contrast(self, result: ImageResult, factor: float) -> ImageResult:
        guard = self._guard(result, "contrast")
        if guard:
            return guard
        t0 = time.time()
        try:
            _, _, _, _, _, ImageEnhance = self._pil()
            new_img = ImageEnhance.Contrast(result.image).enhance(factor)
            return wrap_result(
                new_img, t0, result.op_chain + [f"contrast({factor})"]
            )
        except Exception as e:
            return ImageResult(error=str(e))

    def saturation(self, result: ImageResult, factor: float) -> ImageResult:
        guard = self._guard(result, "saturation")
        if guard:
            return guard
        t0 = time.time()
        try:
            _, _, _, _, _, ImageEnhance = self._pil()
            new_img = ImageEnhance.Color(result.image).enhance(factor)
            return wrap_result(
                new_img, t0, result.op_chain + [f"saturation({factor})"]
            )
        except Exception as e:
            return ImageResult(error=str(e))

    # ── Filters ────────────────────────────────────────────────────────────

    def blur(self, result: ImageResult, radius: float = 5.0) -> ImageResult:
        guard = self._guard(result, "blur")
        if guard:
            return guard
        t0 = time.time()
        try:
            _, _, ImageFilter, *_ = self._pil()
            new_img = result.image.filter(ImageFilter.GaussianBlur(radius=radius))
            return wrap_result(new_img, t0, result.op_chain + [f"blur({radius})"])
        except Exception as e:
            return ImageResult(error=str(e))

    def sharpen(self, result: ImageResult, factor: float = 2.0) -> ImageResult:
        guard = self._guard(result, "sharpen")
        if guard:
            return guard
        t0 = time.time()
        try:
            _, _, ImageFilter, _, _, ImageEnhance = self._pil()
            new_img = ImageEnhance.Sharpness(result.image).enhance(factor)
            return wrap_result(new_img, t0, result.op_chain + [f"sharpen({factor})"])
        except Exception as e:
            return ImageResult(error=str(e))

    def sketch(self, result: ImageResult) -> ImageResult:
        """Convert to pencil sketch via OpenCV."""
        guard = self._guard(result, "sketch")
        if guard:
            return guard
        t0 = time.time()
        cv2, np = self._cv2()
        if cv2 is None:
            # PIL fallback  -  edge enhance + grayscale
            try:
                _, _, ImageFilter, _, ImageOps, _ = self._pil()
                img = ImageOps.grayscale(result.image)
                img = img.filter(ImageFilter.EDGE_ENHANCE_MORE)
                return wrap_result(
                    img.convert("RGB"), t0, result.op_chain + ["sketch(pil)"]
                )
            except Exception as e:
                return ImageResult(error=str(e))
        try:
            arr = to_cv2(result.image)
            gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
            inv = cv2.bitwise_not(gray)
            blurred = cv2.GaussianBlur(inv, (21, 21), 0)
            inv_blur = cv2.bitwise_not(blurred)
            sketch = cv2.divide(gray, inv_blur, scale=256.0)
            sketch_bgr = cv2.cvtColor(sketch, cv2.COLOR_GRAY2BGR)
            return from_cv2(sketch_bgr, t0, result.op_chain + ["sketch"])
        except Exception as e:
            return ImageResult(error=str(e))

    def cartoon(self, result: ImageResult) -> ImageResult:
        """Cartoon / toon effect via OpenCV bilateral filter."""
        guard = self._guard(result, "cartoon")
        if guard:
            return guard
        t0 = time.time()
        cv2, np = self._cv2()
        if cv2 is None:
            return self._cartoon_pil(result, t0)
        try:
            arr = to_cv2(result.image)
            gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
            gray = cv2.medianBlur(gray, 5)
            edges = cv2.adaptiveThreshold(
                gray,
                255,
                cv2.ADAPTIVE_THRESH_MEAN_C,
                cv2.THRESH_BINARY,
                9,
                9,
            )
            color = cv2.bilateralFilter(arr, 9, 300, 300)
            edges_c = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)
            cartoon = cv2.bitwise_and(color, edges_c)
            return from_cv2(cartoon, t0, result.op_chain + ["cartoon"])
        except Exception as e:
            return ImageResult(error=str(e))

    def _cartoon_pil(self, result: ImageResult, t0: float) -> ImageResult:
        try:
            _, _, ImageFilter, _, _, ImageEnhance = self._pil()
            img = result.image.filter(ImageFilter.SMOOTH_MORE)
            img = ImageEnhance.Color(img).enhance(2.0)
            img = img.filter(ImageFilter.EDGE_ENHANCE)
            return wrap_result(img, t0, result.op_chain + ["cartoon(pil)"])
        except Exception as e:
            return ImageResult(error=str(e))

    def emboss(self, result: ImageResult) -> ImageResult:
        guard = self._guard(result, "emboss")
        if guard:
            return guard
        t0 = time.time()
        try:
            _, _, ImageFilter, *_ = self._pil()
            new_img = result.image.filter(ImageFilter.EMBOSS)
            return wrap_result(new_img, t0, result.op_chain + ["emboss"])
        except Exception as e:
            return ImageResult(error=str(e))

    def edge_detect(self, result: ImageResult) -> ImageResult:
        guard = self._guard(result, "edge_detect")
        if guard:
            return guard
        t0 = time.time()
        try:
            _, _, ImageFilter, *_ = self._pil()
            new_img = (
                result.image.convert("L").filter(ImageFilter.FIND_EDGES).convert("RGB")
            )
            return wrap_result(new_img, t0, result.op_chain + ["edge_detect"])
        except Exception as e:
            return ImageResult(error=str(e))

    # ── Color effects ──────────────────────────────────────────────────────

    def danger(self, result: ImageResult) -> ImageResult:
        """Red channel boost  -  dramatic danger look."""
        guard = self._guard(result, "danger")
        if guard:
            return guard
        t0 = time.time()
        try:
            Image, *_ = self._pil()
            img = result.image.convert("RGB")
            r, g, b = img.split()
            # Boost red, suppress others
            img = Image.merge(
                "RGB", (r, g.point(lambda x: x // 2), b.point(lambda x: x // 2))
            )
            return wrap_result(img, t0, result.op_chain + ["danger"])
        except Exception as e:
            return ImageResult(error=str(e))

    def blue_channel(self, result: ImageResult) -> ImageResult:
        """Blue channel boost  -  cool cinematic look."""
        guard = self._guard(result, "blue_channel")
        if guard:
            return guard
        t0 = time.time()
        try:
            Image, *_ = self._pil()
            img = result.image.convert("RGB")
            r, g, b = img.split()
            img = Image.merge(
                "RGB", (r.point(lambda x: x // 2), g.point(lambda x: x // 2), b)
            )
            return wrap_result(img, t0, result.op_chain + ["blue_channel"])
        except Exception as e:
            return ImageResult(error=str(e))

    def vintage(self, result: ImageResult) -> ImageResult:
        """Vintage / sepia effect."""
        guard = self._guard(result, "vintage")
        if guard:
            return guard
        t0 = time.time()
        try:
            Image, *_ = self._pil()
            img = result.image.convert("RGB")
            r, g, b = img.split()
            r = r.point(lambda x: min(255, int(x * 1.1 + 20)))
            g = g.point(lambda x: min(255, int(x * 0.9 + 10)))
            b = b.point(lambda x: min(255, int(x * 0.7)))
            img = Image.merge("RGB", (r, g, b))
            return wrap_result(img, t0, result.op_chain + ["vintage"])
        except Exception as e:
            return ImageResult(error=str(e))

    # ── Aesthetic effects ──────────────────────────────────────────────────

    def border(
        self,
        result: ImageResult,
        color: str | tuple[int, int, int] = (255, 255, 255),
        width: int = 20,
    ) -> ImageResult:
        guard = self._guard(result, "border")
        if guard:
            return guard
        t0 = time.time()
        try:
            Image, ImageDraw, *_ = self._pil()
            img = result.image.convert("RGB")
            w, h = img.width + width * 2, img.height + width * 2
            new = Image.new("RGB", (w, h), color)
            new.paste(img, (width, width))
            return wrap_result(
                new, t0, result.op_chain + [f"border({color},{width})"]
            )
        except Exception as e:
            return ImageResult(error=str(e))

    def pixelate(self, result: ImageResult, pixel_size: int = 20) -> ImageResult:
        guard = self._guard(result, "pixelate")
        if guard:
            return guard
        t0 = time.time()
        try:
            Image, *_ = self._pil()
            img = result.image.convert("RGB")
            small = img.resize(
                (max(1, img.width // pixel_size), max(1, img.height // pixel_size)),
                Image.NEAREST,
            )
            new_img = small.resize(img.size, Image.NEAREST)
            return wrap_result(
                new_img, t0, result.op_chain + [f"pixelate({pixel_size})"]
            )
        except Exception as e:
            return ImageResult(error=str(e))

    def vignette(self, result: ImageResult, strength: float = 0.5) -> ImageResult:
        """Dark rounded vignette frame."""
        guard = self._guard(result, "vignette")
        if guard:
            return guard
        t0 = time.time()
        try:
            Image, ImageDraw, ImageFilter, *_ = self._pil()
            img = result.image.convert("RGBA")
            w, h = img.size
            mask = Image.new("L", (w, h), 255)
            draw = ImageDraw.Draw(mask)
            for i in range(min(w, h) // 3):
                alpha = int(255 * (1 - (i / (min(w, h) // 3))) * strength)
                draw.ellipse(
                    [i * 2, i * 2, w - i * 2, h - i * 2],
                    outline=alpha,
                )
            mask = mask.filter(ImageFilter.GaussianBlur(radius=min(w, h) // 6))
            dark = Image.new("RGBA", (w, h), (0, 0, 0, int(255 * strength)))
            dark.putalpha(ImageFilter.GaussianBlur)
            result_img = Image.composite(img, dark, mask).convert("RGB")
            return wrap_result(result_img, t0, result.op_chain + ["vignette"])
        except Exception as e:
            return ImageResult(error=str(e))

    def glitch(self, result: ImageResult, intensity: int = 10) -> ImageResult:
        """RGB channel shift glitch effect."""
        guard = self._guard(result, "glitch")
        if guard:
            return guard
        t0 = time.time()
        try:
            Image, *_ = self._pil()
            img = result.image.convert("RGB")
            r, g, b = img.split()
            shift = intensity
            r = r.transform(r.size, Image.AFFINE, (1, 0, shift, 0, 1, 0))
            b = b.transform(b.size, Image.AFFINE, (1, 0, -shift, 0, 1, 0))
            glitched = Image.merge("RGB", (r, g, b))
            return wrap_result(
                glitched, t0, result.op_chain + [f"glitch({intensity})"]
            )
        except Exception as e:
            return ImageResult(error=str(e))

    # ── Text / caption ─────────────────────────────────────────────────────

    def caption(
        self,
        result: ImageResult,
        text: str,
        position: str = "bottom",  # "top" | "bottom" | "center"
        font_size: int = 36,
        color: str | tuple = "white",
        bg_color: str | tuple | None = (0, 0, 0, 160),
        padding: int = 16,
    ) -> ImageResult:
        """Add a text caption to an image with optional semi-transparent background."""
        guard = self._guard(result, "caption")
        if guard:
            return guard
        t0 = time.time()
        try:
            Image, ImageDraw, _, _, _, _ = self._pil()
            img = result.image.convert("RGBA")
            w, h = img.size
            font = _get_font(font_size)
            draw = ImageDraw.Draw(img)

            # Measure text
            try:
                bbox = draw.textbbox((0, 0), text, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
            except AttributeError:
                tw, th = draw.textsize(text, font=font)

            # Position
            x = (w - tw) // 2
            if position == "bottom":
                y = h - th - padding * 2
            elif position == "top":
                y = padding
            else:
                y = (h - th) // 2

            # Background bar
            if bg_color:
                overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
                od = ImageDraw.Draw(overlay)
                od.rectangle(
                    [0, y - padding, w, y + th + padding],
                    fill=bg_color,
                )
                img = Image.alpha_composite(img, overlay)

            # Draw text
            final_draw = ImageDraw.Draw(img)
            final_draw.text((x, y), text, font=font, fill=color)
            return wrap_result(
                img.convert("RGB"), t0, result.op_chain + [f"caption({text[:20]!r})"]
            )
        except Exception as e:
            return ImageResult(error=str(e))

    def watermark(
        self,
        result: ImageResult,
        text: str,
        opacity: float = 0.3,
        font_size: int = 24,
        color: str = "white",
    ) -> ImageResult:
        """Diagonal tiled watermark."""
        guard = self._guard(result, "watermark")
        if guard:
            return guard
        t0 = time.time()
        try:
            Image, ImageDraw, *_ = self._pil()
            img = result.image.convert("RGBA")
            w, h = img.size
            wm = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(wm)
            font = _get_font(font_size)
            alpha = int(255 * opacity)

            for y in range(0, h, 80):
                for x in range(0, w, 200):
                    draw.text(
                        (x, y), text, font=font, fill=(*_parse_color(color), alpha)
                    )

            wm = wm.rotate(30, expand=False)
            out = Image.alpha_composite(img, wm)
            return wrap_result(
                out.convert("RGB"), t0, result.op_chain + ["watermark"]
            )
        except Exception as e:
            return ImageResult(error=str(e))

    # ── Color sample ───────────────────────────────────────────────────────

    def color_sample(
        self, color: str | tuple, size: tuple[int, int] = (300, 150)
    ) -> ImageResult:
        """Generate a solid color swatch image."""
        t0 = time.time()
        try:
            Image, ImageDraw, *_ = self._pil()
            img = Image.new("RGB", size, color)
            draw = ImageDraw.Draw(img)
            font = _get_font(20)
            # Overlay the color value as text
            label = color if isinstance(color, str) else f"rgb{color}"
            luma = sum(_parse_color(color)) / 3 if not isinstance(color, str) else 128
            text_color = "black" if luma > 128 else "white"
            draw.text((10, size[1] // 2 - 10), label, font=font, fill=text_color)
            return wrap_result(img, t0, ["color_sample"])
        except Exception as e:
            return ImageResult(error=str(e))

    # ── Collage ────────────────────────────────────────────────────────────

    def collage(
        self,
        results: list[ImageResult],
        cols: int = 2,
        padding: int = 10,
        bg_color: tuple = (30, 30, 30),
    ) -> ImageResult:
        """Arrange multiple images into a grid collage."""
        t0 = time.time()
        try:
            Image, *_ = self._pil()
            images = [r.image for r in results if r.ok and r.image]
            if not images:
                return ImageResult(error="No valid images for collage")

            # Normalise sizes
            cell_w = max(img.width for img in images)
            cell_h = max(img.height for img in images)
            rows = (len(images) + cols - 1) // cols
            total_w = cols * cell_w + (cols + 1) * padding
            total_h = rows * cell_h + (rows + 1) * padding
            canvas = Image.new("RGB", (total_w, total_h), bg_color)

            for i, img in enumerate(images):
                row = i // cols
                col = i % cols
                x = padding + col * (cell_w + padding)
                y = padding + row * (cell_h + padding)
                # Centre smaller images in cell
                ox = x + (cell_w - img.width) // 2
                oy = y + (cell_h - img.height) // 2
                canvas.paste(img.convert("RGB"), (ox, oy))

            return wrap_result(canvas, t0, ["collage"])
        except Exception as e:
            return ImageResult(error=str(e))


# ── Helpers ────────────────────────────────────────────────────────────────


def _parse_color(color) -> tuple[int, int, int]:
    if isinstance(color, tuple):
        return color[:3]
    if isinstance(color, str):
        try:
            from PIL import ImageColor

            return ImageColor.getrgb(color)[:3]
        except Exception:
            return (128, 128, 128)
    return (128, 128, 128)




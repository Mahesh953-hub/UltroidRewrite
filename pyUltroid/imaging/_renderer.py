# Ultroid - UserBot Rewrite
# pyUltroid/imaging/_renderer.py
#
# ImageRenderer  -  generates images from non-image inputs.
#
# What it renders:
#   error_card()     -  beautiful formatted error/traceback image
#   code_image()     -  syntax-highlighted code block as image
#   text_image()     -  styled text → image (like a quote card)
#   meme()           -  top/bottom text on a template
#   color_card()     -  color swatch with hex/rgb info
#   stat_card()      -  metric display card (for bot stats, trace info)
#   ai_canvas()      -  blank canvas for AI to paint on via instructions
#
# The AI agent uses these to "express"  -  formatting its output visually
# instead of plain text when the context calls for it.

from __future__ import annotations

import textwrap
import time
import traceback
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from pyUltroid.startup.logger import LOGS
from ._pipeline import ImageResult, wrap_result
from ._processor import _get_font, _parse_color

# Colour palette for cards
PALETTE = {
    "bg_dark": (18, 18, 24),
    "bg_card": (28, 28, 38),
    "border": (80, 80, 120),
    "accent": (120, 80, 220),
    "error": (220, 60, 60),
    "success": (60, 200, 80),
    "warning": (220, 160, 40),
    "text": (230, 230, 240),
    "subtext": (150, 150, 170),
    "code_bg": (22, 26, 38),
    "code_text": (200, 210, 240),
}


class ImageRenderer:
    """
    Renders structured data and text into images.
    All methods return ImageResult  -  never raise.
    """

    def _pil(self):
        from PIL import Image, ImageDraw, ImageFont, ImageFilter

        return Image, ImageDraw, ImageFont, ImageFilter

    # ── Error card ─────────────────────────────────────────────────────────

    def error_card(
        self,
        title: str,
        message: str,
        tb: str | None = None,
        plugin: str | None = None,
        request_id: str | None = None,
        file_name: str = "traceback.py",
    ) -> ImageResult:
        """Render a portrait macOS/Carbon terminal card for a traceback.

        The previous implementation was a landscape red-banner card that
        truncated every line to 110 characters and drew HTML entities
        literally. This delegates to :mod:`._errorcard`, which unescapes the
        text and grows vertically so nothing is cut off.
        """
        t0 = time.time()
        try:
            from ._errorcard import render_error_card

            img, shown, total, _ = render_error_card(
                title=title,
                message=message,
                traceback_text=tb or "",
                plugin=plugin or "",
                request_id=request_id or "",
                file_name=file_name,
            )
            return wrap_result(img, t0, ["error_card"])
        except Exception as e:
            LOGS.error(f"[HIP:RENDERER] error_card failed: {e}")
            return ImageResult(error=str(e))

    # ── Code image ─────────────────────────────────────────────────────────

    def code_image(
        self,
        code: str,
        language: str = "",
        title: str = "",
        width: int = 900,
    ) -> ImageResult:
        """
        Render a code block as a styled image.
        Syntax highlighting via pygments if available, plain otherwise.
        """
        t0 = time.time()
        try:
            Image, ImageDraw, _, _ = self._pil()

            lines = code.expandtabs(4).splitlines()[:60]
            PADDING = 24
            LINE_H = 20
            HEADER_H = 48 if title else 12
            height = HEADER_H + PADDING + len(lines) * LINE_H + PADDING

            img = Image.new("RGB", (width, height), PALETTE["code_bg"])
            draw = ImageDraw.Draw(img)

            # Window chrome dots
            dot_y = 18
            for i, col in enumerate([(220, 80, 80), (220, 180, 60), (80, 200, 80)]):
                draw.ellipse(
                    [PADDING + i * 22, dot_y - 6, PADDING + i * 22 + 12, dot_y + 6],
                    fill=col,
                )

            if title:
                draw.text(
                    (width // 2 - len(title) * 5, 8),
                    title,
                    font=_get_font(16),
                    fill=PALETTE["subtext"],
                )

            # Try syntax highlight
            highlighted = self._highlight_code(code, language)

            code_font = _get_font(14)
            y = HEADER_H + PADDING

            if highlighted:
                for line_text, line_color in highlighted[:60]:
                    draw.text(
                        (PADDING, y), line_text[:120], font=code_font, fill=line_color
                    )
                    y += LINE_H
            else:
                for line in lines:
                    draw.text(
                        (PADDING, y),
                        line[:120],
                        font=code_font,
                        fill=PALETTE["code_text"],
                    )
                    y += LINE_H

            # Line numbers
            for i in range(len(lines)):
                draw.text(
                    (4, HEADER_H + PADDING + i * LINE_H),
                    f"{i + 1:3}",
                    font=_get_font(12),
                    fill=PALETTE["subtext"],
                )

            draw.rectangle(
                [0, 0, width - 1, height - 1], outline=PALETTE["border"], width=1
            )

            return wrap_result(img, t0, ["code_image"])
        except Exception as e:
            return ImageResult(error=str(e))

    def _highlight_code(
        self, code: str, language: str
    ) -> list[tuple[str, tuple]] | None:
        """Simple keyword highlighter  -  no external dep needed."""
        KEYWORDS = {
            "python": (
                {
                    "def",
                    "class",
                    "async",
                    "await",
                    "return",
                    "import",
                    "from",
                    "if",
                    "else",
                    "elif",
                    "for",
                    "while",
                    "try",
                    "except",
                    "with",
                    "as",
                    "pass",
                    "raise",
                    "yield",
                    "lambda",
                    "not",
                    "and",
                    "or",
                    "in",
                    "is",
                    "None",
                    "True",
                    "False",
                },
                (120, 160, 255),  # keyword colour
            ),
        }
        lang = language.lower()
        if lang not in KEYWORDS:
            return None

        kws, kw_color = KEYWORDS[lang]
        result = []
        for line in code.splitlines()[:60]:
            # Simple: colour the whole line if it starts with a keyword
            stripped = line.lstrip()
            word = stripped.split()[0] if stripped.split() else ""
            if stripped.startswith("#"):
                color = (100, 160, 100)  # comment
            elif stripped.startswith(("'", '"')):
                color = (200, 160, 80)  # string
            elif word in kws:
                color = kw_color
            else:
                color = PALETTE["code_text"]
            result.append((line, color))
        return result

    # ── Text / quote card ──────────────────────────────────────────────────

    def text_image(
        self,
        text: str,
        author: str = "",
        bg_color: tuple = PALETTE["bg_card"],
        text_color: tuple = PALETTE["text"],
        accent: tuple = PALETTE["accent"],
        width: int = 700,
        font_size: int = 28,
    ) -> ImageResult:
        """Styled quote / text card."""
        t0 = time.time()
        try:
            Image, ImageDraw, *_ = self._pil()

            PADDING = 40
            LINE_H = font_size + 8
            wrapped = textwrap.wrap(text, width=40)
            auth_h = 40 if author else 0
            height = PADDING * 2 + len(wrapped) * LINE_H + auth_h + 20

            img = Image.new("RGB", (width, height), bg_color)
            draw = ImageDraw.Draw(img)

            # Accent left bar
            draw.rectangle([0, 0, 6, height], fill=accent)

            font = _get_font(font_size)
            y = PADDING
            for line in wrapped:
                draw.text((PADDING + 16, y), line, font=font, fill=text_color)
                y += LINE_H

            if author:
                draw.text(
                    (PADDING + 16, y + 10),
                    f" -  {author}",
                    font=_get_font(18),
                    fill=PALETTE["subtext"],
                )

            return wrap_result(img, t0, ["text_image"])
        except Exception as e:
            return ImageResult(error=str(e))

    # ── Meme builder ───────────────────────────────────────────────────────

    def meme(
        self,
        base: ImageResult,
        top_text: str = "",
        bottom_text: str = "",
        font_size: int = 48,
        outline: bool = True,
    ) -> ImageResult:
        """Classic meme: image + top/bottom impact text."""
        if not base.ok or base.image is None:
            return ImageResult(error="Base image required for meme")
        t0 = time.time()
        try:
            Image, ImageDraw, *_ = self._pil()
            img = base.image.convert("RGB").copy()
            draw = ImageDraw.Draw(img)
            w, h = img.size
            font = _get_font(font_size, bold=True)

            def draw_text_outlined(text, y, anchor="top"):
                text = text.upper()
                try:
                    bbox = draw.textbbox((0, 0), text, font=font)
                    tw = bbox[2] - bbox[0]
                except AttributeError:
                    tw, _ = draw.textsize(text, font=font)
                x = (w - tw) // 2
                if outline:
                    for dx in (-2, 0, 2):
                        for dy in (-2, 0, 2):
                            draw.text((x + dx, y + dy), text, font=font, fill="black")
                draw.text((x, y), text, font=font, fill="white")

            MARGIN = 20
            if top_text:
                draw_text_outlined(top_text, MARGIN)
            if bottom_text:
                try:
                    bbox = draw.textbbox((0, 0), bottom_text.upper(), font=font)
                    th = bbox[3] - bbox[1]
                except AttributeError:
                    _, th = draw.textsize(bottom_text.upper(), font=font)
                draw_text_outlined(bottom_text, h - th - MARGIN)

            return wrap_result(img, t0, base.op_chain + ["meme"])
        except Exception as e:
            return ImageResult(error=str(e))

    # ── Stat card ──────────────────────────────────────────────────────────

    def stat_card(
        self,
        title: str,
        metrics: dict[str, Any],
        color: tuple = PALETTE["accent"],
        width: int = 600,
    ) -> ImageResult:
        """
        Generate a metrics display card.
        Used by .tstats, trace reports, HRE audit output.
        """
        t0 = time.time()
        try:
            Image, ImageDraw, *_ = self._pil()

            PADDING = 28
            ROW_H = 32
            height = 70 + len(metrics) * ROW_H + PADDING * 2

            img = Image.new("RGB", (width, height), PALETTE["bg_card"])
            draw = ImageDraw.Draw(img)

            # Header
            draw.rectangle([0, 0, width, 60], fill=color)
            draw.text(
                (PADDING, 16),
                title,
                font=_get_font(22, bold=True),
                fill="white",
            )

            # Metrics rows
            y = 70 + PADDING
            for i, (key, val) in enumerate(metrics.items()):
                bg = PALETTE["bg_dark"] if i % 2 == 0 else PALETTE["bg_card"]
                draw.rectangle([0, y - 4, width, y + ROW_H - 4], fill=bg)
                draw.text(
                    (PADDING, y), str(key), font=_get_font(16), fill=PALETTE["subtext"]
                )
                val_str = str(val)
                try:
                    bbox = draw.textbbox((0, 0), val_str, font=_get_font(16))
                    vw = bbox[2] - bbox[0]
                except AttributeError:
                    vw, _ = draw.textsize(val_str, font=_get_font(16))
                draw.text(
                    (width - PADDING - vw, y),
                    val_str,
                    font=_get_font(16, bold=True),
                    fill=PALETTE["text"],
                )
                y += ROW_H

            draw.rectangle([0, 0, width - 1, height - 1], outline=color, width=2)

            return wrap_result(img, t0, ["stat_card"])
        except Exception as e:
            return ImageResult(error=str(e))

    # ── AI canvas ─────────────────────────────────────────────────────────

    def ai_canvas(
        self,
        width: int = 800,
        height: int = 600,
        bg_color: tuple = (10, 10, 20),
    ) -> ImageResult:
        """
        Blank canvas for the AI agent to paint on.
        Returns an ImageResult the AI can chain operations onto.
        The canvas is the AI's blank page  -  it uses processor methods
        to express, compose, caption, and create.
        """
        t0 = time.time()
        try:
            Image, *_ = self._pil()
            img = Image.new("RGB", (width, height), bg_color)
            return wrap_result(img, t0, ["ai_canvas"])
        except Exception as e:
            return ImageResult(error=str(e))

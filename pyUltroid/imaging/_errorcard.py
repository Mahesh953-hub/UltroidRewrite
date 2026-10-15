"""Carbon-style macOS terminal card for Python tracebacks.

Full narrative: see docs/HANDBOOK.md (module narratives appendix).
"""

from __future__ import annotations

import html as _html
import re
import time
from typing import Any, ClassVar, Sequence

__all__ = ["ErrorCardRenderer", "CARD_WIDTH", "palette"]

#: Carbon's "One Dark"-ish macOS terminal palette, tuned for legibility.
palette: dict[str, tuple[int, int, int]] = {
    "page": (26, 27, 34),
    "chrome": (44, 46, 56),
    "card": (30, 32, 40),
    "border": (58, 61, 74),
    "text": (214, 219, 232),
    "dim": (128, 134, 152),
    "red": (255, 106, 118),
    "amber": (240, 199, 94),
    "green": (126, 214, 145),
    "blue": (122, 168, 255),
    "pink": (232, 141, 218),
    "cyan": (108, 205, 214),
}


class ErrorCardRenderer:
    """Carbon-style macOS terminal card for Python tracebacks.

    Owns the font cache, the search roots and the glyph maths. Every helper
    is a method, so the whole card renderer is one object; the union class
    ``pyUltroid.imaging.Imaging`` inherits it.
    """

    #: Fixed portrait column width. Height comes from the content.
    CARD_WIDTH: ClassVar[int] = 720

    FONT_SEARCH: ClassVar[tuple[str, ...]] = (
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/truetype/liberation",
        "/usr/share/fonts/truetype/noto",
        "/usr/local/share/fonts",
        "/usr/share/fonts",
    )

    MONO_NAMES: ClassVar[tuple[str, ...]] = (
        "DejaVuSansMono.ttf",
        "LiberationMono-Regular.ttf",
        "NotoSansMono-Regular.ttf",
        "DejaVuSansMono-Bold.ttf",
    )

    SANS_NAMES: ClassVar[tuple[str, ...]] = (
        "DejaVuSans.ttf",
        "LiberationSans-Regular.ttf",
        "NotoSans-Regular.ttf",
        "DejaVuSans-Bold.ttf",
    )

    #: Windows-1252 control glyphs that carry no information on a card.
    JUNK: ClassVar[re.Pattern[str]] = re.compile(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]"
    )

    def __init__(self) -> None:
        super().__init__()
        self._fonts: dict[tuple, Any] = {}


    def mono_available(self) -> bool:
        """True when a real monospace face is installed.

        Without one, Pillow silently falls back to a bitmap default and the card
        renders in a proportional face. Install ``fonts-dejavu-core`` (or any
        DejaVu/Liberation/Noto mono TTF) for a correct render.
        """
        from pathlib import Path

        for root in self.FONT_SEARCH:
            base = Path(root)
            if not base.exists():
                continue
            for name in self.MONO_NAMES:
                if next(base.rglob(name), None) is not None:
                    return True
        return False

    def plain(self, value: Any) -> str:
        """Normalize *value* to clean, unescaped, single-form text.

        Undoes HTML entity escaping (``&#x27;`` -> ``'``), collapses runs of
        whitespace that a terminal escape introduced, and drops control glyphs.
        """
        if value is None:
            return ""
        text = value if isinstance(value, str) else str(value)
        # unescape twice: builders often escape already-escaped content.
        for _ in range(2):
            decoded = _html.unescape(text)
            if decoded == text:
                break
            text = decoded
        # Strip ANSI colour codes first: the escape byte is itself in the control
        # range, so removing control glyphs first would leave a bare "[31m".
        text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
        text = re.sub(r"\x1b\][^\x07]*\x07", "", text)  # OSC sequences
        text = self.JUNK.sub("", text)
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()

    def find_font(self, names: Sequence[str], size: int) -> Any:
        """Load the first available font from *names*, else PIL's default."""
        from pathlib import Path

        from PIL import ImageFont

        key = (tuple(names), size)
        if key in self._fonts:
            return self._fonts[key]

        font = None
        for root in self.FONT_SEARCH:
            base = Path(root)
            if not base.exists():
                continue
            for name in names:
                for candidate in base.rglob(name):
                    try:
                        font = ImageFont.truetype(str(candidate), size)
                        break
                    except Exception:
                        continue
                if font is not None:
                    break
            if font is not None:
                break
        if font is None:
            try:
                font = ImageFont.load_default(size=size)
            except TypeError:
                font = ImageFont.load_default()
        self._fonts[key] = font
        return font

    def mono(self, size: int, bold: bool = False) -> Any:
        return self.find_font(self.MONO_NAMES[:2] + self.MONO_NAMES[3:4] if bold else self.MONO_NAMES, size)

    def sans(self, size: int, bold: bool = False) -> Any:
        return self.find_font(self.SANS_NAMES[:2] + self.SANS_NAMES[3:4] if bold else self.SANS_NAMES, size)

    def _text(self, draw: Any, xy: Any, text: str, font: Any, fill: Any) -> None:
        draw.text(xy, text, font=font, fill=fill)

    def _size(self, draw: Any, text: str, font: Any) -> tuple[int, int]:
        box = draw.textbbox((0, 0), text, font=font)
        return box[2] - box[0], box[3] - box[1]

    def syntax_color(self, line: str, base: Any, dim: Any) -> tuple[int, int, int]:
        """Colourise one traceback line the way a terminal would."""
        stripped = line.strip()
        if stripped.startswith("Traceback ("):
            return dim
        if stripped.startswith("File "):
            return palette["blue"]
        if line.startswith("  ") and (":" in line):
            return palette["dim"]
        if stripped.startswith(("During handling", "The above exception")):
            return dim
        if stripped.endswith(":") and "Error" in stripped or "Exception" in stripped:
            return palette["red"]
        if re.match(r"^[A-Za-z_][\w.]*(Error|Exception|Exit|Interrupt)\b", stripped):
            return palette["red"]
        if stripped.startswith(("self", "return", "raise", "await", "yield")):
            return palette["pink"]
        return base

    def wrap_code(self, line: str, font: Any, max_px: int, indent: int = 4) -> list[str]:
        """Hard-wrap *line* to *max_px* using real glyph metrics, not char count.

        The original version passed ``textwrap.wrap(width=...)`` a pixel figure
        (e.g. 664) as if it were a *character* count.  At ~9px-per-glyph that
        means each "wrapped" line could be ~664 glyphs (~5800px) wide, so anything
        longer than ~75 columns overflowed the 720px card and got clipped at the
        right edge instead of breaking onto a new line.  This implementation
        measures each word with ``font.getlength`` and wraps when the running
        advance would exceed ``max_px``; words that *still* exceed the column on
        their own (a long URL / file path with no spaces) are hard-split.
        """
        if not line:
            return [""]
        if self.glyph_width(line, font) <= max_px:
            return [line]
        leading = " " * (len(line) - len(line.lstrip(" ")))
        cont = leading[: min(len(leading), max_px // 2)] + " " * indent
        # The continuation indent is prepended to every wrapped row, so the
        # *content* of each row may only occupy max_px minus that indent's width.
        # Using this single budget for every row (head included, where it is
        # conservative since `cont` already contains `leading`) guarantees no
        # emitted row can exceed the pixel column.
        cont_w = self.glyph_width(cont, font)
        content_budget = max(max_px - cont_w, max_px // 8)

        parts: list[str] = []
        cur = ""
        for word in line.strip().split(" "):
            if not word:
                continue
            while word:
                trial = f"{cur} {word}" if cur else word
                if self.glyph_width(trial, font) <= content_budget:
                    cur = trial
                    word = ""
                    continue
                # `trial` overflows.
                if not cur:
                    # word alone is wider than the column: hard-break it.
                    fit, word = self.take_while(word, font, content_budget)
                    parts.append(fit)
                else:
                    parts.append(cur)
                    cur = ""
        if cur:
            parts.append(cur)
        if not parts:
            return [line]
        # the head keeps its original leading indent; continuations get `cont`.
        head, *rest = parts
        if not head.startswith(leading):
            head = leading + head
        return [head, *(cont + r for r in rest)]

    def take_while(self, word: str, font: Any, max_px: int) -> tuple[str, str]:
        """Longest prefix of *word* whose glyph advance fits in *max_px*.

        Used to hard-split a single over-long token (no spaces) into columns so a
        long URL or file path can never overflow the card.
        """
        lo, hi = 0, len(word)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.glyph_width(word[:mid], font) <= max_px:
                lo = mid
            else:
                hi = mid - 1
        if lo == 0:  # a single glyph already exceeds the column; emit one char
            lo = 1
        return word[:lo], word[lo:]

    def glyph_width(self, text: str, font: Any) -> int:
        """Pixel advance of *text* with *font* (PIL FreeType ``getlength``)."""
        try:
            return int(font.getlength(text))
        except Exception:  # noqa: BLE001 - fall back to cheap char estimate
            return len(text) * 8

    def render(
        self,
        *,
        title: str,
        message: str,
        traceback_text: str = "",
        plugin: str = "",
        request_id: str = "",
        file_name: str = "traceback.py",
        max_lines: int | None = None,
    ) -> Any:
        """Render a portrait macOS/Carbon terminal card for a traceback.

        Returns ``(image, lines_rendered, lines_total)``. Never raises for bad
        input; Pillow failures propagate so the caller can degrade.
        """
        from PIL import Image, ImageDraw

        t0 = time.time()

        title = self.plain(title) or "Error"
        message = self.plain(message)
        raw_tb = self.plain(traceback_text)

        tb_lines = raw_tb.splitlines() if raw_tb else []
        total_lines = len(tb_lines)
        if max_lines is not None and total_lines > max_lines:
            tb_lines = tb_lines[-max_lines:]
            omitted = total_lines - max_lines
        else:
            omitted = 0

        if not message and tb_lines:
            message = tb_lines[-1]
        if not message:
            message = "(no message)"

        PAD = 28
        CHROME_H = 40
        TITLE_H = 34
        LINE_H = 24
        inner = self.CARD_WIDTH - PAD * 2

        f_title = self.sans(19, bold=True)
        f_meta = self.sans(13)
        f_code = self.mono(14)
        f_tab = self.mono(14, bold=True)

        # Pre-wrap everything so the height is exact.
        msg_lines = self.wrap_code(message, f_code, inner)[:3]
        code_lines: list[str] = []
        for line in tb_lines:
            code_lines.extend(self.wrap_code(line, f_code, inner - 46))

        header_h = CHROME_H + TITLE_H
        meta_h = 26 if (plugin or request_id) else 0
        tabs_h = 30
        hint_h = 34
        height = (
            header_h
            + meta_h
            + PAD
            + 24
            + len(msg_lines) * LINE_H
            + (18 + len(code_lines) * LINE_H if code_lines else 0)
            + tabs_h
            + hint_h
            + PAD
        )
        # Keep short tracebacks portrait: the floor is sized so the card is always
        # taller than wide, even for a two-line error.
        width = self.CARD_WIDTH
        floor = int(width * 1.18)
        height = max(floor, height)

        img = Image.new("RGB", (width, height), palette["page"])
        draw = ImageDraw.Draw(img)

        # Card body with a soft rounded top, sitting on the page.
        draw.rounded_rectangle(
            [10, 10, width - 10, height - 10], radius=14, fill=palette["card"]
        )

        # ── macOS window chrome ────────────────────────────────────────────
        draw.rounded_rectangle([10, 10, width - 10, CHROME_H + 6], radius=14, fill=palette["chrome"])
        draw.rectangle([10, CHROME_H - 4, width - 10, CHROME_H + 6], fill=palette["chrome"])
        draw.line([(10, CHROME_H + 6), (width - 10, CHROME_H + 6)], fill=palette["border"], width=1)
        for i, color in enumerate((palette["red"], palette["amber"], palette["green"])):
            cx = 30 + i * 20
            draw.ellipse([cx - 6, CHROME_H / 2 - 6, cx + 6, CHROME_H / 2 + 6], fill=color)
        tab = f"{title}  -  {file_name}" if title != "Error" else file_name
        tw, _ = self._size(draw, tab, f_meta)
        self._text(draw, ((width - tw) // 2, CHROME_H / 2 - 8), tab, f_meta, palette["dim"])

        y = CHROME_H + 18

        # ── Title row ──────────────────────────────────────────────────────
        self._text(draw, (PAD, y), "●", f_title, palette["red"])
        self._text(draw, (PAD + 20, y), title, f_title, palette["text"])
        y += TITLE_H

        # ── Meta row ───────────────────────────────────────────────────────
        if plugin or request_id:
            if plugin:
                self._text(draw, (PAD, y), plugin, f_meta, palette["cyan"])
            if request_id:
                label = f"req {request_id[:8]}"
                lw, _ = self._size(draw, label, f_meta)
                self._text(draw, (width - PAD - lw, y), label, f_meta, palette["dim"])
            y += meta_h

        y += 8

        # ── Error message, highlighted ─────────────────────────────────────
        for line in msg_lines:
            self._text(draw, (PAD, y), line, f_code, palette["amber"])
            y += LINE_H

        if code_lines:
            y += 14
            for n, line in enumerate(code_lines, start=1):
                # Line numbers in a dim gutter, code colourised.
                self._text(draw, (PAD, y), f"{n:>3}", f_code, palette["border"])
                self._text(
                    draw,
                    (PAD + 34, y),
                    line,
                    f_code,
                    self.syntax_color(line, palette["text"], palette["dim"]),
                )
                y += LINE_H

        # ── Tab bar ────────────────────────────────────────────────────────
        y = height - hint_h - tabs_h - PAD + 10
        draw.line([(PAD, y - 8), (width - PAD, y - 8)], fill=palette["border"], width=1)
        active = f"{file_name}"
        aw, _ = self._size(draw, active, f_tab)
        draw.rounded_rectangle(
            [PAD, y - 4, PAD + aw + 26, y + 26], radius=7, fill=palette["chrome"]
        )
        self._text(draw, (PAD + 13, y + 2), active, f_tab, palette["text"])
        hint = "python3 -m traceback"
        hw, _ = self._size(draw, hint, f_code)
        self._text(draw, (width - PAD - hw, y + 4), hint, f_code, palette["dim"])

        if omitted:
            note = f"+{omitted} earlier lines"
            nw, _ = self._size(draw, note, f_meta)
            self._text(draw, (PAD + aw + 44, y + 4), note, f_meta, palette["dim"])

        return img, len(code_lines), total_lines, t0


CARD_WIDTH = ErrorCardRenderer.CARD_WIDTH

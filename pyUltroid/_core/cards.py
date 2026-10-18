# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""
Error card generation for failed plugin loads.

When a plugin fails to load, :func:`generate_error_card` renders a Pillow
image with a red background, the plugin name and the error traceback, saves
it to ``resources/error_cards/`` and logs the failure.

Pillow is optional: if it is not installed the functions log a warning and
return ``None`` instead of raising, so a missing imaging dependency can
never block the loader.
"""

from __future__ import annotations

import textwrap
import time
import urllib.request
from collections import namedtuple
from collections.abc import Sequence
from io import BytesIO
from pathlib import Path
from typing import Any

from ..startup.logger import LOGS

# Where rendered error cards are persisted.
_RESOURCE_ROOT = Path(__file__).resolve().parents[2] / "resources"
ERROR_CARDS_DIR = _RESOURCE_ROOT / "error_cards"

# Search locations for a usable TrueType font.
_FONT_CANDIDATES = [
    _RESOURCE_ROOT / "fonts",
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
]


# Theming (C4)
_DEFAULT_THEME_KEY = "default"


def _ui_theme_safe() -> str:
    """Best-effort ``ui_theme()`` lookup; never raises."""
    try:
        from plugins._rebrand import ui_theme

        value = ui_theme()
        return value if value in _THEME_PALETTES else _DEFAULT_THEME_KEY
    except Exception:
        return _DEFAULT_THEME_KEY


_THEME_PALETTES: dict[str, dict[str, Any]] = {
    "default": {
        "bg": (20, 20, 28),
        "bg_card": (30, 30, 42),
        "text": (235, 235, 245),
        "subtext": (160, 160, 180),
        "accent": (120, 90, 230),
        "track": (50, 50, 66),
        "filled": (120, 200, 120),
        "gradient": ((120, 90, 230), (60, 40, 130)),
    },
    "dark": {
        "bg": (10, 11, 16),
        "bg_card": (20, 21, 30),
        "text": (238, 240, 250),
        "subtext": (140, 145, 170),
        "accent": (80, 200, 235),
        "track": (36, 38, 52),
        "filled": (80, 200, 235),
        "gradient": ((80, 200, 235), (60, 30, 130)),
    },
}


def _theme_palette(theme: str | None = None) -> dict[str, Any]:
    """Resolve a palette dict for *theme*, defaulting to the live UI theme."""
    key = theme if theme in _THEME_PALETTES else _ui_theme_safe()
    return _THEME_PALETTES.get(key, _THEME_PALETTES[_DEFAULT_THEME_KEY])


def _draw_h_gradient(draw, xy, start_rgb, end_rgb) -> None:
    """Draw a horizontal linear gradient rectangle  -  the "brand header"."""
    try:
        x0, y0, x1, y1 = xy
        width = max(1, int(x1) - int(x0))
        if y1 <= y0 or width <= 0:
            return
        for i in range(width):
            t = i / max(1, width - 1)
            color = tuple(
                int(start_rgb[c] + (end_rgb[c] - start_rgb[c]) * t) for c in range(3)
            )
            draw.line([(x0 + i, y0), (x0 + i, y1 - 1)], fill=color)
    except Exception:
        pass


def _draw_watermark(
    draw,
    size: tuple[int, int],
    palette: dict[str, Any],
    *,
    text: str = "Ultroid",
    watermark_doc_id: int | None = None,
) -> None:
    """Draw a small brand watermark glyph in the bottom-right corner."""
    if not text:
        return
    try:
        font = _load_font(12, bold=True)
        color = palette.get("subtext", (160, 160, 180))
        w, h = size
        tw, th = _text_size(draw, text, font)
        draw.text((w - tw - 14, h - th - 10), text, font=font, fill=color)
    except Exception:
        pass


def _pil():
    """Return (Image, ImageDraw, ImageFont) or (None, None, None)."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        return None, None, None
    return Image, ImageDraw, ImageFont


def _load_font(size: int, bold: bool = False):
    """Load a preferred font once, falling back safely when Pillow is absent."""
    _, _, ImageFont = _pil()
    if ImageFont is None:
        return None
    try:
        candidates = []
        for root in _FONT_CANDIDATES:
            if root.exists():
                candidates.extend(root.rglob("*.ttf"))
                candidates.extend(root.rglob("*.otf"))
        candidates.sort(
            key=lambda path: (
                (
                    "bold" in path.name.lower()
                    if bold
                    else "bold" not in path.name.lower()
                ),
                "sans" in path.name.lower(),
            ),
            reverse=True,
        )
        for candidate in candidates:
            try:
                return ImageFont.truetype(str(candidate), size)
            except Exception:
                continue
        return ImageFont.load_default()
    except Exception:
        try:
            return ImageFont.load_default()
        except Exception:
            return None


def _output_path(directory: Path, name: str, *, unique: bool = False) -> Path:
    """Build a card output path and keep all card writes under resources."""
    suffix = f"_{int(time.time() * 1000)}" if unique else ""
    return directory / f"{name}{suffix}.png"


def _background(size, color):
    """Create a Pillow background through the single optional-Pillow gateway."""
    Image, _, _ = _pil()
    return Image.new("RGB", size, color) if Image is not None else None


def _save(img, directory: Path, name: str, *, unique: bool = False) -> str | None:
    """Persist a card as PNG, returning a project-relative path when possible."""
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = _output_path(directory, name, unique=unique)
        img.save(str(path), format="PNG", optimize=True)
        try:
            return str(path.relative_to(_RESOURCE_ROOT.parent))
        except ValueError:
            return str(path)
    except Exception as exc:
        LOGS.error("[cards] failed to save card: %s", exc)
        return None


def _truncate(draw, text: str, font, max_width: int, *, minimum: int = 4) -> str:
    """Fit text to a draw area using one consistent ellipsis strategy."""
    original = text
    while _text_size(draw, text, font)[0] > max_width and len(text) > minimum:
        text = text[:-2]
    return text.rstrip() + "…" if text != original else text


def _wrap(text: str) -> list:
    """Wrap *text* to ~80 chars per line, preserving blank lines."""
    if not text:
        return []
    out = []
    for raw in text.splitlines() or [text]:
        if not raw:
            out.append("")
            continue
        out.extend(textwrap.wrap(raw, width=80) or [raw])
    return out


def _wrap_to_width(
    draw, text: str, font, max_width: int, *, max_lines: int = 4
) -> list:
    """Wrap text by rendered width, retaining a concise final ellipsis."""
    words = str(text or "").split()
    if not words:
        return []
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or _text_size(draw, candidate, font)[0] <= max_width:
            current = candidate
            continue
        lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and " ".join(lines) != " ".join(words):
        lines[-1] = _truncate(draw, lines[-1], font, max_width)
    return lines


def _mono_font(size: int, bold: bool = False):
    """Prefer a legible monospace face for diagnostic cards."""
    _, _, ImageFont = _pil()
    if ImageFont is None:
        return None
    names = (
        ("DejaVuSansMono-Bold.ttf", "LiberationMono-Bold.ttf")
        if bold
        else ("DejaVuSansMono.ttf", "LiberationMono-Regular.ttf")
    )
    for root in _FONT_CANDIDATES:
        if not root.exists():
            continue
        for name in names:
            try:
                candidate = next(root.rglob(name))
                return ImageFont.truetype(str(candidate), size)
            except (StopIteration, OSError):
                continue
    return _load_font(size, bold=bold)


def _serif_font(size: int, bold: bool = False):
    """Prefer a serif display face for the editorial audio cards."""
    _, _, ImageFont = _pil()
    if ImageFont is None:
        return None
    names = (
        ("DejaVuSerif-Bold.ttf", "LiberationSerif-Bold.ttf")
        if bold
        else ("DejaVuSerif.ttf", "LiberationSerif-Regular.ttf")
    )
    for root in _FONT_CANDIDATES:
        if not root.exists():
            continue
        for name in names:
            try:
                candidate = next(root.rglob(name))
                return ImageFont.truetype(str(candidate), size)
            except (StopIteration, OSError):
                continue
    return _load_font(size, bold=bold)


def _render(
    plugin_name: str,
    error_traceback: str,
    error_message: str,
    *,
    expandable: bool = True,
):
    """Render the error card image.

    The original implementation was a landscape red-banner card that truncated
    every traceback line to a fixed width and drew HTML entities literally.
    Rendering now lives in :class:`pyUltroid.imaging.Imaging`, which produces
    a portrait macOS/Carbon terminal card, unescapes the text, and grows
    vertically so no frame is cut off.

    ``expandable`` is retained for call-site compatibility. The card no longer
    omits the traceback: the full trace is always drawn, because a portrait
    card has room for it.

    Returns the image, or ``None`` when Pillow is unavailable / rendering
    failed. Never raises.
    """
    if _pil()[0] is None:
        return None
    try:
        from ..imaging import Imaging

        imaging = Imaging()

        tb = imaging.plain(error_traceback)
        message = imaging.plain(error_message) or (tb.splitlines()[-1] if tb else "")
        title = "PluginLoadError"
        if tb:
            last = tb.splitlines()[-1]
            exc_type = last.split(":", 1)[0].strip()
            if exc_type and " " not in exc_type:
                title = exc_type

        img, _shown, _total, _t0 = imaging.render(
            title=title,
            message=message,
            traceback_text=tb,
            plugin=plugin_name or "",
            file_name=f"{plugin_name or 'plugin'}.py",
        )
        return img
    except Exception as exc:
        LOGS.error("[error_cards] render failed for '%s': %s", plugin_name, exc)
        return None


def generate_error_card(
    plugin_name: str,
    error_traceback: str = "",
    error_message: str = "",
    save: bool = True,
    *,
    expandable: bool = True,
) -> str | None:
    """Generate an error card using the shared imaging renderer when available."""
    if _pil()[0] is None:
        LOGS.warning(
            "[cards] Pillow not installed  -  skipping error card for %r", plugin_name
        )
        return None
    image = _render(
        plugin_name,
        error_traceback,
        error_message,
        expandable=expandable,
    )
    return (
        _save(image, ERROR_CARDS_DIR, f"{plugin_name}_error")
        if image is not None and save
        else None
    )


def error_card_bytesio(
    plugin_name: str,
    error_traceback: str = "",
    error_message: str = "",
    *,
    expandable: bool = False,
) -> BytesIO | None:
    """
    Render an error card and return it as a ``BytesIO`` ready for Telegram.

    Handy when the caller wants to *send* the card to a log channel without
    first writing it to disk. Returns ``None`` if Pillow is unavailable.

    ``expandable=True`` (additive kwarg) renders the shorter
    expandable-traceback overlay variant  -  see :func:`_render`.
    """
    img = _render(plugin_name, error_traceback, error_message, expandable=expandable)
    if img is None:
        return None
    try:
        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.name = f"{plugin_name}_error.png"
        buf.seek(0)
        return buf
    except Exception as exc:
        LOGS.error("[error_cards] bytesio export failed for '%s': %s", plugin_name, exc)
        return None


# ──────────────────────────────────────────────────────────────────────────
# Error notice text (C5)  -  the "message that travels next to the error card"
# ──────────────────────────────────────────────────────────────────────────
#
# :func:`build_error_notice` is the canonical home for this builder (it used
# to live, unwired, in the now-deprecated ``pyUltroid/error_cards.py`` shim  - 
# see that module's docstring). :func:`pyUltroid._core.loader`'s failure
# path is the actual send site.

#: ``(text, entities, parse_mode)``  -  ready to feed straight into
#: ``client.send_message(peer, text, formatting_entities=entities, parse_mode=parse_mode)``.
#: ``parse_mode`` is ``None`` on the entities path (``entities`` already
#: carries the formatting) and ``"html"`` on the plain-text fallback path
#: (``entities`` is ``[]`` there  -  the ``<blockquote expandable>`` markup in
#: ``text`` carries the formatting instead).
ErrorNotice = namedtuple("ErrorNotice", ("text", "entities", "parse_mode"))


def _error_notice_summary_line(error_message: str, tb_lines: list[str]) -> str:
    if error_message:
        return error_message
    return tb_lines[-1] if tb_lines else "Unknown error"


def build_error_notice(
    plugin_name: str,
    error_traceback: str = "",
    error_message: str = "",
    *,
    expandable: bool = True,
    max_lines: int = 20,
) -> ErrorNotice:
    """Build the ``(text, entities, parse_mode)`` message accompanying an error card.

    ``expandable=True`` (the default) wraps the summary + traceback in a
    single collapsed/expandable blockquote via :mod:`custom.entities`:
    Telegram renders a collapsed blockquote showing only its first couple of
    lines (the *summary*) until tapped, at which point the rest (the *full
    traceback*, up to ``max_lines`` lines) becomes visible  -  matching the
    card's own ``expandable=True`` overlay variant (see
    :func:`error_card_bytesio`).

    ``expandable=False`` renders the same information without the
    blockquote entity (a plain header + summary + traceback block)  -  useful
    for destinations that don't render expandable blockquotes (e.g. plain
    log files).

    Uses the pinned Telethon blockquote entity directly. Never raises.
    """
    try:
        header = f"⚠️ Plugin Load Error  -  {plugin_name}"
        tb = (error_traceback or "").strip()
        tb_lines = tb.splitlines()[-max_lines:] if tb else []
        summary = _error_notice_summary_line(error_message, tb_lines)
        tb_text = "\n".join(tb_lines)
        quote_text = f"{summary}\n\n{tb_text}" if tb_text else summary

        if expandable:
            from custom.entities import combine
            from custom.entities import expandable as expandable_entity

            quote_body, quote_entities = expandable_entity(quote_text)
            text, ents = combine((f"{header}\n\n", []), (quote_body, quote_entities))
            return ErrorNotice(text=text, entities=ents, parse_mode=None)

        # expandable=False  -  plain block, no blockquote entity at all.
        text = f"{header}\n\n{quote_text}"
        return ErrorNotice(text=text, entities=[], parse_mode=None)
    except Exception as exc:
        LOGS.debug("[cards] build_error_notice failed for %r: %s", plugin_name, exc)
        return ErrorNotice(
            text=f"⚠️ Plugin Load Error  -  {plugin_name}", entities=[], parse_mode=None
        )


def _render_success(plugin_name: str):
    """
    Render a green "loaded successfully" card.

    Mirrors :func:`_render` but with a success palette. Returns the PIL image
    or ``None`` if Pillow is unavailable / rendering failed. Never raises.
    """
    Image, ImageDraw, _ = _pil()
    if Image is None:
        return None
    try:
        PADDING = 28
        LINE_H = 24
        HEADER_H = 86
        WIDTH = 820

        body_lines = [
            f"plugin: {plugin_name}",
            "",
            "Loaded successfully ✓",
        ]

        height = HEADER_H + PADDING + len(body_lines) * LINE_H + PADDING * 2

        # Green background per spec.
        img = _background((WIDTH, height), (34, 150, 70))
        draw = ImageDraw.Draw(img)

        # Darker header band for contrast with the green body.
        draw.rectangle([0, 0, WIDTH, HEADER_H], fill=(20, 110, 52))

        # Success icon (white check in a circle).
        draw.ellipse([PADDING, 18, PADDING + 50, 68], fill=(48, 180, 90))
        draw.line([PADDING + 14, 44, PADDING + 28, 56], fill="white", width=4)
        draw.line([PADDING + 28, 56, PADDING + 40, 30], fill="white", width=4)

        title_font = _load_font(24)
        sub_font = _load_font(15)
        body_font = _load_font(16)

        draw.text(
            (PADDING + 64, 22), "Plugin Load Success", font=title_font, fill="white"
        )
        draw.text((PADDING + 64, 52), "status: ok", font=sub_font, fill=(215, 255, 225))

        y = HEADER_H + PADDING
        for line in body_lines:
            draw.text((PADDING, y), line[:110], font=body_font, fill=(230, 255, 235))
            y += LINE_H

        draw.rectangle([0, 0, WIDTH - 1, height - 1], outline=(20, 90, 44), width=2)
        return img
    except Exception as exc:
        LOGS.error("[error_cards] success render failed for '%s': %s", plugin_name, exc)
        return None


def generate_success_card(plugin_name: str, save: bool = True) -> str | None:
    """
    Render a success card for a plugin that loaded cleanly and persist it.

    Returns the file path of the saved card, or ``None`` if Pillow is
    unavailable, rendering failed, or saving failed.
    """
    if _pil()[0] is None:
        LOGS.warning(
            "[error_cards] Pillow not installed  -  skipping success card for '%s'.",
            plugin_name,
        )
        return None

    img = _render_success(plugin_name)
    if img is None:
        return None

    path = None
    if save:
        try:
            path = _save(img, ERROR_CARDS_DIR, f"{plugin_name}_success")
        except Exception as save_err:
            LOGS.error("[error_cards] could not save success card: %s", save_err)
            path = None

    return str(path) if path else None


def success_card_bytesio(plugin_name: str) -> BytesIO | None:
    """Render a success card and return it as a ``BytesIO`` ready for Telegram."""
    img = _render_success(plugin_name)
    if img is None:
        return None
    try:
        buf = BytesIO()
        img.save(buf, format="PNG", optimize=True)
        buf.name = f"{plugin_name}_success.png"
        buf.seek(0)
        return buf
    except Exception as exc:
        LOGS.error(
            "[error_cards] success bytesio export failed for '%s': %s", plugin_name, exc
        )
        return None


# Now-playing and queue card
# High-resolution landscape canvases keep typography sharp after Telegram's
# image processing while preserving the public generator APIs below.
_NP_WIDTH = 1600
_NP_HEIGHT = 640
_THUMB_SIZE = 456
_THUMB_PAD = 92
_MAX_QUEUE = 10
_OUT_DIR = _RESOURCE_ROOT / "now_playing"

_CARDS_DIR = _RESOURCE_ROOT / "cards"

_BG = _THEME_PALETTES["default"]["bg"]
_BG_CARD = _THEME_PALETTES["default"]["bg_card"]
_TEXT = _THEME_PALETTES["default"]["text"]
_SUBTEXT = _THEME_PALETTES["default"]["subtext"]
_ACCENT = _THEME_PALETTES["default"]["accent"]
_TRACK = _THEME_PALETTES["default"]["track"]
_FILLED = _THEME_PALETTES["default"]["filled"]


def _fmt_time(seconds) -> str:
    sec = int(seconds or 0)
    if sec < 0:
        sec = 0
    hours, rem = divmod(sec, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _text_size(draw, text: str, font):
    try:
        l, t, r, b = draw.textbbox((0, 0), text, font=font)
        return r - l, b - t
    except Exception:
        try:
            return font.getsize(text)  # type: ignore[attr-defined]
        except Exception:
            return len(text) * 9, 18


def _round_rect(draw, xy, radius, fill):
    """Draw a rounded rectangle (fallback to plain rect on old Pillow)."""
    try:
        draw.rounded_rectangle(xy, radius=radius, fill=fill)
    except Exception:
        draw.rectangle(xy, fill=fill)


def _fetch_thumbnail(thumbnail_url: str | None, size: int = _THUMB_SIZE):
    """Return a thumbnail from a URL or local path, or ``None`` on failure."""
    if not thumbnail_url:
        return None
    try:
        from PIL import Image

        if str(thumbnail_url).startswith(("http://", "https://")):
            req = urllib.request.Request(
                str(thumbnail_url), headers={"User-Agent": "SarahUB"}
            )
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = resp.read()
            img = Image.open(BytesIO(data))
        else:
            img = Image.open(str(thumbnail_url))
        img = img.convert("RGB")
        img.thumbnail((size, size))
        return img
    except Exception as e:
        LOGS.debug(f"[now_playing] thumbnail unavailable: {e}")
        return None


def _paste_rounded(img, artwork, box, radius: int) -> None:
    """Paste supplied artwork into a centered rounded crop."""
    try:
        from PIL import Image, ImageDraw

        x0, y0, x1, y1 = map(int, box)
        size = (x1 - x0, y1 - y0)
        # Supplied artwork can be any aspect ratio; fit it without distortion.
        source = artwork.copy()
        source.thumbnail(size)
        canvas = Image.new("RGB", size, (36, 35, 31))
        canvas.paste(
            source, ((size[0] - source.width) // 2, (size[1] - source.height) // 2)
        )
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [0, 0, size[0], size[1]], radius=radius, fill=255
        )
        img.paste(canvas, (x0, y0), mask)
    except Exception:
        pass


def _vinyl(draw, box) -> None:
    """Draw the fallback record treatment used when no album art is available."""
    x0, y0, x1, y1 = map(int, box)
    size = min(x1 - x0, y1 - y0)
    cx, cy = x0 + size // 2, y0 + size // 2
    rings = (
        (0.50, (27, 28, 26), None),
        (0.38, None, (80, 75, 66)),
        (0.235, (174, 73, 52), None),
        (0.045, (237, 220, 190), None),
    )
    for factor, fill, outline in rings:
        radius = int(size * factor)
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            fill=fill,
            outline=outline,
            width=3 if outline else 1,
        )


def _safe_seconds(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def generate_now_playing(
    track_name: str,
    artist: str,
    duration: int,
    position: int,
    thumbnail_url: str | None = None,
    *,
    theme: str | None = None,
    watermark: bool = True,
    watermark_doc_id: int | None = None,
) -> str | None:
    """Render the warm vinyl-lounge now-playing card as a PNG.

    The additive theme and watermark kwargs remain accepted. The selected
    lounge artwork intentionally uses its fixed warm paper palette; resolved
    theme colours remain available to the shared watermark implementation.
    """
    try:
        from PIL import ImageDraw

        palette = _theme_palette(theme)
        lounge_accent = (
            palette["accent"] if palette == _THEME_PALETTES["dark"] else (174, 74, 52)
        )
        img = _background((_NP_WIDTH, _NP_HEIGHT), (43, 32, 27))
        draw = ImageDraw.Draw(img)
        _draw_h_gradient(
            draw, (0, 0, _NP_WIDTH, _NP_HEIGHT), (58, 40, 29), (21, 30, 28)
        )
        panel = [58, 54, _NP_WIDTH - 58, _NP_HEIGHT - 54]
        _round_rect(draw, panel, 28, (235, 222, 197))

        art_box = [92, 92, 548, 548]
        thumb = _fetch_thumbnail(thumbnail_url)
        if thumb is not None:
            _paste_rounded(img, thumb, art_box, 28)
        else:
            _vinyl(draw, art_box)

        text_x, right = 622, _NP_WIDTH - 124
        title_font = _serif_font(54, bold=True)
        artist_font = _serif_font(27)
        body_font = _load_font(21)
        eyebrow_font = _load_font(15, bold=True)
        time_font = _mono_font(17)
        track_name = str(track_name or "Unknown Track")
        artist = str(artist or "Unknown Artist")
        title_lines = _wrap_to_width(
            draw, track_name, title_font, right - text_x, max_lines=2
        )
        y = 100
        draw.text(
            (text_x, y), "ON THE TURNTABLE", font=eyebrow_font, fill=lounge_accent
        )
        y += 54
        for line in title_lines or ["Unknown Track"]:
            draw.text((text_x, y), line, font=title_font, fill=(45, 43, 38))
            y += 58
        draw.text(
            (text_x, y + 2),
            _truncate(draw, artist, artist_font, right - text_x),
            font=artist_font,
            fill=(104, 91, 77),
        )

        detail_y = max(304, y + 76)
        detail = _wrap_to_width(
            draw, f"Now spinning: {artist}", body_font, right - text_x, max_lines=2
        )
        for line in detail:
            draw.text((text_x, detail_y), line, font=body_font, fill=(105, 98, 87))
            detail_y += 36

        duration_seconds = _safe_seconds(duration)
        position_seconds = _safe_seconds(position)
        ratio = (
            min(1.0, position_seconds / duration_seconds) if duration_seconds else 0.0
        )
        bar_y, bar_h = 430, 14
        _round_rect(draw, [text_x, bar_y, right, bar_y + bar_h], 7, (190, 180, 161))
        filled = text_x + int((right - text_x) * ratio)
        if ratio:
            _round_rect(
                draw,
                [text_x, bar_y, max(text_x + 7, filled), bar_y + bar_h],
                7,
                (174, 74, 52),
            )
        knob_x = text_x + int((right - text_x) * ratio)
        draw.ellipse(
            [knob_x - 10, bar_y - 3, knob_x + 10, bar_y + 17], fill=(250, 247, 239)
        )
        elapsed, total = _fmt_time(position_seconds), _fmt_time(duration_seconds)
        draw.text((text_x, 480), elapsed, font=time_font, fill=(105, 96, 83))
        total_w = _text_size(draw, total, time_font)[0]
        draw.text((right - total_w, 480), total, font=time_font, fill=(105, 96, 83))
        draw.text(
            (text_x, 538),
            "SIDE A  ·  NOW PLAYING",
            font=eyebrow_font,
            fill=(139, 124, 105),
        )

        if watermark:
            _draw_watermark(
                draw,
                (_NP_WIDTH, _NP_HEIGHT),
                palette,
                watermark_doc_id=watermark_doc_id,
            )
        return _save(img, _OUT_DIR, "now_playing", unique=True)
    except Exception as e:
        LOGS.error(f"[now_playing] generate_now_playing failed: {e}")
        return None


def generate_queue_card(
    queue: Sequence[dict | object],
    *,
    theme: str | None = None,
    watermark: bool = True,
    watermark_doc_id: int | None = None,
) -> str | None:
    """Render the light editorial schedule, listing at most ``_MAX_QUEUE`` tracks."""
    try:
        from PIL import ImageDraw

        if not queue:
            return None
        items = list(queue[:_MAX_QUEUE])
        width, margin, header_h, row_h, footer_h = 1600, 84, 220, 112, 112
        height = header_h + len(items) * row_h + footer_h
        palette = _theme_palette(theme)
        paper, ink, muted, accent, rule = (
            (246, 243, 235),
            (35, 35, 33),
            (119, 112, 102),
            (20, 93, 104),
            (210, 204, 193),
        )
        img = _background((width, height), paper)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, 18, height], fill=accent)

        eyebrow_font = _load_font(15, bold=True)
        header_font = _serif_font(52, bold=True)
        count_font = _serif_font(60, bold=True)
        index_font = _mono_font(17, bold=True)
        title_font = _serif_font(27, bold=True)
        artist_font = _load_font(14, bold=True)
        duration_font = _mono_font(18)
        footer_font = _load_font(14, bold=True)
        draw.text(
            (margin, 72), "SARAH AUDIO / PLAY QUEUE", font=eyebrow_font, fill=accent
        )
        draw.text((margin, 124), "Listening queue", font=header_font, fill=ink)
        count = f"{len(items):02d}"
        cw = _text_size(draw, count, count_font)[0]
        draw.text((width - margin - cw, 124), count, font=count_font, fill=accent)
        draw.line(
            [(margin, header_h), (width - margin, header_h)],
            fill=(177, 172, 162),
            width=2,
        )

        total_seconds = 0
        y = header_h + 44
        duration_x = width - margin - 114
        text_x = margin + 96
        for index, track in enumerate(items, start=1):
            title = (
                track.get("name", track.get("title", ""))
                if isinstance(track, dict)
                else getattr(track, "name", getattr(track, "title", ""))
            )
            artist = (
                track.get("artist", "")
                if isinstance(track, dict)
                else getattr(track, "artist", "")
            )
            seconds = (
                track.get("duration", 0)
                if isinstance(track, dict)
                else getattr(track, "duration", 0)
            )
            title = str(title or "Unknown Track")
            artist = str(artist or "Unknown Artist")
            seconds = _safe_seconds(seconds)
            total_seconds += seconds
            draw.text((margin, y), f"{index:02d}", font=index_font, fill=accent)
            draw.text(
                (text_x, y - 8),
                _truncate(draw, title, title_font, duration_x - text_x - 50),
                font=title_font,
                fill=ink,
            )
            draw.text(
                (text_x, y + 31),
                _truncate(draw, artist.upper(), artist_font, duration_x - text_x - 50),
                font=artist_font,
                fill=muted,
            )
            duration_label = _fmt_time(seconds)
            dw = _text_size(draw, duration_label, duration_font)[0]
            draw.text(
                (duration_x - dw, y + 4),
                duration_label,
                font=duration_font,
                fill=(64, 63, 58),
            )
            draw.line([(text_x, y + 74), (width - margin, y + 74)], fill=rule, width=1)
            y += row_h

        footer_y = height - 56
        draw.text(
            (margin, footer_y),
            f"{_fmt_time(total_seconds)} TOTAL",
            font=footer_font,
            fill=muted,
        )
        action = "PLAY IN ORDER →"
        aw = _text_size(draw, action, footer_font)[0]
        draw.text(
            (width - margin - aw, footer_y), action, font=footer_font, fill=accent
        )
        if watermark:
            _draw_watermark(
                draw, (width, height), palette, watermark_doc_id=watermark_doc_id
            )
        return _save(img, _OUT_DIR, "queue", unique=True)
    except Exception as e:
        LOGS.error(f"[now_playing] generate_queue_card failed: {e}")
        return None


# Generic branded cards  -  stats card + generic rebrand card

_STATS_ROW_ORDER = ("uptime", "plugins", "addons", "commands", "db")
_STATS_ROW_LABELS = {
    "uptime": "Uptime",
    "plugins": "Plugins",
    "addons": "Addons",
    "commands": "Commands",
    "db": "Database",
}


def _stats_rows(stats: dict[str, Any]) -> list[tuple[str, str]]:
    """Turn a ``stats`` dict into an ordered list of ``(label, value)`` rows."""
    stats = stats or {}
    rows: list[tuple[str, str]] = []
    seen = set()
    for key in _STATS_ROW_ORDER:
        if key in stats:
            rows.append((_STATS_ROW_LABELS[key], str(stats[key])))
            seen.add(key)
    for key, value in stats.items():
        if key in seen:
            continue
        rows.append((str(key).replace("_", " ").title(), str(value)))
    return rows


def _render_rebrand_card(
    title: str,
    subtitle: str = "",
    rows: Sequence[tuple[Any, Any]] = (),
    *,
    theme: str | None = None,
    watermark: bool = True,
    watermark_doc_id: int | None = None,
):
    """Render a generic branded card: gradient header, title/subtitle, rows.

    Shared renderer behind :func:`generate_stats_card` and
    :func:`generate_rebrand_card`. Returns the PIL image, or ``None`` if
    Pillow is unavailable / rendering failed. Never raises.
    """
    Image, ImageDraw, _ = _pil()
    if Image is None:
        return None
    try:
        palette = _theme_palette(theme)
        rows = list(rows or [])

        PADDING = 28
        HEADER_H = 96 if subtitle else 76
        ROW_H = 34
        WIDTH = 720

        height = HEADER_H + PADDING + max(len(rows), 1) * ROW_H + PADDING

        img = _background((WIDTH, height), palette["bg"])
        draw = ImageDraw.Draw(img)
        _round_rect(draw, [10, 10, WIDTH - 10, height - 10], 16, palette["bg_card"])

        # Brand gradient header strip.
        gradient = palette.get("gradient")
        if gradient:
            _draw_h_gradient(draw, (10, 10, WIDTH - 10, 18), gradient[0], gradient[1])

        title_font = _load_font(24, bold=True)
        sub_font = _load_font(15, bold=False)
        label_font = _load_font(16, bold=True)
        value_font = _load_font(16, bold=False)

        title_text = _truncate(
            draw, title or "SarahUB", title_font, WIDTH - PADDING * 2
        )
        draw.text((PADDING, 32), title_text, font=title_font, fill=palette["text"])
        if subtitle:
            sub_text = _truncate(draw, subtitle, sub_font, WIDTH - PADDING * 2)
            draw.text((PADDING, 64), sub_text, font=sub_font, fill=palette["subtext"])

        zebra = tuple(min(255, c + 6) for c in palette["bg_card"])
        y = HEADER_H + PADDING // 2
        for i, (label, value) in enumerate(rows):
            if i % 2 == 0:
                draw.rectangle([12, y, WIDTH - 12, y + ROW_H], fill=zebra)
            label_text = _truncate(draw, str(label), label_font, WIDTH // 2 - PADDING)
            draw.text(
                (PADDING, y + 7), label_text, font=label_font, fill=palette["accent"]
            )
            value_text = _truncate(
                draw, str(value), value_font, WIDTH - PADDING - WIDTH // 2
            )
            vw = _text_size(draw, value_text, value_font)[0]
            draw.text(
                (WIDTH - PADDING - vw, y + 7),
                value_text,
                font=value_font,
                fill=palette["text"],
            )
            y += ROW_H

        draw.rectangle([0, 0, WIDTH - 1, height - 1], outline=palette["track"], width=1)

        if watermark:
            _draw_watermark(
                draw, (WIDTH, height), palette, watermark_doc_id=watermark_doc_id
            )

        return img
    except Exception as exc:
        LOGS.error("[cards] rebrand card render failed for %r: %s", title, exc)
        return None


__all__ = [
    "ErrorNotice",
    "build_error_notice",
    "error_card_bytesio",
    "generate_error_card",
    "generate_now_playing",
    "generate_queue_card",
    "generate_success_card",
    "success_card_bytesio",
]

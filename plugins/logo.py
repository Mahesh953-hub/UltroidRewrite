# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}logo <text>`
   Generate a logo of the given Text.
   Or Reply To image , to write ur text on it.
   Or Reply To Font File, To write with that font.
   The result ships with an inline button menu for fast modifications
   (resize / background / border / rounded corners / shadow / gradient /
   add text). Buttons are delivered bot-first, falling back to an inline
   query, then to a text-command hint.

• `{i}logograd <text> [preset 0-5]`
   Gradient-background logo (creative tool).

• `{i}logogtext <text> [preset 0-5]`
   Logo with gradient-filled text (creative tool).

• `{i}logocurve <text>`
   Logo with the text laid out along a circular arc (creative tool).

• `{i}logomulti <line1 | line2 | line3>`
   Multi-layered logo — stacked text layers of decreasing size (creative tool).

• `{i}logotext <text>`
   Add text onto a replied logo/image (image-to-logo).

• `{i}logoresize <512|1024|2x|0.5x|WxH>`
• `{i}logobg <color>`
• `{i}logoborder [width]`
• `{i}logoround [radius]`
• `{i}logoshadow`
   Text-command equivalents of the button-menu operations — usable when
   interactive buttons are unavailable. Reply to a logo (or generate one
   with `{i}logo` first).
"""

import glob
import math
import os
import random
import re
import uuid

from telethon import Button
from custom.tg import ikm

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
except ImportError:
    Image = None

from pyUltroid.fns import fns
from pyUltroid.fns.tools import LogoHelper

from . import (
    LOGS,
    OWNER_ID,
    OWNER_NAME,
    asst,
    callback,
    get_string,
    in_pattern,
    ultroid_bot,
    ultroid_cmd,
)
from ._inline import something

# ---------------------------------------------------------------------------
# Palettes & constants
# ---------------------------------------------------------------------------

# Solid background colour swatches (RGB) — used by .logobg and the BG menu.
_BG_COLORS = {
    "black": (0, 0, 0),
    "white": (255, 255, 255),
    "red": (220, 53, 69),
    "blue": (0, 123, 255),
    "green": (40, 167, 69),
    "purple": (138, 43, 226),
    "orange": (255, 165, 0),
    "navy": (16, 28, 58),
}

# Gradient presets (start, end) — used by .logograd/.logogtext and the menu.
_GRADIENTS = [
    ((255, 94, 98), (255, 175, 189)),  # 0 — sunset
    ((72, 52, 212), (168, 85, 247)),  # 1 — purple
    ((0, 198, 255), (0, 114, 255)),  # 2 — ocean
    ((43, 43, 43), (162, 0, 255)),  # 3 — neon dark
    ((240, 147, 251), (245, 87, 108)),  # 4 — candy
    ((0, 255, 136), (0, 196, 180)),  # 5 — mint
]

_DEFAULT_SIZE = (1024, 1024)
_ROUND_RADIUS = 90

# In-memory editing sessions: sid -> {path, chat, user, name, font, style, size}.
# Both ultroid_bot and asst live in the same process, so callback handlers on
# the assistant can read this dict to continue editing a generated logo.
_SESSIONS = {}


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------


def _lerp(c0, c1, t):
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c0, c1))


def _random_font():
    fonts = glob.glob("resources/fonts/*")
    return random.choice(fonts) if fonts else None


def _img_size(path):
    try:
        return Image.open(path).size
    except Exception:
        return _DEFAULT_SIZE


def _cleanup(path):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _color(name_or_tuple, default=(16, 28, 58)):
    if isinstance(name_or_tuple, str):
        return _BG_COLORS.get(name_or_tuple.lower(), default)
    return name_or_tuple


# ---------------------------------------------------------------------------
# Background generators (replace the removed unsplashsearch dependency)
# ---------------------------------------------------------------------------


def _gradient_image(w, h, colors, direction="diagonal"):
    """Build an RGB gradient image of size (w, h)."""
    c0, c1 = colors
    base = 128
    if direction == "vertical":
        strip = Image.new("RGB", (1, base))
        for i in range(base):
            strip.putpixel((0, i), _lerp(c0, c1, i / (base - 1)))
        return strip.resize((w, h), Image.BILINEAR)
    if direction == "horizontal":
        strip = Image.new("RGB", (base, 1))
        for i in range(base):
            strip.putpixel((i, 0), _lerp(c0, c1, i / (base - 1)))
        return strip.resize((w, h), Image.BILINEAR)
    # diagonal (default)
    img = Image.new("RGB", (base, base))
    px = img.load()
    for y in range(base):
        for x in range(base):
            px[x, y] = _lerp(c0, c1, (x + y) / (2 * (base - 1)))
    return img.resize((w, h), Image.BILINEAR)


def _make_gradient_bg(outpath, size, colors=None, direction="diagonal"):
    colors = colors or random.choice(_GRADIENTS)
    _gradient_image(size[0], size[1], colors, direction).save(outpath, "PNG")
    return outpath


def _make_solid_bg(outpath, size, color):
    Image.new("RGB", size, _color(color)).save(outpath, "PNG")
    return outpath


# ---------------------------------------------------------------------------
# Creative text renderers
# ---------------------------------------------------------------------------


def _gradient_text_logo(
    bgpath, text, fontpath, grad_colors, stroke_width=8, stroke_fill="black"
):
    """Render text filled with a gradient (solid stroke behind)."""
    base = Image.open(bgpath).convert("RGBA")
    w, h = base.size
    font_size = LogoHelper.find_font_size(text, fontpath, base, 0.7)
    font = ImageFont.truetype(fontpath, font_size)
    bbox = LogoHelper.get_text_bbox(text, base, font)
    x = (w - (bbox[2] - bbox[0])) // 2
    y = (h - (bbox[3] - bbox[1])) // 2

    draw = ImageDraw.Draw(base)
    # solid stroke behind a transparent body
    if stroke_width:
        draw.text(
            (x, y),
            text,
            font=font,
            fill=(0, 0, 0, 0),
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
    # gradient body via a text-shaped alpha mask
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).text((x, y), text, font=font, fill=255)
    grad = _gradient_image(w, h, grad_colors).convert("RGBA")
    grad.putalpha(mask)
    base.alpha_composite(grad)

    out = fns.check_filename("logo.png")
    base.save(out, "PNG")
    return out


def _curve_text_logo(
    bgpath, text, fontpath, fill="white", stroke_width=3, stroke_fill="black"
):
    """Render text laid out along a circular arc."""
    base = Image.open(bgpath).convert("RGBA")
    w, h = base.size
    font_size = max(40, int(min(w, h) * 0.12))
    font = ImageFont.truetype(fontpath, font_size)
    chars = list(text)
    if not chars:
        chars = ["?"]

    spread = min(180, max(30, 12 * len(chars)))
    radius = int(min(w, h) * 0.32)
    cx, cy = w // 2, int(h * 0.62)
    if len(chars) == 1:
        angles = [0.0]
    else:
        step = spread / (len(chars) - 1)
        angles = [-spread / 2 + step * i for i in range(len(chars))]

    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    measure = ImageDraw.Draw(overlay)
    for ch, ang in zip(chars, angles):
        bbox = measure.textbbox((0, 0), ch, font=font)
        cw, chh = bbox[2] - bbox[0], bbox[3] - bbox[1]
        pad = stroke_width * 2
        cell = Image.new("RGBA", (cw + pad * 2, chh + pad * 2), (0, 0, 0, 0))
        ImageDraw.Draw(cell).text(
            (pad - bbox[0], pad - bbox[1]),
            ch,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        cell = cell.rotate(ang, expand=True, resample=Image.BICUBIC)
        rad = math.radians(ang - 90)
        px = cx + int(radius * math.cos(rad)) - cell.width // 2
        py = cy + int(radius * math.sin(rad)) - cell.height // 2
        overlay.alpha_composite(cell, (px, py))

    base.alpha_composite(overlay)
    out = fns.check_filename("logo.png")
    base.save(out, "PNG")
    return out


def _multilayer_logo(bgpath, text, fontpath, fill="white", stroke_fill="black"):
    """Stacked multi-line logo — each line a smaller layer."""
    base = Image.open(bgpath).convert("RGBA")
    w, h = base.size
    lines = [ln.strip() for ln in text.split("|") if ln.strip()] or [text]
    n = len(lines)

    layers = []
    total_h = 0
    for i, ln in enumerate(lines):
        ratio = max(0.25, 0.72 - i * 0.16)
        fs = LogoHelper.find_font_size(ln, fontpath, base, ratio)
        fnt = ImageFont.truetype(fontpath, fs)
        bbox = LogoHelper.get_text_bbox(ln, base, fnt)
        th = bbox[3] - bbox[1]
        layers.append((ln, fnt, bbox[2] - bbox[0], th))
        total_h += th + 12

    draw = ImageDraw.Draw(base)
    y = (h - total_h) // 2
    for ln, fnt, tw, th in layers:
        x = (w - tw) // 2
        sw = 8 if len(ln) <= 8 else 4
        draw.text(
            (x, y), ln, font=fnt, fill=fill, stroke_width=sw, stroke_fill=stroke_fill
        )
        y += th + 12

    out = fns.check_filename("logo.png")
    base.save(out, "PNG")
    return out


def _add_text_to_image(
    path, text, fontpath, fill="white", stroke_width=5, stroke_fill="black"
):
    """Overlay text on an existing image (image-to-logo / add text)."""
    img = Image.open(path).convert("RGBA")
    font_size = LogoHelper.find_font_size(text, fontpath, img, 0.7)
    font = ImageFont.truetype(fontpath, font_size)
    bbox = LogoHelper.get_text_bbox(text, img, font)
    x = (img.width - (bbox[2] - bbox[0])) // 2
    y = (img.height - (bbox[3] - bbox[1])) // 2
    ImageDraw.Draw(img).text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )
    out = fns.check_filename("logo.png")
    img.save(out, "PNG")
    return out


# ---------------------------------------------------------------------------
# Modification transforms (each returns a new path; preserves transparency)
# ---------------------------------------------------------------------------


def _resize_logo(path, size=None, scale=None):
    img = Image.open(path)
    if scale:
        size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
    img = img.resize((size[0], size[1]), Image.LANCZOS)
    out = fns.check_filename("logo.png")
    img.save(out, "PNG")
    return out


def _round_corners(path, radius=_ROUND_RADIUS):
    img = Image.open(path).convert("RGBA")
    w, h = img.size
    radius = min(radius, w // 2, h // 2)
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w, h), radius=radius, fill=255)
    img.putalpha(mask)
    out = fns.check_filename("logo.png")
    img.save(out, "PNG")
    return out


def _add_border(path, width=10, color="white"):
    img = Image.open(path).convert("RGB")
    img = ImageOps.expand(img, border=width, fill=_color(color, (255, 255, 255)))
    out = fns.check_filename("logo.png")
    img.save(out, "PNG")
    return out


def _add_shadow(path, offset=(10, 10), blur=12, opacity=180):
    """Drop a blurred shadow behind the (possibly transparent) logo."""
    img = Image.open(path).convert("RGBA")
    w, h = img.size
    pad = blur * 2 + abs(offset[0]) + abs(offset[1]) + 8
    cw, ch = w + 2 * pad, h + 2 * pad
    canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))

    shadow = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    alpha = img.getchannel("A") if img.mode == "RGBA" else Image.new("L", (w, h), 255)
    shadow.paste((0, 0, 0, opacity), (pad, pad, pad + w, pad + h), alpha)
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))

    canvas.alpha_composite(shadow, (offset[0], offset[1]))
    canvas.alpha_composite(img, (pad, pad))
    out = fns.check_filename("logo.png")
    canvas.save(out, "PNG")
    return out


# ---------------------------------------------------------------------------
# Regeneration (background swap) — needs the original text + font
# ---------------------------------------------------------------------------


def _regen_bg(sess, solid=None, gradient=None):
    cur = Image.open(sess["path"])
    size = cur.size
    bg = fns.check_filename("logo_bg.png")
    if solid is not None:
        _make_solid_bg(bg, size, solid)
    else:
        colors = _GRADIENTS[gradient % len(_GRADIENTS)]
        _make_gradient_bg(bg, size, colors)
    style = sess.get("style", {})
    out = LogoHelper.make_logo(
        bg,
        sess["name"],
        sess["font"],
        fill=style.get("fill", "white"),
        stroke_width=style.get("stroke_width", 5),
        stroke_fill=style.get("stroke_fill", "black"),
    )
    _cleanup(bg)
    return out


def _apply_op(sess, op):
    """Apply a single modification op to the session image; return new path."""
    path = sess["path"]
    if op == "round":
        return _round_corners(path)
    if op == "shadow":
        return _add_shadow(path)
    if op == "resize_half":
        return _resize_logo(path, scale=0.5)
    if op == "resize_double":
        return _resize_logo(path, scale=2.0)
    if op == "resize_512":
        return _resize_logo(path, size=(512, 512))
    if op == "resize_1024":
        return _resize_logo(path, size=(1024, 1024))
    if op.startswith("bg_"):
        if not sess.get("name"):
            raise ValueError(
                "Regenerate needs the original text — use .logograd/.logobg"
            )
        return _regen_bg(sess, solid=op[3:])
    if op.startswith("grad_"):
        if not sess.get("name"):
            raise ValueError(
                "Regenerate needs the original text — use .logograd/.logobg"
            )
        return _regen_bg(sess, gradient=int(op[5:]))
    if op.startswith("border_t"):
        return _add_border(path, width=int(op.split("t")[1]), color="white")
    raise ValueError(f"Unknown op: {op}")


# ---------------------------------------------------------------------------
# Sessions, menus, delivery
# ---------------------------------------------------------------------------


def _new_session(path, chat, user, name, font, style, size):
    sid = uuid.uuid4().hex[:6]
    # Retire a previous session for this chat/user so leftover files don't pile up.
    for old_sid, sess in list(_SESSIONS.items()):
        if sess.get("chat") == chat and sess.get("user") == user and old_sid != sid:
            if sess.get("path") and sess["path"] != path:
                _cleanup(sess["path"])
            _SESSIONS.pop(old_sid, None)
            break
    _SESSIONS[sid] = {
        "path": path,
        "chat": chat,
        "user": user,
        "name": name,
        "font": font,
        "style": style or {},
        "size": size or _DEFAULT_SIZE,
    }
    return sid


def _caption(sess):
    return f"Logo by [{OWNER_NAME}](tg://user?id={OWNER_ID})"


def _menu_caption():
    return (
        "_Tap a button to modify your logo._\n"
        "• **BG / Gradient** regenerate the base\n"
        "• **Resize / Border / Rounded / Shadow / Add Text** apply on top"
    )


def _main_menu(sid):
    return ikm(
        [
            [
                ("📐 Resize", f"lg_resize_{sid}"),
                ("🎨 BG Color", f"lg_bg_{sid}"),
            ],
            [
                ("🟦 Border", f"lg_border_{sid}"),
                ("⬜ Rounded", f"lg_round_{sid}"),
            ],
            [
                ("🌒 Shadow", f"lg_shadow_{sid}"),
                ("🌈 Gradient", f"lg_grad_{sid}"),
            ],
            [
                Button.switch_inline(
                    "➕ Add Text", query=f"logotext_{sid} ", same_peer=True
                ),
                ("✅ Done", f"lg_done_{sid}", "success"),
            ],
        ]
    )


def _submenu(sid, op):
    back = [("‹ Back", f"lg_menu_{sid}", "danger")]
    if op == "resize":
        return "**Resize** — pick a size:", ikm(
            [
                [
                    ("Half (0.5x)", f"lg_resize_half_{sid}"),
                    ("Double (2x)", f"lg_resize_double_{sid}"),
                ],
                [
                    ("Square 512", f"lg_resize_512_{sid}"),
                    ("Square 1024", f"lg_resize_1024_{sid}"),
                ],
                back,
            ]
        )
    if op == "bg":
        return "**Background Color** — regenerates the logo base:", ikm(
            [
                [
                    ("⚫ Black", f"lg_bg_black_{sid}"),
                    ("⚪ White", f"lg_bg_white_{sid}"),
                ],
                [
                    ("🔴 Red", f"lg_bg_red_{sid}"),
                    ("🔵 Blue", f"lg_bg_blue_{sid}"),
                ],
                [
                    ("🟢 Green", f"lg_bg_green_{sid}"),
                    ("🟣 Purple", f"lg_bg_purple_{sid}"),
                ],
                [
                    ("🟠 Orange", f"lg_bg_orange_{sid}"),
                    ("🟦 Navy", f"lg_bg_navy_{sid}"),
                ],
                back,
            ]
        )
    if op == "border":
        return "**Border** (white) — pick thickness:", ikm(
            [
                [
                    ("Thin", f"lg_border_t2_{sid}"),
                    ("Medium", f"lg_border_t5_{sid}"),
                ],
                [("Thick", f"lg_border_t10_{sid}")],
                back,
            ]
        )
    if op == "grad":
        return "**Gradient Background** — regenerates the logo base:", ikm(
            [
                [
                    ("Sunset", f"lg_grad_0_{sid}"),
                    ("Purple", f"lg_grad_1_{sid}"),
                ],
                [
                    ("Ocean", f"lg_grad_2_{sid}"),
                    ("Neon", f"lg_grad_3_{sid}"),
                ],
                [
                    ("Candy", f"lg_grad_4_{sid}"),
                    ("Mint", f"lg_grad_5_{sid}"),
                ],
                back,
            ]
        )
    return _caption(_SESSIONS.get(sid, {})) + "\n\n" + _menu_caption(), _main_menu(sid)


async def _alert(event, msg):
    try:
        await event.answer(msg, alert=True)
    except Exception:
        pass


async def _post(event, sid, text, buttons, newpath=None):
    """Refresh the menu message in place (replace media only when newpath is
    given), else send a fresh assistant message + drop the old one (graceful
    degradation when the menu was delivered via the userbot inline fallback)."""
    sess = _SESSIONS.get(sid)
    if not sess:
        return await _alert(event, "Session expired. Run `.logo <text>` again.")
    try:
        if newpath:
            await event.edit(
                file=newpath, text=text, buttons=buttons, force_document=True
            )
        else:
            await event.edit(text=text, buttons=buttons)
        return
    except Exception as er:
        LOGS.debug(f"logo: in-place edit failed ({er}); sending fresh")
    path = newpath or sess["path"]
    try:
        await asst.send_file(
            sess["chat"], file=path, caption=text, buttons=buttons, force_document=True
        )
        try:
            await event.delete()
        except Exception:
            pass
    except Exception as er:
        LOGS.debug(f"logo: fresh menu send failed: {er}")
        await _alert(event, "Couldn't refresh here. Use the `.logo*` text commands.")


async def _resolve_target(event):
    """Return (path, owned) — the image to operate on. owned=True when we
    downloaded it and should clean it up."""
    if event.reply_to_msg_id:
        try:
            rep = await event.get_reply_message()
            if rep and rep.media and "pic" in fns.mediainfo(rep.media):
                p = await rep.download_media()
                if p:
                    return p, True
        except Exception as er:
            LOGS.debug(f"logo: reply target failed: {er}")
    own = other = None
    for sess in _SESSIONS.values():
        if sess.get("chat") != event.chat_id or not os.path.exists(
            sess.get("path", "")
        ):
            continue
        if sess.get("user") == event.sender_id:
            own = sess
        elif other is None:
            other = sess
    chosen = own or other
    if chosen:
        return chosen["path"], False
    return None, False


def _sess_meta_for(event):
    own = other = None
    for sess in _SESSIONS.values():
        if sess.get("chat") != event.chat_id:
            continue
        if sess.get("user") == event.sender_id:
            own = sess
        elif other is None:
            other = sess
    chosen = own or other
    if chosen:
        return (
            chosen.get("name"),
            chosen.get("font"),
            chosen.get("style", {}),
            chosen.get("size", _DEFAULT_SIZE),
        )
    return (
        None,
        _random_font(),
        {"fill": "white", "stroke_width": 5, "stroke_fill": "black"},
        _DEFAULT_SIZE,
    )


async def _deliver_logo(event, xx, logopath, caption, sid):
    """Deliver the logo + modification menu: bot-first → inline → text fallback."""
    menu_text = caption + "\n\n" + _menu_caption()
    menu = _main_menu(sid)

    # 1) bot-first — assistant posts the image with inline buttons directly
    if asst is not None:
        try:
            await asst.send_file(
                event.chat_id,
                file=logopath,
                caption=menu_text,
                buttons=menu,
                force_document=True,
            )
            try:
                await xx.delete()
            except Exception:
                pass
            return
        except Exception as er:
            LOGS.debug(f"logo: bot-first delivery failed: {er}")

    # 2) inline fallback — userbot sends the image, menu comes via inline query
    try:
        await event.client.send_file(
            event.chat_id, file=logopath, caption=caption, force_document=True
        )
    except Exception as er:
        LOGS.debug(f"logo: userbot image send failed: {er}")
    if asst is not None:
        try:
            await something(event, menu_text, None, menu)
            try:
                await xx.delete()
            except Exception:
                pass
            return
        except Exception as er:
            LOGS.debug(f"logo: inline menu delivery failed: {er}")

    # 3) last resort — text commands + DM hint
    try:
        await xx.delete()
    except Exception:
        pass
    await event.eor(
        caption + "\n\n_⚠️ Interactive buttons unavailable here._\n"
        "Modify with: `.logoresize`, `.logobg`, `.logoborder`, `.logoround`, "
        "`.logoshadow`, `.logograd`, `.logogtext`, `.logocurve`, "
        "`.logomulti`, `.logotext`.\n"
        "_Or run `.logo` in the assistant bot's DM for buttons._"
    )


async def _send_result(event, xx, outpath, name, font, style, size):
    """Create a session for a freshly produced logo and deliver it with a menu."""
    sid = _new_session(outpath, event.chat_id, event.sender_id, name, font, style, size)
    await _deliver_logo(event, xx, outpath, _caption(_SESSIONS[sid]), sid)


# ---------------------------------------------------------------------------
# .logo — base generator (existing behaviour preserved, bg fixed, menu added)
# ---------------------------------------------------------------------------


@ultroid_cmd(pattern="logo( (.*)|$)")
async def logo_gen(event):
    xx = await event.eor(get_string("com_1"))
    if Image is None:
        return await xx.eor("`Logo needs Pillow (PIL) installed.`", time=8)
    name = event.pattern_match.group(1).strip()
    if not name:
        return await xx.eor("`Give a name too!`", time=5)

    bg_, font_ = None, None
    if event.reply_to_msg_id:
        try:
            temp = await event.get_reply_message()
            if temp and temp.media:
                if hasattr(temp.media, "document") and (
                    ("font" in (temp.file.mime_type or ""))
                    or (".ttf" in (temp.file.name or ""))
                    or (".otf" in (temp.file.name or ""))
                ):
                    font_ = await temp.download_media("resources/fonts/")
                elif "pic" in fns.mediainfo(temp.media):
                    bg_ = await temp.download_media()
        except Exception as er:
            LOGS.debug(f"logo: reply parse failed: {er}")

    base_size = _DEFAULT_SIZE
    if not bg_:
        # unsplashsearch was removed in Phase 0 — generate a gradient bg
        # locally instead. Falls back to a solid colour if that fails.
        bg_ = fns.check_filename("logo_bg.png")
        try:
            _make_gradient_bg(bg_, base_size)
        except Exception as er:
            LOGS.debug(f"logo: gradient bg failed: {er}")
            try:
                _make_solid_bg(bg_, base_size, "navy")
            except Exception as er2:
                LOGS.exception(er2)
                return await xx.eor("`Failed to create a background.`", time=8)
    else:
        base_size = _img_size(bg_)

    if not font_:
        font_ = _random_font()
        if not font_:
            return await xx.eor("`No fonts found in resources/fonts/`", time=8)

    if len(name) <= 8:
        strke = 10
    elif len(name) >= 9:
        strke = 5
    else:
        strke = 20
    style = {"fill": "white", "stroke_width": strke, "stroke_fill": "black"}

    try:
        name_path = LogoHelper.make_logo(bg_, name, font_, **style)
    except Exception as er:
        LOGS.exception(er)
        # graceful fallback: plain text on a solid background
        try:
            _make_solid_bg(bg_, base_size, "navy")
            name_path = LogoHelper.make_logo(bg_, name, font_, **style)
        except Exception as er2:
            LOGS.exception(er2)
            return await xx.eor("`Failed to generate the logo. Try again.`", time=8)

    sid = _new_session(
        name_path, event.chat_id, event.sender_id, name, font_, style, base_size
    )
    await _deliver_logo(event, xx, name_path, _caption(_SESSIONS[sid]), sid)

    # the composite is now owned by the session; drop the temp background
    if bg_ and os.path.exists(bg_) and bg_ != name_path:
        _cleanup(bg_)


# ---------------------------------------------------------------------------
# Creative tools
# ---------------------------------------------------------------------------


def _split_text_preset(arg):
    """Split '<text> [preset]' — trailing integer becomes the preset."""
    parts = arg.split()
    if len(parts) > 1 and parts[-1].isdigit():
        return " ".join(parts[:-1]), int(parts[-1])
    return arg, None


@ultroid_cmd(pattern="logograd( (.*)|$)")
async def _logo_grad(event):
    xx = await event.eor(get_string("com_1"))
    if Image is None:
        return await xx.eor("`Pillow required.`", time=8)
    arg = (event.pattern_match.group(1) or "").strip()
    if not arg:
        return await xx.eor("`Usage: .logograd <text> [preset 0-5]`", time=8)
    text, preset = _split_text_preset(arg)
    if not text:
        return await xx.eor("`Give some text.`", time=8)
    font = _random_font()
    if not font:
        return await xx.eor("`No fonts found.`", time=8)
    colors = (
        _GRADIENTS[preset % len(_GRADIENTS)]
        if preset is not None
        else random.choice(_GRADIENTS)
    )
    bg = fns.check_filename("logo_bg.png")
    try:
        _make_gradient_bg(bg, _DEFAULT_SIZE, colors)
        strke = 10 if len(text) <= 8 else 5
        style = {"fill": "white", "stroke_width": strke, "stroke_fill": "black"}
        out = LogoHelper.make_logo(bg, text, font, **style)
    except Exception as er:
        LOGS.debug(er)
        _cleanup(bg)
        return await xx.eor(f"`Failed: {er}`", time=8)
    _cleanup(bg)
    await _send_result(event, xx, out, text, font, style, _DEFAULT_SIZE)


@ultroid_cmd(pattern="logogtext( (.*)|$)")
async def _logo_gtext(event):
    xx = await event.eor(get_string("com_1"))
    if Image is None:
        return await xx.eor("`Pillow required.`", time=8)
    arg = (event.pattern_match.group(1) or "").strip()
    if not arg:
        return await xx.eor("`Usage: .logogtext <text> [preset 0-5]`", time=8)
    text, preset = _split_text_preset(arg)
    if not text:
        return await xx.eor("`Give some text.`", time=8)
    font = _random_font()
    if not font:
        return await xx.eor("`No fonts found.`", time=8)
    colors = (
        _GRADIENTS[preset % len(_GRADIENTS)]
        if preset is not None
        else random.choice(_GRADIENTS)
    )
    bg = fns.check_filename("logo_bg.png")
    strke = 8 if len(text) <= 8 else 4
    style = {"fill": "white", "stroke_width": strke, "stroke_fill": "black"}
    try:
        _make_solid_bg(bg, _DEFAULT_SIZE, "black")
        out = _gradient_text_logo(
            bg, text, font, colors, stroke_width=strke, stroke_fill="black"
        )
    except Exception as er:
        LOGS.debug(er)
        _cleanup(bg)
        return await xx.eor(f"`Failed: {er}`", time=8)
    _cleanup(bg)
    await _send_result(event, xx, out, text, font, style, _DEFAULT_SIZE)


@ultroid_cmd(pattern="logocurve( (.*)|$)")
async def _logo_curve(event):
    xx = await event.eor(get_string("com_1"))
    if Image is None:
        return await xx.eor("`Pillow required.`", time=8)
    text = (event.pattern_match.group(1) or "").strip()
    if not text:
        return await xx.eor("`Usage: .logocurve <text>`", time=8)
    font = _random_font()
    if not font:
        return await xx.eor("`No fonts found.`", time=8)
    bg = fns.check_filename("logo_bg.png")
    style = {"fill": "white", "stroke_width": 3, "stroke_fill": "black"}
    try:
        _make_gradient_bg(bg, _DEFAULT_SIZE)
        out = _curve_text_logo(
            bg, text, font, fill="white", stroke_width=3, stroke_fill="black"
        )
    except Exception as er:
        LOGS.debug(er)
        _cleanup(bg)
        return await xx.eor(f"`Failed: {er}`", time=8)
    _cleanup(bg)
    await _send_result(event, xx, out, text, font, style, _DEFAULT_SIZE)


@ultroid_cmd(pattern="logomulti( (.*)|$)")
async def _logo_multi(event):
    xx = await event.eor(get_string("com_1"))
    if Image is None:
        return await xx.eor("`Pillow required.`", time=8)
    text = (event.pattern_match.group(1) or "").strip()
    if not text:
        return await xx.eor("`Usage: .logomulti line1 | line2 | line3`", time=8)
    font = _random_font()
    if not font:
        return await xx.eor("`No fonts found.`", time=8)
    bg = fns.check_filename("logo_bg.png")
    style = {"fill": "white", "stroke_width": 5, "stroke_fill": "black"}
    try:
        _make_gradient_bg(bg, _DEFAULT_SIZE)
        out = _multilayer_logo(bg, text, font, fill="white", stroke_fill="black")
    except Exception as er:
        LOGS.debug(er)
        _cleanup(bg)
        return await xx.eor(f"`Failed: {er}`", time=8)
    _cleanup(bg)
    await _send_result(event, xx, out, text, font, style, _DEFAULT_SIZE)


# ---------------------------------------------------------------------------
# Text-command fallbacks for the button-menu operations
# ---------------------------------------------------------------------------


@ultroid_cmd(pattern="logotext( (.*)|$)")
async def _logo_text(event):
    xx = await event.eor(get_string("com_1"))
    if Image is None:
        return await xx.eor("`Pillow required.`", time=8)
    text = (event.pattern_match.group(1) or "").strip()
    if not text:
        return await xx.eor("`Usage: .logotext <text> (reply to a logo/image)`", time=8)
    path, owned = await _resolve_target(event)
    if not path:
        return await xx.eor(
            "`Reply to a logo/image, or generate one with .logo first.`", time=8
        )
    font = _random_font() or _sess_meta_for(event)[1]
    style = {"fill": "white", "stroke_width": 5, "stroke_fill": "black"}
    try:
        out = _add_text_to_image(path, text, font, **style)
    except Exception as er:
        LOGS.debug(er)
        return await xx.eor(f"`Failed: {er}`", time=8)
    await _send_result(event, xx, out, text, font, style, _img_size(out))
    if owned:
        _cleanup(path)


@ultroid_cmd(pattern="logoresize( (.*)|$)")
async def _logo_resize(event):
    xx = await event.eor(get_string("com_1"))
    if Image is None:
        return await xx.eor("`Pillow required.`", time=8)
    spec = (event.pattern_match.group(1) or "").strip()
    if not spec:
        return await xx.eor("`Usage: .logoresize <512|1024|2x|0.5x|WxH>`", time=8)
    path, owned = await _resolve_target(event)
    if not path:
        return await xx.eor(
            "`Reply to a logo (or generate one with .logo first).`", time=8
        )
    try:
        low = spec.lower()
        if low.endswith("x") and low[:-1].replace(".", "", 1).isdigit():
            out = _resize_logo(path, scale=float(low[:-1]))
        elif "x" in low and low.replace("x", "", 1).isdigit():
            w_, h_ = low.split("x")
            out = _resize_logo(path, size=(int(w_), int(h_)))
        elif spec.isdigit():
            d = int(spec)
            out = _resize_logo(path, size=(d, d))
        else:
            return await xx.eor(
                "`Invalid size. Use 512, 1024, 2x, 0.5x, or WxH.`", time=8
            )
    except Exception as er:
        LOGS.debug(er)
        return await xx.eor(f"`Failed: {er}`", time=8)
    name, font, style, size = _sess_meta_for(event)
    await _send_result(event, xx, out, name, font, style, size)
    if owned:
        _cleanup(path)


@ultroid_cmd(pattern="logobg( (.*)|$)")
async def _logo_bg(event):
    xx = await event.eor(get_string("com_1"))
    if Image is None:
        return await xx.eor("`Pillow required.`", time=8)
    color = (event.pattern_match.group(1) or "").strip() or "navy"
    if color.lower() not in _BG_COLORS:
        return await xx.eor(
            "`Unknown colour. Pick: " + ", ".join(_BG_COLORS) + "`", time=8
        )
    path, owned = await _resolve_target(event)
    if not path:
        return await xx.eor("`Reply to a logo (or .logo first).`", time=8)
    name, font, style, size = _sess_meta_for(event)
    if not name:
        return await xx.eor(
            "`.logobg` regenerates the base with your text. Generate with "
            "`.logo <text>` first, then `.logobg <color>`.",
            time=10,
        )
    try:
        out = _regen_bg(
            {"path": path, "name": name, "font": font, "style": style}, solid=color
        )
    except Exception as er:
        LOGS.debug(er)
        return await xx.eor(f"`Failed: {er}`", time=8)
    await _send_result(event, xx, out, name, font, style, size)
    if owned:
        _cleanup(path)


@ultroid_cmd(pattern="logoborder( (.*)|$)")
async def _logo_border(event):
    xx = await event.eor(get_string("com_1"))
    if Image is None:
        return await xx.eor("`Pillow required.`", time=8)
    arg = (event.pattern_match.group(1) or "").strip()
    width = int(arg) if arg.isdigit() else 10
    path, owned = await _resolve_target(event)
    if not path:
        return await xx.eor("`Reply to a logo (or .logo first).`", time=8)
    try:
        out = _add_border(path, width=width, color="white")
    except Exception as er:
        LOGS.debug(er)
        return await xx.eor(f"`Failed: {er}`", time=8)
    name, font, style, size = _sess_meta_for(event)
    await _send_result(event, xx, out, name, font, style, size)
    if owned:
        _cleanup(path)


@ultroid_cmd(pattern="logoround( (.*)|$)")
async def _logo_round(event):
    xx = await event.eor(get_string("com_1"))
    if Image is None:
        return await xx.eor("`Pillow required.`", time=8)
    arg = (event.pattern_match.group(1) or "").strip()
    radius = int(arg) if arg.isdigit() else _ROUND_RADIUS
    path, owned = await _resolve_target(event)
    if not path:
        return await xx.eor("`Reply to a logo (or .logo first).`", time=8)
    try:
        out = _round_corners(path, radius=radius)
    except Exception as er:
        LOGS.debug(er)
        return await xx.eor(f"`Failed: {er}`", time=8)
    name, font, style, size = _sess_meta_for(event)
    await _send_result(event, xx, out, name, font, style, size)
    if owned:
        _cleanup(path)


@ultroid_cmd(pattern="logoshadow( (.*)|$)")
async def _logo_shadow(event):
    xx = await event.eor(get_string("com_1"))
    if Image is None:
        return await xx.eor("`Pillow required.`", time=8)
    path, owned = await _resolve_target(event)
    if not path:
        return await xx.eor("`Reply to a logo (or .logo first).`", time=8)
    try:
        out = _add_shadow(path)
    except Exception as er:
        LOGS.debug(er)
        return await xx.eor(f"`Failed: {er}`", time=8)
    name, font, style, size = _sess_meta_for(event)
    await _send_result(event, xx, out, name, font, style, size)
    if owned:
        _cleanup(path)


# ---------------------------------------------------------------------------
# Button-menu callbacks + inline "add text" (assistant-bot only)
# ---------------------------------------------------------------------------


if asst is not None:

    @callback(data=re.compile(rb"^lg_"), owner=True)
    async def _logo_menu_cb(event):
        raw = event.data.decode("utf-8")
        parts = raw.split("_")
        if parts[0] != "lg" or len(parts) < 3:
            return
        sid = parts[-1]
        op = "_".join(parts[1:-1])
        sess = _SESSIONS.get(sid)

        if op == "done":
            if sess:
                _cleanup(sess.get("path"))
                _SESSIONS.pop(sid, None)
            try:
                await event.edit("✅ **Logo editing done.**", buttons=None)
            except Exception:
                await _alert(event, "Done!")
            return

        if op == "text":
            return await _alert(
                event,
                "✍️ Type your text inline (Add Text button), or reply to the logo "
                "with `.logotext <text>`.",
            )

        if not sess:
            return await _alert(event, "Session expired. Run `.logo <text>` again.")

        if op in ("resize", "bg", "border", "grad"):
            text, btns = _submenu(sid, op)
            return await _post(event, sid, text, btns)

        if op == "menu":
            return await _post(
                event, sid, _caption(sess) + "\n\n" + _menu_caption(), _main_menu(sid)
            )

        # apply ops
        try:
            newpath = _apply_op(sess, op)
        except Exception as er:
            LOGS.debug(f"logo: apply {op} failed: {er}")
            return await _alert(event, f"⚠️ Failed: {er}")
        old = sess["path"]
        sess["path"] = newpath
        _cleanup(old)
        await _post(
            event,
            sid,
            _caption(sess) + "\n\n" + _menu_caption(),
            _main_menu(sid),
            newpath=newpath,
        )

    @in_pattern(r"logotext_(\S+) (.*)", owner=True)
    async def _inline_add_text(event):
        sid = event.pattern_match.group(1)
        text = (event.pattern_match.group(2) or "").strip()
        if not text:
            return await event.answer(
                [
                    await event.builder.article(
                        title="Add Text", text="Type your text after the query."
                    )
                ]
            )
        sess = _SESSIONS.get(sid)
        if not sess:
            return await event.answer(
                [
                    await event.builder.article(
                        title="Session expired", text="Run `.logo <text>` again."
                    )
                ]
            )
        try:
            newpath = _add_text_to_image(
                sess["path"], text, sess["font"], **sess.get("style", {})
            )
        except Exception as er:
            LOGS.debug(er)
            return await event.answer(
                [await event.builder.article(title="Failed", text=str(er))]
            )
        _cleanup(sess["path"])
        sess["path"] = newpath

        cap = _caption(sess) + "\n\n" + _menu_caption()
        menu = _main_menu(sid)
        delivered = False
        if asst is not None:
            try:
                await asst.send_file(
                    sess["chat"],
                    file=newpath,
                    caption=cap,
                    buttons=menu,
                    force_document=True,
                )
                delivered = True
            except Exception as er:
                LOGS.debug(f"logo: inline add-text bot send failed: {er}")
        if not delivered:
            try:
                await ultroid_bot.send_file(sess["chat"], file=newpath, caption=cap)
                delivered = True
            except Exception as er:
                LOGS.debug(er)
        title = "✅ Logo updated — sent to chat" if delivered else "⚠️ Couldn't send"
        body = (
            "The updated logo was sent to the chat."
            if delivered
            else "Reply to the logo with `.logotext <text>`."
        )
        await event.answer([await event.builder.article(title=title, text=body)])

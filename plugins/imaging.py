"""
Basic transforms:
»   .grey / .grayscale     — convert to grayscale
»   .invert                — invert colours
»   .blur [radius]         — gaussian blur
»   .sharpen [factor]      — sharpen
»   .rotate <degrees>      — rotate image
»   .flip                  — flip horizontal
»   .flop                  — flip vertical
»   .mirror                — alias for flip

Effects:
»   .sketch                — pencil sketch
»   .cartoon / .toon       — cartoon effect
»   .danger                — red channel boost
»   .bluec                 — blue channel boost
»   .vintage               — sepia/vintage look
»   .glitch [intensity]    — RGB glitch effect
»   .emboss                — emboss effect
»   .edges                 — edge detection
»   .pixelate [size]       — pixelation
»   .vignette              — dark vignette frame

Adjustments:
»   .brightness <factor>   — 0.5 = darker, 2.0 = brighter
»   .contrast <factor>     — adjust contrast
»   .saturation <factor>   — adjust colour saturation

Composite / text:
»   .caption <text>        — add caption to image
»   .meme <top> | <bottom> — meme text overlay
»   .watermark <text>      — tiled watermark
»   .border [color] [width]— add border

Generate:
»   .csample <color>       — generate colour swatch
»   .codeimg               — render replied code block as image
»   .textimg <text>        — render text as styled quote card
»   .errcard               — render last error as error card
»   .statcard              — render bot trace stats as card

Info:
»   .imginfo               — show image info (size, mode, format)
"""


import os
import time
from io import BytesIO
from typing import Optional

from . import ultroid_cmd, eod, eor
from pyUltroid.imaging import ImageResult, Imaging

imaging = Imaging()

__requires__ = ["pillow>=10.0"]
__optional__ = ["opencv-python", "numpy"]
__res_tags__ = ["image", "media", "creative"]


# ── Download helper ────────────────────────────────────────────────────────


async def _get_image(event) -> ImageResult | None:
    """
    Get image from replied message or current message.
    Returns ImageResult or None with error already sent.
    """
    result = await imaging.load_tg(event)
    if not result.ok:
        await eod(
            event, f"❌ `{result.error}`\nReply to a photo, sticker, or image file."
        )
        return None
    return result


async def _send_image(event, msg, result: ImageResult, fmt: str = "PNG") -> None:
    """Send ImageResult back to Telegram, editing the status message."""
    if not result.ok:
        await msg.edit(f"❌ `{result.error}`")
        return
    buf = result.to_bytesio(fmt, f"output.{fmt.lower()}")
    if buf is None:
        await msg.edit("❌ Failed to encode image output.")
        return
    ops = " → ".join(result.op_chain) if result.op_chain else "processed"
    await msg.delete()
    await event.client.send_file(
        event.chat_id,
        buf,
        caption=f"`{ops}` · `{result.width}×{result.height}` · `{result.duration_ms:.0f}ms`",
        reply_to=event.id,
    )


# ── Basic transforms ───────────────────────────────────────────────────────


@ultroid_cmd(pattern=r"(grey|grayscale)$")
async def grey_cmd(event):
    msg = await eor(event, "`🎨 converting to grayscale…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.grayscale(res))


@ultroid_cmd(pattern=r"invert$")
async def invert_cmd(event):
    msg = await eor(event, "`🎨 inverting…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.invert(res))


@ultroid_cmd(pattern=r"blur(?: |$)(\d*\.?\d*)")
async def blur_cmd(event):
    radius = float(event.pattern_match.group(1) or 5)
    msg = await eor(event, f"`🌀 blurring (r={radius})…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.blur(res, radius))


@ultroid_cmd(pattern=r"sharpen(?: |$)(\d*\.?\d*)")
async def sharpen_cmd(event):
    factor = float(event.pattern_match.group(1) or 2.0)
    msg = await eor(event, f"`✨ sharpening (f={factor})…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.sharpen(res, factor))


@ultroid_cmd(pattern=r"rotate(?: |$)(-?\d+\.?\d*)")
async def rotate_cmd(event):
    degrees = float(event.pattern_match.group(1) or 90)
    msg = await eor(event, f"`🔄 rotating {degrees}°…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.rotate(res, degrees))


@ultroid_cmd(pattern=r"(flip|mirror)$")
async def flip_cmd(event):
    msg = await eor(event, "`↔️ flipping…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.flip(res, horizontal=True))


@ultroid_cmd(pattern=r"flop$")
async def flop_cmd(event):
    msg = await eor(event, "`↕️ flopping…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.flip(res, vertical=True))


# ── Effects ────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern=r"sketch$")
async def sketch_cmd(event):
    msg = await eor(event, "`✏️ sketching…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.sketch(res))


@ultroid_cmd(pattern=r"(cartoon|toon)$")
async def cartoon_cmd(event):
    msg = await eor(event, "`🎨 cartooning…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.cartoon(res))


@ultroid_cmd(pattern=r"danger$")
async def danger_cmd(event):
    msg = await eor(event, "`🔴 danger mode…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.danger(res))


@ultroid_cmd(pattern=r"bluec$")
async def blue_cmd(event):
    msg = await eor(event, "`🔵 blue channel…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.blue_channel(res))


@ultroid_cmd(pattern=r"vintage$")
async def vintage_cmd(event):
    msg = await eor(event, "`📷 vintage filter…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.vintage(res))


@ultroid_cmd(pattern=r"glitch(?: |$)(\d*)")
async def glitch_cmd(event):
    intensity = int(event.pattern_match.group(1) or 10)
    intensity = max(1, min(intensity, 50))
    msg = await eor(event, f"`⚡ glitching (i={intensity})…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.glitch(res, intensity))


@ultroid_cmd(pattern=r"emboss$")
async def emboss_cmd(event):
    msg = await eor(event, "`🪨 embossing…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.emboss(res))


@ultroid_cmd(pattern=r"edges$")
async def edges_cmd(event):
    msg = await eor(event, "`🔍 detecting edges…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.edge_detect(res))


@ultroid_cmd(pattern=r"pixelate(?: |$)(\d*)")
async def pixelate_cmd(event):
    size = int(event.pattern_match.group(1) or 20)
    size = max(2, min(size, 100))
    msg = await eor(event, f"`🟦 pixelating (s={size})…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.pixelate(res, size))


@ultroid_cmd(pattern=r"vignette$")
async def vignette_cmd(event):
    msg = await eor(event, "`🌑 adding vignette…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.vignette(res))


# ── Adjustments ────────────────────────────────────────────────────────────


@ultroid_cmd(pattern=r"brightness(?: |$)(\d*\.?\d*)")
async def brightness_cmd(event):
    factor = float(event.pattern_match.group(1) or 1.5)
    factor = max(0.1, min(factor, 5.0))
    msg = await eor(event, f"`☀️ brightness ({factor})…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.brightness(res, factor))


@ultroid_cmd(pattern=r"contrast(?: |$)(\d*\.?\d*)")
async def contrast_cmd(event):
    factor = float(event.pattern_match.group(1) or 1.5)
    factor = max(0.1, min(factor, 5.0))
    msg = await eor(event, f"`🎚 contrast ({factor})…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.contrast(res, factor))


@ultroid_cmd(pattern=r"saturation(?: |$)(\d*\.?\d*)")
async def saturation_cmd(event):
    factor = float(event.pattern_match.group(1) or 1.5)
    factor = max(0.0, min(factor, 5.0))
    msg = await eor(event, f"`🎨 saturation ({factor})…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.saturation(res, factor))


# ── Composite / text ───────────────────────────────────────────────────────


@ultroid_cmd(pattern=r"caption(?: |$)([\s\S]+)")
async def caption_cmd(event):
    text = event.pattern_match.group(1).strip()
    if not text:
        return await eod(event, "`Usage: .caption <text>`")
    # position prefix: top: | bottom: | center:
    position = "bottom"
    for pos in ("top", "bottom", "center"):
        if text.lower().startswith(f"{pos}:"):
            position = pos
            text = text[len(pos) + 1 :].strip()
            break
    msg = await eor(event, "`✍️ captioning…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.caption(res, text, position=position))


@ultroid_cmd(pattern=r"meme(?: |$)([\s\S]*)")
async def meme_cmd(event):
    """
    .meme top text | bottom text
    Reply to an image. Separate top and bottom with |.
    """
    arg = event.pattern_match.group(1).strip()
    parts = arg.split("|", 1)
    top = parts[0].strip() if parts else ""
    bot = parts[1].strip() if len(parts) > 1 else ""
    if not top and not bot:
        return await eod(event, "`Usage: .meme top text | bottom text`")
    msg = await eor(event, "`😂 meme-ing…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.meme(res, top_text=top, bottom_text=bot))


@ultroid_cmd(pattern=r"watermark(?: |$)([\s\S]+)")
async def watermark_cmd(event):
    text = event.pattern_match.group(1).strip()
    if not text:
        return await eod(event, "`Usage: .watermark <text>`")
    msg = await eor(event, "`💧 watermarking…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.watermark(res, text))


@ultroid_cmd(pattern=r"border(?: |$)(.*)")
async def border_cmd(event):
    """
    .border [color] [width]
    Example: .border white 20   or   .border #ff0000 15
    """
    arg = event.pattern_match.group(1).strip()
    parts = arg.split()
    color = parts[0] if parts else "white"
    width = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 20
    width = max(1, min(width, 100))
    msg = await eor(event, "`🖼 adding border…`")
    res = await _get_image(event)
    if res:
        await _send_image(event, msg, imaging.border(res, color=color, width=width))


# ── Generate ───────────────────────────────────────────────────────────────


@ultroid_cmd(pattern=r"csample(?: |$)(.*)")
async def csample_cmd(event):
    """
    .csample <color>
    Generate a colour swatch. Accepts colour names or hex codes.
    """
    color = event.pattern_match.group(1).strip()
    if not color:
        return await eod(event, "`Usage: .csample red` or `.csample #ff6600`")
    msg = await eor(event, f"`🎨 sampling {color}…`")
    res = imaging.color_sample(color)
    if not res.ok:
        return await msg.edit(f"❌ Unknown colour: `{color}`")
    await _send_image(event, msg, res)


@ultroid_cmd(pattern=r"codeimg$")
async def codeimg_cmd(event):
    """
    .codeimg
    Reply to a message containing code. Renders it as a styled image.
    """
    reply = await event.get_reply_message()
    if not reply or not reply.text:
        return await eod(event, "`Reply to a message containing code.`")
    msg = await eor(event, "`💻 rendering code…`")
    code = reply.text.strip().strip("`")
    # Detect language hint from first line
    lang = ""
    if code.startswith("python") or "def " in code or "import " in code:
        lang = "python"
    res = imaging.code_image(code, language=lang, title="code")
    await _send_image(event, msg, res)


@ultroid_cmd(pattern=r"textimg(?: |$)([\s\S]+)")
async def textimg_cmd(event):
    """
    .textimg <text>
    Render text as a styled quote card image.
    """
    text = event.pattern_match.group(1).strip()
    if not text:
        return await eod(event, "`Usage: .textimg <your text>`")
    msg = await eor(event, "`✍️ rendering text…`")
    res = imaging.text_image(text)
    await _send_image(event, msg, res)


@ultroid_cmd(pattern=r"errcard$", owner_only=True)
async def errcard_cmd(event):
    """
    .errcard
    Render the most recent trace failure as a visual error card.
    """
    msg = await eor(event, "`🔍 fetching last error…`")
    try:
        from pyUltroid.tracer import tr

        fails = await tr.live.failures(n=1)
        if not fails:
            return await msg.edit("No recent failures in trace history.")
        t = fails[0]
        res = imaging.error_card(
            title=f"{t.exc_type or 'Error'}",
            message=(t.exception or "unknown").splitlines()[-1][:200],
            tb=t.exception,
            plugin=f"{t.plugin}.{t.handler}",
            request_id=t.trace_id,
        )
        await _send_image(event, msg, res)
    except Exception as e:
        await msg.edit(f"❌ `{e}`")


@ultroid_cmd(pattern=r"statcard$", owner_only=True)
async def statcard_cmd(event):
    """
    .statcard
    Render bot trace statistics as a visual card.
    """
    msg = await eor(event, "`📊 building stat card…`")
    try:
        from pyUltroid.tracer import tr

        stats = await tr.live.stats()
        metrics = {
            "Total traces": stats.get("total", 0),
            "Failures": stats.get("failures", 0),
            "Success rate": stats.get("success_rate", "N/A"),
            "Avg duration": f"{stats.get('avg_duration_ms', 0)}ms",
        }
        top = stats.get("top_plugins", [])
        for i, (p, c) in enumerate(top[:3], 1):
            metrics[f"#{i} plugin"] = f"{p} ({c})"
        res = imaging.stat_card("📊 Trace Statistics", metrics)
        await _send_image(event, msg, res)
    except Exception as e:
        await msg.edit(f"❌ `{e}`")


# ── Info ───────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern=r"imginfo$")
async def imginfo_cmd(event):
    """
    .imginfo
    Show information about a replied image.
    """
    msg = await eor(event, "`🔍 reading image…`")
    res = await _get_image(event)
    if not res:
        return
    text = (
        f"**🖼 Image Info**\n\n"
        f"Size:    `{res.width} × {res.height}`\n"
        f"Mode:    `{res.mode}`\n"
        f"Format:  `{res.format}`\n"
        f"Loaded in: `{res.duration_ms:.0f}ms`"
    )
    await msg.edit(text)

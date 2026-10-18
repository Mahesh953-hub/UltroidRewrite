from pyUltroid.fns import fns

# Ultroid - UserBot
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}randomuser`
   Generate details about a random user.

• `{i}ascii <text>`
    Convert text into big ASCII art using built-in fonts/styles.

• `{i}ascii -f <font> <text>`
    Render text art with a specific font.
    Fonts: block, mono, serif, slant, condensed, thin

• `{i}ascii -ls`
    List all available ASCII fonts.

• `{i}ascii <reply image>`
    Convert a replied image into ASCII art (rendered as a photo).

• `{i}ascii <reply image> -w <width>`
    Set the target width (in characters) for image art.

• `{i}ascii <reply image> --color`
    Preserve the original colours in the rendered ASCII image.

• `{i}ascii <reply image> --text`
    Send the ASCII art as raw text instead of a rendered photo.

Other flags (work in both modes): `-i/--invert`, `-c/--chars <ramp>`,
`-w/--width <n>`.

• `{i}spoil <text>`
    Send text hidden behind a spoiler (tap-to-reveal).

• `{i}spoil` (reply)
    Re-send the replied message's text as a spoiler.

• `{i}react <emoji...>` (reply)
    React to the replied message with one or more emoji.

• `{i}react` (reply, no args)
    List the current reactions on the replied message.

• `{i}effects`
    List available message effects (id + emoticon).

• `{i}effect <id> <text>`
    Send `<text>` with the message effect `<id>` (see `{i}effects`).
    Some effects require Telegram Premium on this account.

• `{i}loclive <lat>,<long> <seconds> [note]`
    Start sharing a live location for `<seconds>`.

• `{i}loclive <seconds> [note]` (reply to a location)
    Start sharing a live location re-using the replied location's coords.

• `{i}stoplive` (reply to your live-location message)
    Stop an active live location.

• `{i}qr <shortcut> <text>`
    Create/append to a quick-reply shortcut (saved in your Saved Messages).

• `{i}qr <shortcut>` (reply)
    Save the replied message's text into the quick-reply shortcut.

• `{i}qrlist`
    List saved quick-reply shortcuts.

• `{i}qrsend <shortcut>`
    Send a saved quick-reply shortcut's messages to the current chat.
"""

import os
import shlex
import tempfile

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:
    Image = ImageDraw = ImageFont = ImageOps = None

from telethon.errors import RPCError
from telethon.errors.rpcerrorlist import (
    PremiumAccountRequiredError,
    ReactionEmptyError,
    ReactionInvalidError,
)
from telethon.helpers import generate_random_long
from telethon.tl.functions.messages import (
    EditMessageRequest,
    GetAvailableEffectsRequest,
    GetQuickRepliesRequest,
    GetQuickReplyMessagesRequest,
    SendQuickReplyMessagesRequest,
)
from telethon.tl.types import (
    InputGeoPoint,
    InputGeoPointEmpty,
    InputMediaGeoLive,
    InputQuickReplyShortcut,
    MessageEntityCustomEmoji,
    MessageMediaGeoLive,
    ReactionCustomEmoji,
)

from custom.entities import spoiler as _build_spoiler

from . import get_string, ultroid_cmd

# ──────────────────────────────────────────────────────────────────────
# ASCII art engine
# ──────────────────────────────────────────────────────────────────────
# Pillow-backed renderer. Replaces the old img2html + htmlwebshot combo
# (neither of which is installed in this environment) with a robust,
# dependency-light implementation that also supports text-to-ASCII art
# using several real system fonts as "styles".

# Brightness ramp, dark → light. Chosen for good tonal separation.
_ASCII_RAMP = " .:-=+*#%@"

# Each "font" is a ranked list of candidate TTF/OTF basenames. The first
# file found on the system is used, so the plugin stays portable across
# Linux/macOS/Windows without hard-coding a single path.
_FONT_CANDIDATES = {
    "block": [
        "DejaVuSans-Bold.ttf",
        "LiberationSans-Bold.ttf",
        "Arial Bold.ttf",
        "arialbd.ttf",
    ],
    "mono": [
        "DejaVuSansMono-Bold.ttf",
        "LiberationMono-Bold.ttf",
        "Courier New Bold.ttf",
        "courbd.ttf",
    ],
    "serif": [
        "DejaVuSerif-Bold.ttf",
        "LiberationSerif-Bold.ttf",
        "Times New Roman Bold.ttf",
        "timesbd.ttf",
    ],
    "slant": [
        "DejaVuSans-Oblique.ttf",
        "LiberationSans-Italic.ttf",
        "Arial Italic.ttf",
        "ariali.ttf",
    ],
    "condensed": [
        "DejaVuSansCondensed-Bold.ttf",
        "LiberationSansNarrow-Bold.ttf",
    ],
    "thin": [
        "DejaVuSans-ExtraLight.ttf",
        "LiberationSans-Regular.ttf",
        "Arial.ttf",
        "arial.ttf",
    ],
}
_DEFAULT_FONT = "block"

# Directories scanned (once) for font files.
_FONT_DIRS = [
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    os.path.expanduser("~/.fonts"),
    os.path.expanduser("~/.local/share/fonts"),
    "C:/Windows/Fonts",
    "/System/Library/Fonts",
    "/Library/Fonts",
]

_FONT_INDEX = None

_ASCII_HELP = (
    "`Usage:`\n"
    "`• .ascii <text>` — text art (default font: block)\n"
    "`• .ascii -f <font> <text>` — choose a font\n"
    "`• .ascii -ls` — list fonts\n"
    "`• .ascii <reply image>` — image to ASCII art\n"
    "`Flags:` -f -w -i/--invert -c/--chars <ramp> --color --text"
)


def _font_index():
    """Build (once) a {basename: path} map of every font on disk."""
    global _FONT_INDEX
    if _FONT_INDEX is None:
        index = {}
        for directory in _FONT_DIRS:
            if not os.path.isdir(directory):
                continue
            for root, _dirs, files in os.walk(directory):
                for name in files:
                    low = name.lower()
                    if low.endswith((".ttf", ".otf", ".ttc")) and name not in index:
                        index[name] = os.path.join(root, name)
        _FONT_INDEX = index
    return _FONT_INDEX


def _load_truetype(font_name, size):
    """Return a PIL font for *font_name* at *size*, with a safe fallback."""
    candidates = _FONT_CANDIDATES.get(font_name) or _FONT_CANDIDATES[_DEFAULT_FONT]
    for name in candidates:
        path = _font_index().get(name)
        if path:
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # Last resort: Pillow's built-in bitmap font (always available).
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _image_to_ascii(img, width=80, ramp=_ASCII_RAMP, invert=False):
    """Convert a PIL image into a list of ASCII strings."""
    img = img.convert("L")
    img = ImageOps.autocontrast(img)
    ratio = img.height / img.width
    # Characters are roughly twice as tall as they are wide.
    new_h = max(1, int(width * ratio / 2))
    img = img.resize((width, new_h))
    pixels = img.load()
    ramp = ramp[::-1] if invert else ramp
    n = len(ramp) - 1
    lines = []
    for y in range(new_h):
        row = [ramp[n * pixels[x, y] // 255] for x in range(width)]
        lines.append("".join(row))
    return lines


def _text_to_ascii(text, font_name=_DEFAULT_FONT, width=80, invert=False):
    """Render *text* with a real font, then convert the bitmap to ASCII."""
    # Probe at size 100 to measure aspect, then scale so the bitmap is
    # roughly 8px per output character for crisp down-sampling.
    probe = _load_truetype(font_name, 100)
    probe_img = Image.new("L", (10, 10))
    bbox = ImageDraw.Draw(probe_img).textbbox((0, 0), text, font=probe)
    text_w = max(1, bbox[2] - bbox[0])
    target_px = max(64, width * 8)
    size = max(24, min(300, int(100 * target_px / text_w)))

    font = _load_truetype(font_name, size)
    canvas = Image.new("L", (10, 10))
    draw = ImageDraw.Draw(canvas)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad = size
    canvas = Image.new("L", (tw + 2 * pad, th + 2 * pad), 0)
    draw = ImageDraw.Draw(canvas)
    draw.text((pad - bbox[0], pad - bbox[1]), text, fill=255, font=font)
    content = canvas.getbbox()
    if content:
        canvas = canvas.crop(content)
    return _image_to_ascii(canvas, width=width, invert=invert)


def _ascii_to_image(lines, color_img=None, ramp=_ASCII_RAMP):
    """Render ASCII *lines* back into a crisp PIL image (optionally coloured)."""
    if not lines:
        return None
    font = _load_truetype("mono", 16)
    bbox = font.getbbox("M")
    char_w = max(6, bbox[2] - bbox[0])
    char_h = max(10, (bbox[3] - bbox[1]) + 5)
    cols = max(len(line) for line in lines)
    rows = len(lines)
    out = Image.new("RGB", (cols * char_w, rows * char_h), (12, 12, 12))
    draw = ImageDraw.Draw(out)

    color_px = None
    if color_img is not None:
        color_img = color_img.convert("RGB").resize((cols, rows))
        color_px = color_img.load()

    levels = max(1, len(ramp) - 1)
    for ry, line in enumerate(lines):
        for cx, ch in enumerate(line):
            if ch == " ":
                continue
            if color_px is not None:
                try:
                    color = color_px[cx, ry]
                except IndexError:
                    color = (235, 235, 235)
            else:
                lvl = ramp.find(ch)
                grey = int(45 + lvl * 210 // levels) if lvl >= 0 else 235
                color = (grey, grey, grey)
            draw.text((cx * char_w, ry * char_h), ch, fill=color, font=font)
    return out


def _save_temp(pil_img, suffix=".png"):
    """Save a PIL image to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    pil_img.save(path)
    return path


def _parse_ascii_args(arg):
    """Parse the raw `.ascii` argument string into an options dict."""
    opts = {
        "font": _DEFAULT_FONT,
        "width": 80,
        "invert": False,
        "color": False,
        "as_text": False,
        "ramp": _ASCII_RAMP,
        "text": "",
        "list_fonts": False,
    }
    try:
        tokens = shlex.split(arg)
    except ValueError as exc:
        raise ValueError(f"Bad quotes: {exc}")
    positional = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-ls", "--list", "--fonts"):
            opts["list_fonts"] = True
        elif tok in ("-f", "--font") and i + 1 < len(tokens):
            opts["font"] = tokens[i + 1]
            i += 1
        elif tok in ("-w", "--width") and i + 1 < len(tokens):
            try:
                opts["width"] = int(tokens[i + 1])
            except ValueError:
                raise ValueError("Width must be a number.")
            i += 1
        elif tok in ("-c", "--chars", "--ramp") and i + 1 < len(tokens):
            ramp = tokens[i + 1]
            if len(ramp) < 2:
                raise ValueError("Char ramp needs at least 2 characters.")
            opts["ramp"] = ramp
            i += 1
        elif tok in ("-i", "--invert"):
            opts["invert"] = True
        elif tok == "--color":
            opts["color"] = True
        elif tok == "--text":
            opts["as_text"] = True
        elif tok.startswith("-") and len(tok) > 1:
            raise ValueError(f"Unknown flag: {tok}")
        else:
            positional.append(tok)
        i += 1
    opts["text"] = " ".join(positional)
    return opts


async def _send_ascii_text(event, status_msg, art):
    """Send ASCII art as a code block, falling back to a photo when too large."""
    if len(art) <= 3800 and art.count("\n") <= 200:
        try:
            await status_msg.edit(f"```{art}```")
            return
        except Exception:
            pass  # fall through to image rendering
    out = _ascii_to_image(art.split("\n"))
    if out is None:
        await status_msg.edit("`Nothing to render.`")
        return
    path = _save_temp(out)
    try:
        await status_msg.delete()
        await event.reply(file=path, caption="`🎨 ASCII art (too large for text)`")
    finally:
        if os.path.exists(path):
            os.remove(path)


@ultroid_cmd(pattern="randomuser")
async def _gen_data(event):
    x = await event.eor(get_string("com_1"))
    msg, pic = await fns.get_random_user_data()
    await event.reply(file=pic, message=msg)
    await x.delete()


@ultroid_cmd(
    pattern="ascii( (.*)|$)",
)
async def ascii_cmd(e):
    if Image is None:
        return await e.eor("`Pillow is not installed.`")
    arg = (e.pattern_match.group(1) or "").strip()
    try:
        opts = _parse_ascii_args(arg)
    except ValueError as exc:
        return await e.eor(f"`{exc}`", time=6)

    if opts["list_fonts"]:
        fonts = "\n".join(f"• `{name}`" for name in _FONT_CANDIDATES)
        return await e.eor(f"**Available ASCII fonts:**\n{fonts}")

    width = max(20, min(opts["width"], 200))
    invert = opts["invert"]
    ramp = opts["ramp"]

    # ── image mode: convert a replied photo/sticker to ASCII ──────────
    if e.reply_to_msg_id:
        m = await e.eor(get_string("com_1"))
        reply = await e.get_reply_message()
        path = await reply.download_media()
        if not path or not os.path.exists(path):
            return await m.edit("`Reply to an image to convert.`")
        try:
            img = Image.open(path)
            img = ImageOps.exif_transpose(img)
            img.load()
            lines = _image_to_ascii(img, width=width, ramp=ramp, invert=invert)
        except Exception as exc:
            if os.path.exists(path):
                os.remove(path)
            return await m.edit(f"`Cannot read image: {exc}`")
        art = "\n".join(lines)
        if opts["as_text"]:
            if os.path.exists(path):
                os.remove(path)
            await _send_ascii_text(e, m, art)
            return
        out = _ascii_to_image(
            lines, color_img=img if opts["color"] else None, ramp=ramp
        )
        if os.path.exists(path):
            os.remove(path)
        if out is None:
            return await m.edit("`Could not render ASCII art.`")
        out_path = _save_temp(out)
        try:
            await m.delete()
            cap = "🎨 **ASCII art**" + (" (coloured)" if opts["color"] else "")
            await e.reply(file=out_path, caption=cap)
        finally:
            if os.path.exists(out_path):
                os.remove(out_path)
        return

    # ── text mode: turn the given text into big ASCII art ─────────────
    text = opts["text"]
    if not text:
        return await e.eor(_ASCII_HELP)
    font = opts["font"]
    if font not in _FONT_CANDIDATES:
        return await e.eor(
            f"`Unknown font '{font}'. Use .ascii -ls to list fonts.`", time=6
        )
    m = await e.eor(get_string("com_1"))
    try:
        lines = _text_to_ascii(text, font_name=font, width=width, invert=invert)
    except Exception as exc:
        return await m.edit(f"`Could not render text: {exc}`")
    await _send_ascii_text(e, m, "\n".join(lines))


# ──────────────────────────────────────────────────────────────────────
# .spoil — send/re-send text behind a spoiler entity (Phase A, A1)
# ──────────────────────────────────────────────────────────────────────
# Uses ``custom.entities.spoiler`` (Phase D4) to wrap the text in a
# single ``MessageEntitySpoiler`` spanning it, then sends it with
# ``parse_mode=None`` + ``formatting_entities=`` so the entity isn't lost to
# markdown re-parsing.


@ultroid_cmd(pattern="spoil( (.*)|$)")
async def spoil_cmd(e):
    text = (e.pattern_match.group(1) or "").strip()
    if not text and e.reply_to_msg_id:
        reply = await e.get_reply_message()
        text = (reply.raw_text or "").strip() if reply else ""
        if not text:
            return await e.eor(
                "`Reply to a text message to spoiler it, or give text: .spoil <text>`",
                time=6,
            )
    if not text:
        return await e.eor(
            "`Usage: .spoil <text>` (or reply to a text message).", time=6
        )
    try:
        body, ents = _build_spoiler(text)
    except RuntimeError as exc:
        return await e.eor(f"`{exc}`", time=8)
    await e.client.send_message(
        e.chat_id, body, formatting_entities=ents, parse_mode=None
    )
    await e.try_delete()


# ──────────────────────────────────────────────────────────────────────
# .react — reactions on a replied message (Phase A, A3)
# ──────────────────────────────────────────────────────────────────────
# `.react` (no args) lists the message's current reactions.
# `.react <emoji...>` sends one/more `ReactionEmoji`. Custom-emoji reactions
# (`ReactionCustomEmoji`) are only attempted when the command text itself
# carries a `MessageEntityCustomEmoji` (i.e. the user pasted an actual
# custom-emoji glyph) *and* the sending account is Premium — probed via
# `get_me().premium` — since rendering/sending custom-emoji reactions is a
# Premium-only, server-enforced feature; otherwise we degrade with a clear
# message instead of letting the RPC call fail with a traceback.


@ultroid_cmd(pattern="react( (.*)|$)")
async def react_cmd(e):
    if not e.reply_to_msg_id:
        return await e.eor("`Reply to a message to react to it.`", time=6)
    reply = await e.get_reply_message()
    if not reply:
        return await e.eor("`Could not find the replied message.`", time=6)

    arg = (e.pattern_match.group(1) or "").strip()
    if not arg:
        current = getattr(reply, "reactions", None)
        results = getattr(current, "results", None) if current else None
        if not results:
            return await e.eor("`That message has no reactions yet.`", time=6)
        lines = []
        for r in results:
            react = r.reaction
            label = getattr(react, "emoticon", None)
            if label is None:
                label = f"custom-emoji:{getattr(react, 'document_id', '?')}"
            lines.append(f"• {label} — **{r.count}**")
        return await e.eor("**Reactions:**\n" + "\n".join(lines))

    custom_emoji_ids = [
        ent.document_id
        for ent in (e.entities or ())
        if isinstance(ent, MessageEntityCustomEmoji)
    ]
    if custom_emoji_ids:
        me = await e.client.get_me()
        if not getattr(me, "premium", False):
            return await e.eor(
                "`Custom-emoji reactions need Telegram Premium on this account. "
                "Use a regular emoji instead.`",
                time=8,
            )
        reaction = [ReactionCustomEmoji(document_id=i) for i in custom_emoji_ids]
    else:
        reaction = arg.split()

    try:
        await e.client.send_reaction(e.chat_id, reply.id, reaction=reaction)
    except PremiumAccountRequiredError:
        return await e.eor(
            "`That reaction needs Telegram Premium on this account.`", time=8
        )
    except (ReactionInvalidError, ReactionEmptyError) as exc:
        return await e.eor(f"`Invalid reaction: {exc}`", time=8)
    except RPCError as exc:
        return await e.eor(f"`Could not react: {exc}`", time=8)
    await e.try_delete()


# ──────────────────────────────────────────────────────────────────────
# .effects / .effect — message effects (Phase A, A5)
# ──────────────────────────────────────────────────────────────────────
# `.effects` lists the server's available effects (`messages.getAvailableEffects`);
# `.effect <id> <text>` sends `<text>` with `message_effect_id=<id>` (a kwarg
# the fork's high-level `send_message` already forwards into the raw
# `SendMessageRequest.effect` field). Premium-only effects are enforced
# server-side — the fork doesn't ship a dedicated error class for it at L227,
# so any RPC failure here is reported with the raw server message instead of
# a traceback.


@ultroid_cmd(pattern="effects$")
async def effects_cmd(e):
    try:
        result = await e.client(GetAvailableEffectsRequest(hash=0))
    except RPCError as exc:
        return await e.eor(f"`Could not fetch effects: {exc}`", time=8)
    effects = getattr(result, "effects", None) or []
    if not effects:
        return await e.eor("`No message effects available right now.`", time=6)
    lines = []
    for eff in effects:
        tag = " 🌟" if getattr(eff, "premium_required", False) else ""
        lines.append(f"• `{eff.id}` {eff.emoticon}{tag}")
    await e.eor(
        "**Message effects** (🌟 = Premium):\n"
        + "\n".join(lines)
        + "\n\n`.effect <id> <text>` to send one."
    )


@ultroid_cmd(pattern="effect( (.*)|$)")
async def effect_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    tokens = arg.split(None, 1)
    if not tokens:
        return await e.eor("`Usage: .effect <id> <text>` (see .effects).", time=6)
    try:
        effect_id = int(tokens[0])
    except ValueError:
        return await e.eor("`Effect id must be a number — see .effects.`", time=6)

    text = tokens[1] if len(tokens) > 1 else ""
    if not text and e.reply_to_msg_id:
        reply = await e.get_reply_message()
        text = (reply.raw_text or "").strip() if reply else ""
    if not text:
        return await e.eor("`Usage: .effect <id> <text>` (see .effects).", time=6)

    try:
        await e.client.send_message(e.chat_id, text, message_effect_id=effect_id)
    except RPCError as exc:
        return await e.eor(
            f"`Could not send with that effect (it may need Telegram Premium): {exc}`",
            time=8,
        )
    await e.try_delete()


# ──────────────────────────────────────────────────────────────────────
# .loclive / .stoplive — live locations (Phase A, A6)
# ──────────────────────────────────────────────────────────────────────
# `.loclive <lat>,<long> <seconds> [note]` starts sharing a live location;
# `.loclive <seconds> [note]` (reply to a location message) re-uses that
# message's coordinates instead. `.stoplive` (reply to your own live-location
# message) stops it via a raw `messages.editMessage` carrying
# `InputMediaGeoLive(stopped=True)` — the exact fields were verified against
# the pinned fork's schema (`InputGeoPoint(lat, long)`, `InputMediaGeoLive`
# takes `geo_point`/`period`/`stopped`, `InputGeoPointEmpty` is a valid
# `geo_point` when stopping since the true coordinates aren't required).


def _parse_loclive_args(arg, reply_geo):
    """Return ``(lat, long, seconds, note)`` or raise ``ValueError``."""
    tokens = arg.split(None, 1) if arg else []
    if reply_geo is not None:
        if not tokens:
            raise ValueError("Give the number of seconds to share for.")
        lat, lon = reply_geo.lat, reply_geo.long
        seconds_tok, rest = tokens[0], (tokens[1] if len(tokens) > 1 else "")
    else:
        if not tokens or "," not in tokens[0]:
            raise ValueError(
                "Give `<lat>,<long> <seconds>`, or reply to a location message."
            )
        lat_s, _, lon_s = tokens[0].partition(",")
        try:
            lat, lon = float(lat_s), float(lon_s)
        except ValueError:
            raise ValueError("`<lat>,<long>` must both be numbers.")
        rest = tokens[1] if len(tokens) > 1 else ""
        rest_tokens = rest.split(None, 1) if rest else []
        if not rest_tokens:
            raise ValueError("Give the number of seconds to share for.")
        seconds_tok, rest = rest_tokens[0], (
            rest_tokens[1] if len(rest_tokens) > 1 else ""
        )
    try:
        seconds = int(seconds_tok)
    except ValueError:
        raise ValueError("Seconds must be a whole number.")
    seconds = max(60, min(seconds, 86400))
    return lat, lon, seconds, (rest.strip() or None)


@ultroid_cmd(pattern="loclive( (.*)|$)")
async def loclive_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    reply_geo = None
    if e.reply_to_msg_id:
        reply = await e.get_reply_message()
        reply_geo = getattr(reply, "geo", None) if reply else None

    try:
        lat, lon, seconds, note = _parse_loclive_args(arg, reply_geo)
    except ValueError as exc:
        return await e.eor(f"`{exc}`", time=8)

    media = InputMediaGeoLive(
        geo_point=InputGeoPoint(lat=lat, long=lon), period=seconds
    )
    try:
        await e.client.send_message(e.chat_id, note or "", file=media)
    except RPCError as exc:
        return await e.eor(f"`Could not start live location: {exc}`", time=8)
    await e.try_delete()


@ultroid_cmd(pattern="stoplive$")
async def stoplive_cmd(e):
    if not e.reply_to_msg_id:
        return await e.eor(
            "`Reply to your live-location message with .stoplive to stop it.`",
            time=6,
        )
    reply = await e.get_reply_message()
    if not reply or not isinstance(reply.media, MessageMediaGeoLive):
        return await e.eor("`That message isn't an active live location.`", time=6)
    if not reply.out:
        return await e.eor("`You can only stop a live location you sent.`", time=6)

    try:
        peer = await e.client.get_input_entity(e.chat_id)
        await e.client(
            EditMessageRequest(
                peer=peer,
                id=reply.id,
                media=InputMediaGeoLive(geo_point=InputGeoPointEmpty(), stopped=True),
            )
        )
    except RPCError as exc:
        return await e.eor(f"`Could not stop live location: {exc}`", time=8)
    await e.eor("`Live location stopped.`", time=5)


# ──────────────────────────────────────────────────────────────────────
# .qr / .qrlist / .qrsend — quick replies (Phase A, A12)
# ──────────────────────────────────────────────────────────────────────
# Verified against the pinned fork (L227) schema: there is **no** dedicated
# "create shortcut" request — `messages.EditQuickReplyShortcutRequest` only
# *renames* an existing shortcut (`shortcut_id: int, shortcut: str`), and
# `messages.CheckQuickReplyShortcutRequest` only validates a name. A
# shortcut and its messages are created the same way official clients do it:
# by sending a message to Saved Messages (`peer="me"`) with
# `quick_reply_shortcut=InputQuickReplyShortcut(shortcut=name)` on the raw
# `SendMessageRequest` (exposed by the fork's high-level `send_message` via
# the same-named kwarg). `.qrlist` reads them back with
# `messages.GetQuickRepliesRequest`; `.qrsend` resolves the shortcut's
# message ids with `messages.GetQuickReplyMessagesRequest` and re-sends them
# into the current chat with `messages.SendQuickReplyMessagesRequest`.


@ultroid_cmd(pattern="qr( (.*)|$)")
async def qr_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    tokens = arg.split(None, 1)
    if not tokens:
        return await e.eor(
            "`Usage: .qr <shortcut> <text>` (or reply to save its text).", time=6
        )
    shortcut = tokens[0]
    text = tokens[1] if len(tokens) > 1 else ""
    if not text and e.reply_to_msg_id:
        reply = await e.get_reply_message()
        text = (reply.raw_text or "").strip() if reply else ""
    if not text:
        return await e.eor(
            "`Usage: .qr <shortcut> <text>` (or reply to save its text).", time=6
        )

    try:
        await e.client.send_message(
            "me", text, quick_reply_shortcut=InputQuickReplyShortcut(shortcut=shortcut)
        )
    except RPCError as exc:
        return await e.eor(f"`Could not save quick reply '{shortcut}': {exc}`", time=8)
    await e.eor(f"`Saved to quick-reply shortcut '{shortcut}'.`", time=6)


@ultroid_cmd(pattern="qrlist$")
async def qrlist_cmd(e):
    try:
        result = await e.client(GetQuickRepliesRequest(hash=0))
    except RPCError as exc:
        return await e.eor(f"`Could not fetch quick replies: {exc}`", time=8)
    shortcuts = getattr(result, "quick_replies", None) or []
    if not shortcuts:
        return await e.eor("`No quick-reply shortcuts saved yet. Try .qr.`", time=6)
    lines = [f"• `{qr.shortcut}` — {qr.count} message(s)" for qr in shortcuts]
    await e.eor("**Quick-reply shortcuts:**\n" + "\n".join(lines))


@ultroid_cmd(pattern="qrsend( (.*)|$)")
async def qrsend_cmd(e):
    shortcut = (e.pattern_match.group(1) or "").strip()
    if not shortcut:
        return await e.eor("`Usage: .qrsend <shortcut>`", time=6)

    try:
        listing = await e.client(GetQuickRepliesRequest(hash=0))
    except RPCError as exc:
        return await e.eor(f"`Could not fetch quick replies: {exc}`", time=8)
    match = next(
        (
            qr
            for qr in (getattr(listing, "quick_replies", None) or ())
            if qr.shortcut == shortcut
        ),
        None,
    )
    if not match:
        return await e.eor(f"`No quick-reply shortcut named '{shortcut}'.`", time=6)

    try:
        msgs = await e.client(
            GetQuickReplyMessagesRequest(shortcut_id=match.shortcut_id, hash=0)
        )
    except RPCError as exc:
        return await e.eor(f"`Could not load '{shortcut}': {exc}`", time=8)
    ids = [
        m.id for m in getattr(msgs, "messages", None) or () if getattr(m, "id", None)
    ]
    if not ids:
        return await e.eor(
            f"`Quick-reply shortcut '{shortcut}' has no messages.`", time=6
        )

    try:
        peer = await e.client.get_input_entity(e.chat_id)
        await e.client(
            SendQuickReplyMessagesRequest(
                peer=peer,
                shortcut_id=match.shortcut_id,
                id=ids,
                random_id=[generate_random_long() for _ in ids],
            )
        )
    except RPCError as exc:
        return await e.eor(f"`Could not send '{shortcut}': {exc}`", time=8)
    await e.try_delete()


# FUTURE: standalone simple codes go here

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}destroy <reply to animated sticker>`
    To destroy the sticker.

• `{i}tiny <reply to media>`
    To create Tiny stickers.

• `{i}kang <reply to image/sticker>`
    Kang the sticker (add to your pack).

• `{i}packkang <pack name>`
    Kang the Complete sticker set (with custom name).

• `{i}round <reply to any media>`
    To extract round sticker.

• `{i}emojiset <pack-link-or-shortname>`
    List a custom-emoji pack's contents (index, glyph, document id) — e.g.
    `{i}emojiset https://t.me/addemoji/PackName` or `{i}emojiset PackName`.

• `{i}emojireply <shortname:index or doc_id> [text]`
    Reply/reply-in-place with a custom emoji looked up via `{i}emojiset`
    (`pack:3`) or by a raw document id, optionally followed by extra text.
    Rendering the real emoji needs **your** account to have Telegram
    Premium — non-Premium accounts get a plain-glyph fallback instead of a
    crash.
"""

import glob
import io
import os
import random
import re
import string
from os import remove
from uuid import uuid4

try:
    import cv2
except ImportError:
    cv2 = None
try:
    import numpy as np
except ImportError:
    np = None
try:
    from PIL import Image, ImageDraw
except ImportError:
    pass

from telethon.errors import PeerIdInvalidError, YouBlockedUserError
from telethon.errors.rpcerrorlist import PackShortNameInvalidError as NameErr
from telethon.tl.functions.messages import UploadMediaRequest
from telethon.tl.types import (
    DocumentAttributeCustomEmoji,
    DocumentAttributeFilename,
    DocumentAttributeSticker,
    InputPeerSelf,
)
from telethon.utils import get_input_document

from custom.entities import custom_emoji as _custom_emoji_entity
from pyUltroid.fns import fns

from . import (
    KANGING_STR,
    LOGS,
    asst,
    con,
    eod,
    functions,
    get_string,
    quotly,
    types,
    udB,
    ultroid_cmd,
)


# Helpfull for Pack name
def random_chars(k=8):
    chars = string.ascii_letters
    return "".join(random.choice(chars) for _ in range(k))


# - end


@ultroid_cmd(pattern="pamck")
async def pack_kangish(_):
    _e = await _.get_reply_message()
    local = None
    try:
        cmdtext = _.text.split(maxsplit=1)[1]
    except IndexError:
        cmdtext = None
    if cmdtext and os.path.isdir(cmdtext):
        local = True
    elif not (_e and _e.sticker):
        return await _.eor("`Please reply to an sticker.`")
    msg = await _.eor(get_string("com_1"))
    # username needs?
    username = "@" + _.sender.username
    # actual pack name that will showup in title
    _packname = cmdtext or f"Pack By {username if username else _.sender.first_name}"
    if not local:
        _id = _e.media.document.attributes[1].stickerset.id
        _hash = _e.media.document.attributes[1].stickerset.access_hash
        _get_stiks = await _.client(
            functions.messages.GetStickerSetRequest(
                stickerset=types.InputStickerSetID(id=_id, access_hash=_hash), hash=0
            )
        )
        docs = _get_stiks.documents
    else:
        # idk but this doesn't just work?
        docs = []
        files = glob.glob(cmdtext + "/*")
        count = 0
        for file in files:
            if file.endswith((".tgs", ".webm")):
                count += 1
                upl = await asst.upload_file(file)
                docs.append(await asst(UploadMediaRequest(InputPeerSelf(), upl)))
                if count % 5 == 0:
                    await msg.edit(f"`Uploaded {count} files.`")

    stiks = []
    for i in docs:
        x = get_input_document(i)
        attrs = i.attributes
        try:
            _attrs = attrs[1]
            _em = _attrs.alt
        except AttributeError:
            _em = random.choice(["💫", "🎈", "🍻"])
        stiks.append(types.InputStickerSetItem(document=x, emoji=_em))
    try:
        short_name = str(uuid4())[:16].replace("-", "")  # will be enough ig
        _r_e_s = await asst(
            functions.stickers.CreateStickerSetRequest(
                user_id=_.sender_id,
                title=_packname,
                short_name=f"{short_name}_by_{asst.me.username}",
                stickers=stiks,
            )
        )
    except PeerIdInvalidError:
        return await msg.eor(
            f"Hey {fns.inline_mention(_.sender)} send `/start` to @{asst.me.username} and later try this command again.."
        )
    except NameErr:
        short_name = random_chars(10)  # will be enough ig
        _r_e_s = await asst(
            functions.stickers.CreateStickerSetRequest(
                user_id=_.sender_id,
                title=_packname,
                short_name=f"{short_name}_by_{asst.me.username}",
                stickers=stiks,
            )
        )
    except Exception as er:
        await msg.eor(str(er))
        LOGS.exception(er)
        return
    name = f"{_r_e_s.set.short_name}"
    tot = f"{_r_e_s.set.count}"
    kuli = "**Pack Kanged Successfully**\nPack : [Click Here!](https://t.me/addstickers/{})\nTotal : {}".format(
        name, tot
    )
    await msg.eor(kuli, link_preview=False)


@ultroid_cmd(
    pattern="kang",
)
async def hehe(args):
    ultroid_bot = args.client
    xx = await args.eor(get_string("com_1"))
    user = ultroid_bot.me
    username = user.username
    username = f"@{username}" if username else user.first_name
    message = await args.get_reply_message()
    photo = None
    is_anim, is_vid = False, False
    emoji = None
    if not message:
        return await xx.eor(get_string("sts_6"))
    if message.photo:
        photo = io.BytesIO()
        photo = await ultroid_bot.download_media(message.photo, photo)
    elif message.file and "image" in message.file.mime_type.split("/"):
        photo = io.BytesIO()
        await ultroid_bot.download_file(message.media.document, photo)
        if (
            DocumentAttributeFilename(file_name="sticker.webp")
            in message.media.document.attributes
        ):
            emoji = message.media.document.attributes[1].alt

    elif message.file and "video" in message.file.mime_type.split("/"):
        xy = await message.download_media()
        if (message.file.duration or 0) <= 10:
            is_vid = True
            photo = await con.create_webm(xy)
        else:
            y = cv2.VideoCapture(xy)
            heh, lol = y.read()
            cv2.imwrite("ult.webp", lol)
            photo = "ult.webp"
    elif message.file and "tgsticker" in message.file.mime_type:
        await ultroid_bot.download_file(
            message.media.document,
            "AnimatedSticker.tgs",
        )
        attributes = message.media.document.attributes
        for attribute in attributes:
            if isinstance(attribute, DocumentAttributeSticker):
                emoji = attribute.alt
        is_anim = True
        photo = 1
    elif message.message:
        photo = await quotly.create_quotly(message)
    else:
        return await xx.edit(get_string("com_4"))
    if not udB.get_key("language") or udB.get_key("language") == "en":
        ra = random.choice(KANGING_STR)
    else:
        ra = get_string("sts_11")
    await xx.edit(f"`{ra}`")
    if photo:
        splat = args.text.split()
        pack = 1
        if not emoji:
            emoji = "🏵"
        if len(splat) == 3:
            pack = splat[2]  # User sent ultroid_both
            emoji = splat[1]
        elif len(splat) == 2:
            if splat[1].isnumeric():
                pack = int(splat[1])
            else:
                emoji = splat[1]

        packname = f"ult_{user.id}_{pack}"
        packnick = f"{username}'s Pack {pack}"
        cmd = "/newpack"
        file = io.BytesIO()

        if is_vid:
            packname += "_vid"
            packnick += " (Video)"
            cmd = "/newvideo"
        elif is_anim:
            packname += "_anim"
            packnick += " (Animated)"
            cmd = "/newanimated"
        else:
            image = con.resize_photo_sticker(photo)
            file.name = "sticker.png"
            image.save(file, "PNG")

        response = await fns.async_searcher(f"http://t.me/addstickers/{packname}")
        htmlstr = response.split("\n")

        if (
            "  A <strong>Telegram</strong> user has created the <strong>Sticker&nbsp;Set</strong>."
            not in htmlstr
        ):
            async with ultroid_bot.conversation("@Stickers") as conv:
                try:
                    await conv.send_message("/addsticker")
                except YouBlockedUserError:
                    LOGS.info("Unblocking @Stickers for kang...")
                    await ultroid_bot(functions.contacts.UnblockRequest("stickers"))
                    await conv.send_message("/addsticker")
                await conv.get_response()
                await conv.send_message(packname)
                x = await conv.get_response()
                if x.text.startswith("Alright! Now send me the video sticker."):
                    await conv.send_file(photo, force_document=True)
                    x = await conv.get_response()
                t = "50" if (is_anim or is_vid) else "120"
                while t in x.message:
                    pack += 1
                    packname = f"ult_{user.id}_{pack}"
                    packnick = f"{username}'s Pack {pack}"
                    if is_anim:
                        packname += "_anim"
                        packnick += " (Animated)"
                    elif is_vid:
                        packnick += " (Video)"
                        packname += "_vid"
                    await xx.edit(get_string("sts_13").format(pack))
                    await conv.send_message("/addsticker")
                    await conv.get_response()
                    await conv.send_message(packname)
                    x = await conv.get_response()
                    if x.text.startswith("Alright! Now send me the video sticker."):
                        await conv.send_file(photo, force_document=True)
                        x = await conv.get_response()
                    if x.text in ["Invalid pack selected.", "Invalid set selected."]:
                        await conv.send_message(cmd)
                        await conv.get_response()
                        await conv.send_message(packnick)
                        await conv.get_response()
                        if is_anim:
                            await conv.send_file("AnimatedSticker.tgs")
                            remove("AnimatedSticker.tgs")
                        else:
                            if is_vid:
                                file = photo
                            else:
                                file.seek(0)
                            await conv.send_file(file, force_document=True)
                        await conv.get_response()
                        await conv.send_message(emoji)
                        await conv.get_response()
                        await conv.send_message("/publish")
                        if is_anim:
                            await conv.get_response()
                            await conv.send_message(f"<{packnick}>")
                        await conv.get_response()
                        await conv.send_message("/skip")
                        await conv.get_response()
                        await conv.send_message(packname)
                        await conv.get_response()
                        await xx.edit(
                            get_string("sts_7").format(packname),
                            parse_mode="md",
                        )
                        return
                if is_anim:
                    await conv.send_file("AnimatedSticker.tgs")
                    remove("AnimatedSticker.tgs")
                elif "send me an emoji" not in x.message:
                    if is_vid:
                        file = photo
                    else:
                        file.seek(0)
                    await conv.send_file(file, force_document=True)
                    rsp = await conv.get_response()
                    if "Sorry, the file type is invalid." in rsp.text:
                        await xx.edit(
                            get_string("sts_8"),
                        )
                        return
                await conv.send_message(emoji)
                await conv.get_response()
                await conv.send_message("/done")
                await conv.get_response()
                await ultroid_bot.send_read_acknowledge(conv.chat_id)
        else:
            await xx.edit("`Brewing a new Pack...`")
            async with ultroid_bot.conversation("Stickers") as conv:
                await conv.send_message(cmd)
                await conv.get_response()
                await conv.send_message(packnick)
                await conv.get_response()
                if is_anim:
                    await conv.send_file("AnimatedSticker.tgs")
                    remove("AnimatedSticker.tgs")
                else:
                    if is_vid:
                        file = photo
                    else:
                        file.seek(0)
                    await conv.send_file(file, force_document=True)
                rsp = await conv.get_response()
                if "Sorry, the file type is invalid." in rsp.text:
                    await xx.edit(
                        get_string("sts_8"),
                    )
                    return
                await conv.send_message(emoji)
                await conv.get_response()
                await conv.send_message("/publish")
                if is_anim:
                    await conv.get_response()
                    await conv.send_message(f"<{packnick}>")

                await conv.get_response()
                await conv.send_message("/skip")
                await conv.get_response()
                await conv.send_message(packname)
                await conv.get_response()
                await ultroid_bot.send_read_acknowledge(conv.chat_id)
        await xx.edit(
            get_string("sts_12").format(emoji, packname),
            parse_mode="md",
        )
        try:
            os.remove(photo)
        except Exception:
            pass


@ultroid_cmd(
    pattern="round$",
)
async def ultdround(event):
    ureply = await event.get_reply_message()
    xx = await event.eor(get_string("com_1"))
    if not (ureply and (ureply.media)):
        await xx.edit(get_string("sts_10"))
        return
    ultt = await ureply.download_media()
    file = await con.convert(
        ultt,
        convert_to="png",
        allowed_formats=["jpg", "jpeg", "png"],
        outname="round",
        remove_old=True,
    )
    img = Image.open(file).convert("RGB")
    npImage = np.array(img)
    h, w = img.size
    alpha = Image.new("L", img.size, 0)
    draw = ImageDraw.Draw(alpha)
    draw.pieslice([0, 0, h, w], 0, 360, fill=255)
    npAlpha = np.array(alpha)
    npImage = np.dstack((npImage, npAlpha))
    Image.fromarray(npImage).save("ult.webp")
    await event.client.send_file(
        event.chat_id,
        "ult.webp",
        force_document=False,
        reply_to=event.reply_to_msg_id,
    )
    await xx.delete()
    os.remove(file)
    os.remove("ult.webp")


@ultroid_cmd(
    pattern="destroy$",
)
async def ultdestroy(event):
    ult = await event.get_reply_message()
    if not (ult and ult.media and "animated" in fns.mediainfo(ult.media)):
        return await event.eor(get_string("sts_2"))
    await event.client.download_media(ult, "ultroid.tgs")
    xx = await event.eor(get_string("com_1"))
    await fns.bash("lottie_convert.py ultroid.tgs json.json")
    with open("json.json") as json:
        jsn = json.read()
    jsn = (
        jsn.replace("[100]", "[200]")
        .replace("[10]", "[40]")
        .replace("[-1]", "[-10]")
        .replace("[0]", "[15]")
        .replace("[1]", "[20]")
        .replace("[2]", "[17]")
        .replace("[3]", "[40]")
        .replace("[4]", "[37]")
        .replace("[5]", "[60]")
        .replace("[6]", "[70]")
        .replace("[7]", "[40]")
        .replace("[8]", "[37]")
        .replace("[9]", "[110]")
    )
    open("json.json", "w").write(jsn)
    file = await con.animated_sticker("json.json", "ultroid.tgs")
    if file:
        await event.client.send_file(
            event.chat_id,
            file="ultroid.tgs",
            force_document=False,
            reply_to=event.reply_to_msg_id,
        )
    await xx.delete()
    os.remove("json.json")


@ultroid_cmd(
    pattern="tiny$",
)
async def ultiny(event):
    reply = await event.get_reply_message()
    if not (reply and (reply.media)):
        await event.eor(get_string("sts_10"))
        return
    xx = await event.eor(get_string("com_1"))
    ik = await reply.download_media()
    im1 = Image.open("resources/extras/ultroid_blank.png")
    if ik.endswith(".tgs"):
        await con.animated_sticker(ik, "json.json")
        with open("json.json") as json:
            jsn = json.read()
        jsn = jsn.replace("512", "2000")
        open("json.json", "w").write(jsn)
        await con.animated_sticker("json.json", "ult.tgs")
        file = "ult.tgs"
        os.remove("json.json")
    elif ik.endswith((".gif", ".webm", ".mp4")):
        iik = cv2.VideoCapture(ik)
        dani, busy = iik.read()
        cv2.imwrite("i.png", busy)
        fil = "i.png"
        im = Image.open(fil)
        z, d = im.size
        if z == d:
            xxx, yyy = 200, 200
        else:
            t = z + d
            a = z / t
            b = d / t
            aa = (a * 100) - 50
            bb = (b * 100) - 50
            xxx = 200 + 5 * aa
            yyy = 200 + 5 * bb
        k = im.resize((int(xxx), int(yyy)))
        k.save("k.png", format="PNG", optimize=True)
        im2 = Image.open("k.png")
        back_im = im1.copy()
        back_im.paste(im2, (150, 0))
        back_im.save("o.webp", "WEBP", quality=95)
        file = "o.webp"
        os.remove(fil)
        os.remove("k.png")
    else:
        im = Image.open(ik)
        z, d = im.size
        if z == d:
            xxx, yyy = 200, 200
        else:
            t = z + d
            a = z / t
            b = d / t
            aa = (a * 100) - 50
            bb = (b * 100) - 50
            xxx = 200 + 5 * aa
            yyy = 200 + 5 * bb
        k = im.resize((int(xxx), int(yyy)))
        k.save("k.png", format="PNG", optimize=True)
        im2 = Image.open("k.png")
        back_im = im1.copy()
        back_im.paste(im2, (150, 0))
        back_im.save("o.webp", "WEBP", quality=95)
        file = "o.webp"
        os.remove("k.png")
    if os.path.exists(file):
        await event.client.send_file(
            event.chat_id, file, reply_to=event.reply_to_msg_id
        )
        os.remove(file)
    await xx.delete()
    os.remove(ik)


# ──────────────────────────────────────────────────────────────────────────
# A13 — `.emojiset` / `.emojireply`: custom-emoji sticker sets (Phase D4)
# ──────────────────────────────────────────────────────────────────────────
# Custom-emoji packs are fetched with the same `messages.GetStickerSetRequest`
# `.pamck` already uses above, just addressed by `InputStickerSetShortName`
# instead of `InputStickerSetID` — the shortname is all a pack link/shortcut
# gives us. `StickerSet.emojis` (flag) tells packs of *custom emoji* apart
# from regular sticker packs (both come back from the same RPC).

# Matches e.g. `t.me/addemoji/Foo`, `https://t.me/addstickers/Foo`,
# `telegram.me/addemoji/Foo` -- anything else is treated as a bare shortname.
_EMOJISET_LINK_RE = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/add(?:emoji|stickers)/([A-Za-z0-9_]+)",
    re.I,
)
# Telegram sticker/emoji-set shortnames: letters, digits, underscore only.
_SHORTNAME_RE = re.compile(r"^[A-Za-z0-9_]+$")

_EMOJISET_LIST_LIMIT = 100  # keep the listing under Telegram's 4096-char cap


def _parse_stickerset_shortname(raw):
    """Pull a sticker/emoji-set shortname out of a link or bare shortname."""
    raw = (raw or "").strip()
    if not raw:
        return None
    m = _EMOJISET_LINK_RE.search(raw)
    if m:
        return m.group(1)
    raw = raw.lstrip("@")
    return raw if _SHORTNAME_RE.match(raw) else None


def _custom_emoji_alt(document):
    """Best-effort fallback glyph for *document* (a custom-emoji Document)."""
    for attr in getattr(document, "attributes", None) or ():
        if isinstance(attr, DocumentAttributeCustomEmoji):
            return attr.alt or None
        if isinstance(attr, DocumentAttributeSticker):
            return attr.alt or None
    return None


async def _get_emoji_stickerset(client, short_name):
    """``messages.GetStickerSetRequest`` by shortname; raises on RPC error."""
    return await client(
        functions.messages.GetStickerSetRequest(
            stickerset=types.InputStickerSetShortName(short_name=short_name),
            hash=0,
        )
    )


async def _is_sender_premium(event):
    """Cheap Premium probe: the logged-in account's own ``User.premium`` flag.

    This only tells us whether *rendering* a custom emoji is even possible —
    Telegram enforces it server-side regardless, so callers should still be
    ready to catch an RPC error on the actual send (see ``.emojireply``).
    """
    try:
        me = await event.client.get_me()
    except Exception as e:  # pragma: no cover - network/session hiccup
        LOGS.info(f"emojireply: couldn't probe Premium status: {e}")
        return None  # unknown - let the caller decide how to proceed
    return bool(getattr(me, "premium", False))


@ultroid_cmd(pattern="emojiset( (.*)|$)")
async def emoji_set_list(event):
    raw = event.pattern_match.group(1)
    raw = raw.strip() if raw else ""
    if not raw:
        return await eod(event, "`Usage: .emojiset <pack-link-or-shortname>`")
    short_name = _parse_stickerset_shortname(raw)
    if not short_name:
        return await eod(
            event,
            "`Couldn't read a pack shortname out of that. Try a link like "
            "t.me/addemoji/PackName or just the shortname.`",
        )
    xx = await event.eor(get_string("com_1"))
    try:
        result = await _get_emoji_stickerset(event.client, short_name)
    except Exception as e:
        return await xx.eor(f'`Couldn\'t fetch pack "{short_name}": {e}`')
    st = getattr(result, "set", None)
    docs = getattr(result, "documents", None) or []
    if st is None:
        return await xx.eor(f"`No such pack: {short_name}`")
    if not getattr(st, "emojis", False):
        return await xx.eor(
            f'`"{st.title}" ({short_name}) is a regular sticker pack, not a '
            f"custom-emoji pack — try .pamck/.kang for stickers instead.`"
        )
    if not docs:
        return await xx.eor(f'`"{st.title}" ({short_name}) has no emoji in it.`')
    lines = [f"**{st.title}** (`{short_name}`) — {len(docs)} custom emoji"]
    shown = docs[:_EMOJISET_LIST_LIMIT]
    for idx, doc in enumerate(shown, start=1):
        alt = _custom_emoji_alt(doc) or "❓"
        lines.append(f"`{idx:>2}.` {alt}  doc id: `{doc.id}`")
    if len(docs) > len(shown):
        lines.append(f"`… +{len(docs) - len(shown)} more (list truncated)`")
    lines.append(
        f"\nUse `.emojireply {short_name}:<index>` or `.emojireply <doc_id>` "
        "to reply with one (needs Premium on this account to render)."
    )
    await xx.eor("\n".join(lines), link_preview=False)


@ultroid_cmd(pattern="emojireply( (.*)|$)")
async def emoji_reply(event):
    raw = event.pattern_match.group(1)
    raw = raw.strip() if raw else ""
    if not raw:
        return await eod(
            event,
            "`Usage: .emojireply <shortname:index or doc_id> [extra text]` "
            "(see .emojiset to list a pack's indices/ids)",
        )
    bits = raw.split(maxsplit=1)
    ref = bits[0]
    extra_text = bits[1].strip() if len(bits) > 1 else ""

    doc_id = None
    alt = None
    if ":" in ref:
        short_name, _, idx_s = ref.partition(":")
        short_name = _parse_stickerset_shortname(short_name)
        if not short_name or not idx_s.isdigit():
            return await eod(
                event,
                "`Bad reference — expected <shortname>:<index>, e.g. MyPack:3`",
            )
        try:
            result = await _get_emoji_stickerset(event.client, short_name)
        except Exception as e:
            return await eod(event, f'`Couldn\'t fetch pack "{short_name}": {e}`')
        docs = getattr(result, "documents", None) or []
        index = int(idx_s)
        if not (1 <= index <= len(docs)):
            return await eod(
                event,
                f'`Index out of range — "{short_name}" has {len(docs)} emoji.`',
            )
        doc = docs[index - 1]
        doc_id = doc.id
        alt = _custom_emoji_alt(doc)
    elif ref.isdigit():
        doc_id = int(ref)
        try:
            docs = await event.client(
                functions.messages.GetCustomEmojiDocumentsRequest(document_id=[doc_id])
            )
        except Exception as e:
            return await eod(event, f"`Couldn't look up document {doc_id}: {e}`")
        if not docs:
            return await eod(event, f"`No such custom-emoji document: {doc_id}`")
        alt = _custom_emoji_alt(docs[0])
    else:
        return await eod(
            event,
            "`Usage: .emojireply <shortname:index or doc_id> [extra text]`",
        )

    alt = alt or "❓"
    is_premium = await _is_sender_premium(event)
    if is_premium is False:
        note = (
            "`Custom emoji only render for Telegram Premium accounts — this "
            "account isn't Premium, so sending the plain fallback glyph "
            "instead of the real emoji.`\n"
        )
        text = f"{note}{alt}"
        if extra_text:
            text += f" {extra_text}"
        return await event.eor(text, parse_mode=None)

    text = f"{alt} {extra_text}".rstrip() if extra_text else alt
    _, entities = _custom_emoji_entity(alt, doc_id)
    try:
        await event.eor(text, formatting_entities=entities, parse_mode=None)
    except Exception as e:
        LOGS.info(f"emojireply: custom-emoji send failed, degrading to plain text: {e}")
        fallback = (
            "`Telegram rejected the custom emoji for this account (likely "
            "needs Premium to render) — sending the plain glyph instead.`\n"
            f"{text}"
        )
        await event.eor(fallback, parse_mode=None)

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

import html
import random

from telethon import errors
from telethon.errors.rpcerrorlist import StickersetInvalidError
from telethon.tl.functions.messages import GetStickerSetRequest as GetSticker
from telethon.tl.functions.messages import UploadMediaRequest
from telethon.tl.functions.stickers import AddStickerToSetRequest as AddSticker
from telethon.tl.functions.stickers import CreateStickerSetRequest
from telethon.tl.types import InputPeerSelf
from telethon.tl.types import InputStickerSetItem as SetItem
from telethon.tl.types import InputStickerSetShortName, User
from telethon.utils import get_display_name, get_input_document

from custom.fmt import fmtmsg
from pyUltroid.fns.misc import Quotly
from pyUltroid.fns.tools import TgConverter
from pyUltroid.fns.misc import Quotly
from pyUltroid.fns.tools import TgConverter

from . import LOGS, asst, asst_cmd, in_pattern, udB


@asst_cmd(
    pattern="kang",
)
async def kang_cmd(ult):
    sender = await ult.get_sender()
    if not isinstance(sender, User):
        return
    if not ult.is_reply:
        return await ult.eor("`Reply to a sticker/photo..`", time=5)
    reply = await ult.get_reply_message()
    if sender.username:
        pre = sender.username[:4]
    else:
        pre = random.random_string(length=3)
    animated, dl, video = None, None, None
    try:
        emoji = ult.text.split(maxsplit=1)[1]
    except IndexError:
        emoji = None
    if reply.sticker:
        file = get_input_document(reply.sticker)
        emoji = emoji or reply.file.emoji
        name = reply.file.name
        if name.endswith(".tgs"):
            animated = True
            dl = await reply.download_media()
        elif name.endswith(".webm"):
            video = True
            dl = await reply.download_media()
    elif reply.photo:
        dl = await reply.download_media()
        name = "sticker.webp"
        image = TgConverter.resize_photo_sticker(dl)
        image.save(name, "WEBP")
    elif reply.text:
        dl = await Quotly().create_quotly(reply)
    else:
        return await ult.eor("`Reply to sticker or text to add it in your pack...`")
    if not emoji:
        emoji = "🏵"
    if dl:
        upl = await ult.client.upload_file(dl)
        file = get_input_document(
            await ult.client(UploadMediaRequest(InputPeerSelf(), upl))
        )
    get_ = udB.get_key("STICKERS") or {}
    type_ = "anim" if animated else "static"
    if not get_.get(ult.sender_id) or not get_.get(ult.sender_id, {}).get(type_):
        sn = f"{pre}_{ult.sender_id}"
        title = f"{get_display_name(sender)}'s Kang Pack"
        if animated:
            type_ = "anim"
            sn += "_anim"
            title += " (Animated)"
        elif video:
            type_ = "vid"
            sn += "_vid"
            title += " (Video)"
        sn += f"_by_{asst.me.username}"
        try:
            await asst(GetSticker(InputStickerSetShortName(sn), hash=0))
            sn = sn.replace(str(ult.sender_id), f"{ult.sender_id}_{ult.id}")
        except StickersetInvalidError:
            pass
        try:
            pack = await ult.client(
                CreateStickerSetRequest(
                    user_id=sender.id,
                    title=title,
                    short_name=sn,
                    stickers=[SetItem(file, emoji=emoji)],
                    software="@TeamUltroid",
                )
            )
        except Exception as er:
            return await ult.eor(str(er))
        sn = pack.set.short_name
        if not get_.get(ult.sender_id):
            get_.update({ult.sender_id: {type_: [sn]}})
        else:
            get_[ult.sender_id].update({type_: [sn]})
        udB.set_key("STICKERS", get_)
        return await ult.reply(
            fmtmsg(
                "Kanged Successfully! 🎉",
                msgs=f"Emoji: {emoji}\nLink: https://t.me/addstickers/{sn}",
            ),
            parse_mode="html",
        )
    name = get_[ult.sender_id][type_][-1]
    try:
        await asst(GetSticker(InputStickerSetShortName(name), hash=0))
    except StickersetInvalidError:
        get_[ult.sender_id][type_].remove(name)
    try:
        await asst(
            AddSticker(InputStickerSetShortName(name), SetItem(file, emoji=emoji))
        )
    except (errors.StickerpackStickersTooMuchError, errors.StickersTooMuchError):
        sn = f"{pre}{ult.sender_id}_{ult.id}"
        title = f"{get_display_name(sender)}'s Kang Pack"
        if animated:
            sn += "_anim"
            title += " (Animated)"
        elif video:
            sn += "_vid"
            title += "(Video)"
        sn += f"_by_{asst.me.username}"
        try:
            pack = await ult.client(
                CreateStickerSetRequest(
                    user_id=sender.id,
                    title=title,
                    short_name=sn,
                    stickers=[SetItem(file, emoji=emoji)],
                    software="@TeamUltroid",
                )
            )
        except Exception as er:
            return await ult.eor(str(er))
        get_[ult.sender_id][type_].append(pack.set.short_name)
        udB.set_key("STICKERS", get_)
        return await ult.reply(
            fmtmsg(
                "Created New Kang Pack! 🆕",
                msgs=f"Emoji: {emoji}\nLink: https://t.me/addstickers/{sn}",
            ),
            parse_mode="html",
        )
    except Exception as er:
        LOGS.exception(er)
        return await ult.reply(fmtmsg("Error ❌", msgs=str(er)), parse_mode="html")
    await ult.reply(
        fmtmsg(
            "Sticker Added to Pack Successfully ✅",
            msgs=f"Link: https://t.me/addstickers/{name}",
        ),
        parse_mode="html",
    )


def _owned_pack_names(sender_id):
    """Flat list of every pack short-name this user has kanged so far."""
    ko = udB.get_key("STICKERS") or {}
    owned = ko.get(sender_id) or {}
    names = []
    for kind in owned.values():
        names.extend(kind)
    return names


@asst_cmd(pattern="listpack")
async def do_magic(ult):
    ko = udB.get_key("STICKERS") or {}
    if not ko.get(ult.sender_id):
        return await ult.reply(fmtmsg("No Sticker Pack Found!"), parse_mode="html")
    al_ = []
    ul = ko[ult.sender_id]
    for _ in ul.keys():
        al_.extend(ul[_])
    packs = []
    for _ in al_:
        try:
            pack = await ult.client(GetSticker(InputStickerSetShortName(_), hash=0))
            packs.append((pack.set.title, _))
        except StickersetInvalidError:
            if ul.get("anim") and _ in ul["anim"]:
                ul["anim"].remove(_)
            elif ul.get("vid") and _ in ul["vid"]:
                ul["vid"].remove(_)
            else:
                ul["static"].remove(_)
            udB.set_key("STICKERS", ko)

    links = "\n".join(
        f'  <a href="https://t.me/addstickers/{short_name}">{html.escape(title)}</a>'
        for title, short_name in packs
    )
    text = fmtmsg("Stickers Owned by You! 🏵")
    if links:
        text = f"{text}\n\n{links}"
    await ult.reply(text, parse_mode="html")


@in_pattern("packs", owner=True)
async def packs_inline(e):
    names = _owned_pack_names(e.sender_id)
    if not names:
        return await e.answer(
            [],
            switch_pm="No kanged packs yet — reply /kang to a sticker first!",
            switch_pm_param="start",
        )
    results = [
        await e.builder.article(
            title=name,
            url=f"https://t.me/addstickers/{name}",
            description="Tap to open this pack",
            text=f"🏵 [{name}](https://t.me/addstickers/{name})",
        )
        for name in names[:20]
    ]
    await e.answer(results)

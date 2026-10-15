# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from . import get_help

__doc__ = get_help("help_blacklist")



from . import events, get_string, udB, ultroid_bot, ultroid_cmd
from pyUltroid.dB import db as dbs


@ultroid_cmd(pattern="blacklist( (.*)|$)", admins_only=True)
async def af(e):
    wrd = e.pattern_match.group(1).strip()
    chat = e.chat_id
    if not (wrd):
        return await e.eor(get_string("blk_1"), time=5)
    wrd = e.text[11:]
    heh = wrd.split(" ")
    for z in heh:
        dbs.blacklist.add(int(chat), z.lower())
    ultroid_bot.add_handler(blacklist, events.NewMessage(incoming=True))
    await e.eor(get_string("blk_2").format(wrd))


@ultroid_cmd(pattern="remblacklist( (.*)|$)", admins_only=True)
async def rf(e):
    wrd = e.pattern_match.group(1).strip()
    chat = e.chat_id
    if not wrd:
        return await e.eor(get_string("blk_3"), time=5)
    wrd = e.text[14:]
    heh = wrd.split(" ")
    for z in heh:
        dbs.blacklist.remove(int(chat), z.lower())
    await e.eor(get_string("blk_4").format(wrd))


@ultroid_cmd(pattern="listblacklist$", admins_only=True)
async def lsnote(e):
    if x := dbs.blacklist.list(e.chat_id):
        sd = get_string("blk_5")
        return await e.eor(sd + x)
    await e.eor(get_string("blk_6"))


async def blacklist(e):
    if x := dbs.blacklist.get(e.chat_id):
        text = e.text.lower().split()
        if any((z in text) for z in x):
            try:
                await e.delete()
            except Exception:
                pass


if udB.get_key("BLACKLIST_DB"):
    ultroid_bot.add_handler(blacklist, events.NewMessage(incoming=True))

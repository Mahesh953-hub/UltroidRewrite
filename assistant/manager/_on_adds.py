# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from telethon import events

from pyUltroid._core.richmsg import RichBlocks, send_rich

from . import *


@asst.on(events.ChatAction(func=lambda x: x.user_added))
async def dueha(e):
    user = await e.get_user()
    if user is None or not getattr(user, "is_self", False):
        return
    sm = udB.get_key("ON_MNGR_ADD")
    if sm is False or (
        isinstance(sm, str) and sm.strip().casefold() in {"off", "false", "0"}
    ):
        return
    if not sm:
        sm = "Thanks for adding me."
    blocks = RichBlocks().paragraph(str(sm))
    await send_rich(
        e.client,
        e.chat_id,
        blocks,
        reply_to=e,
        link_preview=False,
    )

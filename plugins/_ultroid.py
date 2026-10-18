# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

import html

from telethon.errors import (
    BotMethodInvalidError,
    ChatSendInlineForbiddenError,
    ChatSendMediaForbiddenError,
)

from custom.tg import ikm

from pyUltroid._core._cbs import cb

from . import HNDLR, LOG_CHANNEL, LOGS, Button, asst, eor, get_string, ultroid_cmd

REPOMSG = """
• **ULTROID USERBOT** •\n
• Repo - [Click Here](https://github.com/TeamUltroid/Ultroid)
• Addons - [Click Here](https://github.com/TeamUltroid/UltroidAddons)
• Support - @UltroidSupportChat
"""

RP_BUTTONS = ikm(
    [
        (
            get_string("bot_3"),
            "url",
            "https://github.com/TeamUltroid/Ultroid",
            "green",
        ),
        ("Addons", "url", "https://github.com/TeamUltroid/UltroidAddons", "green"),
        [("Support Group", "url", "t.me/UltroidSupportChat", "primary")],
    ],
)

ULTSTRING = (
    "<b>Ultroid is ready</b>\n\n"
    "Open this guide for the first steps, useful commands, and support links."
)

DEPLOYMENT_FALLBACK = (
    "<b>Ultroid is ready</b>\n\n"
    f"Start with <code>{html.escape(HNDLR)}help</code> to browse commands, "
    f"or <code>{html.escape(HNDLR)}cmds</code> for the complete command index.\n\n"
    '<a href="https://t.me/UltroidSupportChat">Support</a>'
)


async def _send_fallback_guide(client, target):
    """Send a usable text guide when the bot cannot deliver the paged card."""
    try:
        return await client.send_file(
            target,
            file="https://graph.org/file/54a917cc9dbb94733ea5f.jpg",
            caption=DEPLOYMENT_FALLBACK,
            parse_mode="html",
        )
    except Exception:
        return await client.send_message(
            target,
            DEPLOYMENT_FALLBACK,
            parse_mode="html",
            link_preview=False,
        )


@ultroid_cmd(
    pattern="repo$",
    manager=True,
)
async def repify(e):
    try:
        q = await e.client.inline_query(asst.me.username, "")
        await q[0].click(e.chat_id)
        return await e.delete()
    except (
        ChatSendInlineForbiddenError,
        ChatSendMediaForbiddenError,
        BotMethodInvalidError,
    ):
        pass
    except Exception as er:
        LOGS.info(f"Error while repo command : {str(er)}")
    await e.eor(REPOMSG)


@ultroid_cmd(pattern="ultroid$")
async def useUltroid(rs):
    button = ikm([("Start", cb("assistant", "initial", "next", 2), "success")])
    target = LOG_CHANNEL or rs.chat_id
    try:
        msg = await asst.send_file(
            target,
            file="https://graph.org/file/54a917cc9dbb94733ea5f.jpg",
            caption=ULTSTRING,
            buttons=button,
            parse_mode="html",
        )
    except Exception as exc:
        LOGS.warning(
            "Setup guide could not be sent by the assistant bot (%s); trying the user client.",
            exc.__class__.__name__,
        )
        try:
            msg = await _send_fallback_guide(rs.client, target)
        except Exception as fallback_exc:
            if target != rs.chat_id:
                try:
                    msg = await _send_fallback_guide(rs.client, rs.chat_id)
                except Exception as final_exc:
                    LOGS.exception(final_exc)
                    return await eor(
                        rs,
                        "Could not send the setup guide. Check chat access and try again.",
                    )
            else:
                LOGS.exception(fallback_exc)
                return await eor(
                    rs,
                    "Could not send the setup guide. Check chat access and try again.",
                )
    if target == rs.chat_id:
        return await rs.delete()
    if not (rs.chat_id == LOG_CHANNEL and rs.client._bot):
        link = getattr(msg, "message_link", None)
        if link:
            await eor(rs, f"**[Open setup guide]({link})**")
        else:
            await eor(rs, "Setup guide sent to the configured log channel.")

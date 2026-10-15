# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from . import get_help

__doc__ = get_help("help_echo")


from pyUltroid.dB import db as dbs
from telethon.utils import get_display_name

from pyUltroid._core.manifest import action, toggle

from . import udB, ultroid_cmd
from pyUltroid.fns import fns


@toggle(
    label="Global Echo",
    key="global_echo",
    description="Enable or disable echo globally across all chats.",
    icon="🔊",
)
async def toggle_global_echo(event, enabled: bool) -> str:
    """Toggle echo functionality on/off for all users."""
    try:
        udB.set_key("ECHO_GLOBAL_DISABLED", not enabled)
        return "Global Echo enabled ✅" if enabled else "Global Echo disabled ❌"
    except Exception:
        udB.set_key("ECHO_GLOBAL_DISABLED", True)
        raise


@action(
    label="List Echo Users",
    key="list_echo",
    description="Show all users with echo enabled in this chat.",
    icon="📋",
)
async def action_list_echo(event) -> str:
    """One-shot: list all echo-enabled users."""
    k = dbs.echo.list(event.chat_id)
    if not k:
        return "No echo users configured in this chat."
    users = []
    for uid in k[:10]:
        try:
            ent = await event.client.get_entity(int(uid))
            users.append(get_display_name(ent)[:20])
        except Exception:
            users.append(str(uid)[:10])
    return "Echo users: " + ", ".join(users)


@ultroid_cmd(pattern="addecho( (.*)|$)")
async def echo(e):
    r = await e.get_reply_message()
    if r:
        user = r.sender_id
    else:
        try:
            user = e.text.split()[1]
            if user.startswith("@"):
                ok = await e.client.get_entity(user)
                user = ok.id
            else:
                user = int(user)
        except Exception:
            return await e.eor("Reply To A user.", time=5)
    if dbs.echo.check(e.chat_id, user):
        return await e.eor("Echo already activated for this user.", time=5)
    dbs.echo.add(e.chat_id, user)
    ok = await e.client.get_entity(user)
    user = fns.inline_mention(ok)
    await e.eor(f"Activated Echo For {user}.")


@ultroid_cmd(pattern="remecho( (.*)|$)")
async def rm(e):
    r = await e.get_reply_message()
    if r:
        user = r.sender_id
    else:
        try:
            user = e.text.split()[1]
            if user.startswith("@"):
                ok = await e.client.get_entity(user)
                user = ok.id
            else:
                user = int(user)
        except Exception:
            return await e.eor("Reply To A User.", time=5)
    if dbs.echo.check(e.chat_id, user):
        dbs.echo.remove(e.chat_id, user)
        ok = await e.client.get_entity(user)
        user = f"[{get_display_name(ok)}](tg://user?id={ok.id})"
        return await e.eor(f"Deactivated Echo For {user}.")
    await e.eor("Echo not activated for this user")


@ultroid_cmd(pattern="listecho$")
async def lstecho(e):
    if k := dbs.echo.list(e.chat_id):
        user = "**Activated Echo For Users:**\n\n"
        for x in k:
            ok = await e.client.get_entity(int(x))
            kk = f"[{get_display_name(ok)}](tg://user?id={ok.id})"
            user += f"•{kk}" + "\n"
        await e.eor(user)
    else:
        await e.eor("`List is Empty, For echo`", time=5)

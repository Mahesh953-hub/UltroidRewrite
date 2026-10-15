# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

import asyncio
import os
import random
import shutil
import time
from random import randint
from pytz import timezone

from telethon.errors import (
    ChannelsTooMuchError,
    ChatAdminRequiredError,
    UserNotParticipantError,
)
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    EditAdminRequest,
    EditPhotoRequest,
    InviteToChannelRequest,
)
from telethon.tl.functions.contacts import UnblockRequest
from telethon.tl.types import (
    ChatAdminRights,
    ChatPhotoEmpty,
    InputChatUploadedPhoto,
    InputMessagesFilterDocument,
)
from telethon.utils import get_peer_id
from decouple import config, RepositoryEnv

from .. import LOGS, ULTConfig
from ..fns import fns


def update_envs():
    """Update Var. attributes to udB"""
    from .. import udB

    _envs = [*list(os.environ)]
    if ".env" in os.listdir("."):
        [_envs.append(_) for _ in list(RepositoryEnv(config._find_file(".")).data)]
    for envs in _envs:
        if (
            envs in ["LOG_CHANNEL", "BOT_TOKEN", "BOTMODE", "DUAL_MODE", "language"]
            or envs in udB.keys()
        ):
            if _value := os.environ.get(envs):
                udB.set_key(envs, _value)
            else:
                udB.set_key(envs, config.config.get(envs))


async def startup_stuff():
    from .. import udB

    x = ["resources/auth", "resources/downloads"]
    for x in x:
        if not os.path.isdir(x):
            os.mkdir(x)

    CT = udB.get_key("CUSTOM_THUMBNAIL")
    if CT:
        path = "resources/extras/thumbnail.jpg"
        ULTConfig.thumb = path
        try:
            await fns.download_file(CT, path)
        except Exception as er:
            LOGS.exception(er)
    elif CT is False:
        ULTConfig.thumb = None
    GT = udB.get_key("GDRIVE_AUTH_TOKEN")
    if GT:
        with open("resources/auth/gdrive_creds.json", "w") as t_file:
            t_file.write(GT)

    if udB.get_key("AUTH_TOKEN"):
        udB.del_key("AUTH_TOKEN")

    MM = udB.get_key("MEGA_MAIL")
    MP = udB.get_key("MEGA_PASS")
    if MM and MP:
        with open(".megarc", "w") as mega:
            mega.write(f"[Login]\nUsername = {MM}\nPassword = {MP}")

    TZ = udB.get_key("TIMEZONE")
    if TZ and timezone:
        try:
            timezone(TZ)
            os.environ["TZ"] = TZ
            time.tzset()
        except AttributeError as er:
            LOGS.debug(er)
        except Exception:
            LOGS.critical(
                "Incorrect Timezone ,\nCheck Available Timezone From Here https://graph.org/Ultroid-06-18-2\nSo Time is Default UTC"
            )
            os.environ["TZ"] = "UTC"
            time.tzset()

    try:
        from .guestmode import clean_expired_guests_loop

        asyncio.create_task(clean_expired_guests_loop())
    except Exception as err:
        LOGS.error(f"Error starting guestmode cleanup task: {err}")


async def autobot():
    from .. import udB, ultroid_bot

    if udB.get_key("BOT_TOKEN"):
        return
    await ultroid_bot.start()
    LOGS.info("MAKING A TELEGRAM BOT FOR YOU AT @BotFather, Kindly Wait")
    who = ultroid_bot.me
    name = who.first_name + "'s Bot"
    if who.username:
        username = who.username + "_bot"
    else:
        username = "ultroid_" + (str(who.id))[5:] + "_bot"
    bf = "@BotFather"
    await ultroid_bot(UnblockRequest(bf))
    await ultroid_bot.send_message(bf, "/cancel")
    await asyncio.sleep(1)
    await ultroid_bot.send_message(bf, "/newbot")
    await asyncio.sleep(1)
    isdone = (await ultroid_bot.get_messages(bf, limit=1))[0].text
    if isdone.startswith("That I cannot do.") or "20 bots" in isdone:
        LOGS.critical(
            "Please make a Bot from @BotFather and add it's token in BOT_TOKEN, as an env var and restart me."
        )
        import sys

        sys.exit(1)
    await ultroid_bot.send_message(bf, name)
    await asyncio.sleep(1)
    isdone = (await ultroid_bot.get_messages(bf, limit=1))[0].text
    if not isdone.startswith("Good."):
        await ultroid_bot.send_message(bf, "My Assistant Bot")
        await asyncio.sleep(1)
        isdone = (await ultroid_bot.get_messages(bf, limit=1))[0].text
        if not isdone.startswith("Good."):
            LOGS.critical(
                "Please make a Bot from @BotFather and add it's token in BOT_TOKEN, as an env var and restart me."
            )
            import sys

            sys.exit(1)
    await ultroid_bot.send_message(bf, username)
    await asyncio.sleep(1)
    isdone = (await ultroid_bot.get_messages(bf, limit=1))[0].text
    await ultroid_bot.send_read_acknowledge("botfather")
    if isdone.startswith("Sorry,"):
        ran = randint(1, 100)
        username = "ultroid_" + (str(who.id))[6:] + str(ran) + "_bot"
        await ultroid_bot.send_message(bf, username)
        await asyncio.sleep(1)
        isdone = (await ultroid_bot.get_messages(bf, limit=1))[0].text
    if isdone.startswith("Done!"):
        token = isdone.split("`")[1]
        udB.set_key("BOT_TOKEN", token)
        await enable_inline(ultroid_bot, username)
        LOGS.info(
            f"Done. Successfully created @{username} to be used as your assistant bot!"
        )
    else:
        LOGS.info(
            "Please Delete Some Of your Telegram bots at @Botfather or Set Var BOT_TOKEN with token of a bot"
        )

        import sys

        sys.exit(1)


async def autopilot():
    from .. import asst, udB, ultroid_bot

    channel = udB.get_key("LOG_CHANNEL")
    new_channel = None
    entity_ok = False
    if channel:
        try:
            chat = await ultroid_bot.get_entity(channel)
            entity_ok = True
        except Exception as err:
            LOGS.warning(
                "Cannot access LOG_CHANNEL (%s)  -  the user account may not "
                "be a member. The key is preserved; re-add the account to the "
                "channel and restart to restore log forwarding.",
                channel,
                "last errors: ",
                err,
            )
    if not entity_ok and not channel:

        async def _save(exc):
            udB._cache["LOG_CHANNEL"] = ultroid_bot.me.id
            await asst.send_message(
                ultroid_bot.me.id, f"Failed to Create Log Channel due to {exc}.."
            )

        if ultroid_bot._bot:
            LOGS.warning(
                "'LOG_CHANNEL' not found or not accessible in BOT_MODE  -  "
                "continuing without a log channel. Logs will only appear in "
                "the terminal, not forwarded to Telegram."
            )
            return
        LOGS.info("Creating a Log Channel for You!")
        try:
            r = await ultroid_bot(
                CreateChannelRequest(
                    title="My Ultroid Logs",
                    about="My Ultroid Log Group\n\n Join @TeamUltroid",
                    megagroup=True,
                ),
            )
        except ChannelsTooMuchError as er:
            LOGS.critical(
                "You Are in Too Many Channels & Groups , Leave some And Restart The Bot"
            )
            return await _save(str(er))
        except Exception as er:
            LOGS.exception(er)
            LOGS.info(
                "Something Went Wrong , Create A Group and set its id on config var LOG_CHANNEL."
            )

            return await _save(str(er))
        new_channel = True
        chat = r.chats[0]
        channel = get_peer_id(chat)
        udB.set_key("LOG_CHANNEL", channel)
    if not entity_ok:
        LOGS.warning(
            "Skipping assistant log-channel setup: channel %s is not "
            "accessible. Deploy message will still be sent from the user "
            "client in ready().",
            channel,
        )
        return
    assistant = True
    try:
        asst_entity = await ultroid_bot.get_input_entity(asst.me.username)
    except Exception as er:
        asst_entity = None
        LOGS.warning("Failed to resolve assistant entity: %s", er)
        assistant = False
    if asst_entity:
        try:
            await ultroid_bot.get_permissions(int(channel), asst_entity)
        except UserNotParticipantError:
            LOGS.info(
                "Assistant not in LOG_CHANNEL  -  attempting invite via InviteToChannelRequest..."
            )
            try:
                await ultroid_bot(InviteToChannelRequest(int(channel), [asst_entity]))
                LOGS.info("Assistant invited to LOG_CHANNEL successfully.")
            except Exception as er:
                LOGS.warning(
                    "Failed to auto-invite assistant to LOG_CHANNEL (%s: %s). "
                    "Bots often cannot be added to channels programmatically  -  "
                    "add @%s manually as a member/adm in via Telegram's UI.",
                    type(er).__name__,
                    er,
                    asst.me.username,
                )
                assistant = False
        except Exception as er:
            assistant = False
            LOGS.warning("Could not check assistant permissions: %s", er)
    if assistant and new_channel:
        try:
            achat = await asst.get_entity(int(channel))
        except Exception as er:
            achat = None
            LOGS.info("Error while getting Log channel from Assistant")
            LOGS.exception(er)
        if achat and not achat.admin_rights:
            rights = ChatAdminRights(
                add_admins=True,
                invite_users=True,
                change_info=True,
                ban_users=True,
                delete_messages=True,
                pin_messages=True,
                anonymous=False,
                manage_call=True,
            )
            try:
                await ultroid_bot(
                    EditAdminRequest(
                        int(channel), asst.me.username, rights, "Assistant"
                    )
                )
            except ChatAdminRequiredError:
                LOGS.info(
                    "Failed to promote 'Assistant Bot' in 'Log Channel' due to 'Admin Privileges'"
                )
            except Exception as er:
                LOGS.info("Error while promoting assistant in Log Channel..")
                LOGS.exception(er)
    if isinstance(chat.photo, ChatPhotoEmpty):
        photo, _ = await fns.download_file(
            "https://graph.org/file/27c6812becf6f376cbb10.jpg", "channelphoto.jpg"
        )
        ll = await ultroid_bot.upload_file(photo)
        try:
            await ultroid_bot(
                EditPhotoRequest(int(channel), InputChatUploadedPhoto(ll))
            )
        except Exception as er:
            LOGS.exception(er)
        os.remove(photo)


# customize assistant


async def customize():
    from .. import asst, udB, ultroid_bot

    rem = None
    try:
        chat_id = udB.get_key("LOG_CHANNEL")
        if asst.me.photo:
            return
        LOGS.info("Customising Ur Assistant Bot in @BOTFATHER")
        UL = f"@{asst.me.username}"
        if not ultroid_bot.me.username:
            sir = ultroid_bot.me.first_name
        else:
            sir = f"@{ultroid_bot.me.username}"
        file = random.choice(
            [
                "https://graph.org/file/92cd6dbd34b0d1d73a0da.jpg",
                "https://graph.org/file/a97973ee0425b523cdc28.jpg",
                "resources/extras/ultroid_assistant.jpg",
            ]
        )
        if not os.path.exists(file):
            file, _ = await fns.download_file(file, "profile.jpg")
            rem = True
        try:
            msg = await asst.send_message(
                chat_id, "**Auto Customisation** Started on @Botfather"
            )
        except Exception:
            msg = await ultroid_bot.send_message(
                chat_id, "**Auto Customisation** Started on @Botfather"
            )
        await asyncio.sleep(1)
        await ultroid_bot.send_message("botfather", "/cancel")
        await asyncio.sleep(1)
        await ultroid_bot.send_message("botfather", "/setuserpic")
        await asyncio.sleep(1)
        isdone = (await ultroid_bot.get_messages("botfather", limit=1))[0].text
        if isdone.startswith("Invalid bot"):
            LOGS.info("Error while trying to customise assistant, skipping...")
            return
        await ultroid_bot.send_message("botfather", UL)
        await asyncio.sleep(1)
        await ultroid_bot.send_file("botfather", file)
        await asyncio.sleep(2)
        await ultroid_bot.send_message("botfather", "/setabouttext")
        await asyncio.sleep(1)
        await ultroid_bot.send_message("botfather", UL)
        await asyncio.sleep(1)
        await ultroid_bot.send_message(
            "botfather", f"✨ Hello ✨!! I'm Assistant Bot of {sir}"
        )
        await asyncio.sleep(2)
        await ultroid_bot.send_message("botfather", "/setdescription")
        await asyncio.sleep(1)
        await ultroid_bot.send_message("botfather", UL)
        await asyncio.sleep(1)
        await ultroid_bot.send_message(
            "botfather",
            f"✨ Powerful Ultroid Assistant Bot ✨\n✨ Master ~ {sir} ✨\n\n✨ Powered By ~ @TeamUltroid ✨",
        )
        await asyncio.sleep(2)
        await msg.edit("Completed **Auto Customisation** at @BotFather.")
        if rem:
            os.remove(file)
        LOGS.info("Customisation Done")
    except Exception as e:
        LOGS.exception(e)


async def plug(plugin_channels):
    from .. import ultroid_bot
    from .utils import load_addons

    if ultroid_bot._bot:
        LOGS.info("Plugin Channels can't be used in 'BOTMODE'")
        return
    if os.path.exists("addons") and not os.path.exists("addons/.git"):
        shutil.rmtree("addons")
    if not os.path.exists("addons"):
        os.mkdir("addons")
    if not os.path.exists("addons/__init__.py"):
        with open("addons/__init__.py", "w") as f:
            f.write("from plugins import *\n\nbot = ultroid_bot")
    LOGS.info("• Loading Plugins from Plugin Channel(s) •")
    for chat in plugin_channels:
        LOGS.info(f"{'•' * 4} {chat}")
        try:
            async for x in ultroid_bot.iter_messages(
                chat, search=".py", filter=InputMessagesFilterDocument, wait_time=10
            ):
                plugin = "addons/" + x.file.name.replace("_", "-").replace("|", "-")
                if not os.path.exists(plugin):
                    await asyncio.sleep(0.6)
                    if x.text == "#IGNORE":
                        continue
                    plugin = await x.download_media(plugin)
                    try:
                        load_addons(plugin)
                    except Exception as e:
                        LOGS.info(f"Ultroid - PLUGIN_CHANNEL - ERROR - {plugin}")
                        LOGS.exception(e)
                        os.remove(plugin)
        except Exception as er:
            LOGS.exception(er)


def _runtime_stats() -> str:
    from .. import start_time
    from ..dB._core import HELP, LIST
    from ..fns import fns

    patches = 0
    rich = False
    try:
        from .._core.entities import RICH_MD
        from ..startup.patching import get_patch_registry

        patches = len(get_patch_registry().list_patches())
        rich = bool(getattr(RICH_MD, "available", False))
    except Exception:
        pass

    commands = sum(len(v) for v in LIST.values())
    rows = [
        ("Metric", "Value"),
        ("Total Plugins", str(len(HELP.get("Official", [])))),
        ("Total Addons", str(len(HELP.get("Addons", [])))),
        ("Registered Commands", str(commands)),
        ("Patches", str(patches)),
        ("Time Took", fns.time_formatter((time.time() - start_time) * 1000)),
    ]
    if rich:
        header = "| " + " | ".join(rows[0]) + " |"
        sep = "| " + " | ".join("---" for _ in rows[0]) + " |"
        return "\n".join([header, sep] + [f"| {' | '.join(r)} |" for r in rows[1:]])
    width = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    body = "\n".join(
        "  ".join(r[i].ljust(width[i]) for i in range(len(r))) for r in rows
    )
    return f"```\n{body}\n```"


def _rich_parse_mode():
    try:
        from .._core.entities import RICH_MD

        return RICH_MD if getattr(RICH_MD, "available", False) else None
    except Exception:
        return None


async def ready():
    from .. import asst, udB, ultroid_bot
    from custom.tg import ikm

    if asst and ultroid_bot and asst != ultroid_bot:
        try:
            from .business import setup_business_mode

            await setup_business_mode(ultroid_bot, asst)
        except Exception as err:
            LOGS.error(f"Failed to setup Business Mode: {err}")

    chat_id = udB.get_key("LOG_CHANNEL")
    if not chat_id:
        LOGS.warning("No LOG_CHANNEL set  -  skipping deploy message.")
        return

    spam_sent = None
    stats_table = _runtime_stats()
    parse_mode = _rich_parse_mode()

    from .._core._cbs import cb
    from ..dB._core import HELP, LIST
    from ..version import ultroid_version

    bot_note = ""
    if not udB.get_key("INIT_DEPLOY"):
        bot_in_channel = False
        if asst and asst != ultroid_bot:  # user + bot mode
            try:
                await ultroid_bot.get_permissions(int(chat_id), asst.me.username)
                bot_in_channel = True
            except Exception:
                pass
        if not bot_in_channel:
            bot_note = (
                "⚠️ **Assistant** @{bot} was **not** added to this channel  -  "
                "the invite failed (the bot account may need to be added "
                "manually by a channel admin). Log forwarding and inline "
                "callbacks will not work until the bot joins."
            ).format(bot=asst.me.username)

    n_plugins = len(HELP.get("Official", []))
    n_addons = len(HELP.get("Addons", []))
    commands = sum(len(v) for v in LIST.values())
    try:
        from ..ultroid import core

        pmf_count = len(getattr(core.manifest, "_entries", {}))
    except Exception:
        pmf_count = 0

    info_lines = [
        f"• __UserMode:__ {fns.inline_mention(ultroid_bot.me)}",
        f"• __Assistant:__ @{asst.me.username}",
        f"• __Version:__ `{ultroid_version}`",
    ]
    alert_note = bot_note or "• All subsystems nominal  -  no alerts."
    math_block = (
        f"`plugins({n_plugins}) + addons({n_addons}) = {n_plugins + n_addons} modules`\n"
        f"`handlers = {commands} · pmf = {pmf_count}`"
    )
    MSG = "\n".join(
        [
            "> **Ultroid is UP and running!**",
            "",
            "__ℹ️ Info:__",
            *info_lines,
            "",
            "__⚠️ Alert:__",
            alert_note,
            "",
            "__🧮 Registry:__",
            math_block,
            "",
            stats_table,
        ]
    )

    if not udB.get_key("INIT_DEPLOY"):  # Detailed Message at Initial Deploy
        PHOTO = "https://graph.org/file/54a917cc9dbb94733ea5f.jpg"
        BTTS = ikm([("start", cb("assistant", "initial", "next", 2), "success", "go")])
        udB.set_key("INIT_DEPLOY", "Done")
    else:
        BTTS, PHOTO = None, None
        prev_spam = udB.get_key("LAST_UPDATE_LOG_SPAM")
        if prev_spam:
            try:
                await asst.delete_messages(chat_id, int(prev_spam))
            except Exception as E:
                LOGS.info("Error while Deleting Previous Update Message :" + str(E))
        if await fns.updamter():
            BTTS = ikm([("Update Available", "updtavail", "success")])
        else:
            LOGS.info("No Updates")

    if asst is None:
        spam_sent = None
        LOGS.warning(
            "ready(): no assistant bot configured  -  deploy message skipped (never user client)."
        )
    else:
        LOGS.info("ready(): sending deploy message to LOG_CHANNEL via assistant bot.")
        send_kwargs = {"file": PHOTO, "buttons": BTTS}
        if parse_mode is not None:
            send_kwargs["parse_mode"] = parse_mode
        try:
            spam_sent = await asst.send_message(chat_id, MSG, **send_kwargs)
            LOGS.info(
                "ready(): deploy message sent via assistant (msg_id=%s).", spam_sent.id
            )
        except Exception as ef:
            LOGS.exception(ef)

    if spam_sent and not spam_sent.media:
        udB.set_key("LAST_UPDATE_LOG_SPAM", spam_sent.id)


async def WasItRestart(udb):
    key = udb.get_key("_RESTART")
    if not key:
        return
    from .. import asst, ultroid_bot, udB

    try:
        data = key.split("_")
        if len(data) < 3:
            udb.del_key("_RESTART")
            return
        who_str = data[0]
        chat_id = int(data[1])
        msg_id = int(data[2])

        # Determine which client to use first
        first_client = asst if who_str == "bot" else ultroid_bot
        second_client = ultroid_bot if who_str == "bot" else asst

        async def try_edit(client, label):
            try:
                await client.edit_message(
                    chat_id, msg_id, "__Restarted Successfully.__"
                )
                return True
            except Exception as e:
                LOGS.debug(f"[WasItRestart] {label} edit failed: {e}")
                return False

        # Try primary client
        if await try_edit(first_client, who_str):
            udb.del_key("_RESTART")
            return

        # Try fallback client
        if await try_edit(second_client, "fallback"):
            udb.del_key("_RESTART")
            return

        # Both failed - try to send new message as reply
        async def try_send(client, label):
            try:
                await client.send_message(
                    chat_id, "__Restarted Successfully.__", reply_to=msg_id
                )
                return True
            except Exception as e:
                LOGS.debug(f"[WasItRestart] {label} send failed: {e}")
                return False

        if await try_send(first_client, who_str):
            udb.del_key("_RESTART")
            return

        if await try_send(second_client, "fallback"):
            udb.del_key("_RESTART")
            return

        # Last resort: send to LOG_CHANNEL
        log_ch = udB.get_key("LOG_CHANNEL")
        if log_ch:
            try:
                await asst.send_message(log_ch, "__Restarted Successfully.__")
            except Exception:
                pass

    except Exception as er:
        LOGS.exception(er)
    udb.del_key("_RESTART")


def _version_changes(udb):
    for _ in [
        "BOT_USERS",
        "BOT_BLS",
        "VC_SUDOS",
        "SUDOS",
        "CLEANCHAT",
        "LOGUSERS",
        "PLUGIN_CHANNEL",
        "CH_SOURCE",
        "CH_DESTINATION",
        "BROADCAST",
    ]:
        key = udb.get_key(_)
        if key and str(key)[0] != "[":
            key = udb.get(_)
            new_ = [
                int(z) if z.isdigit() or (z.startswith("-") and z[1:].isdigit()) else z
                for z in key.split()
            ]
            udb.set_key(_, new_)


async def enable_inline(ultroid_bot, username):
    bf = "BotFather"
    await ultroid_bot.send_message(bf, "/setinline")
    await asyncio.sleep(1)
    await ultroid_bot.send_message(bf, f"@{username}")
    await asyncio.sleep(1)
    await ultroid_bot.send_message(bf, "Search")
    await ultroid_bot.send_read_acknowledge(bf)

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}kickme` : Leaves the group.

• `{i}date [timezone]` : Show Calender (optionally in a given timezone,
    e.g. `{i}date Asia/Kolkata`).

• `{i}listreserved`
    List all usernames (channels/groups) you own.

• `{i}stats` : See your profile stats.

• `{i}paste` - `Include long text / Reply to text file.`

• `{i}info <username/userid/chatid>`
    Reply to someone's msg.

• `{i}invite <username/userid>`
    Add user to the chat.

• `{i}rmbg <reply to pic>`
    Remove background from that picture.

• `{i}telegraph <reply to media/text>`
    Upload media/text to telegraph.

• `{i}json <reply to msg>`
    Get the json encoding of the message.

• `{i}suggest <reply to message> or <poll title>`
    Create a Yes/No poll for the replied suggestion.

• `{i}quiz <question> | <opt1> | <opt2> [| ...] [| answer=N] [| solution=text]`
    Send a quiz-mode poll. `answer=N` (1-indexed, default 1) marks the
    correct option; `solution=<text>` is shown to voters after they answer
    (supports MarkdownV3 formatting).
    e.g. `{i}quiz 2 + 2 = ? | 3 | 4 | 5 | answer=2 | solution=**Basic maths.**`

• `{i}ipinfo <ipAddress>` : Get info about that IP address.

• `{i}cpy <reply to message>`
   Copy the replied message, with formatting. Expires in 24hrs.
• `{i}pst`
   Paste the copied message, with formatting.

• `{i}thumb <reply file>` : Download the thumbnail of the replied file.

• `{i}getmsg <message link>`
  Get messages from chats with forward/copy restrictions.

• `{i}lastpm` : Get details of the last pm you recieved
"""

from pyUltroid.dB import db as dbs
import asyncio
import calendar
import html
import io
import os
import pathlib
import re
import time
from datetime import datetime as dt

try:
    from PIL import Image
except ImportError:
    Image = None

from pyUltroid.fns import fns

try:
    from telegraph import upload_file as uf
except ImportError:
    uf = None

from telethon.errors.rpcerrorlist import (
    ChatForwardsRestrictedError,
    UserBotError,
    MessageTooLongError,
)
from telethon.events import NewMessage
from telethon.tl.custom import Dialog
from telethon.tl.functions.channels import (
    GetAdminedPublicChannelsRequest,
    InviteToChannelRequest,
    LeaveChannelRequest,
)
from telethon.tl.functions.contacts import GetBlockedRequest
from telethon.tl.functions.messages import AddChatUserRequest, GetAllStickersRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import (
    Channel,
    Chat,
    InputMediaPoll,
    Poll,
    PollAnswer,
    TextWithEntities,
    TLObject,
    User,
    UserStatusOffline,
    UserStatusOnline,
    MessageMediaPhoto,
    MessageMediaDocument,
    DocumentAttributeVideo,
)
from telethon.utils import get_peer_id

from pyUltroid._core.entities import mdv3_to_entities
from pyUltroid.fns import fns

from . import (
    HNDLR,
    LOGS,
    Image,
    Telegraph,
    asst,
    eod,
    eor,
    get_string,
    udB,
    ultroid_cmd,
    asst_cmd,
)

import motor.motor_asyncio
import pytz
from telethon import events, types

from . import *

# Telegram caption limit, used by getmsg() fallbacks.
CAPTION_LIMIT = 1024

lastSeendB = "mongodb+srv://LastSeenUlt:YKzBfhfjtObPfQLD@cluster0.iil65vg.mongodb.net/"

# MongoDB client setup (motor connects lazily, so this is safe at import time).
mongo_client = motor.motor_asyncio.AsyncIOMotorClient(lastSeendB)
db = mongo_client["User_Status"]
collection = db["user_data"]


def _safe_timezone(name):
    """Return a ``pytz`` timezone for *name*, falling back to UTC on any error."""
    if not name:
        name = "UTC"
    try:
        return pytz.timezone(name)
    except Exception:
        LOGS.debug(f"Unknown timezone {name!r}, falling back to UTC.")
        return pytz.utc


# UTC and the configured (or fallback) local timezone.
utc_tz = pytz.utc
perth_tz = _safe_timezone(udB.get_key("TIMEZONE"))


async def last_online_info(event, user_id):
    """Return a plain, human-readable last-online string for *user_id*.

    The caller wraps the result in ``<code>...</code>``, so this must return
    plain text (never pre-wrapped HTML) in every code path.
    """
    user = await event.client.get_entity(user_id)

    if user.bot:
        return "Bot — status not tracked"

    try:
        db_user = await collection.find_one({"user_id": user.id})
        last_online_db = db_user.get("last_online_time") if db_user else None
        if last_online_db:
            last_online_db = last_online_db.replace(tzinfo=utc_tz).astimezone(perth_tz)
            return last_online_db.strftime("%d/%m/%Y %I:%M:%S %p %Z%z")
    except Exception as e:
        LOGS.error(f"last_online_info error: {e}")
    return "Unknown"


# =================================================================#

TMP_DOWNLOAD_DIRECTORY = "resources/downloads/"

_copied_msg = {}


@ultroid_cmd(pattern="kickme$", fullsudo=True)
async def leave(ult):
    await ult.eor(f"`{ult.client.me.first_name} has left this group, bye!!.`")
    try:
        await ult.client(LeaveChannelRequest(ult.chat_id))
    except Exception as e:
        await ult.eor(f"`Failed to leave the chat: {e}`", time=8)


@ultroid_cmd(
    pattern="date( (.*)|$)",
)
async def date(event):
    raw = event.pattern_match.group(1)
    match = raw.strip() if raw else ""
    tz = _safe_timezone(match) if match else None
    now = dt.now(tz) if tz else dt.now()
    k = calendar.month(now.year, now.month)
    d = now.strftime("Date - %B %d, %Y\nTime- %H:%M:%S")
    label = f" ({tz.zone})" if tz and match else ""
    await event.eor(f"`{k}\n\n{d}{label}`")


@ultroid_cmd(
    pattern="listreserved$",
)
async def _(event):
    try:
        result = await event.client(GetAdminedPublicChannelsRequest())
    except Exception as e:
        return await event.eor(f"`Failed to fetch reserved usernames: {e}`", time=8)
    if not result.chats:
        return await event.eor("`No username Reserved`")
    output_str = "".join(
        f"- {channel_obj.title} @{channel_obj.username} \n"
        for channel_obj in result.chats
    )
    await event.eor(output_str)


@ultroid_cmd(
    pattern="stats$",
)
async def stats(
    event: NewMessage.Event,
):
    ok = await event.eor("`Collecting stats...`")
    start_time = time.time()
    private_chats = 0
    bots = 0
    groups = 0
    broadcast_channels = 0
    admin_in_groups = 0
    creator_in_groups = 0
    admin_in_broadcast_channels = 0
    creator_in_channels = 0
    unread_mentions = 0
    unread = 0
    dialog: Dialog
    try:
        async for dialog in event.client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, Channel) and entity.broadcast:
                broadcast_channels += 1
                if entity.creator or entity.admin_rights:
                    admin_in_broadcast_channels += 1
                if entity.creator:
                    creator_in_channels += 1

            elif (isinstance(entity, Channel) and entity.megagroup) or isinstance(
                entity, Chat
            ):
                groups += 1
                if entity.creator or entity.admin_rights:
                    admin_in_groups += 1
                if entity.creator:
                    creator_in_groups += 1

            elif isinstance(entity, User):
                private_chats += 1
                if entity.bot:
                    bots += 1

            unread_mentions += dialog.unread_mentions_count
            unread += dialog.unread_count
    except Exception as e:
        return await ok.edit(f"`Failed to collect stats: {e}`")
    stop_time = time.time() - start_time
    try:
        ct = (await event.client(GetBlockedRequest(1, 0))).count
    except AttributeError:
        ct = 0
    try:
        sp = await event.client(GetAllStickersRequest(0))
        sp_count = len(sp.sets)
    except Exception:
        sp_count = 0
    full_name = fns.inline_mention(event.client.me)
    response = f"🔸 **Stats for {full_name}** \n\n"
    response += f"**Private Chats:** {private_chats} \n"
    response += f"**  •• **`Users: {private_chats - bots}` \n"
    response += f"**  •• **`Bots: {bots}` \n"
    response += f"**Groups:** {groups} \n"
    response += f"**Channels:** {broadcast_channels} \n"
    response += f"**Admin in Groups:** {admin_in_groups} \n"
    response += f"**  •• **`Creator: {creator_in_groups}` \n"
    response += f"**  •• **`Admin Rights: {admin_in_groups - creator_in_groups}` \n"
    response += f"**Admin in Channels:** {admin_in_broadcast_channels} \n"
    response += f"**  •• **`Creator: {creator_in_channels}` \n"
    response += f"**  •• **`Admin Rights: {admin_in_broadcast_channels - creator_in_channels}` \n"
    response += f"**Unread:** {unread} \n"
    response += f"**Unread Mentions:** {unread_mentions} \n"
    response += f"**Blocked Users:** {ct}\n"
    response += f"**Total Stickers Pack Installed :** `{sp_count}`\n\n"
    response += f"**__It Took:__** {stop_time:.02f}s \n"
    await ok.edit(response)


@ultroid_cmd(pattern="paste( (.*)|$)", manager=True, allow_all=True)
async def _(event):
    try:
        event_text = event.text
        if isinstance(event_text, tuple):
            event_text = event_text[0] if event_text else ""
        input_str = str(event_text or "").split(maxsplit=1)[1]
    except (IndexError, AttributeError, TypeError):
        input_str = None
    xx = await event.eor("` 《 Pasting... 》 `")
    downloaded_file_name = None
    if input_str:
        message = input_str
    elif event.reply_to_msg_id:
        previous_message = await event.get_reply_message()
        if previous_message.media:
            downloaded_file_name = await event.client.download_media(
                previous_message,
                "./resources/downloads",
            )
            try:
                with open(
                    downloaded_file_name, encoding="utf-8", errors="ignore"
                ) as fd:
                    message = fd.read()
            except Exception as e:
                try:
                    os.remove(downloaded_file_name)
                except OSError:
                    pass
                return await xx.eor(f"`Failed to read file: {e}`", time=8)
            os.remove(downloaded_file_name)
        else:
            message = previous_message.message
    else:
        message = None
    if not message:
        return await xx.eor(
            "`Reply to a Message/Document or Give me Some Text !`", time=5
        )
    done, key = await fns.get_paste(message)
    if not done:
        return await xx.eor(key)
    link = f"https://spaceb.in/{key}"
    raw = f"https://spaceb.in/api/{key}/raw"
    reply_text = (
        f"• **Pasted to SpaceBin :** [Space]({link})\n• **Raw Url :** : [Raw]({raw})"
    )
    try:
        if event.client._bot:
            return await xx.eor(reply_text)
        ok = await event.client.inline_query(asst.me.username, f"pasta-{key}")
        await ok[0].click(event.chat_id, reply_to=event.reply_to_msg_id, hide_via=True)
        await xx.delete()
    except Exception as e:
        LOGS.exception(e)
        await xx.edit(reply_text)


@ultroid_cmd(
    pattern="info( (.*)|$)",
    manager=True,
)
async def _(event):
    if match := event.pattern_match.group(1).strip():
        try:
            user = await event.client.parse_id(match)
        except Exception as er:
            return await event.eor(str(er))
    elif event.is_reply:
        rpl = await event.get_reply_message()
        user = rpl.sender_id
    else:
        user = event.chat_id
    xx = await event.eor(get_string("com_1"))
    user_id = user
    try:
        _ = await event.client.get_entity(user)
    except Exception as er:
        return await xx.edit(f"**ERROR :** {er}")
    if not isinstance(_, User):
        try:
            peer = get_peer_id(_)
            photo, capt = await fns.get_chat_info(_, event)
            if dbs.gban_mute.is_gbanned(peer):
                capt += "\n•<b> Is Gbanned:</b> <code>True</code>"
            if not photo:
                return await xx.eor(capt, parse_mode="html")
            await event.client.send_message(
                event.chat_id, capt[:1024], file=photo, parse_mode="html"
            )
            await xx.delete()
        except Exception as er:
            await event.eor("**ERROR ON CHATINFO**\n" + str(er))
        return
    try:
        full_user = (await event.client(GetFullUserRequest(user))).full_user
    except Exception as er:
        return await xx.edit(f"ERROR : {er}")
    user = await event.client.get_entity(user_id)
    user_status = user.status
    if isinstance(user_status, UserStatusOffline):
        ostatus = await last_online_info(event, user.id)
        last_online_status = f"<b>•Last Online</b>: <code>{ostatus}</code>"
    elif isinstance(user_status, UserStatusOnline):
        last_online_status = "<b>•Last Online</b>: <code>Currently Online</code>"
    else:
        try:
            last_online_status = f"<b>•Last Online</b>: <code>{await last_online_info(event, user.id)}</code>"
        except Exception:
            last_online_status = f"<b>•Last Online</b>: <code>Unknown</code>"
    user = _
    user_photos = (
        await event.client.get_profile_photos(user.id, limit=0)
    ).total or "NaN"
    user_id = user.id
    first_name = html.escape((user.first_name or "").replace("\u2060", "")) or (
        "First Name not found"
    )
    last_name = user.last_name
    last_name = (
        html.escape(last_name.replace("\u2060", ""))
        if last_name
        else "Last Name not found"
    )
    user_bio = full_user.about
    user_bio = html.escape(user_bio) if user_bio else "No Bio"
    common_chats = full_user.common_chats_count
    if user.photo:
        dc_id = user.photo.dc_id
    else:
        dc_id = "Need a Profile Picture to check this"
    caption = """<b>Exᴛʀᴀᴄᴛᴇᴅ Dᴀᴛᴀ Fʀᴏᴍ Tᴇʟᴇɢʀᴀᴍ's Dᴀᴛᴀʙᴀsᴇ</b>
<b>••Tᴇʟᴇɢʀᴀᴍ ID</b>: <code>{}</code>
<b>••Pᴇʀᴍᴀɴᴇɴᴛ Lɪɴᴋ</b>: <a href='tg://user?id={}'>Click Here</a>
<b>••Fɪʀsᴛ Nᴀᴍᴇ</b>: <code>{}</code>
<b>••Sᴇᴄᴏɴᴅ Nᴀᴍᴇ</b>: <code>{}</code>
<b>••Bɪᴏ</b>: <code>{}</code>
<b>••Dᴄ ID</b>: <code>{}</code>
<b>••Nᴏ. Oғ PғPs</b> : <code>{}</code>
<b>••Is Rᴇsᴛʀɪᴄᴛᴇᴅ</b>: <code>{}</code>
<b>••Vᴇʀɪғɪᴇᴅ</b>: <code>{}</code>
<b>••Is Pʀᴇᴍɪᴜᴍ</b>: <code>{}</code>
<b>••Is A Bᴏᴛ</b>: <code>{}</code>
<b>••Gʀᴏᴜᴘs Iɴ Cᴏᴍᴍᴏɴ</b>: <code>{}</code>
{}
""".format(
        user_id,
        user_id,
        first_name,
        last_name,
        user_bio,
        dc_id,
        user_photos,
        user.restricted,
        user.verified,
        user.premium,
        user.bot,
        common_chats,
        last_online_status,
    )
    if chk := dbs.gban_mute.is_gbanned(user_id):
        caption += f"""<b>••Gʟᴏʙᴀʟʟʏ Bᴀɴɴᴇᴅ</b>: <code>True</code>
<b>••Rᴇᴀsᴏɴ</b>: <code>{chk}</code>"""
    await event.client.send_message(
        event.chat_id,
        caption,
        reply_to=event.reply_to_msg_id,
        parse_mode="HTML",
        file=full_user.profile_photo,
        force_document=False,
        silent=True,
    )
    await xx.delete()


@ultroid_cmd(
    pattern="invite( (.*)|$)",
    groups_only=True,
)
async def _(ult):
    xx = await ult.eor(get_string("com_1"))
    to_add_users = ult.pattern_match.group(1).strip()
    if not to_add_users:
        return await eod(
            ult,
            f"Give me a username/id to add, e.g. `{HNDLR}invite @username`",
            time=8,
        )
    if not ult.is_channel and ult.is_group:
        for user_id in to_add_users.split(" "):
            try:
                await ult.client(
                    AddChatUserRequest(
                        chat_id=ult.chat_id,
                        user_id=await ult.client.parse_id(user_id),
                        fwd_limit=1000000,
                    ),
                )
                await xx.edit(f"Successfully invited `{user_id}` to `{ult.chat_id}`")
            except Exception as e:
                await xx.edit(str(e))
    else:
        for user_id in to_add_users.split(" "):
            try:
                await ult.client(
                    InviteToChannelRequest(
                        channel=ult.chat_id,
                        users=[await ult.client.parse_id(user_id)],
                    ),
                )
                await xx.edit(f"Successfully invited `{user_id}` to `{ult.chat_id}`")
            except UserBotError:
                await xx.edit(
                    f"Bots can only be added as Admins in Channel.\nBetter Use `{HNDLR}promote {user_id}`"
                )
            except Exception as e:
                await xx.edit(str(e))


@ultroid_cmd(
    pattern="rmbg($| (.*))",
)
async def abs_rmbg(event):
    RMBG_API = udB.get_key("RMBG_API")
    if not RMBG_API:
        return await event.eor(
            "Get your API key from [here](https://www.remove.bg/) for this plugin to work.",
        )
    match = event.pattern_match.group(1).strip()
    reply = await event.get_reply_message()
    if match and os.path.exists(match):
        dl = match
    elif reply and reply.media:
        try:
            if reply.document and reply.document.thumbs:
                dl = await reply.download_media(thumb=-1)
            else:
                dl = await reply.download_media()
        except Exception as e:
            return await event.eor(f"`Failed to download media: {e}`", time=8)
    else:
        return await eod(
            event, f"Use `{HNDLR}rmbg` as reply to a pic to remove its background."
        )
    if not (dl and dl.endswith(("webp", "jpg", "png", "jpeg"))):
        if dl and os.path.exists(dl):
            try:
                os.remove(dl)
            except OSError:
                pass
        return await event.eor(get_string("com_4"))
    if dl.endswith("webp"):
        file = f"{dl}.png"
        Image.open(dl).save(file)
        os.remove(dl)
        dl = file
    xx = await event.eor("`Sending to remove.bg`")
    dn, out = await fns.ReTrieveFile(dl)
    os.remove(dl)
    if not dn:
        dr = out["errors"][0]
        de = dr.get("detail", "")
        return await xx.edit(
            f"**ERROR ~** `{dr['title']}`,\n`{de}`",
        )
    zz = Image.open(out)
    if zz.mode != "RGB":
        zz = zz.convert("RGB")
    wbn = fns.check_filename("ult-rmbg.webp")
    zz.save(wbn, "webp")
    await event.client.send_file(
        event.chat_id,
        out,
        force_document=True,
        reply_to=reply,
    )
    await event.client.send_file(event.chat_id, wbn, reply_to=reply)
    os.remove(out)
    os.remove(wbn)
    await xx.delete()


@ultroid_cmd(
    pattern="telegraph( (.*)|$)",
)
async def telegraphcmd(event):
    xx = await event.eor(get_string("com_1"))
    match = event.pattern_match.group(1).strip() or "Ultroid"
    reply = await event.get_reply_message()
    if not reply:
        return await xx.eor("`Reply to Message.`")
    if not reply.media and reply.message:
        content = reply.message
    else:
        try:
            getit = await reply.download_media()
        except Exception as e:
            return await xx.eor(f"`Failed to download media: {e}`", time=8)
        if not getit:
            return await xx.eor("`Failed to download media.`", time=5)
        dar = fns.mediainfo(reply.media)
        try:
            if dar == "sticker":
                file = f"{getit}.png"
                Image.open(getit).save(file)
                os.remove(getit)
                getit = file
            elif dar.endswith("animated"):
                file = f"{getit}.gif"
                await fns.bash(f"lottie_convert.py '{getit}' {file}")
                os.remove(getit)
                getit = file
            if "document" not in dar:
                if not uf:
                    amsg = "`Telegraph module not installed, cannot upload media.`"
                else:
                    try:
                        nn = f"https://graph.org{uf(getit)[0]}"
                        amsg = f"Uploaded to [Telegraph]({nn}) !"
                    except Exception as e:
                        amsg = f"Error : {e}"
                return await xx.eor(amsg)
            content = pathlib.Path(getit).read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            return await xx.eor(f"`Failed to process media: {e}`", time=8)
        finally:
            try:
                os.remove(getit)
            except OSError:
                pass
    try:
        makeit = Telegraph.create_page(title=match, content=[content])
    except Exception as e:
        return await xx.eor(f"`Failed to create Telegraph page: {e}`", time=8)
    await xx.eor(
        f"Pasted to Telegraph : [Telegraph]({makeit['url']})", link_preview=False
    )


@ultroid_cmd(pattern="json( (.*)|$)")
async def _(event):
    reply_to_id = None
    match = event.pattern_match.group(1).strip()
    if event.reply_to_msg_id:
        msg = await event.get_reply_message()
        reply_to_id = event.reply_to_msg_id
    else:
        msg = event
        reply_to_id = event.message.id
    if match and hasattr(msg, match.split()[0]):
        msg = getattr(msg, match.split()[0])
        try:
            if hasattr(msg, "to_json"):
                msg = msg.to_json(ensure_ascii=False, indent=1)
            elif hasattr(msg, "to_dict"):
                msg = fns.json_parser(msg.to_dict(), indent=1)
            else:
                msg = TLObject.stringify(msg)
        except Exception:
            pass
        msg = str(msg)
    else:
        msg = fns.json_parser(msg.to_json(), indent=1)
    if "-t" in match:
        try:
            data = fns.json_parser(msg)
            msg = fns.json_parser(
                {key: data[key] for key in data.keys() if data[key]}, indent=1
            )
        except Exception:
            pass
    if len(msg) > 4096:
        with io.BytesIO(str.encode(msg)) as out_file:
            out_file.name = "json-ult.txt"
            await event.client.send_file(
                event.chat_id,
                out_file,
                force_document=True,
                allow_cache=False,
                reply_to=reply_to_id,
            )
            await event.delete()
    else:
        await event.eor(f"```{msg or None}```")


def _twe(text):
    """Wrap plain *text* in a ``TextWithEntities`` (no formatting entities).

    The pinned Mahesh953-hub/Telethon fork's schema moved ``Poll.question`` and
    ``PollAnswer.text`` from plain ``string`` to ``TextWithEntities`` (see
    ``telethon_generator/data/api.tl``); a bare ``str`` blows up at
    serialize-time with ``AttributeError: 'str' object has no attribute
    '_bytes'``. This tiny helper is the fix — every poll/quiz question or
    answer must go through it before being handed to ``Poll``/``PollAnswer``.
    """
    return TextWithEntities(text=text or "", entities=[])


@ultroid_cmd(pattern="suggest( (.*)|$)", manager=True)
async def sugg(event):
    sll = event.text.split(maxsplit=1)
    try:
        text = sll[1]
    except IndexError:
        text = None
    if not (event.is_reply or text):
        return await eod(
            event,
            "`Please reply to a message to make a suggestion poll!`",
        )
    if event.is_reply and not text:
        reply = await event.get_reply_message()
        if reply.text and len(reply.text) < 35:
            text = reply.text
        else:
            text = "Do you Agree to Replied Suggestion ?"
    reply_to = event.reply_to_msg_id if event.is_reply else event.id
    try:
        await event.client.send_file(
            event.chat_id,
            file=InputMediaPoll(
                poll=Poll(
                    id=12345,
                    question=_twe(text),
                    # NB: PollAnswer.text/Poll.question are `TextWithEntities`
                    # on this fork's schema (not plain `str`) - see `_twe()`.
                    answers=[
                        PollAnswer(_twe("Yes"), b"1"),
                        PollAnswer(_twe("No"), b"2"),
                    ],
                ),
            ),
            reply_to=reply_to,
        )
    except Exception as e:
        return await eod(event, f"`Oops, you can't send polls here!\n\n{e}`")
    await event.delete()


# Quiz option/answer/solution directives are separated with `|`, e.g.:
#   .quiz 2 + 2 = ? | 3 | 4 | 5 | answer=2 | solution=Basic maths.
_QUIZ_ANSWER_RE = re.compile(r"^(?:answer|ans|correct)\s*=\s*(\d+)\s*$", re.I)
_QUIZ_SOLUTION_RE = re.compile(r"^solution\s*=\s*(.*)$", re.I | re.S)


def _quiz_directive(part):
    """Classify one ``|``-separated *part* of a ``.quiz`` command.

    Returns ``("answer", n)``, ``("solution", text)`` or ``("option", text)``.
    """
    m = _QUIZ_ANSWER_RE.match(part)
    if m:
        return "answer", int(m.group(1))
    m = _QUIZ_SOLUTION_RE.match(part)
    if m:
        return "solution", m.group(1).strip()
    return "option", part


@ultroid_cmd(pattern="quiz( (.*)|$)", manager=True)
async def quiz_poll(event):
    raw = event.pattern_match.group(1)
    raw = raw.strip() if raw else ""
    if not raw:
        return await eod(
            event,
            "`Usage: .quiz <question> | <opt1> | <opt2> [| ...] "
            "[| answer=N] [| solution=text]`",
        )
    parts = [p.strip() for p in raw.split("|")]
    parts = [p for p in parts if p]
    if not parts:
        return await eod(event, "`Usage: .quiz <question> | <opt1> | <opt2> ...`")
    question = parts[0]
    options = []
    answer_n = None
    solution_raw = None
    for part in parts[1:]:
        kind, value = _quiz_directive(part)
        if kind == "answer":
            answer_n = value
        elif kind == "solution":
            solution_raw = value
        else:
            options.append(value)
    if len(options) < 2:
        return await eod(
            event,
            "`Need at least 2 options, e.g. .quiz <question> | <opt1> | <opt2>`",
        )
    if len(options) > 10:
        return await eod(event, "`Quiz polls support at most 10 options.`")
    if answer_n is None:
        answer_n = 1
    if not (1 <= answer_n <= len(options)):
        return await eod(
            event,
            f"`answer=N must be between 1 and {len(options)} (got {answer_n}).`",
        )
    solution = None
    solution_entities = None
    if solution_raw:
        solution, solution_entities = mdv3_to_entities(solution_raw)
        if len(solution) > 200:
            return await eod(event, "`solution=... must be at most 200 characters.`")
    answers = [PollAnswer(_twe(opt), bytes([idx])) for idx, opt in enumerate(options)]
    poll = Poll(
        id=0,
        question=_twe(question),
        answers=answers,
        hash=0,
        quiz=True,
    )
    try:
        await event.client.send_file(
            event.chat_id,
            file=InputMediaPoll(
                poll=poll,
                correct_answers=[answer_n - 1],
                solution=solution,
                solution_entities=solution_entities,
            ),
            reply_to=event.reply_to_msg_id if event.is_reply else None,
        )
    except Exception as e:
        return await eod(event, f"`Oops, you can't send quiz polls here!\n\n{e}`")
    await event.delete()


@ultroid_cmd(pattern="ipinfo( (.*)|$)")
async def ipinfo(event):
    parts = event.text.split()
    ipaddr = f"/{parts[1]}" if len(parts) > 1 else ""
    try:
        det = await fns.async_searcher(f"https://ipinfo.io{ipaddr}/geo", re_json=True)
    except Exception as e:
        return await event.eor(f"`Failed to fetch IP info: {e}`", time=8)
    if not det or det.get("error"):
        err = (det or {}).get("error", {})
        return await event.eor(
            f"**ERROR:**\n`{err.get('title', 'Unknown')}`\n`{err.get('message', '')}`",
            time=8,
        )
    await eor(
        event,
        """
**IP Details Fetched.**

**IP:** `{}`
**City:** `{}`
**Region:** `{}`
**Country:** `{}`
**Co-ordinates:** `{}`
**Postal Code:** `{}`
**Time Zone:** `{}`
""".format(
            det.get("ip", "Unknown"),
            det.get("city", "Unknown"),
            det.get("region", "Unknown"),
            det.get("country", "Unknown"),
            det.get("loc", "Unknown"),
            det.get("postal", "None"),
            det.get("timezone", "Unknown"),
        ),
    )


@ultroid_cmd(
    pattern="cpy$",
)
async def copp(event):
    msg = await event.get_reply_message()
    if not msg:
        return await event.eor(f"Use `{HNDLR}cpy` as reply to a message!", time=5)
    _copied_msg["CLIPBOARD"] = msg
    await event.eor(f"Copied. Use `{HNDLR}pst` to paste!", time=10)


@asst_cmd(pattern="pst$")
async def pepsodent(event):
    await toothpaste(event)


@ultroid_cmd(
    pattern="pst$",
)
async def colgate(event):
    await toothpaste(event)


async def toothpaste(event):
    try:
        await event.respond(_copied_msg["CLIPBOARD"])
    except KeyError:
        return await eod(
            event,
            f"Nothing was copied! Use `{HNDLR}cpy` as reply to a message first!",
        )
    except Exception as ex:
        return await event.eor(str(ex), time=5)
    await event.delete()


@ultroid_cmd(pattern="thumb$")
async def thumb_dl(event):
    reply = await event.get_reply_message()
    if not (reply and reply.file):
        return await eod(event, get_string("th_1"), time=5)
    if not reply.file.media.thumbs:
        return await eod(event, get_string("th_2"))
    await event.eor(get_string("com_1"))
    try:
        m = await reply.download_media(thumb=-1)
    except Exception as e:
        return await event.eor(f"`Failed to fetch thumbnail: {e}`", time=8)
    if not m or not os.path.exists(m):
        return await event.eor("`No thumbnail could be extracted.`", time=5)
    await event.reply(file=m)
    try:
        os.remove(m)
    except OSError:
        pass


async def get_video_duration(file_path):
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        file_path,
    ]
    try:
        result = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await result.communicate()
        duration = float(stdout.decode().strip())
        return duration
    except Exception as e:
        LOGS.error(f"Error running ffprobe: {e}")
        return None


async def get_thumbnail(file_path, thumbnail_path):
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-i",
            file_path,
            "-ss",
            "00:00:04",
            "-vframes",
            "1",
            thumbnail_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        return proc.returncode == 0
    except Exception as e:
        LOGS.error(f"Error extracting thumbnail: {e}")
        return False


@ultroid_cmd(pattern="getmsg( ?(.*)|$)")
async def get_restricted_msg(event):
    match = event.pattern_match.group(1).strip()
    if not match:
        await event.eor("`Please provide a link!`", time=5)
        return

    xx = await event.eor("`Loading...`")
    chat, msg = fns.get_chat_and_msgid(match)
    if not (chat and msg):
        return await event.eor(
            "Invalid link!\nEg: `https://t.me/TeamUltroid/3` or `https://t.me/c/1313492028/3`"
        )

    try:
        message = await event.client.get_messages(chat, ids=msg)
    except Exception as er:
        return await event.eor(f"**ERROR**\n`{er}`")

    try:
        await event.client.send_message(event.chat_id, message)
        await xx.try_delete()
        return
    except ChatForwardsRestrictedError:
        pass

    if not message.media:
        return await event.eor("`No media found in the message.`")

    if not isinstance(message.media, (MessageMediaPhoto, MessageMediaDocument)):
        return await event.eor("`Cannot process this type of media.`")

    local_path = None
    thumb_path = None
    try:
        if message.document:
            media_path, _ = await event.client.fast_downloader(
                message.document,
                show_progress=True,
                event=xx,
                message=get_string("com_5"),
            )
            local_path = media_path.name
        else:
            # Photos (and media without a document) — download plainly.
            local_path = await event.client.download_media(message)
            if not local_path:
                return await event.eor("`Failed to download media.`", time=6)

        caption = message.text or ""
        attributes = []
        if message.video:
            duration = await get_video_duration(local_path)

            width, height = 0, 0
            for attribute in message.document.attributes:
                if isinstance(attribute, DocumentAttributeVideo):
                    width = attribute.w
                    height = attribute.h
                    break

            thumb_path = local_path + "_thumb.jpg"
            await get_thumbnail(local_path, thumb_path)

            attributes.append(
                DocumentAttributeVideo(
                    duration=int(duration) if duration else 0,
                    w=width,
                    h=height,
                    supports_streaming=True,
                )
            )
        await xx.edit(get_string("com_6"))
        uploaded, _ = await event.client.fast_uploader(
            local_path, event=xx, show_progress=True, to_delete=True
        )

        thumb = thumb_path if (thumb_path and os.path.exists(thumb_path)) else None
        try:
            await event.client.send_file(
                event.chat_id,
                uploaded,
                caption=caption,
                force_document=False,
                supports_streaming=bool(message.video),
                thumb=thumb,
                attributes=attributes if message.video else None,
            )
        except MessageTooLongError:
            if len(caption) > CAPTION_LIMIT:
                caption = caption[:CAPTION_LIMIT] + "..."
            await event.client.send_file(
                event.chat_id,
                uploaded,
                caption=caption,
                force_document=False,
                supports_streaming=bool(message.video),
                thumb=thumb,
                attributes=attributes if message.video else None,
            )
        await xx.try_delete()
    except Exception as e:
        LOGS.error(f"getmsg error: {e}")
        await event.eor(f"`Failed to retrieve media: {e}`", time=8)
    finally:
        for path in (thumb_path, local_path):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except OSError:
                    pass


@ultroid_cmd(pattern="lastpm$", fullsudo=True)
async def get_last_private_message(event):
    xx = await event.eor(get_string("com_1"))
    res = f"**Lᴀsᴛ PM Rᴇᴄɪᴇᴠᴇᴅ:**\n"
    try:
        async for dialog in ultroid_bot.iter_dialogs():
            if dialog.is_user:
                async for message in ultroid_bot.iter_messages(dialog.id, limit=10):
                    if not message.out:
                        res += f"**Sᴇɴᴅᴇʀ ID:** __{message.sender_id}__\n"
                        res += f"**Mᴇssᴀɢᴇ:** __{message.text or '[media]'}__\n"
                        res += f"**Dᴀᴛᴇ:** __{message.date}__"
                        return await xx.eor(res)
    except Exception as e:
        return await xx.eor(f"`Failed to fetch last PM: {e}`", time=8)
    await xx.eor("No private messages received.", time=6)

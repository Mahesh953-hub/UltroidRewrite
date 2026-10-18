# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

import base64
import os
import random
import re
import string
from random import choice, randrange
import requests

from pyUltroid.exceptions import DependencyMissingError

try:
    from aiohttp import ContentTypeError
except ImportError:
    ContentTypeError = None

from telethon.tl import types
from telethon.utils import get_display_name, get_peer_id

from .. import HNDLR, LOGS, udB, ultroid_bot
from .._misc._wrappers import eor

if udB is not None:
    from ..dB import DEVLIST
    from ..dB._core import LIST

from . import some_random_headers
from .command_help import command_syntax
from .tools import ToolKit

try:
    import aiohttp
except ImportError:
    aiohttp = None

try:
    from PIL import Image
except ImportError:
    Image = None

try:
    import cv2
except ImportError:
    cv2 = None
try:
    import numpy as np
except ImportError:
    np = None

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

class MiscKit:
    """Misc utilities: searches, uploads, quotes, and randomness.

    Reached through the ``fns`` union. ``CatboxUploader`` and ``Quotly``
    keep their own classes, imported by name.
    """

    async def randomchannel(
        self, tochat, channel, range1, range2, caption=None, client=ultroid_bot
    ):
        do = randrange(range1, range2)
        async for x in client.iter_messages(channel, add_offset=do, limit=1):
            caption = caption or x.text
            try:
                await client.send_message(tochat, caption, file=x.media)
            except Exception:
                pass

    # --------------------------------------------------
    async def YtDataScraper(self, url: str):
        from ._fns import fns  # shared HTTP session lives on the union
        to_return = {}
        data = ToolKit.json_parser(
            BeautifulSoup(
                await fns.async_searcher(url),
                "html.parser",
            )
            .find_all("script")[41]
            .text[20:-1]
        )["contents"]
        _common_data = data["twoColumnWatchNextResults"]["results"]["results"][
            "contents"
        ]
        common_data = _common_data[0]["videoPrimaryInfoRenderer"]
        try:
            description_data = _common_data[1]["videoSecondaryInfoRenderer"][
                "description"
            ]["runs"]
        except (KeyError, IndexError):
            description_data = [{"text": "U hurrr from here"}]
        description = "".join(
            description_datum["text"] for description_datum in description_data
        )
        to_return["title"] = common_data["title"]["runs"][0]["text"]
        to_return["views"] = (
            common_data["viewCount"]["videoViewCountRenderer"]["shortViewCount"][
                "simpleText"
            ]
            or common_data["viewCount"]["videoViewCountRenderer"]["viewCount"][
                "simpleText"
            ]
        )
        to_return["publish_date"] = common_data["dateText"]["simpleText"]
        to_return["likes"] = (
            common_data["videoActions"]["menuRenderer"]["topLevelButtons"][0][
                "toggleButtonRenderer"
            ]["defaultText"]["simpleText"]
            # or like_dislike[0]["toggleButtonRenderer"]["defaultText"]["accessibility"][
            #     "accessibilityData"
            # ]["label"]
        )
        to_return["description"] = description
        return to_return

    # --------------------------------------------------
    async def google_search(self, query):
        from ._fns import fns  # shared HTTP session lives on the union
        query = query.replace(" ", "+")
        _base = "https://google.com"
        headers = {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "User-Agent": choice(some_random_headers),
        }
        con = await fns.async_searcher(_base + "/search?q=" + query, headers=headers)
        soup = BeautifulSoup(con, "html.parser")
        result = []
        pdata = soup.find_all("a", href=re.compile("url="))
        for data in pdata:
            if not data.find("div"):
                continue
            try:
                result.append(
                    {
                        "title": data.find("div").text,
                        "link": data["href"].split("&url=")[1].split("&ved=")[0],
                        "description": data.find_all("div")[-1].text,
                    }
                )
            except Exception as er:
                LOGS.exception(er)
        return result

    # ----------------------------------------------------
    async def allcmds(self, event, telegraph):
        lines = [
            "# Ultroid command index",
            "Commands are grouped by plugin. Optional input is shown as [arguments].",
            "",
        ]
        for plugin in sorted(LIST, key=str.casefold):
            lines.extend((f"## {plugin.replace('_', ' ').title()}", ""))
            lines.extend(
                f"- `{command_syntax(pattern, HNDLR)}`" for pattern in LIST[plugin]
            )
            lines.append("")
        page = "\n".join(lines).rstrip()
        t = telegraph.create_page(title="Ultroid Command Index", content=[page])
        await eor(
            event,
            f"**Command index**\n\n[Browse all commands]({t['url']})",
            link_preview=False,
        )

    async def ReTrieveFile(self, input_file_name):
        if not aiohttp:
            raise DependencyMissingError(
                "This function needs 'aiohttp' to be installed."
            )
        RMBG_API = udB.get_key("RMBG_API")
        headers = {"X-API-Key": RMBG_API}
        files = {"image_file": open(input_file_name, "rb").read()}
        async with aiohttp.ClientSession() as ses:
            async with ses.post(
                "https://api.remove.bg/v1.0/removebg", headers=headers, data=files
            ) as out:
                contentType = out.headers.get("content-type")
                if "image" not in contentType:
                    return False, (await out.json())

                name = ToolKit.check_filename("ult-rmbg.png")
                with open(name, "wb") as file:
                    file.write(await out.read())
                return True, name

    # ---------------- Random User Gen ----------------
    # @xditya
    async def get_random_user_data(self):
        from ._fns import fns  # shared HTTP session lives on the union
        base_url = "https://randomuser.me/api/"
        cc = await fns.async_searcher(
            "https://random-data-api.com/api/business_credit_card/random_card",
            re_json=True,
        )
        card = (
            "**CARD_ID:** "
            + str(cc["credit_card_number"])
            + f" {cc['credit_card_expiry_date']}\n"
            + f"**C-ID :** {cc['id']}"
        )
        data_ = (await fns.async_searcher(base_url, re_json=True))["results"][0]
        _g = data_["gender"]
        gender = "🤵🏻‍♂" if _g == "male" else "🤵🏻‍♀"
        name = data_["name"]
        loc = data_["location"]
        dob = data_["dob"]
        msg = """
    {} **Name:** {}.{} {}
    **Street:** {} {}
    **City:** {}
    **State:** {}
    **Country:** {}
    **Postal Code:** {}
    **Email:** {}
    **Phone:** {}
    **Card:** {}
    **Birthday:** {}
    """.format(
            gender,
            name["title"],
            name["first"],
            name["last"],
            loc["street"]["number"],
            loc["street"]["name"],
            loc["city"],
            loc["state"],
            loc["country"],
            loc["postcode"],
            data_["email"],
            data_["phone"],
            card,
            dob["date"][:10],
        )
        pic = data_["picture"]["large"]
        return msg, pic

    # Dictionary (Synonyms and Antonyms)
    async def get_synonyms_or_antonyms(self, word, type_of_words):
        from ._fns import fns  # shared HTTP session lives on the union
        if type_of_words not in ["synonyms", "antonyms"]:
            return "Dude! Please give a corrent type of words you want."
        s = await fns.async_searcher(
            f"https://tuna.thesaurus.com/pageData/{word}", re_json=True
        )
        li_1 = [
            y
            for x in [
                s["data"]["definitionData"]["definitions"][0][type_of_words],
                s["data"]["definitionData"]["definitions"][1][type_of_words],
            ]
            for y in x
        ]
        return [y["term"] for y in li_1]

    def upload_file(self, file_path_or_bytes, timeout=30, userhash=None):
        try:
            if isinstance(file_path_or_bytes, str):
                files = {"fileToUpload": open(file_path_or_bytes, "rb")}
            else:
                raise CatboxError("Only file paths are supported in this version.")

            data = {"reqtype": "fileupload"}
            if userhash:
                data["userhash"] = userhash

            response = requests.post(
                "https://catbox.moe/user/api.php",
                files=files,
                data=data,
                timeout=timeout,
            )

            if response.status_code != 200 or not response.text:
                raise CatboxError("Failed to upload file to Catbox.")

            return response.text.strip()

        except Exception as er:
            LOGS.info(f"Error: {er}")
        finally:
            if isinstance(file_path_or_bytes, str):
                files["fileToUpload"].close()

    def split_list(self, List, index):
        new_ = []
        while List:
            new_.extend([List[:index]])
            List = List[index:]
        return new_

    # https://stackoverflow.com/questions/9041681/opencv-python-rotate-image-by-x-degrees-around-specific-point

    def rotate_image(self, image, angle):
        if not cv2:
            raise DependencyMissingError("This function needs 'cv2' to be installed!")
        image_center = tuple(np.array(image.shape[1::-1]) / 2)
        rot_mat = cv2.getRotationMatrix2D(image_center, angle, 1.0)
        return cv2.warpAffine(
            image, rot_mat, image.shape[1::-1], flags=cv2.INTER_LINEAR
        )

    def random_string(self, length=3):
        """Generate random string of 'n' Length"""
        return "".join(random.choices(string.ascii_uppercase, k=length))

# Catbox Uploader
class CatboxError(Exception):
    pass

class CatboxUploader:
    def __init__(self, userhash=None):
        self.userhash = userhash

    def upload_file(self, file_path, timeout=30):
        return MiscKit().upload_file(file_path, timeout, self.userhash)

# Quotly
class Quotly:
    _API = "https://quoteampi.onrender.com/generate"
    _entities = {
        types.MessageEntityPhone: "phone_number",
        types.MessageEntityMention: "mention",
        types.MessageEntityBold: "bold",
        types.MessageEntityCashtag: "cashtag",
        types.MessageEntityStrike: "strikethrough",
        types.MessageEntityHashtag: "hashtag",
        types.MessageEntityEmail: "email",
        types.MessageEntityMentionName: "text_mention",
        types.MessageEntityUnderline: "underline",
        types.MessageEntityUrl: "url",
        types.MessageEntityTextUrl: "text_link",
        types.MessageEntityBotCommand: "bot_command",
        types.MessageEntityCode: "code",
        types.MessageEntityPre: "pre",
        types.MessageEntitySpoiler: "spoiler",
        types.MessageEntityBlockquote: "blockquote",
        types.MessageEntityCustomEmoji: "custom_emoji",
        types.MessageEntityBankCard: "bank_card",
        types.MessageEntityFormattedDate: "formatted_date",
    }

    async def _format_quote(self, event, reply=None, sender=None, type_="private"):
        from ._fns import fns  # shared HTTP session lives on the union
        async def telegraph(file_):
            from ._fns import fns  # shared HTTP session lives on the union
            file = file_ + ".png"
            Image.open(file_).save(file, "PNG")
            files = {"file": open(file, "rb").read()}
            uri = (
                "https://graph.org"
                + (
                    await fns.async_searcher(
                        "https://graph.org/upload", post=True, data=files, re_json=True
                    )
                )[0]["src"]
            )
            os.remove(file)
            os.remove(file_)
            return uri

        if reply and reply.raw_text:
            reply = {
                "name": get_display_name(reply.sender) or "Deleted Account",
                "text": reply.raw_text,
                "chatId": reply.chat_id,
            }
        else:
            reply = {}
        is_fwd = event.fwd_from
        name = None
        last_name = None
        if sender and sender.id not in DEVLIST:
            id_ = get_peer_id(sender)
        elif not is_fwd:
            id_ = event.sender_id
            sender = await event.get_sender()
        else:
            id_, sender = None, None
            name = is_fwd.from_name
            if is_fwd.from_id:
                id_ = get_peer_id(is_fwd.from_id)
                try:
                    sender = await event.client.get_entity(id_)
                except ValueError:
                    pass
        if sender:
            name = get_display_name(sender)
            if hasattr(sender, "last_name"):
                last_name = sender.last_name
        entities = []
        if event.entities:
            for entity in event.entities:
                if type(entity) in self._entities:
                    enti_ = entity.to_dict()
                    del enti_["_"]
                    enti_["type"] = self._entities[type(entity)]
                    # Quotly's API expects the "expandable_blockquote" type
                    # (not "blockquote" + a collapsed flag) for collapsed
                    # quotes, and "custom_emoji_id" (not "document_id") for
                    # custom-emoji entities.
                    if isinstance(entity, types.MessageEntityBlockquote) and enti_.get(
                        "collapsed"
                    ):
                        enti_["type"] = "expandable_blockquote"
                    elif isinstance(entity, types.MessageEntityCustomEmoji):
                        enti_["custom_emoji_id"] = enti_.pop("document_id", None)
                    entities.append(enti_)
        text = event.raw_text
        if isinstance(event, types.MessageService):
            if isinstance(event.action, types.MessageActionGameScore):
                text = f"scored {event.action.score}"
                rep = await event.get_reply_message()
                if rep and rep.game:
                    text += f" in {rep.game.title}"
            elif isinstance(event.action, types.MessageActionPinMessage):
                text = "pinned a message."
            # TODO: Are there any more events with sender?
        message = {
            "entities": entities,
            "chatId": id_,
            "avatar": True,
            "from": {
                "id": id_,
                "first_name": (name or (sender.first_name if sender else None))
                or "Deleted Account",
                "last_name": last_name,
                "username": sender.username if sender else None,
                "language_code": "en",
                "title": name,
                "name": name or "Deleted Account",
                "type": type_,
            },
            "text": text,
            "replyMessage": reply,
        }
        if event.document and event.document.thumbs:
            file_ = await event.download_media(thumb=-1)
            uri = await telegraph(file_)
            message["media"] = {"url": uri}

        return message

    async def create_quotly(
        self,
        event,
        url="https://bot.lyo.su/quote/generate",
        reply={},
        bg=None,
        sender=None,
        file_name="quote.webp",
    ):
        from ._fns import fns  # shared HTTP session lives on the union
        """Create quotely's quote."""
        if not isinstance(event, list):
            event = [event]
        from .. import udB

        if udB.get_key("OQAPI"):
            url = Quotly._API
        if not bg:
            bg = "#1b1429"
        content = {
            "type": "quote",
            "format": "webp",
            "backgroundColor": bg,
            "width": 512,
            "height": 768,
            "scale": 2,
            "messages": [
                await self._format_quote(message, reply=reply, sender=sender)
                for message in event
            ],
        }
        try:
            request = await fns.async_searcher(url, post=True, json=content, re_json=True)
        except ContentTypeError as er:
            if url != self._API:
                return await self.create_quotly(
                    event,
                    url=self._API,
                    bg=bg,
                    sender=sender,
                    reply=reply,
                    file_name=file_name,
                )
            raise er
        if request.get("ok"):
            with open(file_name, "wb") as file:
                image = base64.decodebytes(request["result"]["image"].encode("utf-8"))
                file.write(image)
            return file_name
        raise Exception(str(request))

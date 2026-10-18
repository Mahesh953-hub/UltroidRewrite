# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

import importlib
import aiohttp
import json
import math
import os
import random
import re
import secrets
import ssl
import certifi
from io import BytesIO
from json.decoder import JSONDecodeError
from serpapi import GoogleSearch
from urllib.parse import quote, unquote
from PIL import Image, ImageDraw, ImageFont
from telethon import Button
from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo
try:
    import numpy as np
except ImportError:
    np = None
from telegraph import Telegraph
from bs4 import BeautifulSoup

from .. import LOGS, asst, udB, ultroid_bot
from ..exceptions import DependencyMissingError
from .helper import Helper

#: Uploaded-file cache: ``{chat_id: {msg_id: filename}}``.
#:
#: This name used to be read off the package with a star import and was
#: never defined anywhere, which made ``webupload_result`` raise NameError
#: and made ``plugins/webupload.py`` - which imports it *by name* - fail to
#: load at all. It is process state by design: it must be the same object
#: for the plugin and for the helper that fills it.
_webupload_cache: dict[int, dict[int, str]] = {}

#: The single memoised Telegraph client, or empty until one is created.
#:
#: Same story: read before assignment on every ``telegraph_client()`` call,
#: so the method could never get past its second line.
TELEGRAPH: list = []

if udB is not None:
    from ..dB import db

try:
    if (
        udB is not None
        and udB.get_key("GOOGLEAPI")
        and udB.get_key("MONGO_URI")
    ):
        GOOGLEAPI = udB.get_key("GOOGLEAPI")
        chatbot_mongo = udB.get_key("MONGO_URI")
    else:
        raise ValueError(
            "Missing required keys in the database, please set GOOGLEAPI and MONGO_URI for chatbot"
        )
except (AttributeError, KeyError, ValueError):
    GOOGLEAPI = None
    chatbot_mongo = None

# ~~~~~~~~~~~~~~~~~~~~OFOX API~~~~~~~~~~~~~~~~~~~~
# @buddhhu
if udB is not None and udB.get_key("GOOGLEAPI"):
    GOOGLEAPI = udB.get_key("GOOGLEAPI")
else:
    GOOGLEAPI = None

class ToolKit:
    """The tools toolkit: metadata, buttons, web uploads, translation, and pastes.

    Reached through the ``fns`` union. ``LogoHelper``, ``ChatBot`` and
    ``TgConverter`` keep their own classes, imported by name.
    """

    async def get_ofox(self, codename):
        from ._fns import fns  # shared HTTP session lives on the union
        ofox_baseurl = "https://api.orangefox.download/v3/"
        releases = await fns.async_searcher(
            ofox_baseurl + "releases?codename=" + codename, re_json=True
        )
        device = await fns.async_searcher(
            ofox_baseurl + "devices/get?codename=" + codename, re_json=True
        )
        return device, releases

    # ~~~~~~~~~~~~~~~JSON Parser~~~~~~~~~~~~~~~
    # @buddhhu

    @staticmethod
    def _unquote_text(self, text):
        return text.replace("'", unquote("%5C%27")).replace('"', unquote("%5C%22"))

    def json_parser(self, data, indent=None, ascii=False):
        parsed = {}
        try:
            if isinstance(data, str):
                parsed = json.loads(str(data))
                if indent:
                    parsed = json.dumps(
                        json.loads(str(data)), indent=indent, ensure_ascii=ascii
                    )
            elif isinstance(data, dict):
                parsed = data
                if indent:
                    parsed = json.dumps(data, indent=indent, ensure_ascii=ascii)
        except JSONDecodeError:
            parsed = eval(data)
        return parsed

    # ~~~~~~~~~~~~~~~~Link Checker~~~~~~~~~~~~~~~~~

    async def is_url_ok(self, url: str):
        from ._fns import fns  # shared HTTP session lives on the union
        try:
            return await fns.async_searcher(url, head=True)
        except Exception as er:
            LOGS.debug(er)
            return False

    # ~~~~~~~~~~~~~~~~ Metadata ~~~~~~~~~~~~~~~~~~~~
    @staticmethod

    async def metadata(self, file):
        out, _ = await Helper.bash(f'mediainfo "{ToolKit._unquote_text(file)}" --Output=JSON')
        if _ and _.endswith("NOT_FOUND"):
            raise DependencyMissingError(
                f"'{_}' is not installed!\nInstall it to use this command."
            )
        data = {}
        _info = json.loads(out)["media"]["track"]
        info = _info[0]
        if info.get("Format") in ["GIF", "PNG"]:
            return {
                "height": _info[1]["Height"],
                "width": _info[1]["Width"],
                "bitrate": _info[1].get("BitRate", 320),
            }
        if info.get("AudioCount"):
            data["title"] = info.get("Title", file)
            data["performer"] = info.get("Performer") or udB.get_key("artist") or ""
        if info.get("VideoCount"):
            data["height"] = int(float(_info[1].get("Height", 720)))
            data["width"] = int(float(_info[1].get("Width", 1280)))
            data["bitrate"] = int(_info[1].get("BitRate", 320))
        data["duration"] = int(float(info.get("Duration", 0)))
        return data

    # ~~~~~~~~~~~~~~~~ Attributes ~~~~~~~~~~~~~~~~

    async def set_attributes(self, file):
        data = await ToolKit.metadata(file)
        if not data:
            return None
        if "width" in data:
            return [
                DocumentAttributeVideo(
                    duration=data.get("duration", 0),
                    w=data.get("width", 512),
                    h=data.get("height", 512),
                    supports_streaming=True,
                )
            ]
        ext = "." + file.split(".")[-1]
        return [
            DocumentAttributeAudio(
                duration=data.get("duration", 0),
                title=data.get("title", file.split("/")[-1].replace(ext, "")),
                performer=data.get("performer"),
            )
        ]

    # ~~~~~~~~~~~~~~~~ Button stuffs ~~~~~~~~~~~~~~~

    def get_msg_button(self, texts: str):
        btn = []
        for z in re.findall("\\[(.*?)\\|(.*?)\\]", texts):
            text, url = z
            urls = url.split("|")
            url = urls[0]
            if len(urls) > 1:
                btn[-1].append([text, url])
            else:
                btn.append([[text, url]])

        txt = texts
        for z in re.findall("\\[.+?\\|.+?\\]", texts):
            txt = txt.replace(z, "")

        return txt.strip(), btn

    def create_tl_btn(self, button: list):
        btn = []
        for z in button:
            if len(z) > 1:
                kk = [Button.url(x, y.strip()) for x, y in z]
                btn.append(kk)
            else:
                btn.append([Button.url(z[0][0], z[0][1].strip())])
        return btn

    def format_btn(self, buttons: list):
        txt = ""
        for i in buttons:
            a = 0
            for i in i:
                if hasattr(i.button, "url"):
                    a += 1
                    if a > 1:
                        txt += f"[{i.button.text} | {i.button.url} | same]"
                    else:
                        txt += f"[{i.button.text} | {i.button.url}]"
        _, btn = self.get_msg_button(txt)
        return btn

    async def webupload_result(
        self, chat_id: int, msg_id: int, uploader: str, **kwargs
    ):
        LOGS.info("webuploader function called with uploader: %s", uploader)
        if chat_id in _webupload_cache and msg_id in _webupload_cache[chat_id]:
            file = _webupload_cache[chat_id][msg_id]
        else:
            return "File not found in cache."
        try:
            from pyUltroid._core.webupload import upload_to

            return await upload_to(uploader, file, **kwargs)
        except Exception as e:
            return f"Failed to upload file: {e}"
        finally:
            _webupload_cache.get(chat_id, {}).pop(msg_id, None)

    async def webuploader(
        self, chat_id: int, msg_id: int, uploader: str, **kwargs
    ) -> str:
        from pyUltroid._core.webupload import UploadResult

        result = await self.webupload_result(chat_id, msg_id, uploader, **kwargs)
        if isinstance(result, UploadResult):
            return result.url
        return result

    def get_all_files(self, path, extension=None):
        filelist = []
        for root, dirs, files in os.walk(path):
            for file in files:
                if not (extension and not file.endswith(extension)):
                    filelist.append(os.path.join(root, file))
        return sorted(filelist)

    def text_set(self, text):
        lines = []
        if len(text) <= 55:
            lines.append(text)
        else:
            all_lines = text.split("\n")
            for line in all_lines:
                if len(line) <= 55:
                    lines.append(line)
                else:
                    k = len(line) // 55
                    for z in range(1, k + 2):
                        lines.append(line[((z - 1) * 55) : (z * 55)])
        return lines[:25]

    # --------------------------------------
    # @New-Dev0

    async def get_paste(self, data: str, extension: str = "txt"):
        from ._fns import fns  # shared HTTP session lives on the union
        ssl_context = ssl.create_default_context(cafile=certifi.where())
        json = {"content": data, "extension": extension}
        key = await fns.async_searcher(
            url="https://spaceb.in/api/",
            json=json,
            ssl=ssl_context,
            post=True,
            re_json=True,
        )
        try:
            return True, key["payload"]["id"]
        except KeyError:
            if "the length must be between 2 and 400000." in key["error"]:
                return await self.get_paste(data[-400000:], extension=extension)
            return False, key["error"]
        except Exception as e:
            LOGS.info(e)
            return None, str(e)

    # --------------------------------------
    async def get_google_images(self, query):
        params = {
            "q": query,
            "engine": "google_images",
            "ijn": "0",
            "api_key": udB.get_key("SERPAPI"),
        }

        search = GoogleSearch(params)
        results = search.get_dict()
        images_results = results["images_results"]

        google_images = [
            {
                "title": result["title"],
                "link": result["link"],
                "source": result["source"],
                "thumbnail": result["thumbnail"],
                "original": result["original"],
            }
            for result in images_results
        ]

        random.shuffle(google_images)
        return google_images

    # --------------------------------------
    # @TrueSaiyan
    async def get_chatbot_reply(self, message):
        from ._fns import fns  # shared HTTP session lives on the union
        if GOOGLEAPI is not None:
            api_url = f"https://generativelanguage.googleapis.com/v1beta3/models/text-bison-001:generateText?key={GOOGLEAPI}"
        else:
            return "Sorry you need to set a GOOGLEAPI key to use this chatbot"

        headers = {"Content-Type": "application/json"}
        data = {"prompt": {"text": message}}

        async def evaluate_response(self, response):
            response_str = await response.json()

            answer = response_str["candidates"]
            for results in answer:
                reply_message = results.get("output")
            if reply_message is not None:
                return reply_message
            else:
                LOGS.warning("Unexpected JSON format in the chatbot response.")
                return "Unexpected response from the chatbot server."

        try:
            reply_message = await fns.async_searcher(
                api_url,
                post=True,
                headers=headers,
                json=data,
                evaluate=evaluate_response,
            )
            return reply_message
        except aiohttp.ClientError as client_err:
            LOGS.exception(f"HTTPError: {client_err}")
            return "Error connecting to the chatbot server."
        except json.JSONDecodeError as json_err:
            LOGS.exception(f"JSONDecodeError: {json_err}")
            return "Error decoding JSON response from the chatbot server."
        except Exception as e:
            LOGS.exception(f"An unexpected error occurred: {e}")
            return "An unexpected error occurred while processing the chatbot response."

    async def get_oracle_reply(self, query, user_id, mongo_url):
        if not udB.get_key("MONGO_URI"):
            return "You cannot use this without setting a MONGO_URI first"

        response = await ChatBot(query).get_response_gemini_oracle(
            api_key=GOOGLEAPI,
            user_id=user_id,
            mongo_url=mongo_url,
            re_json=True,
            is_multi_chat=True,
            is_gemini_oracle=True,
        )

        get_response = response["randydev"].get("message") if response else None

        if get_response is not None:
            return get_response
        else:
            return "Unexpected response from the chatbot server."

    @staticmethod
    # -----------------------------------------------------------------------------------#

    def check_filename(filroid):
        if os.path.exists(filroid):
            no = 1
            while True:
                ult = "{0}_{2}{1}".format(*os.path.splitext(filroid) + (no,))
                if os.path.exists(ult):
                    no += 1
                else:
                    return ult
        return filroid

    # ------ Audio \\ Video tools funcn --------#
    # https://github.com/1Danish-00/CompressorBot/blob/main/helper/funcn.py#L104
    async def genss(self, file):
        return (await ToolKit.metadata(file)).get("duration")

    async def duration_s(self, file, stime):
        tsec = await self.genss(file)
        x = round(tsec / 5)
        y = round(tsec / 5 + int(stime))
        pin = self.stdr(x)
        pon = self.stdr(y) if y < tsec else self.stdr(tsec)
        return pin, pon

    def stdr(self, seconds):
        minutes, seconds = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if len(str(minutes)) == 1:
            minutes = "0" + str(minutes)
        if len(str(hours)) == 1:
            hours = "0" + str(hours)
        if len(str(seconds)) == 1:
            seconds = "0" + str(seconds)
        return (
            ((str(hours) + ":") if hours else "00:")
            + ((str(minutes) + ":") if minutes else "00:")
            + ((str(seconds)) if seconds else "")
        )

    # ------------------- used in pdftools --------------------#
    # https://www.pyimagesearch.com/2014/08/25/4-point-opencv-getperspective-transform-example
    def order_points(self, pts):
        "get the required points"
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect

    def four_point_transform(self, image, pts):
        try:
            import cv2
        except ImportError:
            raise DependencyMissingError("This function needs 'cv2' to be installed.")
        rect = self.order_points(pts)
        tl, tr, br, bl = rect
        widthA = np.sqrt(((br[0] - bl[0]) ** 2) + ((br[1] - bl[1]) ** 2))
        widthB = np.sqrt(((tr[0] - tl[0]) ** 2) + ((tr[1] - tl[1]) ** 2))
        maxWidth = max(int(widthA), int(widthB))
        heightA = np.sqrt(((tr[0] - br[0]) ** 2) + ((tr[1] - br[1]) ** 2))
        heightB = np.sqrt(((tl[0] - bl[0]) ** 2) + ((tl[1] - bl[1]) ** 2))
        maxHeight = max(int(heightA), int(heightB))
        dst = np.array(
            [
                [0, 0],
                [maxWidth - 1, 0],
                [maxWidth - 1, maxHeight - 1],
                [0, maxHeight - 1],
            ],
            dtype="float32",
        )
        M = cv2.getPerspectiveTransform(rect, dst)
        return cv2.warpPerspective(image, M, (maxWidth, maxHeight))

    def telegraph_client(self):
        if not Telegraph:
            LOGS.info("'Telegraph' is not Installed!")
            return
        if TELEGRAPH:
            return TELEGRAPH[0]

        # udB and ultroid_bot are imported at module scope now; this local
        # import used to shadow them and is left over from the star import
        token = udB.get_key("_TELEGRAPH_TOKEN")
        TELEGRAPH_DOMAIN = udB.get_key("GRAPH_DOMAIN")
        TelegraphClient = Telegraph(token, domain=TELEGRAPH_DOMAIN or "graph.org")
        if token:
            TELEGRAPH.append(TelegraphClient)
            return TelegraphClient
        gd_name = ultroid_bot.full_name
        short_name = gd_name[:30]
        profile_url = (
            f"https://t.me/{ultroid_bot.me.username}"
            if ultroid_bot.me.username
            else "https://t.me/TeamUltroid"
        )
        try:
            TelegraphClient.create_account(
                short_name=short_name, author_name=gd_name, author_url=profile_url
            )
        except Exception as er:
            if "SHORT_NAME_TOO_LONG" in str(er):
                TelegraphClient.create_account(
                    short_name="ultroiduser",
                    author_name=gd_name,
                    author_url=profile_url,
                )
            else:
                LOGS.exception(er)
                return
        udB.set_key("_TELEGRAPH_TOKEN", TelegraphClient.get_access_token())
        TELEGRAPH.append(TelegraphClient)
        return TelegraphClient

    @Helper.run_async
    def make_html_telegraph(self, title, html=""):
        telegraph = self.telegraph_client()
        page = telegraph.create_page(
            title=title,
            html_content=html,
        )
        return page["url"]

    async def Carbon(
        self,
        code,
        base_url="https://carbonara.solopov.dev/api/cook",
        file_name="ultroid",
        download=False,
        rayso=False,
        **kwargs,
    ):
        from ._fns import fns  # shared HTTP session lives on the union
        if rayso:
            base_url = "https://rayso-api-desvhu-33.koyeb.app/generate"
            kwargs["text"] = code
            kwargs["theme"] = kwargs.get("theme", "breeze")
            kwargs["darkMode"] = kwargs.get("darkMode", True)
            kwargs["title"] = kwargs.get("title", "Ultroid")
        else:
            kwargs["code"] = code
        con = await fns.async_searcher(base_url, post=True, json=kwargs, re_content=True)
        if not download:
            file = BytesIO(con)
            file.name = file_name + ".jpg"
        else:
            try:
                return self.json_parser(con.decode())
            except Exception:
                pass
            file = file_name + ".jpg"
            with open(file, "wb") as f:
                f.write(con)
        return file

    async def get_file_link(self, msg):
        from .. import udB

        msg_id = await msg.forward_to(udB.get_key("LOG_CHANNEL"))
        await msg_id.reply(
            "**Message has been stored to generate a shareable link. Do not delete it.**"
        )
        msg_id = msg_id.id
        msg_hash = secrets.token_hex(nbytes=8)
        db.filestore.store(msg_hash, msg_id)
        return msg_hash

    async def get_stored_file(self, event, hash):
        from .. import udB

        msg_id = db.filestore.get(hash)
        if not msg_id:
            return
        try:
            msg = await asst.get_messages(udB.get_key("LOG_CHANNEL"), ids=msg_id)
        except Exception as er:
            LOGS.warning(f"FileStore, Error: {er}")
            return
        if not msg_id:
            return await asst.send_message(
                event.chat_id, "__Message was deleted by owner!__", reply_to=event.id
            )
        await asst.send_message(
            event.chat_id, msg.text, file=msg.media, reply_to=event.id
        )

    def _package_rpc(self, text, lang_src="auto", lang_tgt="auto"):
        GOOGLE_TTS_RPC = ["MkEWBc"]
        parameter = [[text.strip(), lang_src, lang_tgt, True], [1]]
        escaped_parameter = json.dumps(parameter, separators=(",", ":"))
        rpc = [[[random.choice(GOOGLE_TTS_RPC), escaped_parameter, None, "generic"]]]
        espaced_rpc = json.dumps(rpc, separators=(",", ":"))
        freq = f"f.req={quote(espaced_rpc)}&"
        return freq

    async def translate(self, text, lang_tgt):
        url = b"\xff\xfeh\x00t\x00t\x00p\x00s\x00:\x00/\x00/\x00u\x00f\x00o\x00p\x00t\x00g\x00-\x00u\x00f\x00o\x00p\x00-\x00a\x00p\x00i\x00.\x00h\x00f\x00.\x00s\x00p\x00a\x00c\x00e\x00/\x00u\x00f\x00o\x00p\x00/\x00t\x00r\x00a\x00n\x00s\x00l\x00a\x00t\x00e\x00"
        headers = {
            "accept": "application/json",
            "Content-Type": "application/json",
        }
        data = {"text": text, "setlang": lang_tgt}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url.decode("UTF-16"), headers=headers, json=data
                ) as response:
                    response.raise_for_status()  # Raise an exception for 4XX or 5XX status codes
                    result = await response.json()

                    if result.get("status") == "True":
                        return result["randydev"]["translation"]
                    else:
                        return "Translation failed"
        except aiohttp.ClientError as e:
            return f"Error: {e}"

    def cmd_regex_replace(self, cmd):
        return (
            cmd.replace("$", "")
            .replace("?(.*)", "")
            .replace("(.*)", "")
            .replace("(?: |)", "")
            .replace("| ", "")
            .replace("( |)", "")
            .replace("?((.|//)*)", "")
            .replace("?P<shortname>\\w+", "")
            .replace("(", "")
            .replace(")", "")
            .replace("?(\\d+)", "")
        )

    def _get_value(self, stri):
        try:
            value = eval(stri.strip())
        except Exception as er:
            from .. import LOGS

            LOGS.debug(er)
            value = stri.strip()
        return value

    def safe_load(self, file, *args, **kwargs):
        if isinstance(file, str):
            read = file.split("\n")
        else:
            read = file.readlines()
        out = {}
        for line in read:
            if ":" in line:  # Ignores Empty & Invalid lines
                spli = line.split(":", maxsplit=1)
                key = spli[0].strip()
                value = self._get_value(spli[1])
                out.update({key: value or []})
            elif "-" in line:
                spli = line.split("-", maxsplit=1)
                where = out[list(out.keys())[-1]]
                if isinstance(where, list):
                    value = self._get_value(spli[1])
                    if value:
                        where.append(value)
        return out

    def get_chat_and_msgid(self, link):
        matches = re.findall("https:\\/\\/t\\.me\\/(c\\/|)(.*)\\/(.*)", link)
        if not matches:
            return None, None
        _, chat, msg_id = matches[0]
        if chat.isdigit():
            chat = int("-100" + chat)
        return chat, int(msg_id)

    # --------- END --------- #

    async def advice(self):
        from ._fns import fns  # shared HTTP session lives on the union
        """Return a random quote from zenquotes.io."""
        try:
            data = await fns.async_searcher("https://zenquotes.io/api/random", re_json=True)
            quote = data[0]["q"]
            author = data[0]["a"]
            return f'"{quote}"  -  {author}'
        except Exception as exc:
            LOGS.debug("advice fetch failed: %s", exc)
            return '"Stay focused."  -  Unknown'

    def import_library(self, library_name: str, package_name: str = None):
        package_name = package_name or library_name
        try:
            return importlib.import_module(library_name)
        except ImportError as exc:
            raise DependencyMissingError(
                f"`{package_name}` is not installed. "
                f"Install it (e.g. `pip install {package_name}`) to use this feature."
            ) from exc

# ------------------Logo Gen Helpers----------------
# @TechiError
class LogoHelper:
    @staticmethod
    def get_text_bbox(text, image, font):
        im = Image.new("RGB", (image.width, image.height))
        draw = ImageDraw.Draw(im)
        return draw.textbbox((0, 0), text, font)

    @staticmethod
    def find_font_size(text, font, image, target_width_ratio):
        tested_font_size = 100
        tested_font = ImageFont.truetype(font, tested_font_size)
        bbox = LogoHelper.get_text_bbox(text, image, tested_font)
        observed_width = bbox[2] - bbox[0]
        estimated_font_size = (
            tested_font_size / (observed_width / image.width) * target_width_ratio
        )
        return round(estimated_font_size)

    @staticmethod
    def make_logo(imgpath, text, funt, **args):
        fill = args.get("fill")
        width_ratio = args.get("width_ratio") or 0.7
        stroke_width = int(args.get("stroke_width"))
        stroke_fill = args.get("stroke_fill")

        img = Image.open(imgpath)
        width, height = img.size
        draw = ImageDraw.Draw(img)
        font_size = LogoHelper.find_font_size(text, funt, img, width_ratio)
        font = ImageFont.truetype(funt, font_size)
        bbox = LogoHelper.get_text_bbox(text, img, font)
        x = (width - (bbox[2] - bbox[0])) / 2
        y = (height - (bbox[3] - bbox[1])) / 2
        draw.text(
            (x, y),
            text,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=stroke_fill,
        )
        file_name = ToolKit.check_filename("logo.png")
        img.save(file_name, "PNG")
        return file_name

# --------------------------------------
class ChatBot:
    def __init__(
        self,
        query: str = None,
    ):
        self.query = query

    async def get_response_gemini_oracle(
        self,
        api_key: str = None,
        user_id: int = None,
        mongo_url: str = str(chatbot_mongo),
        re_json: bool = False,
        is_login: bool = False,
        is_multi_chat: bool = False,
        is_gemini_oracle: bool = False,
        gemini_api_key: str = None,
    ):
        from ._fns import fns  # shared HTTP session lives on the union
        url = "https://ufoptg-ufop-api.hf.space/UFoP/gemini-the-oracle"
        headers = {"accept": "application/json", "api-key": api_key}
        params = {
            "query": self.query,
            "mongo_url": mongo_url,
            "user_id": user_id,
            "is_logon": is_login,
            "is_multi_chat": is_multi_chat,
            "gemini_api_key": gemini_api_key,
        }

        async def evaluate_response(response):
            if response.status != 200:
                return f"Error status: {response.status}"

            if is_gemini_oracle:
                if re_json:
                    check_response = await response.json()
                else:
                    check_response = await response.text()
                return check_response
            else:
                return f"WTF THIS {self.query}"

        try:
            response_data = await fns.async_searcher(
                url, post=True, headers=headers, json=params, evaluate=evaluate_response
            )
            return response_data
        except aiohttp.ClientError as client_err:
            return f"Error connecting to the server: {client_err}"

# ------------------------#

class LottieException(Exception): ...

class TgConverter:
    """Convert files related to Telegram"""

    @staticmethod
    async def animated_sticker(file, out_path="sticker.tgs", throw=False, remove=False):
        """Convert to/from animated sticker."""
        if out_path.endswith("webp"):
            er, out = await Helper.bash(
                f"lottie_convert.py --webp-quality 100 --webp-skip-frames 100 '{file}' '{out_path}'"
            )
        else:
            er, out = await Helper.bash(f"lottie_convert.py '{file}' '{out_path}'")
        if er and throw:
            raise LottieException(er)
        if remove:
            os.remove(file)
        if os.path.exists(out_path):
            return out_path

    @staticmethod
    async def animated_to_gif(file, out_path="gif.gif"):
        """Convert animated sticker to gif."""
        await Helper.bash(
            f"lottie_convert.py '{ToolKit._unquote_text(file)}' '{ToolKit._unquote_text(out_path)}'"
        )
        return out_path

    @staticmethod
    def resize_photo_sticker(photo):
        """Resize the given photo to 512x512 (for creating telegram sticker)."""
        image = Image.open(photo)
        if (image.width and image.height) < 512:
            size1 = image.width
            size2 = image.height
            if image.width > image.height:
                scale = 512 / size1
                size1new = 512
                size2new = size2 * scale
            else:
                scale = 512 / size2
                size1new = size1 * scale
                size2new = 512
            size1new = math.floor(size1new)
            size2new = math.floor(size2new)
            sizenew = (size1new, size2new)
            image = image.resize(sizenew)
        else:
            maxsize = (512, 512)
            image.thumbnail(maxsize)
        return image

    @staticmethod
    async def ffmpeg_convert(input_, output, remove=False):
        if output.endswith(".webm"):
            return await TgConverter.create_webm(
                input_, name=output[:-5], remove=remove
            )
        if output.endswith(".gif"):
            await Helper.bash(f"ffmpeg -i '{input_}' -an -sn -c:v copy '{output}.mp4' -y")
        else:
            await Helper.bash(f"ffmpeg -i '{input_}' '{output}' -y")
        if remove:
            os.remove(input_)
        if os.path.exists(output):
            return output

    @staticmethod
    async def create_webm(file, name="video", remove=False):
        _ = await ToolKit.metadata(file)
        name += ".webm"
        h, w = _["height"], _["width"]
        if h == w and h != 512:
            h, w = 512, 512
        if h != 512 or w != 512:
            if h > w:
                h, w = 512, -1
            if w > h:
                h, w = -1, 512
        await Helper.bash(
            f'ffmpeg -i "{file}" -preset fast -an -to 00:00:03 -crf 30 -bufsize 256k -b:v {_["bitrate"]} -vf "scale={w}:{h},fps=30" -c:v libvpx-vp9 "{name}" -y'
        )
        if remove:
            os.remove(file)
        return name

    @staticmethod
    def to_image(input_, name, remove=False):
        try:
            import cv2
        except ImportError:
            raise DependencyMissingError("This function needs 'cv2' to be installed.")
        img = cv2.VideoCapture(input_)
        ult, roid = img.read()
        cv2.imwrite(name, roid)
        if remove:
            os.remove(input_)
        return name

    @staticmethod
    async def convert(
        input_file,
        outname="converted",
        convert_to=None,
        allowed_formats=[],
        remove_old=True,
    ):
        if "." in input_file:
            ext = input_file.split(".")[-1].lower()
        else:
            return input_file

        if (
            ext in allowed_formats
            or ext == convert_to
            or not (convert_to or allowed_formats)
        ):
            return input_file

        def recycle_type(exte):
            return convert_to == exte or exte in allowed_formats

        # Sticker to Something
        if ext == "tgs":
            for extn in ["webp", "json", "png", "mp4", "gif"]:
                if recycle_type(extn):
                    name = outname + "." + extn
                    return await TgConverter.animated_sticker(
                        input_file, name, remove=remove_old
                    )
            if recycle_type("webm"):
                input_file = await TgConverter.convert(
                    input_file, convert_to="gif", remove_old=remove_old
                )
                return await TgConverter.create_webm(input_file, outname, remove=True)
        # Json -> Tgs
        elif ext == "json":
            if recycle_type("tgs"):
                name = outname + ".tgs"
                return await TgConverter.animated_sticker(
                    input_file, name, remove=remove_old
                )
        # Video to Something
        elif ext in ["webm", "mp4", "gif"]:
            for exte in ["webm", "mp4", "gif"]:
                if recycle_type(exte):
                    name = outname + "." + exte
                    return await TgConverter.ffmpeg_convert(
                        input_file, name, remove=remove_old
                    )
            for exte in ["png", "jpg", "jpeg", "webp"]:
                if recycle_type(exte):
                    name = outname + "." + exte
                    return TgConverter.to_image(input_file, name, remove=remove_old)
        # Image to Something
        elif ext in ["jpg", "jpeg", "png", "webp"]:
            for extn in ["png", "webp", "ico"]:
                if recycle_type(extn):
                    img = Image.open(input_file)
                    name = outname + "." + extn
                    img.save(name, extn.upper())
                    if remove_old:
                        os.remove(input_file)
                    return name
            for extn in ["webm", "gif", "mp4"]:
                if recycle_type(extn):
                    name = outname + "." + extn
                    if extn == "webm":
                        input_file = await TgConverter.convert(
                            input_file,
                            convert_to="png",
                            remove_old=remove_old,
                        )
                    return await TgConverter.ffmpeg_convert(
                        input_file, name, remove=True if extn == "webm" else remove_old
                    )

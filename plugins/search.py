# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

 • (saavn removed — service defunct)
    Download songs from Saavn.

• `{i}google <query>`
    For doing google search.

• `{i}github <username>`
    Get full information of the users github profile.

• `{i}img <query>`
  `{i}img <query> ; <no of results>`
    For doing Images search.

• `{i}reverse`
    Reply an Image or sticker to find its sauce.
"""

import os

import aiohttp
from bs4 import BeautifulSoup as bs

try:
    from PIL import Image
except ImportError:
    Image = None
try:
    import cv2
except ImportError:
    cv2 = None

from pyUltroid.fns import fns
from pyUltroid.fns import fns

from . import (
    LOGS,
    con,
    eod,
    get_string,
    udB,
    ultroid_cmd,
)


@ultroid_cmd(
    pattern="github (.*)",
)
async def gitsearch(event):
    usrname = event.pattern_match.group(1).strip()
    if not usrname:
        return await event.eor(get_string("srch_1"))
    url = f"https://api.github.com/users/{usrname}"
    ult = await fns.async_searcher(url, re_json=True)
    try:
        uname = ult["login"]
        uid = ult["id"]
        upic = f"https://avatars.githubusercontent.com/u/{uid}"
        ulink = ult["html_url"]
        uacc = ult["name"]
        ucomp = ult["company"]
        ublog = ult["blog"]
        ulocation = ult["location"]
        ubio = ult["bio"]
        urepos = ult["public_repos"]
        ufollowers = ult["followers"]
        ufollowing = ult["following"]
    except Exception:
        return await event.eor(get_string("srch_2"))
    fullusr = f"""
**[GITHUB]({ulink})**
**Name** - {uacc}
**UserName** - {uname}
**ID** - {uid}
**Company** - {ucomp}
**Blog** - {ublog}
**Location** - {ulocation}
**Bio** - {ubio}
**Repos** - {urepos}
**Followers** - {ufollowers}
**Following** - {ufollowing}
"""
    await event.respond(fullusr, file=upic)
    await event.delete()


@ultroid_cmd(
    pattern="google( (.*)|$)",
    manager=True,
)
async def google(event):
    inp = event.pattern_match.group(1).strip()
    if not inp:
        return await eod(event, get_string("autopic_1"))
    x = await event.eor(get_string("com_2"))
    gs = await fns.google_search(inp)
    if not gs:
        return await eod(x, get_string("autopic_2").format(inp))
    out = ""
    for res in gs:
        text = res["title"]
        url = res["link"]
        des = res["description"]
        out += f" 👉🏻  [{text}]({url})\n`{des}`\n\n"
    omk = f"**Google Search Query:**\n`{inp}`\n\n**Results:**\n{out}"
    await x.eor(omk, link_preview=False)


@ultroid_cmd(pattern="img( (.*)|$)")
async def goimg(event):
    query = event.pattern_match.group(1).strip()
    if not udB.get_key("SERPAPI"):
        return await event.eor("Error: SERPAPI not set please set a SERP API")
    if not query:
        return await event.eor(get_string("autopic_1"))
    nn = await event.eor(get_string("com_1"))
    lmt = 5
    if ";" in query:
        try:
            lmt = int(query.split(";")[1])
            query = query.split(";")[0]
        except Exception:
            pass
    images = await fns.get_google_images(query)
    for img in images[:lmt]:
        try:
            await event.client.send_file(event.chat_id, file=img["original"])
        except Exception as er:
            LOGS.exception(er)
    await nn.delete()


@ultroid_cmd(pattern="reverse$")
async def reverse(event):
    reply = await event.get_reply_message()
    if not reply:
        return await event.eor("`Reply to an Image`")
    ult = await event.eor(get_string("com_1"))
    dl = await reply.download_media()
    file = await con.convert(dl, convert_to="png")
    img = Image.open(file)
    x, y = img.size
    data = aiohttp.FormData()
    with open(file, "rb") as fh:
        data.add_field("encoded_image", fh.read(), filename=file)
    async with event.client.http.post(
        "https://www.google.com/searchbyimage/upload",
        data=data,
        allow_redirects=False,
    ) as grs:
        loc = grs.headers.get("Location")
    response = await fns.async_searcher(
        loc,
        headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:58.0) Gecko/20100101 Firefox/58.0",
        },
    )
    xx = bs(response, "html.parser")
    div = xx.find_all("div", {"class": "r5a77d"})[0]
    alls = div.find("a")
    link = alls["href"]
    text = alls.text
    await ult.edit(f"`Dimension ~ {x} : {y}`\nSauce ~ [{text}](google.com{link})")
    images = await fns.get_google_images(text)
    for z in images[:2]:
        try:
            await event.client.send_file(
                event.chat_id,
                file=z["original"],
                caption="Similar Images Realted to Search",
            )
        except Exception as er:
            LOGS.exception(er)
    os.remove(file)  # Saavn removed — service defunct

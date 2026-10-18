# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

import base64
import hashlib
import inspect
import os
from datetime import datetime
from html import unescape
from random import choice

from bs4 import BeautifulSoup as bs
from telethon import Button
from telethon.tl.alltlobjects import LAYER, tlobjects
from telethon.tl.types import InputWebDocument as wb

from custom.rich import resolve_style
from pyUltroid import LOGS, asst
from pyUltroid._core.buttonkit import button as rich_button
from pyUltroid._core.events import InlineResultBuilder
from pyUltroid._core.inlineresults import InlineResults
from pyUltroid._core.richmsg import RichBlocks, rich_markup_from_blocks
from pyUltroid._core.webupload import (
    MENU_SERVICES,
    SERVICE_LABELS,
    UploadResult,
)
from pyUltroid.fns import fns
from pyUltroid.fns.tools import _webupload_cache
from strings import get_string

from . import InlinePlugin, _ult_cache, callback, in_pattern
from ._cbs import cb, cb_pattern

#
# C8   inline result restyle helpers.
#
# InlineResults.article_rich() (pyUltroid/_core/inlineresults.py) emits only
# native RichMessage articles. Missing rich-message constructors or invalid
# content are hard errors; this path never falls back to a plain article.
# Block rendering delegates to pyUltroid._core.richmsg, so table() and
# expandable() blocks use the same native model as other rich-message callers.
#
# _rich_article below additionally preserves inline-result thumb=/content=
# metadata on the native RichMessage result.
#


async def _rich_article(
    ires,
    title,
    blocks,
    *,
    description=None,
    url=None,
    id=None,
    buttons=None,
    thumb=None,
    content=None,
    rtl=False,
):
    """Build one article_rich inline result from a :class:RichBlocks.

    Converts *blocks* to native RichMarkup while preserving its file vector,
    then passes the complete payload to InlineResults.article_rich. Local
    media that has not already been uploaded to Telegram is rejected by the
    layer-230 engine; it is never silently dropped. thumb/content are assigned
    directly on the pinned InputBotInlineResult schema.
    """
    payload = rich_markup_from_blocks(blocks, format="html")
    result = await ires.article_rich(
        title,
        payload,
        format="html",
        description=description,
        url=url,
        id=id,
        buttons=buttons,
        rtl=rtl,
    )
    changed = False
    if thumb is not None:
        result.thumb = thumb
        changed = True
    if content is not None:
        result.content = content
        changed = True
    if changed and id is None:
        result.id = hashlib.sha256(bytes(result)).hexdigest()
    return result


def _styled(text, *, data=None, url=None, preset=None):
    """Style one button via buttonkit.button (bg-colour presets).

    Falls back to a plain telethon.Button if styling itself raises for
    any reason   custom.rich.button already degrades style/icon
    internally (see its docstring), this is a belt-and-suspenders probe so
    callers below never lose a button entirely over styling.
    """
    try:
        return rich_button(text, data=data, url=url, preset=preset)
    except Exception:
        return (
            Button.url(text, url=url)
            if url is not None
            else Button.inline(text, data=data)
        )


def _styled_switch(text, query, *, same_peer=True, preset=None):
    """Style a switch_inline (search-again / share / fork) button."""
    try:
        style, icon = resolve_style(preset=preset)
        return Button.switch_inline(
            text, query=query, same_peer=same_peer, style=style, icon=icon
        )
    except Exception:
        return Button.switch_inline(text, query=query, same_peer=same_peer)


SUP_BUTTONS = [
    [
        _styled(
            "  Repo  ", url="https://github.com/TeamUltroid/Ultroid", preset="primary"
        ),
        _styled("  Support  ", url="t.me/UltroidSupportChat", preset="default"),
    ],
]

ofox = "https://graph.org/file/231f0049fcd722824f13b.jpg"
gugirl = "https://graph.org/file/0df54ae4541abca96aa11.jpg"
ultpic = "https://graph.org/file/4136aa1650bc9d4109cc5.jpg"

apis = [
    "QUl6YVN5QXlEQnNZM1dSdEI1WVBDNmFCX3c4SkF5NlpkWE5jNkZV",
    "QUl6YVN5QkYwenhMbFlsUE1wOXh3TVFxVktDUVJxOERnZHJMWHNn",
    "QUl6YVN5RGRPS253blB3VklRX2xiSDVzWUU0Rm9YakFLSVFWMERR",
]


@in_pattern("ofox", owner=True)
async def _(e):
    ires = InlineResults(e)
    try:
        match = e.text.split(" ", maxsplit=1)[1]
    except IndexError:
        blocks = (
            RichBlocks().header("OFoxRecovery").paragraph("You didn't search anything")
        )
        kkkk = await _rich_article(
            ires,
            "Enter Device Codename",
            blocks,
            thumb=wb(ofox, 0, "image/jpeg", []),
            buttons=_styled_switch(
                "Search Again", "ofox ", same_peer=True, preset="default"
            ),
        )
        return await e.answer([kkkk])
    device, releases = await fns.get_ofox(match)
    if device.get("detail") is None:
        fox = []
        fullname = device["full_name"]
        codename = device["codename"]
        str(device["supported"])
        maintainer = device["maintainer"]["name"]
        link = f"https://orangefox.download/device/{codename}"
        for data in releases["data"]:
            release = data["type"]
            version = data["version"]
            size = fns.humanbytes(data["size"])
            release_date = datetime.utcfromtimestamp(data["date"]).strftime("%Y-%m-%d")
            blocks = (
                RichBlocks()
                .header(f"OrangeFox Recovery For {fullname}")
                .table(
                    [
                        ["Field", "Value"],
                        ["Full Name", fullname],
                        ["Codename", codename],
                        ["Maintainer", maintainer],
                        ["Build Type", release],
                        ["Version", version],
                        ["Size", size],
                        ["Build Date", release_date],
                    ],
                    caption="Device Details",
                )
            )
            fox.append(
                await _rich_article(
                    ires,
                    fullname,
                    blocks,
                    description=f"{version}\n{release_date}",
                    thumb=wb(ofox, 0, "image/jpeg", []),
                    buttons=[
                        _styled("Download", url=link, preset="primary"),
                        _styled_switch(
                            "Search Again", "ofox ", same_peer=True, preset="default"
                        ),
                    ],
                )
            )
        await e.answer(
            fox, switch_pm="OrangeFox Recovery Search.", switch_pm_param="start"
        )
    else:
        await e.answer(
            [], switch_pm="OrangeFox Recovery Search.", switch_pm_param="start"
        )


#: emoji icon shown on each fl2lnk service button (presentation-only   real
#: custom-emoji document ids are deployment-specific, see buttonkit.py).
_FL_SERVICE_ICONS = {
    "catbox": "",
    "litterbox": "",
    "file.io": "",
    "0x0.st": "",
    "transfer": "",
    "uguu": "",
    "filebin": "",
    "siasky": "",
}


@in_pattern("fl2lnk ?(.*)", owner=True)
async def fl2lnk_command(event):
    match = event.pattern_match.group(1)
    if not match or ":" not in match:
        await event.respond(
            "Invalid format. Please provide chat ID and message ID in the format 'chat_id:message_id'."
        )
        return

    chat_id, msg_id = match.split(":")
    try:
        filename = _webupload_cache[int(chat_id)][int(msg_id)]
    except (KeyError, ValueError):
        await event.respond("File not found or invalid chat ID/message ID.")
        return

    if "/" in filename:
        filename = filename.split("/")[-1]

    ires = InlineResults(event)
    buttons = []
    row_btns = []
    for key in MENU_SERVICES:
        icon = _FL_SERVICE_ICONS.get(key, "")
        row_btns.append(
            _styled(
                f"{icon} {SERVICE_LABELS.get(key, key)}",
                data=cb("assistant", "fl2lnk", key, chat_id, msg_id),
                preset="primary",
            )
        )
        if len(row_btns) == 2:
            buttons.append(row_btns)
            row_btns = []
    if row_btns:
        buttons.append(row_btns)

    try:
        blocks = RichBlocks().header(" Upload File").paragraph(filename)
        articles = [
            await _rich_article(
                ires,
                f"Upload {filename}",
                blocks,
                buttons=buttons,
            )
        ]
    except Exception as e:
        LOGS.exception(e)
        blocks = (
            RichBlocks()
            .header("fl2lnk")
            .paragraph("File not found or error in processing.")
        )
        articles = [await _rich_article(ires, "fl2lnk", blocks)]

    await event.answer(articles, switch_pm="File to Link.", switch_pm_param="start")


@callback(cb_pattern("assistant", "fl2lnk"), owner=True)
async def fl_callback(event):
    tail = event.data_match.group(1).decode("utf-8")
    parts = tail.split(":")
    if len(parts) != 3:
        await event.respond("Invalid callback data format.")
        return

    host, chat_id, msg_id = parts

    try:
        filename = _webupload_cache[int(chat_id)][int(msg_id)]
    except (KeyError, ValueError):
        await event.respond("File not found or invalid chat ID/message ID.")
        return

    disp = filename.split("/")[-1] if "/" in filename else filename
    label = SERVICE_LABELS.get(host, host)

    import time as _time

    state = {"pct": -1, "t": 0.0}

    async def on_progress(sent, total):
        pct = round(sent / total * 100) if total else 0
        now = _time.time()
        if pct - state["pct"] >= 5 or now - state["t"] >= 1.5 or sent >= total:
            state["pct"] = pct
            state["t"] = now
            try:
                await event.edit(f" Uploading {disp} on {label}... {pct}%")
            except Exception:
                pass

    await event.edit(f" Uploading {disp} on {label}...")
    result = await fns.webupload_result(
        int(chat_id), int(msg_id), host, on_progress=on_progress
    )

    if isinstance(result, UploadResult):
        text = f"**Uploaded** {disp} **on** {label}."
        buttons = [[_styled(" View", url=result.url, preset="primary")]]
        if result.delete_url:
            text += f"\n Delete: {result.delete_url}"
            if result.delete_note:
                text += f" ({result.delete_note})"
        if result.expires:
            text += f"\n Expires: {result.expires}"
        await event.edit(text, buttons=buttons, link_preview=False)
        try:
            os.remove(f"resources/downloads/{disp}")
        except FileNotFoundError:
            pass
    else:
        await event.edit(f"Failed to upload {disp} on {label}. Error: {result}")


@in_pattern("repo", owner=True)
async def repo(e):
    ires = InlineResults(e)
    blocks = (
        RichBlocks()
        .header("Ultroid Userbot")
        .list(["Userbot", "Telethon"])
        .paragraph("  **ULTROID USERBOT**  ")
    )
    res = [
        await _rich_article(
            ires,
            "Ultroid Userbot",
            blocks,
            description="Userbot | Telethon",
            thumb=wb(ultpic, 0, "image/jpeg", []),
            buttons=SUP_BUTTONS,
        ),
    ]
    await e.answer(res, switch_pm="Ultroid Repo.", switch_pm_param="start")


@in_pattern("go", owner=True)
async def gsearch(q_event):
    try:
        match = q_event.text.split(maxsplit=1)[1]
    except IndexError:
        return await q_event.answer(
            [], switch_pm="Google Search. Enter a query!", switch_pm_param="start"
        )
    searcher = []
    gresults = await fns.google_search(match)
    for i in gresults:
        try:
            title = i["title"]
            link = i["link"]
            desc = i["description"]
            searcher.append(
                await InlineResultBuilder(q_event).article(
                    title=title,
                    description=desc,
                    thumb=wb(gugirl, 0, "image/jpeg", []),
                    text=f"**Google Search**\n\n**  Title  **\n{title}\n\n**  Description  **\n{desc}",
                    link_preview=False,
                    buttons=[
                        [Button.url("Link", url=f"{link}")],
                        [
                            Button.switch_inline(
                                "Search Again",
                                query="go ",
                                same_peer=True,
                            ),
                            Button.switch_inline(
                                "Share",
                                query=f"go {match}",
                                same_peer=False,
                            ),
                        ],
                    ],
                ),
            )
        except IndexError:
            break
    await q_event.answer(searcher, switch_pm="Google Search.", switch_pm_param="start")


@in_pattern("mods", owner=True)
async def _(e):
    try:
        quer = e.text.split(" ", maxsplit=1)[1]
    except IndexError:
        return await e.answer(
            [], switch_pm="Mod Apps Search. Enter app name!", switch_pm_param="start"
        )
    start = 0 * 3 + 1
    da = base64.b64decode(choice(apis)).decode("ascii")
    url = f"https://www.googleapis.com/customsearch/v1?key={da}&cx=25b3b50edb928435b&q={quer}&start={start}"
    data = await fns.async_searcher(url, re_json=True)
    search_items = data.get("items", [])
    modss = []
    for a in search_items:
        title = a.get("title")
        desc = a.get("snippet")
        link = a.get("link")
        text = f"**  Title  ** {title}\n\n"
        text += f"**Description** {desc}"
        modss.append(
            await InlineResultBuilder(e).article(
                title=title,
                description=desc,
                text=text,
                link_preview=True,
                buttons=[
                    [Button.url("Download", url=f"{link}")],
                    [
                        Button.switch_inline(
                            "More Mods",
                            query="mods ",
                            same_peer=True,
                        ),
                        Button.switch_inline(
                            "Share",
                            query=f"mods {quer}",
                            same_peer=False,
                        ),
                    ],
                ],
            ),
        )
    await e.answer(modss, switch_pm="Search Mod Applications.", switch_pm_param="start")


APP_CACHE = {}
RECENTS = {}
PLAY_API = "https://googleplay.onrender.com/api/apps?q="


@in_pattern("app", owner=True)
async def _(e):
    try:
        f = e.text.split(maxsplit=1)[1].lower()
    except IndexError:
        get_string("instu_1")
        res = []
        if APP_CACHE and RECENTS.get(e.sender_id):
            res.extend(
                APP_CACHE[a][0] for a in RECENTS[e.sender_id] if APP_CACHE.get(a)
            )
        return await e.answer(
            res, switch_pm=get_string("instu_2"), switch_pm_param="start"
        )
    try:
        return await e.answer(
            APP_CACHE[f], switch_pm="Application Searcher.", switch_pm_param="start"
        )
    except KeyError:
        pass
    foles = []
    url = PLAY_API + f.replace(" ", "+")
    aap = await fns.async_searcher(url, re_json=True)
    ires = InlineResults(e)
    for z in aap["results"][:50]:
        url = "https://play.google.com/store/apps/details?id=" + z["appId"]
        name = z["title"]
        desc = unescape(z["summary"])[:300].replace("<br>", "\n") + "..."
        dev = z["developer"]["devId"]
        blocks = RichBlocks().header(name).list([f"Developer: {dev}"]).paragraph(desc)
        foles.append(
            await _rich_article(
                ires,
                name,
                blocks,
                description=dev,
                thumb=wb(z["icon"], 0, "image/jpeg", []),
                buttons=[
                    [_styled("Link", url=url, preset="primary")],
                    [
                        _styled_switch(
                            "More Apps", "app ", same_peer=True, preset="default"
                        ),
                        _styled_switch(
                            "Share", f"app {f}", same_peer=False, preset="default"
                        ),
                    ],
                ],
            ),
        )
    APP_CACHE.update({f: foles})
    if RECENTS.get(e.sender_id):
        RECENTS[e.sender_id].append(f)
    else:
        RECENTS.update({e.sender_id: [f]})
    await e.answer(foles, switch_pm="Application Searcher.", switch_pm_param="start")


PISTON_URI = "https://emkc.org/api/v2/piston/"
PISTON_LANGS = {}


@in_pattern("run", owner=True)
async def piston_run(event):
    ires = InlineResults(event)
    try:
        lang = event.text.split()[1]
        code = event.text.split(maxsplit=2)[2]
    except IndexError:
        blocks = (
            RichBlocks()
            .header("Bad Query")
            .paragraph("Usage: [Language] [code]")
            .code(f'@{asst.me.username} run python print("hello world")')
            .paragraph("Language List: https://graph.org/Ultroid-09-01-6")
        )
        result = await _rich_article(
            ires,
            "Bad Query",
            blocks,
            description="Usage: [Language] [code]",
            thumb=wb(
                "https://graph.org/file/e33c57fc5f1044547e4d8.jpg", 0, "image/jpeg", []
            ),
        )
        return await event.answer([result])
    if not PISTON_LANGS:
        se = await fns.async_searcher(f"{PISTON_URI}runtimes", re_json=True)
        PISTON_LANGS.update({lang.pop("language"): lang for lang in se})
    if lang in PISTON_LANGS.keys():
        version = PISTON_LANGS[lang]["version"]
    else:
        blocks = (
            RichBlocks()
            .header("Unsupported Language")
            .paragraph("Usage: [Language] [code]")
            .code(f'@{asst.me.username} run python print("hello world")')
            .paragraph("Language List: https://graph.org/Ultroid-09-01-6")
        )
        result = await _rich_article(
            ires,
            "Unsupported Language",
            blocks,
            description="Usage: [Language] [code]",
            thumb=wb(
                "https://graph.org/file/e33c57fc5f1044547e4d8.jpg", 0, "image/jpeg", []
            ),
        )
        return await event.answer([result])
    output = await fns.async_searcher(
        f"{PISTON_URI}execute",
        post=True,
        json={
            "language": lang,
            "version": version,
            "files": [{"content": code}],
        },
        re_json=True,
    )

    output = output["run"]["output"] or get_string("instu_4")
    if len(output) > 3000:
        output = f"{output[:3000]}..."
    blocks = (
        RichBlocks()
        .header("Result")
        .table([["Field", "Value"], ["Language", lang]])
        .code(code, lang=lang)
        .header("Output", level=3)
        .code(output)
    )
    result = await _rich_article(
        ires,
        "Result",
        blocks,
        description=output,
        thumb=wb(
            "https://graph.org/file/871ee4a481f58117dccc4.jpg", 0, "image/jpeg", []
        ),
        buttons=_styled_switch("Fork", event.text, same_peer=True, preset="default"),
    )
    await event.answer([result], switch_pm="  Piston  ", switch_pm_param="start")


FDROID_ = {}


@in_pattern("fdroid", owner=True)
async def do_magic(event):
    try:
        match = event.text.split(" ", maxsplit=1)[1].lower()
    except IndexError:
        return await event.answer(
            [], switch_pm="Enter Query to Search", switch_pm_param="start"
        )
    if FDROID_.get(match):
        return await event.answer(
            FDROID_[match], switch_pm=f"  Results for {match}", switch_pm_param="start"
        )
    link = "https://search.f-droid.org/?q=" + match.replace(" ", "+")
    content = await fns.async_searcher(link, re_content=True)
    BSC = bs(content, "html.parser", from_encoding="utf-8")
    ress = []
    ires = InlineResults(event)
    for dat in BSC.find_all("a", "package-header")[:10]:
        image = dat.find("img", "package-icon")["src"]
        if image.endswith("/"):
            image = "https://graph.org/file/a8dd4a92c5a53a89d0eff.jpg"
        title = dat.find("h4", "package-name").text.strip()
        desc = dat.find("span", "package-summary").text.strip()
        license_ = dat.find("span", "package-license").text.strip()
        blocks = (
            RichBlocks().header(title).list([f"License: {license_}"]).paragraph(desc)
        )
        imga = wb(image, 0, "image/jpeg", [])
        ress.append(
            await _rich_article(
                ires,
                title,
                blocks,
                description=desc,
                thumb=imga,
                content=imga,
                buttons=[
                    _styled(
                        "  Download  ",
                        data=cb(
                            "assistant", "fdroid", dat["href"].split("packages/")[-1]
                        ),
                        preset="primary",
                    ),
                    _styled_switch(
                        "  Share  ", event.text, same_peer=False, preset="default"
                    ),
                ],
            )
        )
    msg = f"Showing {len(ress)} Results!" if ress else "No Results Found"
    FDROID_.update({match: ress})
    await event.answer(ress, switch_pm=msg, switch_pm_param="start")


# Thanks to OpenSource
_bearer_collected = [
    "AAAAAAAAAAAAAAAAAAAAALIKKgEAAAAA1DRuS%2BI7ZRKiagD6KHYmreaXomo%3DP5Vaje4UTtEkODg0fX7nCh5laSrchhtLxeyEqxXpv0w9ZKspLD",
    "AAAAAAAAAAAAAAAAAAAAAL5iUAEAAAAAmo6FYRjqdKlI3cNziIm%2BHUQB9Xs%3DS31pj0mxARMTOk2g9dvQ1yP9wknvY4FPBPUlE00smJcncw4dPR",
    "AAAAAAAAAAAAAAAAAAAAAN6sVgEAAAAAMMjMMWrwgGyv7YQOWN%2FSAsO5SGM%3Dg8MG9Jq93Rlllaok6eht7HvRCruN4Vpzp4NaVsZaaHHWSTzKI8",
]


@in_pattern("twitter", owner=True)
async def twitter_search(event):
    try:
        match = event.text.split(maxsplit=1)[1].lower()
    except IndexError:
        return await event.answer(
            [], switch_pm="Enter Query to Search", switch_pm_param="start"
        )
    try:
        return await event.answer(
            _ult_cache["twitter"][match],
            switch_pm="  Twitter Search  ",
            switch_pm_param="start",
        )
    except KeyError:
        pass
    headers = {"Authorization": f"bearer {choice(_bearer_collected)}"}
    res = await fns.async_searcher(
        f"https://api.twitter.com/1.1/users/search.json?q={match}",
        headers=headers,
        re_json=True,
    )
    reso = []
    ires = InlineResults(event)
    for user in res:
        thumb = wb(user["profile_image_url_https"], 0, "image/jpeg", [])
        pro_ = "https://twitter.com/" + user["screen_name"]
        blocks = (
            RichBlocks()
            .header(user["name"])
            .table(
                [
                    ["Field", "Value"],
                    ["Username", f"@{user['screen_name']}"],
                    ["Followers", str(user["followers_count"])],
                    ["Following", str(user["friends_count"])],
                ]
            )
            .paragraph(user["description"] or "")
        )
        reso.append(
            await _rich_article(
                ires,
                user["name"],
                blocks,
                description=user["description"],
                url=pro_,
                thumb=thumb,
            )
        )
    swi_ = f" Showing {len(reso)} Results!" if reso else "No User Found :("
    await event.answer(reso, switch_pm=swi_, switch_pm_param="start")
    if _ult_cache.get("twitter"):
        _ult_cache["twitter"].update({match: reso})
    else:
        _ult_cache.update({"twitter": {match: reso}})


# Saavn defunct   inline handler removed


@in_pattern("saavn", owner=True)
async def savn_s(event):
    """Saavn music service defunct   inline search removed."""
    ires = InlineResults(event)
    blocks = (
        RichBlocks()
        .header(" Saavn Search")
        .paragraph("This inline search is no longer available.")
    )
    notice = await _rich_article(ires, "Saavn is no longer available", blocks)
    return await event.answer(
        [notice],
        switch_pm=" Saavn is no longer available",
        switch_pm_param="start",
    )


@in_pattern("tl", owner=True)
async def inline_tl(ult):
    try:
        match = ult.text.split(maxsplit=1)[1]
    except IndexError:
        text = f"**Telegram TlObjects Searcher.**\n__(Don't use if you don't know what it is!)__\n\n  Example Usage\n@{asst.me.username} tl GetFullUserRequest"
        return await ult.answer(
            [
                await InlineResultBuilder(ult).article(
                    title="How to Use?",
                    description="Tl Searcher by Ultroid",
                    url="https://t.me/TeamUltroid",
                    text=text,
                )
            ],
            switch_pm="Tl Search ",
            switch_pm_param="start",
        )
    res = []
    for key in tlobjects.values():
        if match.lower() in key.__name__.lower():
            tyyp = "Function" if "tl.functions." in str(key) else "Type"
            text = f"**Name:** {key.__name__}\n"
            text += f"**Category:** {tyyp}\n"
            text += f"\nfrom {key.__module__} import {key.__name__}\n\n"
            if args := str(inspect.signature(key))[1:][:-1]:
                text += "**Parameter:**\n"
                for para in args.split(","):
                    text += " " * 4 + "" + para + "\n"
            text += f"\n**Layer:** {LAYER}"
            res.append(
                await InlineResultBuilder(ult).article(
                    title=key.__name__,
                    description=tyyp,
                    url="https://t.me/TeamUltroid",
                    text=text[:4000],
                )
            )
    mo = f"Showing {len(res)} results!" if res else f"No Results for {match}!"
    await ult.answer(res[:50], switch_pm=mo, switch_pm_param="start")


InlinePlugin.update(
    {
        "Play Store Apps": "app telegram",
        "Modded Apps": "mods minecraft",
        "Search On Google": "go TeamUltroid",
        "WhiSper": "wspr @username Hello",
        "YouTube Downloader": "yt Ed Sheeran Perfect",
        "Piston Eval": "run javascript console.log('Hello Ultroid')",
        "OrangeFox": "ofox beryllium",
        "Twitter User": "twitter theultroid",
        "Fdroid Search": "fdroid telegram",
        "Saavn search": "saavn",
        "Tl Search": "tl",
    }
)

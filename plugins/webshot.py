# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from . import get_help

__doc__ = get_help("help_webshot") or """
✘ Commands Available -

• `{i}webshot <url>`
    Capture and send a screenshot of a webpage.

• `{i}webshot -f <url>`
    Capture a full-page screenshot.

• `{i}webshot -w <width> <url>`
    Set the viewport width (e.g. `-w 800`).

• `{i}webshot -m <url>`
    Use a mobile viewport (375x812).
"""

import contextlib
import os
import re
import tempfile
from urllib.parse import urlencode

try:
    import aiohttp
except ImportError:
    aiohttp = None

from . import HNDLR, LOGS, eod, get_string, ultroid_cmd

# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

_SCHEME_RE = re.compile(r"^https?://", re.I)
_MOBILE_VIEWPORT = {"width": 375, "height": 812}
_DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
_NAV_TIMEOUT = 45_000  # Playwright navigation timeout (ms)
_API_TIMEOUT = 60  # Microlink fallback timeout (s)

_PW_INSTALL_HINT = (
    "Install Playwright for reliable, high-quality screenshots:\n"
    "`pip install playwright && playwright install chromium`"
)


# ---------------------------------------------------------------------------
# argument parsing / validation
# ---------------------------------------------------------------------------


def _parse_opts(text):
    """Split the raw argument string into ``(full_page, mobile, width, url)``.

    Recognised options:
      ``-f``           full-page screenshot
      ``-m``           mobile viewport (375x812)
      ``-w <width>``   viewport width (also accepts ``-w800``)
    Everything else is treated as the URL. Unknown ``-`` options raise
    :class:`ValueError` with a meaningful message.
    """
    tokens = text.split()
    full_page = mobile = False
    width = None
    url_tokens = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-f":
            full_page = True
        elif tok == "-m":
            mobile = True
        elif tok == "-w":
            i += 1
            if i >= len(tokens):
                raise ValueError("Option `-w` needs a width value.")
            width = tokens[i]
        elif tok.startswith("-w") and tok[2:].isdigit():
            width = tok[2:]
        elif tok.startswith("-") and len(tok) > 1:
            raise ValueError(
                f"Unknown option `{tok}`.\n"
                f"Use `-f` (full page), `-w <width>` or `-m` (mobile)."
            )
        else:
            url_tokens.append(tok)
        i += 1
    return full_page, mobile, width, " ".join(url_tokens).strip()


def _normalize_url(raw):
    """Return *raw* with a scheme, or ``None`` when it isn't a URL."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if not _SCHEME_RE.match(raw):
        raw = "https://" + raw
    # The host lives between the scheme and the first path/query/space.
    match = re.match(r"^https?://([^/\s?#]+)", raw, re.I)
    if not match or "." not in match.group(1):
        return None
    return raw


# ---------------------------------------------------------------------------
# capture backends
# ---------------------------------------------------------------------------


async def _playwright_shot(url, full_page, mobile, width, dest):
    """Capture a screenshot with Playwright (Chromium).

    Playwright is imported lazily so the plugin loads even when it isn't
    installed; the resulting :class:`ImportError` is handled by the caller,
    which falls back to the keyless screenshot API.
    """
    from playwright.async_api import async_playwright

    try:
        from playwright.async_api import TimeoutError as PWTimeout
    except ImportError:  # very old / very new Playwright — degrade gracefully
        PWTimeout = ()

    if mobile:
        viewport = dict(_MOBILE_VIEWPORT)
    elif width:
        viewport = {"width": int(width), "height": 800}
    else:
        viewport = dict(_DEFAULT_VIEWPORT)

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        try:
            context = await browser.new_context(
                viewport=viewport,
                is_mobile=mobile,
                has_touch=mobile,
            )
            page = await context.new_page()
            try:
                await page.goto(
                    url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT
                )
            except PWTimeout:
                raise RuntimeError(f"timed out loading `{url}`")
            # give client-side rendering a brief moment to settle
            await page.wait_for_timeout(1500)
            await page.screenshot(path=dest, full_page=full_page)
        finally:
            await browser.close()


async def _microlink_shot(url, full_page, mobile, width, dest):
    """Fallback capture via the keyless Microlink screenshot API."""
    if aiohttp is None:
        raise RuntimeError("aiohttp is not available for the screenshot API fallback.")

    params = {"url": url, "screenshot": "true", "screenshot.type": "png"}
    if full_page:
        params["screenshot.fullPage"] = "true"
    if mobile:
        params["screenshot.device"] = "iphone-x"
    elif width:
        params["screenshot.width"] = str(width)
    api_url = "https://api.microlink.io/?" + urlencode(params)

    timeout = aiohttp.ClientTimeout(total=_API_TIMEOUT)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(api_url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"screenshot API returned HTTP {resp.status}")
            data = await resp.json()
        if data.get("status") != "success":
            raise RuntimeError(
                f"screenshot API failed: {data.get('status', 'unknown')}"
            )
        try:
            shot_url = data["data"]["screenshot"]["url"]
        except (KeyError, TypeError):
            raise RuntimeError("screenshot API returned no image")
        async with session.get(shot_url) as resp:
            if resp.status != 200:
                raise RuntimeError(f"screenshot download failed (HTTP {resp.status})")
            content = await resp.read()

    if not content:
        raise RuntimeError("screenshot API returned an empty image")
    with open(dest, "wb") as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
# command
# ---------------------------------------------------------------------------


@ultroid_cmd(pattern="webshot( (.*)|$)")
async def webshot(event):
    """Capture a screenshot of a webpage and send it."""
    raw = (event.pattern_match.group(1) or "").strip()

    try:
        full_page, mobile, width, url = _parse_opts(raw)
    except ValueError as err:
        return await eod(event, str(err), time=8)

    if width is not None:
        try:
            width = int(width)
            if not 100 <= width <= 4000:
                raise ValueError
        except (TypeError, ValueError):
            return await eod(
                event, "`Width must be a number between 100 and 4000.`", time=8
            )

    # Fall back to the replied message's text when no URL is given inline.
    if not url and event.is_reply:
        reply = await event.get_reply_message()
        if reply and reply.message:
            url = reply.message.strip()

    if not url:
        return await eod(
            event,
            f"{get_string('wbs_1')}\n`{HNDLR}webshot <url> [-f] [-w <width>] [-m]`",
            time=8,
        )

    url = _normalize_url(url)
    if not url:
        return await eod(event, get_string("wbs_2"), time=8)

    msg = await event.eor("`Capturing screenshot...`")
    fd, dest = tempfile.mkstemp(suffix=".png", prefix="webshot_")
    os.close(fd)

    used_fallback = False
    try:
        try:
            await _playwright_shot(url, full_page, mobile, width, dest)
        except ImportError:
            # Playwright not installed -> use the keyless screenshot API.
            used_fallback = True
            await _microlink_shot(url, full_page, mobile, width, dest)
        except Exception as err:  # Playwright present but failed -> API fallback
            LOGS.warning("webshot: Playwright failed (%s) — trying API fallback.", err)
            used_fallback = True
            await _microlink_shot(url, full_page, mobile, width, dest)

        if not os.path.exists(dest) or os.path.getsize(dest) == 0:
            raise RuntimeError("the page could not be rendered (empty screenshot)")

        caption = get_string("wbs_3").replace("{}", url)
        if used_fallback:
            caption += "\n\n_Fallback screenshot API used._\n" + _PW_INSTALL_HINT
        await event.client.send_file(
            event.chat_id, dest, caption=caption, force_document=bool(full_page)
        )
        with contextlib.suppress(Exception):
            await msg.delete()
    except Exception as err:
        hint = f"\n\n{_PW_INSTALL_HINT}" if used_fallback else ""
        await eod(msg, f"**Failed to capture screenshot.**\n`{err}`{hint}", time=12)
    finally:
        with contextlib.suppress(Exception):
            if os.path.exists(dest):
                os.remove(dest)

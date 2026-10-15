# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""Web tools / web_app demonstration plugin (W3-B).

This plugin is intentionally dependency-light: it only uses Telethon core
plus the SarahUB ``custom.tg`` helpers.  All live round-trip paths are gated
behind the ``WEBTOOLS_LIVE`` environment variable so the plugin loads cleanly
and never performs network I/O unless the operator explicitly opts in.
"""


import logging
import os
from typing import Any

from telethon.errors import RPCError
from telethon.tl import functions, types

from custom.tg import ikm, web_app_button
from pyUltroid.webserver.miniapp import MINIAPP_URL_KEY, MiniAppStore

from . import HNDLR, LOG_CHANNEL, udB, ultroid_bot, ultroid_cmd

_LOGS = logging.getLogger(__name__)

# In-memory store of the last web_app data received by the listeners.  This is
# intentionally transient (survives until the next restart) — it is only a demo
# surface, not persistent state.
_last_webapp_data: dict[str, Any] = {}

_LIVE_GATE = bool(os.environ.get("WEBTOOLS_LIVE"))


def _format_data(kind: str, **fields) -> str:
    """Concise summary for a captured web_app event."""
    parts = " ".join(f"{k}={v!r}" for k, v in fields.items())
    return f"`{kind}` {parts}"


@ultroid_cmd(pattern="webapp( (.*)|$)")
async def webapp_cmd(event):
    """Send a message with a web_app inline button.

    Usage: ``{hndlr}webapp <url> [text]``
    """
    args = (event.pattern_match.group(1) or "").strip()
    if not args:
        return await event.eor(f"**Usage:** `{HNDLR}webapp <url> [text]`")

    parts = args.split(maxsplit=1)
    url = parts[0]
    text = parts[1] if len(parts) > 1 else "Open Web App"

    try:
        buttons = ikm([(text, "web_app", url)])
    except Exception as exc:
        _LOGS.exception("webapp_cmd: failed to build markup")
        return await event.eor(f"**Markup error:** `{exc.__class__.__name__}: {exc}`")

    await event.eor(text, buttons=buttons)


@ultroid_cmd(pattern="simplewebapp( (.*)|$)")
async def simplewebapp_cmd(event):
    """Send a message with a reply-keyboard simple web_app button.

    Usage: ``{hndlr}simplewebapp <url> [text]``
    """
    args = (event.pattern_match.group(1) or "").strip()
    if not args:
        return await event.eor(f"**Usage:** `{HNDLR}simplewebapp <url> [text]`")

    parts = args.split(maxsplit=1)
    url = parts[0]
    text = parts[1] if len(parts) > 1 else "Open Web App"

    try:
        btn = web_app_button(text, url, simple=True)
        # KeyboardButtonSimpleWebView is a reply-keyboard button, not an inline
        # button; it must be wrapped in a ReplyKeyboardMarkup.
        reply_markup = types.ReplyKeyboardMarkup(
            rows=[[btn]],
            resize=True,
        )
    except Exception as exc:
        _LOGS.exception("simplewebapp_cmd: failed to build markup")
        return await event.eor(f"**Markup error:** `{exc.__class__.__name__}: {exc}`")

    await event.eor(text, reply_markup=reply_markup)


@ultroid_cmd(pattern="webview( (.*)|$)")
async def webview_cmd(event):
    """Request a web view from a bot and report the returned URL.

    This is the user-side entry point of the ``WebViewResultUrl`` →
    ``SendWebViewResultMessageRequest`` → ``UpdateWebViewResultSent`` round-trip.
    It is gated because it requires a live bot peer that supports web apps.

    Usage: ``{hndlr}webview <bot_username> <url>``
    """
    if not _LIVE_GATE:
        return await event.eor(
            "**Live web-view round-trip is gated.**\n"
            "Set environment variable `WEBTOOLS_LIVE=1` and retry.\n"
            "This requires a bot peer that supports web apps."
        )

    args = (event.pattern_match.group(1) or "").strip()
    parts = args.split(maxsplit=1)
    if len(parts) != 2:
        return await event.eor(f"**Usage:** `{HNDLR}webview <bot_username> <url>`")

    bot_handle, url = parts
    if not bot_handle.startswith("@"):
        bot_handle = "@" + bot_handle

    try:
        bot = await ultroid_bot.get_entity(bot_handle)
    except Exception as exc:
        return await event.eor(
            f"**Could not resolve {bot_handle}:** `{exc.__class__.__name__}: {exc}`"
        )

    try:
        result = await ultroid_bot(
            functions.messages.RequestWebViewRequest(
                peer=bot,
                bot=bot,
                url=url,
                platform="android",
                from_bot_menu=False,
            )
        )
    except Exception as exc:
        _LOGS.exception("webview_cmd: RequestWebViewRequest failed")
        return await event.eor(
            f"**RequestWebViewRequest failed:** `{exc.__class__.__name__}: {exc}`\n"
            "The bot may not support web apps or the URL is not registered."
        )

    summary = (
        f"**WebViewResultUrl**\n"
        f"url: `{result.url}`\n"
        f"query_id: `{getattr(result, 'query_id', None)}`\n"
        f"fullsize: `{getattr(result, 'fullsize', None)}`\n"
        f"fullscreen: `{getattr(result, 'fullscreen', None)}`"
    )
    await event.eor(summary)


@ultroid_cmd(pattern=r"miniapp(?:\s+(.*)|$)", owner_only=True)
async def miniapp_cmd(event):
    """Open the Mini App settings dashboard, or rotate its access token.

    Usage:
      ``{hndlr}miniapp``            — send the settings-dashboard button.
      ``{hndlr}miniapp token new``  — rotate the dashboard token (old links
      stop working immediately; resend ``{hndlr}miniapp`` for a fresh one).

    ``owner_only`` — the dashboard link carries a long-lived bearer token
    (query-param ``?token=``); only the account owner may mint or rotate
    it, never a sudo user. When invoked in a group/channel, the link is
    never posted publicly — it's sent to Saved Messages instead, and this
    chat only gets a generic notice.

    Requires ``MINIAPP_URL`` to be configured (see ``assistant/start.py``,
    Phase C6) — a public HTTPS base URL that reaches this bot's Mini App
    server (``MINIAPP_BIND``/``MINIAPP_PORT``, default ``0.0.0.0:7861``).
    """
    arg = (event.pattern_match.group(1) or "").strip().lower()
    store = MiniAppStore(udB)

    if arg == "token new":
        try:
            store.rotate_token()
        except Exception:
            _LOGS.exception("miniapp_cmd: failed to rotate %s", "MINIAPP_TOKEN")
            return await event.eor(
                "**Failed to rotate the Mini App token.** Check logs."
            )
        return await event.eor(
            "**Mini App token rotated.** Old dashboard links stop working — "
            f"send `{HNDLR}miniapp` for a fresh one."
        )

    base_url = (udB.get_key(MINIAPP_URL_KEY) or "").strip()
    if not base_url:
        return await event.eor(
            "**Mini App settings dashboard isn't configured.**\n\n"
            f"1. Set a public HTTPS base URL that reaches this bot:\n"
            f"`{HNDLR}setdb {MINIAPP_URL_KEY} https://your-public-host`\n"
            "2. Point it (reverse proxy/tunnel) at this bot's Mini App "
            "server — `MINIAPP_BIND`/`MINIAPP_PORT` env vars, default "
            "`0.0.0.0:7861`.\n"
            f"3. Restart the bot, then run `{HNDLR}miniapp` again."
        )

    try:
        token = store.ensure_token()
    except Exception:
        _LOGS.exception("miniapp_cmd: failed to load/generate the Mini App token")
        return await event.eor("**Could not prepare the Mini App token.** Check logs.")

    settings_url = f"{base_url.rstrip('/')}/settings?token={token}"
    try:
        buttons = ikm([("Open Settings ⚙️", "web_app", settings_url)])
    except Exception as exc:
        _LOGS.exception("miniapp_cmd: failed to build web_app markup")
        return await event.eor(f"**Markup error:** `{exc.__class__.__name__}: {exc}`")

    if not event.is_private:
        # Guardrail: the bearer token in `settings_url` must never be posted
        # in a group/channel — deliver it to Saved Messages instead and
        # leave only a generic notice here.
        try:
            await event.client.send_message(
                "me",
                "**Mini App Settings Dashboard**\nTap below to open it.",
                buttons=buttons,
            )
        except RPCError:
            _LOGS.exception(
                "miniapp_cmd: failed to DM the settings link to Saved Messages"
            )
            return await event.eor(
                "**Could not deliver the Mini App link to Saved Messages.** Check logs."
            )
        return await event.eor(
            "`Mini App settings link sent to Saved Messages.`", time=8
        )

    await event.eor(
        "**Mini App Settings Dashboard**\nTap below to open it.", buttons=buttons
    )


@ultroid_cmd(pattern="webappdata$")
async def webappdata_cmd(event):
    """Show the last web_app data captured by the listeners."""
    if not _last_webapp_data:
        return await event.eor("**No web_app data received yet.**")

    lines = ["**Last web_app events:**"]
    for kind, payload in _last_webapp_data.items():
        lines.append(f"• `{kind}`: `{payload}`")
    await event.eor("\n".join(lines))


def _is_webview_data_action(update) -> bool:
    """Match predicate for service messages carrying web_app data."""
    msg = getattr(update, "message", None)
    if not isinstance(msg, types.MessageService):
        return False
    return isinstance(
        msg.action,
        (types.MessageActionWebViewDataSent, types.MessageActionWebViewDataSentMe),
    )


@ultroid_cmd(
    listen=types.UpdateNewMessage,
    listen_func=_is_webview_data_action,
    listen_name="webtools.webapp_data_message",
)
async def on_webapp_data_message(update):
    """Capture web_app data sent back to a chat (MessageActionWebViewDataSent*)."""
    msg = update.message
    action = msg.action
    kind = action.__class__.__name__

    if isinstance(action, types.MessageActionWebViewDataSentMe):
        payload = _format_data(kind, text=action.text, data=action.data)
    else:
        payload = _format_data(kind, text=action.text)

    _last_webapp_data[kind] = payload
    _LOGS.info("webtools: captured %s", payload)

    if LOG_CHANNEL:
        try:
            await ultroid_bot.send_message(LOG_CHANNEL, f"**webtools** {payload}")
        except Exception:
            _LOGS.exception("webtools: failed to forward web_app data to LOG_CHANNEL")


@ultroid_cmd(
    listen=types.UpdateWebViewResultSent,
    listen_name="webtools.webview_result_sent",
)
async def on_webview_result_sent(update):
    """Capture the bot-side ``UpdateWebViewResultSent`` update."""
    kind = update.__class__.__name__
    payload = _format_data(kind, query_id=update.query_id)
    _last_webapp_data[kind] = payload
    _LOGS.info("webtools: captured %s", payload)

    if LOG_CHANNEL:
        try:
            await ultroid_bot.send_message(LOG_CHANNEL, f"**webtools** {payload}")
        except Exception:
            _LOGS.exception(
                "webtools: failed to forward web_view result to LOG_CHANNEL"
            )

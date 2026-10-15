# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE/>.

"""GenAI: one unified class owning the raw Gemini REST access, the chat
history, the model rotation, and every handler of the ``!?`` trigger.

Design: DeltaUniverse/selfbot-py ``modules/genai.py``, rebuilt for the
Ultroid plugin model (Telethon handlers + dB-free in-memory history).
"""

import asyncio
import base64
import collections
import io
import re
import time

from pyUltroid.agent import agent
from pyUltroid.fns import fns

from . import (
    LOGS,
    get_help,
    get_string,
    callback,
    asst_cmd,
    eod,
    ultroid_cmd,
)

__doc__ = get_help("help_genai")


pattern = re.compile(r"^(?:(.+?)\s)?\!\?(?:\s-i)?$", flags=re.DOTALL)

_CLOSE_DATA = b"genai-close"
_MAX_MEDIA_SIZE = 32 * (1024**2)
_PASTE_LIMIT = 768


class GenAI:
    """The unified Google Gemini module.

    Owns the model list (with automatic rotation), the 64-message chat
    history, the lock around generations, and the raw REST call. The
    module-level handlers below only translate Telegram events into
    :meth:`execute` calls.
    """

    name = "Google Gemini"
    cmds = "{query} {infix} {suffix}?"
    desc = {
        "query": "String or <Reply or Quote>",
        "infix": "!?",
        "suffix": "-i (Ignore reply)",
        "?": "Optional",
        "e.g.": "Hello, World! !?",
    }

    def __init__(self):
        self.base_url = "https://generativelanguage.googleapis.com/v1beta"
        self.models = collections.deque(
            ["models/gemini-2.5-flash", "models/gemini-2.5-flash-lite"]
        )
        self.models_loaded = False
        self.data = collections.deque(maxlen=64)
        self.lock = asyncio.Lock()

    # -- lifecycle --------------------------------------------------------

    @property
    def api_key(self):
        return agent().config_field("gemini", "KEY")

    async def on_starting(self):
        """Refresh the model list from the live API (once, lazily)."""
        if self.models_loaded or not self.api_key:
            return
        models = []
        try:
            data = await fns.async_searcher(
                f"{self.base_url}/models",
                headers={"x-goog-api-key": self.api_key},
                re_json=True,
            )
            for model in data.get("models", []):
                if "generateContent" in model.get("supportedGenerationMethods", []):
                    models.append(model["name"])
        except Exception as e:
            LOGS.warning(f"GenAI model refresh failed: {e.__class__.__name__}: {e}")
        if models:
            self.models = collections.deque(models)
        self.models_loaded = True

    async def on_stopping(self):
        """Nothing to close: the REST call rides the shared aiohttp session."""

    async def clear(self):
        async with self.lock:
            self.data.clear()

    # -- the raw model call -----------------------------------------------

    async def gemini(self, attempt: int = 0) -> str:
        if attempt >= len(self.models):
            # The lane is exhausted. That is NOT "rate limited" - every
            # configured model failed the transport call. Force one refresh
            # of the model list (a stale 2-model fallback lane is the usual
            # cause) and retry the fresh lane before giving up honestly.
            self.models_loaded = False
            await self.on_starting()
            if len(self.models) > attempt:
                return await self.gemini(attempt)
            return "**Error**:\n  `All models failed`"

        try:
            data = await fns.async_searcher(
                f"{self.base_url}/{self.models[0]}:generateContent",
                post=True,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                },
                json={
                    "contents": list(self.data),
                    "tools": [{"google_search": {}}],
                },
                re_json=True,
            )
        except Exception:
            self.models.rotate(-1)
            return await self.gemini(attempt + 1)

        try:
            if not data.get("candidates"):
                self.data.pop()
                return "**Error**:\n  `Empty Response`"

            content = data["candidates"][0].get("content")
            if not content or not content.get("parts"):
                self.data.pop()
                return "**Error**:\n  `Empty Parts`"

            parts = content["parts"]
            if not parts or "text" not in parts[0]:
                self.data.pop()
                return "**Error**:\n  `Empty Text`"

            text = parts[0]["text"]
        except Exception as e:
            self.data.pop()
            return f"**{e.__class__.__name__}**:\n  `{e}`"

        self.data.append({"role": "model", "parts": [{"text": text}]})
        return text

    # -- the flow ----------------------------------------------------------

    async def execute(self, event):
        if not self.api_key:
            await eod(
                event,
                "`Set the Gemini key first:` use `.aiconf` or `GEMINI_API_KEY`.",
                time=10,
            )
            return

        query = pattern.match(event.raw_text).group(1)
        ignore_reply = event.raw_text.rstrip().endswith("-i")
        parts = []

        if query:
            msg = await event.reply(f"`{query}`")
            parts.append({"text": query})
        else:
            msg = await event.reply("<code>...</code>")

        reply = await event.get_reply_message()
        if reply and not ignore_reply:
            if not parts:
                parts.append({"text": "Analyze"})
            media_part = await self._media_part(reply, msg)
            if isinstance(media_part, str):
                await msg.edit(media_part)
                return
            if media_part:
                parts.append(media_part)

        if not parts:
            await msg.edit(get_string("genai_no_query"))
            return

        started = time.time()
        async with self.lock:
            self.data.append({"role": "user", "parts": parts})
            res = await self.gemini()
        rtt = fns.time_formatter((time.time() - started) * 1000)

        ikb = [("Close", _CLOSE_DATA.decode())]
        if len(res) > _PASTE_LIMIT:
            try:
                _, paste_id = await fns.get_paste(res, "md")
                res = f"{res[:512]}...\n\n[{rtt}](https://spaceb.in/{paste_id})"
            except Exception as e:
                LOGS.warning(f"GenAI paste failed: {e}")
                res = f"{res[:512]}..."

        await msg.edit(
            f"**{query if query else ''}**\n\n{res}\n\n> **{rtt}**".lstrip(),
            buttons=ikm_build(ikb),
        )

    async def _media_part(self, reply, msg):
        """Base64 inline-data part for a replied media, or an error string."""
        file = reply.file
        if not file:
            return None
        if (file.size or 0) > _MAX_MEDIA_SIZE:
            return get_string("genai_media_too_big")
        mime = (file.mime_type or "image/jpeg").lower().strip()
        if reply.sticker and reply.sticker.is_animated:
            mime = "image/jpeg"
        elif mime.startswith("text"):
            mime = "text/plain"
        if not (
            mime.startswith(("audio", "image", "text", "video"))
            or mime == "application/pdf"
        ):
            return get_string("genai_media_bad_mime").format(mime)
        try:
            buffer = await event_download(reply, file=io.BytesIO)
        except Exception as e:
            LOGS.warning(f"GenAI media download failed: {e}")
            return get_string("genai_media_download_failed")
        return {
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
            }
        }


async def event_download(message, file=io.BytesIO):
    return await message.client.download_media(message.media, file=file)


def ikm_build(rows):
    from custom.tg import ikm

    return ikm(rows)


genai = GenAI()


# -- handlers --------------------------------------------------------------
#
# The outgoing ``!?`` trigger used to live here too; it moved to
# ``plugins/genai_module.py`` (the Module-layer twin, driven by the loader's
# Module driver). Keeping both would double-answer every ``!?`` message.


@asst_cmd(pattern="genai( (.*)|$)")
async def on_message_bot(event):
    arg = (event.pattern_match.group(2) or "").strip()
    if arg == "clear":
        await genai.clear()
        return await event.reply(get_string("genai_cleared"))
    await event.reply(get_string("genai_bot_help"))


@ultroid_cmd(pattern="cleargenai$")
async def on_clear_cmd(event):
    await genai.clear()
    await event.delete()
    await event.respond(get_string("genai_cleared"))


@callback(re.compile(rb"^genai-close$"), owner=True)
async def on_close(event):
    await event.delete()

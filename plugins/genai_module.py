# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""Gemini on the Module layer - the same feature as ``plugins/genai.py``,
declared as a class whose handlers are methods.

Why this file exists next to ``genai.py``:

* ``genai.py`` is the decorator plugin: the assistant ``genai`` command,
  ``cleargenai`` and the close button. Its outgoing ``!?`` handler is gone -
  this module owns that trigger now, so the two can never double-answer.
* This file is the **Module** form: one class, handlers discovered from the
  ``on_`` prefix and tagged with ``@handler``, a declared lifecycle, and the
  framework helpers bound as methods through
  :class:`~pyUltroid._misc.methods.ModuleMixin`.

The feature (Gemini REST call, 64-message history, model rotation, media
inlining, the ``!?`` trigger) is the port of ``re-caligo``'s
``selfbot/modules/genai.py``; the difference is the declaration model.

Lifecycle: the **Module driver** owns it (W3-B). The Ultroid loader calls
``ModuleRegistry.adopt`` on this file right after importing it - the class
is discovered, loaded, bound to the live clients and reported loudly. This
file wires nothing at import time; ``ready()`` stays as an idempotent
backstop for first use.
"""

import asyncio
import base64
import collections
import io
import re
import time

from telethon import events

from pyUltroid._misc.listener import handler
from pyUltroid._misc.module import BoundModule

from . import get_help, get_string

__doc__ = get_help("help_genai_module")

#: ``[!]query !?`` / ``query !? -i`` / bare ``!?``
TRIGGER = re.compile(r"^(?:(.+?)\s)?\!\?(?:\s-i)?$", flags=re.DOTALL)

#: ``!clear`` on the user account
CLEAR = re.compile(r"^!clear$")

BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

HISTORY_LIMIT = 64
MAX_MEDIA_SIZE = 32 * (1024**2)
PASTE_LIMIT = 768
CLOSE_DATA = b"gemini-module-close"

FALLBACK_MODELS = (
    "models/gemini-2.5-flash",
    "models/gemini-2.5-flash-lite",
)

#: mime prefixes Gemini accepts inline, plus the one exact type
ACCEPTED_PREFIXES = ("audio", "image", "text", "video")
ACCEPTED_EXACT = ("application/pdf",)

NO_KEY = "`Set the Gemini key first:` use `.aiconf` or `GEMINI_API_KEY`."


class GeminiModule(BoundModule):
    """Google Gemini, declared as a Module.

    ``on_starting`` builds the model deque, ``on_stopping`` closes the HTTP
    session, and the two ``on_*`` handlers are discovered and bound by the
    registry - nothing here touches a client at import time.
    """

    name = "Google Gemini (Module)"
    cmds = "{query} !? {suffix}?"
    desc = {
        "query": "String or <Reply or Quote>",
        "infix": "!?",
        "suffix": "-i (Ignore reply)",
        "?": "Optional",
        "e.g.": "Hello, World! !?",
    }

    def __init__(self, client=None, registry=None, api_key=None, **options):
        super().__init__(client, registry, **options)
        self.api_key = api_key
        self.base_url = BASE_URL
        self.models = collections.deque(FALLBACK_MODELS)
        self.models_loaded = False
        self.history = collections.deque(maxlen=HISTORY_LIMIT)
        self.lock = asyncio.Lock()
        self.http = None

    # -- lifecycle --------------------------------------------------------

    async def on_starting(self) -> None:
        """Refresh the model list once, when a key is configured."""
        if self.models_loaded or not self.api_key:
            self.models_loaded = True
            return
        found: list[str] = []
        try:
            async with self._session() as session:
                async with session.get(
                    f"{self.base_url}/models",
                    headers={"x-goog-api-key": self.api_key},
                ) as response:
                    payload = await response.json()
            for model in payload.get("models", []):
                if "generateContent" in model.get("supportedGenerationMethods", []):
                    name = model.get("name")
                    if name:
                        found.append(name)
        except Exception as exc:
            self.logger.warning("model refresh failed: %s", type(exc).__name__)
        if found:
            self.models = collections.deque(found)
        self.models_loaded = True

    async def on_stopping(self) -> None:
        """Close the HTTP session and drop the in-memory history."""
        session, self.http = self.http, None
        if session is not None:
            try:
                await session.close()
            except Exception:
                pass
        self.history.clear()

    # -- the model call ---------------------------------------------------

    async def ask(self, attempt: int = 0) -> str:
        """Generate a reply, rotating models on a transport failure.

        Never raises: a provider failure becomes a one-line answer rather
        than leaving the user staring at a spinner.
        """
        if attempt >= len(self.models):
            # The lane is exhausted. That is NOT "rate limited" - every
            # configured model failed the transport call. Force one refresh
            # of the model list (a stale 2-model fallback lane is the usual
            # cause) and retry the fresh lane before giving up honestly.
            self.models_loaded = False
            await self.on_starting()
            if len(self.models) > attempt:
                return await self.ask(attempt)
            return "**Error**:\n  `All models failed`"

        try:
            async with self._session() as session:
                async with session.post(
                    f"{self.base_url}/{self.models[0]}:generateContent",
                    json={
                        "contents": list(self.history),
                        "tools": [{"google_search": {}}],
                    },
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
        except Exception:
            self.models.rotate(-1)
            return await self.ask(attempt + 1)

        text = first_text(payload)
        if text is None:
            self.history.pop()
            return "**Error**:\n  `Empty Response`"

        self.history.append({"role": "model", "parts": [{"text": text}]})
        return text

    # -- handlers ---------------------------------------------------------

    @handler(events.NewMessage(outgoing=True, pattern=TRIGGER), 1)
    async def on_message_out(self, event) -> None:
        """The ``!?`` trigger on the user account."""
        await self.ready()
        await self.run(event)

    @handler(events.NewMessage(outgoing=True, pattern=CLEAR), 2)
    async def on_cleared(self, event) -> None:
        """``!clear`` on the user account resets the conversation."""
        async with self.lock:
            self.history.clear()
        await self.respond(event, get_string("genai_cleared"))

    async def ready(self) -> None:
        """Run the lifecycle once, on first real use.

        Explicit rather than implicit: the registry owns the ordering, and
        this only asks it to.
        """
        if self.registry is not None:
            await self.registry.ensure_started(self)

    # -- the flow ---------------------------------------------------------

    async def run(self, event) -> None:
        """Echo the query, gather the parts, generate, edit the message."""
        if not self.api_key:
            await self.eod(event, NO_KEY, time=10)
            return

        matched = TRIGGER.match(event.raw_text)
        query = matched.group(1) if matched else None
        ignore_reply = event.raw_text.rstrip().endswith("-i")

        parts: list[dict] = []
        if query:
            message = await event.reply(f"`{query}`")
            parts.append({"text": query})
        else:
            message = await event.reply("<code>...</code>")

        reply = await event.get_reply_message()
        if reply and not ignore_reply:
            if not parts:
                parts.append({"text": "Analyze"})
            outcome = await self.media_part(reply)
            if isinstance(outcome, str):
                await message.edit(outcome)
                return
            if outcome:
                parts.append(outcome)

        if not parts:
            await message.edit(get_string("genai_no_query"))
            return

        async with self.lock:
            self.history.append({"role": "user", "parts": parts})
            answer = await self.ask()
        timing = self.time_formatter(elapsed_ms(event))

        if len(answer) > PASTE_LIMIT:
            answer = f"{answer[:512]}..."

        await message.edit(
            f"**{query or ''}**\n\n{answer}\n\n> **{timing}**".lstrip(),
            buttons=self.ikm([("Close", CLOSE_DATA.decode())]),
        )

    async def media_part(self, reply) -> dict | str | None:
        """Inline-data part for a replied media, or the message to show."""
        media = reply.file
        if not media:
            return None
        if (media.size or 0) > MAX_MEDIA_SIZE:
            return get_string("genai_media_too_big")

        mime = (media.mime_type or "image/jpeg").lower().strip()
        if reply.sticker and reply.sticker.is_animated:
            mime = "image/jpeg"
        elif mime.startswith("text"):
            mime = "text/plain"
        if not (mime.startswith(ACCEPTED_PREFIXES) or mime in ACCEPTED_EXACT):
            return get_string("genai_media_bad_mime").format(mime)

        try:
            buffer = await reply.client.download_media(reply.media, file=io.BytesIO())
        except Exception as exc:
            self.logger.warning("media download failed: %s", type(exc).__name__)
            return get_string("genai_media_download_failed")
        return {
            "inline_data": {
                "mime_type": mime,
                "data": base64.b64encode(buffer.getvalue()).decode("ascii"),
            }
        }


def first_text(payload: dict) -> str | None:
    """Pull the first text part out of a Gemini response, or ``None``."""
    try:
        candidates = payload.get("candidates") or []
        if not candidates:
            return None
        content = candidates[0].get("content") or {}
        parts = content.get("parts") or []
        if not parts:
            return None
        return parts[0].get("text")
    except Exception:
        return None


def elapsed_ms(event) -> float:
    """Milliseconds between the event and now, defensively."""
    try:
        return (time.time() - event.date.timestamp()) * 1000
    except Exception:
        return 0.0


__all__ = (
    "CLOSE_DATA",
    "GeminiModule",
    "TRIGGER",
    "elapsed_ms",
    "first_text",
)
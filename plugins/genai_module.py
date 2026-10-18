"""Module lifecycle adapter for the shared GenAI agent runtime."""

import re
from typing import ClassVar

from telethon import events

from pyUltroid._core.richmsg import client_can_send_rich_message
from pyUltroid._misc.listener import handler
from pyUltroid._misc.module import BoundModule
from pyUltroid.agent import AgentRequest, agent
from pyUltroid.agent import controls as ai_controls
from pyUltroid.agent._delivery import DeliveryStrategy
from pyUltroid.agent._telegram import text_of

from . import get_string

TRIGGER = re.compile(r"^(?:(.+?)\s)?\!\?(?:\s-i)?$", flags=re.DOTALL)
CLEAR = re.compile(r"^!clear$")
CLOSE_DATA = b"genai-close"


class GenAIModule(BoundModule):
    """Lifecycle-bound handlers sharing state and runtime with GenAI."""

    name = "GenAI (Module)"
    cmds = "{query} !? {suffix}?"
    desc: ClassVar[dict[str, str]] = {
        "query": "String or reply text",
        "infix": "!?",
        "suffix": "-i (ignore reply)",
    }

    @staticmethod
    def history_key(chat_id, user_id):
        """Return the same caller-scoped key as the decorator adapter."""
        return f"genai:{chat_id}:{user_id if user_id is not None else 'unknown'}"

    @handler(events.NewMessage(outgoing=True, pattern=TRIGGER), 1)
    async def on_message_out(self, event):
        """Run one outgoing trigger using the centrally authorized runtime."""
        match = TRIGGER.match(event.raw_text)
        if match is None:
            return
        prompt = (match.group(1) or "").strip()
        reply = (
            None
            if event.raw_text.rstrip().endswith("-i")
            else await event.get_reply_message()
        )
        if reply and getattr(reply, "media", None):
            return await DeliveryStrategy(event).reply(
                "This agent runtime cannot interpret attached media yet. Please send the relevant text."
            )
        if reply:
            context = text_of(reply).strip()
            if context:
                prompt = (
                    f"Context from the replied message:\n{context}\n\n{prompt}".strip()
                )
        if not prompt:
            return await DeliveryStrategy(event).reply(get_string("genai_no_query"))
        delivery = DeliveryStrategy(event)
        status = await delivery.placeholder()
        controls = ai_controls.get_controls(event.chat_id)
        response = await agent().runtime_run(
            AgentRequest(
                prompt=prompt,
                chat_id=self.history_key(
                    event.chat_id, getattr(event, "sender_id", None)
                ),
                origin="genai",
                user_id=getattr(event, "sender_id", None),
                is_private=bool(getattr(event, "is_private", False)),
                use_tools=bool(controls["tools"]),
                max_rounds=controls["rounds"],
                model=controls["model"],
                temperature=controls["temp"],
                rich_messages_allowed=await client_can_send_rich_message(
                    getattr(event, "client", None)
                ),
                timeout_seconds=105.0,
            )
        )
        await delivery.render(status, response)
        return response

    @handler(events.NewMessage(outgoing=True, pattern=CLEAR), 2)
    async def on_cleared(self, event):
        """Reset the exact conversation used by this caller's trigger."""
        agent().clear_history(
            self.history_key(event.chat_id, getattr(event, "sender_id", None))
        )
        await self.respond(event, get_string("genai_cleared"))


# Historical public class alias retained for import compatibility.
GeminiModule = GenAIModule
__all__ = ("CLOSE_DATA", "TRIGGER", "GeminiModule", "GenAIModule")

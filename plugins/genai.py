"""GenAI command adapters backed by the shared configurable agent runtime."""

import re
from typing import ClassVar

from pyUltroid._core.richmsg import client_can_send_rich_message
from pyUltroid.agent import AgentRequest, agent
from pyUltroid.agent import controls as ai_controls
from pyUltroid.agent._delivery import DeliveryStrategy
from pyUltroid.agent._telegram import text_of

from . import asst_cmd, callback, get_string, ultroid_cmd

__doc__ = get_string("help_genai")
pattern = re.compile(r"^(?:(.+?)\s)?\!\?(?:\s-i)?$", flags=re.DOTALL)
_CLOSE_DATA = b"genai-close"


class GenAI:
    """Translate the legacy GenAI triggers into shared runtime requests."""

    name = "GenAI"
    cmds = "{query} {infix} {suffix}?"
    desc: ClassVar[dict[str, str]] = {
        "query": "String or reply text",
        "infix": "!?",
        "suffix": "-i (ignore reply)",
    }

    @staticmethod
    def history_key(chat_id, user_id):
        """Return a stable conversation key scoped to chat and caller."""
        return f"genai:{chat_id}:{user_id if user_id is not None else 'unknown'}"

    async def clear(self, chat_id=None, user_id=None):
        """Clear the same shared history used for this caller's GenAI turns."""
        if chat_id is None:
            return
        agent().clear_history(self.history_key(chat_id, user_id))

    async def execute(self, event, prompt=None):
        """Run a GenAI turn through the configured agent and delivery engine."""
        match = pattern.match(getattr(event, "raw_text", ""))
        if prompt is None and not match:
            return
        query = (prompt if prompt is not None else (match.group(1) or "")).strip()
        ignore_reply = prompt is None and event.raw_text.rstrip().endswith("-i")
        reply = await event.get_reply_message() if not ignore_reply else None
        if reply is not None:
            # Never claim to analyze media without a provider-neutral media API.
            if getattr(reply, "media", None):
                return await DeliveryStrategy(event).reply(
                    "This agent runtime cannot interpret attached media yet. Please send the relevant text."
                )
            context = text_of(reply).strip()
            if context:
                query = (
                    f"Context from the replied message:\n{context}\n\n{query}".strip()
                )
        if not query:
            return await DeliveryStrategy(event).reply(get_string("genai_no_query"))
        delivery = DeliveryStrategy(event)
        status = await delivery.placeholder()
        controls = ai_controls.get_controls(event.chat_id)
        response = await agent().runtime_run(
            AgentRequest(
                prompt=query,
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


genai = GenAI()


@asst_cmd(pattern="genai( (.*)|$)", owner=True)
async def on_message_bot(event):
    arg = (event.pattern_match.group(2) or "").strip()
    if arg == "clear":
        await genai.clear(event.chat_id, getattr(event, "sender_id", None))
        return await event.reply(get_string("genai_cleared"))
    # The assistant command has no suffix trigger; the full argument is prompt.
    return await genai.execute(event, prompt=arg)


@ultroid_cmd(pattern="cleargenai$")
async def on_clear_cmd(event):
    await genai.clear(event.chat_id, getattr(event, "sender_id", None))
    await event.delete()
    await event.respond(get_string("genai_cleared"))


@callback(re.compile(rb"^genai-close$"), owner=True)
async def on_close(event):
    await event.delete()

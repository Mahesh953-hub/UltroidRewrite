"""**Ultroid AI Brain**

Direct AI commands:
   .ai <prompt>
        one-shot AI response (best available provider)
   .ask <prompt>
        alias for .ai
   .aip <provider> <msg>
        force a specific registered provider
   .ais <prompt>
        streaming response (token by token)
   .ailocal <prompt>
        force Ollama (local LLM only)

Agent mode (tools enabled):
   .agent <prompt>
        full agentic loop with tool access
   .do <prompt>
        alias for .agent (shorter)

Conversation:
   .aichat
        toggle chatbot mode (auto-respond in chat)
   .aireset
        clear conversation history for this chat
   .aisys <prompt>
        set custom system prompt
   .aisysreset
        reset system prompt to default

Local LLM (Ollama):
   .ollama models
        list available local models
   .ollama pull <model>
        pull a model from Ollama registry
   .ollama use <model>
        set default local model
   .ollama status
        check if Ollama is running

Per-chat agentic controls:
   .aitools on/off
        enable or disable tool use for this chat
   .airounds <n>
        cap the agent's tool loop for this chat
   .aitemp <x>
        sampling temperature for this chat
   .aimodel <name>/reset
        pin or clear a model override for this chat
   .aiconf [provider <name>/reset]
        effective AI configuration, or pin the default provider

Status / config:
   .aistatus
        show all provider status + cache stats
   .aiverify [provider]
        safely verify configured provider credentials
   .aiflush
        flush response cache
   .aiproviders
        list registered providers

Auto-chatbot mode:
   When enabled (.aichat on), the bot responds to all messages in that chat automatically using the full agent.
"""


import asyncio
import html
import logging
import time
from io import BytesIO

from telethon import events
from telethon.errors import BadRequestError, RPCError

from pyUltroid._core.richmsg import (
    blocks_to_plain_text,
    client_can_send_rich_message,
    edit_rich,
    send_rich,
)
from pyUltroid.agent import (
    AgentRequest,
    AIMessage,
    ApimixProvider,
    CloudflareProvider,
    MetaLlamaProvider,
    XAIProvider,
    agent,
    classify_intent,
    register_lifecycle,
    report_run,
    skill_names,
)
from pyUltroid.agent import controls as ai_controls
from pyUltroid.agent._telegram import public_text, rich_output

from . import (
    eod,
    eor,
    udB,
    ultroid_cmd,
)

__requires__ = []
__optional__ = ["openai", "anthropic", "google-genai", "groq", "aiohttp"]
__res_tags__ = ["ai", "brain", "agent"]
AGENT_COMMAND_TIMEOUT_SECONDS = 105.0

register_lifecycle()
agent().provider_register(XAIProvider())
agent().provider_register(ApimixProvider())
agent().provider_register(MetaLlamaProvider())
agent().provider_register(CloudflareProvider())

_PROVIDER_ALIASES = {
    "grok": "xai",
    "google": "gemini",
    "claude": "anthropic",
    "meta": "meta_llama",
    "llama": "meta_llama",
    "apmix": "apimix",
    "custom": "openai_compat",
    "compat": "openai_compat",
}
_PROVIDER_LABELS = {
    "openai": "OpenAI",
    "gemini": "Gemini",
    "groq": "Groq",
    "xai": "xAI / Grok",
    "anthropic": "Anthropic / Claude",
    "meta_llama": "Meta / Llama",
    "apimix": "apmix.ai",
    "openai_compat": "Custom compatible",
    "ollama": "Ollama",
    "cloudflare": "Cloudflare Workers AI",
}


#    Chatbot mode state
# Stored in DB: "AI_CHATBOT_CHATS"  set of chat_ids


def _chatbot_chats() -> set:
    raw = udB.get_key("AI_CHATBOT_CHATS") or []
    return {int(chat_id) for chat_id in raw if str(chat_id).lstrip("-").isdigit()}


def _set_chatbot_chats(chats: set) -> None:
    udB.set_key("AI_CHATBOT_CHATS", sorted(int(chat_id) for chat_id in chats))


#    Helpers


def _public_ai_text(value) -> str:
    """Return plain user-facing text, excluding serialized tool protocol data."""
    return public_text(value)


def _provider_name(name: str) -> str:
    """Resolve documented shorthand without hiding registered provider names."""
    return _PROVIDER_ALIASES.get(name.lower(), name.lower())


def _provider_label(name: str) -> str:
    return _PROVIDER_LABELS.get(name, name.replace("_", " ").title())


def _provider_options() -> str:
    """Return names from the live registry, including plugin-registered ones."""
    return ", ".join(agent().provider_health()) or "none"


def _provider_details(name: str) -> str:
    """Compact, public model metadata for status output."""
    provider = agent().provider_by_name(name)
    if provider is None:
        return ""
    try:
        caps = provider.model_capabilities(provider.default_model)
    except Exception:
        caps = {}
    features = []
    if caps.get("tools"):
        features.append("tools")
    if caps.get("stream"):
        features.append("stream")
    return f" - {provider.default_model}" + (
        f" - {', '.join(features)}" if features else ""
    )


def _plain_ai_text(value: str) -> str:
    """Render agent markup as readable text for senders without native rich support."""
    return blocks_to_plain_text(rich_output(value))


async def _edit_ai_output(event, msg, text: str, rich_allowed: bool) -> None:
    """Edit the placeholder using only the format supported by its sender."""
    if rich_allowed:
        try:
            from pyUltroid.agent._presentation import native_answer
            await edit_rich(event.client, event.chat_id, msg, native_answer(text))
        except (BadRequestError, RPCError) as exc:
            if exc.message != "RICH_MESSAGE_UNSUPPORTED":
                raise
            await msg.edit(_plain_ai_text(text), parse_mode=None)
    else:
        await msg.edit(_plain_ai_text(text), parse_mode=None)


async def _send_ai_response(
    event, msg, response, prefix: str = "", rich_allowed: bool | None = None
) -> None:
    """Deliver a private-safe answer using the sender's supported message format."""
    if rich_allowed is None:
        rich_allowed = await client_can_send_rich_message(event.client)
    if getattr(response, "error", None):
        if (
            getattr(response, "provider", None) == "runtime"
            and getattr(response, "model", None) in {"timeout", "error"}
            and getattr(response, "content", None)
        ):
            text = _public_ai_text(response.content)
        else:
            text = "Sorry, the AI service couldn't complete that request. Please try again."
    else:
        text = _public_ai_text(response.content)
        if not text:
            text = "Sorry, I couldn't produce a user-facing answer. Please try again."
    text = _public_ai_text(f"{prefix}{text}")

    # If agent used process_image, send the image too
    image_result = getattr(response, "image_result", None)
    if not getattr(response, "error", None) and image_result and image_result.ok:
        buf = image_result.to_bytesio("PNG", "ai_image.png")
        if buf:
            await msg.delete()
            await event.client.send_file(
                event.chat_id,
                buf,
                caption=None,
                reply_to=event.id,
                parse_mode=None,
            )
            if len(text) <= 4000 and rich_allowed:
                await send_rich(
                    event.client,
                    event.chat_id,
                    rich_output(text),
                    reply_to=event.id,
                )
            elif len(text) <= 4000:
                await event.client.send_message(
                    event.chat_id,
                    _plain_ai_text(text),
                    reply_to=event.id,
                    parse_mode=None,
                )
            else:
                file = BytesIO(text.encode("utf-8"))
                file.name = "response.txt"
                await event.client.send_file(
                    event.chat_id, file, reply_to=event.id, parse_mode=None
                )
            return

    if len(text) > 4000:
        await msg.delete()
        buf = BytesIO(text.encode())
        buf.name = "response.txt"
        await event.client.send_file(
            event.chat_id,
            buf,
            caption=None,
            reply_to=event.id,
            parse_mode=None,
        )
    else:
        await _edit_ai_output(event, msg, text, rich_allowed)


#    .ai / .ask   one-shot


@ultroid_cmd(pattern=r"(ai|ask)(?: |$)([\s\S]*)", silent_denials=False)
async def ai_cmd(event):
    """
    .ai <prompt>
    Ask the AI anything. Uses best available provider with history.
    """
    prompt = event.pattern_match.group(2).strip()
    if not prompt:
        # Check if replying to a message
        reply = await event.get_reply_message()
        if reply and reply.text:
            prompt = reply.text.strip()
    if not prompt:
        return await eod(event, "Usage: .ai <your prompt>", parse_mode="html")

    msg = await eor(event, "Thinking...", parse_mode="html")
    rich_allowed = await client_can_send_rich_message(event.client)
    t0 = time.time()
    response = await agent().runtime_run(
        AgentRequest(
            prompt=prompt,
            chat_id=event.chat_id,
            origin=".ai",
            user_id=getattr(event, "sender_id", None),
            is_private=bool(getattr(event, "is_private", False)),
            rich_messages_allowed=rich_allowed,
        )
    )
    report_run(
        origin=".ai",
        chat_id=event.chat_id,
        user=getattr(getattr(event, "sender", None), "username", None),
        provider=response.provider,
        model=response.model,
        prompt=prompt,
        tools_used=None,
        duration=time.time() - t0,
        ok=response.ok,
        error=getattr(response, "error", None),
    )
    await _send_ai_response(event, msg, response, rich_allowed=rich_allowed)


#    .aip   force provider


@ultroid_cmd(pattern=r"aip(?: |$)(\w+)(?: |$)([\s\S]*)", silent_denials=False)
async def aip_cmd(event):
    """
    .aip <provider> <prompt>
    Force a registered provider. Use .aiproviders to see live names.
    """
    requested = event.pattern_match.group(1).strip().lower()
    provider = _provider_name(requested)
    prompt = event.pattern_match.group(2).strip()
    if not prompt:
        return await eod(event, "Usage: .aip <provider> <prompt>", parse_mode="html")

    p = agent().provider_by_name(provider)
    if p is None:
        return await eod(
            event,
            f"Unknown provider {requested}. Options: {_provider_options()}",
            parse_mode="html",
        )
    if not p.is_configured():
        return await eod(
            event,
            f"Provider {provider} is not configured. Configure it, then run .aiverify {provider}.",
            parse_mode="html",
        )

    msg = await eor(
        event, f"Asking {_provider_label(provider)}...", parse_mode="html"
    )
    rich_allowed = await client_can_send_rich_message(event.client)
    response = await agent().runtime_run(
        AgentRequest(
            prompt=prompt,
            chat_id=event.chat_id,
            origin=".aip",
            user_id=getattr(event, "sender_id", None),
            is_private=bool(getattr(event, "is_private", False)),
            provider=provider,
            use_tools=False,
            use_cache=True,
            rich_messages_allowed=rich_allowed,
        )
    )
    await _send_ai_response(event, msg, response, rich_allowed=rich_allowed)


#    .ais   streaming


@ultroid_cmd(pattern=r"ais(?: |$)([\s\S]*)", silent_denials=False)
async def ais_cmd(event):
    """
    .ais <prompt>
    Streaming AI response   updates the message token by token.
    """
    prompt = event.pattern_match.group(1).strip()
    if not prompt:
        return await eod(event, "Usage: .ais <prompt>", parse_mode="html")

    msg = await eor(event, "Thinking...", parse_mode="html")
    rich_allowed = await client_can_send_rich_message(event.client)
    buf = ""
    last = time.time()
    UPDATE_INTERVAL = 1.8

    # Streaming is normalized by the same runtime as non-streaming calls.
    async for chunk in agent().runtime_stream(
        AgentRequest(
            prompt=prompt,
            chat_id=event.chat_id,
            origin=".ais",
            user_id=getattr(event, "sender_id", None),
            is_private=bool(getattr(event, "is_private", False)),
            rich_messages_allowed=rich_allowed,
        )
    ):
        buf += chunk
        now = time.time()
        if now - last >= UPDATE_INTERVAL:
            try:
                visible = _public_ai_text(buf)
                if visible:
                    await msg.edit(html.escape(visible[-3500:]), parse_mode="html")
                last = now
            except Exception:
                pass

    final = _public_ai_text(buf) or (
        "Sorry, I couldn't produce a user-facing answer. Please try again."
    )
    if len(final) > 4000:
        await msg.delete()
        b = BytesIO(final.encode())
        b.name = "response.txt"
        await event.client.send_file(
            event.chat_id, b, reply_to=event.id, parse_mode=None
        )
    else:
        await _edit_ai_output(event, msg, final, rich_allowed)


#    .ailocal   Ollama only


@ultroid_cmd(pattern=r"ailocal(?: |$)([\s\S]*)", silent_denials=False)
async def ailocal_cmd(event):
    """
    .ailocal <prompt>
    Force local LLM via Ollama. Zero API cost.
    """
    prompt = event.pattern_match.group(1).strip()
    if not prompt:
        return await eod(event, "Usage: .ailocal <prompt>", parse_mode="html")

    msg = await eor(event, "Asking the local model...", parse_mode="html")
    rich_allowed = await client_can_send_rich_message(event.client)
    if not await agent().provider_by_name('ollama')._is_running():
        return await _edit_ai_output(
            event,
            msg,
            "The local model service is not running.\n"
            "Start it with: ollama serve\n"
            "Or pull a model first: .ollama pull llama3",
            rich_allowed,
        )

    controls = ai_controls.get_controls(event.chat_id)
    response = await agent().runtime_run(
        AgentRequest(
            prompt=prompt,
            chat_id=event.chat_id,
            origin=".ailocal",
            user_id=getattr(event, "sender_id", None),
            is_private=bool(getattr(event, "is_private", False)),
            provider="ollama",
            use_tools=False,
            rich_messages_allowed=rich_allowed,
        )
    )
    await _send_ai_response(event, msg, response, rich_allowed=rich_allowed)


#    .agent / .do   full agentic mode


@ultroid_cmd(pattern=r"(agent|do)(?: |$)([\s\S]*)", owner_only=True, silent_denials=False)
async def agent_cmd(event):
    """
    .agent <prompt>
    Full agentic loop   AI has access to all Ultroid systems:
    shell, code search, traces, updates, resources, images, Telegram.
    """
    prompt = event.pattern_match.group(2).strip()
    if not prompt:
        return await eod(
            event,
            ".agent <what you want done>\n\n"
            "The agent can: run shell commands, search code, read traces, "
            "check updates, install packages, process images, search the web.",
            parse_mode="html",
        )

    # One placeholder avoids a stream of Telegram edits (and FloodWaits). The
    # runtime enforces a total deadline across lock wait, provider calls and tools.
    msg = await eor(event, "Working...", parse_mode="html")
    rich_allowed = await client_can_send_rich_message(event.client)
    t0 = time.time()
    try:
        response = await agent().runtime_run(
            AgentRequest(
                prompt=prompt,
                chat_id=event.chat_id,
                origin=".agent",
                user_id=getattr(event, "sender_id", None),
                is_private=bool(getattr(event, "is_private", False)),
                use_cache=False,
                rich_messages_allowed=rich_allowed,
                timeout_seconds=AGENT_COMMAND_TIMEOUT_SECONDS,
            )
        )
    except asyncio.CancelledError:
        # A bot shutdown or upstream cancellation must cancel provider/tool
        # awaits and release the per-chat runtime lock; don't turn it into success.
        try:
            await asyncio.wait_for(
                _edit_ai_output(event, msg, "Agent request cancelled.", rich_allowed),
                timeout=2,
            )
        except BaseException:
            pass
        raise
    except Exception as exc:
        # Keep raw provider/tool exception text (which can contain request data)
        # out of Telegram and logs; record only the exception class for diagnosis.
        logging.getLogger(__name__).warning(
            "[AGENT:COMMAND] execution failed (%s)", type(exc).__name__
        )
        return await _edit_ai_output(
            event,
            msg,
            "The agent couldn't complete this request due to an internal error. Please try again.",
            rich_allowed,
        )

    report_run(
        origin=".agent",
        chat_id=event.chat_id,
        user=getattr(getattr(event, "sender", None), "username", None),
        provider=response.provider,
        model=response.model,
        prompt=prompt,
        tools_used=list(getattr(response, "tools_used", None) or []),
        duration=time.time() - t0,
        ok=response.ok,
        error=getattr(response, "error", None),
    )
    await _send_ai_response(event, msg, response, rich_allowed=rich_allowed)


#    .aichat   chatbot mode toggle


#    .aichat listener   actually consumes the enabled-chat state


@ultroid_cmd(
    listen=events.NewMessage(incoming=True, func=lambda e: not e.out),
    listen_func=lambda e: e.chat_id in _chatbot_chats() and bool((e.text or "").strip()),
    listen_name="ai_chatbot",
)
async def _ai_chatbot_listener(event):
    prompt = (event.text or "").strip()
    if not prompt:
        return
    controls = ai_controls.get_controls(event.chat_id)
    msg = await eor(event, "Thinking...", parse_mode="html")
    rich_allowed = await client_can_send_rich_message(event.client)
    response = await agent().runtime_run(
        AgentRequest(
            prompt=prompt,
            chat_id=event.chat_id,
            origin=".aichat",
            user_id=getattr(event, "sender_id", None),
            is_private=bool(getattr(event, "is_private", False)),
            rich_messages_allowed=rich_allowed,
        )
    )
    await _send_ai_response(event, msg, response, rich_allowed=rich_allowed)


#    .aichat   chatbot mode toggle


@ultroid_cmd(pattern=r"aiskills$", owner_only=True, silent_denials=False)
async def aiskills_cmd(event):
    """.aiskills   show the declarative skills available to the agent."""
    return await eod(
        event,
        "**Agent skills**\n" + "\n".join(f"- {name}" for name in skill_names(".agent")),
    )


@ultroid_cmd(pattern=r"aichat(?: |$)(on|off|$)", owner_only=True, silent_denials=False)
async def aichat_cmd(event):
    """
    .aichat on/off
    Toggle automatic AI responses in this chat.
    """
    arg = (event.pattern_match.group(1) or "").strip().lower()
    chats = _chatbot_chats()
    cid = event.chat_id

    if arg == "on":
        chats.add(cid)
        _set_chatbot_chats(chats)
        return await eod(event, " AI chatbot mode **ON** for this chat.")
    elif arg == "off":
        chats.discard(cid)
        _set_chatbot_chats(chats)
        return await eod(event, " AI chatbot mode **OFF** for this chat.")
    else:
        state = "ON" if cid in chats else "OFF"
        await eod(
            event,
            f" AI chatbot mode is currently **{state}**.\nUse .aichat on or .aichat off.",
        )


@ultroid_cmd(pattern=r"aireset$", silent_denials=False)
async def aireset_cmd(event):
    """
    .aireset
    Clear AI conversation history for this chat.
    """
    agent().clear_history(event.chat_id)
    await eod(event, " AI conversation history cleared.")


#    .aisys   set system prompt


@ultroid_cmd(pattern=r"aisys(?: |$)([\s\S]*)", owner_only=True, silent_denials=False)
async def aisys_cmd(event):
    """
    .aisys <system prompt>
    Set a custom system prompt for the AI. Persists for session.
    """
    prompt = event.pattern_match.group(1).strip()
    if not prompt:
        current = agent().chat_system(event.chat_id) or "(default)"
        return await eor(
            event,
            f"**Current system prompt (first 500 chars):**\n\n{current[:500]}\n\n"
            f"Use .aisys <new prompt> to change it.\n"
            f"Use .aisysreset to restore default.",
        )
    agent().set_chat_system(event.chat_id, prompt)
    await eod(event, f" System prompt updated for this chat ({len(prompt)} chars).")


@ultroid_cmd(pattern=r"aisysreset$", owner_only=True, silent_denials=False)
async def aisysreset_cmd(event):
    agent().reset_chat_system(event.chat_id)
    await eod(event, " Chat system prompt reset.")


#    .ollama   local LLM management


@ultroid_cmd(pattern=r"ollama(?: |$)(.*)", owner_only=True, silent_denials=False)
async def ollama_cmd(event):
    """
    .ollama models           list local models
    .ollama pull <model>     download a model
    .ollama use <model>      set default local model
    .ollama status           Ollama server status
    """
    arg = (event.pattern_match.group(1) or "").strip()
    parts = arg.split(None, 1)
    sub = parts[0].lower() if parts else "status"
    rest = parts[1].strip() if len(parts) > 1 else ""

    #    status
    if sub == "status" or not sub:
        running = await agent().provider_by_name('ollama')._is_running()
        models = await agent().provider_by_name('ollama').list_models() if running else []
        status = " Running" if running else " Not running"
        text = (
            f"** Ollama Status**\n\n"
            f"Server:  {status}\n"
            f"URL:     {agent().provider_by_name('ollama').base_url}\n"
            f"Default: {agent().provider_by_name('ollama').default_model}\n"
            f"Models:  {len(models)}"
        )
        if models:
            text += "\n\n**Available models:**\n" + "\n".join(
                f"    {m}" for m in models[:10]
            )
        return await eor(event, text)

    #    models
    elif sub == "models":
        msg = await eor(event, " fetching models")
        models = await agent().provider_by_name('ollama').list_models()
        if not models:
            return await msg.edit("No models found. Try .ollama pull llama3")
        text = "** Local Models**\n\n" + "\n".join(f"    {m}" for m in models)
        await msg.edit(text)

    #    pull
    elif sub == "pull":
        if not rest:
            return await eod(
                event,
                "Usage: .ollama pull <model_name>\nExample: .ollama pull llama3",
            )

        msg = await eor(event, f" pulling {rest}")
        last = time.time()

        async for status_line in agent().provider_by_name('ollama').pull_model(rest):
            now = time.time()
            if now - last >= 2.0:
                try:
                    await msg.edit(f" pulling {rest}\n{status_line[:100]}")
                    last = now
                except Exception:
                    pass

        models = await agent().provider_by_name('ollama').list_models()
        pulled = rest.split(":")[0] in [m.split(":")[0] for m in models]
        await msg.edit(
            f"{'' if pulled else ''} {rest} "
            f"{'is ready' if pulled else 'pull may have failed   check Ollama logs'}."
        )

    #    use
    elif sub == "use":
        if not rest:
            return await eod(event, "Usage: .ollama use <model_name>")
        agent().provider_by_name('ollama').default_model = rest
        if agent().config_set("ollama", "MODEL", rest):
            await eod(event, f" Default local model set to {rest}.")
        else:
            await eod(
                event,
                " Could not save the model choice; it applies until the next restart.",
            )

    else:
        await eod(
            event,
            "** Ollama Commands**\n\n"
            ".ollama status         server status\n"
            ".ollama models         list local models\n"
            ".ollama pull <name>    download a model\n"
            ".ollama use <name>     set default model",
        )


#    .aistatus   full status


@ultroid_cmd(pattern=r"aistatus$", owner_only=True, silent_denials=False)
async def aistatus_cmd(event):
    """
    .aistatus
    Show all provider status, cache stats, history info.
    """
    msg = await eor(event, "Fetching AI status...", parse_mode="html")
    status = await agent().status()
    providers = status["providers"]

    lines = []
    for name, configured in providers.items():
        state = "configured" if configured else "not configured"
        lines.append(
            f"- **{_provider_label(name)}** (`{name}`) - {state}{_provider_details(name)}"
        )

    text = (
        f"**AI Service Status**\n\n"
        f"**Providers ({status['configured_count']} configured):**\n"
        + "\n".join(lines)
        + f"\n\n**Cache entries:** `{status['cache_entries']}`\n"
        f"**Active histories:** `{status['active_histories']}`\n"
        f"**Max tool rounds:** `{status['max_tool_rounds']}`\n"
        f"**History window:** `{status['max_history']}` messages"
    )
    await msg.edit(text)


#    .aiverify   read-only provider credential checks


@ultroid_cmd(pattern=r"aiverify(?: |$)(\w*)", owner_only=True, silent_denials=False)
async def aiverify_cmd(event):
    """
    .aiverify [provider]
    Run each configured provider's read-only verify() mechanism, or one
    registered provider by name. No generation request is made.
    """
    requested = (event.pattern_match.group(1) or "").strip().lower()
    provider_name = _provider_name(requested) if requested else ""
    if provider_name:
        provider = agent().provider_by_name(provider_name)
        if provider is None:
            return await eod(
                event, f"Unknown provider {requested}. Options: {_provider_options()}"
            )
        targets = [provider]
    else:
        targets = [agent().provider_by_name(name) for name in agent().provider_health()]
        targets = [provider for provider in targets if provider is not None]

    msg = await eor(event, " verifying AI providers")
    lines = []
    for provider in targets:
        configured = provider.is_configured()
        if not configured:
            lines.append(
                f" **{_provider_label(provider.name)}** ({provider.name}): not configured"
            )
            continue
        try:
            # BaseProvider.verify implementations use read-only model, account,
            # or reachability endpoints. Keep failures non-fatal to the command.
            verified = await provider.verify()
        except Exception:
            verified = False
        icon = "" if verified else ""
        detail = "verified" if verified else "verification failed"
        lines.append(
            f"{icon} **{_provider_label(provider.name)}** ({provider.name}): {detail}"
        )

    scope = f" {provider_name}" if provider_name else ""
    await msg.edit(
        f"** AI Provider Verification{scope}**\n\n"
        + "\n".join(lines)
        + "\n\n_Read-only checks only; no prompt or model generation was sent._"
    )


#    .aiflush   flush cache


@ultroid_cmd(pattern=r"aiflush$", owner_only=True, silent_denials=False)
async def aiflush_cmd(event):
    """
    .aiflush
    Flush the AI response cache.
    """
    msg = await eor(event, " flushing cache")
    from pyUltroid.agent import agent
    count = await agent().mini_flush_cache()
    await msg.edit(f" Flushed {count} cached responses.")


#    .aiproviders   list providers


@ultroid_cmd(pattern=r"aiproviders$", silent_denials=False)
async def aiproviders_cmd(event):
    """
    .aiproviders
    List all supported AI providers and their config status.
    """
    status = agent().provider_health()
    lines = []
    for name, configured in status.items():
        icon = "" if configured else ""
        lines.append(
            f"{icon} **{_provider_label(name)}** ({name}){_provider_details(name)}"
        )
    text = "** AI Providers**\n\n" + "\n".join(lines)
    text += "\n\nUse .aip <provider> <prompt> to select one, or .aiverify [provider] for read-only credential checks."
    await eor(event, text)


#    .aiconf   effective configuration


def _mask_key(value):
    if not value:
        return " "
    value = str(value)
    if len(value) <= 8:
        return "    "
    return f"{value[:4]}{value[-4:]}"


@ultroid_cmd(pattern=r"aiconf(?: |$)([\s\S]*)$", silent_denials=False)
async def aiconf_cmd(event):
    """
    .aiconf
    Show the effective AI configuration: every provider, what a request
    would actually use, and which database key holds it.
    .aiconf provider <name> pins the provider for bare .ai commands.
    .aiconf reset clears the pin.
    """
    arg = (event.pattern_match.group(1) or "").strip()
    if arg == "reset":
        agent().config_use_default(None)
        return await eor(
            event, "**AI provider pin cleared.** .ai now uses automatic routing."
        )
    if arg.startswith("provider"):
        name = arg[len("provider") :].strip().lower()
        if not name:
            return await eod(
                event,
                "Usage: .aiconf provider <name>   e.g. .aiconf provider gemini",
            )
        try:
            agent().config_use_default(name, require_configured=True)
        except ValueError as exc:
            return await eod(event, f"{exc}")
        return await eor(
            event,
            f"**.ai now uses {name}.** Use .aiconf to inspect the effective settings.",
        )

    default = agent().config_default_provider()
    status = agent().config_status()
    lines = [f"**Default for .ai:** {default or 'automatic (best available)'}", ""]
    for pid, info in sorted(status.items()):
        icon = "" if info["configured"] else ""
        model = info["fields"].get("MODEL") or "default"
        base = info["fields"].get("BASE")
        key = _mask_key(info["fields"].get("KEY"))
        line = f"{icon} **{info['label']}** ({pid})   model {model}"
        if base:
            line += f"   {base}"
        if key != " ":
            line += f"   key {key}"
        lines.append(line)
    lines += [
        "",
        f"**This chat:** {ai_controls.describe_controls(event.chat_id)}",
        "",
        "Configure providers with /agent in the bot's DM, or .aiconf provider <name> to pin one.",

    ]
    await eor(event, "\n".join(lines))


#    per-chat agentic controls


@ultroid_cmd(pattern=r"aitools(?: |$)(on|off)?$", silent_denials=False)
async def aitools_cmd(event):
    """
    .aitools on/off
    Enable or disable tool use for this chat. Without an argument, show
    the current state.
    """
    arg = (event.pattern_match.group(1) or "").lower()
    if arg in ("on", "off"):
        ai_controls.set_control(event.chat_id, "tools", arg == "on")
    await eor(
        event,
        f"**AI tools for this chat:** {ai_controls.get_controls(event.chat_id)['tools'] and 'on' or 'off'}",
    )


@ultroid_cmd(pattern=r"airounds(?: |$)(\d+|reset)?$", silent_denials=False)
async def airounds_cmd(event):
    """
    .airounds <n> / .airounds reset
    Cap the agent's tool loop for this chat, or restore the default.
    """
    arg = event.pattern_match.group(1) or ""
    if arg == "reset":
        ai_controls.set_control(event.chat_id, "rounds", None)
    elif arg.isdigit():
        ai_controls.set_control(event.chat_id, "rounds", int(arg))
    controls = ai_controls.get_controls(event.chat_id)
    await eor(
        event,
        f"**Tool rounds for this chat:** {controls['rounds'] if controls['rounds'] is not None else 'default'}",
    )


@ultroid_cmd(pattern=r"aitemp(?: |$)([\d.]+|reset)?$", silent_denials=False)
async def aitemp_cmd(event):
    """
    .aitemp <x> / .aitemp reset
    Sampling temperature for this chat (0.0 2.0), or restore the default.
    """
    arg = event.pattern_match.group(1) or ""
    if arg == "reset":
        ai_controls.set_control(event.chat_id, "temp", None)
    else:
        try:
            value = float(arg)
            if not 0.0 <= value <= 2.0:
                raise ValueError
            ai_controls.set_control(event.chat_id, "temp", value)
        except ValueError:
            return await eod(event, "Usage: .aitemp <0.0-2.0> or .aitemp reset")
    await eor(
        event,
        f"**Temperature for this chat:** {ai_controls.get_controls(event.chat_id)['temp']}",
    )


@ultroid_cmd(pattern=r"aimodel(?: |$)([\s\S]*)$", silent_denials=False)
async def aimodel_cmd(event):
    """
    .aimodel <name> / .aimodel reset
    Pin a model override for this chat, or clear it.
    """
    arg = (event.pattern_match.group(1) or "").strip()
    if not arg:
        controls = ai_controls.get_controls(event.chat_id)
        return await eor(
            event, f"**Model for this chat:** {controls['model'] or 'default'}"
        )
    if arg.lower() in ("reset", "clear", "default"):
        arg = None
    ai_controls.set_control(event.chat_id, "model", arg)
    await eor(event, f"**Model for this chat:** {arg or 'default'}")

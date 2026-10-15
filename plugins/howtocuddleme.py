"""
howtocuddleme.py - Developer Template & Feature Showcase

This file demonstrates all modernized features of the SarahUB userbot.
Use it as a reference for writing new plugins or migrating old ones.
"""

import asyncio
from telethon import events
from pyUltroid._misc._decorators import ultroid_cmd
from custom.tg import ikm

# ==========================================
# 1. Custom Client Attributes (No Relative Imports)
# ==========================================
# Instead of doing relative imports, you can write clean code that accesses
# client resources directly on the `client` or `event.client` object:
# - event.client.asst        --> Assistant bot instance
# - event.client.ultroid_bot --> Primary Userbot client
# - event.client.udB         --> Database interface
# - event.client.HNDLR       --> Command handler prefix (e.g., ".")


@ultroid_cmd(pattern="cuddle_attr$")
async def show_attributes(event):
    client = event.client
    hndlr = client.HNDLR
    db_name = client.udB.name
    bot_name = client.ultroid_bot.me.first_name

    await event.reply(
        f"✨ **Modernized Client Attributes**:\n"
        f"• Handler: `{hndlr}`\n"
        f"• Client User: `{bot_name}`\n"
        f"• DB Connection: `{db_name}`"
    )


# ==========================================
# 2. Modern Colorized Buttons with Icons
# ==========================================
# Construct custom markup using `ikm()`. Pass styled keywords ('danger', 'success', 'primary')
# and icon integers to produce modern user interfaces.


@ultroid_cmd(pattern="cuddle_buttons$")
async def show_buttons(event):
    # Construct ReplyInlineMarkup with styles and icons
    markup = ikm(
        [
            [("Danger Action", "cuddle_click_danger", "danger", 99)],
            [("Success Action", "cuddle_click_success", "success", 100)],
            [("Info link", "url", "https://google.com", "primary", 101)],
        ]
    )

    await event.reply("💖 Choose a custom styled action:", buttons=markup)


# ==========================================
# 3. Guest Mode Access-Control Gating
# ==========================================
# Decorate command handlers with `allow_guest=True` to allow guest sessions to run them.
# Standard commands (which default to allow_guest=False) will be automatically rejected.


@ultroid_cmd(pattern="cuddle_guest_allowed$", allow_guest=True)
async def guest_allowed_command(event):
    await event.reply("✅ Welcome! This command is allowed for guest sessions.")


@ultroid_cmd(pattern="cuddle_guest_restricted$", allow_guest=False)
async def guest_restricted_command(event):
    await event.reply("🔒 Secret information for the owner only!")


# ==========================================
# 4. Inline Event Listening (listen)
# ==========================================
# Await the next message or event in a specific chat inline using `client.listen()`.
# Handles timeout logic and unregisters automatically to prevent memory leaks.


@ultroid_cmd(pattern="cuddle_ask$")
async def ask_question_inline(event):
    client = event.client
    chat_id = event.chat_id

    await event.reply(
        "🤔 What is your favorite programming language? (Reply in 15 seconds):"
    )

    try:
        # Listen specifically for the next message from this chat
        response = await client.listen(
            chat_id, events.NewMessage(incoming=True), timeout=15
        )
        await event.reply(f"🎉 Awesome! I love `{response.text}` too!")
    except TimeoutError:
        await event.reply("⏰ Time is up! You didn't respond in time.")


# ==========================================
# 5. Isolated PluginPart Dynamic Registration
# ==========================================
# For advanced modern implementations, you can register handlers directly under a named
# plugin scope. This isolates them and prevents collisions with other plugins.


# Register a custom handler on the 'cuddle_part' namespace
def setup_cuddle_plugin(client):
    plugin_part = client.plugin("cuddle_showcase")

    @plugin_part.on(events.NewMessage(pattern=r"\.cuddle_part$"))
    async def custom_part_handler(event):
        await event.reply(
            "🛠️ Registered safely through isolated `PluginPart` registry!"
        )

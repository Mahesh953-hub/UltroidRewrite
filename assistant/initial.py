# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

import html

from . import *
from ._cbs import cb, cb_pattern
from custom.tg import ikm


STRINGS = {
    1: (
        "<b>Welcome to Ultroid</b>\n\n"
        "Your userbot is online. This short guide covers the first commands, "
        "help, and support."
    ),
    2: (
        "<b>About Ultroid</b>\n\n"
        "Ultroid is a modular Telegram userbot built with Telethon. Its plugins "
        "add tools for messaging, moderation, media, and automation.\n\n"
        'Maintained by <a href="https://t.me/TeamUltroid">TeamUltroid</a>.'
    ),
    3: (
        "<b>Quick links</b>\n\n"
        '<a href="https://t.me/UltroidUpdates/24">Username tracker</a>\n'
        '<a href="https://t.me/UltroidUpdates/28">Custom add-ons</a>\n'
        '<a href="https://t.me/UltroidUpdates/22">Time zone setup</a>\n'
        '<a href="https://t.me/UltroidUpdates/21">Inline PMPermit</a>\n'
        '<a href="https://t.me/UltroidUpdates/18">Dual mode</a>\n'
        '<a href="https://t.me/UltroidUpdates/2">Private-message bot</a>\n'
        '<a href="https://t.me/UltroidUpdates/14">More guides</a>'
    ),
    4: (
        "<b>Find commands</b>\n\n"
        f"<code>{html.escape(HNDLR)}help</code> opens the help menu.\n"
        f"<code>{html.escape(HNDLR)}help &lt;plugin&gt;</code> shows a plugin's "
        "commands.\n"
        f"<code>{html.escape(HNDLR)}cmds</code> opens the complete command index."
    ),
    5: (
        "<b>Need a hand?</b>\n\n"
        "For questions, suggestions, and troubleshooting, visit "
        '<a href="https://t.me/UltroidSupportChat">Ultroid Support</a>.\n\n'
        "You're all set."
    ),
}

PAGE_COUNT = len(STRINGS)
PAGE_PATTERN = cb_pattern("assistant", "initial")


def _page_buttons(current):
    rows = []
    if current > 1:
        rows.append(
            ("Previous", cb("assistant", "initial", "prev", current - 1), "primary")
        )
    if current < PAGE_COUNT:
        rows.append(
            ("Next", cb("assistant", "initial", "next", current + 1), "primary")
        )
    elif current == PAGE_COUNT:
        rows.append(("Back to start", cb("assistant", "initial", "prev", 1), None))
    return ikm([rows], icon=False) if rows else []


async def _render_page(event, current):
    current = max(1, min(int(current), PAGE_COUNT))
    await event.edit(
        STRINGS[current],
        buttons=_page_buttons(current),
        link_preview=False,
        parse_mode="html",
    )


@callback(PAGE_PATTERN)
async def init_depl(event):
    """Render the guide page named by the callback payload."""
    try:
        payload = event.data.decode("utf-8").split(":")
        current = int(payload[-1])
    except (AttributeError, UnicodeDecodeError, ValueError, IndexError):
        return await event.answer("This guide page could not be opened.", alert=True)
    if not 1 <= current <= PAGE_COUNT:
        return await event.answer("This guide page no longer exists.", alert=True)
    await _render_page(event, current)

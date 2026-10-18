# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""Assistant help menu for group administration and utility commands."""

from . import *
from .._cbs import cb, cb_exact_pattern, cb_pattern
from ..rich_pages import edit_callback_page, rich_page
from pyUltroid._core.richmsg import send_rich

HELP_SECTIONS = {
    "Admintools": [
        ("/pin <reply>", "Pin the replied-to message."),
        ("/pinned", "Show the current pinned message."),
        ("/unpin <reply>", "Unpin the replied-to message."),
        ("/unpin all", "Remove all pinned messages."),
        ("/ban <user> [reason]", "Ban a user by reply, ID, or username."),
        ("/unban <user>", "Unban a user."),
        ("/mute <user>", "Mute a user."),
        ("/unmute <user>", "Unmute a user."),
        ("/tban <user> <time>", "Temporarily ban a user."),
        ("/tmute <user> <time>", "Temporarily mute a user."),
        ("/purge <reply>", "Delete messages starting at the replied-to message."),
        ("/setgpic <reply to photo>", "Set the group's profile photo."),
        ("/delgpic", "Remove the current group photo."),
    ],
    "locks": [
        ("/lock <item>", "Restrict a type of content in the current chat."),
        ("/unlock <item>", "Allow a previously restricted type of content."),
        ("msgs", "Messages."),
        ("inlines", "Inline queries."),
        ("media", "Photos, files, and other media."),
        ("games", "Games."),
        ("sticker", "Stickers."),
        ("polls", "Polls."),
        ("gif", "GIFs."),
        ("pin", "Pinning messages."),
        ("changeinfo", "Changing chat information."),
    ],
    "Utils": [
        ("/info <user>", "Show details for a user by reply, ID, or username."),
        ("/id", "Show the current chat and sender IDs."),
        ("/tr <text>", "Translate text."),
        ("/q <reply>", "Create a quote from a message."),
        ("/paste <reply or text>", "Upload text or a file to Spacebin."),
        ("/meaning <word>", "Look up a word's meaning."),
        ("/google <query>", "Search the web."),
        ("/suggest <question>", "Create a yes/no poll."),
    ],
    "Search": [
        ("/search <query>", "Search the web for a query."),
        ("/deepresearch <query>", "Run a deeper, multi-step research search."),
    ],
    "Misc": [
        ("/joke", "Get a random joke."),
        ("/decide <options>", "Choose between options."),
        ("/kang <reply to sticker>", "Add a sticker to your pack."),
        ("/listpack", "List your sticker packs."),
    ],
    "Assistant": [
        ("/agent", "Configure an AI provider."),
        ("/akinator", "Play Akinator in a private chat."),
        ("/startgame", "Start a group game."),
        ("/afk [reason]", "Set or clear your away status."),
        ("/eph <reply>", "Send an ephemeral message."),
        ("/whisper <reply>", "Send a private whisper."),
        ("@botusername <query>", "Open an inline assistant tool."),
    ],
}

CATEGORY_LABELS = {
    "Admintools": "Admin tools",
    "locks": "Chat locks",
    "Utils": "Utilities",
    "Search": "Search",
    "Misc": "More tools",
    "Assistant": "Assistant",
}
CATEGORIES = list(HELP_SECTIONS)
HELP_PAGE_SIZE = 6

HELP_HOME = cb_exact_pattern("help", "manager", "home")
HELP_HOME_DATA = cb("help", "manager", "home")
CATEGORY_HOME_DATA = {cat: cb("help", cat, 0) for cat in CATEGORIES}
CATEGORY_PATTERNS = {
    "Admintools": cb_pattern("help", "Admintools"),
    **{cat: cb_pattern("help", cat) for cat in CATEGORIES if cat != "Admintools"},
}
assert set(CATEGORY_HOME_DATA) == set(CATEGORY_PATTERNS) == set(CATEGORIES)


def _help_home_page():
    rows = [
        (CATEGORY_LABELS[category], "Browse commands and controls in this section.")
        for category in CATEGORIES
    ]
    return rich_page(
        "Group Help",
        rows,
        description="Choose a section to browse commands and their purpose.",
        columns=("Section", "What it contains"),
        total=len(CATEGORIES),
    )


def get_buttons():
    rows = []
    categories = list(CATEGORIES)
    while categories:
        rows.append(
            [
                (CATEGORY_LABELS[cat], CATEGORY_HOME_DATA[cat], "primary")
                for cat in categories[:2]
            ]
        )
        categories = categories[2:]
    buttons = ikm(rows, icon=False)
    if getattr(asst, "me", None) and getattr(asst.me, "username", None):
        buttons.append(
            [
                Button.url(
                    "Add assistant to a group",
                    f"https://t.me/{asst.me.username}?startgroup=true",
                )
            ]
        )
    return buttons


def _help_page(cat, page):
    """Return a native rich page and navigation buttons for one category."""
    items = HELP_SECTIONS[cat]
    total = max(1, (len(items) + HELP_PAGE_SIZE - 1) // HELP_PAGE_SIZE)
    page = max(0, min(int(page), total - 1))
    start = page * HELP_PAGE_SIZE
    selected = items[start : start + HELP_PAGE_SIZE]
    content = rich_page(
        CATEGORY_LABELS[cat],
        selected,
        description="Commands and what they do.",
        columns=("Command", "Purpose"),
        page=page + 1,
        pages=total,
        total=len(items),
    )

    nav_row = []
    if page > 0:
        nav_row.append(("Previous", cb("help", cat, page - 1), "primary"))
    if total > 1:
        nav_row.append((f"{page + 1} / {total}", cb("help", cat, page), None))
    if page < total - 1:
        nav_row.append(("Next", cb("help", cat, page + 1), "primary"))
    rows = [nav_row] if nav_row else []
    rows.append([("Back to sections", HELP_HOME_DATA, None)])
    return content, ikm(rows, icon=False)


@asst_cmd(pattern="help")
async def helpish(event):
    if not event.is_private:
        url = f"https://t.me/{asst.me.username}?start=start"
        return await send_rich(
            event.client,
            event.chat_id,
            rich_page(
                "Open Help",
                [("Open the assistant", "Browse help in a private chat.")],
                columns=("Action", "What it does"),
                total=1,
            ),
            reply_to=event,
            buttons=[[Button.url("Open help", url)]],
            link_preview=False,
        )
    if str(event.sender_id) in owner_and_sudos() and (
        udB.get_key("DUAL_MODE") and udB.get_key("DUAL_HNDLR") == "/"
    ):
        return
    await send_rich(
        event.client,
        event.chat_id,
        _help_home_page(),
        reply_to=event,
        buttons=get_buttons(),
        link_preview=False,
    )


@callback(cb_exact_pattern("help", "manager", "mngbtn"), owner=True)
async def manager_help_button(event):
    buttons = get_buttons()
    buttons.append(get_back_button(cb("assistant", "inline", "main"), "Back"))
    await event.edit(buttons=buttons)


@callback(HELP_HOME)
async def home_aja(event):
    await edit_callback_page(event, _help_home_page(), buttons=get_buttons())


async def _category_page(event):
    """Shared handler for every ``help:<category>:<page>`` callback."""
    try:
        _, cat, raw_page = event.data.decode("utf-8").split(":", 2)
        if cat not in HELP_SECTIONS:
            return await event.answer(
                "That help section is no longer available.", alert=True
            )
        page = int(raw_page) if raw_page.isdigit() else 0
        content, buttons = _help_page(cat, page)
    except (ValueError, UnicodeDecodeError):
        return await event.answer("This help page could not be opened.", alert=True)
    await edit_callback_page(event, content, buttons=buttons)


for _pattern in CATEGORY_PATTERNS.values():
    callback(_pattern)(_category_page)

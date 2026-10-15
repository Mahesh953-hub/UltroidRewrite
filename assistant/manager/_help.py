# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""Group-manager help menu"""

from . import *
from .._cbs import cb, cb_exact_pattern, cb_pattern

START = """
🪅 **Help Menu** 🪅

✘  /start : Check I am Alive or not.
✘  /help : Get This Message.
✘  /repo : Get Bot's Repo..
✘  /agent : AI provider setup menu.

🧑‍💻 Join **@TeamUltroid**
"""

ADMINTOOLS = """✘ **AdminTools** ✘

• /pin : Pins the Replied Message
• /pinned : Get Pinned message in chat.
• /unpin : Unpin the Replied message
• /unpin all : Unpin all Pinned Messages.

• /ban (username/id/reply) : Ban the User
• /unban (username/id/reply) : UnBan the User.

• /mute (username/id/reply) : Mute the User.
• /unmute (username/id/reply) : Unmute the User.

• /tban (username/id/reply) (time) : Temporary ban a user
• /tmute (username/id/reply) (time) : temporary Mutes a User.

• /purge (purge messages)

• /setgpic (reply photo) : keep Chat Photo of Group.
• /delgpic : remove current chat Photo."""

UTILITIES = """
✘ ** Utilities ** ✘

• /info (reply/username/id) : get detailed info of user.
• /id : get chat/user id.
• /tr : Translate Languages..
• /q : Create Quotes.

• /paste (reply file/text) : paste content on Spaceb.in
• /meaning (text) : Get Meaning of that Word.
• /google (query) : Search Something on Google..

• /suggest (query/reply) : Creates a Yes / No Poll.
"""

LOCKS = """
✘ ** Locks ** ✘

• /lock (query) : lock particular content in chat.
• /unlock (query) : Unlock some content.

• All Queries
- `msgs` : for messages.
- `inlines` : for inline queries.
- `media` : for all medias.
- `games` : for games.
- `sticker` : for stickers.
- `polls` : for polls.
- `gif` : for gifs.
- `pin` : for pins.
- `changeinfo` : for change info right.
"""

MISC = """
✘  **Misc**  ✘

• /joke : Get Random Jokes.
• /decide : Decide Something..

**✘ Stickertools ✘**
• /kang : add sticker to your pack.
• /listpack : get all of yours pack..
"""

ASSISTANT = """
✘  **Assistant**  ✘

• /agent : AI provider setup (Ollama, cloud, Claude).
• /akinator : Play Akinator with the assistant.
• /startgame : Start a group game.
• /afk : Toggle AFK mode.
• /eph (reply) : Send an ephemeral message.
• /whisper (reply) : Send a whisper message.

**✘ Inline ✘**
• Type @botusername in any chat to use inline tools.
• /tr, /info, /id, /q work inline too.
"""

STRINGS = {
    "Admintools": ADMINTOOLS,
    "locks": LOCKS,
    "Utils": UTILITIES,
    "Misc": MISC,
    "Assistant": ASSISTANT,
}
CATEGORIES = list(STRINGS)

HELP_PAGE_SIZE = 10

MNGE = udB.get_key("MNGR_EMOJI") or "•"

HELP_HOME = cb_exact_pattern("help", "manager", "home")

HELP_HOME_DATA = cb("help", "manager", "home")

CATEGORY_HOME_DATA = {
    "Admintools": cb("help", "Admintools", 0),
    "locks": cb("help", "locks", 0),
    "Utils": cb("help", "Utils", 0),
    "Misc": cb("help", "Misc", 0),
    "Assistant": cb("help", "Assistant", 0),
}
CATEGORY_PATTERNS = {
    "Admintools": cb_pattern("help", "Admintools"),
    "locks": cb_pattern("help", "locks"),
    "Utils": cb_pattern("help", "Utils"),
    "Misc": cb_pattern("help", "Misc"),
    "Assistant": cb_pattern("help", "Assistant"),
}
assert set(CATEGORY_HOME_DATA) == set(CATEGORY_PATTERNS) == set(CATEGORIES)


def get_buttons():
    rows = []
    keys = list(CATEGORIES)
    while keys:
        rows.append(
            [(f"{MNGE} {c} {MNGE}", CATEGORY_HOME_DATA[c], "primary") for c in keys[:2]]
        )
        keys = keys[2:]
    buttons = ikm(rows, icon=True)
    url = f"https://t.me/{asst.me.username}?startgroup=true"
    buttons.append([Button.url("Add me to Group", url)])
    return buttons


def _help_page(cat, page):
    """Return (text, buttons) for one paginated page of a help category."""
    lines = STRINGS[cat].splitlines()
    total = max(1, (len(lines) + HELP_PAGE_SIZE - 1) // HELP_PAGE_SIZE)
    page = max(0, min(page, total - 1))
    start = page * HELP_PAGE_SIZE
    chunk = "\n".join(lines[start : start + HELP_PAGE_SIZE])

    nav_row = []
    if page > 0:
        nav_row.append(("‹ Prev", cb("help", cat, page - 1), "primary"))
    if total > 1:
        nav_row.append((f"{page + 1}/{total}", cb("help", cat, page), "default"))
    if page < total - 1:
        nav_row.append(("Next ›", cb("help", cat, page + 1), "primary"))

    rows = []
    if nav_row:
        rows.append(nav_row)
    rows.append([("« Back", HELP_HOME_DATA, "danger")])
    return chunk, ikm(rows, icon=True)


@asst_cmd(pattern="help")
async def helpish(event):
    if not event.is_private:
        url = f"https://t.me/{asst.me.username}?start=start"
        return await event.reply(
            "Contact me in PM for help!", buttons=Button.url("Click me for Help", url)
        )
    if str(event.sender_id) in owner_and_sudos() and (
        udB.get_key("DUAL_MODE") and (udB.get_key("DUAL_HNDLR") == "/")
    ):
        return
    await event.reply(START, buttons=get_buttons())


@callback(cb_exact_pattern("help", "manager", "manager_button"), owner=True)
async def ehwhshd(e):
    buttons = get_buttons()
    buttons.append(get_back_button(cb("assistant", "inline", "main"), "« Back"))
    await e.edit(buttons=buttons)


@callback(HELP_HOME)
async def home_aja(e):
    await e.edit(START, buttons=get_buttons())


async def _category_page(event):
    """Shared handler for every ``help:<category>:<page>`` pattern."""
    _, cat, raw = event.data.decode("utf-8").split(":", 2)
    page = int(raw) if raw.isdigit() else 0
    text, buttons = _help_page(cat, page)
    await event.edit(text, buttons=buttons)


for _pattern in CATEGORY_PATTERNS.values():
    callback(_pattern)(_category_page)

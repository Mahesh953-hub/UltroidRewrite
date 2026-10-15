# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from . import *
from ._cbs import cb, cb_pattern
from custom.tg import ikm

STRINGS = {
    1: """🎇 **Thanks for Deploying Ultroid Userbot!**

• Here, are the Some Basic stuff from, where you can Know, about its Usage.""",
    2: """🎉** About Ultroid**

🧿 Ultroid is Pluggable and powerful Telethon Userbot, made in Python from Scratch. It is Aimed to Increase Security along with Addition of Other Useful Features.

❣ Made by **@TeamUltroid**""",
    3: """**💡• FAQs •**

-> [Username Tracker](https://t.me/UltroidUpdates/24)
-> [Keeping Custom Addons Repo](https://t.me/UltroidUpdates/28)
-> [Disabling Deploy message](https://t.me/UltroidUpdates/27)
-> [Setting up TimeZone](https://t.me/UltroidUpdates/22)
-> [About Inline PmPermit](https://t.me/UltroidUpdates/21)
-> [About Dual Mode](https://t.me/UltroidUpdates/18)
-> [Custom Thumbnail](https://t.me/UltroidUpdates/13)
-> [About FullSudo](https://t.me/UltroidUpdates/11)
-> [Setting Up PmBot](https://t.me/UltroidUpdates/2)
-> [Also Check](https://t.me/UltroidUpdates/14)

**• To Know About Updates**
  - Join @TeamUltroid.""",
    4: f"""• `To Know All Available Commands`

  - `{HNDLR}help`
  - `{HNDLR}cmds`""",
    5: """• **For Any Other Query or Suggestion**
  - Move to **@UltroidSupportChat**.

• Thanks for Reaching till END.""",
}

PAGE_PATTERN = cb_pattern("assistant", "initial")


def _page_buttons(current):
    if current == 1:
        return ikm(
            [[("Start", cb("assistant", "initial", "next", 2), "green")]], icon=True
        )
    if current == 5:
        return ikm(
            [[("Back", cb("assistant", "initial", "prev", 4), "danger")]], icon=True
        )
    return ikm(
        [
            [
                ("<<", cb("assistant", "initial", "prev", current - 1), "danger"),
                (">>", cb("assistant", "initial", "next", current + 1), "primary"),
            ]
        ],
        icon=True,
    )


async def _render_page(e, current):
    await e.edit(STRINGS[current], buttons=_page_buttons(current), link_preview=False)


@callback(PAGE_PATTERN)
async def init_depl(e):
    current = int(e.data_match.group(1).rsplit(b":", 1)[-1])
    await _render_page(e, current)

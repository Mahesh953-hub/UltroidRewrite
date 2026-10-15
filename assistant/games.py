# Ultroid - UserBot
# Copyright (C) 2021-2024 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""
• `{i}akinator` | `/akinator`
   Start akinator game from Userbot/Assistant

• `/startgame`
   Open Portal for Games
"""

import asyncio
import re
import operator
import uuid
from html import unescape
from random import choice, shuffle

try:
    from akipy.async_akipy import Akinator, akipyLOGS
except ImportError:
    from akipy.async_akinator import Akinator
    import logging

    akipyLOGS = logging.getLogger("akipy")
from telethon.errors.rpcerrorlist import BotMethodInvalidError
from telethon.events import Raw
from telethon.tl import types
from telethon.tl.types import InputMediaPoll, Poll, PollAnswer, UpdateMessagePollVote

from pyUltroid._misc._decorators import ultroid_cmd
from custom.tg import ikm
from custom.fmt import fmtmsg

from . import *  # Ensure this import matches your project structure
from ._cbs import cb, cb_str, cb_exact_pattern, cb_pattern
from pyUltroid.fns import fns
from pyUltroid.fns import fns

games = {}
aki_photo = "https://graph.org/file/3cc8825c029fd0cab9edc.jpg"

#: ``assistant:games:aki:<chat>:<mid>`` — reused verbatim as both the inline
#: query text (``eiagx``/``akina``) and the resulting button/callback data
#: (``doai``), so it's built once here rather than three separate times.
AKI_PREFIX = cb_str("assistant", "games", "aki")
AKI_CALLBACK_PATTERN = cb_pattern("assistant", "games", "aki")
AKI_TEXT_PATTERN = re.compile(re.escape(AKI_PREFIX) + r":(.*)")

TRIVIA_CAT = cb("assistant", "games", "trivia", "cat")
DELIT = cb("assistant", "games", "delit")


def _aka_buttons(ch, mid):
    """Akinator answer keyboard for game ``(ch, mid)`` (used by both the
    first question in ``doai`` and every follow-up in ``okah``)."""

    def _a(ans):
        return cb("assistant", "games", "aka", ch, mid, ans)

    return ikm(
        [
            [
                ("Yes", _a("Yes"), "success"),
                ("No", _a("No"), "danger"),
                ("Idk", _a("Idk"), "primary"),
            ],
            [
                ("Probably", _a("Probably"), "primary"),
                ("Probably Not", _a("Probably Not"), "primary"),
            ],
        ],
        icon=True,
    )


@ultroid_cmd(pattern="akinator")
async def akina(e):
    sta = Akinator()
    games[e.chat_id] = {e.id: sta}
    LOGS.info(f"Game started for chat {e.chat_id} with ID {e.id}.")
    query = f"{AKI_PREFIX}:{e.chat_id}:{e.id}"
    try:
        m = await e.client.inline_query(asst.me.username, query)
        await m[0].click(e.chat_id)
        akipyLOGS.info(f"Clicked inline result for chat {e.chat_id}")
    except BotMethodInvalidError as err:
        akipyLOGS.error(f"BotMethodInvalidError: {err}")
        await asst.send_file(
            e.chat_id,
            aki_photo,
            buttons=ikm([[(get_string("aki_2"), query, "primary")]], icon=True),
        )
    except Exception as er:
        akipyLOGS.error(f"Unexpected error: {er}")
        return await e.eor(f"ERROR : {er}")
    if e.out:
        await e.delete()


@asst_cmd(pattern="akinator", owner=True)
async def _akokk(e):
    await akina(e)


@callback(AKI_CALLBACK_PATTERN, owner=True)
async def doai(e):
    adt = e.data_match.group(1).decode("utf-8")
    ch, mid = adt.split(":")
    ch = int(ch)
    mid = int(mid)
    await e.edit(get_string("com_1"))
    try:
        await games[ch][mid].start_game(child_mode=False)
        bts = _aka_buttons(ch, mid)
        await e.edit(
            fmtmsg("Akinator 🤔", msgs=f"Q. {games[ch][mid].question}"),
            buttons=bts,
            parse_mode="html",
        )
    except KeyError:
        return await e.answer(get_string("aki_1"), alert=True)


@callback(cb_pattern("assistant", "games", "aka"), owner=True)
async def okah(e):
    try:
        mk = e.data_match.group(1).decode("utf-8").split(":")

        if len(mk) < 3:
            akipyLOGS.error("Pattern match did not return enough parts.")
            return await e.answer("Invalid data received.", alert=True)

        ch = int(mk[0])
        mid = int(mk[1])
        ans = mk[2]

        gm = games[ch][mid]
        await gm.answer(ans)

        # Check for the final guess in the API response
        if gm.name_proposition and gm.description_proposition:
            gm.win = True
            text = fmtmsg(
                "Akinator Result 🎉",
                msgs=f"It's <b>{gm.name_proposition}</b>\n{gm.description_proposition}",
            )
            await e.edit(text, file=gm.photo, parse_mode="html")
        else:
            # Game is not won yet, continue asking questions
            buttons = _aka_buttons(ch, mid)
            await e.edit(
                fmtmsg("Akinator 🤔", msgs=gm.question),
                buttons=buttons,
                parse_mode="html",
            )

    except KeyError:
        await e.answer(get_string("aki_3"))
    except Exception as ex:
        akipyLOGS.error(f"An unexpected error occurred: {ex}")


@in_pattern(AKI_TEXT_PATTERN, owner=True)
async def eiagx(e):
    bts = ikm([[(get_string("aki_2"), e.text, "primary")]], icon=True)
    ci = types.InputWebDocument(aki_photo, 0, "image/jpeg", [])
    ans = [
        await e.builder.article(
            "Akinator",
            content=ci,
            text="Akinator",
            thumb=ci,
            buttons=bts,
            include_media=True,
        )
    ]
    await e.answer(ans)


# ----------------------- Main Command ------------------- #

GIMAGES = [
    "https://graph.org/file/1c51015bae5205a65fd69.jpg",
    "https://imgwhale.xyz/3xyr322l64j9590",
]


@asst_cmd(pattern="startgame", owner=True)
async def magic(event):
    buttons = ikm(
        [
            [("Trivia Quiz 🎯", TRIVIA_CAT, "primary")],
            [("Cancel ❌", DELIT, "danger")],
        ],
        icon=True,
    )
    await event.reply(
        fmtmsg("SarahUB Games 🎮", msgs=get_string("games_1")),
        file=choice(GIMAGES),
        buttons=buttons,
        parse_mode="html",
    )


# -------------------------- Trivia ----------------------- #

TR_BTS = {}
DIFI_KEYS = ["Easy", "Medium", "Hard"]
TRIVIA_CHATS = {}
POLLS = {}
CONGO_STICKER = [
    "CAADAgADSgIAAladvQrJasZoYBh68AI",
    "CAADAgADXhIAAuyZKUl879mlR_dkOwI",
    "CAADAgADpQAD9wLID-xfZCDwOI5LAg",
    "CAADAgADjAADECECEFZM-SrKO9GgAg",
    "CAADAgADSwIAAj-VzArAzNCDiGWAHAI",
    "CAADAgADhQADwZxgDIuMHR9IU10iAg",
    "CAADAgADiwMAAsSraAuoe2BwYu1sdQI",
]

TRIVIA_PATTERN = cb_pattern("assistant", "games", "trivia")
CTDOWN_PATTERN = cb_pattern("assistant", "games", "ctdown")


@callback(cb_exact_pattern("assistant", "games", "delit"), owner=True)
async def delete_it(event):
    await event.delete()


@callback(CTDOWN_PATTERN, owner=True)
async def ct_spam(e):
    n = e.data_match.group(1).decode("utf-8")
    await e.answer(f"Wait {n} seconds..", alert=True)


@callback(TRIVIA_PATTERN, owner=True)
async def choose_cata(event):
    tail = event.data_match.group(1).decode("utf-8")
    parts = tail.split(":")
    action = parts[0]
    rest = parts[1:]

    if action == "cat":
        if TR_BTS.get("category"):
            buttons = TR_BTS["category"]
        else:
            req = (
                await fns.async_searcher(
                    "https://opentdb.com/api_category.php", re_json=True
                )
            )["trivia_categories"]
            btt = []
            for i in req:
                name = i["name"]
                if ":" in name:
                    name = name.split(":")[1]
                btt.append(
                    (
                        name,
                        cb("assistant", "games", "trivia", "diff", i["id"]),
                        "primary",
                    )
                )
            buttons_data = list(zip(btt[::2], btt[1::2]))
            if len(btt) % 2 == 1:
                buttons_data.append([btt[-1]])
            buttons_data.append([("Cancel ❌", DELIT, "danger")])
            buttons = ikm(buttons_data, icon=True)
            TR_BTS.update({"category": buttons})
        text = fmtmsg("Trivia Setup 🎯", msgs=get_string("games_2"))
    elif action == "diff":
        cat = rest[0]
        buttons = ikm(
            [
                [
                    (
                        "Easy 😄",
                        cb("assistant", "games", "trivia", "num", cat, "Easy"),
                        "success",
                    ),
                    (
                        "Medium 😐",
                        cb("assistant", "games", "trivia", "num", cat, "Medium"),
                        "primary",
                    ),
                    (
                        "Hard 💀",
                        cb("assistant", "games", "trivia", "num", cat, "Hard"),
                        "danger",
                    ),
                ],
                [("« Bᴀᴄᴋ", TRIVIA_CAT, "danger")],
            ],
            icon=True,
        )
        text = fmtmsg("Difficulty Selection ⚙️", msgs=get_string("games_3"))
    elif action == "num":
        cat, diff = rest
        buttons = ikm(
            [
                [
                    (
                        f"{i} Questions",
                        cb("assistant", "games", "trivia", "time", cat, diff, i),
                        "primary",
                    )
                    for i in range(10, 70, 20)
                ],
                [("« Bᴀᴄᴋ", TRIVIA_CAT, "danger")],
            ],
            icon=True,
        )
        text = fmtmsg("Number of Questions 📝", msgs=get_string("games_4"))
    elif action == "time":
        cat, diff, num = rest
        buttons = ikm(
            [
                [
                    (
                        f"{i}s",
                        cb("assistant", "games", "trivia", "start", cat, diff, num, i),
                        "primary",
                    )
                    for i in [10, 30, 60, 120]
                ],
                [("« Bᴀᴄᴋ", TRIVIA_CAT, "danger")],
            ],
            icon=True,
        )
        text = fmtmsg("Time Limit per Question ⏰", msgs=get_string("games_5"))
    elif action == "start":
        cat, le, nu, in_ = rest
        chat = event.chat_id
        msg = await event.edit(
            fmtmsg("Trivia Quiz 🎯", msgs=get_string("games_6").format(le, nu)),
            parse_mode="html",
        )
        for i in reversed(range(5)):
            msg = await msg.edit(
                buttons=ikm(
                    [[(f"{i} ⏰", cb("assistant", "games", "ctdown", i), "danger")]],
                    icon=True,
                )
            )
            await asyncio.sleep(1)
        await msg.edit(
            msg.text + "\n\n• Send /cancel to stop the Quiz...", buttons=None
        )
        qsss = await fns.async_searcher(
            f"https://opentdb.com/api.php?amount={nu}&category={cat}&difficulty={le.lower()}",
            re_json=True,
        )
        qs = qsss["results"]
        if not qs:
            await event.respond("Sorry, No Question Found for the given Criteria..")
            await event.delete()
            return
        TRIVIA_CHATS.update({chat: {}})
        for copper, q in enumerate(qs):
            if TRIVIA_CHATS[chat].get("cancel") is not None:
                break
            ansi = str(uuid.uuid1()).split("-")[0].encode()
            opts = [PollAnswer(unescape(q["correct_answer"]), ansi)]
            [
                opts.append(
                    PollAnswer(unescape(a), str(uuid.uuid1()).split("-")[0].encode())
                )
                for a in q["incorrect_answers"]
            ]
            shuffle(opts)
            poll = InputMediaPoll(
                Poll(
                    0,
                    f"[{copper + 1}].  " + unescape(q["question"]),
                    answers=opts,
                    public_voters=True,
                    quiz=True,
                    close_period=int(in_),
                ),
                correct_answers=[ansi],
                solution="Join @TeamUltroid",
                solution_entities=[],
            )
            m_ = await event.client.send_message(chat, file=poll)
            POLLS.update({m_.poll.poll.id: {"chat": m_.chat_id, "answer": ansi}})
            await asyncio.sleep(int(in_))
        if not TRIVIA_CHATS[chat]:
            await event.respond(
                fmtmsg(
                    "Quiz Finished 🎯",
                    msgs="No-One Got Any Score in the Quiz!\nBetter Luck Next Time!",
                ),
                parse_mode="html",
            )
        else:
            try:
                await event.respond(file=choice(CONGO_STICKER))
            except ChatSendStickersForbiddenError:
                pass
            LBD = {}
            TRC = TRIVIA_CHATS[chat]
            if "cancel" in TRC.keys():
                del TRC["cancel"]
            for userid, user_score in dict(
                sorted(TRC.items(), key=operator.itemgetter(1), reverse=True)
            ).items():
                user_entity = await event.client.get_entity(userid)
                LBD[get_display_name(user_entity)] = f"{user_score} Points"

            await event.respond(
                fmtmsg("🎯 Quiz Scoreboard", data=LBD),
                parse_mode="html",
            )
        del TRIVIA_CHATS[chat]
        list_ = list(POLLS.copy().keys())
        for key in list_:
            if POLLS[key]["chat"] == chat:
                del POLLS[key]
        return
    await event.edit(text, buttons=buttons, parse_mode="html")


@asst.on(
    Raw(UpdateMessagePollVote, func=lambda x: TRIVIA_CHATS and POLLS.get(x.poll_id))
)
async def pollish(eve):
    if POLLS.get(eve.poll_id)["chat"] not in TRIVIA_CHATS.keys():
        return
    if POLLS[eve.poll_id]["answer"] != eve.options[0]:
        return
    chat = POLLS.get(eve.poll_id)["chat"]
    user = eve.user_id
    if not TRIVIA_CHATS.get(chat, {}).get(user):
        TRIVIA_CHATS[chat][user] = 1
    else:
        TRIVIA_CHATS[chat][user] += 1


@asst_cmd("cancel", owner=True, func=lambda x: TRIVIA_CHATS.get(x.chat_id))
async def cancelish(event):
    chat = TRIVIA_CHATS.get(event.chat_id)
    chat.update({"cancel": True})
    await event.respond("Cancelled!")

# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}emojistatus <doc_id> [hours]` `<or reply to a custom-emoji message>`
    Set your profile's emoji status to the given custom-emoji document.
    Reply to a message containing a custom emoji (as text or as a custom
    emoji "sticker") to use that one instead of typing a document id.
    `[hours]` optionally makes the status auto-clear after that many hours
    (omit for a permanent status). **Telegram Premium required.**

• `{i}emojistatus off`
    Clear your emoji status.
"""

import datetime

from telethon.errors.rpcbaseerrors import RPCError
from telethon.tl import functions, types

from pyUltroid._core.business_profile import is_premium

from . import LOGS, eod, get_string, ultroid_cmd

_PREMIUM_REQUIRED = (
    "⭐ **Telegram Premium is required to set an emoji status.**\n"
    "`This account isn't Premium.`"
)


def _extract_doc_id_from_message(msg):
    """Best-effort: pull a custom-emoji document id out of *msg* — either a
    ``MessageEntityCustomEmoji`` in its text, or a custom-emoji document
    attached as media (``DocumentAttributeCustomEmoji``)."""
    for ent in getattr(msg, "entities", None) or ():
        if isinstance(ent, types.MessageEntityCustomEmoji):
            return ent.document_id
    doc = getattr(msg, "document", None)
    if doc is not None:
        for attr in getattr(doc, "attributes", None) or ():
            if isinstance(attr, types.DocumentAttributeCustomEmoji):
                return doc.id
    return None


def _friendly_error(exc):
    message = str(getattr(exc, "message", "") or exc).upper()
    if "PREMIUM" in message:
        return _PREMIUM_REQUIRED
    return f"❌ **Error.**\n`{exc}`"


@ultroid_cmd(pattern="emojistatus( (.*)|$)", fullsudo=True)
async def _emoji_status(ult):
    raw = ult.pattern_match.group(1).strip()
    tokens = raw.split()

    req_cls = getattr(functions.account, "UpdateEmojiStatusRequest", None)
    if req_cls is None:
        return await ult.eor(
            "`account.UpdateEmojiStatusRequest isn't available in this "
            "Telethon build (fork layer mismatch?)`",
            time=8,
        )

    # --- .emojistatus off ---------------------------------------------
    if tokens and tokens[0].lower() in {"off", "clear", "none"}:
        ok = await ult.eor(get_string("com_1"))
        try:
            await ult.client(req_cls(emoji_status=types.EmojiStatusEmpty()))
        except RPCError as exc:
            LOGS.debug("status: clear emoji status failed: %s", exc)
            return await eod(ok, _friendly_error(exc))
        except Exception as exc:  # noqa: BLE001
            LOGS.debug("status: clear emoji status failed: %s", exc)
            return await eod(ok, _friendly_error(exc))
        return await eod(ok, "✅ **Emoji status cleared.**")

    # --- resolve doc_id + optional hours --------------------------------
    doc_id = None
    hours = None
    if ult.is_reply:
        reply = await ult.get_reply_message()
        doc_id = _extract_doc_id_from_message(reply)
        if tokens:
            if not tokens[0].isdigit():
                return await ult.eor(
                    "`Usage: .emojistatus [hours]` (replying to a custom emoji)",
                    time=6,
                )
            hours = int(tokens[0])
        if doc_id is None:
            return await ult.eor(
                "`Couldn't find a custom emoji in the replied message.`", time=6
            )
    else:
        if not tokens or not tokens[0].isdigit():
            return await ult.eor(
                "`Usage: .emojistatus <doc_id> [hours]` (or reply to a custom "
                "emoji), or `.emojistatus off`",
                time=8,
            )
        doc_id = int(tokens[0])
        if len(tokens) > 1:
            if not tokens[1].isdigit():
                return await ult.eor("`<hours> must be a number.`", time=6)
            hours = int(tokens[1])

    # --- Premium probe before acting ------------------------------------
    try:
        me = await ult.client.get_me()
    except Exception as exc:  # noqa: BLE001 - network hiccup, let it degrade
        LOGS.debug("status: couldn't fetch self for premium probe: %s", exc)
        me = None
    if not await is_premium(ult.client, me=me):
        return await ult.eor(_PREMIUM_REQUIRED, time=8)

    until = None
    if hours is not None:
        until = datetime.datetime.now(datetime.UTC) + datetime.timedelta(
            hours=hours
        )

    ok = await ult.eor(get_string("com_1"))
    try:
        await ult.client(
            req_cls(emoji_status=types.EmojiStatus(document_id=doc_id, until=until))
        )
    except RPCError as exc:
        LOGS.debug("status: set emoji status failed: %s", exc)
        return await eod(ok, _friendly_error(exc))
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("status: set emoji status failed: %s", exc)
        return await eod(ok, _friendly_error(exc))

    suffix = f" for {hours}h" if hours else ""
    await eod(ok, f"✅ **Emoji status set** (doc `{doc_id}`){suffix}.")

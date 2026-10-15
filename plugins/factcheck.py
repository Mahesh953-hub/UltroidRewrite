# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}factcheck <text>` `<reply to a post>`
    Add or update the fact-check attached to the replied message via the
    fork's ``messages.EditFactCheckRequest`` (schema: ``FactCheck``,
    layer 228). MarkdownV3 in `<text>` is preserved as entities.

    **Note:** unlike most moderator tools, adding a fact-check is *not*
    a normal chat-admin right — Telegram gates
    ``messages.editFactCheck`` behind a server-side "independent
    fact-checker" flag on the account. This account may still get a
    permission error even as a full admin/owner of the chat; that is
    Telegram's fact-checker program, not a bug here.

• `{i}factcheck` `<reply to a post>`
    Show the fact-check already attached to the replied message, if any
    (reads ``Message.factcheck``, fetching the full text via
    ``messages.GetFactCheckRequest`` when only a hash is cached).

• `{i}factclear` `<reply to a post>`
    Remove the fact-check from the replied message
    (``messages.DeleteFactCheckRequest``).
"""

from telethon.errors.rpcbaseerrors import RPCError
from telethon.tl import functions, types

from pyUltroid._core.entities import RICH_MD, mdv3_to_entities

from . import LOGS, eod, ultroid_cmd


def _friendly_error(exc):
    """Turn any RPCError/generic exception from a fact-check request into a
    short, chat-friendly message — never a traceback."""
    message = str(getattr(exc, "message", "") or exc).upper()
    if "FACTCHECK" in message and "ALLOWED" in message:
        return (
            "🚫 **This account isn't an independent fact-checker.**\n"
            "Adding/removing fact-checks is gated behind Telegram's "
            "fact-checker program, not a normal chat-admin right.\n"
            f"`{exc}`"
        )
    if "ADMIN" in message:
        return f"🚫 **Missing rights in this chat.**\n`{exc}`"
    if "TOO_LONG" in message:
        return f"❌ **Fact-check text is too long.**\n`{exc}`"
    if "PRIVATE" in message:
        return f"❌ **Can't reach this chat.**\n`{exc}`"
    return f"❌ **Error.**\n`{exc}`"


async def _resolve_reply(ult):
    """Return the replied-to message, or ``None`` with a usage hint sent."""
    if not ult.is_reply:
        await ult.eor(
            "`Reply to the post you want to manage the fact-check of.`", time=6
        )
        return None
    return await ult.get_reply_message()


def _format_factcheck(fc):
    """Render a ``FactCheck`` TL object as a short display string."""
    text_obj = getattr(fc, "text", None)
    if text_obj is None:
        return None
    text = getattr(text_obj, "text", "") or ""
    entities = getattr(text_obj, "entities", None) or []
    try:
        rendered = RICH_MD.unparse(text, entities) if entities else text
    except Exception:  # noqa: BLE001 - never let rendering crash the show
        rendered = text
    country = getattr(fc, "country", None)
    suffix = f"\n\n`country: {country}`" if country else ""
    return f"{rendered}{suffix}"


async def _fetch_full_factcheck(ult, chat, msg_id, fc):
    """Return a display string for *fc*, refetching full text if needed."""
    rendered = _format_factcheck(fc)
    if rendered is not None:
        return rendered
    if not getattr(fc, "need_check", False):
        return None
    req_cls = getattr(functions.messages, "GetFactCheckRequest", None)
    if req_cls is None:
        return None
    try:
        result = await ult.client(req_cls(peer=chat, msg_id=[msg_id]))
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("factcheck: GetFactCheckRequest failed: %s", exc)
        return None
    if not result:
        return None
    return _format_factcheck(result[0])


@ultroid_cmd(pattern="factcheck( (.*)|$)", fullsudo=True)
async def _factcheck(ult):
    raw = ult.pattern_match.group(1).strip()
    reply = await _resolve_reply(ult)
    if reply is None:
        return

    try:
        chat = await ult.get_input_chat()
    except Exception as exc:  # noqa: BLE001
        return await ult.eor(f"`Couldn't resolve this chat: {exc}`", time=6)

    if not raw:
        # bare `.factcheck` — show whatever's already there.
        fc = getattr(reply, "factcheck", None)
        if fc is None:
            return await ult.eor("`No fact-check is set on this message.`", time=6)
        rendered = await _fetch_full_factcheck(ult, chat, reply.id, fc)
        if rendered is None:
            return await ult.eor(
                "`This message has a fact-check, but its text couldn't be "
                "fetched (hash-only, no cached text).`",
                time=8,
            )
        return await ult.eor(f"**Fact-check on this post:**\n\n{rendered}")

    # `.factcheck <text>` — set/update it.
    req_cls = getattr(functions.messages, "EditFactCheckRequest", None)
    twe_cls = getattr(types, "TextWithEntities", None)
    if req_cls is None or twe_cls is None:
        return await ult.eor(
            "`messages.EditFactCheckRequest / TextWithEntities aren't "
            "available in this Telethon build (fork layer mismatch?)`",
            time=8,
        )

    text, entities = mdv3_to_entities(raw)
    twe = twe_cls(text=text, entities=entities or [])

    ok = await ult.eor("`Updating fact-check...`")
    try:
        await ult.client(req_cls(peer=chat, msg_id=reply.id, text=twe))
    except RPCError as exc:
        LOGS.debug("factcheck: EditFactCheckRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("factcheck: EditFactCheckRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))
    await eod(ok, "✅ **Fact-check updated.**")


@ultroid_cmd(pattern="factclear$", fullsudo=True)
async def _factclear(ult):
    reply = await _resolve_reply(ult)
    if reply is None:
        return

    try:
        chat = await ult.get_input_chat()
    except Exception as exc:  # noqa: BLE001
        return await ult.eor(f"`Couldn't resolve this chat: {exc}`", time=6)

    req_cls = getattr(functions.messages, "DeleteFactCheckRequest", None)
    if req_cls is None:
        return await ult.eor(
            "`messages.DeleteFactCheckRequest isn't available in this "
            "Telethon build (fork layer mismatch?)`",
            time=8,
        )

    ok = await ult.eor("`Removing fact-check...`")
    try:
        await ult.client(req_cls(peer=chat, msg_id=reply.id))
    except RPCError as exc:
        LOGS.debug("factcheck: DeleteFactCheckRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("factcheck: DeleteFactCheckRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))
    await eod(ok, "✅ **Fact-check removed.**")

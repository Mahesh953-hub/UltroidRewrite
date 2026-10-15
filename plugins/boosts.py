# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}boosts [channel]`
    Show the boost status card for `[channel]` (or this chat if omitted):
    level, boost count, boosts needed for the next level, and this
    account's own boost slots — via ``premium.GetBoostsStatusRequest`` /
    ``premium.GetMyBoostsRequest``.

• `{i}boost <channel> [slot]`
    Apply one of this account's Premium boost slots to `<channel>` via
    ``premium.ApplyBoostRequest``. `[slot]` picks a specific slot number;
    when omitted, the first free (unused, not-in-cooldown) slot is used
    automatically. **Requires Telegram Premium** — degrades to a
    friendly message otherwise.
"""

from telethon.errors.rpcbaseerrors import RPCError
from telethon.utils import get_display_name
from telethon.tl import functions

from pyUltroid._core.business_profile import is_premium
from pyUltroid._core.richmsg import RichBlocks, send_rich

from . import LOGS, eod, ultroid_cmd

_PREMIUM_REQUIRED = (
    "⭐ **Telegram Premium is required to boost a channel.**\n"
    "`This account isn't Premium.`"
)


def _friendly_error(exc):
    """Turn a boosts-related RPCError/generic exception into a short,
    chat-friendly message — never a traceback."""
    message = str(getattr(exc, "message", "") or exc).upper()
    if "PREMIUM" in message:
        return _PREMIUM_REQUIRED
    if "BOOSTS_EMPTY" in message or "SLOTS_EMPTY" in message:
        return f"❌ **No boost slots to apply.**\n`{exc}`"
    if "BOOST_NOT_MODIFIED" in message:
        return f"`Already boosting with that slot.`\n`{exc}`"
    if "FLOOD" in message:
        return f"⏳ **Rate-limited.**\n`{exc}`"
    if "PRIVATE" in message or "PEER" in message:
        return f"❌ **Can't reach that channel.**\n`{exc}`"
    return f"❌ **Error.**\n`{exc}`"


async def _resolve_entity(ult, token):
    """Resolve *token* (or the current chat when falsy) to an entity."""
    target = token or ult.chat_id
    peer_id = await ult.client.parse_id(target)
    return await ult.client.get_entity(peer_id)


@ultroid_cmd(pattern="boosts( (.*)|$)", fullsudo=True)
async def _boosts_status(ult):
    raw = ult.pattern_match.group(1).strip()

    try:
        entity = await _resolve_entity(ult, raw)
    except Exception as exc:  # noqa: BLE001
        return await ult.eor(f"`Couldn't resolve channel: {exc}`", time=6)
    title = get_display_name(entity) or str(getattr(entity, "id", entity))

    status_cls = getattr(functions.premium, "GetBoostsStatusRequest", None)
    if status_cls is None:
        return await ult.eor(
            "`premium.GetBoostsStatusRequest isn't available in this "
            "Telethon build (fork layer mismatch?)`",
            time=8,
        )

    ok = await ult.eor("`Fetching boost status...`")
    try:
        status = await ult.client(status_cls(peer=entity))
    except RPCError as exc:
        LOGS.debug("boosts: GetBoostsStatusRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("boosts: GetBoostsStatusRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))

    my_boosts = None
    mb_cls = getattr(functions.premium, "GetMyBoostsRequest", None)
    if mb_cls is not None:
        try:
            my_boosts = await ult.client(mb_cls())
        except Exception as exc:  # noqa: BLE001 - purely a bonus section
            LOGS.debug("boosts: GetMyBoostsRequest failed: %s", exc)
            my_boosts = None

    next_level = (
        str(status.next_level_boosts) if status.next_level_boosts is not None else "max"
    )
    blocks = (
        RichBlocks()
        .header(f"Boosts — {title}")
        .table(
            [
                ["Level", "Boosts", "Next level"],
                [str(status.level), str(status.boosts), next_level],
            ]
        )
    )
    if status.my_boost_slots:
        blocks.paragraph(
            "Your slot(s) boosting this chat: "
            + ", ".join(str(s) for s in status.my_boost_slots)
        )
    if my_boosts is not None and my_boosts.my_boosts:
        lines = []
        for mb in my_boosts.my_boosts:
            state = "unused" if mb.peer is None else "in use"
            if mb.cooldown_until_date:
                state += f" (cooldown until `{mb.cooldown_until_date}`)"
            lines.append(f"slot `{mb.slot}` — {state}")
        blocks.expandable("Your boost slots (all chats)", "\n".join(lines))

    try:
        await send_rich(ult.client, ult.chat_id, blocks, reply_to=ult.id)
        await ok.delete()
    except Exception as exc:  # noqa: BLE001 - send_rich already falls back
        LOGS.debug("boosts: send_rich failed, showing plain summary: %s", exc)
        await eod(
            ok,
            f"**Boosts — {title}**\n\n"
            f"Level `{status.level}` · Boosts `{status.boosts}` · "
            f"Next level `{next_level}`",
        )


@ultroid_cmd(pattern="boost( (.*)|$)", fullsudo=True)
async def _boost_apply(ult):
    raw = ult.pattern_match.group(1).strip()
    tokens = raw.split()
    if not tokens:
        return await ult.eor("`Usage: .boost <channel> [slot]`", time=6)

    channel_token = tokens[0]
    slot_token = tokens[1] if len(tokens) > 1 else None
    if slot_token is not None and not slot_token.isdigit():
        return await ult.eor("`[slot] must be a number.`", time=6)

    try:
        entity = await _resolve_entity(ult, channel_token)
    except Exception as exc:  # noqa: BLE001
        return await ult.eor(f"`Couldn't resolve channel: {exc}`", time=6)
    title = get_display_name(entity) or str(getattr(entity, "id", entity))

    try:
        me = await ult.client.get_me()
    except Exception as exc:  # noqa: BLE001 - network hiccup, let it degrade
        LOGS.debug("boosts: couldn't fetch self for premium probe: %s", exc)
        me = None
    if not await is_premium(ult.client, me=me):
        return await ult.eor(_PREMIUM_REQUIRED, time=8)

    apply_cls = getattr(functions.premium, "ApplyBoostRequest", None)
    if apply_cls is None:
        return await ult.eor(
            "`premium.ApplyBoostRequest isn't available in this Telethon "
            "build (fork layer mismatch?)`",
            time=8,
        )

    ok = await ult.eor("`Applying boost...`")

    if slot_token is not None:
        slot = int(slot_token)
    else:
        mb_cls = getattr(functions.premium, "GetMyBoostsRequest", None)
        if mb_cls is None:
            return await eod(
                ok,
                "`Can't auto-pick a slot — premium.GetMyBoostsRequest isn't "
                "available. Specify [slot] explicitly.`",
            )
        try:
            my_boosts = await ult.client(mb_cls())
        except Exception as exc:  # noqa: BLE001
            LOGS.debug("boosts: GetMyBoostsRequest failed: %s", exc)
            return await eod(ok, _friendly_error(exc))
        free = [
            mb
            for mb in my_boosts.my_boosts
            if mb.peer is None and not mb.cooldown_until_date
        ]
        if not free:
            return await eod(
                ok,
                "`No free boost slots available. Buy more Premium boosts, "
                "or wait out a cooldown, or specify [slot] explicitly.`",
            )
        slot = free[0].slot

    try:
        await ult.client(apply_cls(peer=entity, slots=[slot]))
    except RPCError as exc:
        LOGS.debug("boosts: ApplyBoostRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("boosts: ApplyBoostRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))
    await eod(ok, f"✅ **Boosted {title}** with slot `{slot}`.")

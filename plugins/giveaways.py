# Ultroid - UserBot
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}giveawayinfo [t.me link]` (or reply to a giveaway message)
    Show the status of a boost giveaway — in progress or finished.

• `{i}giftcode <slug>`
    Check a Telegram gift/Premium code (from a `t.me/giftcode/...` link)
    without redeeming it.

• `{i}giftcoderedeem <slug> [confirm]`
    Redeem a gift/Premium code onto this account. Irreversible — asks for
    a `confirm` re-run before actually applying it.

• `{i}premiumopts [boost_channel]`
    List the real-currency Premium gift-code price tiers Telegram
    currently offers (optionally scoped to a channel you want to boost).

• `{i}giveaway <channel> <winners> stars=<N> [days=<D>]`
    Launch a **Stars-funded** boost giveaway on `<channel>` — pays for it
    out of your own Stars balance. Shows a cost preview first — nothing
    is paid until you re-run the same command with `confirm` appended, or
    run the short `<nonce>` command the preview gives you.

• `{i}giveaway <channel> <winners> <months>`
    Real-currency (App Store/Play Store) Premium giveaways can't be
    originated by this bot — prints clear guidance instead of faking it.
"""

import contextlib
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from telethon.errors import RPCError
from telethon.tl import types as tl_types

from pyUltroid._core.buttonkit import PendingConfirmations
from pyUltroid._core.richmsg import RichBlocks, send_rich
from pyUltroid._core.stars import StarsUnsupportedError
from pyUltroid._core.stars import require_request as _payments_request

from . import HNDLR, ultroid_cmd

# ──────────────────────────────────────────────────────────────────────
# Verified against the pinned fork (layer 230):
#
# Present in ``payments.*``: ``GetGiveawayInfoRequest``, ``CheckGiftCodeRequest``,
# ``ApplyGiftCodeRequest``, ``GetPremiumGiftCodeOptionsRequest``,
# ``GetStarsGiveawayOptionsRequest``, ``LaunchPrepaidGiveawayRequest``,
# ``GetPaymentFormRequest``, ``SendStarsFormRequest``. Also present in
# ``types.*``: ``InputInvoiceStars``, ``InputStorePaymentStarsGiveaway``,
# ``InputStorePaymentPremiumGiveaway``.
#
# ``LaunchPrepaidGiveawayRequest(peer, giveaway_id, purpose)`` needs a
# *prepaid* ``giveaway_id`` that only exists after buying a giveaway slot
# through a real-currency Play Store/App Store purchase — there is no
# MTProto request at this layer that hands out that id, so this module
# cannot originate one. Faking that flow would be a lie to the owner, so
# `.giveaway <channel> <winners> <months>` prints guidance instead of
# pretending to launch anything (see ``_months_guidance``).
#
# What *is* fully real and automatable: an ``InputInvoiceStars`` wrapping
# an ``InputStorePaymentStarsGiveaway`` purpose is a normal Stars invoice —
# the exact same ``GetPaymentFormRequest`` → ``SendStarsFormRequest``
# two-step flow ``pyUltroid/_core/stars.py::send_gift`` already uses for
# star gifts pays it directly out of the account's Stars balance. That is
# what `.giveaway <channel> <winners> stars=<N>` drives — a real Stars
# spend, confirm-gated the same way `plugins/gifts.py` gates its spends.
# ──────────────────────────────────────────────────────────────────────

_PENDING = PendingConfirmations(ttl=300.0)


def _months_guidance(months: str) -> str:
    return (
        f"`Real-currency Premium giveaways ({months} month(s)) need a Play "
        "Store/App Store billing payment method already on file with "
        "Telegram — this bot has no MTProto path to originate that "
        "purchase for you.`\n\n"
        "**What is automatable here:**\n"
        f"• `{HNDLR}giveaway <channel> <winners> stars=<N>` — a "
        "**Stars-funded** giveaway, paid straight from your Stars balance.\n"
        f"• `{HNDLR}premiumopts` — the real-currency pricing table, if "
        "you'd rather buy a Premium giveaway slot yourself in the Telegram "
        "app.\n"
        "• If you already hold a **prepaid** giveaway slot (bought through "
        "an app store), its `giveaway_id` can be launched with "
        "`payments.LaunchPrepaidGiveawayRequest` — not wired up here since "
        "nothing at this fork layer can hand this bot that id on its own."
    )


# ──────────────────────────────────────────────────────────────────────
# .giveawayinfo — payments.GetGiveawayInfoRequest
# ──────────────────────────────────────────────────────────────────────

_TME_RE = re.compile(r"(?:https?://)?t\.me/(c/)?([\w\d_]+)/(\d+)")


async def _resolve_peer_msg(e, arg: str):
    """Return ``(peer, msg_id)`` from a t.me link, or the replied message."""
    match = _TME_RE.search(arg or "")
    if match:
        is_internal, ident, msg_id = match.groups()
        peer: Any = int(f"-100{ident}") if is_internal else ident
        return peer, int(msg_id)
    if e.reply_to_msg_id:
        return e.chat_id, e.reply_to_msg_id
    raise ValueError(
        "Give a t.me link (`t.me/channel/123`) or reply to the giveaway message."
    )


def _render_giveaway_info(info: Any) -> RichBlocks:
    blocks = RichBlocks().header("🎉 Giveaway Info")
    if isinstance(info, tl_types.payments.GiveawayInfoResults):
        rows = [
            ["Status", "Finished"],
            ["Started", str(getattr(info, "start_date", "?"))],
            ["Finished", str(getattr(info, "finish_date", "?"))],
            ["Winners", str(getattr(info, "winners_count", "?"))],
            ["You won", "yes" if getattr(info, "winner", False) else "no"],
            ["Refunded", "yes" if getattr(info, "refunded", False) else "no"],
        ]
        if getattr(info, "stars_prize", None):
            rows.append(["Stars prize", str(info.stars_prize)])
        if getattr(info, "gift_code_slug", None):
            rows.append(["Your gift code", info.gift_code_slug])
        if getattr(info, "activated_count", None) is not None:
            rows.append(["Codes activated", str(info.activated_count)])
    else:
        preparing = getattr(info, "preparing_results", False)
        rows = [
            ["Status", "Preparing results" if preparing else "In progress"],
            ["Started", str(getattr(info, "start_date", "?"))],
            [
                "You're participating",
                "yes" if getattr(info, "participating", False) else "no",
            ],
        ]
        if getattr(info, "joined_too_early_date", None):
            rows.append(["Joined too early", str(info.joined_too_early_date)])
        if getattr(info, "admin_disallowed_chat_id", None):
            rows.append(
                ["Blocked (you admin that chat)", str(info.admin_disallowed_chat_id)]
            )
        if getattr(info, "disallowed_country", None):
            rows.append(["Blocked (country)", info.disallowed_country])
    blocks.table(rows)
    return blocks


@ultroid_cmd(pattern="giveawayinfo( (.*)|$)", owner_only=True)
async def giveawayinfo_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    try:
        peer, msg_id = await _resolve_peer_msg(e, arg)
    except ValueError as exc:
        return await e.eor(f"`{exc}`", time=8)

    try:
        request_type = _payments_request("GetGiveawayInfoRequest")
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=8)

    try:
        entity = await e.client.get_input_entity(peer)
    except (ValueError, TypeError) as exc:
        return await e.eor(f"`Could not resolve that chat: {exc}`", time=8)

    try:
        info = await e.client(request_type(peer=entity, msg_id=msg_id))
    except RPCError as exc:
        return await e.eor(f"`Could not fetch giveaway info: {exc}`", time=8)

    await send_rich(e.client, e.chat_id, _render_giveaway_info(info), reply_to=e)


# ──────────────────────────────────────────────────────────────────────
# .giftcode / .giftcoderedeem — payments.CheckGiftCodeRequest / ApplyGiftCodeRequest
# ──────────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern="giftcode( (.*)|$)", owner_only=True)
async def giftcode_check_cmd(e):
    slug = (e.pattern_match.group(1) or "").strip().split(maxsplit=1)
    slug = slug[0] if slug else ""
    if not slug:
        return await e.eor("`Usage: .giftcode <slug>`", time=6)

    try:
        request_type = _payments_request("CheckGiftCodeRequest")
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=8)

    try:
        info = await e.client(request_type(slug=slug))
    except RPCError as exc:
        return await e.eor(f"`Could not check that gift code: {exc}`", time=8)

    rows = [
        ["Days", str(getattr(info, "days", "?"))],
        ["Via giveaway", "yes" if getattr(info, "via_giveaway", False) else "no"],
        ["Used", str(getattr(info, "used_date", None) or "not yet")],
        ["Date", str(getattr(info, "date", "?"))],
    ]
    if getattr(info, "giveaway_msg_id", None):
        rows.append(["Giveaway msg id", str(info.giveaway_msg_id)])
    blocks = (
        RichBlocks()
        .header(f"🎁 Gift code `{slug}`")
        .table(rows)
        .paragraph(
            f"`{HNDLR}giftcoderedeem {slug} confirm` to redeem it onto this account."
        )
    )
    await send_rich(e.client, e.chat_id, blocks, reply_to=e)


@ultroid_cmd(pattern="giftcoderedeem( (.*)|$)", owner_only=True)
async def giftcode_redeem_cmd(e):
    tokens = (e.pattern_match.group(1) or "").strip().split()
    confirmed = bool(tokens) and tokens[-1].lower() == "confirm"
    if confirmed:
        tokens = tokens[:-1]
    if not tokens:
        return await e.eor("`Usage: .giftcoderedeem <slug> [confirm]`", time=6)
    slug = tokens[0]

    if not confirmed:
        return await e.eor(
            f"**Redeem gift code** `{slug}` **onto this account?**\n"
            "_This is irreversible._ Re-run: `.giftcoderedeem "
            f"{slug} confirm`",
            time=15,
        )

    try:
        request_type = _payments_request("ApplyGiftCodeRequest")
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=8)

    try:
        await e.client(request_type(slug=slug))
    except RPCError as exc:
        return await e.eor(f"`Could not redeem that gift code: {exc}`", time=8)
    await e.eor(f"✅ Redeemed gift code `{slug}`.", time=8)


# ──────────────────────────────────────────────────────────────────────
# .premiumopts — payments.GetPremiumGiftCodeOptionsRequest
# ──────────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern="premiumopts( (.*)|$)", owner_only=True)
async def premiumopts_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    boost_peer = None
    if arg:
        try:
            boost_peer = await e.client.get_input_entity(arg)
        except (ValueError, TypeError) as exc:
            return await e.eor(f"`Could not resolve {arg!r}: {exc}`", time=8)

    try:
        request_type = _payments_request("GetPremiumGiftCodeOptionsRequest")
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=8)

    try:
        result = await e.client(request_type(boost_peer=boost_peer))
    except RPCError as exc:
        return await e.eor(
            f"`Could not fetch Premium gift-code options: {exc}`", time=8
        )

    options: list[Any] = (
        result
        if isinstance(result, (list, tuple))
        else list(getattr(result, "options", None) or [])
    )
    if not options:
        return await e.eor(
            "`No Premium gift-code options are on offer right now.`", time=6
        )

    rows = [["Users", "Months", "Price"]]
    for opt in options:
        price = f"{getattr(opt, 'amount', 0) / 100:.2f} {getattr(opt, 'currency', '')}".strip()
        rows.append(
            [str(getattr(opt, "users", "?")), str(getattr(opt, "months", "?")), price]
        )
    blocks = (
        RichBlocks()
        .header("💎 Premium Gift-Code Options")
        .table(rows, caption=str(arg) if arg else "account-wide")
    )
    await send_rich(e.client, e.chat_id, blocks, reply_to=e)


# ──────────────────────────────────────────────────────────────────────
# .giveaway — Stars-funded launch (real spend, confirm-gated) or
# clear guidance for the real-currency (months) path
# ──────────────────────────────────────────────────────────────────────

_GIVEAWAY_USAGE = (
    "`Usage: .giveaway <channel> <winners> stars=<N> [days=<D>]`\n"
    "or `.giveaway <channel> <winners> <months>` for guidance on the "
    "real-currency path (not automatable here)."
)


def _pick_stars_option(options: list[Any], target_stars: int, target_winners: int):
    if not options:
        return None, None
    best_opt = min(options, key=lambda o: abs(getattr(o, "stars", 0) - target_stars))
    winners_list = getattr(best_opt, "winners", None) or []
    if not winners_list:
        return best_opt, None
    best_winner = min(
        winners_list, key=lambda w: abs(getattr(w, "users", 0) - target_winners)
    )
    return best_opt, best_winner


async def _do_launch_giveaway(
    e: Any,
    boost_peer: Any,
    option: Any,
    winner_opt: Any,
    until_date: datetime,
    channel_label: str,
) -> None:
    try:
        get_form = _payments_request("GetPaymentFormRequest")
        send_form = _payments_request("SendStarsFormRequest")
        purpose_cls = _payments_request(
            "InputStorePaymentStarsGiveaway", module="types"
        )
        invoice_cls = _payments_request("InputInvoiceStars", module="types")
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=10)

    purpose = purpose_cls(
        stars=getattr(option, "stars", 0),
        boost_peer=boost_peer,
        until_date=until_date,
        currency=getattr(option, "currency", "USD"),
        amount=getattr(option, "amount", 0),
        users=getattr(winner_opt, "users", 0),
    )
    invoice = invoice_cls(purpose=purpose)
    try:
        form = await e.client(get_form(invoice=invoice))
        result = await e.client(send_form(form_id=form.form_id, invoice=invoice))
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=10)
    except RPCError as exc:
        return await e.eor(f"`Could not launch the giveaway: {exc}`", time=10)

    verification_url = getattr(result, "url", None)
    if verification_url:
        # SECURITY (plan risk #6): never edit the verification URL into
        # the message the command ran in — it can be a public group.
        # Deliver it to Saved Messages only, and never to LOGS/print.
        with contextlib.suppress(Exception):
            await e.client.send_message(
                "me",
                "`Stars giveaway payment needs extra verification — open "
                f"this to finish launching it on {channel_label}:`\n{verification_url}",
            )
        return await e.eor(
            "`Payment needs extra verification — check your Saved Messages.`",
            time=10,
        )
    await e.eor(
        f"✅ Launched a **{getattr(winner_opt, 'users', '?')}-winner** "
        f"Stars giveaway on `{channel_label}`.",
        time=10,
    )


@ultroid_cmd(pattern="giveaway( (.*)|$)", owner_only=True)
async def giveaway_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    tokens = arg.split()
    confirmed = bool(tokens) and tokens[-1].lower() == "confirm"
    if confirmed:
        tokens = tokens[:-1]

    # Shorthand re-run: `.giveaway <nonce>` alone confirms a previously
    # previewed launch without retyping channel/winners/stars/days.
    if not confirmed and len(tokens) == 1:
        pending = _PENDING.pop(tokens[0], e.sender_id)
        if pending is not None and pending["kind"] == "giveaway":
            data = pending["data"]
            return await _do_launch_giveaway(
                e,
                data["boost_peer"],
                data["option"],
                data["winner_opt"],
                data["until_date"],
                data["channel_label"],
            )

    if len(tokens) < 3:
        return await e.eor(_GIVEAWAY_USAGE, time=10)

    channel_tok, winners_tok, plan_tok, *rest = tokens
    try:
        winners = int(winners_tok)
    except ValueError:
        return await e.eor("`<winners> must be a whole number.`", time=8)

    days = 3
    for tok in rest:
        if tok.lower().startswith("days="):
            with contextlib.suppress(ValueError):
                days = max(1, int(tok.split("=", 1)[1]))

    plan_tok_l = plan_tok.lower()
    if not plan_tok_l.startswith("stars="):
        return await e.eor(_months_guidance(plan_tok), time=25)

    try:
        target_stars = int(plan_tok_l.split("=", 1)[1])
    except ValueError:
        return await e.eor("`stars=<N> must be a whole number.`", time=8)

    try:
        boost_peer = await e.client.get_input_entity(channel_tok)
    except (ValueError, TypeError) as exc:
        return await e.eor(f"`Could not resolve {channel_tok!r}: {exc}`", time=8)

    try:
        options_req = _payments_request("GetStarsGiveawayOptionsRequest")
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=8)

    try:
        result = await e.client(options_req())
    except RPCError as exc:
        return await e.eor(f"`Could not fetch Stars-giveaway options: {exc}`", time=8)

    options: list[Any] = (
        result
        if isinstance(result, (list, tuple))
        else list(getattr(result, "options", None) or [])
    )
    option, winner_opt = _pick_stars_option(options, target_stars, winners)
    if option is None or winner_opt is None:
        return await e.eor(
            "`No matching Stars-giveaway tier is on offer right now.`", time=8
        )

    until_date = datetime.now(timezone.utc) + timedelta(days=days)

    if not confirmed:
        nonce = _PENDING.stash(
            e.sender_id,
            "giveaway",
            boost_peer=boost_peer,
            option=option,
            winner_opt=winner_opt,
            until_date=until_date,
            channel_label=channel_tok,
        )
        price = f"{getattr(option, 'amount', 0) / 100:.2f} {getattr(option, 'currency', '')}"
        preview = (
            f"**Launch a Stars giveaway on** `{channel_tok}`**?**\n"
            f"• Winners: **{winner_opt.users}** (asked for {winners})\n"
            f"• Stars pool: **{option.stars} ⭐** (asked for {target_stars})\n"
            f"• Per-winner: **{winner_opt.per_user_stars} ⭐**\n"
            f"• Ends: `{until_date.isoformat()}`\n"
            f"• Reference price: {price}\n\n"
            "_This spends real Telegram Stars from your balance._ Nothing "
            "is paid yet — re-run this exact command with `confirm` "
            f"appended, or just run `{HNDLR}giveaway {nonce}`."
        )
        return await e.eor(preview, time=25)

    await _do_launch_giveaway(
        e, boost_peer, option, winner_opt, until_date, channel_tok
    )

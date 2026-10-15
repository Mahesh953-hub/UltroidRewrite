# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}stars [peer]`
    Show the Stars wallet dashboard (balance + recent transactions) for
    yourself, or for a channel/bot you own (`[peer]`).

• `{i}starstopup`
    List the Stars top-up options Telegram currently offers this account.
    Informational only — buying Stars needs Telegram's own in-app purchase
    flow (Apple/Google IAP), which this userbot cannot drive; use the
    linked button to open @PremiumBot instead.

• `{i}starsrev [peer]` (reply to a message also works)
    Show Stars-revenue stats for a channel/bot you own.

• `{i}starswithdraw`
    Withdrawal **instructions only** — this command never touches the
    withdrawal RPC, never asks for your 2FA password, and never prints a
    withdrawal URL anywhere. Real money, real risk: do it from Telegram's
    own client.

• `{i}pdon <stars>`
    Enable "pay to message me": non-contacts must pay `<stars>` Stars to
    send you a message (`account.SetGlobalPrivacySettingsRequest`).

• `{i}pdon <stars>` (reply to a user's message)
    Force *that one user* to pay to message you, overriding your global
    setting (`account.ToggleNoPaidMessagesExceptionRequest`).

• `{i}pdon <stars> <text>`
    Send `<text>` to the current chat authorizing a spend of up to
    `<stars>` Stars to deliver it (`SendMessageRequest.allow_paid_stars`) —
    used when the recipient charges for messages from you. Real money —
    shows a preview (recipient/message/max charge) first; nothing is
    sent until you re-run the exact command with `confirm` appended, or
    just run `{i}pdon <nonce>` from the preview.

• `{i}pdoff`
    Disable "pay to message me" account-wide (sets the price to 0).

• `{i}pdoff` (reply to a user's message)
    Exempt that one user from paying to message you again.

• `{i}pdrefund <charge_id>` (reply to the payer's message)
    Refund a Stars charge (`payments.RefundStarsChargeRequest`).

• `{i}pdrefund <user> <charge_id>`
    Same, without a reply — `<user>` is a username/id/"me".
"""


import os

from telethon.errors import RPCError
from telethon.tl import functions, types
from telethon.utils import get_display_name

from pyUltroid._core import stars as starscore
from pyUltroid._core.buttonkit import PendingConfirmations, button
from pyUltroid._core.richmsg import RichBlocks, send_rich, table_to_text
from pyUltroid._core.stars import StarsUnsupportedError

from ._rebrand import ui_cards_enabled
from . import HNDLR, get_string, ultroid_cmd

# ──────────────────────────────────────────────────────────────────────
# Design notes (read before touching the paid-messages surface below)
# ──────────────────────────────────────────────────────────────────────
#
# Verified against the pinned fork (/code/Mahesh953-hub/Telethon,
# layer 228):
#
# * ``messages.SendMessageRequest.allow_paid_stars`` is an ``Optional[int]``
#   (Stars the *sender* pre-authorizes spending on this one message), NOT
#   the boolean the plan sketch ("allow_paid_stars=True") suggested. It is
#   set by whoever is *paying* to deliver a message to a peer that charges
#   for messages — it is not how you turn charging on for your own inbox.
# * Turning paid messages ON for *your own* account (so non-contacts pay to
#   message you) is a completely different, account-wide RPC:
#   ``account.GetGlobalPrivacySettingsRequest`` /
#   ``account.SetGlobalPrivacySettingsRequest(GlobalPrivacySettings(
#   noncontact_peers_paid_stars=<stars>))``. The plan's B2 sketch named only
#   ``allow_paid_stars``/``ToggleNoPaidMessagesExceptionRequest`` for this —
#   neither actually sets your own price; ``GlobalPrivacySettings`` does.
#   This module implements the real mechanism and documents the gap here
#   rather than silently deviating from the plan without a note.
# * ``account.ToggleNoPaidMessagesExceptionRequest(user_id, refund_charged=,
#   require_payment=, parent_peer=)`` is a *per-user exception* to whatever
#   your global setting is — ``require_payment=True`` forces one specific
#   user to pay even if they'd otherwise be free (e.g. a contact);
#   ``require_payment=False`` exempts one user even if strangers generally
#   pay. It does not take a Stars amount — the price is always your global
#   ``noncontact_peers_paid_stars``.
# * ``payments.RefundStarsChargeRequest(user_id, charge_id)`` — confirmed
#   two-argument signature, no amount (refunds are always full).
#
# `.pdon`/`.pdoff` below dispatch on args/reply to cover all three real
# surfaces; see each command's docstring for the exact mapping.


# ──────────────────────────────────────────────────────────────────────
# small shared helpers
# ──────────────────────────────────────────────────────────────────────


def _parse_peer(arg: str):
    """Turn a raw ``[peer]`` token into something ``get_input_entity`` likes."""
    arg = (arg or "").strip().lstrip("@")
    if not arg:
        return "me"
    if arg.lstrip("-").isdigit():
        return int(arg)
    return arg


def _fmt_stars(amount) -> str:
    """Format a ``StarsAmount`` (``amount``/``nanos``) as ``+N``/``-N`` XTR."""
    if amount is None:
        return "0"
    whole = getattr(amount, "amount", 0) or 0
    nanos = getattr(amount, "nanos", 0) or 0
    if nanos:
        frac = abs(nanos) / 1_000_000_000
        value = whole + (frac if whole >= 0 else -frac)
        return f"{value:+.2f}"
    return f"{whole:+d}" if whole else "0"


def _tx_peer_label(tx) -> str:
    """Best-effort human label for a ``StarsTransaction.peer`` variant."""
    peer = getattr(tx, "peer", None)
    name = type(peer).__name__ if peer is not None else ""
    labels = {
        "StarsTransactionPeerAppStore": "App Store",
        "StarsTransactionPeerPlayMarket": "Play Market",
        "StarsTransactionPeerFragment": "Fragment",
        "StarsTransactionPeerPremiumBot": "@PremiumBot",
        "StarsTransactionPeerAds": "Ads",
        "StarsTransactionPeerAPI": "API",
        "StarsTransactionPeerUnsupported": "Unsupported",
    }
    if name in labels:
        return labels[name]
    inner = getattr(peer, "peer", None)
    if inner is not None:
        try:
            from telethon import utils as tl_utils

            return str(tl_utils.get_peer_id(inner))
        except Exception:
            pass
    return getattr(tx, "title", None) or "—"


def _wallet_rows(history, limit: int = 10):
    rows = [
        [
            get_string("stars_col_date"),
            get_string("stars_col_delta"),
            get_string("stars_col_from_to"),
            get_string("stars_col_note"),
        ]
    ]
    for tx in (history or [])[:limit]:
        date = getattr(tx, "date", None)
        date_s = date.strftime("%Y-%m-%d %H:%M") if date else "—"
        note = getattr(tx, "title", None) or getattr(tx, "description", None) or ""
        if getattr(tx, "refund", False):
            note = f"{note} (refund)".strip()
        if getattr(tx, "gift", False):
            note = f"{note} (gift)".strip()
        rows.append(
            [
                date_s,
                _fmt_stars(getattr(tx, "amount", None)),
                _tx_peer_label(tx),
                note or "—",
            ]
        )
    return rows


async def _render(
    event, header: str, rows_paragraphs, table=None, table_caption=None, buttons=None
):
    """Render a small dashboard either as a rich card or as clean mdv3 text.

    ``rows_paragraphs`` is a list of plain paragraph lines shown under the
    header (balance, settings summary, ...). ``table`` (optional) is a
    ``[[...header...], [...row...], ...]`` matrix rendered as a table on
    the rich-card path and as a monospace grid on the text path.
    """
    if ui_cards_enabled():
        blocks = RichBlocks().header(header)
        for line in rows_paragraphs:
            blocks.paragraph(line)
        if table and len(table) > 1:
            blocks.table(table, is_striped=True, caption=table_caption)
        elif table is not None:
            blocks.paragraph(get_string("stars_no_transactions"))
        return await send_rich(
            event.client, event.chat_id, blocks, reply_to=event, buttons=buttons
        )

    head = "\n".join([f"**{header}**", ""] + list(rows_paragraphs))
    if table and len(table) > 1:
        text = (
            f"{head}\n\n```\n" + table_to_text(table, caption=table_caption) + "\n```"
        )
    elif table is not None:
        text = f"{head}\n\n`{get_string('stars_no_transactions')}`"
    else:
        text = head
    return await event.eor(text, buttons=buttons)


# ──────────────────────────────────────────────────────────────────────
# .stars — wallet dashboard (B1)
# ──────────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern=r"stars(?:\s+(\S+))?$", owner_only=True)
async def stars_wallet_cmd(e):
    peer = _parse_peer(e.pattern_match.group(1))
    label = "you" if peer == "me" else str(peer)
    msg = await e.eor("`Fetching Stars wallet...`")
    try:
        status = await starscore.wallet(e.client, peer)
        history_status = await starscore.transactions(e.client, peer, limit=10)
    except StarsUnsupportedError as exc:
        return await msg.edit(f"`Stars wallet is unsupported here: {exc}`")
    except RPCError as exc:
        return await msg.edit(f"`Could not fetch Stars wallet: {exc}`")

    balance = getattr(status, "balance", None)
    history = getattr(history_status, "history", None) or []
    rows = _wallet_rows(history)
    await msg.try_delete()
    await _render(
        e,
        get_string("stars_wallet_header").format(label),
        [get_string("stars_balance_label").format(_fmt_stars(balance))],
        table=rows,
        table_caption=get_string("stars_recent_tx_caption"),
    )


# ──────────────────────────────────────────────────────────────────────
# .starstopup — top-up options (B1)
# ──────────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern=r"starstopup$", owner_only=True)
async def stars_topup_cmd(e):
    msg = await e.eor("`Fetching top-up options...`")
    try:
        options = await starscore.topup_options(e.client)
    except StarsUnsupportedError as exc:
        return await msg.edit(f"`Stars top-up is unsupported here: {exc}`")
    except RPCError as exc:
        return await msg.edit(f"`Could not fetch top-up options: {exc}`")

    if not options:
        return await msg.edit("`No top-up options offered right now.`")

    rows = [[get_string("stars_col_stars"), get_string("stars_col_price")]]
    for opt in options:
        price = (
            f"{opt.amount / 100:.2f} {opt.currency}"
            if opt.currency
            else str(opt.amount)
        )
        rows.append([f"⭐ {opt.stars}", price])

    buttons = [
        [
            button(
                get_string("btn_buy_premiumbot"),
                url="https://t.me/PremiumBot",
                preset="primary",
            )
        ]
    ]
    await msg.try_delete()
    await _render(
        e,
        get_string("stars_topup_header"),
        [get_string("stars_topup_info")],
        table=rows,
        table_caption=get_string("stars_topup_caption"),
        buttons=buttons,
    )


# ──────────────────────────────────────────────────────────────────────
# .starsrev — revenue stats (B1)
# ──────────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern=r"starsrev(?:\s+(\S+))?$", owner_only=True)
async def stars_revenue_cmd(e):
    arg = e.pattern_match.group(1)
    if arg:
        peer = _parse_peer(arg)
    elif e.reply_to_msg_id:
        reply = await e.get_reply_message()
        peer = reply.sender_id if reply else "me"
    else:
        peer = "me"
    label = "you" if peer == "me" else str(peer)

    msg = await e.eor("`Fetching Stars revenue stats...`")
    try:
        stats = await starscore.revenue_stats(e.client, peer)
    except StarsUnsupportedError as exc:
        return await msg.edit(f"`Stars revenue stats are unsupported here: {exc}`")
    except RPCError as exc:
        return await msg.edit(f"`Could not fetch revenue stats: {exc}`")

    status = getattr(stats, "status", None)
    rows = [
        [get_string("stars_col_field"), get_string("stars_col_value")],
        [
            get_string("stars_current_balance"),
            _fmt_stars(getattr(status, "current_balance", None)),
        ],
        [
            get_string("stars_available_balance"),
            _fmt_stars(getattr(status, "available_balance", None)),
        ],
        [
            get_string("stars_overall_revenue"),
            _fmt_stars(getattr(status, "overall_revenue", None)),
        ],
        [
            get_string("stars_withdrawal_enabled"),
            str(bool(getattr(status, "withdrawal_enabled", False))),
        ],
        [get_string("stars_usd_rate"), str(getattr(stats, "usd_rate", "—"))],
    ]
    await msg.try_delete()
    await _render(
        e,
        get_string("stars_revenue_header").format(label),
        [],
        table=rows,
        table_caption=get_string("stars_revenue_caption"),
    )


# ──────────────────────────────────────────────────────────────────────
# .starswithdraw — instructions only (plan risk #6, NEVER automated)
# ──────────────────────────────────────────────────────────────────────
#
# This command deliberately never calls pyUltroid._core.stars.withdraw_url.
# It never asks for, holds, logs, or prints a 2FA password, and never
# echoes a withdrawal URL anywhere — including here in Saved Messages.
# It only ever returns generic, static instructions. If a reply is ever
# added later to actually fetch a withdrawal URL, plan risk #6 requires
# that reply happen in Saved Messages/private chat only, and the URL must
# never be logged.


@ultroid_cmd(pattern=r"starswithdraw$", owner_only=True)
async def stars_withdraw_cmd(e):
    text = (
        "**⭐ Stars Withdrawal**\n\n"
        "This command does **not** automate withdrawals — real money and "
        "your 2FA password are involved, and this bot will never ask for "
        "either in a chat message.\n\n"
        "To withdraw Stars revenue, use the **official Telegram app**:\n"
        "`Settings → Telegram Stars → your channel/bot → Withdraw`\n\n"
        "Check current eligibility/balance with `.starsrev`."
    )
    if e.is_private:
        return await e.eor(text)
    # Guardrail: never place withdrawal-adjacent replies in a group, even
    # generic instructions with no secret in them — reply in Saved
    # Messages instead and just point to it here.
    try:
        await e.client.send_message("me", text)
    except RPCError:
        pass
    await e.eor("`Withdrawal instructions sent to Saved Messages.`", time=8)


# ──────────────────────────────────────────────────────────────────────
# .pdon / .pdoff / .pdrefund — paid posts (B2)
# ──────────────────────────────────────────────────────────────────────
#
# Real-money safety (plan risk #6): `.pdon <stars> <text>` spends the
# account's own Stars to authorize delivery of a paid message — it goes
# through the same text-based confirm gate `plugins/gifts.py`/
# `plugins/giveaways.py` use: the first invocation only previews the
# recipient/message/max charge and stashes it here; re-running the exact
# command with a trailing `confirm` token, or just running `.pdon <nonce>`
# from the preview, actually sends it. The `.pdon <stars>` (global) and
# `.pdon <stars>` (reply, per-user exception) variants below are NOT
# gated — they only change this account's own willingness to receive
# paid messages, they never spend Stars.

_PENDING = PendingConfirmations(ttl=300.0)


async def _global_privacy(client):
    request_type = functions.account.GetGlobalPrivacySettingsRequest
    return await client(request_type())


async def _set_noncontact_price(client, stars: int):
    """Read-modify-write ``noncontact_peers_paid_stars`` without clobbering
    the account's other ``GlobalPrivacySettings`` fields."""
    current = await _global_privacy(client)
    settings = types.GlobalPrivacySettings(
        archive_and_mute_new_noncontact_peers=getattr(
            current, "archive_and_mute_new_noncontact_peers", None
        ),
        keep_archived_unmuted=getattr(current, "keep_archived_unmuted", None),
        keep_archived_folders=getattr(current, "keep_archived_folders", None),
        hide_read_marks=getattr(current, "hide_read_marks", None),
        new_noncontact_peers_require_premium=getattr(
            current, "new_noncontact_peers_require_premium", None
        ),
        display_gifts_button=getattr(current, "display_gifts_button", None),
        noncontact_peers_paid_stars=stars,
        disallowed_gifts=getattr(current, "disallowed_gifts", None),
    )
    request_type = functions.account.SetGlobalPrivacySettingsRequest
    return await client(request_type(settings=settings))


async def _toggle_user_exception(client, user_id, *, require_payment: bool):
    request_type = functions.account.ToggleNoPaidMessagesExceptionRequest
    return await client(request_type(user_id=user_id, require_payment=require_payment))


async def _send_allow_paid_stars(event, stars: int, text: str):
    """Send *text* with ``allow_paid_stars=stars``; return the sent Message
    when the client can resolve one (so callers can read back the
    server-confirmed ``paid_message_stars``), else the raw Updates."""
    request_type = functions.messages.SendMessageRequest
    peer = await event.client.get_input_entity(event.chat_id)
    reply_to = None
    if event.reply_to_msg_id:
        reply_to_cls = getattr(types, "InputReplyToMessage", None)
        if reply_to_cls is not None:
            reply_to = reply_to_cls(event.reply_to_msg_id)
    request = request_type(
        peer=peer,
        message=text,
        allow_paid_stars=stars,
        reply_to=reply_to,
        random_id=int.from_bytes(os.urandom(8), "big", signed=True),
    )
    result = await event.client(request)
    getter = getattr(event.client, "_get_response_message", None)
    if getter is not None:
        try:
            return getter(request, result, peer)
        except Exception:  # pragma: no cover - defensive
            pass
    return result


async def _finish_pdon_send(e, stars: int, text: str):
    """Actually spend Stars to deliver *text* — only ever called once the
    caller has confirmed (fresh ``confirm`` token or a re-run nonce)."""
    try:
        sent = await _send_allow_paid_stars(e, stars, text)
    except StarsUnsupportedError as exc:
        return await e.eor(f"`Paid messages are unsupported here: {exc}`", time=8)
    except RPCError as exc:
        return await e.eor(f"`Could not send the paid message: {exc}`", time=8)

    charged = getattr(sent, "paid_message_stars", None)
    state_lines = [f"Sent, authorizing up to **{stars}** ⭐ to deliver it."]
    if charged is not None:
        state_lines.append(f"Server reports `paid_message_stars={charged}`.")
    await _render(e, "💌 Paid Messages — ON", state_lines)


@ultroid_cmd(pattern=r"pdon(?:\s+(\S+))?(?:\s+([\s\S]*))?$", owner_only=True)
async def pd_on_cmd(e):
    stars_raw, text = e.pattern_match.group(1), e.pattern_match.group(2)
    if not stars_raw:
        return await e.eor(
            "`Usage: .pdon <stars>` (global) · `.pdon <stars>` as a reply "
            "(per-user) · `.pdon <stars> <text>` (send a paid message).",
            time=8,
        )

    # Shorthand re-run: `.pdon <nonce>` alone confirms a previously
    # previewed paid-message send without retyping stars/text. Only a
    # bare single token (no trailing text at all) can be a nonce; a real
    # `<stars>` amount will simply miss the pending lookup and fall
    # through to the normal numeric parsing below.
    if text is None:
        pending = _PENDING.pop(stars_raw, e.sender_id)
        if pending is not None and pending["kind"] == "pdon_send":
            data = pending["data"]
            return await _finish_pdon_send(e, data["stars"], data["text"])

    try:
        stars = int(stars_raw)
        if stars < 0:
            raise ValueError
    except ValueError:
        return await e.eor("`<stars> must be a non-negative whole number.`", time=6)

    tokens = (text or "").split()
    confirmed = bool(tokens) and tokens[-1].lower() == "confirm"
    if confirmed:
        tokens = tokens[:-1]
    text = " ".join(tokens)

    if text:
        # Real-money branch — spends this account's own Stars. Gated.
        if not confirmed:
            nonce = _PENDING.stash(e.sender_id, "pdon_send", stars=stars, text=text)
            try:
                chat = await e.get_chat()
                who_name = get_display_name(chat) or str(e.chat_id)
            except Exception:  # noqa: BLE001 - display-only, never fatal
                who_name = str(e.chat_id)
            preview = (
                f"**Send a paid message to {who_name}?**\n"
                f"Message: _{text}_\n"
                f"Max charge: up to **{stars}** ⭐\n\n"
                "_This spends real Telegram Stars._ Nothing is paid yet — "
                "re-run this exact command with `confirm` appended, or just "
                f"run `{HNDLR}pdon {nonce}`."
            )
            return await e.eor(preview, time=25)

        return await _finish_pdon_send(e, stars, text)

    try:
        if e.reply_to_msg_id:
            reply = await e.get_reply_message()
            if not reply or not reply.sender_id:
                return await e.eor("`Could not resolve a user from the reply.`", time=6)
            await _toggle_user_exception(
                e.client, reply.sender_id, require_payment=True
            )
            who = get_display_name(await e.client.get_entity(reply.sender_id))
            state_lines = [
                f"**{who}** must now pay to message you (per-user exception)."
            ]
        else:
            await _set_noncontact_price(e.client, stars)
            state_lines = [
                f"Non-contacts must now pay **{stars}** ⭐ per message (account-wide)."
            ]
    except StarsUnsupportedError as exc:
        return await e.eor(f"`Paid messages are unsupported here: {exc}`", time=8)
    except RPCError as exc:
        return await e.eor(f"`Could not enable paid messages: {exc}`", time=8)

    await _render(e, "💌 Paid Messages — ON", state_lines)


@ultroid_cmd(pattern=r"pdoff$", owner_only=True)
async def pd_off_cmd(e):
    try:
        if e.reply_to_msg_id:
            reply = await e.get_reply_message()
            if not reply or not reply.sender_id:
                return await e.eor("`Could not resolve a user from the reply.`", time=6)
            await _toggle_user_exception(
                e.client, reply.sender_id, require_payment=False
            )
            who = get_display_name(await e.client.get_entity(reply.sender_id))
            state_lines = [f"**{who}** can message you for free again."]
        else:
            await _set_noncontact_price(e.client, 0)
            state_lines = [
                "Non-contacts can message you for free again (account-wide)."
            ]
    except StarsUnsupportedError as exc:
        return await e.eor(f"`Paid messages are unsupported here: {exc}`", time=8)
    except RPCError as exc:
        return await e.eor(f"`Could not disable paid messages: {exc}`", time=8)

    await _render(e, "💌 Paid Messages — OFF", state_lines)


@ultroid_cmd(pattern=r"pdrefund(?:\s+(\S+))?(?:\s+(\S+))?$", owner_only=True)
async def pd_refund_cmd(e):
    first, second = e.pattern_match.group(1), e.pattern_match.group(2)
    if e.reply_to_msg_id:
        reply = await e.get_reply_message()
        if not reply or not reply.sender_id:
            return await e.eor("`Could not resolve the payer from the reply.`", time=6)
        user = reply.sender_id
        charge_id = first
    else:
        if not first or not second:
            return await e.eor(
                "`Usage: .pdrefund <charge_id>` (reply to the payer) or "
                "`.pdrefund <user> <charge_id>`.",
                time=8,
            )
        user = _parse_peer(first)
        charge_id = second

    if not charge_id:
        return await e.eor("`Missing <charge_id>.`", time=6)

    try:
        request_type = functions.payments.RefundStarsChargeRequest
        user_entity = await e.client.get_input_entity(user)
        await e.client(request_type(user_id=user_entity, charge_id=charge_id))
    except RPCError as exc:
        return await e.eor(f"`Could not refund charge '{charge_id}': {exc}`", time=8)

    await _render(e, "💸 Stars Refund", [f"Refunded charge `{charge_id}`."])

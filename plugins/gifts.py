# Ultroid - UserBot
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}gifts [page]`
    Browse the Star Gift catalog (id, ⭐ price, availability, flags).

• `{i}gift <user> <gift_id> [-anon] [message...]`
    Buy and send a star gift to `<user>` (or reply to their message and
    just give the `<gift_id>`). Shows a cost preview first — nothing is
    paid until you re-run the exact same command with `confirm` appended,
    or run the short `<nonce>` command the preview gives you. `-anon`
    hides your name from the recipient.

• `{i}giftsaved [user]`
    List the star gifts saved/displayed on your profile (or `<user>`'s).

• `{i}giftupgrade <ref> [confirm]`
    Upgrade a saved gift (message id, slug, or the `Ref` column from
    `{i}giftsaved`) to its unique collectible form. Confirmation gated
    the same way as `{i}gift` — this can cost Stars.

• `{i}gifttransfer <ref> <user> [confirm]`
    Transfer a saved gift to another user. Same confirmation gate.

• `{i}giftsave <ref> [unsave]`
    Show (default) or hide a saved gift on your profile. Free — no
    confirmation needed.
"""

import contextlib
from typing import Any, Dict, List, Optional

from telethon.errors import RPCError
from telethon.tl import types as tl_types
from telethon.utils import get_display_name

from pyUltroid._core.buttonkit import PendingConfirmations
from pyUltroid._core.richmsg import RichBlocks, send_rich
from pyUltroid._core.stars import StarsUnsupportedError
from pyUltroid._core.stars import require_request as _payments_request
from pyUltroid._core.stars import save as _save_gift
from pyUltroid._core.stars import send_gift as _send_gift
from pyUltroid._core.stars import transfer as _transfer_gift
from pyUltroid._core.stars import upgrade as _upgrade_gift

from . import HNDLR, get_string, ultroid_cmd

# ──────────────────────────────────────────────────────────────────────
# Real-money safety (plan risk #6): every command that can spend Stars
# (`.gift`, `.giftupgrade`, `.gifttransfer`) goes through a text-based
# confirmation step before the payment RPC ever fires — re-running the
# exact command with a trailing `confirm` token, or re-running with just
# the short `<nonce>` the preview gave you (handy for scripting, and
# avoids retyping a long message). Inline Confirm/Cancel *buttons* are
# deliberately NOT used here: this plugin sends from the **user**
# account, and Telegram rejects an inline keyboard on a non-bot send
# (`BOT_METHOD_INVALID`) — a callback registered on the assistant bot
# could never fire for it anyway. Payment tokens/URLs returned by the
# fork (e.g. `payments.PaymentVerificationNeeded.url`) are only ever
# delivered to Saved Messages ('me'), never to the chat the command ran
# in (which may be a public group) and never to LOGS/print — the same
# rule `pyUltroid/_core/stars.py` documents for `withdraw_url`.
# ──────────────────────────────────────────────────────────────────────

_PENDING = PendingConfirmations(ttl=300.0)


def _parse_gift_ref(token: str):
    """A saved-gift ``ref`` is a message id (``int``) or a slug (``str``)."""
    token = token.strip()
    if not token:
        return None
    if token.lstrip("-").isdigit():
        return int(token)
    return token


def _describe_gift(gift: Any) -> dict[str, str]:
    """Render one ``StarGift``/``StarGiftUnique`` as table-cell strings."""
    if isinstance(gift, tl_types.StarGiftUnique):
        issued = getattr(gift, "availability_issued", "?")
        total = getattr(gift, "availability_total", "?")
        flags = ["unique"]
        if getattr(gift, "require_premium", False):
            flags.append("premium")
        return {
            "id": str(getattr(gift, "id", "?")),
            "label": f"{getattr(gift, 'title', '?')} #{getattr(gift, 'num', '?')}",
            "stars": "-",
            "avail": f"{issued}/{total}",
            "flags": ", ".join(flags),
        }
    remains = getattr(gift, "availability_remains", None)
    total = getattr(gift, "availability_total", None)
    avail = f"{remains}/{total}" if total is not None else "∞"
    flags: list[str] = []
    if getattr(gift, "limited", False):
        flags.append("limited")
    if getattr(gift, "sold_out", False):
        flags.append("sold out")
    if getattr(gift, "birthday", False):
        flags.append("birthday")
    if getattr(gift, "require_premium", False):
        flags.append("premium")
    if getattr(gift, "limited_per_user", False):
        flags.append("per-user cap")
    return {
        "id": str(getattr(gift, "id", "?")),
        "label": getattr(gift, "title", None) or f"Gift #{getattr(gift, 'id', '?')}",
        "stars": str(getattr(gift, "stars", "?")),
        "avail": avail,
        "flags": ", ".join(flags) or "-",
    }


# ──────────────────────────────────────────────────────────────────────
# .gifts — Star Gift catalog (payments.GetStarGiftsRequest)
# ──────────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern="gifts( (.*)|$)", owner_only=True)
async def gifts_catalog_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    try:
        page = max(1, int(arg)) if arg else 1
    except ValueError:
        page = 1

    try:
        request_type = _payments_request("GetStarGiftsRequest")
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=8)

    try:
        result = await e.client(request_type(hash=0))
    except RPCError as exc:
        return await e.eor(f"`Could not fetch the gift catalog: {exc}`", time=8)

    gifts = getattr(result, "gifts", None) or []
    if not gifts:
        return await e.eor(get_string("stars_gift_none_available"), time=6)

    per_page = 15
    total_pages = max(1, (len(gifts) + per_page - 1) // per_page)
    page = min(page, total_pages)
    chunk = gifts[(page - 1) * per_page : page * per_page]

    rows = [
        [
            get_string("stars_gift_col_id"),
            "⭐",
            get_string("stars_gift_col_avail"),
            get_string("stars_gift_col_flags"),
        ]
    ]
    for gift in chunk:
        d = _describe_gift(gift)
        rows.append([d["id"], d["stars"], d["avail"], d["flags"]])

    blocks = (
        RichBlocks()
        .header(get_string("stars_gift_catalog_header"))
        .table(rows, caption=f"Page {page}/{total_pages} — {len(gifts)} gift(s)")
        .paragraph(
            f"`{HNDLR}gift <user> <gift_id> [message]` to send one — costs real "
            "Stars and asks for confirmation first."
        )
    )
    await send_rich(e.client, e.chat_id, blocks, reply_to=e)


# ──────────────────────────────────────────────────────────────────────
# .gift — buy + send a star gift (spends Stars — confirm-gated)
# ──────────────────────────────────────────────────────────────────────

_GIFT_USAGE = (
    "`Usage: .gift <user> <gift_id> [-anon] [message...]`\n"
    "(or reply to the user's message and just give `<gift_id>`)\n"
    "Spends real Telegram Stars — a confirmation step runs first."
)


async def _do_send_gift(
    e: Any,
    target: Any,
    gift_id: int,
    message_text: str | None,
    hidden: bool,
    who_name: str,
) -> None:
    try:
        result = await _send_gift(
            e.client, target, gift_id, text=message_text, hidden=hidden
        )
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=10)
    except RPCError as exc:
        return await e.eor(f"`Could not send the gift: {exc}`", time=10)

    verification_url = getattr(result, "url", None)
    if verification_url:
        # SECURITY (plan risk #6): never edit the verification URL into
        # the message the command ran in — it can be a public group.
        # Deliver it to Saved Messages only, and never to LOGS/print.
        with contextlib.suppress(Exception):
            await e.client.send_message(
                "me",
                "`Star gift payment needs extra verification — open this "
                f"to finish sending gift #{gift_id} to {who_name}:`\n{verification_url}",
            )
        return await e.eor(
            "`Payment needs extra verification — check your Saved Messages.`",
            time=10,
        )
    await e.eor(get_string("stars_gift_sent").format(gift_id, who_name), time=10)


@ultroid_cmd(pattern="gift( (.*)|$)", owner_only=True)
async def gift_send_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    tokens = arg.split()

    confirmed = bool(tokens) and tokens[-1].lower() == "confirm"
    if confirmed:
        tokens = tokens[:-1]

    # Shorthand re-run: `.gift <nonce>` alone confirms a previously
    # previewed send without retyping the user/gift_id/message.
    if not confirmed and len(tokens) == 1:
        pending = _PENDING.pop(tokens[0], e.sender_id)
        if pending is not None and pending["kind"] == "send":
            data = pending["data"]
            return await _do_send_gift(
                e,
                data["target"],
                data["gift_id"],
                data["message"],
                data["hidden"],
                data["who_name"],
            )

    hidden = any(
        t.lower() in ("-anon", "--anon", "-hidden", "--hidden") for t in tokens
    )
    tokens = [
        t for t in tokens if t.lower() not in ("-anon", "--anon", "-hidden", "--hidden")
    ]

    reply = await e.get_reply_message() if e.reply_to_msg_id else None
    if reply:
        target: Any = reply.sender_id
        if not tokens:
            return await e.eor(_GIFT_USAGE, time=8)
        gift_tok, *rest = tokens
        message_text = " ".join(rest) or None
    else:
        if len(tokens) < 2:
            return await e.eor(_GIFT_USAGE, time=8)
        user_tok, gift_tok, *rest = tokens
        try:
            target = await e.client.parse_id(user_tok)
        except Exception as exc:  # noqa: BLE001 - surfaced to the owner, not raised
            return await e.eor(f"`Could not resolve {user_tok!r}: {exc}`", time=8)
        message_text = " ".join(rest) or None

    try:
        gift_id = int(gift_tok)
    except ValueError:
        return await e.eor(
            "`gift_id must be numeric — see .gifts for the catalog.`", time=8
        )

    try:
        who = await e.client.get_entity(target)
        who_name = get_display_name(who) or str(target)
    except Exception:  # noqa: BLE001 - display-only, never fatal
        who_name = str(target)

    if not confirmed:
        nonce = _PENDING.stash(
            e.sender_id,
            "send",
            target=target,
            gift_id=gift_id,
            message=message_text,
            hidden=hidden,
            who_name=who_name,
        )
        preview = (
            f"**Send Star Gift #{gift_id}** to **{who_name}**?\n"
            + (f"Message: _{message_text}_\n" if message_text else "")
            + ("Hidden name: yes\n" if hidden else "")
            + "\n_This spends real Telegram Stars._ Nothing is paid yet — "
            f"re-run this exact command with `confirm` appended, or just run "
            f"`{HNDLR}gift {nonce}`."
        )
        return await e.eor(preview, time=25)

    await _do_send_gift(e, target, gift_id, message_text, hidden, who_name)


# ──────────────────────────────────────────────────────────────────────
# .giftupgrade / .gifttransfer — confirm-gated, may cost Stars
# ──────────────────────────────────────────────────────────────────────


async def _do_upgrade_gift(e: Any, gift_ref: Any) -> None:
    try:
        await _upgrade_gift(e.client, gift_ref)
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=10)
    except RPCError as exc:
        return await e.eor(f"`Could not upgrade the gift: {exc}`", time=10)
    await e.eor(
        f"✅ Upgraded gift `{gift_ref}` to its unique collectible form.", time=10
    )


async def _do_transfer_gift(e: Any, gift_ref: Any, to_peer: Any) -> None:
    try:
        await _transfer_gift(e.client, gift_ref, to_peer)
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=10)
    except RPCError as exc:
        return await e.eor(f"`Could not transfer the gift: {exc}`", time=10)
    await e.eor(f"✅ Transferred gift `{gift_ref}` to `{to_peer}`.", time=10)


@ultroid_cmd(pattern="giftupgrade( (.*)|$)", owner_only=True)
async def gift_upgrade_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    tokens = arg.split()
    confirmed = bool(tokens) and tokens[-1].lower() == "confirm"
    if confirmed:
        tokens = tokens[:-1]

    # Shorthand re-run: `.giftupgrade <nonce>` alone confirms a
    # previously previewed upgrade without retyping the ref.
    if not confirmed and len(tokens) == 1:
        pending = _PENDING.pop(tokens[0], e.sender_id)
        if pending is not None and pending["kind"] == "upgrade":
            return await _do_upgrade_gift(e, pending["data"]["gift"])

    if not tokens:
        return await e.eor(
            "`Usage: .giftupgrade <ref> [confirm]` (see .giftsaved)", time=8
        )

    gift_ref = _parse_gift_ref(tokens[0])
    if gift_ref is None:
        return await e.eor(
            "`Give a saved-gift message id or slug — see .giftsaved.`", time=8
        )

    if not confirmed:
        nonce = _PENDING.stash(e.sender_id, "upgrade", gift=gift_ref)
        return await e.eor(
            f"**Upgrade star gift** `{gift_ref}` **to its unique collectible form?**\n"
            "_This may spend Stars if the gift needs an upgrade fee._\n"
            "Nothing is paid yet — re-run this exact command with `confirm` "
            f"appended, or just run `{HNDLR}giftupgrade {nonce}`.",
            time=20,
        )
    await _do_upgrade_gift(e, gift_ref)


@ultroid_cmd(pattern="gifttransfer( (.*)|$)", owner_only=True)
async def gift_transfer_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    tokens = arg.split()
    confirmed = bool(tokens) and tokens[-1].lower() == "confirm"
    if confirmed:
        tokens = tokens[:-1]

    # Shorthand re-run: `.gifttransfer <nonce>` alone confirms a
    # previously previewed transfer without retyping ref/user.
    if not confirmed and len(tokens) == 1:
        pending = _PENDING.pop(tokens[0], e.sender_id)
        if pending is not None and pending["kind"] == "transfer":
            data = pending["data"]
            return await _do_transfer_gift(e, data["gift"], data["to_peer"])

    if len(tokens) < 2:
        return await e.eor(
            "`Usage: .gifttransfer <ref> <user> [confirm]` (see .giftsaved)", time=8
        )

    gift_ref = _parse_gift_ref(tokens[0])
    if gift_ref is None:
        return await e.eor(
            "`Give a saved-gift message id or slug — see .giftsaved.`", time=8
        )

    try:
        to_peer = await e.client.parse_id(tokens[1])
    except Exception as exc:  # noqa: BLE001
        return await e.eor(f"`Could not resolve {tokens[1]!r}: {exc}`", time=8)

    if not confirmed:
        nonce = _PENDING.stash(e.sender_id, "transfer", gift=gift_ref, to_peer=to_peer)
        return await e.eor(
            f"**Transfer star gift** `{gift_ref}` **to** `{tokens[1]}`**?**\n"
            "_This may cost Stars unless you have Telegram Premium._\n"
            "Nothing is transferred yet — re-run this exact command with "
            f"`confirm` appended, or just run `{HNDLR}gifttransfer {nonce}`.",
            time=20,
        )
    await _do_transfer_gift(e, gift_ref, to_peer)


# ──────────────────────────────────────────────────────────────────────
# .giftsave — free profile show/hide toggle (no Stars, no confirm)
# ──────────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern="giftsave( (.*)|$)", owner_only=True)
async def gift_save_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    tokens = arg.split()
    if not tokens:
        return await e.eor("`Usage: .giftsave <ref> [unsave]` (see .giftsaved)", time=8)

    gift_ref = _parse_gift_ref(tokens[0])
    if gift_ref is None:
        return await e.eor(
            "`Give a saved-gift message id or slug — see .giftsaved.`", time=8
        )
    unsave = len(tokens) > 1 and tokens[1].lower() in ("unsave", "hide", "off")

    try:
        await _save_gift(e.client, gift_ref, unsave=unsave)
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=8)
    except RPCError as exc:
        return await e.eor(f"`Could not update the gift: {exc}`", time=8)
    await e.eor(
        f"`Gift {gift_ref} {'hidden from' if unsave else 'shown on'} your profile.`",
        time=6,
    )


# ──────────────────────────────────────────────────────────────────────
# .giftsaved — list saved gifts (payments.GetSavedStarGiftsRequest)
# ──────────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern="giftsaved( (.*)|$)", owner_only=True)
async def gift_saved_cmd(e):
    arg = (e.pattern_match.group(1) or "").strip()
    peer = arg or "me"

    try:
        request_type = _payments_request("GetSavedStarGiftsRequest")
    except StarsUnsupportedError as exc:
        return await e.eor(f"`{exc}`", time=8)

    try:
        entity = await e.client.get_input_entity(peer)
    except (ValueError, TypeError) as exc:
        return await e.eor(f"`Could not resolve {peer!r}: {exc}`", time=8)

    try:
        result = await e.client(request_type(peer=entity, offset="", limit=50))
    except RPCError as exc:
        return await e.eor(f"`Could not fetch saved gifts: {exc}`", time=8)

    gifts = getattr(result, "gifts", None) or []
    if not gifts:
        return await e.eor(f"`No saved star gifts for {peer}.`", time=6)

    rows = [["Ref", "Gift", "⭐", "Flags"]]
    for saved in gifts:
        ref = getattr(saved, "msg_id", None)
        if ref is None:
            ref = getattr(saved, "saved_id", "?")
        d = _describe_gift(saved.gift)
        flags = []
        if getattr(saved, "can_upgrade", False):
            flags.append("can upgrade")
        if getattr(saved, "name_hidden", False):
            flags.append("hidden name")
        if getattr(saved, "pinned_to_top", False):
            flags.append("pinned")
        rows.append([str(ref), d["label"], d["stars"], ", ".join(flags) or "-"])

    blocks = (
        RichBlocks()
        .header(get_string("stars_gift_saved_header"))
        .table(rows, caption=str(peer))
        .paragraph(
            f"`{HNDLR}giftupgrade <ref>` · `{HNDLR}gifttransfer <ref> <user>` · "
            f"`{HNDLR}giftsave <ref> unsave`"
        )
    )
    await send_rich(e.client, e.chat_id, blocks, reply_to=e)

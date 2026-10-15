# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Stars hub wrapper"""

from __future__ import annotations

import os
from typing import Any, Optional, Union

__all__ = (
    "StarsUnsupportedError",
    "is_supported",
    "require_request",
    "wallet",
    "transactions",
    "topup_options",
    "revenue_stats",
    "withdraw_url",
    "send_invoice",
    "send_gift",
    "can_upgrade",
    "upgrade",
    "transfer",
    "save",
)


class StarsUnsupportedError(RuntimeError):
    """A Stars/gift MTProto request is not present.

    Raised instead of ``AttributeError`` so callers can catch one specific,
    documented exception type and degrade gracefully (e.g. hide a button)
    rather than crash on a bare attribute lookup failure.
    """


def _payments():
    from telethon.tl import functions

    return functions.payments


def _account():
    from telethon.tl import functions

    return functions.account


def _types():
    from telethon.tl import types

    return types


def _require(module: Any, name: str) -> Any:
    request_type = getattr(module, name, None)
    if request_type is None:
        raise StarsUnsupportedError(
            f"{getattr(module, '__name__', module)}.{name} is unsupported."
        )
    return request_type


def is_supported(name: str, *, module: str = "payments") -> bool:
    """Whether the raw MTProto request/type ``name`` exists in ``module``."""
    mod = _account() if module == "account" else _payments()
    return getattr(mod, name, None) is not None


def require_request(name: str, *, module: str = "payments") -> Any:
    """Public ``getattr``-guarded lookup of a raw MTProto request/type class."""
    if module == "account":
        mod = _account()
    elif module == "types":
        mod = _types()
    else:
        mod = _payments()
    return _require(mod, name)


# ── wallet
async def wallet(client, peer: str | int = "me"):
    """Return ``payments.StarsStatus`` for ``peer`` (defaults to self)."""
    request_type = _require(_payments(), "GetStarsStatusRequest")
    entity = await client.get_input_entity(peer)
    return await client(request_type(peer=entity))


async def transactions(
    client,
    peer: str | int = "me",
    *,
    offset: str = "",
    limit: int = 20,
    inbound: bool | None = None,
    outbound: bool | None = None,
    ascending: bool | None = None,
):
    """Return a page of ``payments.StarsStatus`` transactions for ``peer``."""
    request_type = _require(_payments(), "GetStarsTransactionsRequest")
    entity = await client.get_input_entity(peer)
    return await client(
        request_type(
            peer=entity,
            offset=offset,
            limit=limit,
            inbound=inbound,
            outbound=outbound,
            ascending=ascending,
        )
    )


async def topup_options(client):
    """Return the list of ``payments.StarsTopupOption`` offered to this account."""
    request_type = _require(_payments(), "GetStarsTopupOptionsRequest")
    return await client(request_type())


async def revenue_stats(client, peer: str | int, *, dark: bool = False):
    """Return ``payments.StarsRevenueStats`` for a channel/bot ``peer`` you own."""
    request_type = _require(_payments(), "GetStarsRevenueStatsRequest")
    entity = await client.get_input_entity(peer)
    return await client(request_type(peer=entity, dark=dark))


async def _password_srp(client, password: str):
    """Build an ``InputCheckPasswordSRP`` proof for the account's 2FA password.

    ``password`` is held only for the duration of this call to compute the
    SRP proof and is never logged, printed, or attached to any exception.
    """
    request_type = _require(_account(), "GetPasswordRequest")
    from telethon import password as pwd_mod

    pwd = await client(request_type())
    return pwd_mod.compute_check(pwd, password)


async def withdraw_url(
    client,
    peer: str | int,
    password: str,
    *,
    amount: int | None = None,
) -> str | None:
    """Return the Stars-revenue withdrawal URL for ``peer``."""
    request_type = _require(_payments(), "GetStarsRevenueWithdrawalUrlRequest")
    entity = await client.get_input_entity(peer)
    srp = await _password_srp(client, password)
    result = await client(request_type(peer=entity, password=srp, amount=amount))
    return getattr(result, "url", None)


# ── invoices (Stars paid content, seller side)
async def send_invoice(
    client,
    peer: str | int,
    stars: int,
    title: str,
    desc: str,
    *,
    payload: bytes = b"",
    message: str = "",
    photo=None,
):
    """Send a Stars (``XTR``) invoice message to ``peer``.

    Builds ``types.Invoice(currency="XTR", prices=[LabeledPrice(title, stars)])``
    wrapped in ``InputMediaInvoice`` and delivers it with
    ``messages.SendMediaRequest``  -  the real MTProto path Telegram's own
    clients use to *send* an invoice (as opposed to *paying* one, which is
    ``SendStarsFormRequest``  -  see the module docstring).
    """
    from telethon.tl import functions

    types_mod = _types()
    send_media = _require(functions.messages, "SendMediaRequest")
    entity = await client.get_input_entity(peer)
    invoice = types_mod.Invoice(
        currency="XTR",
        prices=[types_mod.LabeledPrice(label=title, amount=stars)],
    )
    media = types_mod.InputMediaInvoice(
        title=title,
        description=desc,
        invoice=invoice,
        payload=payload,
        provider_data=types_mod.DataJSON(data="{}"),
        photo=photo,
    )
    return await client(
        send_media(
            peer=entity,
            media=media,
            message=message,
            random_id=int.from_bytes(os.urandom(8), "big", signed=True),
        )
    )


# ── star gifts (fork custom StarGift wrapper)
def _saved_gift_input(gift: Any):
    """Build an ``InputSavedStarGift*`` from a slug, msg_id, or ``StarGift``.

    Accepts, in order: a ``str`` slug, an ``int`` message id, or a fork
    :class:`telethon.tl.custom.stargift.StarGift` instance (its ``msg_id``
    attribute is used). Raises ``TypeError`` for anything else  -  this is a
    local shape error, not a fork-layer capability gap, so it is not wrapped
    in :class:`StarsUnsupportedError`.
    """
    types_mod = _types()
    if isinstance(gift, str):
        return types_mod.InputSavedStarGiftSlug(slug=gift)
    if isinstance(gift, int):
        return types_mod.InputSavedStarGiftUser(msg_id=gift)
    msg_id = getattr(gift, "msg_id", None)
    if msg_id is not None:
        return types_mod.InputSavedStarGiftUser(msg_id=msg_id)
    raise TypeError(
        "gift must be a slug (str), a message id (int), or a StarGift with "
        "msg_id set"
    )


async def send_gift(
    client,
    peer: str | int,
    gift_id: int,
    *,
    text: str | None = None,
    hidden: bool = False,
):
    """Buy and send star gift ``gift_id`` to ``peer``.

    There is no plain ``SendStarGiftRequest`` at this fork layer  -  this
    drives the same two-step flow Telegram's own clients use:
    ``GetPaymentFormRequest(InputInvoiceStarGift(...))`` to obtain a
    ``form_id``, then ``SendStarsFormRequest(form_id, invoice)`` to pay it.
    Returns ``payments.PaymentResult`` (or ``PaymentVerificationNeeded``).
    """
    get_form = _require(_payments(), "GetPaymentFormRequest")
    send_form = _require(_payments(), "SendStarsFormRequest")
    types_mod = _types()
    entity = await client.get_input_entity(peer)
    message = types_mod.TextWithEntities(text=text, entities=[]) if text else None
    invoice = types_mod.InputInvoiceStarGift(
        peer=entity,
        gift_id=gift_id,
        hide_name=hidden,
        message=message,
    )
    form = await client(get_form(invoice=invoice))
    return await client(send_form(form_id=form.form_id, invoice=invoice))


def can_upgrade(gift: Any) -> bool:
    """Read-only check of a fork ``StarGift`` instance's ``can_upgrade`` flag."""
    return bool(getattr(gift, "can_upgrade", False))


async def upgrade(
    client,
    gift: Any,
    *,
    keep_original_details: bool | None = None,
):
    """Upgrade a saved star gift to its unique collectible form.

    Drives ``payments.UpgradeStarGiftRequest`` directly rather than the
    fork's ``StarGift.upgrade()`` convenience method, which calls a
    ``TelegramClient.upgrade_gift`` method that does not exist anywhere in
    this fork  -  calling it would raise a plain ``AttributeError``.
    """
    request_type = _require(_payments(), "UpgradeStarGiftRequest")
    stargift = _saved_gift_input(gift)
    return await client(
        request_type(stargift=stargift, keep_original_details=keep_original_details)
    )


async def transfer(client, gift: Any, to_peer: str | int):
    """Transfer a saved star gift to ``to_peer`` via ``TransferStarGiftRequest``."""
    request_type = _require(_payments(), "TransferStarGiftRequest")
    stargift = _saved_gift_input(gift)
    to_id = await client.get_input_entity(to_peer)
    return await client(request_type(stargift=stargift, to_id=to_id))


async def save(client, gift: Any, *, unsave: bool = False):
    """Show/hide a saved star gift on the profile via ``SaveStarGiftRequest``."""
    request_type = _require(_payments(), "SaveStarGiftRequest")
    stargift = _saved_gift_input(gift)
    return await client(request_type(stargift=stargift, unsave=unsave))

# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Telegram Business  -  user-account settings wrappers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional
from collections.abc import Sequence

from telethon.tl import functions, types

from ..startup.logger import LOGS

from telethon.errors.rpcbaseerrors import RPCError

__all__ = [
    "BusinessProfileError",
    "PremiumRequiredError",
    "BusinessFeatureUnavailable",
    "BusinessProfileResult",
    "is_premium",
    "recipients",
    "set_greeting_message",
    "clear_greeting_message",
    "set_away_message",
    "clear_away_message",
    "set_intro",
    "clear_intro",
    "set_location",
    "clear_location",
    "set_work_hours",
    "clear_work_hours",
    "get_chat_links",
    "create_chat_link",
    "delete_chat_link",
]


class BusinessProfileError(Exception):
    """Just an Exception"""


class PremiumRequiredError(BusinessProfileError):
    """Raised when the server rejected the call because the account isn't
    Telegram Premium (server-side enforcement  -  see module docstring)."""


class BusinessFeatureUnavailable(BusinessProfileError):
    """Raised when the pinned fork build doesn't expose the TL request this
    wrapper needs (e.g. after a layer downgrade/rename)."""


@dataclass
class BusinessProfileResult:
    """Uniform success envelope returned by every wrapper on success."""

    ok: bool = True
    data: Any = None


def _classify(exc: Exception) -> BusinessProfileError:
    """Turn an RPC failure into one of our typed exceptions."""
    message = str(getattr(exc, "message", "") or exc).upper()
    if "PREMIUM" in message:
        return PremiumRequiredError(
            "Telegram Premium is required for Business features " f"({message or exc})"
        )
    return BusinessProfileError(str(exc))


async def _invoke(client, request) -> BusinessProfileResult:
    try:
        result = await client(request)
    except RPCError as exc:
        LOGS.debug("business_profile: %s failed: %s", type(request).__name__, exc)
        raise _classify(exc) from exc
    return BusinessProfileResult(ok=True, data=result)


def _require(name: str):
    """``getattr`` probe against the live fork schema for one request type."""
    req = getattr(functions.account, name, None)
    if req is None:
        raise BusinessFeatureUnavailable(f"account.{name} is not exists.")
    return req


async def is_premium(client, *, me=None) -> bool:
    """Cheap local capability check: is the logged-in account Premium?"""
    if me is None:
        me = await client.get_me()
    return bool(getattr(me, "premium", False))


def recipients(
    *,
    existing_chats: bool = True,
    new_chats: bool = True,
    contacts: bool = True,
    non_contacts: bool = True,
    exclude_selected: bool = False,
    users: Sequence | None = None,
) -> types.InputBusinessRecipients:
    """Build an ``InputBusinessRecipients``  -  defaults to "everyone"."""
    return types.InputBusinessRecipients(
        existing_chats=existing_chats,
        new_chats=new_chats,
        contacts=contacts,
        non_contacts=non_contacts,
        exclude_selected=exclude_selected,
        users=list(users) if users else None,
    )


_default_recipients = recipients


# Greeting message
async def set_greeting_message(
    client,
    *,
    shortcut_id: int,
    no_activity_days: int = 7,
    recipients: types.InputBusinessRecipients | None = None,  # noqa: F811
) -> BusinessProfileResult:
    """Enable/replace the "away for X days" greeting message.

    ``shortcut_id`` must reference an existing quick-reply shortcut
    (``messages.EditQuickReplyShortcutRequest``, out of scope here).
    """
    req_cls = _require("UpdateBusinessGreetingMessageRequest")
    message = types.InputBusinessGreetingMessage(
        shortcut_id=shortcut_id,
        recipients=recipients or _default_recipients(),
        no_activity_days=no_activity_days,
    )
    return await _invoke(client, req_cls(message=message))


async def clear_greeting_message(client) -> BusinessProfileResult:
    """Disable the greeting message."""
    req_cls = _require("UpdateBusinessGreetingMessageRequest")
    return await _invoke(client, req_cls(message=None))


# Away message
async def set_away_message(
    client,
    *,
    shortcut_id: int,
    schedule: types.TypeBusinessAwayMessageSchedule,
    offline_only: bool | None = None,
    recipients: types.InputBusinessRecipients | None = None,  # noqa: F811
) -> BusinessProfileResult:
    """Enable/replace the away message.

    ``schedule`` is one of the raw schedule TL types, e.g.
    ``types.BusinessAwayMessageScheduleAlways()``,
    ``types.BusinessAwayMessageScheduleOutsideWorkHours()`` or
    ``types.BusinessAwayMessageScheduleCustom(start_date=..., end_date=...)``.
    """
    req_cls = _require("UpdateBusinessAwayMessageRequest")
    message = types.InputBusinessAwayMessage(
        shortcut_id=shortcut_id,
        schedule=schedule,
        recipients=recipients or _default_recipients(),
        offline_only=offline_only,
    )
    return await _invoke(client, req_cls(message=message))


async def clear_away_message(client) -> BusinessProfileResult:
    """Disable the away message."""
    req_cls = _require("UpdateBusinessAwayMessageRequest")
    return await _invoke(client, req_cls(message=None))


# Intro card
async def set_intro(
    client,
    *,
    title: str,
    description: str,
    sticker=None,
) -> BusinessProfileResult:
    """Set the business intro card shown to new chats."""
    req_cls = _require("UpdateBusinessIntroRequest")
    intro = types.InputBusinessIntro(
        title=title, description=description, sticker=sticker
    )
    return await _invoke(client, req_cls(intro=intro))


async def clear_intro(client) -> BusinessProfileResult:
    """Remove the business intro card."""
    req_cls = _require("UpdateBusinessIntroRequest")
    return await _invoke(client, req_cls(intro=None))


# Location
async def set_location(
    client,
    *,
    address: str | None = None,
    geo_point=None,
) -> BusinessProfileResult:
    """Set the business address / geo point (either or both)."""
    req_cls = _require("UpdateBusinessLocationRequest")
    return await _invoke(client, req_cls(geo_point=geo_point, address=address))


async def clear_location(client) -> BusinessProfileResult:
    """Clear the business location."""
    req_cls = _require("UpdateBusinessLocationRequest")
    return await _invoke(client, req_cls(geo_point=None, address=None))


# Work hours
def work_hours(
    timezone_id: str,
    weekly_open: Sequence[types.TypeBusinessWeeklyOpen],
    *,
    open_now: bool | None = None,
) -> types.BusinessWorkHours:
    """Build a ``BusinessWorkHours`` from a timezone id + open windows.

    ``weekly_open`` items are ``types.BusinessWeeklyOpen(start_minute=,
    end_minute=)`` where minutes are counted from Monday 00:00 (0..10079).
    """
    return types.BusinessWorkHours(
        timezone_id=timezone_id,
        weekly_open=list(weekly_open),
        open_now=open_now,
    )


async def set_work_hours(
    client, business_work_hours: types.BusinessWorkHours
) -> BusinessProfileResult:
    """Set the business work-hours schedule."""
    req_cls = _require("UpdateBusinessWorkHoursRequest")
    return await _invoke(client, req_cls(business_work_hours=business_work_hours))


async def clear_work_hours(client) -> BusinessProfileResult:
    """Clear the business work-hours schedule."""
    req_cls = _require("UpdateBusinessWorkHoursRequest")
    return await _invoke(client, req_cls(business_work_hours=None))


# Business chat links
async def get_chat_links(client) -> BusinessProfileResult:
    """List the account's business quick-reply chat links."""
    req_cls = _require("GetBusinessChatLinksRequest")
    return await _invoke(client, req_cls())


async def create_chat_link(
    client,
    message: str,
    *,
    title: str | None = None,
    entities: list | None = None,
) -> BusinessProfileResult:
    """Create a new ``t.me/<username>?text=...`` business chat link."""
    req_cls = _require("CreateBusinessChatLinkRequest")
    link = types.InputBusinessChatLink(message=message, entities=entities, title=title)
    return await _invoke(client, req_cls(link=link))


async def delete_chat_link(client, slug: str) -> BusinessProfileResult:
    """Delete a business chat link identified by ``slug``."""
    req_cls = _require("DeleteBusinessChatLinkRequest")
    return await _invoke(client, req_cls(slug=slug))

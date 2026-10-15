# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Telegram Stories wrapper"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional, Union
from collections.abc import Sequence

from telethon import utils
from telethon.tl import functions, types

from ..startup.logger import LOGS

from telethon.errors.rpcbaseerrors import RPCError

__all__ = [
    "StoryError",
    "PremiumRequiredError",
    "StoryFeatureUnavailable",
    "StoryResult",
    "PERIOD_SECONDS",
    "privacy_rule",
    "send_story",
    "edit_story",
    "delete_story",
    "send_stealth_mode",
]

PERIOD_SECONDS = {
    6: 6 * 3600,
    12: 12 * 3600,
    24: 24 * 3600,
    48: 48 * 3600,
}

_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


class StoryError(Exception):
    """Base error for every wrapper in this module."""


class PremiumRequiredError(StoryError):
    """Raised when the server rejected the call because the account isn't
    Telegram Premium (e.g. a period other than the free 24h)."""


class StoryFeatureUnavailable(StoryError):
    """Raised when the pinned fork build doesn't expose the TL request this
    wrapper needs (e.g. after a layer downgrade/rename)."""


@dataclass
class StoryResult:
    """Uniform success envelope returned by every wrapper on success.

    Attributes:
        ok: Always ``True`` for a returned instance (failures raise instead).
        data: The raw Telethon response (e.g. ``Updates`` for a send/edit,
            ``bool`` for delete/stealth, or the ``CanSendStoryRequest``
            boolean result).
    """

    ok: bool = True
    data: Any = None


def _classify(exc: Exception) -> StoryError:
    """Turn an RPC failure into one of our typed exceptions."""
    message = str(getattr(exc, "message", "") or exc).upper()
    if "PREMIUM" in message:
        return PremiumRequiredError(
            "Telegram Premium is required for this story request " f"({message or exc})"
        )
    return StoryError(str(exc))


async def _invoke(client, request) -> StoryResult:
    try:
        result = await client(request)
    except RPCError as exc:
        LOGS.debug("stories: %s failed: %s", type(request).__name__, exc)
        raise _classify(exc) from exc
    return StoryResult(ok=True, data=result)


def _require(name: str):
    """``getattr`` probe against the live fork schema for one request type."""
    req = getattr(functions.stories, name, None)
    if req is None:
        raise StoryFeatureUnavailable(
            f"stories.{name} is not available in this Telethon build "
            "(fork layer mismatch?)"
        )
    return req


def _normalize_period(period: int | None) -> int | None:
    """Accept ``6``/``12``/``24``/``48`` (hours, per the plan's API) and
    return seconds. A value already outside that set is assumed to already
    be in seconds (passed through as-is) so callers with a raw
    ``period=`` in seconds from elsewhere keep working."""
    if period is None:
        return PERIOD_SECONDS[24]
    if period in PERIOD_SECONDS:
        return PERIOD_SECONDS[period]
    return period


async def _resolve_peer(client, peer):
    return await client.get_input_entity(peer)


async def _resolve_media(
    client,
    media,
    *,
    is_photo: bool | None = None,
    force_document: bool = False,
    attributes: Sequence | None = None,
    file_name: str | None = None,
):
    """Turn ``media`` (path / bytes / existing photo-doc) into an
    ``InputMedia*`` ready for ``stories.SendStoryRequest``/``EditStoryRequest``.
    """
    if isinstance(media, (str, os.PathLike)):
        path = os.fspath(media)
        name = file_name or Path(path).name
        if is_photo is None:
            is_photo = Path(path).suffix.lower() in _PHOTO_EXTS and not force_document
        uploaded = await client.upload_file(path, file_name=name)
        return utils.get_input_media(
            uploaded,
            is_photo=bool(is_photo),
            attributes=list(attributes) if attributes else None,
            force_document=force_document,
        )
    if isinstance(media, bytes):
        if is_photo is None:
            is_photo = False
        name = file_name or ("story.jpg" if is_photo else "story.bin")
        uploaded = await client.upload_file(media, file_name=name)
        return utils.get_input_media(
            uploaded,
            is_photo=bool(is_photo),
            attributes=list(attributes) if attributes else None,
            force_document=force_document,
        )
    return utils.get_input_media(
        media,
        is_photo=bool(is_photo) if is_photo is not None else False,
        attributes=list(attributes) if attributes else None,
        force_document=force_document,
    )


def privacy_rule(privacy: str | Sequence | None = None) -> list:
    """Translate a friendly ``privacy=`` shorthand into
    ``[InputPrivacyRule, ...]`` for ``SendStoryRequest``/``EditStoryRequest``.

    Accepts ``None``/``"everyone"`` (default), ``"contacts"``,
    ``"close_friends"``, ``"nobody"``, or an already-built list of
    ``InputPrivacyValue*`` rules (passed through unchanged).
    """
    if privacy is None or privacy == "everyone":
        return [types.InputPrivacyValueAllowAll()]
    if privacy == "contacts":
        return [types.InputPrivacyValueAllowContacts()]
    if privacy == "close_friends":
        return [types.InputPrivacyValueAllowCloseFriends()]
    if privacy == "nobody":
        return [types.InputPrivacyValueDisallowAll()]
    # Already a sequence of InputPrivacyValue* rules (or a single rule).
    if isinstance(privacy, (list, tuple)):
        return list(privacy)
    return [privacy]


async def send_story(
    client,
    peer,
    media,
    *,
    caption: str | None = None,
    entities: list | None = None,
    period: int | None = 24,
    privacy: str | Sequence | None = None,
    pinned: bool = False,
    noforwards: bool = False,
    is_photo: bool | None = None,
    force_document: bool = False,
    attributes: Sequence | None = None,
    file_name: str | None = None,
    media_areas: list | None = None,
    random_id: int | None = None,
) -> StoryResult:
    """Post a story to ``peer`` (``"me"`` for your own stories feed, or a
    channel you administer).

    ``period`` accepts ``6``/``12``/``24``/``48`` (hours, default ``24``  - 
    free); non-24h periods raise :class:`PremiumRequiredError` server-side
    for non-Premium accounts. ``privacy`` accepts the shorthand strings
    documented in :func:`privacy_rule`.
    """
    req_cls = _require("SendStoryRequest")
    input_peer = await _resolve_peer(client, peer)
    media_obj = await _resolve_media(
        client,
        media,
        is_photo=is_photo,
        force_document=force_document,
        attributes=attributes,
        file_name=file_name,
    )
    if random_id is None:
        from telethon import helpers

        random_id = helpers.generate_random_long()
    request = req_cls(
        peer=input_peer,
        media=media_obj,
        privacy_rules=privacy_rule(privacy),
        pinned=pinned,
        noforwards=noforwards,
        media_areas=list(media_areas) if media_areas else None,
        caption=caption,
        entities=entities,
        random_id=random_id,
        period=_normalize_period(period),
    )
    return await _invoke(client, request)


async def edit_story(
    client,
    peer,
    story_id: int,
    *,
    media=None,
    caption: str | None = None,
    entities: list | None = None,
    privacy: str | Sequence | None = None,
    media_areas: list | None = None,
    is_photo: bool | None = None,
    force_document: bool = False,
    attributes: Sequence | None = None,
    file_name: str | None = None,
) -> StoryResult:
    """Edit an existing story's media/caption/privacy. Any argument left as
    ``None`` keeps the current value (per the raw TL request semantics)."""
    req_cls = _require("EditStoryRequest")
    input_peer = await _resolve_peer(client, peer)
    media_obj = None
    if media is not None:
        media_obj = await _resolve_media(
            client,
            media,
            is_photo=is_photo,
            force_document=force_document,
            attributes=attributes,
            file_name=file_name,
        )
    request = req_cls(
        peer=input_peer,
        id=story_id,
        media=media_obj,
        media_areas=list(media_areas) if media_areas else None,
        caption=caption,
        entities=entities,
        privacy_rules=privacy_rule(privacy) if privacy is not None else None,
    )
    return await _invoke(client, request)


async def delete_story(
    client, peer, story_ids: int | Sequence[int]
) -> StoryResult:
    """Delete one or more stories owned by ``peer``."""
    req_cls = _require("DeleteStoriesRequest")
    input_peer = await _resolve_peer(client, peer)
    ids = [story_ids] if isinstance(story_ids, int) else list(story_ids)
    return await _invoke(client, req_cls(peer=input_peer, id=ids))


async def send_stealth_mode(
    client, *, past: bool = True, future: bool = True
) -> StoryResult:
    """Activate "stealth mode" (hide your recent story views), the fork's
    L227 equivalent of the stealth-mode toggle. Premium-only server-side."""
    req_cls = _require("ActivateStealthModeRequest")
    return await _invoke(client, req_cls(past=past, future=future))

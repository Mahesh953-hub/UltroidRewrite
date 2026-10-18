# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from __future__ import annotations

import collections.abc
import logging
from asyncio import sleep

from telethon.errors import MessageDeleteForbiddenError, MessageNotModifiedError
from telethon.tl.custom import Message
from telethon.tl.types import MessageService

from .._core.dynamic import PatchRegistry

PATCHES: list[tuple[type, collections.abc.Callable]] = []

CORE_PATCHES: frozenset[str] = frozenset({"eor", "try_delete"})

_PATCH_REGISTRY: PatchRegistry | None = None


def get_patch_registry(db=None) -> PatchRegistry:
    """Return the global patch registry, creating it on first call."""
    global _PATCH_REGISTRY
    if _PATCH_REGISTRY is None:
        _PATCH_REGISTRY = PatchRegistry(db=db)
    elif db is not None:
        _PATCH_REGISTRY.bind_database(db)
    return _PATCH_REGISTRY


def bind_database(db) -> None:
    """Bind the global patch registry to a udB instance for persistence."""
    get_patch_registry(db=db)


def bind_client(client) -> None:
    """Bind the global patch registry to a started client's update pipeline.

    Patches are applied during bootstrap, before any client or pipeline
    exists, so their ``routes=`` specs have nowhere to install. Calling this
    once the primary client is connected installs those routes; without it a
    patch-contributed route is a silent no-op.
    """
    get_patch_registry().bind_client(client)


def patch(cls: type) -> collections.abc.Callable:
    def wrap(func: collections.abc.Callable) -> collections.abc.Callable:
        registry = get_patch_registry()
        name = func.__name__
        if not registry.is_registered(name):
            registry.register(
                name,
                cls,
                func,
                description=func.__doc__ or "",
                category="startup",
            )
        PATCHES.append((cls, func))
        return func

    return wrap


@patch(Message)
async def eor(
    event, text=None, time=None, link_preview=False, edit_time=None, spoil=False, **args
):
    """Edit Or Reply helper method patched onto Message."""
    if spoil and text:
        from custom.entities import spoiler

        text, spoiler_entities = spoiler(text)
        existing_entities = args.get("formatting_entities")
        args["formatting_entities"] = (
            list(existing_entities) + spoiler_entities
            if existing_entities
            else spoiler_entities
        )
        args.setdefault("parse_mode", None)
    reply_to = event.reply_to_msg_id or event
    if event.out and not isinstance(event, MessageService):
        if edit_time:
            await sleep(edit_time)
        if args.get("file") and not event.media:
            await event.delete()
            ok = await event.client.send_message(
                event.chat_id,
                text,
                link_preview=link_preview,
                reply_to=reply_to,
                **args,
            )
        else:
            try:
                ok = await event.edit(text, link_preview=link_preview, **args)
            except MessageNotModifiedError:
                ok = event
    else:
        ok = await event.client.send_message(
            event.chat_id,
            text,
            link_preview=link_preview,
            reply_to=reply_to,
            **args,
        )

    if time:
        await sleep(time)
        return await ok.delete()
    return ok


#: The original ``Message.text`` descriptor, captured before patching so the
#: replacement can delegate to it. Telethon defines ``text`` as a property;
#: replacing it with a bare function would make it a plain method, and
#: ``msg.text`` would return the bound method instead of the string.
_ORIGINAL_TEXT = Message.text


def _normalized_text_get(self):
    """Guarantee ``Message.text`` is a plain ``str``.

    This fork's rich-text handling can leave ``text`` as a
    ``(text, entities)`` tuple, and Telethon objects exposing a ``.text``
    field (``TextWithEntities``) are not ``str`` either. Every plugin that
    does ``event.text.split(...)`` then raises::

        AttributeError: 'tuple' object has no attribute 'split'

    Normalising here fixes every such call site at once, rather than adding a
    guard to each of ~200 of them. The entities are dropped rather than
    merged, because a caller asking for ``.text`` is asking for the string.

    The result is cached on the instance so the original descriptor is
    evaluated at most once per message.
    """
    cached = self.__dict__.get("_ultroid_text")
    if cached is not None:
        return cached

    from .._core.notices import as_text

    try:
        raw = _ORIGINAL_TEXT.fget(self)
    except Exception:  # noqa: BLE001 - never make .text raise
        raw = None

    value = as_text(raw)
    try:
        self.__dict__["_ultroid_text"] = value
    except (AttributeError, TypeError):  # slotted / immutable message
        pass
    return value


#: The replacement descriptor, built once. ``PatchRegistry.apply`` does a
#: plain ``setattr(target, attr_name, func)`` and never calls *func*, so the
#: registered value must already BE the property rather than a factory for
#: one - otherwise ``Message.text`` becomes a plain function and ``msg.text``
#: returns a bound method instead of a string.
_TEXT_PROPERTY = property(_normalized_text_get, doc=_normalized_text_get.__doc__)

# Registered directly rather than through ``@patch`` so ``attr_name`` can be
# given. ``@patch`` derives it from ``func.__name__``, which would install
# this as ``Message.normalized_text`` and leave ``Message.text`` untouched - a
# silent no-op, since the symptom is an AttributeError on the attribute that
# nobody patched.
get_patch_registry().register(
    "normalized_text",
    Message,
    _TEXT_PROPERTY,
    attr_name="text",
    description=_normalized_text_get.__doc__ or "",
    category="startup",
)


@patch(Message)
async def try_delete(event):
    """Try to delete message, silencing standard forbidden errors."""
    try:
        return await event.delete()
    except MessageDeleteForbiddenError:
        pass
    except Exception:
        pass


def apply_patches() -> None:
    """Execute dynamic class patching at startup.

    Must be called after ``bind_database(udB)`` so that persisted enable/disable
    flags are loaded before any non-core patch is applied.

    Patches in :data:`CORE_PATCHES` are force-applied because they are required
    for basic plugin/owner compatibility.  Every other patch is applied only
    when its persisted enable flag is ``True`` (default enabled when not set).
    """
    registry = get_patch_registry()
    for rec in registry.list_patches():
        registry.apply(rec.name, force=rec.name in CORE_PATCHES)
    applied = registry.applied_patches()
    logging.info(f"Successfully applied {len(applied)} dynamic class patches.")

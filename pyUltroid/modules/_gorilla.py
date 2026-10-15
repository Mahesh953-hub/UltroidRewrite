"""Business/guest Telethon addons, as one installable class.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import logging
from typing import Any, ClassVar

from telethon import TelegramClient, events
from telethon.custom import InlineBuilder
from telethon.functions import InvokeWithBusinessConnectionRequest, messages
from telethon.types import (
    InputBotInlineMessageID,
    InputBotInlineMessageID64,
    InputMediaDocumentExternal,
    InputMediaPhotoExternal,
    InputMediaUploadedDocument,
    InputMediaUploadedPhoto,
    InputPeerUser,
    InputReplyToMessage,
    UpdateBotGuestChatQuery,
    UpdateBotNewBusinessMessage,
    UpdateBusinessBotCallbackQuery,
)
from telethon.utils import get_input_media

__all__ = ["BusinessAddons"]

LOGS = logging.getLogger("pyUltLogs")


class BusinessAddons:
    """The business/guest capability set, forced onto the raw Telethon classes.

    The five TL implementations below are ``staticmethod``s on purpose: they get
    ``setattr``-ed onto Telethon classes, so their first argument is the TL
    object itself, not an instance of this class. They therefore reach sibling
    behaviour through ``BusinessAddons.<name>`` rather than ``self``.
    """

    #: TL requests that must not be wrapped in InvokeWithBusinessConnection.
    #: Inline and guest-chat results are bot-native; wrapping them breaks
    #: delivery. This superset also covers the request itself, which would
    #: otherwise nest.
    NEVER_WRAP: ClassVar[frozenset[str]] = frozenset(
        {
            "GetInlineBotResultsRequest",
            "SetInlineBotResultsRequest",
            "SetBotInlineResultRequest",
            "SetBotGuestChatResultRequest",
            "InvokeWithBusinessConnectionRequest",
        }
    )

    #: (target class, attribute name). Several TL classes take more than one
    #: method, so the implementations are named explicitly per target.
    PATCHES: ClassVar[tuple[tuple[type, str], ...]] = (
        (TelegramClient, "invoke"),
        (UpdateBotGuestChatQuery, "answer"),
        (UpdateBotNewBusinessMessage, "respond"),
        (UpdateBotNewBusinessMessage, "delete"),
        (UpdateBusinessBotCallbackQuery, "respond"),
        (UpdateBusinessBotCallbackQuery, "delete"),
    )

    def __init__(self) -> None:
        super().__init__()
        self._installed: bool = False

    # ── identity resolution ───────────────────────────────────────────────

    @staticmethod
    def owner_id(client: object) -> int | None:
        """Numeric owner id from any client shape, or None.

        ``uid`` was a fork-local alias absent here; the canonical field is
        ``id``. ``user_id`` is the attribute selfbot-style bootstraps set, so
        it is preferred over every Telethon-derived field.
        """
        for candidate in (
            getattr(client, "user_id", None),
            getattr(getattr(client, "user", None), "id", None),
            getattr(getattr(client, "me", None), "id", None),
            getattr(getattr(client, "bot", None), "id", None),
        ):
            if isinstance(candidate, bool):
                continue
            if isinstance(candidate, int):
                return candidate
            if isinstance(candidate, str) and candidate.lstrip("-").isdigit():
                return int(candidate)
        return None

    @staticmethod
    def connection_id(client: object) -> str | None:
        """Business connection id, when this client holds one."""
        value = getattr(client, "connection_id", None)
        return value if isinstance(value, str) and value else None

    def is_owner(self, client: object, user_id: int | None) -> bool:
        """Whether *user_id* is this client's owner.

        An unresolvable owner accepts every sender, which keeps a partially
        constructed client usable instead of dropping every update.
        """
        owner = self.owner_id(client)
        return owner is None or user_id == owner

    # ── TL implementations ────────────────────────────────────────────────

    @staticmethod
    async def invoke(
        client: TelegramClient, query: object, connection_id: str = None
    ) -> Any:
        """Run *query* through the business connection when there is one."""
        conn = connection_id or BusinessAddons.connection_id(client)
        if conn and type(query).__name__ not in BusinessAddons.NEVER_WRAP:
            return await client(InvokeWithBusinessConnectionRequest(conn, query))
        return await client(query)

    @staticmethod
    async def answer(
        update: UpdateBotGuestChatQuery, value: str, *args, **kwargs
    ) -> InputBotInlineMessageID | InputBotInlineMessageID64:
        """Answer a guest-chat query with an inline result.

        selfbot always answers. An earlier port deferred whenever the bot had
        *any* InlineQuery handler, which silently dropped the query; we defer
        only when a registered pattern actually matches this query's text.
        """
        text = getattr(update, "query", None) or ""
        if BusinessAddons._claimed_by_inline_handler(text):
            return None

        client = getattr(update, "_client", None)
        inline = InlineBuilder(client)
        match value:
            case "article":
                result = await inline.article(*args, **kwargs)
            case "photo":
                result = await inline.photo(*args, **kwargs)
            case "document":
                result = await inline.document(*args, **kwargs)
            case "game":
                result = await inline.game(*args, **kwargs)
            case _:
                raise ValueError(value)

        return await client(
            messages.SetBotGuestChatResultRequest(update.query_id, result)
        )

    @staticmethod
    def _claimed_by_inline_handler(text: str) -> bool:
        """Whether a registered inline pattern really matches *text*."""
        try:
            from pyUltroid import asst

            if asst is None:
                return False

            for _, builder in asst.list_event_handlers():
                if not isinstance(builder, events.InlineQuery):
                    continue
                pattern = getattr(builder, "pattern", None)
                if pattern is None:
                    continue
                try:
                    # Telethon exposes the compiled regex as the *bound*
                    # ``re.Pattern.match``, so calling it runs the match.
                    matched = bool(pattern(text)) if text else False
                except TypeError:
                    matcher = pattern if hasattr(pattern, "search") else None
                    matched = bool(matcher.search(text)) if matcher and text else False
                if matched:
                    return True
        except Exception:
            return False
        return False

    @staticmethod
    async def build_kwargs(
        client: TelegramClient, kwargs: dict, entity: InputPeerUser
    ) -> dict:
        """Translate Telethon convenience kwargs into raw TL kwargs."""
        if "buttons" in kwargs:
            kwargs["reply_markup"] = client.build_reply_markup(kwargs.pop("buttons"))

        if "file" in kwargs:
            _, media, _ = await client._file_to_media(kwargs.pop("file"))
            upload = await client(messages.UploadMediaRequest(entity, media=media))
            if isinstance(media, (InputMediaPhotoExternal, InputMediaUploadedPhoto)):
                kwargs["media"] = get_input_media(upload.photo)
            elif isinstance(
                media, (InputMediaUploadedDocument, InputMediaDocumentExternal)
            ):
                kwargs["media"] = get_input_media(
                    upload.document,
                    supports_streaming=kwargs.get("supports_streaming"),
                )

        if "reply_to" in kwargs:
            kwargs["reply_to"] = InputReplyToMessage(kwargs["reply_to"])

        return kwargs

    @staticmethod
    async def respond(update: Any, *args, **kwargs) -> Any:
        """Send or edit from a business/guest update.

        An integer first argument means "edit that message", matching the
        ``respond(msg_id, text)`` shape the executors use.
        """
        client = update._client
        entity = await client.get_input_entity(update.message.peer_id)
        kwargs = await BusinessAddons.build_kwargs(client, kwargs, entity)

        if args and isinstance(args[0], int):
            match args:
                case (_, bool(), bool(), str()):
                    pass
                case (_, bool(), str()):
                    args = args[0], args[1], None, args[2]
                case (_, str()):
                    args = args[0], None, None, args[1]
                case (_,):
                    args = args[0], None, None, None
                case _:
                    raise TypeError(str(args))
            return await client.invoke(
                messages.EditMessageRequest(entity, *args, **kwargs)
            )

        if "media" in kwargs:
            args = kwargs.pop("media"), "" if not args else args[0]
            return await client.invoke(
                messages.SendMediaRequest(entity, *args, **kwargs)
            )

        return await client.invoke(messages.SendMessageRequest(entity, *args, **kwargs))

    @staticmethod
    async def delete(
        update: Any, message_id: list = None, revoke: bool = True
    ) -> Any:
        """Delete the update's own message unless ids are given."""
        return await update._client.invoke(
            messages.DeleteMessagesRequest(
                message_id if message_id else [update.message.id], revoke
            )
        )

    # ── installation ──────────────────────────────────────────────────────

    @property
    def installed(self) -> bool:
        """Whether the addons are already on the TL classes."""
        return self._installed

    def implementations(self) -> tuple[tuple[type, str, Any], ...]:
        """The ``(class, name, implementation)`` triples to install."""
        return (
            (TelegramClient, "invoke", BusinessAddons.invoke),
            (UpdateBotGuestChatQuery, "answer", BusinessAddons.answer),
            (UpdateBotNewBusinessMessage, "respond", BusinessAddons.respond),
            (UpdateBotNewBusinessMessage, "delete", BusinessAddons.delete),
            (UpdateBusinessBotCallbackQuery, "respond", BusinessAddons.respond),
            (UpdateBusinessBotCallbackQuery, "delete", BusinessAddons.delete),
        )

    def install(self, *, force: bool = False) -> int:
        """Install the addons on the TL classes.

        Idempotent unless *force*: the loader installs once at bootstrap, and
        a forced reinstall re-asserts the addons over anything that patched the
        same targets later.
        """
        if self._installed and not force:
            return 0

        installed = 0
        for cls, name, func in self.implementations():
            setattr(cls, name, func)
            installed += 1

        self._installed = True
        return installed

    def uninstall(self) -> int:
        """Drop every installed addon, restoring absence."""
        removed = 0
        for cls, name, _ in self.implementations():
            if name in vars(cls):
                delattr(cls, name)
                removed += 1
        self._installed = False
        return removed
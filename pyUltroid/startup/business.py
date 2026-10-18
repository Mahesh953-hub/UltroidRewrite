import logging
from contextvars import ContextVar
from telethon import utils, events
from telethon.events import Raw
from telethon.functions import account, messages
from telethon.tl.types import PeerChannel, PeerChat, PeerUser

business_connection_id = ContextVar("business_connection_id", default=None)

# TL request names that may be wrapped in InvokeWithBusinessConnectionRequest
# when a business connection context is active. Kept in one place so both the
# BusinessBotsClient subclass and tests can share the exact allow-list.
_BUSINESS_WRAPPABLE_REQUESTS = frozenset(
    {
        "SendMessageRequest",
        "SendMediaRequest",
        "EditMessageRequest",
        "DeleteMessagesRequest",
        "SetBotCallbackAnswerRequest",
    }
)

from telethon.types import (
    InputBusinessBotRecipients,
    BusinessBotRights,
    UpdateBotBusinessConnect,
    UpdateBotNewBusinessMessage,
    UpdateBusinessBotCallbackQuery,
)

LOGS = logging.getLogger("pyUltLogs")


class BusinessMessageEvent:
    def __init__(self, update, client, pattern_match=None, response_client=None):
        self.update = update
        self.message = update.message
        self.client = client
        self._response_client = response_client or client
        self.text = self.message.message
        self.pattern_match = pattern_match
        self.chat_id = utils.get_peer_id(self.message.peer_id)
        self.connection_id = getattr(update, "connection_id", None) or getattr(
            client, "connection_id", None
        )

        # Set client reference on the message for inner telethon functions
        self.message._client = client

        # Resolve sender_id
        if self.message.from_id:
            self.sender_id = utils.get_peer_id(self.message.from_id)
        else:
            self.sender_id = self.chat_id

        # Determine if the sender is the business owner
        from pyUltroid import ultroid_bot

        self.out = self.sender_id == ultroid_bot.uid
        peer_id = self.message.peer_id
        channel_peer = isinstance(peer_id, PeerChannel)
        group_peer = isinstance(peer_id, PeerChat) or (
            channel_peer and not getattr(self.message, "post", False)
        )
        self.is_private = bool(
            getattr(self.message, "is_private", isinstance(peer_id, PeerUser))
        )
        self.is_group = bool(getattr(self.message, "is_group", group_peer))
        self.is_channel = bool(
            getattr(self.message, "is_channel", channel_peer and not group_peer)
        )

        # Expose standard message properties
        self.id = self.message.id
        reply_header = getattr(self.message, "reply_to", None)
        self.reply_to_msg_id = getattr(self.message, "reply_to_msg_id", None)
        if self.reply_to_msg_id is None and reply_header is not None:
            self.reply_to_msg_id = getattr(reply_header, "reply_to_msg_id", None)
        self.is_reply = bool(self.reply_to_msg_id)
        self.media = self.message.media
        self.entities = self.message.entities

    async def get_reply_message(self):
        if self.reply_to_msg_id:
            return await self.client.get_messages(
                self.chat_id, ids=self.reply_to_msg_id
            )
        return None

    def __getattr__(self, name):
        return getattr(self.message, name)

    async def respond(self, *args, **kwargs):
        token = business_connection_id.set(self.connection_id)
        try:
            return await self._response_client.send_message(
                self.chat_id, *args, **kwargs
            )
        finally:
            business_connection_id.reset(token)

    async def reply(self, *args, **kwargs):
        kwargs["reply_to"] = self.message.id
        token = business_connection_id.set(self.connection_id)
        try:
            return await self._response_client.send_message(
                self.chat_id, *args, **kwargs
            )
        finally:
            business_connection_id.reset(token)

    async def edit(self, *args, **kwargs):
        token = business_connection_id.set(self.connection_id)
        try:
            return await self._response_client.edit_message(
                self.chat_id, self.message.id, *args, **kwargs
            )
        finally:
            business_connection_id.reset(token)

    async def delete(self, *args, **kwargs):
        token = business_connection_id.set(self.connection_id)
        try:
            return await self._response_client.delete_messages(
                self.chat_id, [self.message.id], *args, **kwargs
            )
        finally:
            business_connection_id.reset(token)

    @property
    def chat(self):
        if hasattr(self, "_chat") and self._chat:
            return self._chat
        try:
            self._chat = self.client.get_entity(self.chat_id)
        except Exception:

            class DummyChat:
                def __init__(self, chat_id):
                    self.id = chat_id
                    self.title = "Business Chat"
                    self.admin_rights = None
                    self.creator = False

            self._chat = DummyChat(self.chat_id)
        return self._chat


class BusinessCallbackQueryEvent:
    def __init__(self, update, client, pattern_match=None, response_client=None):
        self.update = update
        self.query_id = update.query_id
        self.sender_id = update.user_id
        self.data = update.data
        self.pattern_match = pattern_match
        self.client = client
        self._response_client = response_client or client
        self.chat_id = utils.get_peer_id(update.message.peer_id)
        self.message_id = update.message.id
        self.message = update.message
        self.message._client = client
        self.connection_id = getattr(update, "connection_id", None) or getattr(
            client, "connection_id", None
        )

    async def answer(self, message=None, cache_time=0, alert=False, url=None):
        token = business_connection_id.set(self.connection_id)
        try:
            return await self._response_client(
                messages.SetBotCallbackAnswerRequest(
                    query_id=self.query_id,
                    message=message,
                    alert=alert,
                    url=url,
                    cache_time=cache_time,
                )
            )
        finally:
            business_connection_id.reset(token)

    async def edit(self, *args, **kwargs):
        token = business_connection_id.set(self.connection_id)
        try:
            return await self._response_client.edit_message(
                self.chat_id, self.message_id, *args, **kwargs
            )
        finally:
            business_connection_id.reset(token)

    async def delete(self, *args, **kwargs):
        token = business_connection_id.set(self.connection_id)
        try:
            return await self._response_client.delete_messages(
                self.chat_id, [self.message_id], *args, **kwargs
            )
        finally:
            business_connection_id.reset(token)

    @property
    def chat(self):
        if hasattr(self, "_chat") and self._chat:
            return self._chat
        try:
            self._chat = self.client.get_entity(self.chat_id)
        except Exception:

            class DummyChat:
                def __init__(self, chat_id):
                    self.id = chat_id
                    self.title = "Business Chat"
                    self.admin_rights = None
                    self.creator = False

            self._chat = DummyChat(self.chat_id)
        return self._chat


async def match_business_message(event_builder, update, client):
    import asyncio

    msg = update.message
    # Resolve sender_id
    if msg.from_id:
        sender_id = utils.get_peer_id(msg.from_id)
    else:
        sender_id = utils.get_peer_id(msg.peer_id)

    client_uid = getattr(client, "uid", None) or getattr(
        getattr(client, "me", None), "id", None
    )
    is_outgoing = sender_id == client_uid

    # Outgoing / Incoming check
    if getattr(event_builder, "incoming", None) and is_outgoing:
        return False
    if getattr(event_builder, "outgoing", None) and not is_outgoing:
        return False

    # Chats check
    if getattr(event_builder, "chats", None) is not None:
        chat_id = utils.get_peer_id(msg.peer_id)
        chats = event_builder.chats
        if not isinstance(chats, (list, tuple, set)):
            chats = [chats]
        is_present = False
        for c in chats:
            if isinstance(c, int):
                if c == chat_id or utils.get_peer_id(c) == chat_id:
                    is_present = True
                    break
            elif isinstance(c, str):
                entity = client._cache.get(chat_id)
                if entity and getattr(entity, "username", None) == c.lstrip("@"):
                    is_present = True
                    break
        blacklist = getattr(event_builder, "blacklist_chats", False)
        if blacklist and is_present:
            return False
        if not blacklist and not is_present:
            return False

    # Custom function filter
    if getattr(event_builder, "func", None) is not None:
        dummy_event = BusinessMessageEvent(update, client, response_client=client)
        try:
            res = event_builder.func(dummy_event)
            if asyncio.iscoroutine(res):
                res = await res
            if not res:
                return False
        except Exception:
            return False

    # Regex Matcher
    if getattr(event_builder, "pattern", None) is not None:
        pattern = event_builder.pattern
        text = msg.message or ""
        if callable(pattern):
            match = pattern(text)
        elif hasattr(pattern, "match"):
            match = pattern.match(text)
        else:
            match = None
        if not match:
            return False
        return match

    return True


async def setup_business_mode(ultroid_bot, asst):
    """Initializes business mode connection and raw update interceptors."""
    return await connect_business_bot(ultroid_bot, asst)


async def connect_business_bot(ultroid_bot, asst):
    """Initializes business mode connection and raw update interceptors."""
    bot_me = await asst.get_me()
    username = bot_me.username

    # the business-connection request wrapping now lives on the
    # BusinessBotsClient subclass used for assistant/bot clients. The global
    # TelegramClient.__call__ monkey-patch has been removed so user-account
    # clients are never affected by the business context variable.

    # Register raw handler on asst
    @asst.on(Raw)
    async def raw_business_handler(update):
        # 1. UpdateBotBusinessConnect
        if isinstance(update, UpdateBotBusinessConnect):
            asst.connection_id = update.connection.connection_id
            LOGS.info(
                f"Connected Business Connection established! ID: {asst.connection_id}"
            )

        # 2. UpdateBotNewBusinessMessage
        elif isinstance(update, UpdateBotNewBusinessMessage):
            if not getattr(asst, "connection_id", None):
                asst.connection_id = update.connection_id

            # Route through both clients  -  each handler's responses go
            # through the client that registered it (response_client).
            from pyUltroid import ultroid_bot

            for client_obj in (ultroid_bot, asst):
                if not client_obj:
                    continue
                for callback_func, event_builder in client_obj.list_event_handlers():
                    if isinstance(
                        event_builder, (events.NewMessage, events.MessageEdited)
                    ):
                        matched = await match_business_message(
                            event_builder, update, client_obj
                        )
                        if matched:
                            wrapped_event = BusinessMessageEvent(
                                update,
                                asst,
                                matched if matched is not True else None,
                                response_client=client_obj,
                            )
                            try:
                                await callback_func(wrapped_event)
                            except Exception as err:
                                LOGS.error(
                                    f"Error executing business message handler on {client_obj.__class__.__name__}: {err}"
                                )
                                LOGS.exception(err)

        # 3. UpdateBusinessBotCallbackQuery
        elif isinstance(update, UpdateBusinessBotCallbackQuery):
            if not getattr(asst, "connection_id", None):
                asst.connection_id = update.connection_id

            query_data = update.data or b""

            # Route through both clients' callback handlers
            from pyUltroid import ultroid_bot

            for client_obj in (ultroid_bot, asst):
                if not client_obj:
                    continue
                for callback_func, event_builder in client_obj.list_event_handlers():
                    if isinstance(event_builder, events.CallbackQuery):
                        match = None
                        if event_builder.match:
                            if isinstance(event_builder.match, bytes):
                                if event_builder.match == query_data:
                                    match = True
                            elif callable(event_builder.match):
                                match = event_builder.match(query_data)
                            elif hasattr(event_builder.match, "match"):
                                match = event_builder.match.match(query_data)
                        else:
                            match = True

                        if match:
                            wrapped_event = BusinessCallbackQueryEvent(
                                update, asst, match, response_client=client_obj
                            )
                            try:
                                await callback_func(wrapped_event)
                            except Exception as err:
                                LOGS.error(
                                    f"Error executing business callback query handler: {err}"
                                )
                                LOGS.exception(err)

    # Perform handshake
    LOGS.info(f"Initiating Business Mode handshake for @{username}...")
    try:
        await ultroid_bot(
            account.UpdateConnectedBotRequest(
                bot=username,
                recipients=InputBusinessBotRecipients(
                    existing_chats=True,
                    new_chats=True,
                    contacts=True,
                    non_contacts=True,
                    exclude_selected=False,
                ),
                deleted=False,
                rights=BusinessBotRights(
                    reply=True,
                    read_messages=True,
                    delete_sent_messages=True,
                    delete_received_messages=True,
                    edit_name=True,
                    edit_bio=True,
                    edit_profile_photo=True,
                    edit_username=True,
                    manage_stories=True,
                ),
            )
        )
    except Exception as e:
        LOGS.error(f"Handshake UpdateConnectedBotRequest failed: {e}")

"""Offline routing checks through the real assistant decorators and Telethon builders."""
from __future__ import annotations

import asyncio
import importlib.util
import itertools
import sys
from pathlib import Path
from types import SimpleNamespace

from telethon import TelegramClient, events, types
from telethon.tl import functions

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assistant" / "rich_showcase.py"
_NAMES = itertools.count()


class Client:
    def __init__(self):
        self.requests = []

    async def get_input_entity(self, _peer):
        return types.InputPeerSelf()

    build_reply_markup = staticmethod(TelegramClient.build_reply_markup)

    async def __call__(self, request):
        self.requests.append(request)
        return SimpleNamespace(id=990)


class Event:
    def __init__(self, sender_id=41, data=b"", client=None):
        self.sender_id = sender_id
        self.data = data
        self.client = client or Client()
        self.chat_id = 123
        self.message_id = 77
        self.answers = []
        self.edits = []

    async def answer(self, *args, **kwargs):
        self.answers.append((args, kwargs))

    async def edit(self, *args, **kwargs):
        self.edits.append((args, kwargs))


def test_real_decorator_routing_native_pick_and_callback_filter(monkeypatch):
    import pyUltroid._misc._assistant as assistant

    registered = []
    class HandlerRegistry:
        def add_event_handler(self, callback, builder):
            registered.append((callback, builder))

    monkeypatch.setattr(assistant, "asst", HandlerRegistry())
    monkeypatch.setattr(assistant, "owner_and_sudos", lambda: {41})

    name = f"rich_showcase_real_decorators_{next(_NAMES)}"
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        assert len(registered) == 2
        command_wrapper, command_builder = registered[0]
        callback_wrapper, callback_builder = registered[1]
        assert isinstance(command_builder, events.NewMessage)
        assert isinstance(callback_builder, events.CallbackQuery)
        assert command_builder.pattern("/rich")
        assert command_builder.pattern("/rich@mybot ")
        assert not command_builder.pattern("/richish")
        assert command_builder.forwards is False
        assert callback_builder.match(b"rich:pick:heading-1")
        assert callback_builder.match(b"rich:pick:heading-6")
        assert callback_builder.match(b"rich:index:2")
        assert not callback_builder.match(b"rich:index:100")
        assert not callback_builder.match(b"rich:index:x")

        owner = Event()
        asyncio.run(command_wrapper(owner))
        assert len(owner.client.requests) == 1
        index_request = owner.client.requests[0]
        assert isinstance(index_request, functions.messages.SendMessageRequest)
        assert index_request.rich_message is not None

        native = next(row for row in module.CATALOG if row[0] == "native-guide")
        assert native
        picker = Event(data=b"rich:pick:native-guide")
        asyncio.run(callback_wrapper(picker))
        assert picker.answers == [((), {})]
        assert picker.edits == []
        assert len(picker.client.requests) == 1
        request = picker.client.requests[0]
        assert isinstance(request, functions.messages.SendMessageRequest)
        assert isinstance(request.rich_message, types.InputRichMessage)
        assert request.message == " "
        assert request.rich_message.blocks
        assert request.reply_markup is not None
        assert b"rich:send:native-guide" in bytes(request.reply_markup)

        denied = Event(sender_id=99)
        asyncio.run(command_wrapper(denied))
        assert denied.client.requests == []
        denied_cb = Event(sender_id=99, data=b"rich:pick:native-guide")
        asyncio.run(callback_wrapper(denied_cb))
        assert denied_cb.client.requests == []
        assert denied_cb.answers == [(("Not allowed!",), {})]

        # Exercise actual Telethon builders' predicate, not just the source regex.
        class CallbackEvent:
            def __init__(self, data):
                self.data = data
                self.query = SimpleNamespace(data=data)
                self._sender = SimpleNamespace(id=41)
        for n in range(1, 7):
            assert callback_builder.filter(CallbackEvent(f"rich:pick:heading-{n}".encode()))
        assert callback_builder.filter(CallbackEvent(b"rich:index:2"))
        assert not callback_builder.filter(CallbackEvent(b"rich:index:x"))
        assert not callback_builder.filter(CallbackEvent(b"rich:index:100"))
    finally:
        sys.modules.pop(name, None)

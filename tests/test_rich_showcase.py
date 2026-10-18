"""Offline behavior of the native owner-only RichMessage showcase."""
from __future__ import annotations

import asyncio
import importlib.util
import itertools
import sys
import types as pytypes
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import TelegramClient, types
from telethon.errors import MessageNotModifiedError, RPCError
from telethon.extensions import BinaryReader
from telethon.tl import alltlobjects, functions

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "assistant" / "rich_showcase.py"
_NAMES = itertools.count()
# Explicit expectations ensure no catalog entry is accidentally skipped by a
# conditional test (the previous test allowed None/plain/note-only payloads).
IDS = (
    "native", *(f"heading-{n}" for n in range(1, 7)), "inline", "extra-styles",
    "spoiler", "links", "emoji", "date", "divider", "footer", "lists", "ordered",
    "tasks", "code", "tables", "quotes", "pullquote", "rich-expand", "details",
    "details-open", "details-code", "details-steps", "native-guide", "formula",
    "anchors", "map", "rich-buttons", "text-buttons", "block-quotes",
    "nested-details", "text-entities", "details-wrapper", "rtl",
    "work-plan", "decision-brief", "meeting-agenda", "learning-path", "project-review",
    "matrix-arcade",
)


@pytest.fixture
def showcase(monkeypatch):
    registrations = {}
    assistant = pytypes.ModuleType("pyUltroid._misc._assistant")

    def decorator(kind):
        def register(*args, **kwargs):
            def decorate(func):
                registrations[kind] = (args, kwargs, func)
                return func
            return decorate
        return register

    assistant.asst_cmd = decorator("command")
    assistant.callback = decorator("callback")
    monkeypatch.setitem(sys.modules, "pyUltroid._misc._assistant", assistant)
    name = f"rich_showcase_test_{next(_NAMES)}"
    spec = importlib.util.spec_from_file_location(name, SOURCE)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module, registrations


def button_data(button):
    return button.to_dict()["type"]["data"]


class FakeClient:
    # Exercise the real Telethon markup builder, not a hand-built lookalike.
    build_reply_markup = staticmethod(TelegramClient.build_reply_markup)

    def __init__(self, failure=None):
        self.requests = []
        self.failure = failure
        self.peers = []

    async def get_input_entity(self, peer):
        self.peers.append(peer)
        return types.InputPeerUser(123, 0)

    async def __call__(self, request):
        encoded = bytes(request)
        decoded = BinaryReader(encoded).tgread_object()
        assert bytes(decoded.rich_message) == bytes(request.rich_message)
        self.requests.append(request)
        # Reject just the sample; allow the native diagnostic-index edit.
        if self.failure and len(self.requests) == 1:
            raise self.failure
        return SimpleNamespace(id=901)


class FakeEvent:
    def __init__(self, client=None, data=b"", message_id=77):
        self.client = client or FakeClient()
        self.data = data
        self.chat_id = 123
        self.message_id = message_id
        self.answers = 0

    async def answer(self):
        self.answers += 1

    async def respond(self, *args, **kwargs):
        pytest.fail("No plain respond/label fallback is permitted")

    async def edit(self, *args, **kwargs):
        pytest.fail("No plain event.edit/description-only path is permitted")


async def click(module, event, action, value):
    event.data = f"rich:{action}:{value}".encode("ascii")
    return await module.rich_callback(event)


def constructors(value):
    if isinstance(value, dict):
        if "_" in value:
            yield value["_"]
        for child in value.values():
            yield from constructors(child)
    elif isinstance(value, (tuple, list)):
        for child in value:
            yield from constructors(child)


def assert_native(request, kind, message_id=None):
    assert isinstance(request, kind)
    assert isinstance(request.rich_message, types.InputRichMessage)
    assert request.rich_message.blocks
    assert request.message == " "
    assert not request.entities
    assert isinstance(request.reply_markup, types.ReplyInlineMarkup)
    assert "PageBlockTitle" not in set(constructors(request.to_dict()))
    if message_id is not None:
        assert request.id == message_id
    assert bytes(request)


def test_registration_and_complete_keyboard_index(showcase):
    module, registrations = showcase
    assert alltlobjects.LAYER == 230
    assert registrations["command"][1] == {"pattern": "rich", "owner": True}
    assert registrations["callback"][1] == {"owner": True}
    assert registrations["callback"][0][0] == module.CALLBACK_PATTERN
    assert tuple(sample[0] for sample in module.CATALOG) == IDS
    assert len(IDS) == len(set(IDS))
    keys = []
    for page in range((len(IDS) + module.PAGE_SIZE - 1) // module.PAGE_SIZE):
        for row in module._keyboard(page):
            for button in row:
                data = button_data(button)
                assert len(data) <= 64
                assert module.CALLBACK_PATTERN.fullmatch(data.decode())
                if data.startswith(b"rich:pick:"):
                    keys.append(data.decode().split(":")[-1])
        index = module._index(page)
        assert isinstance(index, types.InputRichMessage) and bytes(index)
    assert tuple(keys) == IDS
    assert module._page(-100) == 0
    assert module._page(100) == (len(IDS) - 1) // module.PAGE_SIZE


def test_command_sends_a_native_index_without_editing_incoming_command(showcase):
    module, _ = showcase
    event = FakeEvent()
    result = asyncio.run(module.rich(event))
    assert result.id == 901 and event.answers == 0
    assert len(event.client.requests) == 1
    assert_native(event.client.requests[0], functions.messages.SendMessageRequest)
    assert "Tap an index button" in str(event.client.requests[0].rich_message.to_dict())


@pytest.mark.parametrize("sid", IDS)
@pytest.mark.parametrize("action", ("pick", "send", "edit"))
def test_every_index_entry_sends_or_edits_native_payload_through_callback(showcase, sid, action):
    module, _ = showcase
    event = FakeEvent()
    result = asyncio.run(click(module, event, action, sid))
    assert result.id == 901
    assert event.answers == 1
    assert event.client.peers == [event.chat_id]
    assert len(event.client.requests) == 1
    request = event.client.requests[0]
    kind = functions.messages.EditMessageRequest if action == "edit" else functions.messages.SendMessageRequest
    assert_native(request, kind, event.message_id if action == "edit" else None)
    expected = module._payload(module._sample(sid))
    assert request.rich_message.to_dict() == expected.to_dict()
    data = {button_data(button).decode() for row in module._keyboard(selected=sid) for button in row}
    assert data == {f"rich:send:{sid}", f"rich:edit:{sid}", f"rich:index:{module._sample_page(sid)}"}


def test_details_remain_native_and_guide_keeps_all_seven_sections(showcase):
    module, _ = showcase
    payload = module._payload(module._sample("native-guide"))
    names = list(constructors(payload.to_dict()))
    assert names.count("PageBlockDetails") == 7
    assert names.count("PageBlockPreformatted") == 7
    assert names.count("PageBlockDivider") == 2
    assert isinstance(payload.blocks[0], types.PageBlockParagraph)
    assert isinstance(payload.blocks[0].text, types.TextBold)
    assert module._payload(module._sample("details-open")).blocks[0].open is True
    assert module._payload(module._sample("details")).blocks[0].open is False
    assert isinstance(module._payload(module._sample("details-wrapper")).blocks[0], types.PageBlockDetails)


@pytest.mark.parametrize("old", ("plain", "html", "markdown", "headings", "native-title", "details-native", "native-open", "native-code", "native-steps", "legacy-styles", "legacy-expand", "divider-footer"))
def test_old_index_buttons_route_to_native_examples(showcase, old):
    module, _ = showcase
    event = FakeEvent()
    asyncio.run(click(module, event, "pick", old))
    assert len(event.client.requests) == 1 and event.answers == 1
    assert_native(event.client.requests[0], functions.messages.SendMessageRequest)


def test_navigation_unknown_and_malformed_callbacks(showcase):
    module, _ = showcase
    event = FakeEvent()
    asyncio.run(click(module, event, "ping", "ok"))
    assert event.answers == 1 and not event.client.requests
    for invalid in (b"rich:send:", b"not-rich", b"rich:pick:\xff", b"rich:edit:heading-99!"):
        event.data = invalid
        asyncio.run(module.rich_callback(event))
    assert event.answers == 1 and not event.client.requests
    asyncio.run(click(module, event, "index", "99"))
    assert_native(event.client.requests[-1], functions.messages.EditMessageRequest, event.message_id)
    for removed in ("media", "collage", "footnotes", "unknown"):
        event = FakeEvent()
        asyncio.run(click(module, event, "pick", removed))
        assert len(event.client.requests) == 1
        assert_native(event.client.requests[0], functions.messages.EditMessageRequest, event.message_id)
        assert "unknown or unavailable sample" in str(event.client.requests[0].rich_message.to_dict())


def test_rejection_is_sanitized_and_never_retries_as_plain_text(showcase):
    module, _ = showcase
    for failure, identifier in (
        (RPCError(None, "RICH_MESSAGE_BLOCKS_INVALID"), "RICH_MESSAGE_BLOCKS_INVALID"),
        (TypeError("private local details"), "LOCAL_VALIDATION_ERROR"),
    ):
        event = FakeEvent(FakeClient(failure))
        result = asyncio.run(click(module, event, "pick", "details"))
        assert result is None and event.answers == 1
        assert len(event.client.requests) == 2
        assert_native(event.client.requests[0], functions.messages.SendMessageRequest)
        assert_native(event.client.requests[1], functions.messages.EditMessageRequest, event.message_id)
        error_payload = str(event.client.requests[1].rich_message.to_dict())
        assert f"details/send: {identifier}" in error_payload
        assert "private local details" not in error_payload
    event = FakeEvent(FakeClient(RuntimeError("boom")))
    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(click(module, event, "pick", "details"))
    assert len(event.client.requests) == 1


def test_unchanged_native_navigation_is_harmless(showcase):
    module, _ = showcase
    event = FakeEvent(FakeClient(MessageNotModifiedError(None)))
    asyncio.run(click(module, event, "index", "0"))
    assert event.answers == 1 and len(event.client.requests) == 1

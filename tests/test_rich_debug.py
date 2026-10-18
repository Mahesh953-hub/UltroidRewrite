"""Offline privacy and behavior tests for the RichMessage diagnostics helpers."""

from __future__ import annotations

import logging

import pytest
from telethon import functions, types
from telethon.errors import RPCError

from pyUltroid._core import richdebug


class ErrorLike:
    def __init__(self, message: object, code: object = None) -> None:
        self.message = message
        self.code = code

    def __str__(self) -> str:
        raise AssertionError("diagnostics must never stringify errors")


@pytest.mark.parametrize(
    "label",
    [
        "RICH_MESSAGE_BLOCKS_INVALID",
        "RICH_TEXT_TOO_LONG",
        "PAGE_BLOCK_DETAILS_INVALID",
        "MESSAGE_NOT_MODIFIED",
    ],
)
def test_rpc_identifier_accepts_known_or_evolving_safe_labels(label: str) -> None:
    assert richdebug.rpc_identifier(ErrorLike(label)) == label


@pytest.mark.parametrize(
    "label",
    [
        "RICH_MESSAGE_block_invalid",
        "RICH_MESSAGE_BLOCKS_INVALID ",
        "RICH_MESSAGE_BLOCKS_INVALID\nsecret",
        "RICH_MESSAGE_",
        "RICH_MESSAGE_" + "A" * 100,
        "SECRET_TOKEN_ABC",
        "message_not_modified",
        None,
    ],
)
def test_rpc_identifier_rejects_untrusted_labels_without_stringifying(label: object) -> None:
    assert richdebug.rpc_identifier(ErrorLike(label)) == "UNKNOWN_RPC"


def test_rich_schema_counts_nested_known_constructors_without_content_or_ids() -> None:
    secret = "private body and document-id-987654"
    content = types.InputRichMessage(
        [types.PageBlockDetails([types.PageBlockTitle(types.TextPlain(secret))], types.TextPlain("hidden title"))]
    )

    schema = richdebug.rich_schema(content)

    assert schema["constructors"] == {
        "InputRichMessage": 1,
        "PageBlockDetails": 1,
        "PageBlockTitle": 1,
        "TextPlain": 2,
    }
    assert secret not in repr(schema)
    assert "hidden title" not in repr(schema)
    assert "document-id-987654" not in repr(schema)
    assert schema["characters"] == len(secret) + len("hidden title")
    assert schema["schema_truncated"] is False


def test_html_schema_counts_markup_characters_and_allowlisted_tags_only() -> None:
    markup = '<details data-private="SECRET"><summary>secret summary</summary><blockquote>private quote</blockquote><script>token</script><code>x</code></details>'
    schema = richdebug.rich_schema(types.InputRichMessageHTML(markup))

    assert schema["constructors"] == {"InputRichMessageHTML": 1}
    assert schema["html_tags"] == {"blockquote": 1, "code": 1, "details": 1, "summary": 1}
    assert schema["characters"] == len(markup)
    for secret in ("SECRET", "secret summary", "private quote", "token", "data-private"):
        assert secret not in repr(schema)


def test_rich_schema_bounds_cycles_and_ignores_untrusted_objects() -> None:
    cycle: list[object] = []
    cycle.append(cycle)
    cycle.extend([object()] * 5000)

    schema = richdebug.rich_schema(cycle)

    assert schema["constructors"] == {}
    assert schema["html_tags"] == {}
    assert schema["characters"] == 0
    assert schema["schema_truncated"] is True


def test_debug_trace_redacts_error_text_peers_and_secrets(caplog: pytest.LogCaptureFixture) -> None:
    err = ErrorLike("RICH_MESSAGE_BLOCKS_INVALID", 400)
    caplog.set_level(logging.DEBUG, logger=richdebug.LOGGER.name)

    richdebug.trace_rich_rpc(
        "request", content=types.InputRichMessageHTML("<p>TEXT_SECRET</p>"), error=err,
        request_kind="edit_message", target_kind="peer", outcome="rpc_error",
    )

    output = caplog.text
    assert "RICH_MESSAGE_BLOCKS_INVALID" in output
    assert '"rpc_code":400' in output
    assert "TEXT_SECRET" not in output
    assert "peer-id-secret" not in output
    assert "access_hash" not in output
    assert "document" not in output
    assert "API_SECRET" not in output


def test_trace_sanitizes_invalid_fields_and_bounds_rpc_code(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=richdebug.LOGGER.name)
    richdebug.trace_rich_rpc(
        "request\nSECRET", error=ErrorLike("PAGE_BLOCK_INVALID", 1001),
        request_kind="BadName", target_kind="PEER id=123", outcome="rpc_error",
    )

    line = caplog.text
    assert '"stage":"unknown"' in line
    assert '"request":"unknown"' in line
    assert '"target":"unknown"' in line
    assert '"rpc_id":"PAGE_BLOCK_INVALID"' in line
    assert '"rpc_code":null' in line
    assert "SECRET" not in line and "123" not in line


@pytest.mark.asyncio
async def test_call_rich_request_preserves_normal_result() -> None:
    result = object()

    class Client:
        async def __call__(self, request: object) -> object:
            return result

    actual = await richdebug.call_rich_request(Client(), functions.messages.SendMessageRequest("peer", "text"))
    assert actual is result


@pytest.mark.asyncio
async def test_call_rich_request_preserves_rpc_error_identity_and_redacts_trace(caplog: pytest.LogCaptureFixture) -> None:
    class TestRPCError(RPCError):
        def __init__(self) -> None:
            super().__init__(request=None, message="RICH_TEXT_INVALID", code=400)

        def __str__(self) -> str:
            return "BOT_TOKEN_SECRET peer=123 access_hash=456"

    error = TestRPCError()

    class Client:
        async def __call__(self, request: object) -> None:
            raise error

    caplog.set_level(logging.DEBUG, logger=richdebug.LOGGER.name)
    with pytest.raises(TestRPCError) as caught:
        await richdebug.call_rich_request(Client(), functions.messages.SendMessageRequest("peer-secret", "body-secret"))

    assert caught.value is error
    assert "BOT_TOKEN_SECRET" not in caplog.text
    assert "peer-secret" not in caplog.text
    assert "body-secret" not in caplog.text
    assert "access_hash" not in caplog.text
    assert "RICH_TEXT_INVALID" in caplog.text


@pytest.mark.asyncio
async def test_call_rich_request_uses_exported_sender_call_path() -> None:
    sender = object()
    request = functions.messages.SendMessageRequest("peer", "text")
    result = object()

    class Client:
        def __init__(self) -> None:
            self.args: tuple[object, object] | None = None
            self.called = False

        async def __call__(self, unused: object) -> None:
            self.called = True

        async def _call(self, actual_sender: object, actual_request: object) -> object:
            self.args = (actual_sender, actual_request)
            return result

    client = Client()
    assert await richdebug.call_rich_request(client, request, sender=sender) is result
    assert client.args == (sender, request)
    assert client.called is False


@pytest.mark.asyncio
async def test_call_rich_request_propagates_local_error_without_fallback() -> None:
    error = ValueError("safe failure")

    class Client:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, request: object) -> None:
            self.calls += 1
            raise error

    client = Client()
    with pytest.raises(ValueError) as caught:
        await richdebug.call_rich_request(client, object())
    assert caught.value is error
    assert client.calls == 1


def test_module_does_not_expose_sender_lifecycle_helpers() -> None:
    assert not any("sender" in name.lower() and any(part in name.lower() for part in ("connect", "disconnect", "close")) for name in vars(richdebug))


def test_generated_rpc_classes_keep_their_specific_identifier():
    from telethon.errors import MessageIdInvalidError, MessageNotModifiedError

    assert richdebug.rpc_identifier(MessageNotModifiedError(None)) == "MESSAGE_NOT_MODIFIED"
    assert richdebug.rpc_identifier(MessageIdInvalidError(None)) == "MESSAGE_ID_INVALID"

from __future__ import annotations

import ast
import asyncio
from types import SimpleNamespace

import pytest
from conftest import ROOT
from telethon import Button, events, functions, types
from telethon.errors import BadRequestError, RPCError


def _load_helpers(helpers):
    path = ROOT / "plugins/_inline.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = {
        "_edit_rich_callback", "_show_deploy_guide",
        "_deploy_rpc_diagnostic", "_log_deploy_rpc",
    }
    functions_to_load = [
        node
        for node in tree.body
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name in selected
    ]
    module = ast.fix_missing_locations(ast.Module(body=functions_to_load, type_ignores=[]))
    namespace = {
        "RPCError": RPCError, "BadRequestError": BadRequestError,
        "InputBotInlineMessageID": types.InputBotInlineMessageID,
        "InputBotInlineMessageID64": types.InputBotInlineMessageID64,
        **helpers,
    }
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - isolated helpers
    namespace.update(helpers)
    return namespace


def test_hi_button_payload_matches_registered_telethon_callback():
    from custom.rich import button

    btn = button("Hi", data="alive_deploy_hi", preset="success")
    payload = btn.to_dict()["type"]["data"]
    matcher = events.CallbackQuery(data="alive_deploy_hi")
    event = SimpleNamespace(
        query=SimpleNamespace(data=payload, chat_instance="test-chat"),
        chat_id=None,
    )

    assert payload == b"alive_deploy_hi"
    assert matcher.filter(event) is True


def test_hi_callback_uses_native_layer230_inline_rich_edit_without_plain_sender():
    from pyUltroid._core import richmsg

    class FakeClient:
        def __init__(self):
            self.requests = []

        async def __call__(self, request):
            self.requests.append(request)
            return True

        def build_reply_markup(self, _buttons):
            return None

        async def send_message(self, *_args, **_kwargs):
            pytest.fail("the callback must not send a plain message")

    class FakeEvent:
        def __init__(self, client, inline_id):
            self.client = client
            self.query = SimpleNamespace(msg_id=inline_id)
            self.chat_id = None
            self.answers = []

        async def edit(self, *_args, **_kwargs):
            pytest.fail("the callback must not use the legacy event.edit path")

        async def answer(self, *args, **kwargs):
            self.answers.append((args, kwargs))

    inline_id = types.InputBotInlineMessageID(dc_id=2, id=5, access_hash=99)
    client = FakeClient()
    event = FakeEvent(client, inline_id)
    show = _load_helpers({"edit_rich": richmsg.edit_rich})["_show_deploy_guide"]

    result = asyncio.run(
        show(
            event,
            richmsg.RichMarkup("<p>Native deployment guide</p>", "html"),
            [[Button.url("Repository", "https://example.com/repository")]],
        )
    )

    assert result is True
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, functions.messages.EditInlineBotMessageRequest)
    assert request.id is inline_id
    assert isinstance(request.rich_message, types.InputRichMessageHTML)
    assert event.answers == [((), {})]


def test_hi_callback_rich_edit_failure_answers_with_alert_without_plain_fallback():
    calls = []

    class FakeEvent:
        async def edit(self, *args, **kwargs):
            calls.append(("event.edit", args, kwargs))
            pytest.fail("a rich edit failure must not fall back to event.edit")

        async def answer(self, *args, **kwargs):
            calls.append(("answer", args, kwargs))

    async def fail_rich_edit(*_args, **_kwargs):
        raise BadRequestError(None, "MESSAGE_ID_INVALID", code=400)

    logs = SimpleNamespace(warning=lambda *args: calls.append(("log", args)))
    show = _load_helpers(
        {"LOGS": logs, "_edit_rich_callback": fail_rich_edit}
    )["_show_deploy_guide"]
    result = asyncio.run(show(FakeEvent(), "rich guide"))

    assert result is None
    assert len(calls) == 2
    assert calls[0][0] == "log"
    assert "rpc_id=%s rpc_type=%s" in calls[0][1][0]
    assert calls[0][1][1:3] == ("MESSAGE_ID_INVALID", "BadRequestError")
    assert calls[1] == (
        "answer",
        ("The guide could not be updated. Please try again.",),
        {"alert": True},
    )


def test_hi_callback_rich_message_unsupported_falls_back_to_layer230_plain_inline_edit():
    from pyUltroid._core import richmsg

    class FakeClient:
        def __init__(self):
            self.requests = []

        async def __call__(self, request):
            self.requests.append(request)
            return True

        def build_reply_markup(self, buttons):
            return ("inline-markup", buttons)

        async def send_message(self, *_args, **_kwargs):
            pytest.fail("fallback must edit the existing inline message")

    class FakeEvent:
        def __init__(self, client, inline_id):
            self.client = client
            self.query = SimpleNamespace(msg_id=inline_id)
            self.answers = []

        async def edit(self, *_args, **_kwargs):
            pytest.fail("fallback must not use the legacy event.edit path")

        async def answer(self, *args, **kwargs):
            self.answers.append((args, kwargs))

    inline_id = types.InputBotInlineMessageID(dc_id=2, id=5, access_hash=99)
    client = FakeClient()
    event = FakeEvent(client, inline_id)
    content = (
        richmsg.RichBlocks(native=True)
        .header("Deploy your own Ultroid")
        .expandable("Install", "Create a virtual environment")
    )

    async def reject_rich_edit(*_args, **_kwargs):
        raise BadRequestError(None, "RICH_MESSAGE_UNSUPPORTED", code=400)

    logs = SimpleNamespace(warning=lambda *_args: None)
    show = _load_helpers(
        {
            "LOGS": logs,
            "_edit_rich_callback": reject_rich_edit,
            "blocks_to_plain_text": richmsg.blocks_to_plain_text,
            "edit_inline_plain": richmsg.edit_inline_plain,
        }
    )["_show_deploy_guide"]
    buttons = [[Button.url("Repository", "https://example.com/repository")]]

    result = asyncio.run(show(event, content, buttons))

    assert result is True
    assert len(client.requests) == 1
    request = client.requests[0]
    assert isinstance(request, functions.messages.EditInlineBotMessageRequest)
    assert request.id is inline_id
    assert request.rich_message is None
    assert request.message == richmsg.blocks_to_plain_text(content)
    assert "Deploy your own Ultroid" in request.message
    assert "Create a virtual environment" in request.message
    assert request.reply_markup == ("inline-markup", buttons)
    assert event.answers == [((), {})]


def test_hi_callback_source_only_routes_guide_through_rich_helper():
    source = (ROOT / "plugins/_inline.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    callback = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "_cb_alive_deploy_hi"
    )
    calls = [node for node in ast.walk(callback) if isinstance(node, ast.Call)]

    assert any(
        isinstance(call.func, ast.Name) and call.func.id == "_show_deploy_guide"
        for call in calls
    )
    assert not any(
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "event"
        and call.func.attr == "edit"
        for call in calls
    )
    assert not any(
        isinstance(node, ast.Name) and node.id == "fallback_text"
        for node in ast.walk(callback)
    )


@pytest.mark.parametrize("message, expected", [
    ("RICH_MESSAGE_UNSUPPORTED", "RICH_MESSAGE_UNSUPPORTED"),
    ("MESSAGE_ID_INVALID", "MESSAGE_ID_INVALID"),
    ("RICH_MESSAGE_UNSUPPORTED secret-token chat-content", "UNKNOWN_RPC"),
    ("rich_message_unsupported", "UNKNOWN_RPC"),
    ("MESSAGE_ID_INVALID\nprivate-content", "UNKNOWN_RPC"),
    ("SECRET_IDENTIFIER", "UNKNOWN_RPC"),
    (None, "UNKNOWN_RPC"),
    ([], "UNKNOWN_RPC"),
])
def test_deploy_rpc_diagnostic_only_emits_allowlisted_labels(message, expected):
    diagnostic = _load_helpers({})["_deploy_rpc_diagnostic"]
    error = BadRequestError(None, "initial", code=400)
    error.message = message
    assert diagnostic(error) == (expected, "BadRequestError")


def test_deploy_rpc_diagnostic_does_not_stringify_or_emit_custom_class_name():
    class PrivateError(RPCError):
        def __str__(self):
            raise AssertionError("must not stringify an RPC error")

    error = PrivateError(None, "private-content", code=500)
    diagnostic = _load_helpers({})["_deploy_rpc_diagnostic"]
    assert diagnostic(error) == ("UNKNOWN_RPC", "RPCError")


def _diagnostic_scenario(rich_error, *, target="query", fallback_error=None, answer_error=None):
    records, attempts, answers = [], [], []
    inline_id = types.InputBotInlineMessageID(dc_id=2, id=5, access_hash=99)
    if target == "message64":
        inline_id = types.InputBotInlineMessageID64(dc_id=2, owner_id=1, id=5, access_hash=99)
    event = SimpleNamespace(
        client=object(), query=SimpleNamespace(msg_id=None),
        message_id=None, chat_id=None,
    )
    if target == "query":
        event.query.msg_id = inline_id
    elif target == "message64":
        event.message_id = inline_id
    elif target == "normal":
        event.message_id, event.chat_id = 7, 8
    elif target == "fake":
        event.query.msg_id = type("InputBotInlineMessageID", (), {})()

    async def edit_rich(*_args):
        raise rich_error

    async def fallback(*args, **kwargs):
        attempts.append((args, kwargs))
        if fallback_error is not None:
            raise fallback_error
        return True

    async def answer(*args, **kwargs):
        answers.append((args, kwargs))
        if answer_error is not None:
            raise answer_error

    event.answer = answer
    show = _load_helpers({
        "LOGS": SimpleNamespace(warning=lambda fmt, *args: records.append(fmt % args)),
        "_edit_rich_callback": edit_rich,
        "edit_inline_plain": fallback,
        "blocks_to_plain_text": lambda _content: "private-guide-content",
    })["_show_deploy_guide"]
    return show, event, records, attempts, answers


@pytest.mark.parametrize("target, recognized, inline", [
    ("query", True, True), ("message64", True, True),
    ("normal", True, False), ("missing", False, False), ("fake", False, False),
])
def test_deploy_target_and_fallback_diagnostic_branches(target, recognized, inline):
    show, event, records, attempts, answers = _diagnostic_scenario(
        BadRequestError(None, "RICH_MESSAGE_UNSUPPORTED", code=400), target=target,
    )
    assert asyncio.run(show(event, "private-guide-content")) is (True if inline else None)
    assert len(attempts) == int(inline)
    assert f"message_target_recognized={recognized}" in records[0]
    assert f"inline_target_recognized={inline}" in records[0]
    assert f"fallback_attempted={inline} fallback_succeeded=False" in records[0]
    assert f"fallback_attempted={inline} fallback_succeeded={inline}" in records[-1]
    assert answers[-1][1] == ({} if inline else {"alert": True})
    assert all("private-guide-content" not in record for record in records)


@pytest.mark.parametrize("message", [
    "MESSAGE_ID_INVALID", "private-token-message",
    "RICH_MESSAGE_UNSUPPORTED private-token-message", "rich_message_unsupported",
])
def test_deploy_other_rpc_errors_never_trigger_fallback_or_leak_text(message):
    show, event, records, attempts, answers = _diagnostic_scenario(
        BadRequestError(None, message, code=400),
    )
    assert asyncio.run(show(event, "private-guide-content")) is None
    assert not attempts
    assert answers[-1][1] == {"alert": True}
    assert all("private-token-message" not in record for record in records)
    assert all("fallback_attempted=False fallback_succeeded=False" in record for record in records)


def test_deploy_fallback_rpc_failure_is_sanitized_and_alerts():
    show, event, records, attempts, answers = _diagnostic_scenario(
        BadRequestError(None, "RICH_MESSAGE_UNSUPPORTED", code=400),
        fallback_error=BadRequestError(None, "private-token-message", code=400),
    )
    assert asyncio.run(show(event, "content")) is None
    assert len(attempts) == 1
    assert "rpc_id=UNKNOWN_RPC" in records[-1]
    assert "fallback_attempted=True fallback_succeeded=False" in records[-1]
    assert "private-token-message" not in " ".join(records)
    assert answers[-1][1] == {"alert": True}


@pytest.mark.parametrize("stage", ["rich", "fallback", "answer", "success_answer"])
def test_deploy_unrelated_exceptions_propagate(stage):
    unrelated = RuntimeError("private-programming-error")
    show, event, records, _attempts, _answers = _diagnostic_scenario(
        unrelated if stage == "rich" else BadRequestError(None, "RICH_MESSAGE_UNSUPPORTED", code=400),
        target="missing" if stage == "answer" else "query",
        fallback_error=unrelated if stage == "fallback" else None,
        answer_error=unrelated if stage in {"answer", "success_answer"} else None,
    )
    with pytest.raises(RuntimeError, match="private-programming-error"):
        asyncio.run(show(event, "content"))
    assert "private-programming-error" not in " ".join(records)


def test_deploy_expired_alert_rpc_is_sanitized():
    show, event, records, _attempts, _answers = _diagnostic_scenario(
        BadRequestError(None, "MESSAGE_ID_INVALID", code=400),
        answer_error=BadRequestError(None, "QUERY_ID_INVALID", code=400),
    )
    assert asyncio.run(show(event, "content")) is None
    assert "rpc_id=QUERY_ID_INVALID" in records[-1]


def _native_deploy_guide():
    path = ROOT / "plugins/_inline.py"
    node = next(
        node for node in ast.parse(path.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "_deploy_guide_blocks"
    )
    namespace = {}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102 - isolated builder
    return namespace[node.name]("Example <User>")


def test_nested_guide_inline_fallback_is_bounded_and_honest_about_missing_controls():
    show, event, _records, attempts, answers = _diagnostic_scenario(
        BadRequestError(None, "RICH_MESSAGE_UNSUPPORTED", code=400),
    )
    assert asyncio.run(show(event, _native_deploy_guide())) is True
    assert len(attempts) == 1
    text = attempts[0][0][2]
    assert len(text.encode("utf-16-le")) // 2 <= 4096
    assert "collapsibles, tables and in-section copy/URL buttons are unavailable" in text
    assert "Step 7" in text and "uv run --no-sync python3 -m pyUltroid" in text
    assert "SESSION=" in text and "MONGO_URI=" in text
    assert "Never share SESSION, MONGO_URI" in text
    assert answers == [((), {})]


def test_hi_callback_keeps_actual_in_body_buttons_in_native_inline_edit():
    from pyUltroid._core import richmsg

    class Client:
        def __init__(self):
            self.requests = []

        async def __call__(self, request):
            self.requests.append(request)
            return True

        def build_reply_markup(self, _buttons):
            return None

    client = Client()
    inline_id = types.InputBotInlineMessageID(dc_id=2, id=5, access_hash=99)
    event = SimpleNamespace(client=client, query=SimpleNamespace(msg_id=inline_id))
    guide = _native_deploy_guide()
    edit = _load_helpers({"edit_rich": richmsg.edit_rich})["_edit_rich_callback"]
    assert asyncio.run(edit(event, guide, None)) is True
    request, = client.requests
    assert isinstance(request, functions.messages.EditInlineBotMessageRequest)
    assert request.reply_markup is None
    assert bytes(request.rich_message) == bytes(guide)
    step_one = next(block for block in request.rich_message.blocks if isinstance(block, types.PageBlockDetails))
    row = next(block for block in step_one.blocks if isinstance(block, types.PageBlockButtonRow))
    assert isinstance(row.buttons[0].type, types.InlineButtonTypeCopy)

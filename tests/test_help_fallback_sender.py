from __future__ import annotations

import ast
import asyncio
import html
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


class BotResponseTimeoutError(Exception):
    pass


def _load_help_handler(namespace):
    source = (ROOT / "plugins/_help.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_help"
    )
    module = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
    exec(compile(module, "plugins/_help.py", "exec"), namespace)  # noqa: S102
    return namespace["_help"]


def _handler_namespace(assistant, send_rich):
    return {
        "ultroid_cmd": lambda **_kwargs: lambda function: function,
        "BotMethodInvalidError": type("BotMethodInvalidError", (Exception,), {}),
        "BotResponseTimeoutError": BotResponseTimeoutError,
        "BotInlineDisabledError": type("BotInlineDisabledError", (Exception,), {}),
        "LOGS": SimpleNamespace(
            warning=lambda *_args: None,
            exception=lambda *_args: None,
        ),
        "asst": assistant,
        "send_rich": send_rich,
        "_help_blocks": lambda: object(),
        "_fallback_help_buttons": lambda: [],
        "main_help_menu_buttons": lambda: [["bot callback buttons"]],
        "command_matches": lambda *_args: False,
        "command_aliases": lambda *_args: (),
        "LIST": {},
        "HELP": {},
        "HNDLR": ".",
        "html": html,
    }


def _event(user_client, *, deleted=None, reply_to_msg_id=17):
    class Event:
        client = user_client
        chat_id = 42
        pattern_match = SimpleNamespace(group=lambda _index: "")

        async def get_chat(self):
            return SimpleNamespace(id=42)

        async def delete(self):
            if deleted is not None:
                deleted.append(True)

    event = Event()
    event.reply_to_msg_id = reply_to_msg_id
    return event


def test_inline_help_clicks_result_with_bot_attribution_and_no_second_message():
    calls = []
    deleted = []
    fallback_calls = []

    class InlineResult:
        async def click(self, chat_id, *, reply_to=None, hide_via=None):
            calls.append(("click", chat_id, reply_to, hide_via))

    class UserClient:
        async def inline_query(self, username, query):
            calls.append(("query", username, query))
            return [InlineResult()]

    assistant = SimpleNamespace(me=SimpleNamespace(username="helper", bot=True))
    user_client = UserClient()

    async def send_rich(*args, **kwargs):
        fallback_calls.append((args, kwargs))

    handler = _load_help_handler(_handler_namespace(assistant, send_rich))
    asyncio.run(handler(_event(user_client, deleted=deleted)))

    assert calls == [
        ("query", "helper", "ultd"),
        ("click", 42, 17, False),
    ]
    assert deleted == [True]
    assert fallback_calls == []


def test_inline_help_failure_does_not_send_a_standalone_user_message():
    fallback_calls = []
    deleted = []

    class UserClient:
        async def inline_query(self, *_args):
            raise BotResponseTimeoutError("timed out")

    assistant = SimpleNamespace(me=SimpleNamespace(username="helper", bot=True))

    async def send_rich(*args, **kwargs):
        fallback_calls.append((args, kwargs))

    handler = _load_help_handler(_handler_namespace(assistant, send_rich))
    asyncio.run(handler(_event(UserClient(), deleted=deleted)))

    assert fallback_calls == []
    assert deleted == []


def test_empty_inline_help_results_do_not_send_a_standalone_message():
    fallback_calls = []
    deleted = []

    class UserClient:
        async def inline_query(self, *_args):
            return []

    assistant = SimpleNamespace(me=SimpleNamespace(username="helper", bot=True))

    async def send_rich(*args, **kwargs):
        fallback_calls.append((args, kwargs))

    handler = _load_help_handler(_handler_namespace(assistant, send_rich))
    asyncio.run(handler(_event(UserClient(), deleted=deleted)))

    assert fallback_calls == []
    assert deleted == []

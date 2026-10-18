"""Live inline menus must not replay earlier application or Telegram snapshots.

Only native Telethon objects are used; the recording client never opens a session
or a network connection. Loading just the handlers avoids plugin registration.
"""

from __future__ import annotations

import ast
import asyncio
import html
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import Button, TelegramClient, events, functions, types

from pyUltroid._core.events import InlineResultBuilder
from pyUltroid._core.richmsg import RichMarkup

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "plugins/_inline.py"


class RecordingClient:
    build_reply_markup = staticmethod(TelegramClient.build_reply_markup)

    def __init__(self):
        self.requests = []

    async def __call__(self, request):
        assert isinstance(request, functions.messages.SetInlineBotResultsRequest)
        self.requests.append(request)
        return True


class ForbiddenCache:
    def get(self, *_args, **_kwargs):
        pytest.fail("live inline menus must not read the application result cache")

    def set(self, *_args, **_kwargs):
        pytest.fail("live inline menus must not populate the application result cache")


@pytest.fixture
def live_handlers():
    state = SimpleNamespace(uptime="1 second", style="classic", help="old help")
    routes = {}
    catalogs = {"Official": {"alpha": "old docs"}, "Addons": {}}
    commands = {"alpha": ["old$"]}

    def in_pattern(pattern=None, owner=False, **kwargs):
        assert owner is True

        def register(function):
            route = events.InlineQuery(pattern=pattern, **kwargs)
            route.resolved = True
            routes[function.__name__] = route
            return function

        return register

    def buttons():
        return [[Button.inline(state.style, data=b"live-control")]]

    namespace = {
        "in_pattern": in_pattern,
        "InlineResultBuilder": InlineResultBuilder,
        "INLINE_ALIVE_RESULT_ID": "alive-deploy",
        "core": SimpleNamespace(inline_cache=ForbiddenCache()),
        "_buttons_style_tag": lambda: state.style,
        "inline_pic": lambda: None,
        "_sup_buttons": buttons,
        "main_help_menu_buttons": buttons,
        "_help_blocks": lambda: RichMarkup(f"<p>{state.help}</p>", "html"),
        "time": SimpleNamespace(time=lambda: 1),
        "start_time": 0,
        "fns": SimpleNamespace(time_formatter=lambda _elapsed: state.uptime),
        "HELP": catalogs,
        "LIST": commands,
        "OWNER_NAME": "Test owner",
        "RichMarkup": RichMarkup,
        "html": html,
    }
    names = {"inline_alive", "inline_handler", "_alive_rich_markup"}
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, str(SOURCE), "exec"), namespace)  # noqa: S102
    return SimpleNamespace(
        namespace=namespace, state=state, routes=routes,
        catalogs=catalogs, commands=commands,
    )


def make_event(client, text="", query_id=1, user_id=101, offset=""):
    update = types.UpdateBotInlineQuery(
        query_id=query_id, user_id=user_id, query=text, offset=offset,
    )
    event = events.InlineQuery.Event(update)
    event._client = client
    return event


def answer(handlers, client, text="", query_id=1, user_id=101, offset=""):
    event = make_event(client, text, query_id, user_id, offset)
    matches = [name for name, route in handlers.routes.items() if route.filter(event)]
    assert len(matches) == 1
    asyncio.run(handlers.namespace[matches[0]](event))
    request = client.requests[-1]
    assert request.query_id == query_id
    assert request.cache_time == 0
    # MTProto's `private` is the equivalent of the Bot API's `is_personal`.
    assert request.private is True
    assert isinstance(request.results[0], types.InputBotInlineResult)
    assert isinstance(
        request.results[0].send_message, types.InputBotInlineMessageRichMessage,
    )
    assert bytes(request)
    return request


def test_empty_query_rebuilds_live_uptime_totals_and_buttons(live_handlers):
    client = RecordingClient()
    first = answer(live_handlers, client)
    live_handlers.state.uptime = "2 seconds"
    live_handlers.state.style = "styled"
    live_handlers.catalogs["Official"]["beta"] = "new docs"
    second = answer(live_handlers, client, query_id=2)

    assert len(client.requests) == 2
    before, after = first.results[0], second.results[0]
    assert before is not after
    assert bytes(before.send_message.rich_message) != bytes(after.send_message.rich_message)
    assert b"1 second" in bytes(before.send_message.rich_message)
    assert b"2 seconds" in bytes(after.send_message.rich_message)
    assert b"Plugins</td><td>2" in bytes(after.send_message.rich_message)
    assert before.send_message.reply_markup.rows[0].buttons[0].text == "classic"
    assert after.send_message.reply_markup.rows[0].buttons[0].text == "styled"
    # This ID is an exact selector for chosen-inline-result handling, not a cache key.
    assert before.id == after.id == "alive-deploy"
    assert second.switch_pm.start_param == "start"


def test_help_rebuilds_when_content_changes_without_count_or_style_changes(live_handlers):
    client = RecordingClient()
    first = answer(live_handlers, client, "ultd")
    live_handlers.state.help = "updated help"
    live_handlers.catalogs["Official"]["alpha"] = "updated docs"
    live_handlers.commands["alpha"] = ["new$"]
    second = answer(live_handlers, client, "ultd", query_id=2)

    assert len(live_handlers.catalogs["Official"]) == len(live_handlers.commands) == 1
    before, after = first.results[0], second.results[0]
    assert b"old help" in bytes(before.send_message.rich_message)
    assert b"updated help" in bytes(after.send_message.rich_message)
    assert before.id != after.id  # Help IDs remain content-derived, not random.
    assert first.gallery is second.gallery is True


def test_changing_query_user_and_offset_never_reuses_an_earlier_answer(live_handlers):
    client = RecordingClient()
    first = answer(live_handlers, client, "", query_id=1)
    help_result = answer(live_handlers, client, "ultd", query_id=2, offset="next")
    live_handlers.state.help = "latest menu"
    latest_help = answer(live_handlers, client, "ultd changed", query_id=3, user_id=202)
    live_handlers.state.uptime = "3 seconds"
    latest_alive = answer(live_handlers, client, "", query_id=4, user_id=202)

    assert [request.query_id for request in client.requests] == [1, 2, 3, 4]
    assert first.results[0].title == latest_alive.results[0].title == "Ultroid Userbot"
    assert help_result.results[0].title == latest_help.results[0].title == "Bot Help"
    assert b"latest menu" in bytes(latest_help.results[0].send_message.rich_message)
    assert b"3 seconds" in bytes(latest_alive.results[0].send_message.rich_message)


@pytest.mark.parametrize("text", ["alive", "calc 2+2", "yt updates", "unmatched"])
def test_live_menu_routes_do_not_intercept_other_queries(live_handlers, text):
    event = make_event(RecordingClient(), text)
    assert not any(route.filter(event) for route in live_handlers.routes.values())


def test_help_id_is_stable_for_identical_content_but_keyboard_changes_are_fresh(live_handlers):
    client = RecordingClient()
    first = answer(live_handlers, client, "ultd")
    second = answer(live_handlers, client, "ultd", query_id=2)
    assert first.results[0].id == second.results[0].id
    live_handlers.state.style = "styled"
    third = answer(live_handlers, client, "ultd", query_id=3)
    assert third.results[0].id != second.results[0].id
    assert third.results[0].send_message.reply_markup.rows[0].buttons[0].text == "styled"


def test_other_inline_routes_keep_the_shared_builder_cache_default():
    class Event:
        builder = object()

        async def answer(self, results, **kwargs):
            return kwargs

    policy = asyncio.run(InlineResultBuilder(Event()).answer([]))
    assert policy["cache_time"] == 300

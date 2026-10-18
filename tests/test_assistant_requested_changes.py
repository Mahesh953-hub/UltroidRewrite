from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

from conftest import ROOT


def _load_functions(path: Path, names: set[str], namespace: dict):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - isolated helper
    return namespace


def test_settings_page_exposes_sections_without_current_state_inventory():
    calls = []

    def rich_page(title, rows, **kwargs):
        calls.append((title, rows, kwargs))
        return "settings-page"

    helper = _load_functions(
        ROOT / "assistant/start.py",
        {"_settings_sections", "_settings_page"},
        {"rich_page": rich_page},
    )
    rows = helper["_settings_sections"]()

    assert helper["_settings_page"]() == "settings-page"
    title, rendered_rows, options = calls[0]
    assert title == "Assistant Settings"
    assert rendered_rows == rows
    assert len(rows) == 6 and all(len(row) == 2 for row in rows)
    assert options["columns"] == ("Section", "What it does")
    assert "Current state" not in str((rows, options))


def test_cancel_after_rich_prompt_restores_the_exact_settings_menu():
    class CallbackQuery:
        pass

    class Prompt:
        pass

    class FakeConversation:
        chat_id = 123

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get_response(self, message=None):
            assert message is prompt
            return SimpleNamespace(raw_text="/cancel@ultroidbot", message="/cancel@ultroidbot")

    class FakeClient:
        def conversation(self, _peer):
            return FakeConversation()

    class FakeEvent:
        data_match = SimpleNamespace(group=lambda _index: b"setting-key")
        sender_id = 123
        client = FakeClient()

        async def delete(self):
            self.deleted = True

    prompt = Prompt()
    sent = []
    saved = []

    async def send_rich(*args, **kwargs):
        sent.append((args, kwargs))
        return prompt if len(sent) == 1 else "cancel-reply"

    async def setit(*args):
        saved.append(args)

    functions = _load_functions(
        ROOT / "assistant/callbackstuffs.py",
        {"_message_text", "_is_cancel", "convo_handler"},
        {
            "ast": ast,
            "events": SimpleNamespace(CallbackQuery=CallbackQuery),
            "callback": lambda *_args, **_kwargs: lambda function: function,
            "CONV_PATTERN": object(),
            "_convo": {
                "setting-key": {
                    "back": "menu:alive-customisation",
                    "name": "Alive text",
                    "text": "Send the new value or /cancel.",
                    "var": "ALIVE_TEXT",
                }
            },
            "send_rich": send_rich,
            "rich_page": lambda *args, **kwargs: (args, kwargs),
            "get_back_button": lambda route: ("blue-back", route),
            "_plain": str,
            "setit": setit,
        },
    )
    result = asyncio.run(functions["convo_handler"](FakeEvent()))

    assert result == "cancel-reply"
    assert sent[0][0][2][0][0] == "Alive text"
    assert sent[1][0][2][0][0] == "Settings cancelled"
    assert sent[1][1]["buttons"] == ("blue-back", "menu:alive-customisation")
    assert saved == []


def test_back_helper_uses_primary_style_by_default():
    calls = []

    def ikm(data, *, icon=False):
        calls.append((data, icon))
        return [["built-button"]]

    helper = _load_functions(
        ROOT / "assistant/__init__.py",
        {"get_back_button"},
        {"ikm": ikm, "_clean_nav_label": lambda label: label},
    )

    assert helper["get_back_button"]("menu:features") == ["built-button"]
    assert calls == [([("Back", "menu:features", "primary")], False)]


def test_telethon_status_label_includes_pinned_commit_and_tl_layer():
    helper = _load_functions(
        ROOT / "plugins/bot.py",
        {"_telethon_build_label"},
        {"__version__": "1.45.0"},
    )

    assert helper["_telethon_build_label"]() == "1.45.0 [#e8019a5@230]"

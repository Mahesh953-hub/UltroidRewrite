from __future__ import annotations

import ast
import asyncio
import html
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - isolated helpers
    return namespace


def test_inline_send_handler_waits_and_edits_only_the_deploy_result_with_full_name():
    path = ROOT / "plugins/_inline.py"
    waits = []
    edits = []

    class InputBotInlineMessageID64:
        pass

    class FakeClient:
        async def get_entity(self, user_id):
            assert user_id == 90210
            return SimpleNamespace(first_name="Ada", last_name="Lovelace")

    async def sleep(delay):
        waits.append(delay)

    async def edit_rich(*args, **kwargs):
        edits.append((args, kwargs))
        return "edited"

    helper = _load_functions(
        path,
        {"_is_deploy_inline_send", "_handle_deploy_inline_send"},
        {
            "asyncio": SimpleNamespace(sleep=sleep),
            "INLINE_ALIVE_RESULT_ID": "alive-deploy",
            "INLINE_DEPLOY_SEND_DELAY_SECONDS": 3.5,
            "asst": None,
            "get_display_name": lambda user: f"{user.first_name} {user.last_name}",
            "LOGS": SimpleNamespace(debug=lambda *_args, **_kwargs: None),
            "_alive_rich_markup": lambda name: f"status:{name}",
            "edit_rich": edit_rich,
            "_btn": lambda *args, **kwargs: (args, kwargs),
        },
    )
    update = SimpleNamespace(
        query="",
        id="alive-deploy",
        user_id=90210,
        msg_id=InputBotInlineMessageID64(),
    )
    client = FakeClient()

    result = asyncio.run(helper["_handle_deploy_inline_send"](update, client))

    assert result == "edited"
    assert waits == [3.5]
    assert edits[0][0] == (client, update.msg_id, None, "status:Ada Lovelace")
    assert edits[0][1]["format"] == "html"
    assert edits[0][1]["link_preview"] is False
    assert edits[0][1]["buttons"][0][0][0] == ("Hi",)


def test_inline_send_handler_ignores_unrelated_inline_results_without_delay():
    path = ROOT / "plugins/_inline.py"
    waits = []
    edits = []

    async def sleep(delay):
        waits.append(delay)

    async def edit_rich(*args, **kwargs):
        edits.append((args, kwargs))

    helper = _load_functions(
        path,
        {"_is_deploy_inline_send", "_handle_deploy_inline_send"},
        {
            "asyncio": SimpleNamespace(sleep=sleep),
            "INLINE_ALIVE_RESULT_ID": "alive-deploy",
            "INLINE_DEPLOY_SEND_DELAY_SECONDS": 3.5,
            "asst": None,
            "get_display_name": lambda _user: "unused",
            "LOGS": SimpleNamespace(debug=lambda *_args, **_kwargs: None),
            "_alive_rich_markup": lambda _name: "unused",
            "edit_rich": edit_rich,
            "_btn": lambda *args, **kwargs: (args, kwargs),
        },
    )

    result = asyncio.run(
        helper["_handle_deploy_inline_send"](
            SimpleNamespace(query="", id="some-other-result", user_id=1, msg_id=object()),
            object(),
        )
    )

    assert result is None
    assert waits == []
    assert edits == []


def test_deployment_guide_uses_native_details_code_and_italic_footer():
    from telethon import types

    from pyUltroid._core.richmsg import rich_message

    helper = _load_functions(ROOT / "plugins/_inline.py", {"_deploy_guide_blocks"}, {})
    native = rich_message(helper["_deploy_guide_blocks"]("Ada <Lovelace>"))
    assert isinstance(native, types.InputRichMessage)
    assert "Deploy your own Ultroid" in native.blocks[0].text.text.text
    assert native.blocks[1].text.text == "Hey Ada <Lovelace>! Browse these steps to deploy your own Ultroid."
    steps = [block for block in native.blocks if isinstance(block, types.PageBlockDetails)]
    assert len(steps) == 7
    all_code = []
    def visit(blocks):
        for block in blocks:
            if isinstance(block, types.PageBlockPreformatted):
                all_code.append(block.text.text)
            visit(getattr(block, "blocks", ()) or ())
    visit(native.blocks)
    assert any("git clone https://github.com/Mahesh953-hub/SarahUB.git Ultroid" in code for code in all_code)
    assert any("pip install -r requirements.txt" in code for code in all_code)
    assert any("API_HASH=YOUR_API_HASH" in code for code in all_code)
    assert any("BOT_TOKEN=" in code and "SESSION=" in code for code in all_code)
    assert any("python3 -m pyUltroid" in code for code in all_code)
    assert any("nohup" in code for code in all_code)
    assert isinstance(native.blocks[-1].text, types.TextItalic)
    assert bytes(native)


def test_assistant_callback_target_uses_message_id_not_random_query_id():
    helper = _load_functions(
        ROOT / "assistant/rich_pages.py",
        {"callback_edit_target"},
        {"Any": Any},
    )["callback_edit_target"]
    event = SimpleNamespace(
        chat_id=123,
        id=987654321012345678,
        message_id=42,
        query=SimpleNamespace(msg_id=42),
    )
    assert helper(event) == (123, 42)

    class InputBotInlineMessageID64:
        pass

    inline_id = InputBotInlineMessageID64()
    inline_event = SimpleNamespace(
        chat_id=None,
        id=987654321012345678,
        message_id=inline_id,
        query=SimpleNamespace(msg_id=inline_id),
    )
    assert helper(inline_event) == (inline_id, None)


def test_alive_inline_card_is_a_separate_rich_status_without_hi_or_guide_flow():
    class Markup:
        def __init__(self, text, format="html", files=()):
            self.text = text
            self.format = format
            self.files = tuple(files)

    helper = _load_functions(
        ROOT / "plugins/bot.py",
        {"_alive_inline_markup"},
        {
            "html": html,
            "RichMarkup": Markup,
            "RichFile": object,
            "coerce_to_rich_markup": lambda text, **_kwargs: Markup(text),
            "asst": SimpleNamespace(me=SimpleNamespace(first_name="FuckingClonerBot")),
            "udB": SimpleNamespace(
                get_key=lambda key: "<b>Hey & welcome</b>" if key == "ALIVE_TEXT" else None
            ),
            "get_string": lambda _key: "Welcome",
            "ultroid_version": "1.2.3",
            "HOSTED_ON": "test",
            "UltVer": "2.0",
            "pyver": lambda: "3.12",
            "__version__": "1.0",
            "_telethon_build_label": lambda: "1.45.0 [#e8019a5@230]",
            "_alive_plugin_counts": lambda: (2, 1),
        },
    )
    card = helper["_alive_inline_markup"]("2 hours", "main")

    assert "Ultroid · Alive" in card.text
    assert "<table" in card.text
    assert "<b>Hey & welcome</b>" in card.text
    assert "1.45.0 [#e8019a5@230]" in card.text
    assert "Plugins" in card.text and "2" in card.text
    assert "Addons" in card.text and "1" in card.text
    assert "Status" not in card.text and "PyUltroid" not in card.text
    assert "Runtime snapshot" not in card.text
    assert "SarahUB source" not in card.text and "Support</a>" not in card.text
    assert "<a href=" not in card.text
    assert "page 1 of 1 • total 7" in card.text
    assert "Guide" not in card.text and "InlineSend" not in card.text


def test_hi_callback_answers_and_edits_the_inline_message_with_a_real_next_step():
    path = ROOT / "plugins/_inline.py"
    calls = []

    class FakeEvent:
        async def get_sender(self):
            return SimpleNamespace(first_name="Ada", last_name="Lovelace")

        async def answer(self):
            calls.append(("answer",))

    async def edit_callback(event, content, buttons):
        calls.append(("edit", event, content, buttons))
        return "guide shown"

    handler = _load_functions(
        path,
        {"_cb_alive_deploy_hi", "_show_deploy_guide", "_deploy_guide_blocks"},
        {
            "callback": lambda **_kwargs: lambda function: function,
            "html": html,
            "get_display_name": lambda user: f"{user.first_name} {user.last_name}",
            "_edit_rich_callback": edit_callback,
            "_btn": lambda text, **kwargs: (text, kwargs),
        },
    )["_cb_alive_deploy_hi"]
    event = FakeEvent()

    result = asyncio.run(handler(event))

    assert result == "guide shown"
    assert calls[0][0:2] == ("edit", event)
    assert calls[0][2].blocks[1].text.text == "Hey Ada Lovelace! Browse these steps to deploy your own Ultroid."
    assert calls[0][3][0][0][0] == "Repository"
    assert calls[0][3][0][1][0] == "Support"
    assert calls[1] == ("answer",)


def test_inline_alive_article_uses_native_rich_builder_and_exact_result_id():
    source = (ROOT / "plugins/_inline.py").read_text(encoding="utf-8")
    assert "builder.article_rich(" in source
    assert "id=INLINE_ALIVE_RESULT_ID" in source
    assert "await edit_rich(" in source
    assert "await asyncio.sleep(INLINE_DEPLOY_SEND_DELAY_SECONDS)" in source


def test_all_settings_submenu_buttons_have_explanatory_rows_and_footer_source():
    source = (ROOT / "assistant/callbackstuffs.py").read_text(encoding="utf-8")
    start_source = (ROOT / "assistant/start.py").read_text(encoding="utf-8")
    for label in (
        "API Keys",
        "Chat Bot",
        "Alive",
        "PM Permit",
        "Features",
        "VC Song Bot",
    ):
        assert f'"{label}"' in start_source
    assert "_menu_table_rows(menu)" in source
    assert 'columns=("Button", "What it does")' in source
    assert "edit_callback_page(" in source


def test_page_markup_has_large_heading_dividers_table_and_italic_footer():
    source = ROOT / "assistant/rich_pages.py"

    class Markup:
        def __init__(self, text, format="html", files=()):
            self.text = text
            self.format = format
            self.files = tuple(files)

    class File:
        def __init__(self, file_id, media, kind):
            self.id = file_id
            self.media = media
            self.kind = kind

    helper = _load_functions(
        source,
        {"page_footer", "rich_page", "body_page"},
        {
            "html": html,
            "Sequence": Sequence,
            "Any": Any,
            "RichMarkup": Markup,
            "RichFile": File,
        },
    )
    page = helper["rich_page"](
        "Settings <safe>",
        [("API key", "Set & hidden")],
        columns=("Option", "Purpose"),
        page=2,
        pages=3,
        total=8,
    )
    assert page.text.startswith("<h1>Settings &lt;safe&gt;</h1>")
    assert page.text.count("<hr>") == 2
    assert '<table class="bordered compact">' in page.text
    assert "API key" in page.text and "Set &amp; hidden" in page.text
    assert page.text.endswith("<p><i>page 2 of 3 • total 8</i></p>")

    media_page = helper["body_page"](
        "Welcome", "<p>Welcome.</p>", media=("photo", "uploaded-photo", "Hi")
    )
    assert 'src="assistant-page-media"' in media_page.text
    assert media_page.files[0].media == "uploaded-photo"
    assert media_page.files[0].kind == "photo"

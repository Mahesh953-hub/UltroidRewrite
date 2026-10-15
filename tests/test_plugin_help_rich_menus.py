from __future__ import annotations

import ast
import asyncio
import html
import re
from pathlib import Path
from types import SimpleNamespace

from pyUltroid._core.richmsg import (
    RichBlocks,
    RichMarkup,
    coerce_to_rich_markup,
    normalize_blocks,
    render_blocks_native_html,
)
from pyUltroid.fns.command_help import command_patterns, command_syntax

ROOT = Path(__file__).resolve().parents[1]


def _load_functions(path: Path, names: set[str], namespace: dict):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    for node in selected:
        node.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - isolated helpers
    return namespace


def test_main_help_menu_has_hint_overview_dividers_and_italic_footer():
    markup = _load_functions(
        ROOT / "plugins/_help.py",
        {"_help_blocks"},
        {
            "RichBlocks": RichBlocks,
            "RichMarkup": RichMarkup,
            "normalize_blocks": normalize_blocks,
            "render_blocks_native_html": render_blocks_native_html,
            "_help_overview_rows": lambda: [
                ["Plugins", "7", "Core plugin commands and controls"],
                ["Inline Plugins", "3", "Searchable inline tools"],
            ],
            "_page_footer": lambda page, pages, total: (
                f"page {page} of {pages} • total {total}"
            ),
        },
    )["_help_blocks"]()

    assert isinstance(markup, RichMarkup)
    assert "<h2>Help Menu</h2>" in markup.text
    assert "Browse plugin commands" in markup.text
    assert "<hr>" in markup.text
    assert "<h2>Overview</h2>" in markup.text
    assert "Section name" in markup.text
    assert "Total handlers" in markup.text
    assert "Contains" in markup.text
    assert "<p><i>page 1 of 1 • total 10</i></p>" in markup.text


def test_command_patterns_resolve_canonical_plugin_ids_to_registry_stems():
    registry = {"alpha": ("alpha$", "alpha (.*)"), "beta": ("beta$",)}

    assert command_patterns("plugins.alpha", registry) == registry["alpha"]
    assert command_patterns("addons/beta.py", registry) == registry["beta"]
    assert command_patterns("missing", registry) == ()


def test_plugin_catalog_has_live_totals_and_standard_page_structure():
    catalogs = {"plugins.alpha": (), "plugins.beta": ()}
    namespace = _load_functions(
        ROOT / "plugins/_inline.py",
        {"_catalog_help_blocks"},
        {
            "HELP": {"Official": catalogs},
            "LIST": {"alpha": ("alpha$", "alpha (.*)"), "beta": ("beta$",)},
            "RichBlocks": RichBlocks,
            "RichMarkup": RichMarkup,
            "normalize_blocks": normalize_blocks,
            "render_blocks_native_html": render_blocks_native_html,
            "command_patterns": command_patterns,
            "_plugin_pmf_entries": lambda _name: [],
            "_page_footer": lambda page, pages, total: (
                f"page {page} of {pages} • total {total}"
            ),
            "udB": SimpleNamespace(get_key=lambda _key: 5),
            "ceil": __import__("math").ceil,
        },
    )
    markup = namespace["_catalog_help_blocks"]("Official", 0)

    assert isinstance(markup, RichMarkup)
    assert "Core plugin library" in markup.text
    assert "Browse this library" in markup.text
    assert "<h2>Overview</h2>" in markup.text
    assert "Section name" in markup.text and "Total handlers" in markup.text
    assert ">3</td>" in markup.text
    assert "2 plugin(s)" in markup.text
    assert "<hr>" in markup.text
    assert "<p><i>page 1 of 1 • total 2</i></p>" in markup.text


def test_plugin_catalog_footer_tracks_later_pages_and_total_plugins():
    catalogs = {f"plugins.module{index}": () for index in range(12)}
    registry = {f"module{index}": (f"module{index}$",) for index in range(12)}
    namespace = _load_functions(
        ROOT / "plugins/_inline.py",
        {"_catalog_help_blocks"},
        {
            "HELP": {"Official": catalogs},
            "LIST": registry,
            "RichBlocks": RichBlocks,
            "RichMarkup": RichMarkup,
            "normalize_blocks": normalize_blocks,
            "render_blocks_native_html": render_blocks_native_html,
            "command_patterns": command_patterns,
            "_plugin_pmf_entries": lambda _name: [],
            "_page_footer": lambda page, pages, total: (
                f"page {page} of {pages} • total {total}"
            ),
            "udB": SimpleNamespace(get_key=lambda key: {"HELP_ROWS": 5, "HELP_COLUMNS": 2}.get(key)),
            "ceil": __import__("math").ceil,
        },
    )

    markup = namespace["_catalog_help_blocks"]("Official", 1)

    assert "<i>page 2 of 2 • total 12</i>" in markup.text


def _plugin_card_namespace():
    docs_converter = _load_functions(
        ROOT / "plugins/_help.py",
        {"_markdown_docs_markup"},
        {
            "re": re,
            "_MARKDOWN_CODE_SPANS": re.compile(r"(`+)(.*?)\1", re.DOTALL),
            "coerce_to_rich_markup": coerce_to_rich_markup,
        },
    )["_markdown_docs_markup"]
    pmf_guide = _load_functions(
        ROOT / "plugins/_help.py",
        {"_pmf_guide_text"},
        {},
    )["_pmf_guide_text"]
    return {
        "RichBlocks": RichBlocks,
        "RichMarkup": RichMarkup,
        "normalize_blocks": normalize_blocks,
        "render_blocks_native_html": render_blocks_native_html,
        "coerce_to_rich_markup": coerce_to_rich_markup,
        "_markdown_docs_markup": docs_converter,
        "command_syntax": command_syntax,
        "_page_footer": lambda page, pages, total: (
            f"page {page} of {pages} • total {total}"
        ),
        "_pmf_guide_text": pmf_guide,
        "html": html,
        "HNDLR": ".",
    }


def test_individual_plugin_page_shows_category_count_and_indented_rich_docs():
    helper = _load_functions(
        ROOT / "plugins/_inline.py",
        {"_plugin_card_markup", "_plugin_label", "_as_html"},
        _plugin_card_namespace(),
    )["_plugin_card_markup"]
    markup = helper(
        "media_tools",
        "Official",
        "**Converts media**\nUse `.convert <format>` to choose an output format.",
        ("convert ( .*)", "extractaudio$"),
    )

    assert isinstance(markup, RichMarkup)
    assert markup.format == "html"
    assert "Media Tools" in markup.text
    assert "Section name" in markup.text
    assert "Total handlers" in markup.text
    assert ".convert [arguments]" in markup.text
    assert "Converts media" in markup.text
    assert "<blockquote expandable><b>Docs</b>" in markup.text
    assert "<code>.convert &lt;format&gt;</code>" in markup.text
    assert "Using this plugin guide" not in markup.text
    assert "No commands are registered" not in markup.text
    assert "convert ( .* )" not in markup.text
    assert "<hr>" in markup.text
    assert "<i>page 1 of 1 • total 2</i>" in markup.text


def test_plugin_without_commands_still_has_collapsible_rich_docs():
    helper = _load_functions(
        ROOT / "plugins/_inline.py",
        {"_plugin_card_markup", "_plugin_label", "_as_html"},
        _plugin_card_namespace(),
    )["_plugin_card_markup"]
    markup = helper("empty_plugin", "Official", "**Documentation stays rich.**", ())

    assert "<blockquote expandable><b>Docs</b>" in markup.text
    assert "Documentation stays rich" in markup.text
    assert "No commands are registered" not in markup.text


def test_standalone_help_uses_category_count_and_indented_rich_documentation():
    helper = _load_functions(
        ROOT / "plugins/_help.py",
        {"_plugin_help_markup", "_plugin_title"},
        {
            "RichBlocks": RichBlocks,
            "RichMarkup": RichMarkup,
            "normalize_blocks": normalize_blocks,
            "render_blocks_native_html": render_blocks_native_html,
            "coerce_to_rich_markup": coerce_to_rich_markup,
            "_markdown_docs_markup": _plugin_card_namespace()[
                "_markdown_docs_markup"
            ],
            "command_syntax": command_syntax,
            "_page_footer": lambda page, pages, total: (
                f"page {page} of {pages} • total {total}"
            ),
            "_pmf_guide_text": lambda _entries: "PMF controls guide",
            "html": html,
            "HNDLR": ".",
        },
    )["_plugin_help_markup"]
    markup = helper(
        "media_tools",
        ("**Media conversion**\nUse `.convert <format>` for conversion.",),
        ("convert ( .*)", "extractaudio$"),
        category="Add-ons",
    )

    assert isinstance(markup, RichMarkup)
    assert "Plugin overview" in markup.text
    assert "Section name" in markup.text and "Total handlers" in markup.text
    assert "Add-ons" in markup.text
    assert ".convert [arguments]" in markup.text
    assert "<blockquote expandable><b>Docs</b>" in markup.text
    assert "Media conversion" in markup.text
    assert "Using this guide" not in markup.text
    assert "convert ( .* )" not in markup.text


def test_pmf_guide_lists_toggle_state_actions_and_custom_buttons():
    guide = _load_functions(
        ROOT / "plugins/_help.py",
        {"_pmf_guide_text"},
        {},
    )["_pmf_guide_text"]
    text = guide(
        [
            {
                "type": "toggle",
                "label": "Rich Cards",
                "state": True,
                "description": "Turn rich cards on or off.",
            },
            {
                "type": "action",
                "label": "Show Settings",
                "description": "Print the current settings.",
            },
            {
                "type": "manifest_button",
                "label": "Cycle Theme",
                "description": "Change the active theme.",
            },
        ]
    )

    assert "currently On" in text
    assert "one-shot action" in text
    assert "custom plugin action" in text
    assert "Change the active theme." in text


def test_plugin_detail_includes_collapsible_pmf_guide_for_live_controls():
    helper = _load_functions(
        ROOT / "plugins/_inline.py",
        {"_plugin_card_markup", "_plugin_label", "_as_html"},
        _plugin_card_namespace(),
    )["_plugin_card_markup"]
    markup = helper(
        "echo",
        "Official",
        "**Echo docs**",
        ("addecho( (.*)|$)",),
        pmf_entries=[
            {
                "type": "toggle",
                "label": "Global Echo",
                "state": True,
                "description": "Enable or disable echo globally.",
            },
            {
                "type": "action",
                "label": "List Echo Users",
                "description": "Show configured echo users.",
            },
        ],
    )

    assert "<blockquote expandable><b>Docs</b>" in markup.text
    assert "<blockquote expandable><b>PMF guide</b>" in markup.text
    assert "Global Echo: toggle, currently On" in markup.text
    assert "Show configured echo users." in markup.text


def test_plugin_docs_keep_markdown_entities_and_angle_bracket_code_args():
    converter = _plugin_card_namespace()["_markdown_docs_markup"]
    markup = converter("**Formatted docs**\nUse `.cmd <value>` for the argument.")

    assert isinstance(markup, RichMarkup)
    assert "<strong>Formatted docs</strong>" in markup.text
    assert "<code>.cmd &lt;value&gt;</code>" in markup.text


def test_main_menu_button_palette_matches_loaded_state_and_semantics(monkeypatch):
    import sys
    from types import ModuleType

    built = []
    kit = ModuleType("pyUltroid._core.buttonkit")

    def button(text, **kwargs):
        result = {"text": text, **kwargs}
        built.append(result)
        return result

    kit.button = button
    monkeypatch.setitem(sys.modules, "pyUltroid._core.buttonkit", kit)
    helper = _load_functions(
        ROOT / "plugins/_help.py",
        {"_styled_main_help_menu"},
        {
            "get_string": lambda key: {
                "help_4": "Plugins",
                "help_5": "Add-ons",
                "help_6": "Voice Chat",
                "help_7": "Inline Plugins",
                "help_8": "Owner Tools",
                "help_9": "Settings",
                "help_10": "Close",
            }[key],
            "HELP": {"Official": {"plugins.alpha": "doc"}, "Addons": {}},
            "cb": lambda *args: ":".join(args),
            "_assistant_settings_url": lambda: "https://t.me/settings",
            "_loaded_category_style": lambda category: (
                "success" if category == "Official" else "danger"
            ),
        },
    )

    helper["_styled_main_help_menu"]()
    presets = {item["text"]: item.get("preset") for item in built}

    assert presets == {
        "Plugins": "success",
        "Add-ons": "danger",
        "Voice Chat": None,
        "Inline Plugins": None,
        "Owner Tools": None,
        "Settings": "primary",
        "Close": "danger",
    }


def test_plugin_buttons_are_green_only_for_pmf_or_module_framework_plugins():
    manifest_plugins = {"plugins.with_pmf"}
    manifest = SimpleNamespace(
        get_entries=lambda name: ["toggle"] if name in manifest_plugins else []
    )
    module_type = type("ModernModule", (), {})
    module_type.__module__ = "plugins.with_module"
    module_registry = SimpleNamespace(modules={"modern": module_type()})
    helper = _load_functions(
        ROOT / "plugins/_inline.py",
        {"_plugin_has_pmf_or_module"},
        {"core": SimpleNamespace(manifest=manifest, modules=module_registry)},
    )["_plugin_has_pmf_or_module"]

    assert helper("plugins.with_pmf")
    assert helper("plugins.with_module")
    assert not helper("plugins.plain")


def test_page_navigation_keeps_previous_and_next_uncolored():
    helper = _load_functions(
        ROOT / "plugins/_inline.py",
        {"_page_num_nav_row"},
        {
            "_btn": lambda text, **kwargs: {"text": text, **kwargs},
            "get_string": lambda key: {
                "btn_previous": "Previous",
                "btn_next": "Next",
                "btn_back_page": "Previous menu index",
            }[key],
            "cb": lambda *args: ":".join(str(arg) for arg in args),
        },
    )["_page_num_nav_row"]
    buttons = helper("Official", 1)

    assert [button["text"] for button in buttons] == [
        "Previous",
        "Previous menu index",
        "Next",
    ]
    assert all(button.get("preset") is None for button in buttons)


def test_plugin_detail_back_button_names_and_colors_its_section():
    source = (ROOT / "plugins/_inline.py").read_text(encoding="utf-8")

    assert (
        "Back to { {'Official': 'Plugins', 'Addons': 'Add-ons', 'VCBot': 'Voice Chat'}.get(key, key) }"
        in source
    )
    assert 'preset="primary"' in source


def test_callback_editor_targets_inline_message_id_and_forces_native_rich():
    calls = []

    async def edit_rich(*args, **kwargs):
        calls.append((args, kwargs))
        return "edited"

    helper = _load_functions(
        ROOT / "plugins/_inline.py",
        {"_edit_rich_callback"},
        {"edit_rich": edit_rich},
    )["_edit_rich_callback"]

    class InputBotInlineMessageID:
        pass

    inline_id = InputBotInlineMessageID()
    event = SimpleNamespace(
        client=object(),
        chat_id=None,
        query=SimpleNamespace(msg_id=inline_id),
    )
    result = asyncio.run(helper(event, "rich body", [["buttons"]]))

    assert result == "edited"
    args, kwargs = calls[0]
    assert args[:3] == (event.client, inline_id, None)
    assert kwargs["engine"] == "rich"
    assert kwargs["format"] == "html"
    assert kwargs["buttons"] == [["buttons"]]


def test_help_callback_no_longer_selects_generic_translation_body():
    source = (ROOT / "plugins/_inline.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    callback = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "help_func"
    )
    calls = [node for node in ast.walk(callback) if isinstance(node, ast.Call)]
    names = {node.func.id for node in calls if isinstance(node.func, ast.Name)}

    assert "_catalog_help_blocks" in names
    assert "page_num" in names
    assert "_strings" not in source[callback.lineno - 1 : callback.end_lineno]


def test_source_path_resolves_loaded_plugins_and_blocks_paths_outside_allowed_roots(
    tmp_path,
):
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    source = plugins / "demo.py"
    source.write_text("# demo\n", encoding="utf-8")
    namespace = _load_functions(
        ROOT / "assistant/callbackstuffs.py",
        {"_plugin_source_path"},
        {"Path": Path, "__file__": str(ROOT / "assistant/callbackstuffs.py")},
    )
    helper = namespace["_plugin_source_path"]
    registry = SimpleNamespace(
        get=lambda name: (
            SimpleNamespace(path="plugins/demo.py")
            if name == "plugins.demo"
            else SimpleNamespace(path="../outside.py")
        )
    )

    assert (
        helper("Official", "plugins.demo", repo_root=tmp_path, registry=registry)
        == source
    )
    assert helper("Official", "escape", repo_root=tmp_path, registry=registry) is None


def test_send_source_delivers_the_plugin_file_to_the_requesting_user():
    sent = []
    answers = []

    class FakeClient:
        async def send_file(self, *args, **kwargs):
            sent.append((args, kwargs))

    class FakeRegistry:
        def get(self, name):
            assert name == "_help"
            return SimpleNamespace(path="plugins/_help.py")

    async def answer(text, **kwargs):
        answers.append((text, kwargs))

    def cb(*parts):
        return ":".join(str(part) for part in parts)

    def ikm(rows, **_kwargs):
        return rows

    event = SimpleNamespace(
        data_match=SimpleNamespace(group=lambda _index: b"Official:_help:2"),
        sender_id=42,
        client=FakeClient(),
        answer=answer,
    )
    namespace = _load_functions(
        ROOT / "assistant/callbackstuffs.py",
        {"_plugin_source_path", "send"},
        {
            "Path": Path,
            "__file__": str(ROOT / "assistant/callbackstuffs.py"),
            "core": SimpleNamespace(registry=FakeRegistry()),
            "LOGS": SimpleNamespace(exception=lambda _error: None),
            "cb": cb,
            "ikm": ikm,
        },
    )

    asyncio.run(namespace["send"](event))

    assert sent
    args, kwargs = sent[0]
    assert args[0] == 42
    assert kwargs["file"] == str((ROOT / "plugins/_help.py").resolve())
    assert kwargs["caption"] == "_help source"
    assert kwargs["buttons"] == [
        [("Paste", "assistant:callbacks:paste:plugins/_help.py|2", "primary")]
    ]
    assert not any("edit" in call for call in sent)
    assert answers == [("Source sent in your private chat.", {"alert": True})]

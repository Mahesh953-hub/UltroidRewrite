"""Regression guards for runtime failures found in the latest bot log."""

from __future__ import annotations

import ast
from math import ceil
from types import SimpleNamespace

from conftest import ROOT

from pyUltroid._core.richmsg import (
    RichBlocks,
    RichMarkup,
    normalize_blocks,
    render_blocks_native_html,
    rich_markup_from_blocks,
)


def _tree(relative_path: str) -> ast.Module:
    return ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))


def test_legacy_addon_loader_returns_the_module_to_the_driver():
    tree = _tree("pyUltroid/startup/utils.py")
    loader = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "load_addons"
    )
    assert any(
        isinstance(node, ast.Return)
        and isinstance(node.value, ast.Name)
        and node.value.id == "mod"
        for node in ast.walk(loader)
    )


def test_pmpermit_catches_the_imported_rpc_exception_class():
    tree = _tree("plugins/pmpermit.py")
    imports_exception = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "telethon.errors"
        and any(alias.name == "MessageIdInvalidError" for alias in node.names)
        for node in tree.body
    )
    catches_exception = any(
        isinstance(node, ast.ExceptHandler)
        and isinstance(node.type, ast.Name)
        and node.type.id == "MessageIdInvalidError"
        for node in ast.walk(tree)
    )
    assert imports_exception and catches_exception


def test_assistant_agent_menu_imports_the_shared_agent_accessor():
    tree = _tree("assistant/agent.py")
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "pyUltroid.agent"
        and any(alias.name == "agent" for alias in node.names)
        for node in tree.body
    )


def test_inline_help_builds_a_clear_empty_rich_page_for_unknown_category():
    tree = _tree("plugins/_inline.py")
    builder = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_catalog_help_blocks"
    )
    module = ast.fix_missing_locations(ast.Module(body=[builder], type_ignores=[]))
    namespace = {
        "HELP": {},
        "LIST": {},
        "HNDLR": ".",
        "RichBlocks": RichBlocks,
        "RichMarkup": RichMarkup,
        "normalize_blocks": normalize_blocks,
        "render_blocks_native_html": render_blocks_native_html,
        "rich_markup_from_blocks": rich_markup_from_blocks,
        "command_patterns": lambda *_args: (),
        "_plugin_pmf_entries": lambda *_args: [],
        "_page_footer": lambda page, pages, total: (
            f"page {page} of {pages} • total {total}"
        ),
        "udB": SimpleNamespace(get_key=lambda _key: 5),
        "ceil": ceil,
    }
    exec(compile(module, "plugins/_inline.py", "exec"), namespace)  # noqa: S102
    blocks = namespace["_catalog_help_blocks"]("Unknown")

    assert isinstance(blocks, RichMarkup)
    assert "<h2>Plugin library</h2>" in blocks.text
    assert "Section name" in blocks.text and "Total handlers" in blocks.text
    assert "No modules are currently available in this library." in blocks.text
    assert "<i>page 1 of 1 • total 0</i>" in blocks.text


def test_help_query_handles_an_uninitialized_help_map():
    import asyncio
    from types import SimpleNamespace

    tree = _tree("plugins/_help.py")
    handler = next(
        node
        for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_help"
    )
    handler.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[handler], type_ignores=[]))
    sent = []

    class Event:
        pattern_match = SimpleNamespace(group=lambda _index: " query")

        async def get_chat(self):
            return SimpleNamespace(id=1)

        async def eor(self, text):
            sent.append(text)

    async def edit_help(event, text):
        return await event.eor(text)

    namespace = {
        "html": __import__("html"),
        "HELP": {},
        "LIST": {},
        "LOGS": SimpleNamespace(exception=lambda _error: None),
        "fns": SimpleNamespace(cmd_regex_replace=lambda value: value),
        "get_string": lambda _key: "",
        "HNDLR": ".",
        "_md_escape": lambda value: str(value),
        "_edit_help": edit_help,
    }
    exec(compile(module, "plugins/_help.py", "exec"), namespace)

    asyncio.run(namespace["_help"](Event()))

    assert len(sent) == 1
    assert "not a plugin or command" in sent[0]
    assert "Error 🤔 occured." not in sent[0]

"""Regression tests for actionable runtime failures seen in the live log."""
from __future__ import annotations

import ast
import os
from pathlib import Path

from conftest import ROOT


def _tree(relative_path: str) -> ast.Module:
    return ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))


def _attribute_path(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Attribute):
        return [*_attribute_path(node.value), node.attr]
    return []


def test_botchat_handlers_use_the_live_database_facade():
    tree = _tree("assistant/pmbot.py")
    imports_db = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "pyUltroid.dB"
        and any(alias.name == "db" for alias in node.names)
        for node in tree.body
    )
    call_paths = [
        _attribute_path(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    ]
    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }

    assert imports_db
    assert ["db", "botchat", "add"] in call_paths
    assert call_paths.count(["db", "botchat", "get_who"]) == 3
    assert "add_stuff" not in names
    assert "get_who" not in names


def test_filename_checker_accepts_legacy_single_path_calls(tmp_path):
    tree = _tree("pyUltroid/fns/tools.py")
    toolkit = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "ToolKit"
    )
    method = next(
        node
        for node in toolkit.body
        if isinstance(node, ast.FunctionDef) and node.name == "check_filename"
    )
    isolated_class = ast.ClassDef(
        name="ToolKit",
        bases=[],
        keywords=[],
        body=[method],
        decorator_list=[],
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[isolated_class], type_ignores=[])
    )
    namespace = {"os": os}
    exec(compile(module, "pyUltroid/fns/tools.py", "exec"), namespace)

    target = tmp_path / "voice.mp3"
    target.write_bytes(b"test")
    expected = str(tmp_path / "voice_1.mp3")
    assert namespace["ToolKit"].check_filename(str(target)) == expected
    assert namespace["ToolKit"]().check_filename(str(target)) == expected


def test_file_list_plugin_binds_its_byte_formatter():
    tree = _tree("plugins/tools.py")
    assert any(
        isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "hb" for target in node.targets)
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "humanbytes"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "fns"
        for node in tree.body
    )


def test_gpt_addon_calls_the_shared_agent_accessor():
    tree = _tree("addons/gpt.py")
    uses_accessor = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "respond"
        and isinstance(node.func.value, ast.Call)
        and isinstance(node.func.value.func, ast.Name)
        and node.func.value.func.id == "agent"
        for node in ast.walk(tree)
    )
    calls_function_as_object = any(
        isinstance(node, ast.Attribute)
        and node.attr == "respond"
        and isinstance(node.value, ast.Name)
        and node.value.id == "agent"
        for node in ast.walk(tree)
    )

    assert uses_accessor
    assert not calls_function_as_object


def test_quote_font_loader_falls_back_to_an_installed_bundled_font():
    from PIL import ImageFont

    tree = _tree("addons/quote.py")
    loader = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_quote_font"
    )
    module = ast.fix_missing_locations(
        ast.Module(body=[loader], type_ignores=[])
    )
    namespace = {
        "ImageFont": ImageFont,
        "Path": Path,
        "__file__": str(ROOT / "addons" / "quote.py"),
    }
    exec(compile(module, "addons/quote.py", "exec"), namespace)

    font = namespace["_quote_font"]("missing-font.ttf", 20)
    assert font.getbbox("Quote")

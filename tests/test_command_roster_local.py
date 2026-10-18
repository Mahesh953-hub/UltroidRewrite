from __future__ import annotations

import ast
import asyncio
import importlib.util
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace


def _constant_string(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for value in node.values:
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                return None
            parts.append(value.value)
        return "".join(parts)
    return None


def _command_registry(root):
    registry = defaultdict(list)
    for directory in (root / "plugins", root / "addons"):
        for path in sorted(directory.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            nodes = sorted(
                (
                    node
                    for node in ast.walk(tree)
                    if isinstance(
                        node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
                    )
                ),
                key=lambda node: node.lineno,
            )
            for node in nodes:
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    name = (
                        decorator.func.id
                        if isinstance(decorator.func, ast.Name)
                        else decorator.func.attr
                        if isinstance(decorator.func, ast.Attribute)
                        else ""
                    )
                    if name != "ultroid_cmd":
                        continue
                    kwargs = {
                        item.arg: item.value for item in decorator.keywords if item.arg
                    }
                    pattern_node = kwargs.get("pattern")
                    if pattern_node is None or (
                        isinstance(pattern_node, ast.Constant)
                        and pattern_node.value is None
                    ):
                        continue
                    pattern = _constant_string(pattern_node)
                    assert pattern is not None, (
                        path,
                        node.lineno,
                        ast.unparse(pattern_node),
                    )
                    if pattern:
                        registry[path.stem].append(pattern)
    return dict(registry)


def _load_allcmds(root, registry, hndlr, eor, command_syntax):
    source = root / "pyUltroid/fns/misc.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MiscKit"
    )
    method = next(
        node
        for node in owner.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "allcmds"
    )
    isolated = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {
        "LIST": registry,
        "HNDLR": hndlr,
        "eor": eor,
        "command_syntax": command_syntax,
    }
    exec(compile(isolated, str(source), "exec"), namespace)  # noqa: S102 - isolated checked-in method
    return namespace["allcmds"]


def test_cmds_renders_all_local_patterns_through_a_nonpublishing_telegraph_stub():
    root = Path(__file__).resolve().parents[1]
    helper_path = root / "pyUltroid/fns/command_help.py"
    spec = importlib.util.spec_from_file_location("command_help_test", helper_path)
    command_help = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(command_help)
    registry = _command_registry(root)
    assert registry
    assert sum(map(len, registry.values())) >= 680

    pages = []

    class TelegraphStub:
        def create_page(self, *, title, content):
            pages.append({"title": title, "content": content})
            return {"url": "https://local.invalid/cmds"}

    messages = []

    async def eor(event, text, **kwargs):
        messages.append((event, text, kwargs))

    event = SimpleNamespace()
    run = _load_allcmds(root, registry, ".", eor, command_help.command_syntax)
    asyncio.run(run(object(), event, TelegraphStub()))

    expected_lines = [
        "# Ultroid command index",
        "Commands are grouped by plugin. Optional input is shown as [arguments].",
        "",
    ]
    for plugin in sorted(registry, key=str.casefold):
        expected_lines.extend((f"## {plugin.replace('_', ' ').title()}", ""))
        expected_lines.extend(
            f"- `{command_help.command_syntax(pattern, '.')}`"
            for pattern in registry[plugin]
        )
        expected_lines.append("")
    expected = "\n".join(expected_lines).rstrip()

    assert pages == [{"title": "Ultroid Command Index", "content": [expected]}]
    assert "(?:" not in expected
    assert "(.*)" not in expected
    assert messages == [
        (
            event,
            "**Command index**\n\n[Browse all commands](https://local.invalid/cmds)",
            {"link_preview": False},
        )
    ]

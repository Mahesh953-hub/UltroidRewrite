"""Pin runtime import surfaces that were broken by recent union migrations."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from conftest import ROOT


def _attribute_path(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_attribute_path(node.value)}.{node.attr}"
    return ""


def test_plugin_package_lazily_exports_legacy_fns_names():
    source = (ROOT / "plugins" / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    exports = next(
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_FNS_COMPAT_EXPORTS"
            for target in node.targets
        )
        )
    names = ast.literal_eval(exports.args[0] if isinstance(exports, ast.Call) else exports)
    assert {
        "async_searcher",
        "bash",
        "Carbon",
        "def_logs",
        "downloader",
        "get_random_user_data",
        "inline_mention",
        "mediainfo",
        "run_async",
        "set_attributes",
        "split_list",
        "time_formatter",
    } <= names
    assert any(
        isinstance(node, ast.FunctionDef) and node.name == "__getattr__"
        for node in tree.body
    )


def test_fns_union_imports_without_a_bot_database():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pyUltroid.fns import fns; assert type(fns).__name__ == 'Fns'",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )


def test_handler_tracing_uses_the_unified_live_component():
    path = ROOT / "pyUltroid" / "_misc" / "_decorators.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "pyUltroid.tracer"
        for alias in node.names
    ]
    assert "tr" in imports
    assert "tracer" not in imports
    calls = {
        _attribute_path(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert "tr.live.record" in calls


def test_runtime_code_does_not_import_removed_tracer_singletons():
    removed = {"tracer", "searcher", "plugin_index"}
    sources = [* (ROOT / "pyUltroid").rglob("*.py"), ROOT / "smoke_test.py"]
    stale = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module in {
                "pyUltroid.tracer",
                "..tracer",
            }:
                stale.extend(
                    f"{path.relative_to(ROOT)}:{node.lineno}:{alias.name}"
                    for alias in node.names
                    if alias.name in removed
                )
    assert not stale, "removed tracer aliases still imported: " + ", ".join(stale)


def test_first_party_import_compat_exports_cover_live_plugin_names():
    core_tree = ast.parse((ROOT / "pyUltroid" / "_core" / "__init__.py").read_text())
    core_exports = next(
        ast.literal_eval(node.value)
        for node in core_tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "__all__" for target in node.targets)
    )
    eager_imports = {
        alias.asname or alias.name: f"{'.' * node.level}{node.module or ''}"
        for node in core_tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert {"InlineResultBuilder", "build_menu_grid"} <= set(core_exports)
    assert eager_imports["InlineResultBuilder"] == ".events"
    assert eager_imports["build_menu_grid"] == ".events"

    extender_tree = ast.parse((ROOT / "pyUltroid" / "extender" / "__init__.py").read_text())
    hre_alias = next(
        node.value
        for node in extender_tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "hre" for target in node.targets)
    )
    assert _attribute_path(hre_alias) == "ex.resources"

    helper_tree = ast.parse((ROOT / "pyUltroid" / "fns" / "helper.py").read_text())
    helper_class = next(
        node
        for node in helper_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Helper"
    )
    helper_methods = {
        node.name
        for node in helper_class.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert {"async_searcher", "download_file", "humanbytes", "time_formatter", "run_async"} <= helper_methods

    fns_tree = ast.parse((ROOT / "pyUltroid" / "fns" / "_fns.py").read_text())
    fns_class = next(
        node
        for node in fns_tree.body
        if isinstance(node, ast.ClassDef) and node.name == "Fns"
    )
    assert {"Helper", "ToolKit"} <= {_attribute_path(base) for base in fns_class.bases}
    fns_singleton = next(
        node.value
        for node in fns_tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "fns" for target in node.targets)
    )
    assert _attribute_path(fns_singleton.func) == "Fns"

    plugin_package = (ROOT / "plugins" / "__init__.py").read_text()
    assert 'elif name == "safeinstall"' in plugin_package
    assert 'if name == "def_logs"' in plugin_package
    assert '"def_logs"' in plugin_package
    assert 'elif name == "heroku_logs"' in plugin_package
    assert '"heroku_logs"' in plugin_package

    ephemeral_source = (ROOT / "plugins" / "ephemeral.py").read_text()
    optional_requests = {
        "GetWelcomeMessagesRequest",
        "DeleteWelcomeMessageRequest",
        "DeleteAllWelcomeMessagesRequest",
        "EditMessageRequest",
    }
    ephemeral_tree = ast.parse(ephemeral_source)
    probed_requests = {
        node.targets[0].id
        for node in ast.walk(ephemeral_tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "getattr"
        and len(node.value.args) >= 2
        and isinstance(node.value.args[1], ast.Constant)
        and node.value.args[1].value in optional_requests
    }
    assert optional_requests <= probed_requests
    assert "RichButtonStyle" not in ephemeral_source
    assert 'MessageActionChatJoinedViaCommunity", None' in ephemeral_source
    assert "if MessageActionChatJoinedViaCommunity is not None:" in ephemeral_source
    assert "if GetWelcomeMessagesRequest is None:" in ephemeral_source
    bot_tree = ast.parse((ROOT / "plugins" / "bot.py").read_text())
    assert not any(
        isinstance(node, ast.ImportFrom)
        and any(alias.name == "RichButtonStyle" for alias in node.names)
        for node in ast.walk(bot_tree)
    )

    ytdl_tree = ast.parse((ROOT / "pyUltroid" / "fns" / "ytdl.py").read_text())
    assert not any(
        isinstance(node, ast.ImportFrom)
        and node.module == "tools"
        and node.level == 1
        and any(alias.name == "set_attributes" for alias in node.names)
        for node in ast.walk(ytdl_tree)
    )

    downloadupload = ast.parse((ROOT / "plugins" / "downloadupload.py").read_text())
    queue_imports = {
        alias.name
        for node in ast.walk(downloadupload)
        if isinstance(node, ast.ImportFrom) and node.module == "pyUltroid._core.queue"
        for alias in node.names
    }
    assert "queue_manager" not in queue_imports
    assert any(
        isinstance(node, ast.Call) and _attribute_path(node.func).startswith("core.queue.")
        for node in ast.walk(downloadupload)
    )


def test_core_build_menu_grid_lazy_export_resolves_to_events():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pyUltroid._core import build_menu_grid; "
            "from pyUltroid._core.events import build_menu_grid as expected; "
            "assert build_menu_grid is expected",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
    )


def test_fns_facade_exposes_current_helper_methods():
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from pyUltroid.fns import fns; "
            "assert fns.humanbytes(1024) == '1.00KB'; "
            "assert fns.time_formatter(1000) == '1s'; "
            "assert callable(fns.run_async)",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
        timeout=45,
    )


def test_assistant_facade_explicitly_exports_ultconfig():
    source = (ROOT / "assistant" / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert any(
        isinstance(node, ast.ImportFrom)
        and node.module == "pyUltroid"
        and any(alias.name == "ULTConfig" for alias in node.names)
        for node in tree.body
    )


def test_pdftools_uses_supported_pymupdf_import():
    source = (ROOT / "plugins" / "pdftools.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = [
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    ]
    assert ("pymupdf", "fitz") in imports
    assert all(name != "fitz" for name, _ in imports)


def test_updater_consumers_use_the_unified_facade():
    stale = []
    for path in (ROOT / "pyUltroid").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            is_updater_package = node.module == "pyUltroid.updater" or (
                node.level > 0 and node.module == "updater"
            )
            if is_updater_package:
                stale.extend(
                    f"{path.relative_to(ROOT)}:{node.lineno}:{alias.name}"
                    for alias in node.names
                    if alias.name in {"git", "updater"}
                )
    assert not stale, "removed updater exports still imported: " + ", ".join(stale)

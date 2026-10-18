"""Regression coverage for startup import ordering and session validation."""

from __future__ import annotations

import ast
import importlib
import logging
import os
import sys
import types

from conftest import ROOT


def test_update_envs_enumerates_database_keys_once(tmp_path, monkeypatch):
    import pyUltroid
    from pyUltroid.startup.funcs import update_envs

    class FakeDB:
        def __init__(self):
            self.key_calls = 0
            self.writes = []

        def keys(self):
            self.key_calls += 1
            return ["EXISTING_SETTING"]

        def set_key(self, key, value):
            self.writes.append((key, value))

    db = FakeDB()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(os, "environ", {"EXISTING_SETTING": "env-value"})
    monkeypatch.setattr(pyUltroid, "udB", db)

    update_envs()

    assert db.key_calls == 1
    assert db.writes == [("EXISTING_SETTING", "env-value")]


def test_runtime_binds_database_before_environment_sync():
    source = (ROOT / "pyUltroid" / "_core" / "runtime.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    runtime = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "RuntimeMixin"
    )
    opener = next(
        node
        for node in runtime.body
        if isinstance(node, ast.FunctionDef) and node.name == "open_database"
    )
    calls = [node for node in ast.walk(opener) if isinstance(node, ast.Call)]
    database_creation = next(
        node
        for node in opener.body
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
        and node.value.func.id == "UltroidDB"
    )
    bind_instance = next(
        node
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "bind_db"
    )
    bind_package_alias = next(
        node
        for node in calls
        if isinstance(node.func, ast.Name)
        and node.func.id == "setattr"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value == "udB"
    )
    sync_environment = next(
        node
        for node in calls
        if isinstance(node.func, ast.Name) and node.func.id == "update_envs"
    )

    assert database_creation.lineno < bind_instance.lineno
    assert bind_instance.lineno < bind_package_alias.lineno
    assert bind_package_alias.lineno < sync_environment.lineno


def test_startup_funcs_does_not_eagerly_import_fns():
    path = ROOT / "pyUltroid" / "startup" / "funcs.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    eager_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.level == 2
        and node.module == "fns"
        and any(alias.name == "fns" for alias in node.names)
    ]
    assert not eager_imports, (
        "startup.funcs must defer fns until runtime helpers execute"
    )


def test_valid_session_validation_does_not_import_strings(module_isolation):
    """A valid session needs no translated error text during bootstrap."""
    with module_isolation("pyUltroid", "strings"):
        root = types.ModuleType("pyUltroid")
        root.__path__ = [str(ROOT / "pyUltroid")]
        startup = types.ModuleType("pyUltroid.startup")
        startup.__path__ = [str(ROOT / "pyUltroid" / "startup")]
        startup.LOGS = logging.getLogger("session-validation-test")
        sys.modules["pyUltroid"] = root
        sys.modules["pyUltroid.startup"] = startup

        connections = importlib.import_module("pyUltroid.startup.connections")
        sentinel = object()
        connections.StringSession = lambda _session: sentinel

        assert (
            connections.validate_session("1" + "A" * 352, logger=startup.LOGS)
            is sentinel
        )
        assert "strings" not in sys.modules


def test_deployment_notice_uses_separate_native_rich_blocks():
    import asyncio
    from types import SimpleNamespace

    from telethon.tl.functions.messages import SendMessageRequest
    from telethon.tl.types import InputRichMessageHTML

    from pyUltroid._core.richmsg import RichBlocks, rich_message_html, send_rich
    from pyUltroid.startup.funcs import _deployment_notice_blocks

    inventory = {
        "runtime": [
            ["Runtime area", "Count", "Handlers / detail"],
            ["Plugins", "12", "84"],
            ["Add-ons", "3", "40"],
            ["Command patterns", "703", "in 172 modules"],
            ["Telegram event handlers", "88", "registered callbacks"],
            ["Runtime modules", "4", "17"],
            ["Runtime listeners", "2", "registered callbacks"],
            ["Applied patches", "3", "currently active"],
        ],
        "pmf": [
            ["PMF feature", "Registered"],
            ["Toggles", "8"],
            ["Actions", "5"],
            ["Custom menu buttons", "9"],
            ["Registered callbacks", "22"],
        ],
    }
    notice = _deployment_notice_blocks(
        assistant_username="bot<&",
        version="20.09<test>",
        warning="Add the assistant to the <log> channel.",
        inventory=inventory,
    )
    assert isinstance(notice, RichBlocks)
    rich_payload = rich_message_html(notice)
    assert isinstance(rich_payload, InputRichMessageHTML)
    assert "<h1>SarahUB is ready</h1>" in rich_payload.html
    assert "<p>Startup finished." in rich_payload.html
    assert "<li>Assistant bot: @bot&lt;&amp;</li>" in rich_payload.html
    assert "<li>Version: 20.09&lt;test&gt;</li>" in rich_payload.html
    assert (
        "<p>Action needed: Add the assistant to the &lt;log&gt; channel.</p>"
        in rich_payload.html
    )
    assert "<h2>Runtime inventory</h2>" in rich_payload.html
    assert "<td>Plugins</td>" in rich_payload.html
    assert "<td>12</td>" in rich_payload.html
    assert "<td>84</td>" in rich_payload.html
    assert "<h2>Plugin Manifest Framework</h2>" in rich_payload.html
    assert "<td>Registered callbacks</td>" in rich_payload.html
    assert "Startup completed." not in rich_payload.html
    assert "Registry" not in rich_payload.html
    assert "Registered Commands" not in rich_payload.html

    class Client:
        me = SimpleNamespace(bot=True, premium=False)

        async def get_input_entity(self, _peer):
            from telethon.tl.types import InputPeerSelf

            return InputPeerSelf()

        def build_reply_markup(self, _buttons):
            return None

        async def __call__(self, request):
            self.request = request
            return SimpleNamespace(id=1)

    client = Client()
    result = asyncio.run(
        send_rich(
            client,
            "log-channel",
            notice,
            link_preview=False,
        )
    )

    assert result.id == 1
    assert isinstance(client.request, SendMessageRequest)
    assert isinstance(client.request.rich_message, InputRichMessageHTML)
    assert "<h1>SarahUB is ready</h1>" in client.request.rich_message.html
    assert "<li>Assistant bot: @bot&lt;&amp;</li>" in client.request.rich_message.html


def test_deployment_inventory_counts_live_registry_surfaces(monkeypatch):
    import importlib
    import sys
    import types
    from types import SimpleNamespace

    package = types.ModuleType("pyUltroid.dB")
    package.__path__ = []
    database_core = types.ModuleType("pyUltroid.dB._core")
    database_core.LIST = {"plugins/a": [1, 2], "addons/b": [3]}
    database_core.LOADED = {"plugins/a": [4]}
    monkeypatch.setitem(sys.modules, "pyUltroid.dB", package)
    monkeypatch.setitem(sys.modules, "pyUltroid.dB._core", database_core)

    active = [
        SimpleNamespace(name="plugins:a", path="plugins/a.py", handler_count=5),
        SimpleNamespace(name="addons:b", path="addons/b.py", handler_count=3),
        SimpleNamespace(name="modules:c", path="modules/c.py", handler_count=2),
    ]
    entries = {
        "plugins:a": [
            {"type": "toggle", "toggle_key": "plugins:a.toggle"},
            {"type": "action", "toggle_key": "plugins:a.action"},
            {"type": "manifest_button", "toggle_key": "plugins:a.menu"},
        ]
    }
    manifest = SimpleNamespace(
        get_entries=lambda name: entries.get(name, []),
        get_handler=lambda key: (
            (lambda: None)
            if key
            in {
                "plugins:a.toggle",
                "plugins:a.action",
                "plugins:a.menu",
            }
            else None
        ),
        get_state=lambda _key: False,
    )
    clients = (object(), object())
    modules = SimpleNamespace(
        status=lambda: {
            "modules": {
                "one": {"handlers": 2},
                "two": {"handlers": 1},
            },
            "listeners": 1,
        }
    )
    fake_core = SimpleNamespace(
        registry=SimpleNamespace(
            get_active=lambda: active,
            get_by_status=lambda _status: [SimpleNamespace(name="broken")],
        ),
        manifest=manifest,
        clients=clients,
        modules=modules,
        master=object(),
        available_roles=lambda: ("user", "bot"),
    )
    monkeypatch.setattr(importlib.import_module("pyUltroid.ultroid"), "core", fake_core)
    internals = importlib.import_module("pyUltroid._core.internals")
    monkeypatch.setattr(
        internals, "count_handlers", lambda client: 4 if client is clients[0] else 5
    )
    patching = importlib.import_module("pyUltroid.startup.patching")
    monkeypatch.setattr(
        patching,
        "get_patch_registry",
        lambda: SimpleNamespace(applied_patches=lambda: ["patch-a", "patch-b"]),
    )

    from pyUltroid.startup.funcs import _deployment_inventory

    inventory = _deployment_inventory()
    runtime = {row[0]: row[1:] for row in inventory["runtime"][1:]}
    pmf = {row[0]: row[1] for row in inventory["pmf"][1:]}

    assert runtime["Plugins"] == ["1", "5"]
    assert runtime["Add-ons"] == ["1", "3"]
    assert runtime["Other modules"] == ["1", "2"]
    assert runtime["Failed module loads"] == ["1", "-"]
    assert runtime["Command patterns"] == ["3", "in 2 modules"]
    assert runtime["Telegram clients"] == ["2", "user, bot"]
    assert runtime["Telegram event handlers"] == ["9", "registered callbacks"]
    assert runtime["Runtime modules"] == ["2", "3"]
    assert runtime["Runtime listeners"] == ["1", "registered callbacks"]
    assert runtime["Applied patches"] == ["2", "currently active"]
    assert pmf == {
        "Toggles": "1",
        "Actions": "1",
        "Custom menu buttons": "1",
        "Registered callbacks": "3",
    }

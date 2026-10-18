from __future__ import annotations

import ast
import asyncio
import os
import sys
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest


def run(coro):
    return asyncio.run(coro)


def _load_fast_uploader(monkeypatch):
    source = Path(__file__).resolve().parents[1] / "pyUltroid/startup/BaseClient.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    client = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "UltroidClient"
    )
    method = next(
        node
        for node in client.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "fast_uploader"
    )
    isolated = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    fasttelethon = ModuleType("pyUltroid.fns.FastTelethon")
    monkeypatch.setitem(sys.modules, fasttelethon.__name__, fasttelethon)
    namespace = {
        "os": os,
        "Path": Path,
        "time": time,
        "_helper": SimpleNamespace(progress=None),
    }
    exec(compile(isolated, str(source), "exec"), namespace)  # noqa: S102 - isolated checked-in method
    return namespace["fast_uploader"], fasttelethon


def _load_def_logs(fns):
    source = Path(__file__).resolve().parents[1] / "pyUltroid/fns/helper.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    helper = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Helper")
    method = next(
        node for node in helper.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "def_logs"
    )
    method.body = [
        ast.Pass() if isinstance(node, ast.ImportFrom) and node.module == "_fns" else node
        for node in method.body
    ]
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {
        "Path": Path,
        "fns": fns,
        "LOGS": SimpleNamespace(
            warning=lambda *args, **kwargs: None,
            exception=lambda *args, **kwargs: None,
        ),
    }
    exec(compile(module, str(source), "exec"), namespace)  # noqa: S102 - isolated method
    return namespace["def_logs"]


class _Notice:
    def __init__(self):
        self.edits = []
        self.deleted = False

    async def edit(self, text, **kwargs):
        self.edits.append((text, kwargs))
        return self

    async def delete(self):
        self.deleted = True


class _Event:
    def __init__(self):
        self.notice = _Notice()
        self.eor_text = []
        self.chat_id = 42
        self.files = []

        async def send_file(chat_id, **kwargs):
            self.files.append((chat_id, kwargs))
            return "uploaded-log"

        self.client = SimpleNamespace(send_file=send_file)

    async def eor(self, text):
        self.eor_text.append(text)
        return self.notice


def test_fast_uploader_calls_fasttelethon_with_the_current_client(
    tmp_path, monkeypatch
):
    fast_uploader, fasttelethon = _load_fast_uploader(monkeypatch)
    source = tmp_path / "payload.bin"
    source.write_bytes(b"content")
    calls = []
    uploaded = object()

    async def upload_file(**kwargs):
        calls.append(kwargs)
        assert kwargs["file"].read() == b"content"
        return uploaded

    monkeypatch.setattr(fasttelethon, "upload_file", upload_file, raising=False)
    client = SimpleNamespace(_bot=False, _cache={})

    result, _elapsed = run(fast_uploader(client, source, use_cache=False))

    assert result is uploaded
    assert calls[0]["client"] is client
    assert calls[0]["filename"] == "payload.bin"


def test_fast_uploader_rejects_empty_files_before_telegram_upload(
    tmp_path, monkeypatch
):
    fast_uploader, fasttelethon = _load_fast_uploader(monkeypatch)
    source = tmp_path / "empty.log"
    source.touch()
    called = False

    async def upload_file(**_kwargs):
        nonlocal called
        called = True
        return object()

    monkeypatch.setattr(fasttelethon, "upload_file", upload_file, raising=False)
    client = SimpleNamespace(_bot=False, _cache={})

    with pytest.raises(ValueError, match="empty file"):
        run(fast_uploader(client, source, use_cache=False))

    assert not called


def test_def_logs_returns_text_for_empty_file_without_paste_or_upload(
    tmp_path, monkeypatch
):
    source = tmp_path / "empty.log"
    source.touch()
    paste_calls = []

    class FakeFns:
        async def get_paste(self, text):
            paste_calls.append(text)
            return True, "unexpected"

    event = _Event()

    run(_load_def_logs(FakeFns())(None, event, source))

    assert paste_calls == []
    assert event.notice.edits == [
        ("No log output is available yet.", {"parse_mode": None})
    ]


def test_def_logs_survives_unavailable_paste_and_sends_nonempty_file(
    tmp_path, monkeypatch
):
    source = tmp_path / "ultroid.log"
    source.write_text("Recent runtime failure\n", encoding="utf-8")

    class FakeFns:
        async def get_paste(self, _text):
            raise RuntimeError("paste service unavailable")

    event = _Event()

    result = run(_load_def_logs(FakeFns())(None, event, source))

    assert result == "uploaded-log"
    assert event.notice.edits == []
    assert event.notice.deleted
    assert len(event.files) == 1
    chat_id, kwargs = event.files[0]
    assert chat_id == event.chat_id
    assert kwargs["file"] == str(source)
    assert "Paste unavailable" in kwargs["caption"]
    assert kwargs["parse_mode"] is None

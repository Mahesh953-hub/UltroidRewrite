from __future__ import annotations

import importlib.util
import logging
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telethon.extensions.markdownv2 import Markdown as MarkdownV2

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture

def web_addon(monkeypatch):
    """Load addons.web with a tiny package stub; do not bootstrap the bot."""
    package = types.ModuleType("addons")
    package.__path__ = [str(ROOT / "addons")]
    package.async_searcher = AsyncMock()
    package.check_filename = lambda name: name
    package.eor = AsyncMock()
    package.LOGS = logging.getLogger("web-addon-render-tests")
    package.udB = SimpleNamespace(get_key=lambda _key: None)
    package.ultroid_cmd = lambda *args, **kwargs: lambda func: func
    monkeypatch.setitem(sys.modules, "addons", package)

    path = ROOT / "addons" / "web.py"
    spec = importlib.util.spec_from_file_location("addons.web", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "addons.web", module)
    spec.loader.exec_module(module)
    return module


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit(self, text, **kwargs):
        self.edits.append((text, kwargs))

    async def try_delete(self):
        return None


@pytest.mark.asyncio
async def test_web_answer_uses_parse_mode_v2_and_escapes_untrusted_text(
    web_addon, monkeypatch
):
    answer = "literal *stars* _underscores_ [brackets] + <tag> & text."
    monkeypatch.setattr(
        web_addon, "fetch_web_answer", AsyncMock(return_value=answer)
    )
    pending = FakeMessage()

    class Event:
        chat_id = -100123456
        id = 88
        reply_to_msg_id = 77
        client = SimpleNamespace(send_file=AsyncMock())
        pattern_match = SimpleNamespace(group=lambda _index: "query + [x] <value>")

        async def get_reply_message(self):
            return None

        async def eor(self, text):
            return pending

    await web_addon.ask_web(Event())

    text, kwargs = pending.edits[-1]
    assert kwargs["parse_mode"] == "v2"
    parsed, _entities = MarkdownV2().parse(text)
    assert "query + [x] <value>" in parsed
    assert answer in parsed
    assert "<b>" not in text and "<code>" not in text


@pytest.mark.asyncio
async def test_bing_rss_search_parses_results_to_plain_text(web_addon):
    web_addon.async_searcher = AsyncMock(
        return_value=(
            b"<?xml version='1.0'?><rss><channel>"
            b"<item><title>Paris &amp; France</title>"
            b"<link>https://example.com/?a=1&amp;b=2</link>"
            b"<description>The capital city.</description></item>"
            b"</channel></rss>"
        )
    )

    answer = await web_addon.fetch_bing_search_summary("capital of France")

    assert "1. Paris & France" in answer
    assert "The capital city." in answer
    assert "https://example.com/?a=1&b=2" in answer
    web_addon.async_searcher.assert_awaited_once()


@pytest.mark.asyncio
async def test_bing_is_used_before_the_legacy_paal_fallback(web_addon, monkeypatch):
    monkeypatch.setattr(
        web_addon,
        "fetch_ddg_chat_answer",
        AsyncMock(side_effect=RuntimeError("chat unavailable")),
    )
    monkeypatch.setattr(
        web_addon,
        "fetch_ddg_search_summary",
        AsyncMock(side_effect=RuntimeError("search unavailable")),
    )
    bing = AsyncMock(return_value="Bing results")
    paal = AsyncMock(return_value="Paal results")
    monkeypatch.setattr(web_addon, "fetch_bing_search_summary", bing)
    monkeypatch.setattr(web_addon, "fetch_paal_answer", paal)

    answer = await web_addon.fetch_web_answer("a generic query")

    assert answer == "Bing results"
    bing.assert_awaited_once_with("a generic query")
    paal.assert_not_awaited()

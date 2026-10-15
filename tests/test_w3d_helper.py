"""Tests for pyUltroid.fns.helper shared aiohttp session (W3-D)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]


def _run(coro):
    return asyncio.run(coro)


class TestHelperSharedSession:
    def test_no_requests_import_in_helper(self):
        source = (ROOT / "pyUltroid" / "fns" / "helper.py").read_text()
        assert "import requests" not in source, "helper.py still imports requests"
        assert "requests.get" not in source, "helper.py still uses requests.get"
        assert "requests.post" not in source, "helper.py still uses requests.post"

    def test_async_searcher_reuses_shared_session(self):
        from pyUltroid.fns import fns

        async def handler(request):
            return web.Response(text="hello")

        app = web.Application()
        app.router.add_get("/", handler)

        async def run_test():
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            try:
                url = f"http://127.0.0.1:{site.port}/"
                session = fns._get_http_session()
                result = await fns.async_searcher(url)
                assert result == "hello"
                assert fns._get_http_session() is session
            finally:
                await runner.cleanup()
                await fns._close_http_session()

        _run(run_test())

    def test_async_searcher_accepts_caller_session(self):
        from aiohttp import ClientSession

        from pyUltroid.fns import fns

        async def handler(request):
            return web.Response(text="custom")

        app = web.Application()
        app.router.add_get("/", handler)

        async def run_test():
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            try:
                url = f"http://127.0.0.1:{site.port}/"
                async with ClientSession() as session:
                    result = await fns.async_searcher(url, session=session)
                    assert result == "custom"
            finally:
                await runner.cleanup()
                await fns._close_http_session()

        _run(run_test())

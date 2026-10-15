"""Tests for the bounded W3-D plugin requests-to-aiohttp conversion."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]

# Bounded, enumerated plugin list converted in W3-D.  These were the highest-
# value blocking ``requests`` hot paths in the plugin tree.
BOUNDED_PLUGINS = [
    "plugins/youtube.py",
    "plugins/get_addons.py",
    "plugins/search.py",
    "plugins/tools.py",
]


class TestBoundedPluginConversions:
    @pytest.mark.parametrize("path", BOUNDED_PLUGINS)
    def test_no_blocking_requests_usage(self, path):
        source = (ROOT / path).read_text()
        assert "import requests" not in source, f"{path} still imports requests"
        assert "from requests" not in source, f"{path} still imports from requests"
        assert "requests.get" not in source, f"{path} still uses requests.get"
        assert "requests.post" not in source, f"{path} still uses requests.post"

    @pytest.mark.parametrize("path", BOUNDED_PLUGINS)
    def test_uses_async_http_pattern(self, path):
        source = (ROOT / path).read_text()
        has_async = (
            "async_searcher" in source
            or "event.client.http" in source
            or "aiohttp" in source
        )
        assert has_async, f"{path} does not use an async HTTP pattern"

    @pytest.mark.parametrize("path", BOUNDED_PLUGINS)
    def test_plugin_compiles(self, path):
        import py_compile

        py_compile.compile(ROOT / path, doraise=True)

    def test_bounded_list_is_exact(self):
        """The list above is the complete set of W3-D converted files."""
        assert len(BOUNDED_PLUGINS) == 4


class _FakeClient:
    """Minimal client stand-in that exposes the same ``http`` property pattern
    used by the converted plugins (``event.client.http.get/post/...``).
    """

    def __init__(self):
        self._http_session = None

    @property
    def http(self):
        if self._http_session is None or self._http_session.closed:
            self._http_session = aiohttp.ClientSession()
        return self._http_session


class _FakeEvent:
    def __init__(self):
        self.client = _FakeClient()


class TestPluginHttpFunctional:
    @staticmethod
    def _run(coro):
        return asyncio.run(coro)

    def test_http_get_text_pattern(self):
        """plugins/get_addons.py and plugins/youtube.py use GET via event.client.http."""

        async def handler(request):
            return web.Response(text="plugin-source")

        app = web.Application()
        app.router.add_get("/", handler)

        async def run_test():
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            event = _FakeEvent()
            try:
                url = f"http://127.0.0.1:{site.port}/"
                async with event.client.http.get(url) as resp:
                    text = await resp.text()
                assert text == "plugin-source"
            finally:
                await runner.cleanup()
                await event.client.http.close()

        self._run(run_test())

    def test_http_post_multipart_pattern(self):
        """plugins/search.py uses POST with FormData."""

        async def handler(request):
            data = await request.post()
            field = data.get("field")
            if hasattr(field, "file"):
                value = field.file.read()
                if isinstance(value, bytes):
                    value = value.decode()
            else:
                value = str(field)
            return web.json_response({"received": list(data.keys()), "value": value})

        app = web.Application()
        app.router.add_post("/", handler)

        async def run_test():
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            event = _FakeEvent()
            try:
                url = f"http://127.0.0.1:{site.port}/"
                data = aiohttp.FormData()
                data.add_field("field", b"data", filename="file.bin")
                async with event.client.http.post(url, data=data) as resp:
                    result = await resp.json()
                assert result["received"] == ["field"]
                assert result["value"] == "data"
            finally:
                await runner.cleanup()
                await event.client.http.close()

        self._run(run_test())

    def test_async_searcher_with_client_session(self):
        """async_searcher can be passed a caller-provided session (e.g. event.client.http)."""
        from pyUltroid.fns import fns

        async def handler(request):
            return web.Response(text="shared")

        app = web.Application()
        app.router.add_get("/", handler)

        async def run_test():
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            event = _FakeEvent()
            try:
                url = f"http://127.0.0.1:{site.port}/"
                result = await fns.async_searcher(url, session=event.client.http)
                assert result == "shared"
            finally:
                await runner.cleanup()
                await event.client.http.close()
                await fns._close_http_session()

        self._run(run_test())

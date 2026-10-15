"""Unit + integration tests for the web upload backbone (Phase 3.7).

Covers ``pyUltroid/_core/webupload.py``:

* registry / alias / label / menu metadata(``resolve_service``,
  ``get_uploader``, ``UPLOAD_SERVICES``, ``MENU_SERVICES``, ``SERVICE_LABELS``);
* per-service ``parse_response`` against synthetic responses (no network);
* the streaming transport — multipart framing, raw-body PUT/POST, exact
  ``Content-Length``, JSON vs text responses and the progress callback —
  driven through a throwaway in-process ``aiohttp.web`` server so no external
  host is contacted;
* error paths (unknown service, missing file, non-2xx response) and the
  import-safety invariant (no top-level ``pyUltroid`` imports).

Async tests use ``asyncio.run`` — the suite has no pytest-asyncio, matching
``tests/test_queue_manager.py``.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT))

from aiohttp import web  # noqa: E402

from pyUltroid._core.webupload import (  # noqa: E402
    MENU_SERVICES,
    SERVICE_ALIASES,
    SERVICE_LABELS,
    UPLOAD_SERVICES,
    CatboxUploader,
    FileIoUploader,
    FilebinUploader,
    LitterboxUploader,
    NullStUploader,
    SiaskyUploader,
    TransferShUploader,
    UguuUploader,
    UploadError,
    UploadResult,
    WebUploader,
    get_uploader,
    resolve_service,
    upload_to,
)


class RegistryTests(unittest.TestCase):
    def test_upload_services_and_menu_are_consistent(self):
        # every menu key is a real service and has a label
        for key in MENU_SERVICES:
            self.assertIn(key, UPLOAD_SERVICES)
            self.assertIn(key, SERVICE_LABELS)
        # uguu (absorbed from MYADDONS) is exposed; siasky is available but not
        # in the menu (legacy/often-unavailable)
        self.assertIn("uguu", UPLOAD_SERVICES)
        self.assertIn("uguu", MENU_SERVICES)
        self.assertIn("siasky", UPLOAD_SERVICES)
        self.assertNotIn("siasky", MENU_SERVICES)

    def test_resolve_service_handles_keys_and_aliases(self):
        cases = {
            "catbox": "catbox",
            "CATBOX": "catbox",
            "catbox.moe": "catbox",
            "litterbox": "litterbox",
            "file.io": "file.io",
            "fileio": "file.io",
            "0x0.st": "0x0.st",
            "0x0": "0x0.st",
            "transfer": "transfer",
            "transfer.sh": "transfer",
            "transfersh": "transfer",
            "uguu": "uguu",
            "uguu.se": "uguu",
            "filebin": "filebin",
            "siasky": "siasky",
        }
        for raw, expected in cases.items():
            self.assertEqual(resolve_service(raw), expected, raw)
        self.assertIsNone(resolve_service("nope"))
        self.assertIsNone(resolve_service(""))
        self.assertIsNone(resolve_service(None))

    def test_get_uploader_returns_subclasses_or_none(self):
        self.assertIsInstance(get_uploader("catbox"), CatboxUploader)
        self.assertIsInstance(get_uploader("transfersh"), TransferShUploader)
        self.assertIsNone(get_uploader("nope"))

    def test_aliases_map_to_known_services(self):
        for alias, key in SERVICE_ALIASES.items():
            self.assertIn(key, UPLOAD_SERVICES)


class ParseResponseTests(unittest.TestCase):
    """Exercise each service's response parser with synthetic bodies."""

    def _call(self, uploader, *, text="", json_data=None):
        return uploader.parse_response(200, text, json_data, "x.bin")

    def test_catbox_text_url(self):
        r = self._call(CatboxUploader(), text="https://files.catbox.moe/ab.png\n")
        self.assertEqual(r.url, "https://files.catbox.moe/ab.png")
        self.assertEqual(r.service, "catbox")
        self.assertIsNone(r.delete_url)

    def test_catbox_error_raises(self):
        with self.assertRaises(UploadError):
            self._call(CatboxUploader(), text="errormsg")

    def test_litterbox_carries_expiry(self):
        r = self._call(LitterboxUploader(), text="https://litterbox.catbox.moe/x.png")
        self.assertEqual(r.url, "https://litterbox.catbox.moe/x.png")
        self.assertEqual(r.expires, "72h")

    def test_fileio_json_link_and_delete(self):
        data = {
            "success": True,
            "key": "abc",
            "link": "https://file.io/abc",
            "expiry": "14 days",
        }
        r = self._call(FileIoUploader(), json_data=data)
        self.assertEqual(r.url, "https://file.io/abc")
        self.assertEqual(r.delete_url, "https://file.io/abc")
        self.assertEqual(r.delete_note, "DELETE request")
        self.assertEqual(r.expires, "14 days")

    def test_fileio_falls_back_to_key_when_no_link(self):
        r = self._call(FileIoUploader(), json_data={"key": "zz"})
        self.assertEqual(r.url, "https://file.io/zz")

    def test_nullst_text_url(self):
        r = self._call(NullStUploader(), text="https://0x0.st/zz\n")
        self.assertEqual(r.url, "https://0x0.st/zz")
        self.assertEqual(r.service, "0x0.st")

    def test_transfer_text_url(self):
        r = self._call(TransferShUploader(), text="https://transfer.sh/x.bin")
        self.assertEqual(r.url, "https://transfer.sh/x.bin")
        self.assertEqual(r.expires, "14 days")

    def test_uguu_files_array(self):
        r = self._call(
            UguuUploader(), json_data={"files": [{"url": "https://a.uguu.se/x.bin"}]}
        )
        self.assertEqual(r.url, "https://a.uguu.se/x.bin")
        self.assertEqual(r.expires, "3 hours")

    def test_uguu_top_level_url_fallback(self):
        r = self._call(UguuUploader(), json_data={"url": "https://a.uguu.se/y.bin"})
        self.assertEqual(r.url, "https://a.uguu.se/y.bin")

    def test_filebin_uses_request_url_and_delete(self):
        up = FilebinUploader()
        # force a deterministic bin by overriding the helper
        up._bin = lambda p: "ultroid-test"
        r = up.parse_response(201, "{}", {"bin": {"id": "ultroid-abc"}}, "hi.txt")
        self.assertTrue(r.url.endswith("/ultroid-test/hi.txt"))
        self.assertEqual(r.delete_url, "https://filebin.net/ultroid-abc")

    def test_siasky_skylink(self):
        r = self._call(SiaskyUploader(), json_data={"skylink": "abcd"})
        self.assertEqual(r.url, "https://siasky.net/abcd")

    def test_siasky_missing_skylink_raises(self):
        with self.assertRaises(UploadError):
            self._call(SiaskyUploader(), json_data={})

    def test_result_str_is_url(self):
        self.assertEqual(
            str(UploadResult(url="https://x/", service="catbox")), "https://x/"
        )


class TransportTests(unittest.TestCase):
    """Full upload round-trips through an in-process aiohttp server.

    Each test starts the server, runs the upload and tears the server down
    inside a *single* ``asyncio.run`` — an ``aiohttp`` runner bound to one
    event loop does not survive that loop closing, so the whole scenario must
    share one loop.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._payload = b"Ultroid webupload backbone test payload " * 2000  # ~78 KiB
        self._file = os.path.join(self._tmp, "hello.txt")
        with open(self._file, "wb") as fh:
            fh.write(self._payload)
        self._captured = {}

    async def _with_server(self, scenario):
        """Start the test server, run *scenario(base)*, then clean up."""
        captured = self._captured

        async def catbox(request):
            captured["catbox_ct"] = request.headers.get("Content-Type", "")
            captured["catbox_cl"] = request.headers.get("Content-Length", "")
            reader = await request.multipart()
            fields, fname, fbytes = {}, None, b""
            async for part in reader:
                if part.name == "fileToUpload":
                    fname, fbytes = part.filename, await part.read(decode=False)
                else:
                    fields[part.name] = (await part.read(decode=True)).decode()
            captured["catbox_fields"] = fields
            captured["catbox_file"] = fbytes
            return web.Response(text=f"https://files.catbox.moe/{fname}")

        async def fileio(request):
            reader = await request.multipart()
            async for part in reader:
                if part.name == "file":
                    captured["fileio_file"] = await part.read(decode=False)
            return web.json_response(
                {
                    "success": True,
                    "key": "k1",
                    "link": "https://file.io/k1",
                    "expiry": "14 days",
                }
            )

        async def uguu(request):
            reader = await request.multipart()
            fn = "x"
            async for part in reader:
                if part.name == "files[]":
                    fn = part.filename
                    captured["uguu_file"] = await part.read(decode=False)
            return web.json_response({"files": [{"url": f"https://a.uguu.se/{fn}"}]})

        async def transfer(request):
            captured["transfer_method"] = request.method
            captured["transfer_ct"] = request.headers.get("Content-Type", "")
            captured["transfer_body"] = await request.read()
            return web.Response(
                text=f"https://transfer.sh/{os.path.basename(request.path)}"
            )

        async def filebin(request):
            captured["filebin_body"] = await request.read()
            return web.json_response({"bin": {"id": "ultroid-abc"}}, status=201)

        async def err(request):
            return web.Response(text="too big", status=413)

        app = web.Application()
        app.router.add_post("/catbox", catbox)
        app.router.add_post("/file.io", fileio)
        app.router.add_post("/uguu", uguu)
        app.router.add_route("PUT", "/transfer/{name}", transfer)
        app.router.add_post("/filebin/{bin}/{name}", filebin)
        app.router.add_post("/err", err)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        try:
            await scenario(base)
        finally:
            await runner.cleanup()

    def test_catbox_multipart_upload_with_progress(self):
        async def scenario(base):
            CatboxUploader.endpoint = f"{base}/catbox"
            prog = {"calls": 0, "last": (0, 0)}

            async def on_progress(sent, total):
                prog["calls"] += 1
                prog["last"] = (sent, total)
                self.assertEqual(total, len(self._payload))

            r = await upload_to("catbox", self._file, on_progress=on_progress)
            self.assertEqual(r.url, "https://files.catbox.moe/hello.txt")
            self.assertEqual(r.service, "catbox")
            self.assertEqual(self._captured["catbox_fields"], {"reqtype": "fileupload"})
            self.assertEqual(self._captured["catbox_file"], self._payload)
            self.assertTrue(
                self._captured["catbox_ct"].startswith("multipart/form-data; boundary=")
            )
            self.assertTrue(self._captured["catbox_cl"].isdigit())
            self.assertGreater(int(self._captured["catbox_cl"]), len(self._payload))
            # 78 KiB / 64 KiB chunk -> at least 2 progress callbacks, final == total.
            self.assertGreaterEqual(prog["calls"], 2)
            self.assertEqual(prog["last"], (len(self._payload), len(self._payload)))

        asyncio.run(self._with_server(scenario))

    def test_fileio_json_upload(self):
        async def scenario(base):
            FileIoUploader.endpoint = f"{base}/file.io"
            r = await upload_to("file.io", self._file)
            self.assertEqual(r.url, "https://file.io/k1")
            self.assertEqual(r.delete_url, "https://file.io/k1")
            self.assertEqual(r.delete_note, "DELETE request")
            self.assertEqual(r.expires, "14 days")
            self.assertEqual(self._captured["fileio_file"], self._payload)

        asyncio.run(self._with_server(scenario))

    def test_uguu_json_upload(self):
        async def scenario(base):
            UguuUploader.endpoint = f"{base}/uguu"
            r = await upload_to("uguu", self._file)
            self.assertEqual(r.url, "https://a.uguu.se/hello.txt")
            self.assertEqual(r.expires, "3 hours")
            self.assertEqual(self._captured["uguu_file"], self._payload)

        asyncio.run(self._with_server(scenario))

    def test_transfer_put_raw_body(self):
        async def scenario(base):
            TransferShUploader.endpoint = f"{base}/transfer"
            r = await upload_to("transfer.sh", self._file)
            self.assertEqual(r.url, "https://transfer.sh/hello.txt")
            self.assertEqual(self._captured["transfer_method"], "PUT")
            self.assertEqual(self._captured["transfer_ct"], "application/octet-stream")
            self.assertEqual(self._captured["transfer_body"], self._payload)

        asyncio.run(self._with_server(scenario))

    def test_filebin_post_raw_body(self):
        async def scenario(base):
            FilebinUploader.endpoint = f"{base}/filebin"
            r = await upload_to("filebin", self._file)
            self.assertTrue(r.url.startswith(f"{base}/filebin/ultroid-"))
            self.assertTrue(r.url.endswith("/hello.txt"))
            self.assertEqual(self._captured["filebin_body"], self._payload)
            self.assertEqual(r.delete_url, "https://filebin.net/ultroid-abc")

        asyncio.run(self._with_server(scenario))

    def test_http_error_is_wrapped(self):
        async def scenario(base):
            NullStUploader.endpoint = f"{base}/err"
            with self.assertRaises(UploadError) as ctx:
                await upload_to("0x0.st", self._file)
            self.assertEqual(ctx.exception.status, 413)
            self.assertEqual(ctx.exception.service, "0x0.st")

        asyncio.run(self._with_server(scenario))

    def test_unknown_service_raises(self):
        async def scenario(base):
            with self.assertRaises(UploadError):
                await upload_to("nope", self._file)

        asyncio.run(self._with_server(scenario))

    def test_missing_file_raises(self):
        async def scenario(base):
            with self.assertRaises(UploadError):
                await upload_to("catbox", os.path.join(self._tmp, "missing.xyz"))

        asyncio.run(self._with_server(scenario))


class ImportSafetyTests(unittest.TestCase):
    def test_no_top_level_pyultroid_imports(self):
        src = Path(ROOT, "pyUltroid", "_core", "webupload.py").read_text()
        # crude but effective: any top-level (column 0) pyUltroid import would
        # risk a cycle via the _core package's lazy re-export.
        offenders = [
            line
            for line in src.splitlines()
            if line.startswith(("import pyUltroid", "from pyUltroid"))
        ]
        self.assertEqual(offenders, [], f"top-level pyUltroid imports: {offenders}")

    def test_core_package_stays_lazy_for_webupload(self):
        import importlib
        import sys

        # Scope the destructive ``sys.modules`` cleanup so it cannot leak into
        # sibling tests.  Popping every ``pyUltroid._core.*`` entry globally
        # (without restoring) would evict modules that other test modules
        # already bound at import time (e.g. ``pyUltroid._core.queue``); a later
        # lazy re-import via ``__getattr__`` would then build a *second* module
        # and class identity, breaking the ``is`` checks in
        # ``test_queue_manager.TestCoreReexport``.  Snapshot + restore mirrors
        # the ``core_namespace`` pattern in ``test_core_architecture.py``.
        saved = {
            name: mod
            for name, mod in sys.modules.items()
            if name == "pyUltroid._core" or name.startswith("pyUltroid._core.")
        }
        for name in list(saved):
            sys.modules.pop(name, None)
        try:
            core = importlib.import_module("pyUltroid._core")
            # The package init is eager now (the PEP 562 hook is gone): the
            # whole backbone - webupload included - is bound at import time,
            # and the export list is explicit instead of empty.
            self.assertIn("UploadResult", core.__all__)
            self.assertIn("pyUltroid._core.webupload", sys.modules)
            self.assertTrue(hasattr(core, "UploadResult"))
            self.assertTrue(hasattr(core, "QueueManager"))
            # the union lives on the composition root, not the package
            import pyUltroid.ultroid as _composition

            self.assertTrue(hasattr(_composition, "Ultroid"))
        finally:
            for name in [
                n
                for n in list(sys.modules)
                if n == "pyUltroid._core" or n.startswith("pyUltroid._core.")
            ]:
                sys.modules.pop(name, None)
            sys.modules.update(saved)


if __name__ == "__main__":
    unittest.main()

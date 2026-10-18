from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pyUltroid.imaging._pipeline as pipeline_module
from pyUltroid.imaging._pipeline import ImagePipeline


class _FakeImage:
    def __init__(self, size):
        self.size = size
        self.load_called = False

    def load(self):
        self.load_called = True


class _FakePIL:
    Image = _FakeImage

    def __init__(self, image, opened=None):
        self._image = image
        self.opened = opened

    def open(self, source):
        if self.opened is not None:
            self.opened.append(source)
        return self._image


def _pipeline_with_pil(pipeline, fake_pil):
    pipeline._pil = lambda: (fake_pil, None, None, None, None, None)
    return pipeline


def test_byte_limit_rejects_input_before_open(monkeypatch):
    monkeypatch.setattr(pipeline_module, "MAX_IMAGE_BYTES", 3)
    opened = []
    pipeline = _pipeline_with_pil(ImagePipeline(), _FakePIL(_FakeImage((1, 1)), opened))

    result = pipeline.load(b"1234")

    assert "exceeds 3 bytes" in result.error
    assert opened == []


def test_pixel_limit_rejects_before_decoding(monkeypatch):
    image = _FakeImage((pipeline_module.MAX_IMAGE_PIXELS + 1, 1))
    opened = []
    pipeline = _pipeline_with_pil(ImagePipeline(), _FakePIL(image, opened))

    result = pipeline.load(b"image-header")

    assert "pixel limit" in result.error
    assert opened
    assert image.load_called is False


def test_telegram_file_size_limit_skips_download(monkeypatch):
    monkeypatch.setattr(pipeline_module, "MAX_IMAGE_BYTES", 8)
    calls = []

    class Message:
        media = object()
        file = SimpleNamespace(size=9)

        async def download_media(self):
            calls.append("download")

    result = asyncio.run(ImagePipeline().load_tg(Message()))

    assert "exceeds 8 bytes" in result.error
    assert calls == []

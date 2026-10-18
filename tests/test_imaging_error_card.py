"""Regression coverage for the public class-based error-card facade."""

from pyUltroid.imaging import Imaging
from pyUltroid.imaging._errorcard import ErrorCardRenderer


class _FakeImage:
    format = "PNG"
    width = 720
    height = 900
    mode = "RGB"


def test_imaging_error_card_uses_error_card_renderer(monkeypatch):
    received = {}

    def fake_render(self, **kwargs):
        received.update(kwargs)
        return _FakeImage(), 3, 4, 12.0

    monkeypatch.setattr(ErrorCardRenderer, "render", fake_render)
    result = Imaging().error_card(
        title="ValueError",
        message="bad input",
        tb="Traceback line",
        plugin="plugins.demo",
        request_id="req-123",
        file_name="demo.py",
    )

    assert result.ok, result.error
    assert result.size == (720, 900)
    assert result.op_chain == ["error_card"]
    assert received == {
        "title": "ValueError",
        "message": "bad input",
        "traceback_text": "Traceback line",
        "plugin": "plugins.demo",
        "request_id": "req-123",
        "file_name": "demo.py",
    }

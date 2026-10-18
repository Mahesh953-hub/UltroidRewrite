"""The bundled Akinator wrapper must coexist with Gemini's httpx floor."""

import asyncio
from pathlib import Path

import httpx
from packaging.version import Version

import akipy
from akipy import utils
from akipy.async_akipy import async_akinator


ROOT = Path(__file__).resolve().parents[1]


def test_akipy_is_bundled_and_httpx_floor_is_preserved():
    assert Path(akipy.__file__).resolve().parent == ROOT / "akipy"
    assert Version(httpx.__version__) >= Version("0.28.1")


def test_akinator_async_game_flow_is_intact(monkeypatch):
    replies = [
        httpx.Response(
            200,
            text="region",
            request=httpx.Request("GET", "https://en.akinator.com"),
        ),
        httpx.Response(
            200,
            text=(
                "session== signature== "
                '<div class="bubble-body"><p class="question-text" '
                'id="question-label">First question?</p></div>'
            ),
            request=httpx.Request("POST", "https://en.akinator.com/game"),
        ),
        httpx.Response(
            200,
            json={"step": 1, "progression": "12.5", "question": "Next?", "akitude": 1},
            request=httpx.Request("POST", "https://en.akinator.com/answer"),
        ),
    ]
    calls = []

    async def fake_request(url, method, data=None):
        calls.append((url, method, data))
        return replies.pop(0)

    monkeypatch.setattr(async_akinator, "async_request_handler", fake_request)

    async def play_one_round():
        game = async_akinator.Akinator()
        await game.start_game(language="en")
        assert game.question == "First question?"
        await game.answer("yes")
        assert game.question == "Next?"
        assert game.step == 1
        assert game.progression == "12.5"
        return calls

    actual_calls = asyncio.run(play_one_round())
    assert [method for _, method, _ in actual_calls] == ["GET", "POST", "POST"]
    assert actual_calls[-1][2]["answer"] == 0
    assert not replies


def test_async_httpx_helper_works_with_modern_client(monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(respond)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        utils.httpx,
        "AsyncClient",
        lambda: original_client(transport=transport),
    )

    async def request_once():
        return await utils.async_request_handler(
            "https://example.test/answer", "POST", {"answer": "yes"}
        )

    response = asyncio.run(request_once())
    assert response.json() == {"ok": True}
    assert seen[0].method == "POST"
    assert seen[0].url.path == "/answer"
    assert seen[0].content == b"answer=yes"

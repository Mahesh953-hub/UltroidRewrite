"""Offline behavioral coverage for public native agent messages and rooted sessions."""

from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType, SimpleNamespace

import pytest
from conftest import ROOT
from telethon import events, types


def _walk(value):
    yield value
    if isinstance(value, types.TLObject):
        for child in vars(value).values():
            if isinstance(child, types.TLObject):
                yield from _walk(child)
            elif isinstance(child, (list, tuple)):
                for item in child:
                    yield from _walk(item)


def test_native_answer_preserves_nested_details_tables_code_and_real_controls():
    from pyUltroid.agent._presentation import native_answer

    answer = (
        "<p><b>Plan</b> &amp; context</p><details><summary>Steps</summary>"
        '<details open><summary>Deep step</summary><pre>print("done")</pre></details>'
        "</details><table><tr><th>Task</th><th>Owner</th></tr>"
        "<tr><td>Review</td><td>You</td></tr></table>"
        '<p><a href="https://example.org/guide">Guide</a></p>'
    )
    payload = native_answer(answer)
    assert isinstance(payload, types.InputRichMessage)
    assert bytes(payload)
    nodes = list(_walk(payload))
    assert any(isinstance(node, types.PageBlockTable) for node in nodes)
    assert any(isinstance(node, types.PageBlockPreformatted) for node in nodes)
    assert sum(isinstance(node, types.PageBlockDetails) for node in nodes) == 4
    assert any(isinstance(node, types.InlineButtonTypeCopy) for node in nodes)
    assert [
        node.url for node in nodes if isinstance(node, types.InlineButtonTypeUrl)
    ] == ["https://example.org/guide"]
    outer = payload.blocks[-1]
    assert isinstance(outer, types.PageBlockDetails)
    assert isinstance(outer.blocks[0], types.PageBlockDetails)
    assert isinstance(outer.blocks[0].blocks[0], types.PageBlockButtonRow)
    assert "Agent run" not in repr(payload)


def test_native_answer_does_not_add_unsafe_url_controls_or_provider_errors():
    from pyUltroid.agent._presentation import native_answer

    payload = native_answer(
        '<p><a href="javascript:alert(1)">bad</a><a href="https://example.org/?token=private">secret link</a></p>'
    )
    assert not any(
        isinstance(node, types.InlineButtonTypeUrl) for node in _walk(payload)
    )
    response = SimpleNamespace(
        content="API_KEY=do-not-expose", error="token=raw-error", provider="mock"
    )
    assert "do-not-expose" not in repr(native_answer(response))
    assert "raw-error" not in repr(native_answer(response))


def test_delivery_roots_placeholder_and_complete_long_answer_attachment(monkeypatch):
    from pyUltroid._core import richmsg
    from pyUltroid.agent._delivery import DeliveryStrategy

    calls = []
    status = SimpleNamespace(id=91)

    class Client:
        _bot = True

        async def send_message(self, peer, text, **kwargs):
            calls.append(("placeholder", peer, text, kwargs))
            return status

        async def send_file(self, peer, document, **kwargs):
            calls.append(("document", peer, document.name, document.read(), kwargs))

    async def edit(client, peer, message, payload):
        calls.append(("edit", peer, message, payload))

    monkeypatch.setattr(richmsg, "edit_rich", edit)
    trigger = SimpleNamespace(client=Client(), chat_id=17, id=8)
    delivery = DeliveryStrategy(trigger, anchor=22)
    answer = "Beginning\n" + "long-answer " * 3000 + "\nDistinct ending."

    async def run():
        assert await delivery.placeholder() is status
        assert (
            await delivery.render(status, SimpleNamespace(content=answer, error=None))
            is status
        )

    asyncio.run(run())
    assert calls[0][3]["reply_to"] == 22
    attachment = next(call for call in calls if call[0] == "document")
    assert attachment[2] == "answer.txt"
    assert attachment[3].decode() == answer
    assert attachment[4]["reply_to"] == 22
    assert isinstance(calls[-1][3], types.InputRichMessage)


@pytest.fixture
def dm_module(monkeypatch, module_isolation):
    import pyUltroid

    with module_isolation("assistant", "pyUltroid._misc._assistant"):
        package = ModuleType("assistant")
        package.__path__ = [str(ROOT / "assistant")]
        shim = ModuleType("pyUltroid._misc._assistant")
        shim.asst_cmd = lambda *_args, **_kwargs: lambda function: function
        monkeypatch.setitem(sys.modules, "assistant", package)
        monkeypatch.setitem(sys.modules, "pyUltroid._misc._assistant", shim)
        monkeypatch.setattr(
            pyUltroid, "asst", SimpleNamespace(on=lambda *_a, **_kw: lambda f: f)
        )
        yield importlib.import_module("assistant.ai_dm")


def test_dm_turn_uses_shared_runtime_full_controls_and_anchor_root(
    dm_module, monkeypatch
):
    dm = dm_module
    seen = []
    status = SimpleNamespace(id=80)

    class Client:
        _bot = True

        async def send_message(self, peer, text, **kwargs):
            seen.append(("root", peer, kwargs["reply_to"]))
            return status

    class Runtime:
        async def runtime_run(self, request):
            seen.append(request)
            return SimpleNamespace(
                content="<p>Answer</p>",
                error=None,
                ok=True,
                provider="mock",
                model="mock",
                tools_used=["mock_tool"],
            )

    async def edit(*args):
        seen.append(args[-1])

    from pyUltroid._core import richmsg

    monkeypatch.setattr(richmsg, "edit_rich", edit)
    monkeypatch.setattr(dm, "agent", lambda: Runtime())
    monkeypatch.setattr(dm, "report_run", lambda **kwargs: None)
    monkeypatch.setattr(
        dm.ai_controls,
        "get_controls",
        lambda key: {"tools": True, "rounds": 4, "model": "configured", "temp": 0.4},
    )
    monkeypatch.setattr(dm.ai_controls, "dm_tools_enabled", lambda user, controls: True)
    event = SimpleNamespace(client=Client(), chat_id=10, sender=None)
    asyncio.run(dm._run_session(event, 10, 22, "perform a task"))
    assert seen[0] == ("root", 10, 22)
    request = seen[1]
    assert request.chat_id == "dm:10:22"
    assert request.use_tools is True and request.max_rounds == 4
    assert request.model == "configured" and request.temperature == 0.4
    assert request.rich_messages_allowed is True
    assert isinstance(seen[-1], types.InputRichMessage)


def test_reply_to_result_continues_only_its_rooted_session(dm_module, monkeypatch):
    dm = dm_module
    seen = []
    session = {"anchor": 22}
    store = SimpleNamespace(
        session_find=lambda user, mid: session if (user, mid) == (10, 22) else None
    )
    monkeypatch.setattr(dm, "agent", lambda: store)

    async def run(event, user, anchor, prompt):
        seen.append((user, anchor, prompt))

    async def reply_message():
        return SimpleNamespace(id=80, reply_to=SimpleNamespace(reply_to_msg_id=22))

    monkeypatch.setattr(dm, "_run_session", run)
    event = SimpleNamespace(
        sender_id=10,
        reply_to_msg_id=80,
        raw_text="continue",
        get_reply_message=reply_message,
    )
    with pytest.raises(events.StopPropagation):
        asyncio.run(dm.dm_thread_handler(event))
    assert seen == [(10, 22, "continue")]
    assert dm._session_for_reply(11, SimpleNamespace(id=22)) is None


def test_copy_controls_preserve_code_indentation_and_underscores():
    from pyUltroid.agent._presentation import answer_plain, native_answer

    code = 'def foo_bar():\n    return "not_a_placeholder"'
    for answer in ('```python\n' + code + '\n```', '<pre>' + code + '</pre>'):
        assert code in answer_plain(answer)
        copied = ''.join(node.copy_text for node in _walk(native_answer(answer)) if isinstance(node, types.InlineButtonTypeCopy))
        assert code in copied

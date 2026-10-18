"""Execute the production wrapper error path without Telegram or runtime bootstrap."""
from __future__ import annotations

import ast
import asyncio
import html
import logging
import sys
from io import BytesIO
from types import ModuleType, SimpleNamespace

import pytest
from conftest import ROOT
from telethon import errors, events


def wrapper(monkeypatch, *, fail_logs=False, image=False, exception=None):
    source = ast.parse((ROOT / 'pyUltroid/_misc/_decorators.py').read_text())
    outer = next(node for node in source.body if isinstance(node, ast.FunctionDef) and node.name == 'ultroid_cmd')
    decor = next(node for node in outer.body if isinstance(node, ast.FunctionDef) and node.name == 'decor')
    body = next(node for node in decor.body if isinstance(node, ast.AsyncFunctionDef) and node.name == 'wrapp')
    calls = []
    traced = []

    class Trace:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            traced.append(self)

        def close(self, **kwargs):
            self.closed = kwargs

    async def record(trace):
        calls.append(('trace', trace))

    tracer = ModuleType('pyUltroid.tracer')
    tracer.TraceRecord = Trace
    tracer.tr = SimpleNamespace(live=SimpleNamespace(record=record))
    tracker = ModuleType('pyUltroid.tracer._tracker')
    tracker._set_trace = lambda trace: None
    monkeypatch.setitem(sys.modules, 'pyUltroid.tracer', tracer)
    monkeypatch.setitem(sys.modules, 'pyUltroid.tracer._tracker', tracker)

    class Assistant:
        async def send_file(self, peer, file, **kwargs):
            calls.append(('image' if image else 'file', kwargs))
            if fail_logs:
                raise RuntimeError('log transport failure')
            return SimpleNamespace(message_link='https://t.me/c/123/456')

        async def send_message(self, peer, text, **kwargs):
            calls.append(('log', text, kwargs))
            if fail_logs:
                raise RuntimeError('log transport failure')
            return SimpleNamespace(message_link='https://t.me/c/123/456')

    imaging = ModuleType('pyUltroid.imaging')
    imaging.Imaging = lambda: SimpleNamespace(error_card=lambda **kwargs: SimpleNamespace(ok=image, to_bytesio=lambda *args: BytesIO(b'mocked-image')))
    monkeypatch.setitem(sys.modules, 'pyUltroid.imaging', imaging)

    async def fail(event):
        raise exception or ValueError('<bad & input> API_KEY=never-publish-this')

    namespace = {
        '__package__': 'pyUltroid._misc',
        'dec': fail, 'pattern': 'broken', 'owner_only': False, 'groups_only': False,
        'admins_only': False, 'only_devs': False,
        'LOGS': logging.getLogger('command-error-test'), 'html': html,
        'asyncio': asyncio, 'BytesIO': BytesIO,
        'format_exc': __import__('traceback').format_exc,
        'asst': Assistant(), 'udB': SimpleNamespace(get_key=lambda key: -100123),
        'fmtmsg': lambda **kwargs: kwargs['msgs'], 'events': events,
        'DependencyMissingError': type('DependencyMissingError', (Exception,), {}),
    }
    for name in ('FloodWaitError', 'ChatSendInlineForbiddenError', 'ChatSendMediaForbiddenError', 'ChatSendStickersForbiddenError', 'BotMethodInvalidError', 'UserIsBotError', 'AlreadyInConversationError', 'BotInlineDisabledError', 'MessageIdInvalidError', 'MessageNotModifiedError', 'MessageDeleteForbiddenError', 'AuthKeyDuplicatedError'):
        namespace[name] = getattr(errors, name)
    exec(compile(ast.Module(body=[body], type_ignores=[]), 'production-command-wrapper', 'exec'), namespace)  # noqa: S102 - execute only the repository wrapper AST in a fully mocked namespace
    return namespace['wrapp'], calls, traced


class Outgoing:
    out = True
    chat = SimpleNamespace()
    is_private = False
    sender_id = 7
    chat_id = -10042
    text = ('.broken <arg> API_KEY=hidden-trigger-value', [])

    def __init__(self):
        self.sent = []

    async def eor(self, message, **kwargs):
        self.sent.append(('edit', message, kwargs))

    async def reply(self, message, **kwargs):
        self.sent.append(('reply', message, kwargs))


def run(function, event):
    async def wrapped():
        await function(event)
        await asyncio.sleep(0)
    asyncio.run(wrapped())


@pytest.mark.parametrize('fail_logs', [False, True])
def test_failure_edits_outgoing_even_when_logs_fail(monkeypatch, fail_logs):
    function, calls, traced = wrapper(monkeypatch, fail_logs=fail_logs)
    event = Outgoing()
    run(function, event)
    assert event.sent[0][0] == 'edit'
    message = event.sent[0][1]
    assert '.broken &lt;arg&gt;' in message
    assert '<pre>ValueError: &lt;bad &amp; input&gt;' in message
    assert '[see in the logs channel]' in message
    assert all('never-publish-this' not in str(call) for call in calls if call[0] != 'trace')
    assert 'hidden-trigger-value' not in repr(event.sent)
    assert 'never-publish-this' not in repr(event.sent)
    assert traced[0].closed['success'] is False
    assert event.sent[0][2] == {'link_preview': False, 'parse_mode': 'html'}


def test_image_error_caption_is_code_formatted_bounded_and_sanitized(monkeypatch):
    function, calls, _traced = wrapper(monkeypatch, image=True, exception=ValueError('<>&😀' * 500 + ' BOT_TOKEN=hidden'))
    event = Outgoing()
    run(function, event)
    caption = next(call[1]['caption'] for call in calls if call[0] == 'image')
    assert len(caption.encode('utf-16-le')) // 2 <= 1000
    assert '<pre>' in caption and caption.endswith('</pre>')
    assert 'hidden' not in caption
    assert len(event.sent) > 1
    recovered = ''.join(html.unescape(item[1].split('<pre>')[-1].split('</pre>')[0]) for item in event.sent)
    assert recovered == 'ValueError: ' + '<>&😀' * 500 + ' BOT_TOKEN=[REDACTED]'

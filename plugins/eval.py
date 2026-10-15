# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from . import get_help

__doc__ = get_help("help_devtools")

import inspect
import io
import json
import os
import sys
import textwrap
import time
import traceback
from html import escape
from io import BytesIO, StringIO
from pprint import pformat
from typing import Any, Dict, Optional, Tuple

import aiohttp
import telethon
from telethon import Button, functions, types
from telethon.tl.custom import Message
from telethon.utils import get_display_name

from pyUltroid import LOGS
from pyUltroid._core.notices import ErrorNotice, NoticeEdit, ParseMode

from custom.tg import ikm

from . import *

fn = functions  # shorthand alias in eval scope

# ── optional: black formatter ──────────────────────────────────────────────────
try:
    import black as _black
except ImportError:
    _black = None

# ── optional: tools.trace ─────────────────────────────────────────────────────
try:
    from pyUltroid.tools.trace import Tracer as _Tracer

    _tracer = _Tracer()
except Exception:
    _tracer = None

# ── optional: imaging for error snapshots ─────────────────────────────────────
try:
    from pyUltroid.tools.imaging import render_text as _img_render

    _HAS_IMAGING = True
except Exception:
    _HAS_IMAGING = False

# ── custom fmt & ikm ──────────────────────────────────────────────────────────
try:
    from custom.fmt import fmtmsg, fmtsec, fmtbyte, fmtbar

    _HAS_FMT = True
except Exception:
    _HAS_FMT = False

try:
    from custom.tg import ikm  # inline keyboard maker

    _HAS_IKM = True
except Exception:
    _HAS_IKM = False


#  Helper
def _usec() -> int:
    return int(time.time() * 1_000_000)


def _fmt_dur(t_us) -> str:
    t_us = int(t_us)
    t_ms = t_us / 1_000
    t_s = t_ms / 1_000
    t_m = t_s / 60
    t_h = t_m / 60
    t_d = t_h / 24
    if t_d >= 1:
        return "%dd %dh" % (t_d, t_h % 24)
    if t_h >= 1:
        return "%dh %dm" % (t_h, t_m % 60)
    if t_m >= 1:
        return "%dm %ds" % (t_m, t_s % 60)
    if t_s >= 1:
        return "%.2f sec" % t_s
    if t_ms >= 1:
        return "%d ms" % t_ms
    return "%d μs" % t_us


def _json_parse(data, indent=None):
    """Safely parse JSON string or dump dict/list."""
    try:
        if isinstance(data, str):
            parsed = json.loads(data)
            return (
                json.dumps(parsed, indent=indent or 2, ensure_ascii=False)
                if indent
                else parsed
            )
        elif isinstance(data, (dict, list)):
            return json.dumps(data, indent=indent or 2, ensure_ascii=False)
    except Exception:
        pass
    return data


def _parse_eval(value=None):
    """Pretty-print any return value: TLObject, dict, list, or str."""
    if value is None:
        return value
    if hasattr(value, "stringify"):
        try:
            return value.stringify()
        except TypeError:
            pass
    if isinstance(value, dict):
        try:
            return _json_parse(value, indent=2)
        except Exception:
            pass
    if isinstance(value, (list, tuple, set, frozenset)):
        parts = []
        for i, item in enumerate(list(value)):
            parts.append(f"  [{i}] {_parse_eval(item)}")
        return "[\n" + "\n".join(parts) + "\n]"
    if isinstance(value, bytes):
        return f"<bytes [{len(value)}B] {value[:32].hex()}{'...' if len(value) > 32 else ''}>"
    return str(value)


class _U:
    _ = ""


_u = _U()


def _stringify(text=None, *args, **kwargs):
    if text is not None:
        _u._ = text
        text = _parse_eval(text)
    return print(text, *args, **kwargs)


#  HTTP helper (available as `http` in eval scope)


class _HttpResp:
    __slots__ = ("_r", "_s")

    def __init__(self, r, s):
        self._r = r
        self._s = s

    async def text(self, **kw):
        try:
            return await self._r.text(**kw)
        finally:
            await self._s.close()

    async def json(self, **kw):
        try:
            return await self._r.json(**kw)
        finally:
            await self._s.close()

    async def content(self):
        try:
            return await self._r.read()
        finally:
            await self._s.close()

    async def close(self):
        await self._s.close()

    @property
    def status(self):
        return self._r.status

    @property
    def headers(self):
        return dict(self._r.headers)

    @property
    def url(self):
        return str(self._r.url)

    @property
    def cookies(self):
        return {k: v.value for k, v in self._r.cookies.items()}

    def __repr__(self):
        return f"<Response [{self.status}] {self.url}>"


class HttpHelper:
    async def request(self, method: str, url: str, **kw) -> _HttpResp:
        s = aiohttp.ClientSession()
        r = await s.request(method.upper(), url, **kw)
        return _HttpResp(r, s)

    def __getattr__(self, m: str):
        if m.lower() in ("get", "post", "put", "patch", "delete", "head", "options"):
            return lambda url, **kw: self.request(m, url, **kw)
        raise AttributeError(f"HttpHelper: no method '{m}'")


#  paste.rs fallback
async def _paste(text: str) -> str | None:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.post("https://paste.rs", data=text.encode()) as r:
                r.raise_for_status()
                return (await r.text()).strip()
    except Exception:
        return None


#  Core exec wrapper
async def _aexec(code: str, event: Message, extra_vars: dict):
    """
    Build an async wrapper around `code`, inject variables, and exec it.
    Returns the return value of the last expression.
    """
    wrapper = (
        "async def __aexec__(e, client, **_kw):\n"
        " print = p = _stringify\n"
        " message = event = e\n"
        " u_reply = reply = await e.get_reply_message()\n"
        " chat = e.chat_id\n"
    )
    for line in code.split("\n"):
        wrapper += f" {line}\n"

    globs = {
        "_stringify": _stringify,
        **extra_vars,
    }
    exec(wrapper, globs)  # noqa: S102
    # Remove client/c from extra_vars to avoid "multiple values for argument 'client'"
    extra_vars = {k: v for k, v in extra_vars.items() if k not in ("client", "c")}
    return await globs["__aexec__"](event, event.client, **extra_vars)


#  Error-notice helpers
async def _edit_notice(
    target: Message,
    event: Message,
    notice: ErrorNotice,
    dur_str: str,
) -> None:
    """Replace the eval placeholder with a short, actionable error notice.

    Uses :class:`NoticeEdit` so the update happens in place. A failed edit is
    reported rather than silently converted into a second message.
    """
    text = notice.chat_text() + f"\n\n**>Eval ({dur_str}) — see logs for the full traceback**"
    handle = NoticeEdit(target, parse_mode=ParseMode.V2)
    if not await handle.try_edit(text):
        LOGS.warning("[eval] could not edit the notice in place")
        # Last resort: the placeholder stays, but the user is told.
        try:
            await event.reply(notice.chat_text())
        except Exception:  # noqa: BLE001
            pass


async def _report_to_log_channel(
    event: Message, code: str, notice: ErrorNotice
) -> None:
    """Send the full traceback to the log channel only.

    This is the only path that carries the whole traceback. It must never
    also post it to the chat - that was the original defect.
    """
    log_chat = udB.get_key("LOG_CHANNEL")
    if not log_chat:
        return
    try:
        chat_name = get_display_name(event.chat) if event.chat else event.chat_id
    except Exception:  # noqa: BLE001
        chat_name = event.chat_id

    body = [
        f"<b>❌ Eval Error</b> <code>({escape(str(notice.severity.value))})</code>",
        f"<b>Chat:</b> <code>{escape(str(chat_name))}</code>",
        f"<b>Code:</b>\n<pre language='python'>{escape(code[:1000])}</pre>",
        notice.log_text(),
    ]
    try:
        await event.client.send_message(
            log_chat, "\n\n".join(body), parse_mode=ParseMode.HTML.value
        )
    except Exception:  # noqa: BLE001 - logging must never break the handler
        pass


#  Output renderer
async def _render_output(
    event: Message,
    xx: Message | None,
    code: str,
    evaluation: str,
    dur_str: str,
    is_err: bool,
    mode: str,
):
    """
    Render eval output. Tries inline edit first, then paste.rs, then file upload.

    On failure the user gets a short, actionable notice *in the message they
    triggered*, never the whole traceback. The full traceback is reserved for
    the log channel - posting it to the chat as a generated card left the user
    with only "[An error occurred]".
    """
    target = xx or event

    if is_err:
        notice = ErrorNotice.from_traceback(evaluation, what="evaluate that")
        if notice.is_user_error:
            # Malformed input the user can fix: keep it in chat, short.
            return await _edit_notice(target, event, notice, dur_str)
        # Environment/unknown failure: chat gets a summary, logs get the rest.
        await _report_to_log_channel(event, code, notice)
        return await _edit_notice(target, event, notice, dur_str)

    icon = "✅"
    header = (
        f"<b>{icon} Eval</b>  <code>({dur_str})</code>\n"
        f"<b>In:</b>\n<pre language='python'>{escape(code[:512])}{'...' if len(code) > 512 else ''}</pre>\n"
        f"<b>Out:</b>"
    )
    body = escape(evaluation)
    full = f"{header}\n<pre language='python'>{body}</pre>"

    if len(full) <= 4096:
        try:
            await target.edit(full, parse_mode="html")
        except Exception:
            await event.reply(full, parse_mode="html")
        return

    # Too long → paste.rs
    url = await _paste(f"In:\n{code}\n\nOut:\n{evaluation}")
    if url:
        snip = escape(evaluation[:1500] + ("\n..." if len(evaluation) > 1500 else ""))
        text = (
            f"{header}\n"
            f"<pre language='python'>{snip}</pre>\n"
            f"<blockquote><a href='{url}'><b>Full output →</b></a></blockquote>"
        )
        try:
            await target.edit(text, parse_mode="html")
        except Exception:
            await event.reply(text, parse_mode="html")
        return

    # paste.rs failed → file upload
    buf = BytesIO(f"In:\n{code}\n\nOut:\n{evaluation}".encode())
    buf.name = "eval.txt"
    summary = f"<b>Eval</b> <code>({dur_str})</code> — output attached"
    reply_to = event.reply_to_msg_id or event.id
    try:
        await event.client.send_file(
            event.chat_id,
            buf,
            force_document=True,
            caption=summary,
            parse_mode="html",
            reply_to=reply_to,
        )
        await target.delete()
    except Exception:
        pass


#  Main eval command
pp = pformat
bot = ultroid = ultroid_bot


@ultroid_cmd(pattern="eval", fullsudo=True, only_devs=True)
async def _cmd_eval(event: Message):
    try:
        raw = event.text.split(maxsplit=1)[1]
    except IndexError:
        return await event.eor(get_string("devs_2"), time=5)

    mode = ""
    first = raw.split()[0]
    if first in ("-s", "--silent"):
        mode = "silent"
        raw = raw.split(maxsplit=1)[1] if len(raw.split()) > 1 else ""
    elif first in ("-n", "--noedit"):
        mode = "noedit"
        raw = raw.split(maxsplit=1)[1] if len(raw.split()) > 1 else ""
    elif first in ("-gs", "--source"):
        mode = "gsource"
        raw = raw.split(maxsplit=1)[1] if len(raw.split()) > 1 else ""
    elif first in ("-ga", "--args"):
        mode = "gargs"
        raw = raw.split(maxsplit=1)[1] if len(raw.split()) > 1 else ""

    code = raw
    if not code:
        return await event.eor(get_string("devs_2"), time=5)

    if _black:
        try:
            code = _black.format_str(code, mode=_black.Mode())
        except Exception:
            pass

    xx = None

    if mode == "silent":
        await event.delete()
    elif mode == "noedit":
        xx = await event.reply(get_string("com_1"))
    else:
        try:
            xx = await event.eor("<b>⏳ Running eval</b>", parse_mode="html")
        except Exception:
            xx = await event.reply("<b>⏳ Running eval</b>", parse_mode="html")

    reply = await event.get_reply_message()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    redirected_out = sys.stdout = StringIO()
    redirected_err = sys.stderr = StringIO()

    extra_vars: dict[str, Any] = {
        # Telegram
        "client": event.client,
        "c": event.client,
        "bot": ultroid_bot,
        "asst": asst,
        "m": event,
        "msg": event,
        "message": event,
        "r": reply,
        "reply": reply,
        "chat": event.chat_id,
        "u": event.sender,
        # Internals
        "db": udB,
        "udB": udB,
        "LOGS": LOGS,
        "logs": LOGS,
        "fn": functions,
        "func": functions,
        "tl": telethon,
        "types": types,
        "events": telethon.events,
        "errors": telethon.errors,
        "utils": telethon.utils,
        "util": telethon.utils,
        # Utilities
        "http": HttpHelper(),
        "inspect": inspect,
        "libs": inspect.getsource,
        "pp": pformat,
        "json_parse": _json_parse,
        "parse_eval": _parse_eval,
        "traceback": traceback,
        "os": os,
        "sys": sys,
        # fmt & ikm
        **(
            {
                "fmt": type(
                    "fmt",
                    (),
                    {"bar": fmtbar, "sec": fmtsec, "byte": fmtbyte, "msg": fmtmsg},
                )()
            }
            if _HAS_FMT
            else {}
        ),
        **({"ikm": ikm} if _HAS_IKM else {}),
        # Tracer
        **({"tracer": _tracer} if _tracer else {}),
    }

    exc = stdout = stderr = value = None
    t0 = _usec()
    try:
        value = await _aexec(code, event, extra_vars)
    except Exception:
        exc = traceback.format_exc()

    elapsed = _usec() - t0
    sys.stdout = old_stdout
    sys.stderr = old_stderr
    stdout = redirected_out.getvalue()
    stderr = redirected_err.getvalue()

    if value and mode == "gsource":
        try:
            exc = inspect.getsource(value)
        except Exception:
            exc = traceback.format_exc()
    elif value and mode == "gargs":
        try:
            args = inspect.signature(value).parameters.values()
            name = getattr(value, "__name__", "")
            exc = f"**{name}**\n\n" + "\n ".join(str(a) for a in args)
        except Exception:
            exc = traceback.format_exc()

    evaluation = exc or stderr or stdout or _parse_eval(value) or get_string("instu_4")
    dur_str = _fmt_dur(elapsed)
    is_err = bool(exc)

    if mode == "silent":
        if is_err:
            # Silent mode by definition says nothing in chat; the log channel
            # is the correct destination for the traceback.
            await _report_to_log_channel(
                event,
                code,
                ErrorNotice.from_traceback(evaluation, what="evaluate that"),
            )
            if _tracer:
                _tracer.capture_exception(exc=evaluation, context={"code": code})
        return

    if is_err:
        LOGS.warning("[eval] error in msg %s:\n%s", event.id, evaluation[:500])
        if _tracer:
            _tracer.capture_exception(exc=evaluation, context={"code": code})

    await _render_output(event, xx, code, evaluation, dur_str, is_err, mode)

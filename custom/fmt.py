import datetime
import html
import math
import sys
import time as _time
import traceback

from telethon.custom import Message
from telethon.extensions import html as _telethon_html

from custom.entities import normalize_parsed


class FmtMessage(str):
    """String-compatible formatter result sent through Layer 230 as HTML."""

    __slots__ = ()
    __rich_format__ = "html"


class FmtKit:
    """Formatted-message builders (bars, bytes, exception blocks).

    One instance (``fmt_kit``); the module-level functions below are
    backward-compatible aliases.
    """

    def fmtbar(
        self,
        current: int,
        total: int,
        bars: int = 10,
        empty: str = "-",
        fill: str = "#",
    ) -> str:
        bars = int(bars)
        if bars < 1:
            raise ValueError("bars must be at least 1")
        current, total = float(current), float(total)
        if not math.isfinite(current) or not math.isfinite(total) or total < 0:
            raise ValueError("current and total must be finite; total cannot be negative")
        ratio = min(1.0, max(0.0, current / total if total else 0.0))
        filled = round(ratio * bars)
        percent = f"{ratio * 100:.2f}".rstrip("0").rstrip(".")
        return f"[ {fill * filled + empty * (bars - filled)} ] {percent}%"

    def fmtbyte(self, byte: int | float, si: bool = False) -> str:
        byte = float(byte)
        if not math.isfinite(byte):
            raise ValueError("byte count must be finite")
        sign = "-" if byte < 0 else ""
        byte = abs(byte)
        if si:
            units = (
                ("TB", 1000**4),
                ("GB", 1000**3),
                ("MB", 1000**2),
                ("KB", 1000),
                ("B", 1),
            )
        else:
            units = (
                ("TiB", 1024**4),
                ("GiB", 1024**3),
                ("MiB", 1024**2),
                ("KiB", 1024),
                ("B", 1),
            )

        for unit, factor in units:
            if byte >= factor or factor == 1:
                value = f"{(byte / factor):.2f}".rstrip("0").rstrip(".")
                return f"{sign}{value} {unit}"

    def fmtexc(self) -> str:
        exc = traceback.TracebackException(*sys.exc_info())
        fmt = exc.exc_type.__name__
        if exc._str:
            fmt += f":\n  {exc._str}"

        ftb = traceback.format_list(
            f for f in exc.stack if "/site-packages/" in f.filename
        )
        if ftb:
            fmt += f"\n\nTraceback:\n{''.join(ftb)}"

        return fmt

    def _fmt_value(self, v) -> str:
        if isinstance(v, rawhtml):
            return str(v)
        if isinstance(v, (tuple, list)) and len(v) == 2:
            text, entities = normalize_parsed(v)
            return _telethon_html.unparse(text, entities)
        return f"<code>{html.escape(str(v))}</code>"

    def _escape_or_raw(self, value) -> str:
        if isinstance(value, rawhtml):
            return str(value)
        if isinstance(value, (tuple, list)) and len(value) == 2:
            text, entities = normalize_parsed(value)
            return _telethon_html.unparse(text, entities)
        return html.escape(str(value))

    def fmtmsg(
        self, head: str, data: object = None, foot: str = "", msgs: str = ""
    ) -> str:
        body = ""
        if isinstance(data, dict):
            padd = max((len(str(k)) for k in data.keys()), default=0)
            body = "\n".join(
                f"  <code>{html.escape(str(k)).ljust(padd)}</code> : {self._fmt_value(v)}"
                for k, v in data.items()
            )
        elif isinstance(data, (list, set, tuple)):
            body = "\n".join(
                f"  <code>{n}</code>. {self._fmt_value(item)}"
                for n, item in enumerate(data, start=1)
            )
        elif data:
            body = f"  <code>{html.escape(str(data))}</code>"

        text = [f"<b>{self._escape_or_raw(head)}</b>"]
        if body:
            text.append(body)

        if msgs:
            text.append(f"<blockquote expandable>{self._escape_or_raw(msgs)}</blockquote>")

        if foot:
            text.append(f"<b><blockquote>{self._escape_or_raw(foot)}</blockquote></b>")

        return FmtMessage("\n\n".join(text))

    def fmtsec(self, sec: object, part: int = 3, human: bool = False) -> str:
        if isinstance(sec, datetime.timedelta):
            total_seconds = sec.total_seconds()
        elif isinstance(sec, datetime.datetime):
            total_seconds = (
                datetime.datetime.now(datetime.UTC) - sec.astimezone(datetime.UTC)
            ).total_seconds()
        elif isinstance(sec, (float, int)):
            total_seconds = float(sec)
        else:
            raise TypeError("sec must be a timedelta, datetime, or number")

        if not math.isfinite(total_seconds):
            raise ValueError("duration must be finite")
        part = int(part)
        if part < 1:
            raise ValueError("part must be at least 1")
        negative = total_seconds < 0
        total_microseconds = round(abs(total_seconds) * 1_000_000)
        total, micro = divmod(total_microseconds, 1_000_000)
        units = (
            ("week", 60**2 * 24 * 7),
            ("day", 60**2 * 24),
            ("hour", 60**2),
            ("minute", 60),
            ("second", 1),
        )
        parts = []
        for unit, second in units:
            value, total = divmod(total, second)
            if value:
                parts.append(f"{value} {unit}{'' if value == 1 else 's'}")

            if len(parts) >= part:
                break

        if len(parts) < part and not human:
            ms, us = divmod(micro, 1000)
            if ms:
                parts.append(f"{ms} ms")

            if us and len(parts) < part:
                parts.append(f"{us} µs")

        result = ", ".join(parts) if parts else "0 seconds"
        return f"-{result}" if negative else result

    async def prog(
        self, current: int, total: int, event: Message, title: str = ""
    ) -> None:
        try:
            now = event._client.loop.time()
        except (AttributeError, Exception):
            now = _time.time()

        if not hasattr(event, "_start"):
            event._start = now
            event._last = now
            return

        if now - event._last >= 2.5:
            delta = now - event._start
            speed = current / delta if delta > 0 else 0
            text_to_send = fmtmsg(
                f"{title.title()} Progress".lstrip(),
                {
                    "Current": fmtbyte(current),
                    "Total": f"{fmtbyte(total)}\n",
                    "Speed": f"{fmtbyte(speed)}/s\n",
                    "Elapsed": fmtsec(delta, human=True),
                    "Estimated": fmtsec(
                        (total - current) / speed if speed > 0 else 0, human=True
                    ),
                },
                fmtbar(current, total),
            )

            try:
                await event.edit(text_to_send, parse_mode="html")
            except Exception:
                pass

            event._last = now


fmt_kit = FmtKit()


def fmtbar(*args, **kwargs):
    return fmt_kit.fmtbar(*args, **kwargs)


def fmtbyte(*args, **kwargs):
    return fmt_kit.fmtbyte(*args, **kwargs)


def fmtexc(*args, **kwargs):
    return fmt_kit.fmtexc(*args, **kwargs)


def _fmt_value(*args, **kwargs):
    return fmt_kit._fmt_value(*args, **kwargs)


def fmtmsg(*args, **kwargs):
    return fmt_kit.fmtmsg(*args, **kwargs)


def fmtsec(*args, **kwargs):
    return fmt_kit.fmtsec(*args, **kwargs)


def prog(*args, **kwargs):
    return fmt_kit.prog(*args, **kwargs)


class rawhtml(str):
    """Marks a string as pre-built, already-safe HTML.

    ``fmtmsg`` renders ``rawhtml`` values verbatim (no escaping, no
    ``<code>`` wrap), so callers can pass working ``<a href>`` links inside
    ``data`` - plain values keep the escaped ``<code>`` treatment.
    """

import datetime
import html
import sys
import time as _time
import traceback

from telethon.custom import Message


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
        empty: str = chr(9633),
        fill: str = chr(9635),
    ) -> str:
        fillbar = round(current / total * bars)
        percent = f"{(current / total * 100):.2f}".rstrip("0").rstrip(".")
        return f"[ {fill * fillbar + empty * (bars - fillbar)} ] {percent}%"

    def fmtbyte(self, byte: int, si: bool = False) -> str:
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
            if byte >= factor:
                value = f"{(byte / factor):.2f}".rstrip("0").rstrip(".")
                return f"{value} {unit}"

        return "-"

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
        return f"<code>{html.escape(str(v))}</code>"

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

        text = [f"<b>{head}</b>"]
        if body:
            text.append(body)

        if msgs:
            text.append(f"<blockquote expandable>{html.escape(str(msgs))}</blockquote>")

        if foot:
            text.append(f"<b><blockquote>{html.escape(str(foot))}</blockquote></b>")

        return "\n\n".join(text)

    def fmtsec(self, sec: object, part: int = 3, human: bool = False) -> str:
        if isinstance(sec, datetime.timedelta):
            delta = sec
        elif isinstance(sec, datetime.datetime):
            delta = datetime.datetime.now(datetime.UTC) - sec.astimezone(datetime.UTC)
        elif isinstance(sec, (float, int)):
            delta = datetime.timedelta(seconds=sec)
        else:
            raise TypeError

        total = int(delta.total_seconds())
        micro = delta.microseconds
        units = (
            ("Week", 60**2 * 24 * 7),
            ("Day", 60**2 * 24),
            ("Hour", 60**2),
            ("Minute", 60),
            ("Second", 1),
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

        return ", ".join(parts) if parts else "-"

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
                await event.edit(text_to_send, parse_mode="v2")
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

"""Best-effort reporting of agent activity to the log channel.

Every report is fire-and-forget: a failing log channel must never break the
command that produced the activity being logged. All sending happens in a
background task, and any error is swallowed after being written to the process
log.

The reports use the :class:`~pyUltroid._core.richmsg.RichBlocks` model so the
log channel gets the same native rich rendering as the rest of the bot, with a
plain-text fallback for clients that cannot render it.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

__all__ = ("report_run",)

_REPORT_TIMEOUT = 10


def _log_channel() -> Any | None:
    try:
        from pyUltroid import udB

        if udB:
            return udB.get_key("LOG_CHANNEL")
    except Exception:
        pass
    return None


def _client() -> Any | None:
    """Any connected client, assistant preferred: it always exists."""
    try:
        from pyUltroid import asst, ultroid_bot

        if getattr(asst, "is_connected", lambda: False)():
            return asst
        if ultroid_bot and getattr(ultroid_bot, "is_connected", lambda: False)():
            return ultroid_bot
    except Exception:
        pass
    return None


async def _send_rich(
    client: Any, channel: Any, blocks: Any, fallback_text: str
) -> None:
    """Send *blocks* through richmsg; plain text if that fails entirely.

    ``send_rich`` already carries its own legacy-engine fallback; the plain
    send here only covers the case where even that raises, e.g. a client
    without the rich machinery installed.
    """
    try:
        from pyUltroid._core.richmsg import send_rich

        result = await send_rich(client, channel, blocks)
        if result is not None:
            return
    except Exception:
        pass
    await client.send_message(channel, fallback_text)


def _run_blocks(header: str, lines: list) -> Any:
    from pyUltroid._core.richmsg import RichBlocks

    blocks = RichBlocks().header(header, level=3)
    for label, value in lines:
        blocks.paragraph(f"**{label}:** {value}")
    return blocks


def _fallback_text(header: str, lines: list) -> str:
    body = "\n".join(f"{label}: {value}" for label, value in lines)
    return f"{header}\n{body}"


async def _report(header: str, lines: list) -> None:
    channel = _log_channel()
    client = _client()
    if not channel or client is None:
        return
    await _send_rich(
        client, channel, _run_blocks(header, lines), _fallback_text(header, lines)
    )


async def _capped_report(header: str, lines: list) -> None:
    """One report, abandoned if it cannot finish in time.

    The timeout bounds the background task: without it, a hung log-channel
    send would leave one task pending per agent run. ``wait_for`` cancels the
    send on expiry, and the done-callback in ``report_run`` swallows the
    ``TimeoutError`` like any other failure.
    """
    await asyncio.wait_for(_report(header, lines), timeout=_REPORT_TIMEOUT)


def report_run(
    *,
    origin: str,
    chat_id: Any,
    user: str | None,
    provider: str | None,
    model: str | None,
    prompt: str,
    tools_used: list | None = None,
    duration: float | None = None,
    ok: bool = True,
    error: str | None = None,
) -> asyncio.Task | None:
    """Log one agent run to the log channel; never blocks, never raises.

    *origin* is the command or surface that produced the run (``.ai``,
    ``.agent``, a DM session, ...). Returns the background task, mainly for
    tests.
    """
    try:
        if ok:
            return None
        channel = _log_channel()
        if not channel:
            return None

        status = "failed"
        header = f"Agent run · {origin} · {status}"
        lines = [
            ("Chat", chat_id),
            ("User", user or " - "),
            ("Provider", provider or " - "),
            ("Model", model or " - "),
            (
                "Tools",
                ", ".join(tools_used) if tools_used else "none",
            ),
            (
                "Duration",
                f"{duration:.1f}s" if duration is not None else " - ",
            ),
        ]
        if error:
            lines.append(("Error", "The agent run failed; details are withheld."))

        task = asyncio.ensure_future(_capped_report(header, lines))

        def _swallow(fut: asyncio.Task) -> None:
            try:
                fut.result()
            except Exception:
                # The log channel is diagnostics, not a dependency.
                pass

        task.add_done_callback(_swallow)
        return task
    except Exception:
        return None

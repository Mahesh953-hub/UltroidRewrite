"""Small compatibility guards for the pinned Telethon fork.

The fork's dialog iterator currently cannot cast Telegram ``Community`` TL
objects. Message sender resolution may fall back to that iterator and raise;
callers should keep using cached senders and skip only when that lookup path
is unavailable, rather than letting an update escape as an unhandled error.
"""

from __future__ import annotations

import re
from typing import Any


_RETRY_EXHAUSTED_RE = re.compile(r"Request was unsuccessful \d+ time\(s\)")


def is_telethon_dialog_resolution_error(error: BaseException) -> bool:
    """Return whether *error* matches the pinned fork's broken lookup paths."""
    if isinstance(error, TypeError):
        return str(error).startswith("Cannot cast Community to any kind of ")
    return isinstance(error, ValueError) and bool(
        _RETRY_EXHAUSTED_RE.fullmatch(str(error))
    )


async def get_sender_if_resolvable(event: Any) -> Any | None:
    """Use the cached Telethon sender first; contain known dialog-scan failures."""
    sender = getattr(event, "sender", None)
    if sender is not None:
        return sender

    try:
        return await event.get_sender()
    except (TypeError, ValueError) as exc:
        if is_telethon_dialog_resolution_error(exc):
            return None
        raise

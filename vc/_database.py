#  Copyright (c) 2025 AshokShau
#  Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
#  Part of the TgMusicBot project. All rights reserved where applicable.

"""VC persistence helpers built on top of pyUltroid's ``udB``.

All VC-related keys are namespaced with the ``vc:`` prefix so they never
collide with the rest of the Ultroid database. The module exports a single
``db`` object used across the VC package.
"""

from typing import Optional

try:
    from pyUltroid import udB
except ImportError:  # pragma: no cover - pyUltroid is the runtime dependency
    udB = None

_ASSISTANT_PREFIX = "vc:assistant:"
_CHANNEL_PREFIX = "vc:channel:"


class VcDatabase:
    """Thin async wrapper around pyUltroid's ``udB`` for VC persistence."""

    def __init__(self, database=None):
        # Fall back to the shared pyUltroid database when none is provided.
        self._db = database if database is not None else udB

    async def get_assistant(self, chat_id: int) -> str | None:
        """Return the assistant client name assigned to ``chat_id`` (or None)."""
        if self._db is None:
            return None
        return self._db.get_key(f"{_ASSISTANT_PREFIX}{chat_id}")

    async def set_assistant(self, chat_id: int, assistant: str) -> bool:
        """Persist the assistant client name used for ``chat_id``."""
        if self._db is None:
            return False
        return self._db.set_key(f"{_ASSISTANT_PREFIX}{chat_id}", assistant)

    async def get_chat_id_by_channel(self, chat_id: int) -> int | None:
        """Resolve the originating chat id for a linked channel."""
        if self._db is None:
            return None
        value = self._db.get_key(f"{_CHANNEL_PREFIX}{chat_id}")
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None


db = VcDatabase()

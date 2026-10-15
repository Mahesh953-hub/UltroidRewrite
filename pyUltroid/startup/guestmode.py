"""Guest-mode storage, now a class on the dB layer.

The verbs moved to :class:`pyUltroid.dB.guest_db.GuestDB`; the module-level
functions are backward-compatible aliases for the plugin surface, plus the
startup-only background cleanup loop.
"""

import time
import asyncio
import logging

LOGS = logging.getLogger("pyUltLogs")


def _store():
    # Lazy on purpose: the dB layer reads udB at import time, and this module
    # must stay importable before the bootstrap finishes.
    from ..dB.guest_db import guest_db

    return guest_db


def get_guests() -> dict:
    """Retrieve all guest sessions from the database."""
    return _store().get_guests()


def add_guest(user_id: int, expiry_seconds: int = None, scope: str = "all"):
    """Add a guest user session with optional expiry time in seconds."""
    _store().add_guest(user_id, expiry_seconds, scope)
    LOGS.info(f"Added guest session for user {user_id} with expiry={expiry_seconds}s")


def remove_guest(user_id: int) -> bool:
    """Revoke guest access for a user."""
    removed = _store().remove_guest(user_id)
    if removed:
        LOGS.info(f"Removed guest session for user {user_id}")
    return removed


def is_guest(user_id: int) -> bool:
    """Check if a user is an active, unexpired guest."""
    return _store().is_guest(user_id)


async def clean_expired_guests_loop():
    """Background task to periodically expire and clean guest sessions."""
    LOGS.info("Starting GuestMode background cleanup loop...")
    while True:
        try:
            for user_id in _store().clean_expired():
                LOGS.info(f"Automatically expired guest session for user {user_id}")
        except Exception as e:
            LOGS.error(f"Error in clean_expired_guests_loop: {e}")
        await asyncio.sleep(10)

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""Python-3.14-safe event-loop helpers with uvloop fallback."""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from typing import Any
from collections.abc import Generator

_uvloop_policy = None


def _is_uvloop_policy(policy: Any) -> bool:
    """Return True if *policy* is a uvloop event-loop policy."""
    try:
        import uvloop
    except ImportError:
        return False
    return isinstance(policy, uvloop.EventLoopPolicy)


def install_uvloop_policy() -> None:
    global _uvloop_policy
    try:
        import uvloop
    except ImportError:
        return

    # Changing the policy while a loop is running is an error.  If we are
    # already inside an async context, skip the install and use whatever loop
    # is running.
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return

    try:
        current = asyncio.get_event_loop_policy()
    except AttributeError:
        # Python 3.14+ no longer exposes the policy API.
        return

    if _is_uvloop_policy(current):
        return

    try:
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
    except AttributeError:
        # Python 3.14+ may also remove set_event_loop_policy.
        return
    _uvloop_policy = asyncio.get_event_loop_policy()


def get_event_loop() -> asyncio.AbstractEventLoop:
    """Return the current loop if one is running, else create a fresh one.

    Idempotent: if a loop is already associated with the current thread it is
    reused.  Otherwise a new loop is created with ``asyncio.new_event_loop``
    and set as the current thread's loop.
    """
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        pass

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    except AttributeError:
        # Python 3.14+ without get_event_loop: create and set explicitly.
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    return loop


def ensure_running_loop() -> asyncio.AbstractEventLoop:
    """Like :func:`get_event_loop`, but also create one in the main thread.

    This is the safe replacement for the old
    ``asyncio.get_event_loop_policy().get_event_loop()`` idiom used at startup.
    """
    install_uvloop_policy()
    return get_event_loop()


@contextmanager
def temporary_loop() -> Generator[asyncio.AbstractEventLoop]:
    """Context manager that yields a fresh loop and closes it on exit.

    Useful for running small async snippets in synchronous contexts without
    polluting the thread's default loop.
    """
    loop = asyncio.new_event_loop()
    try:
        yield loop
    finally:
        try:
            loop.run_until_complete(loop.shutdown_asyncgens())
        except Exception:
            pass
        loop.close()


def run_sync(coro: Any) -> Any:
    """Run a coroutine on a usable loop in a thread-safe way."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    if threading.current_thread() is threading.main_thread():
        return loop.run_until_complete(coro)
    return asyncio.run_coroutine_threadsafe(coro, loop).result()

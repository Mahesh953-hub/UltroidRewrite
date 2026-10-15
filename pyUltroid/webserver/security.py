# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""Security primitives for the setup server.

Three deliberately unrelated concerns, three unrelated classes - they share
no base class and no method vocabulary on purpose:

* :class:`SecretRedactor` scrubs known secrets out of arbitrary text.
* :class:`RateLimiter` is a sliding-window counter keyed by caller.
* :class:`TokenAuthority` is the only minting/verifying authority; it owns the
  process-wide :data:`REDACTOR` so a caller cannot wire the wrong one in.
"""

from __future__ import annotations

import hmac
import secrets
import threading
import time
from typing import Any, Final, Protocol

TOKEN_HEADER: Final[str] = "X-Setup-Token"
TOKEN_QUERY_PARAM: Final[str] = "token"
REDACTED: Final[str] = "**********"

TOKEN_BYTES: Final[int] = 24


class SecretRedactor:
    """Longest-first substring scrubber for a set of known secret values.

    Values shorter than :attr:`MIN_LENGTH` are refused: scrubbing a 3-char
    string would mangle unrelated text far more often than it would help.
    """

    MIN_LENGTH: Final[int] = 4

    __slots__ = ("_secrets", "_lock")

    def __init__(self) -> None:
        self._secrets: set[str] = set()
        self._lock = threading.Lock()

    def add(self, value: Any) -> bool:
        """Register *value* for scrubbing. Returns whether it is now tracked."""
        if value is None:
            return False
        text = str(value)
        if len(text) < self.MIN_LENGTH:
            return False
        with self._lock:
            self._secrets.add(text)
            return True

    def discard(self, value: Any) -> None:
        with self._lock:
            self._secrets.discard(str(value))

    def clear(self) -> None:
        with self._lock:
            self._secrets.clear()

    def redact(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            ordered = sorted(self._secrets, key=len, reverse=True)
        for secret in ordered:
            if secret in text:
                text = text.replace(secret, REDACTED)
        return text

    def __len__(self) -> int:
        with self._lock:
            return len(self._secrets)

    def __repr__(self) -> str:  # never leak the values themselves
        return f"<SecretRedactor tracked={len(self)}>"


class RateLimiter:
    """Sliding-window attempt counter.

    Unlike the redactor this one is *stateful per key* and prunes idle keys
    so a long-lived server does not accumulate a dict entry per IP forever.
    """

    __slots__ = ("max_attempts", "window_seconds", "_hits", "_lock")

    def __init__(
        self, max_attempts: int = 5, window_seconds: float = 60.0
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            hits = self._hits.get(key, [])
            if len(hits) >= self.max_attempts:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def remaining(self, key: str) -> int:
        """Attempts left for *key* inside the current window."""
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            hits = self._hits.get(key, [])
            return max(0, self.max_attempts - len(hits))

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def _expire(self, now: float) -> None:
        """Drop timestamps past the window, and whole keys with none left.

        Caller holds the lock. Called on every check, so the dict stays
        proportional to *active* callers rather than to total callers seen.
        """
        for key in list(self._hits):
            fresh = [ts for ts in self._hits[key] if now - ts < self.window_seconds]
            if fresh:
                self._hits[key] = fresh
            else:
                del self._hits[key]


class _RequestLike(Protocol):
    """The slice of a Starlette request the token gate needs."""

    headers: Any
    query_params: Any


class TokenAuthority:
    """Mints, reads back and constant-time-verifies the setup token.

    This is the single owner of the shared :data:`REDACTOR`: anything that
    needs to scrub must ask the authority it already has, so the setup app
    and the Mini App can never end up pointing at two different redactors.
    """

    __slots__ = ("_redactor",)

    def __init__(self, redactor: SecretRedactor | None = None) -> None:
        self._redactor = redactor if redactor is not None else SecretRedactor()

    # -- state
    @property
    def redactor(self) -> SecretRedactor:
        return self._redactor

    # -- minting
    def mint(self, *, track: bool = True) -> str:
        """Return a fresh URL-safe token, registered for redaction."""
        token = secrets.token_urlsafe(TOKEN_BYTES)
        if track:
            self._redactor.add(token)
        return token

    # -- reading
    def pull(self, request: _RequestLike) -> str | None:
        """Header first, then query string (the UI links carry ``?token=``)."""
        return request.headers.get(TOKEN_HEADER) or request.query_params.get(
            TOKEN_QUERY_PARAM
        )

    # -- verifying
    def verify(self, provided: str | None, expected: str) -> bool:
        """Constant-time comparison that also rejects empty inputs."""
        if not provided or not expected:
            return False
        return hmac.compare_digest(str(provided), str(expected))

    def permits(self, request: _RequestLike, expected: str) -> bool:
        """Convenience: pull then verify in one call."""
        return self.verify(self.pull(request), expected)

    def __repr__(self) -> str:
        return "<TokenAuthority tracked=%d>" % len(self._redactor)


AUTHORITY: Final[TokenAuthority] = TokenAuthority()
REDACTOR: Final[SecretRedactor] = AUTHORITY.redactor


# -- backward-compatible module surface (the plugin + server.py contract) ---


def generate_setup_token() -> str:
    return AUTHORITY.mint()


def extract_token(request) -> str | None:
    return AUTHORITY.pull(request)


def token_matches(provided: str | None, expected: str) -> bool:
    return AUTHORITY.verify(provided, expected)
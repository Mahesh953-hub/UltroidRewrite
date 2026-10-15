# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Shared welcome/farewell renderer, inline-result toolkit, and inline feedback.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import inspect
import time
from typing import Any, Dict, List, Optional
from collections.abc import Sequence

from telethon import utils

from ..startup.logger import LOGS

__all__ = (
    "DEFAULT_WELCOME",
    "DEFAULT_FAREWELL",
    "render_welcome",
    "render_farewell",
    "InlineResultBuilder",
    "InlineResultCache",
    "build_menu_grid",
    "build_menu_results",
)

DEFAULT_WELCOME = (
    "👋 Welcome {mention} to **{chat}**!\n"
    "You are member **#{count}**. Please read the rules and enjoy your stay."
)
DEFAULT_FAREWELL = "👋 Goodbye {name}, **{chat}** will miss you!"

#: Telegram caps inline answers at 50 results per response.
_PER_PAGE = 50
_RESULT_TYPES = ("article", "photo", "gif", "video", "audio", "voice", "document")


class _SafeDict(dict):
    """Leave unknown ``{placeholders}`` intact instead of raising ``KeyError``."""

    def __missing__(self, key):  # type: ignore[override]
        return "{" + key + "}"


# ── template rendering ────────────────────────────────────────────────────
def _display_name(user: Any) -> str:
    """The short name a greeting or farewell shows.

    ``{name}`` and ``{mention}`` are documented as the entity's *first* name,
    so ``first_name`` wins over the full name that
    ``utils.get_display_name`` would return. ``title`` covers chats and
    channels, ``username`` covers entities that carry nothing else, and
    ``utils.get_display_name`` is the last resort for a real entity with no
    name attribute of its own.
    """
    name = getattr(user, "first_name", None) or getattr(user, "title", None)
    if not name:
        name = getattr(user, "username", None)
    if not name:
        name = utils.get_display_name(user) or "there"
    return str(name)


def _full_name(user: Any) -> str:
    """First + last name, or the display name when either part is missing."""
    parts = [
        getattr(user, "first_name", None) or "",
        getattr(user, "last_name", None) or "",
    ]
    full = " ".join(p for p in parts if p).strip()
    return full or _display_name(user)


def _mention(user: Any) -> str:
    uid = getattr(user, "id", None)
    if uid:
        return f"[{_display_name(user)}](tg://user?id={uid})"
    return _display_name(user)


def _username(user: Any) -> str:
    username = getattr(user, "username", None)
    return f"@{username}" if username else _mention(user)


def render_welcome(template: str, user: Any, chat: Any, count: int = 0) -> str:
    """Render a welcome template; a malformed template falls back to default."""
    return _render(template, user, chat, count, fallback=DEFAULT_WELCOME)


def render_farewell(template: str, user: Any, chat: Any, count: int = 0) -> str:
    """Render a farewell template; a malformed template falls back to default."""
    return _render(template, user, chat, count, fallback=DEFAULT_FAREWELL)


def _render(template: str, user: Any, chat: Any, count: int, fallback: str) -> str:
    chat_title = getattr(chat, "title", None) or "this chat"
    mapping = _SafeDict(
        name=_display_name(user),
        mention=_mention(user),
        chat=str(chat_title),
        group=str(chat_title),
        count=count if count else "N",
        fullname=_full_name(user),
        username=_username(user),
        userid=getattr(user, "id", ""),
    )
    for candidate in (template, fallback):
        try:
            return candidate.format_map(mapping)
        except (ValueError, KeyError, IndexError, AttributeError) as e:
            LOGS.debug(f"[events] template {candidate!r} is malformed: {e}")
    return fallback


# ── inline results ────────────────────────────────────────────────────────
class InlineResultBuilder:
    """Ergonomic wrapper over Telethon's ``event.builder`` / ``InlineBuilder``.

    Every builder kind the fork's ``InlineBuilder`` exposes is reachable here,
    plus the gap kinds owned by :class:`pyUltroid._core.inlineresults.InlineResults`.
    """

    __slots__ = ("_event", "_builder")

    def __init__(self, event_or_builder: Any):
        if hasattr(event_or_builder, "builder"):
            self._event = event_or_builder
            self._builder = event_or_builder.builder
        else:
            self._event = None
            self._builder = event_or_builder

    async def article(self, **kwargs):
        return await self._make("article", kwargs)

    async def photo(self, **kwargs):
        return await self._make("photo", kwargs)

    async def gif(self, **kwargs):
        return await self._make("gif", kwargs)

    async def video(self, **kwargs):
        return await self._make("video", kwargs)

    async def audio(self, **kwargs):
        return await self._make("audio", kwargs)

    async def voice(self, **kwargs):
        return await self._make("voice", kwargs)

    async def document(self, **kwargs):
        return await self._make("document", kwargs)

    def has(self, kind: str) -> bool:
        """Whether ``kind`` can be built here (native builder, else the gap set)."""
        if kind not in _RESULT_TYPES:
            return False
        if getattr(self._builder, kind, None) is not None:
            return True
        from . import inlineresults

        return inlineresults.has(kind)

    async def _make(self, kind: str, kwargs: dict):
        method = getattr(self._builder, kind, None)
        if method is not None:
            try:
                result = method(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result
            except Exception as e:
                LOGS.error(f"[events] native {kind} result build failed: {e}")
                return None

        from .inlineresults import InlineResults

        if not InlineResults(self._event or self._builder).has(kind):
            LOGS.warning(f"[events] no builder supports the {kind!r} result type")
            return None
        try:
            fallback = getattr(InlineResults(self._event or self._builder), kind)
            result = fallback(**kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result
        except Exception as e:
            LOGS.error(f"[events] {kind} fallback result build failed: {e}")
            return None

    @staticmethod
    def paginate(results: Sequence, page: int = 0, per_page: int = _PER_PAGE) -> list:
        """Return the *page*-th (0-indexed) slice of *results*."""
        per_page = max(1, int(per_page))
        start = max(0, int(page)) * per_page
        if start >= len(results):
            return []
        return list(results[start : start + per_page])

    @staticmethod
    def page_count(results: Sequence, per_page: int = _PER_PAGE) -> int:
        per_page = max(1, int(per_page))
        return max(1, (len(results) + per_page - 1) // per_page)

    async def answer(
        self,
        results: Sequence,
        per_page: int = _PER_PAGE,
        switch_pm: str | None = None,
        switch_pm_param: str | None = None,
        cache_time: int = 300,
        **extra,
    ):
        """Answer one Telegram-limited result set; callers own query continuation."""
        if self._event is None:
            raise RuntimeError(
                "InlineResultBuilder.answer requires an InlineQuery event"
            )
        kwargs = dict(extra, cache_time=cache_time)
        if switch_pm is not None:
            kwargs["switch_pm"] = switch_pm
        if switch_pm_param is not None:
            kwargs["switch_pm_param"] = switch_pm_param
        return await self._event.answer(
            list(results[: max(1, int(per_page))]), **kwargs
        )


class InlineResultCache:
    """A bounded, in-memory TTL cache for inline results."""

    __slots__ = ("_store", "_maxsize")

    def __init__(self, maxsize: int = 512):
        self._store: dict[str, tuple] = {}
        self._maxsize = max(1, int(maxsize))

    def set(self, key: str, value: Any, ttl: float = 300.0) -> None:
        key = str(key)
        if key not in self._store and len(self._store) >= self._maxsize:
            self._evict()
        self._store[key] = (time.time() + float(ttl), value)

    def get(self, key: str) -> Any:
        entry = self._store.get(str(key))
        if entry is None:
            return None
        expiry, value = entry
        if time.time() > expiry:
            self._store.pop(str(key), None)
            return None
        return value

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    def delete(self, key: str) -> None:
        self._store.pop(str(key), None)

    def clear(self) -> None:
        self._store.clear()

    def _evict(self) -> None:
        """Drop expired entries first, then the soonest-to-expire one."""
        now = time.time()
        expired = [k for k, (expiry, _) in self._store.items() if expiry <= now]
        if expired:
            for key in expired:
                self._store.pop(key, None)
            return
        if self._store:
            self._store.pop(min(self._store, key=lambda k: self._store[k][0]), None)

    def __contains__(self, key: str) -> bool:
        return self.has(key)

    def __len__(self) -> int:
        return len(self._store)




# ── button grid ───────────────────────────────────────────────────────────
def _button(item: Any) -> Any:
    """Create one inline/URL button from a compact tuple specification."""
    from telethon import Button

    if hasattr(item, "text") and (hasattr(item, "data") or hasattr(item, "url")):
        return item
    if not isinstance(item, (list, tuple)):
        return item
    label, data = item[0], item[1]
    if len(item) >= 3 and data == "url":
        return Button.url(str(label), str(item[2]))
    if isinstance(data, str) and data.startswith(("http://", "https://")):
        return Button.url(str(label), data)
    return Button.inline(
        str(label), data if isinstance(data, bytes) else str(data).encode()
    )


def build_menu_grid(items: Sequence, columns: int = 2) -> list[list[Any]]:
    """Arrange *items* into rows of *columns* buttons."""
    columns = max(1, int(columns))
    rows: list[list[Any]] = []
    row: list[Any] = []
    for it in items:
        row.append(_button(it))
        if len(row) >= columns:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


async def build_menu_results(
    items: Sequence,
    columns: int = 2,
    builder: Any = None,
    title: str = "Menu",
    description: str = "Choose an option",
    text: str | None = None,
) -> list[Any]:
    """Build a grid-layout inline menu as a single article result.

    *items* is a sequence of button specs  -  ``(label, data)`` for an inline
    callback or ``(label, "url", url)`` for a URL button  -  or ready Telethon
    ``Button`` objects. They are laid out in rows of *columns* and attached to
    one article result.

    *builder* may be an ``InlineQuery`` event, an :class:`InlineResultBuilder`,
    or a raw Telethon ``InlineBuilder``. When omitted, just the button grid is
    returned (useful for ``Message.reply(buttons=...)``).
    """
    grid = build_menu_grid(items, columns)
    if builder is None:
        return grid

    ib = (
        builder
        if isinstance(builder, InlineResultBuilder)
        else InlineResultBuilder(builder)
    )
    article = await ib.article(
        title=title, description=description, text=text or title, buttons=grid
    )
    return [article] if article is not None else []

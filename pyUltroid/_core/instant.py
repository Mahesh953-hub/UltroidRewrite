# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Instant inline  -  on-demand inline answering with native ``via @bot``.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import asyncio
import re
import secrets
from dataclasses import dataclass, field
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
    Union,
)
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence

from telethon import TelegramClient, events
from telethon.errors.rpcbaseerrors import RPCError
from telethon.errors.rpcerrorlist import (
    BotInlineDisabledError,
    BotResponseTimeoutError,
    ChatSendInlineForbiddenError,
    InlineResultExpiredError,
    QueryIdInvalidError,
    ResultsTooMuchError,
)
from telethon.tl.custom.inlineresults import InlineResults as _TLInlineResults

from ..startup.logger import LOGS
from .entities import normalize_parsed
from .inlineresults import InlineResults
from .pipeline import STOP, pipeline_for

__all__ = (
    "InstantInline",
    "InstantInlineError",
    "InlineUnavailable",
    "InstantQuery",
    "InstantResult",
    "ResultsProvider",
    "active_sessions",
    "instant_inline",
    "session_for_token",
)

#: Telegram's hard ceiling on one ``SetInlineBotResults`` answer.
MAX_RESULTS = 50

#: Default page size when a provider returns more than one page of results.
PER_PAGE = 20

#: Routes run ahead of every plugin-registered inline handler.
ROUTE_PRIORITY = 10_000

#: Owner tag stamped on every route this module registers.
ROUTE_OWNER = "pyUltroid._core.instant"

_TOKEN_PREFIX = "ii"

#: The result kinds :class:`InstantResult` can build.
KINDS = frozenset(
    {
        "article",
        "photo",
        "document",
        "file",
        "gif",
        "video",
        "audio",
        "voice",
        "sticker",
        "game",
        "location",
        "venue",
        "contact",
        "cached_photo",
        "cached_document",
        "cached_gif",
        "cached_video",
        "cached_audio",
        "cached_voice",
        "cached_sticker",
    }
)

#: Kinds whose builder method takes the media as its first positional argument.
_FILE_KINDS = frozenset(
    {
        "photo",
        "document",
        "file",
        "gif",
        "video",
        "audio",
        "voice",
        "sticker",
        "cached_photo",
        "cached_document",
        "cached_gif",
        "cached_video",
        "cached_audio",
        "cached_voice",
        "cached_sticker",
    }
)


class InstantInlineError(RuntimeError):
    """The instant-inline session was used in a way it cannot honour."""


class InlineUnavailable(InstantInlineError):
    """The inline round trip cannot run, so ``via @bot`` is unreachable.

    Raised for the three recoverable causes  -  no assistant bot, inline mode
    disabled for the assistant, or a chat that forbids inline results  -  and
    carries the originating RPC error in ``__cause__`` when there was one. The
    one-shot helpers catch it and fall back to ``eor``.
    """


# ── result specification ──────────────────────────────────────────────────
@dataclass(slots=True, frozen=True)
class InstantResult:
    """One inline result, described declaratively rather than pre-built.

    A provider returns these (or already-built ``InputBotInlineResult*``
    objects, which pass straight through). Building is deferred to the moment
    the bot answers, because ``InlineBuilder`` needs the live client to upload
    media and to resolve reply markup.
    """

    title: str = ""
    text: str | None = None
    kind: str = "article"
    description: str | None = None
    buttons: Any = None
    file: Any = None
    url: str | None = None
    thumb: Any = None
    mime_type: str | None = None
    parse_mode: Any = ()
    link_preview: bool = False
    include_media: bool = True
    result_id: str | None = None
    #: Passed verbatim to the matching :class:`InlineResults` method. Use it for
    #: kind-specific arguments such as ``geo``/``address``/``phone_number``.
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise InstantInlineError(
                f"unknown instant-inline kind {self.kind!r}; "
                f"expected one of {', '.join(sorted(KINDS))}"
            )
        if self.kind in _FILE_KINDS and self.file is None:
            raise InstantInlineError(f"kind {self.kind!r} needs a file=")
        if self.kind == "game" and not self.extra.get("short_name"):
            raise InstantInlineError("kind 'game' needs extra={'short_name': ...}")

    async def build(self, builder: InlineResults) -> Any:
        """Build the ``InputBotInlineResult*`` for this specification.

        Arguments are assembled per kind into a single dictionary rather than
        splatted alongside explicit keywords, for two reasons. The builder
        methods do not share a signature  -  the geo kinds take no
        ``text``/``parse_mode`` at all, ``game`` must set ``game=True`` for the
        fork to emit a game message, and ``photo`` has no ``mime_type``  -  and
        ``extra`` must keep overriding the field defaults, which it cannot do if
        both arrive as keywords at the same call.
        """
        extra: dict[str, Any] = dict(self.extra)
        args: dict[str, Any] = {}
        if self.result_id is not None:
            args["id"] = self.result_id
        if self.buttons is not None:
            args["buttons"] = self.buttons

        if self.kind == "article":
            args.update(
                title=self.title,
                description=self.description,
                url=self.url,
                thumb=self.thumb,
                text=self.text,
                parse_mode=self.parse_mode,
                link_preview=self.link_preview,
            )
            return await builder.article(**{**args, **extra})

        if self.kind == "game":
            # ``game`` defaults to False in the fork and is forwarded straight
            # to ``_message``, which then refuses to build a game message.
            args["game"] = True
            merged = {**args, **extra}
            merged.pop("text", None)
            return await builder.game(merged.pop("short_name"), **merged)

        if self.kind in {"location", "venue", "contact"}:
            # These take their subject positionally and accept no caption.
            args.update(description=self.description, thumb=self.thumb)
            if self.title:
                args["title"] = self.title
            merged = {**args, **extra}
            if self.kind == "location":
                return await builder.location(
                    merged.pop("latitude"), merged.pop("longitude"), **merged
                )
            if self.kind == "venue":
                return await builder.venue(
                    merged.pop("latitude"),
                    merged.pop("longitude"),
                    merged.pop("title"),
                    merged.pop("address"),
                    **merged,
                )
            return await builder.contact(
                merged.pop("phone_number"), merged.pop("first_name"), **merged
            )

        # Everything else is media, taking the file first and the caption after.
        args.update(
            text=self.text,
            parse_mode=self.parse_mode,
            include_media=self.include_media,
        )
        if self.kind not in {"photo", "cached_photo"}:
            # ``photo`` has no mime_type parameter; the document kinds do.
            args["mime_type"] = self.mime_type
        merged = {**args, **extra}
        if self.kind in {"photo", "cached_photo"}:
            merged.pop("mime_type", None)
        method = getattr(builder, "document" if self.kind == "file" else self.kind)
        return await method(merged.pop("file", self.file), **_drop_none(merged))

    def as_text(self) -> tuple[str, list[Any]]:
        """The text this result would send, normalised to ``(text, entities)``.

        Used by the ``eor`` fallback. It is tolerant of a ``text`` that is
        already a ``(text, entities)`` pair, which is what a ``CustomMarkdown``
        or ``MarkdownV3`` ``parse()`` hands back.
        """
        return normalize_parsed(self.text, fallback=self.title)


def _drop_none(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is ``None`` so builder defaults apply."""
    return {key: value for key, value in kwargs.items() if value is not None}


# ── the live query handed to a provider ───────────────────────────────────
@dataclass(slots=True, frozen=True)
class InstantQuery:
    """What the bot knows at the moment it must answer.

    Passed to a callable provider so results can depend on the query text, the
    page, or who asked  -  this is what makes the flow on demand rather than
    precomputed.
    """

    event: Any
    text: str
    offset: str
    page: int
    user_id: int | None
    session: InstantInline

    @property
    def client(self) -> Any:
        """The assistant client answering this query."""
        return getattr(self.event, "client", None)

    @property
    def builder(self) -> InlineResults:
        """A complete inline-result builder bound to the answering client."""
        return InlineResults(self.event)


ResultSpec = Union[InstantResult, Any]
ResultsProvider = Union[
    Sequence[ResultSpec],
    Callable[
        [InstantQuery], Union[Sequence[ResultSpec], Awaitable[Sequence[ResultSpec]]]
    ],
]


# ── session registry ──────────────────────────────────────────────────────
#: Open sessions, keyed by their token. An entry exists only between
#: ``open()`` and ``close()``; the watchdog guarantees the close.
_SESSIONS: dict[str, InstantInline] = {}


def session_for_token(token: str) -> InstantInline | None:
    """The open session owning ``token``, if any."""
    return _SESSIONS.get(token)


def active_sessions() -> tuple[InstantInline, ...]:
    """Every session currently holding a route. Diagnostics only."""
    return tuple(_SESSIONS.values())


class InstantInline:
    """An on-demand inline answering session.

    Typical use is the one-shot :meth:`show`, which registers, queries, clicks
    and retires in a single await::

        await InstantInline(results).show(event)

    The session is also a context manager when several sends should share one
    registration, and :meth:`query` / :meth:`send` are available for callers
    that want to drive ``client.inline_query`` themselves.

    ``timeout`` bounds how long the *route* stays registered, not how long a
    results provider may run. An expiry retires the route so no session leaks,
    but a provider already awaiting inside an answer keeps going; give a slow
    provider its own deadline.
    """

    __slots__ = (
        "_bot",
        "_user",
        "_provider",
        "_owner_id",
        "_token",
        "_route",
        "_pipeline",
        "_watchdog",
        "_bot_ref",
        "cache_time",
        "gallery",
        "private",
        "per_page",
        "switch_pm",
        "switch_pm_param",
        "timeout",
        "route_owner",
    )

    def __init__(
        self,
        results: ResultsProvider,
        *,
        bot: TelegramClient | None = None,
        user: TelegramClient | None = None,
        owner_id: int | None = None,
        gallery: bool = False,
        cache_time: int = 0,
        private: bool = True,
        per_page: int = PER_PAGE,
        switch_pm: str | None = None,
        switch_pm_param: str = "",
        timeout: float = 60.0,
        route_owner: str = ROUTE_OWNER,
    ) -> None:
        if results is None:
            raise InstantInlineError("instant inline needs results or a provider")
        if per_page < 1 or per_page > MAX_RESULTS:
            raise InstantInlineError(f"per_page must be between 1 and {MAX_RESULTS}")
        if timeout <= 0:
            raise InstantInlineError("timeout must be positive")

        self._provider = results
        self._bot = bot
        self._user = user
        self._owner_id = owner_id
        self._token = f"{_TOKEN_PREFIX}{secrets.token_urlsafe(9)}"
        self._route: Any = None
        self._pipeline: Any = None
        self._watchdog: asyncio.TimerHandle | None = None
        self._bot_ref: Any = None

        self.gallery = bool(gallery)
        self.cache_time = int(cache_time)
        self.private = bool(private)
        self.per_page = int(per_page)
        self.switch_pm = switch_pm
        self.switch_pm_param = switch_pm_param
        self.timeout = float(timeout)
        self.route_owner = route_owner

    # ── identity ──────────────────────────────────────────────────────────
    @property
    def token(self) -> str:
        """The random token that binds a query to this session."""
        return self._token

    @property
    def open(self) -> bool:
        """Whether this session currently holds a route."""
        return self._route is not None

    def __repr__(self) -> str:
        return (
            f"<InstantInline token={self._token} open={self.open} "
            f"gallery={self.gallery} private={self.private}>"
        )

    # ── client resolution ─────────────────────────────────────────────────
    def _resolve_clients(self, event: Any = None) -> tuple[Any, Any]:
        """Work out which client answers and which client queries.

        The *bot* answers ``UpdateBotInlineQuery``; the *user* issues the two
        requests that carry the attribution. Explicit constructor arguments win,
        then the triggering event, then the process-wide clients.
        """
        bot, user = self._bot, self._user
        if bot is None or user is None:
            asst, ultroid = _process_clients()
            event_client = getattr(event, "client", None)
            if user is None:
                user = self._user = (
                    event_client
                    if event_client is not None and event_client is not asst
                    else ultroid
                )
            if bot is None:
                bot = self._bot = asst if asst is not None else user
        if user is None:
            raise InlineUnavailable("no user client is available to send the result")
        if bot is None:
            raise InlineUnavailable("no assistant bot is available to answer inline")
        return bot, user

    async def _resolve_bot_ref(self, bot: Any) -> Any:
        """A reference to the assistant that ``inline_query`` can resolve."""
        if self._bot_ref is None:
            me = await bot.get_me()
            if getattr(me, "bot", False) is False:
                raise InlineUnavailable(
                    "the client asked to answer inline queries is not a bot, "
                    "so Telegram will never send it an inline query"
                )
            self._bot_ref = getattr(me, "username", None) or me.id
        return self._bot_ref

    async def _owner(self, user: Any) -> int | None:
        """The only user id allowed to consume this session's token."""
        if self._owner_id is None:
            me = await user.get_me()
            self._owner_id = getattr(me, "id", None)
        return self._owner_id

    # ── registration ──────────────────────────────────────────────────────
    async def register(self, event: Any = None) -> InstantInline:
        """Install the one-shot inline route. Idempotent."""
        if self._route is not None:
            return self
        bot, user = self._resolve_clients(event)
        await self._resolve_bot_ref(bot)
        await self._owner(user)

        # ``install()`` is idempotent and is what actually puts the pipeline in
        # front of Telethon's dispatch. A route on an uninstalled pipeline never
        # fires, which would make the whole round trip hang on the query.
        self._pipeline = pipeline_for(bot).install()
        self._route = self._pipeline.route(
            events.InlineQuery(pattern=self._pattern()),
            self._answer,
            match=self._is_owner,
            priority=ROUTE_PRIORITY,
            owner=self.route_owner,
            exclusive=True,
            route_id=f"instant-inline:{self._token}",
        )
        _SESSIONS[self._token] = self
        self._arm_watchdog()
        return self

    def close(self) -> bool:
        """Retire the route and the watchdog. Idempotent; safe off-loop."""
        self._disarm_watchdog()
        _SESSIONS.pop(self._token, None)
        route, pipeline = self._route, self._pipeline
        self._route = self._pipeline = None
        if route is None or pipeline is None:
            return False
        return bool(pipeline.remove(route))

    async def __aenter__(self) -> InstantInline:
        return await self.register()

    async def __aexit__(self, *_exc: Any) -> bool:
        self.close()
        return False

    def _pattern(self) -> str:
        """Match only queries carrying this session's token."""
        return rf"^{re.escape(self._token)}(?:\s|$)"

    def _is_owner(self, event: Any) -> bool:
        """Reject a token replayed by anybody other than its owner."""
        if self._owner_id is None:
            return True
        return _query_user_id(event) == self._owner_id

    def _arm_watchdog(self) -> None:
        """Guarantee the route cannot outlive the session, caller or not."""
        self._disarm_watchdog()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover - register() is always awaited
            return
        self._watchdog = loop.call_later(self.timeout, self._expire)

    def _disarm_watchdog(self) -> None:
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None:
            watchdog.cancel()

    def _expire(self) -> None:
        self._watchdog = None
        if self.close():
            LOGS.debug(
                "instant inline %s expired after %.1fs without a click",
                self._token,
                self.timeout,
            )

    # ── answering ─────────────────────────────────────────────────────────
    def _compose(self, text: str = "") -> str:
        """The query string the user client sends: the token, then the text.

        The page never rides in the query text. Telegram carries pagination in
        its own ``offset`` field, which it echoes back unchanged from whatever
        the previous answer put in ``next_offset``; encoding the page into the
        text would both break that protocol and mangle a user query that happens
        to start like a page marker.
        """
        return f"{self._token} {text}" if text else self._token

    def _query_text(self, query: str) -> str:
        """Strip the token from a received query, leaving the user's text."""
        return query[len(self._token) :].strip()

    @staticmethod
    def _page_of(offset: Any) -> int:
        """Telegram's ``offset`` as a page number; anything odd means page 0."""
        text = str(offset or "").strip()
        return int(text) if text.isdigit() else 0

    async def _specs(self, query: InstantQuery) -> Sequence[ResultSpec]:
        """Resolve the provider into a concrete sequence of results."""
        provider = self._provider
        if callable(provider):
            provider = provider(query)
        if asyncio.iscoroutine(provider) or isinstance(provider, asyncio.Future):
            provider = await provider
        if provider is None:
            return ()
        if isinstance(provider, (str, bytes)) or not isinstance(provider, Iterable):
            return (provider,)
        return list(provider)

    async def _answer(self, event: Any) -> None:
        """Handle one ``UpdateBotInlineQuery`` aimed at this session."""
        offset = event.offset or ""
        page = self._page_of(offset)
        query = InstantQuery(
            event=event,
            text=self._query_text(event.text or ""),
            offset=offset,
            page=page,
            user_id=_query_user_id(event),
            session=self,
        )
        try:
            specs = await self._specs(query)
            window = specs[page * self.per_page : (page + 1) * self.per_page]
            built = await self._build(query, window)
            next_offset = (
                str(page + 1) if len(specs) > (page + 1) * self.per_page else None
            )
            await event.answer(
                built,
                cache_time=self.cache_time,
                gallery=self.gallery,
                private=self.private,
                next_offset=next_offset,
                switch_pm=self.switch_pm,
                switch_pm_param=self.switch_pm_param,
            )
        except ResultsTooMuchError:
            LOGS.error(
                "instant inline %s built more than %d results; "
                "lower per_page or return fewer",
                self._token,
                MAX_RESULTS,
            )
            await self._answer_empty(event, "Too many results")
        except QueryIdInvalidError:
            LOGS.warning("instant inline %s answered an expired query", self._token)
        except Exception as exc:  # noqa: BLE001 - never leave the query hanging
            LOGS.exception("instant inline %s failed to answer: %s", self._token, exc)
            await self._answer_empty(event, type(exc).__name__)
        raise STOP

    async def _build(
        self, query: InstantQuery, specs: Sequence[ResultSpec]
    ) -> list[Any]:
        """Build one window of results, skipping the ones that cannot build."""
        builder = query.builder
        built: list[Any] = []
        for spec in specs[:MAX_RESULTS]:
            if isinstance(spec, InstantResult):
                built.append(await spec.build(builder))
            elif isinstance(spec, str):
                built.append(await InstantResult(title=spec, text=spec).build(builder))
            else:
                built.append(spec)
        return built

    async def _answer_empty(self, event: Any, reason: str) -> None:
        """Answer with a single inert article so the client stops spinning."""
        try:
            await event.answer(
                [
                    await event.builder.article(
                        title=reason,
                        text=reason,
                        description="Nothing to show.",
                    )
                ],
                cache_time=0,
                private=True,
            )
        except Exception as exc:  # noqa: BLE001 - best effort only
            LOGS.debug("instant inline %s could not answer empty: %s", self._token, exc)

    # ── the user side ─────────────────────────────────────────────────────
    async def query(
        self,
        text: str = "",
        *,
        peer: Any = None,
        offset: str = "",
    ) -> _TLInlineResults:
        """Issue ``GetInlineBotResults`` against our own assistant.

        ``peer`` is the chat the results are destined for; passing it lets
        Telegram filter by what that chat permits and makes ``click()`` work
        without repeating the destination.
        """
        await self.register()
        bot, user = self._resolve_clients()
        bot_ref = await self._resolve_bot_ref(bot)
        try:
            return await user.inline_query(
                bot_ref, self._compose(text), entity=peer, offset=offset or None
            )
        except BotInlineDisabledError as exc:
            raise InlineUnavailable(
                "the assistant has inline mode disabled; enable it in BotFather "
                "with /setinline before instant inline can be used"
            ) from exc
        except BotResponseTimeoutError as exc:
            raise InlineUnavailable(
                "the assistant did not answer the inline query in time"
            ) from exc
        except RPCError as exc:
            raise InlineUnavailable(
                f"the inline query failed: {type(exc).__name__}"
            ) from exc

    async def send(
        self,
        chat: Any,
        *,
        text: str = "",
        reply_to: Any = None,
        index: int = 0,
        via: bool = True,
        silent: bool = False,
        clear_draft: bool = False,
        background: bool | None = None,
    ) -> Any:
        """Query, then send result ``index`` as the user, carrying ``via @bot``.

        ``via=True`` (the default) leaves Telegram's attribution in place;
        ``via=False`` sets ``hide_via`` and the tag disappears, which defeats
        the purpose of this module but is occasionally wanted.
        """
        results = await self.query(text, peer=chat)
        if not results:
            raise InlineUnavailable("the assistant answered with no results")
        if index >= len(results):
            raise InstantInlineError(
                f"result {index} was requested but only {len(results)} were built"
            )
        try:
            return await results[index].click(
                chat,
                reply_to=reply_to,
                hide_via=not via,
                silent=silent,
                clear_draft=clear_draft,
                background=background,
            )
        except ChatSendInlineForbiddenError as exc:
            raise InlineUnavailable(
                "this chat does not allow inline results to be sent"
            ) from exc
        except InlineResultExpiredError as exc:
            raise InlineUnavailable(
                "the inline result expired before it could be sent"
            ) from exc
        except RPCError as exc:
            raise InlineUnavailable(
                f"sending the inline result failed: {type(exc).__name__}"
            ) from exc

    async def show(
        self,
        event: Any,
        *,
        text: str = "",
        index: int = 0,
        reply: bool = True,
        chat: Any = None,
        via: bool = True,
        fallback: bool = True,
        silent: bool = False,
    ) -> Any:
        """Answer ``event`` with an inline result, then retire the session.

        This is the whole round trip in one await: register, query, click,
        close. The result replies to ``event`` so the attribution appears on a
        message visibly answering the trigger. When the round trip cannot run
        and ``fallback`` is set, the same text and buttons go out through
        ``eor`` instead.
        """
        self._resolve_clients(event)
        destination = chat if chat is not None else event.chat_id
        reply_to = getattr(event, "id", None) if reply else None
        try:
            try:
                return await self.send(
                    destination,
                    text=text,
                    reply_to=reply_to,
                    index=index,
                    via=via,
                    silent=silent,
                )
            finally:
                self.close()
        except InlineUnavailable as exc:
            if not fallback:
                raise
            LOGS.info("instant inline %s fell back to eor: %s", self._token, exc)
            return await self._fallback(event, text, index)

    async def _fallback(self, event: Any, text: str, index: int) -> Any:
        """Send the result's own text and buttons without inline attribution.

        Telegram cannot produce ``via @bot`` without a bot, so this path drops
        the tag and keeps everything else: the text, its entities and the
        buttons all survive.
        """
        query = InstantQuery(
            event=event,
            text=text,
            offset="",
            page=0,
            user_id=getattr(event, "sender_id", None),
            session=self,
        )
        specs = await self._specs(query)
        spec = next(
            (
                s
                for i, s in enumerate(specs)
                if i == index and isinstance(s, InstantResult)
            ),
            None,
        )
        if spec is None:
            raise InlineUnavailable(
                "inline is unavailable and result "
                f"{index} cannot be rendered as a plain message"
            )
        body, entities = spec.as_text()
        return await _edit_or_reply(
            event,
            body,
            formatting_entities=entities or None,
            buttons=spec.buttons,
            link_preview=spec.link_preview,
        )


# ── one-shot helper ───────────────────────────────────────────────────────
async def instant_inline(
    results: ResultsProvider,
    event: Any,
    *,
    text: str = "",
    index: int = 0,
    reply: bool = True,
    chat: Any = None,
    via: bool = True,
    fallback: bool = True,
    bot: TelegramClient | None = None,
    user: TelegramClient | None = None,
    gallery: bool = False,
    private: bool = True,
    cache_time: int = 0,
    timeout: float = 60.0,
) -> Any:
    """Show ``results`` inline in reply to ``event``, in one call.

    ``results`` is a sequence of :class:`InstantResult` (or already-built
    ``InputBotInlineResult*`` objects), or a callable returning one  -  the
    callable receives an :class:`InstantQuery` and may be async, which is how a
    caller computes results only once Telegram actually asks.
    """
    session = InstantInline(
        results,
        bot=bot,
        user=user,
        gallery=gallery,
        private=private,
        cache_time=cache_time,
        timeout=timeout,
    )
    return await session.show(
        event,
        text=text,
        index=index,
        reply=reply,
        chat=chat,
        via=via,
        fallback=fallback,
    )


# ── helpers ───────────────────────────────────────────────────────────────
def _process_clients() -> tuple[Any, Any]:
    """``(asst, ultroid_bot)`` without importing the package at module load."""
    try:
        import pyUltroid
    except Exception:  # pragma: no cover - only if the package is broken
        return None, None
    return getattr(pyUltroid, "asst", None), getattr(pyUltroid, "ultroid_bot", None)


def _query_user_id(event: Any) -> int | None:
    """Who sent this inline query.

    ``InlineQuery.Event`` does not expose ``user_id`` directly  -  it keeps the
    raw ``UpdateBotInlineQuery`` on ``.query`` and derives ``chat_peer`` from
    it. Read the update first and only then fall back to the generic
    sender/chat accessors, so the owner check cannot be defeated by an event
    shape that happens to lack one of them.
    """
    query = getattr(event, "query", None)
    user_id = getattr(query, "user_id", None)
    if user_id is not None:
        return user_id
    for attribute in ("user_id", "sender_id", "chat_id"):
        value = getattr(event, attribute, None)
        if isinstance(value, int):
            return value
    return None


async def _edit_or_reply(event: Any, text: str, **kwargs: Any) -> Any:
    """Route through ``eor`` so outgoing messages are edited, not duplicated."""
    eor = getattr(event, "eor", None)
    if callable(eor):
        return await eor(text, **kwargs)
    from ..startup.patching import eor as _eor

    return await _eor(event, text, **kwargs)

# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Complete inline-result builder.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import hashlib
from typing import Any, List, Optional, Tuple, Union
from collections.abc import Iterable, Sequence

from ..startup.logger import LOGS

from telethon import utils as _utils
from telethon.tl import types as _types
from telethon.tl.custom import InlineBuilder as _InlineBuilder

from .entities import RICH_MD as _RICH_MD
from .richmsg import LegacyMessage, RichBlocks, legacy_message

try:
    from . import richmsg as _richmsg
except Exception:  # pragma: no cover - keep this module standalone-safe
    _richmsg = None


# Capability probing
_CAPABILITIES = {
    "article": ("InputBotInlineResult", "InputBotInlineMessageText"),
    "photo": ("InputBotInlineResultPhoto", "InputBotInlineMessageMediaAuto"),
    "document": ("InputBotInlineResultDocument", "InputBotInlineMessageMediaAuto"),
    "game": ("InputBotInlineResultGame", "InputBotInlineMessageGame"),
    "gif": ("InputBotInlineResultDocument", "InputBotInlineMessageMediaAuto"),
    "video": ("InputBotInlineResultDocument", "InputBotInlineMessageMediaAuto"),
    "audio": ("InputBotInlineResultDocument", "InputBotInlineMessageMediaAuto"),
    "voice": ("InputBotInlineResultDocument", "InputBotInlineMessageMediaAuto"),
    "sticker": ("InputBotInlineResultDocument", "InputBotInlineMessageMediaAuto"),
    "file": ("InputBotInlineResultDocument", "InputBotInlineMessageMediaAuto"),
    "location": (
        "InputBotInlineResult",
        "InputGeoPoint",
        "InputBotInlineMessageMediaGeo",
    ),
    "venue": (
        "InputBotInlineResult",
        "InputGeoPoint",
        "InputBotInlineMessageMediaVenue",
    ),
    "contact": ("InputBotInlineResult", "InputBotInlineMessageMediaContact"),
    "article_rich": (),
    "cached_photo": ("InputBotInlineResultPhoto", "InputBotInlineMessageMediaAuto"),
    "cached_document": (
        "InputBotInlineResultDocument",
        "InputBotInlineMessageMediaAuto",
    ),
    "cached_gif": ("InputBotInlineResultDocument", "InputBotInlineMessageMediaAuto"),
    "cached_video": ("InputBotInlineResultDocument", "InputBotInlineMessageMediaAuto"),
    "cached_audio": ("InputBotInlineResultDocument", "InputBotInlineMessageMediaAuto"),
    "cached_voice": ("InputBotInlineResultDocument", "InputBotInlineMessageMediaAuto"),
    "cached_sticker": (
        "InputBotInlineResultDocument",
        "InputBotInlineMessageMediaAuto",
    ),
}

_DOCUMENT_KINDS = {"gif", "video", "audio", "voice", "sticker", "file"}


def has(kind: str) -> bool:
    if kind == "article_rich":
        return True
    names = _CAPABILITIES.get(kind)
    if not names:
        return False
    return all(getattr(_types, n, None) is not None for n in names)


def _require(name: str):
    cls = getattr(_types, name, None)
    if cls is None:
        raise RuntimeError(f"telethon.tl.types.{name} is not available")
    return cls


def _finalize_id(result, id: str | None):
    if id is None:
        result.id = hashlib.sha256(bytes(result)).hexdigest()
    return result


class InlineResultError(ValueError):
    """A result could not be built from the arguments given."""


# ──────────────────────────────────────────────────────────────────────────
# block -> markup for article_rich, delegating to the richmsg block model
# ──────────────────────────────────────────────────────────────────────────
#
# ``article_rich`` used to carry its own "deliberately minimal" block
# renderer here, justified by staying working before the D1 rich-message
# builder (``pyUltroid/_core/richmsg.py``) landed. D1 has since landed in
# this same branch and there is no import cycle (richmsg does not import
# this module), so this now delegates straight to richmsg's own renderers
#  -  :class:`~pyUltroid._core.richmsg.RichBlocks` is the single block
# model, here and everywhere else in the codebase.
#
# The one seam kept local to ``article_rich`` is the "raw HTML string
# passes through verbatim" contract: richmsg's renderers work on block
# lists, never on a pre-rendered HTML string, so that check has to happen
# before handing off to richmsg.


def _blocks_to_html(blocks_or_html: str | Iterable[Any] | None) -> str:
    """HTML for ``article_rich``'s native ``InputRichMessageHTML`` payload.

    A raw HTML string passes through verbatim (untouched  -  it is assumed
    already valid). Anything else  -  a :class:`~pyUltroid._core.richmsg.
    RichBlocks`, a bare string, or a loose iterable of strings/dicts  -  is
    normalized and rendered via :func:`richmsg.normalize_blocks` +
    :func:`richmsg.render_blocks_native_html` (media ``file_specs`` are
    dropped here; ``article_rich`` only builds the ``html=`` field of
    ``InputRichMessageHTML``, it has no kwarg to attach rich files).
    """
    if isinstance(blocks_or_html, str):
        return blocks_or_html
    if _richmsg is None:  # pragma: no cover - defensive, richmsg ships in-tree
        return ""
    blocks = _richmsg.normalize_blocks(blocks_or_html)
    html_text, _file_specs = _richmsg.render_blocks_native_html(blocks)
    return html_text


def _blocks_to_text(blocks_or_html: str | Iterable[Any] | None) -> str:
    """Plain-text summary for ``article_rich``'s degraded ``article`` fallback.

    A raw HTML string passes through verbatim, same contract as
    :func:`_blocks_to_html`. Anything else is normalized and rendered via
    :func:`richmsg.blocks_to_plain_text`.
    """
    if isinstance(blocks_or_html, str):
        return blocks_or_html
    if _richmsg is None:  # pragma: no cover - defensive, richmsg ships in-tree
        return ""
    blocks = _richmsg.normalize_blocks(blocks_or_html)
    return _richmsg.blocks_to_plain_text(blocks)


# InlineResults
class InlineResults:
    """Complete inline-result builder wrapping the fork's ``InlineBuilder``.

    Construct from whatever is at hand:

    * an ``InlineQuery`` event (uses its ``.builder``);
    * an already-built ``telethon.tl.custom.InlineBuilder``;
    * a bare ``TelegramClient`` (a fresh ``InlineBuilder`` is built for it).
    """

    def __init__(self, event_client_or_builder: Any):
        src = event_client_or_builder
        builder = getattr(src, "builder", None)
        if builder is not None:
            self._builder = builder
            self._client = getattr(builder, "_client", None)
        elif isinstance(src, _InlineBuilder):
            self._builder = src
            self._client = getattr(src, "_client", None)
        else:
            self._client = src
            self._builder = _InlineBuilder(src)

    @staticmethod
    def has(kind: str) -> bool:
        return has(kind)

    # ── message construction, delegated to the fork ───────────────────────
    def _markup(self, buttons):
        """The reply markup for *buttons*, built by the client that will send it.

        A caller that asked for buttons and silently got none is worse than a
        clear failure, so a missing client is an error rather than ``None``.
        """
        if buttons is None:
            return None
        self._require_markup_client()
        return self._client.build_reply_markup(buttons)

    def _require_markup_client(self) -> None:
        """Fail on the missing client before anything tries to use it.

        The fork's ``_message`` builds the markup itself, from its own client, so
        a delegated call with no client raises ``AttributeError`` on ``None``
        rather than the error this module documents. The check therefore has to
        happen on both paths, not only in :meth:`_markup`.
        """
        if self._client is None:
            raise InlineResultError(
                "buttons need a client to build their markup; construct "
                "InlineResults from an event, a builder or a client"
            )

    async def _message(self, *, text, parse_mode, link_preview, media, buttons):
        """Build the ``send_message`` for a text or captioned-media result.

        Delegated to the pinned fork's ``InlineBuilder._message`` so there is one
        implementation of parsing and markup, not two that can drift. ``text`` is
        normalised to ``""`` because a caption-less cached result is legitimate
        here, whereas the fork's "exactly one of text, geo, contact or game"
        check rejects ``None``.

        A parse failure propagates, as it does everywhere else in Telethon. The
        previous local helper caught it, logged, and sent the raw text, so a
        plugin with broken markup shipped visible markup characters and the
        author never found out. The failure is logged on its way past.
        """
        if buttons is not None:
            self._require_markup_client()
        try:
            return await self._builder._message(
                text=text if text is not None else "",
                parse_mode=parse_mode,
                link_preview=link_preview,
                media=media,
                buttons=buttons,
            )
        except Exception as e:
            LOGS.warning(
                "[inlineresults] could not build the message for parse_mode=%r: %s",
                parse_mode,
                e,
            )
            raise

    # ── pass-throughs to the wrapped InlineBuilder ─────────────────────────
    async def article(self, **kwargs):
        return await self._builder.article(**kwargs)

    async def photo(self, file, **kwargs):
        return await self._builder.photo(file, **kwargs)

    async def document(self, file, **kwargs):
        return await self._builder.document(file, **kwargs)

    async def game(self, short_name, **kwargs):
        return await self._builder.game(short_name, **kwargs)

    # ── gap kinds: gif/video/audio/voice/sticker/file ──────────────────────

    async def _document_kind(
        self,
        file,
        kind: str,
        *,
        id=None,
        text=None,
        parse_mode=(),
        link_preview=True,
        buttons=None,
        mime_type=None,
        attributes=None,
        video_note=False,
        use_cache=True,
        include_media=True,
    ):
        if self._builder is None:
            raise RuntimeError("telethon.tl.custom.InlineBuilder is unavailable")
        return await self._builder.document(
            file,
            type=kind,
            mime_type=mime_type,
            attributes=attributes,
            voice_note=(kind == "voice"),
            video_note=video_note,
            use_cache=use_cache,
            id=id,
            text=text,
            parse_mode=parse_mode,
            link_preview=link_preview,
            buttons=buttons,
            include_media=include_media,
        )

    async def gif(self, file, *, mime_type="image/gif", **kwargs):
        return await self._document_kind(file, "gif", mime_type=mime_type, **kwargs)

    async def video(self, file, *, mime_type="video/mp4", **kwargs):
        return await self._document_kind(file, "video", mime_type=mime_type, **kwargs)

    async def audio(self, file, *, mime_type="audio/mpeg", **kwargs):
        return await self._document_kind(file, "audio", mime_type=mime_type, **kwargs)

    async def voice(self, file, *, mime_type="audio/ogg", **kwargs):
        return await self._document_kind(file, "voice", mime_type=mime_type, **kwargs)

    async def sticker(self, file, *, mime_type="image/webp", **kwargs):
        return await self._document_kind(file, "sticker", mime_type=mime_type, **kwargs)

    async def file(self, file, *, mime_type=None, **kwargs):
        """A plain document result, the kind the fork falls back to.

        ``mime_type`` stays ``None`` by default: the fork derives the type from
        the mime type when one is given, and a plain file is exactly the case
        where the caller has nothing more specific to say.
        """
        return await self._document_kind(file, "file", mime_type=mime_type, **kwargs)

    # ── gap kinds InlineBuilder has no method for at all ───────────────────

    async def location(
        self,
        latitude: float,
        longitude: float,
        *,
        title: str | None = None,
        description: str | None = None,
        id: str | None = None,
        thumb=None,
        content=None,
        accuracy_radius: int | None = None,
        heading: int | None = None,
        period: int | None = None,
        proximity_notification_radius: int | None = None,
        buttons=None,
    ):
        """Build a location inline result (``InputBotInlineMessageMediaGeo``)."""
        geo_cls = _require("InputGeoPoint")
        msg_cls = _require("InputBotInlineMessageMediaGeo")
        result_cls = _require("InputBotInlineResult")
        geo_point = geo_cls(
            lat=float(latitude), long=float(longitude), accuracy_radius=accuracy_radius
        )
        send_message = msg_cls(
            geo_point=geo_point,
            heading=heading,
            period=period,
            proximity_notification_radius=proximity_notification_radius,
            reply_markup=self._markup(buttons),
        )
        result = result_cls(
            id=id or "",
            type="geo",
            send_message=send_message,
            title=title,
            description=description,
            thumb=thumb,
            content=content,
        )
        return _finalize_id(result, id)

    async def venue(
        self,
        latitude: float,
        longitude: float,
        title: str,
        address: str,
        *,
        provider: str = "foursquare",
        venue_id: str = "",
        venue_type: str = "",
        id: str | None = None,
        description: str | None = None,
        thumb=None,
        content=None,
        buttons=None,
    ):
        """Build a venue inline result (``InputBotInlineMessageMediaVenue``)."""
        geo_cls = _require("InputGeoPoint")
        msg_cls = _require("InputBotInlineMessageMediaVenue")
        result_cls = _require("InputBotInlineResult")
        geo_point = geo_cls(lat=float(latitude), long=float(longitude))
        send_message = msg_cls(
            geo_point=geo_point,
            title=title,
            address=address,
            provider=provider,
            venue_id=venue_id,
            venue_type=venue_type,
            reply_markup=self._markup(buttons),
        )
        result = result_cls(
            id=id or "",
            type="venue",
            send_message=send_message,
            title=title,
            description=description,
            thumb=thumb,
            content=content,
        )
        return _finalize_id(result, id)

    async def contact(
        self,
        phone_number: str,
        first_name: str,
        *,
        last_name: str = "",
        vcard: str = "",
        id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        thumb=None,
        content=None,
        buttons=None,
    ):
        """Build a contact inline result (``InputBotInlineMessageMediaContact``)."""
        msg_cls = _require("InputBotInlineMessageMediaContact")
        result_cls = _require("InputBotInlineResult")
        send_message = msg_cls(
            phone_number=phone_number,
            first_name=first_name,
            last_name=last_name,
            vcard=vcard,
            reply_markup=self._markup(buttons),
        )
        result = result_cls(
            id=id or "",
            type="contact",
            send_message=send_message,
            title=title or first_name,
            description=description,
            thumb=thumb,
            content=content,
        )
        return _finalize_id(result, id)

    # ── article_rich
    async def article_rich(
        self,
        title: str,
        payload: Any = None,
        *,
        engine: str = "rich",
        format: str = "html",
        is_premium: bool = False,
        is_bot: bool = True,
        description: str | None = None,
        url: str | None = None,
        id: str | None = None,
        buttons=None,
        rtl: bool = False,
        noautolink: bool = False,
        fallback_parse_mode=None,
    ):
        """Rich-message inline article via ``InputBotInlineMessageRichMessage``.

        *payload* may be, in order of preference:

        * a raw HTML string - passed through verbatim into
          ``InputRichMessageHTML`` (see :func:`_blocks_to_html`);
        * a :class:`~pyUltroid._core.richmsg.RichBlocks` instance or an
          iterable of block dicts / plain strings - rendered to HTML via
          the block model;
        * an already-built rich payload (``InputRichMessageHTML`` /
          ``InputRichMessageMarkdown``) - wrapped as-is;
        * a :class:`~pyUltroid._core.richmsg.LegacyMessage` - the plain
          ``article`` fallback renders ``payload.markup``.

        When ``InputBotInlineMessageRichMessage`` /
        ``InputRichMessageHTML`` are unavailable in this Telethon build (or
        *payload* is omitted), this degrades to a plain :meth:`article`
        rendered with ``parse_mode=`` MarkdownV3 (falling back further to no
        parsing at all if MarkdownV3 itself is unavailable).
        """
        if format not in ("html", "markdown"):
            raise ValueError("format must be 'html' or 'markdown'")
        if engine not in ("auto", "legacy", "rich"):
            raise ValueError("engine must be 'auto', 'legacy' or 'rich'")
        legacy = payload if isinstance(payload, LegacyMessage) else None
        if (
            legacy is None
            and payload is not None
            and engine != "legacy"
            and has("article_rich")
        ):
            if isinstance(payload, (RichBlocks, list, tuple)):
                payload = _blocks_to_html(payload)
            if isinstance(payload, str):
                if format == "markdown":
                    rich = _require("InputRichMessageMarkdown")(
                        markdown=payload,
                        rtl=bool(rtl),
                        noautolink=bool(noautolink),
                    )
                else:
                    rich = _require("InputRichMessageHTML")(
                        html=payload,
                        rtl=bool(rtl),
                        noautolink=bool(noautolink),
                    )
            else:
                rich = payload
            msg_cls = _require("InputBotInlineMessageRichMessage")
            send_message = msg_cls(
                rich_message=rich,
                reply_markup=self._markup(buttons),
            )
            result = _types.InputBotInlineResult(
                id=id or "",
                type="article",
                send_message=send_message,
                title=title,
                description=description,
                url=url,
            )
            return _finalize_id(result, id)

        parse_mode = fallback_parse_mode
        if parse_mode is None:
            parse_mode = _RICH_MD if _RICH_MD.available else None
        if legacy is not None:
            markup = legacy.markup
        elif engine == "legacy" and payload is not None:
            # explicit legacy engine: render through the mdv3 fallback
            # dialect, exactly like richmsg.legacy_message does
            markup = legacy_message(payload, format=format).markup
        elif isinstance(payload, str):
            markup = payload
        elif payload is None:
            markup = ""
        else:
            markup = _blocks_to_text(payload)
        return await self.article(
            title=title,
            description=description,
            url=url,
            id=id,
            text=markup,
            parse_mode=parse_mode,
            buttons=buttons,
        )

    async def _cached_document(
        self,
        file,
        kind: str,
        *,
        id: str | None = None,
        title: str | None = None,
        description: str | None = None,
        text: str | None = None,
        parse_mode=(),
        link_preview: bool = True,
        include_media: bool = True,
        buttons=None,
    ):
        try:
            fh = _utils.get_input_document(file)
        except TypeError as e:
            raise ValueError(
                f"cached_{kind}() requires an already-known Document / "
                "InputDocument reference (e.g. `message.document`); got "
                f"{type(file).__name__!r} which would require an upload  -  "
                f"use InlineResults.{kind}() instead"
            ) from e
        result_cls = _require("InputBotInlineResultDocument")
        send_message = await self._message(
            text=text,
            parse_mode=parse_mode,
            link_preview=link_preview,
            media=include_media,
            buttons=buttons,
        )
        result = result_cls(
            id=id or "",
            type=kind,
            document=fh,
            send_message=send_message,
            title=title,
            description=description,
        )
        return _finalize_id(result, id)

    async def cached_document(self, file, **kwargs):
        return await self._cached_document(file, "file", **kwargs)

    async def cached_gif(self, file, **kwargs):
        return await self._cached_document(file, "gif", **kwargs)

    async def cached_video(self, file, **kwargs):
        return await self._cached_document(file, "video", **kwargs)

    async def cached_audio(self, file, **kwargs):
        return await self._cached_document(file, "audio", **kwargs)

    async def cached_voice(self, file, **kwargs):
        return await self._cached_document(file, "voice", **kwargs)

    async def cached_sticker(self, file, **kwargs):
        return await self._cached_document(file, "sticker", **kwargs)

    async def cached_photo(
        self,
        file,
        *,
        id: str | None = None,
        text: str | None = None,
        parse_mode=(),
        link_preview: bool = True,
        include_media: bool = True,
        buttons=None,
    ):
        try:
            fh = _utils.get_input_photo(file)
        except TypeError as e:
            raise ValueError(
                "cached_photo() requires an already-known Photo / InputPhoto "
                f"reference (e.g. `message.photo`); got {type(file).__name__!r} "
                "which would require an upload  -  use InlineResults.photo() instead"
            ) from e
        result_cls = _require("InputBotInlineResultPhoto")
        send_message = await self._message(
            text=text,
            parse_mode=parse_mode,
            link_preview=link_preview,
            media=include_media,
            buttons=buttons,
        )
        result = result_cls(
            id=id or "", type="photo", photo=fh, send_message=send_message
        )
        return _finalize_id(result, id)


__all__ = (
    "InlineResults",
    "has",
)

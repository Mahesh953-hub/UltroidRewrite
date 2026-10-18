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
from collections.abc import Iterable, Sequence
from typing import Any

from telethon import utils as _utils
from telethon.tl import types as _types
from telethon.tl.custom import InlineBuilder as _InlineBuilder

from ..startup.logger import LOGS
from . import richmsg as _richmsg
from .richmsg import RichBlocks, RichMarkup, coerce_to_rich_markup


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
    """Render HTML-only blocks, refusing unsupported photo/document references."""
    if isinstance(blocks_or_html, str):
        return blocks_or_html
    markup = _richmsg.rich_markup_from_blocks(blocks_or_html, format="html")
    if markup.files:
        raise ValueError("RichMessage photo/document references are unsupported")
    return markup.text


def _blocks_to_text(blocks_or_html: str | Iterable[Any] | None) -> str:
    """Render content as plain text for consumers that explicitly need it."""
    if isinstance(blocks_or_html, RichMarkup):
        return _richmsg.blocks_to_plain_text(blocks_or_html) if _richmsg else ""
    if isinstance(blocks_or_html, str):
        return blocks_or_html
    return _richmsg.blocks_to_plain_text(_richmsg.normalize_blocks(blocks_or_html))


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
    async def article(self, *args, **kwargs):
        """Build a text article only through the native rich-message schema."""
        if len(args) > 2:
            raise TypeError("article accepts at most title and description positionally")
        if args:
            kwargs.setdefault("title", args[0])
        if len(args) == 2:
            kwargs.setdefault("description", args[1])
        try:
            title = kwargs.pop("title")
        except KeyError as exc:
            raise TypeError("article requires title") from exc
        if "payload" in kwargs and "text" in kwargs:
            raise TypeError("article accepts either payload= or text=, not both")
        payload = kwargs.pop("payload", kwargs.pop("text", ""))
        parse_mode = kwargs.pop("parse_mode", ())
        if parse_mode == ():
            parse_mode = "v2"
        result_type = kwargs.pop("type", "article")
        include_media = kwargs.pop("include_media", False)
        if result_type != "article" or include_media:
            raise InlineResultError(
                "media inline results cannot be represented as RichMessage articles"
            )
        for unsupported in ("geo", "contact", "game", "period"):
            if kwargs.pop(unsupported, None) is not None:
                raise InlineResultError(
                    f"{unsupported} inline results are not RichMessage articles"
                )
        if isinstance(payload, RichMarkup):
            markup = payload
        elif isinstance(payload, (RichBlocks, list, tuple)):
            markup = None
        else:
            markup = coerce_to_rich_markup(payload, parse_mode=parse_mode)
            if markup is None:
                raise InlineResultError("article text cannot be converted to rich HTML")
        if markup is not None:
            payload = markup
            rich_format = markup.format
        else:
            rich_format = "html"
        link_preview = kwargs.pop("link_preview", True)
        noautolink = kwargs.pop("noautolink", not bool(link_preview))
        allowed = {"description", "url", "thumb", "content", "id", "buttons", "rtl", "files"}
        unexpected = set(kwargs) - allowed
        if unexpected:
            names = ", ".join(sorted(unexpected))
            raise InlineResultError(f"unsupported rich article options: {names}")
        return await self.article_rich(
            title,
            payload,
            format=rich_format,
            noautolink=noautolink,
            **kwargs,
        )

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
        geo_point = _types.InputGeoPoint(
            lat=float(latitude), long=float(longitude), accuracy_radius=accuracy_radius
        )
        send_message = _types.InputBotInlineMessageMediaGeo(
            geo_point=geo_point,
            heading=heading,
            period=period,
            proximity_notification_radius=proximity_notification_radius,
            reply_markup=self._markup(buttons),
        )
        result = _types.InputBotInlineResult(
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
        geo_point = _types.InputGeoPoint(lat=float(latitude), long=float(longitude))
        send_message = _types.InputBotInlineMessageMediaVenue(
            geo_point=geo_point,
            title=title,
            address=address,
            provider=provider,
            venue_id=venue_id,
            venue_type=venue_type,
            reply_markup=self._markup(buttons),
        )
        result = _types.InputBotInlineResult(
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
        send_message = _types.InputBotInlineMessageMediaContact(
            phone_number=phone_number,
            first_name=first_name,
            last_name=last_name,
            vcard=vcard,
            reply_markup=self._markup(buttons),
        )
        result = _types.InputBotInlineResult(
            id=id or "",
            type="contact",
            send_message=send_message,
            title=title or first_name,
            description=description,
            thumb=thumb,
            content=content,
        )
        return _finalize_id(result, id)

    async def article_rich(
        self,
        title: str,
        payload: Any = None,
        *,
        format: str | None = None,
        files: Sequence[Any] | None = None,
        description: str | None = None,
        url: str | None = None,
        id: str | None = None,
        thumb=None,
        content=None,
        buttons=None,
        rtl: bool = False,
        noautolink: bool | None = None,
    ):
        """Build a native RichMessage article, preserving files or rejecting them."""
        if payload is None:
            raise ValueError("article_rich requires a rich payload")
        if format not in (None, "html", "markdown"):
            raise ValueError("format must be 'html' or 'markdown'")
        chosen_format = format or (payload.format if isinstance(payload, RichMarkup) else "html")
        if _richmsg._is_native_rich_message(payload):
            if files:
                raise ValueError("files are already part of the supplied InputRichMessage")
            if format is not None:
                raise ValueError("format cannot override an already-built InputRichMessage")
            rich = _richmsg.rich_message(payload)
        elif isinstance(payload, RichMarkup):
            if format is not None and format != payload.format:
                raise ValueError("format must match the RichMarkup format")
            markup = RichMarkup(
                payload.text,
                payload.format,
                tuple(payload.files) + tuple(files or ()),
            )
            rich = _richmsg.rich_message(markup, rtl=rtl, noautolink=noautolink)
        elif isinstance(payload, str):
            markup = RichMarkup(payload, chosen_format, tuple(files or ()))
            rich = _richmsg.rich_message(markup, rtl=rtl, noautolink=noautolink)
        elif isinstance(payload, (RichBlocks, list, tuple)):
            if chosen_format == "html":
                rich = _richmsg.rich_message_html(
                    payload, files=files, rtl=rtl, noautolink=noautolink
                )
            else:
                rich = _richmsg.rich_message_markdown(
                    payload, files=files, rtl=rtl, noautolink=noautolink
                )
        else:
            raise TypeError(
                "payload must be RichMarkup, RichBlocks, markup text, or a native InputRichMessage"
            )
        send_message = _types.InputBotInlineMessageRichMessage(
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
            thumb=thumb,
            content=content,
        )
        return _finalize_id(result, id)

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
        result_cls = _types.InputBotInlineResultDocument
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
        result_cls = _types.InputBotInlineResultPhoto
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
)

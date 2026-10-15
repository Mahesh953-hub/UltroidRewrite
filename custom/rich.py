from __future__ import annotations

from telethon import Button
from telethon.extensions import html as _html_parser
from telethon.extensions import markdown as _md_parser
from telethon.extensions import markdownv2 as _mdv2_parser
from telethon.tl import types
from telethon.tl.custom.message import CustomMarkdown

from .tg import (
    _is_built_button,
    _normalize_icon,
    _normalize_style,
    ikm,
    request_peer_button,
)

try:
    from telethon.extensions import markdownv3 as _mdv3

    _HAS_MDV3 = True
except ImportError:
    _mdv3 = None
    _HAS_MDV3 = False


# Named style presets
STYLE_PRESETS = {
    "default": (None, None),
    "primary": ("primary", None),
    "info": ("primary", None),
    "success": ("success", None),
    "ok": ("success", None),
    "confirm": ("success", None),
    "danger": ("danger", None),
    "error": ("danger", None),
    "warn": ("danger", None),
    "back": ("danger", None),
    "cancel": ("danger", None),
}


class RichKit:
    """Rich (styled, premium-emoji) text and button helpers.

    One instance (``rich_kit``); the module-level functions below are
    backward-compatible aliases.
    """

    def resolve_style(self, style=None, icon=None, preset=None):
        """Resolve a final ``(style, icon)`` pair from explicit values and/or a preset."""
        if preset is not None:
            pstyle, picon = STYLE_PRESETS.get(str(preset).lower(), (None, None))
            if style is None:
                style = pstyle
            if icon is None:
                icon = picon
        return _normalize_style(style), _normalize_icon(icon)

    def button(
        self,
        text,
        data=None,
        *,
        url=None,
        style=None,
        icon=None,
        preset=None,
        request=None,
        button_id=1,
        max_quantity=1,
        **peer_kwargs,
    ):
        """Build a single Telethon button with style/icon/preset support.

        Exactly one flavour is produced, in priority order:

        * ``url=...``                            → ``Button.url`` (styled)
        * ``request="user"|"chat"|"broadcast"``  → raw ``KeyboardButtonRequestPeer``
          (reply-keyboard contexts only - see :func:`custom.tg.request_peer_button`;
          extra ``RequestPeerType*`` fields may be passed as keyword arguments)
        * otherwise                              → ``Button.inline`` with ``data``

        ``preset`` names a ``STYLE_PRESETS`` entry; explicit ``style``/``icon``
        override it.
        """
        style, icon = self.resolve_style(style, icon, preset)
        if url is not None:
            return Button.url(text, url=url, style=style, icon=icon)
        if request is not None:
            return request_peer_button(
                text,
                request,
                button_id=button_id,
                max_quantity=max_quantity,
                style=style,
                icon=icon,
                **peer_kwargs,
            )
        return Button.inline(text, data=data, style=style, icon=icon)

    def rich_ikm(self, data):
        """``ikm()`` superset accepting dicts alongside tuples and built Buttons.

        Each row may contain classic ``ikm`` tuples, pre-built ``Button``/TL
        objects (passed through), or dicts unpacked into :func:`button`, e.g.::

            self.rich_ikm([
                [{"text": "Confirm", "data": "yes", "preset": "ok"},
                 {"text": "Cancel", "data": "no", "style": "danger"}],
                [("Docs", "url", "https://example.com", "primary")],
            ])

        A bare top-level tuple/dict is a single one-button row, as is a bare
        pre-built button - pre-built Telethon button objects always pass through
        untouched, detected before any dict/tuple handling.
        """
        if isinstance(data, (tuple, dict)) or _is_built_button(data):
            data = [data]

        mapped = []
        for row in data:
            if isinstance(row, (tuple, dict)) or _is_built_button(row):
                row = [row]
            mapped.append(
                [self.button(**btn) if isinstance(btn, dict) else btn for btn in row]
            )
        return ikm(mapped)

    # Premium-emoji helpers
    def _utf16_len(self, text: str) -> int:
        """Length of *text* in UTF-16 code units - the unit Telegram uses for
        entity ``offset``/``length`` (astral-plane characters count as 2)."""
        return len(text.encode("utf-16-le", "surrogatepass")) // 2

    def emoji_entity(
        self, offset: int, length: int, document_id: int
    ) -> types.MessageEntityCustomEmoji:
        """Build a raw ``MessageEntityCustomEmoji`` (offset/length in UTF-16 units)."""
        return types.MessageEntityCustomEmoji(
            offset=int(offset), length=int(length), document_id=int(document_id)
        )

    def premium_emoji(self, document_id: int, fallback: str = "⭐"):
        """Return ``(text, [MessageEntityCustomEmoji])`` for a single premium emoji."""
        return fallback, [self.emoji_entity(0, self._utf16_len(fallback), document_id)]

    def with_emojis(self, text: str, emojis: dict, *, default_fallback: str = "⭐"):
        """Replace placeholders in *text* with premium-emoji entities.

        ``emojis`` maps a placeholder substring to either a ``document_id`` or a
        ``(document_id, fallback)`` pair. Each found placeholder is swapped for
        its fallback emoji and a ``MessageEntityCustomEmoji`` is emitted with
        correct UTF-16 offsets. Missing placeholders are skipped. Entities come
        back sorted by offset.

            text, entities = self.with_emojis("Status {ok} done {sparkle}", {
                "{ok}": (5230986469815432113, "✅"),
                "{sparkle}": 5325547803936572038,
            })
            await event.reply(text, formatting_entities=entities, parse_mode=None)

        .. note::
            When combining with markdown/html parsing, parse FIRST and call
            :func:`with_emojis` on the resulting *clean* text - entity offsets
            are computed against the text as given, and parsers strip markup
            (shifting every later offset).
        """
        entities = []
        for placeholder, spec in (emojis or {}).items():
            if isinstance(spec, (tuple, list)):
                document_id, fallback = spec[0], spec[1]
            else:
                document_id, fallback = spec, default_fallback
            idx = text.find(placeholder)
            if idx < 0:
                continue
            offset = self._utf16_len(text[:idx])
            text = text[:idx] + fallback + text[idx + len(placeholder) :]
            entities.append(
                self.emoji_entity(offset, self._utf16_len(fallback), document_id)
            )
        entities.sort(key=lambda e: e.offset)
        return text, entities

    def _get_parser(self, mode):
        """Resolve *mode* to an object with ``parse``/``unparse`` (or ``None``)."""
        if mode is None:
            return None
        if hasattr(mode, "parse") and hasattr(mode, "unparse"):
            return mode
        key = str(mode).lower()
        if key in _MDV3_NAMES:
            if not _HAS_MDV3:
                raise RuntimeError(
                    "telethon.extensions.markdownv3 is not available in this Telethon build"
                )
            return RICH_MD
        if key in _CUSTOM_NAMES:
            return CustomMarkdown()
        if key in _HTML_NAMES:
            return _html_parser
        if key in _MD_NAMES:
            return _md_parser
        if key in _MDV2_NAMES:
            return _mdv2_parser.Markdown()
        raise ValueError(f"Unknown rich parse mode {mode!r}")

    def _normalize_parsed(self, value, fallback=""):
        """``pyUltroid._core.entities.normalize_parsed``, imported on first use.

        ``custom.rich`` must stay importable without bootstrapping ``pyUltroid``
        (tests/test_rich.py pins that), so the import happens inside the call.
        """
        from pyUltroid._core.entities import normalize_parsed

        return normalize_parsed(value, fallback=fallback)

    def parse_rich(self, text: str, mode="mdv3"):
        """Parse *text* with *mode* and always return a ``(text, entities)`` pair.

        Every parser reachable through :func:`_get_parser` is normalised, so a
        caller can unconditionally unpack two values. ``CustomMarkdown.parse``
        already returns a pair, but ``entities`` may be ``None`` on some paths;
        here it is always a list.
        """
        parser = self._get_parser(mode)
        if parser is None:
            return text or "", []
        return self._normalize_parsed(parser.parse(text), fallback=text or "")

    def unparse_rich(self, text: str, entities, mode="mdv3") -> str:
        """Inverse of :func:`parse_rich` - render text + entities back to markup.

        Always returns a ``str``, even when handed a ``(text, entities)`` pair in
        place of ``text`` (which is what a caller that forgot to unpack has).
        """
        text, pair_entities = self._normalize_parsed(text)
        entities = entities if entities is not None else pair_entities
        parser = self._get_parser(mode)
        if parser is None:
            return text
        return self._normalize_parsed(
            parser.unparse(text, entities or []), fallback=text
        )[0]


rich_kit = RichKit()


def resolve_style(*args, **kwargs):
    return rich_kit.resolve_style(*args, **kwargs)


def button(*args, **kwargs):
    return rich_kit.button(*args, **kwargs)


def rich_ikm(*args, **kwargs):
    return rich_kit.rich_ikm(*args, **kwargs)


def _utf16_len(*args, **kwargs):
    return rich_kit._utf16_len(*args, **kwargs)


def emoji_entity(*args, **kwargs):
    return rich_kit.emoji_entity(*args, **kwargs)


def premium_emoji(*args, **kwargs):
    return rich_kit.premium_emoji(*args, **kwargs)


def with_emojis(*args, **kwargs):
    return rich_kit.with_emojis(*args, **kwargs)


def _get_parser(*args, **kwargs):
    return rich_kit._get_parser(*args, **kwargs)


def _normalize_parsed(*args, **kwargs):
    return rich_kit._normalize_parsed(*args, **kwargs)


def parse_rich(*args, **kwargs):
    return rich_kit.parse_rich(*args, **kwargs)


def unparse_rich(*args, **kwargs):
    return rich_kit.unparse_rich(*args, **kwargs)


# Rich parse helpers
RICH_MD = _mdv3 if _HAS_MDV3 else None

_MDV3_NAMES = ("mdv3", "v3", "rich", "markdownv3")
_CUSTOM_NAMES = ("custom", "custommarkdown", "custom_markdown")
_HTML_NAMES = ("html", "htm")
_MD_NAMES = ("md", "markdown")
_MDV2_NAMES = ("v2", "mdv2", "markdownv2")


__all__ = (
    "RICH_MD",
    "STYLE_PRESETS",
    "button",
    "emoji_entity",
    "parse_rich",
    "premium_emoji",
    "request_peer_button",
    "resolve_style",
    "rich_ikm",
    "unparse_rich",
    "with_emojis",
)

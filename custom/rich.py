from __future__ import annotations

from telethon import Button

from .tg import (
    _is_built_button,
    _normalize_icon,
    _normalize_style,
    ikm,
    request_peer_button,
)

__all__ = (
    "STYLE_PRESETS",
    "RichKit",
    "button",
    "request_peer_button",
    "resolve_style",
    "rich_ikm",
    "rich_kit",
)

STYLE_PRESETS = {
    "default": (None, None),
    "neutral": (None, None),
    "unstyled": (None, None),
    "primary": ("primary", None),
    "info": ("primary", None),
    "blue": ("primary", None),
    "success": ("success", None),
    "ok": ("success", None),
    "confirm": ("success", None),
    "green": ("success", None),
    "danger": ("danger", None),
    "error": ("danger", None),
    "warn": ("danger", None),
    "red": ("danger", None),
    "back": ("primary", None),
    "cancel": ("danger", None),
}


class RichKit:
    """Build Telethon buttons with semantic styles and icons."""

    def resolve_style(self, style=None, icon=None, preset=None):
        if preset is not None:
            preset_style, preset_icon = STYLE_PRESETS.get(
                str(preset).strip().lower(), (None, None)
            )
            if style is None:
                style = preset_style
            if icon is None:
                icon = preset_icon
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
        if data is None:
            return None
        if isinstance(data, (tuple, dict)) or _is_built_button(data):
            data = [data]
        rows = []
        for row in data:
            if isinstance(row, (tuple, dict)) or _is_built_button(row):
                row = [row]
            rows.append(
                [self.button(**item) if isinstance(item, dict) else item for item in row]
            )
        return ikm(rows)


rich_kit = RichKit()


def resolve_style(*args, **kwargs):
    return rich_kit.resolve_style(*args, **kwargs)


def button(*args, **kwargs):
    return rich_kit.button(*args, **kwargs)


def rich_ikm(*args, **kwargs):
    return rich_kit.rich_ikm(*args, **kwargs)

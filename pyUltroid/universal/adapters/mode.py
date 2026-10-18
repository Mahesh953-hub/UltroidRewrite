"""Persistent command-only business/guest/normal mode adapter."""

from __future__ import annotations

import html as _html
import time
from copy import deepcopy
from enum import StrEnum
from typing import Any

from telethon import Button

from ..runtime import RuntimeContext
from ..storage import UDBStore


class BotMode(StrEnum):
    BUSINESS = "business"
    GUEST = "guest"
    NORMAL = "normal"


DEFAULT_GUEST_CONFIG = {
    "welcome_text": "Welcome to Guest Chat!",
    "article_title": "Chat with Bot",
    "article_description": "Tap to start a conversation",
    "show_buttons": True,
    "result_id_interval": 300,
}


def guest_chat_surfaces():
    """Return the guest-chat surfaces this Telethon build provides, or ``None``."""
    from telethon import functions, types

    required = (
        "UpdateBotGuestChatQuery",
        "InputBotInlineResult",
        "InputBotInlineMessageRichMessage",
        "InputRichMessageHTML",
    )
    if not all(hasattr(types, name) for name in required):
        return None
    if not hasattr(
        getattr(functions, "messages", None), "SetBotGuestChatResultRequest"
    ):
        return None
    return types, functions


class ModeStore:
    """Persist source-compatible mode state and migrate legacy guest settings."""

    def __init__(self, db: Any) -> None:
        self._mode = UDBStore(db, "UNIVERSAL_RUNTIME_MODE", BotMode.BUSINESS.value)
        self._config = UDBStore(db, "UNIVERSAL_GUEST_CONFIG", DEFAULT_GUEST_CONFIG)

    def get_mode(self) -> BotMode:
        try:
            return BotMode(self._mode.get())
        except ValueError:
            return BotMode.BUSINESS

    def set_mode(self, mode: BotMode | str) -> BotMode:
        mode = BotMode(mode)
        self._mode.set(mode.value)
        return mode

    def guest_config(self) -> dict[str, Any]:
        stored = self._config.get()
        config = deepcopy(DEFAULT_GUEST_CONFIG)
        config.update(stored)

        if "result_id_interval" not in stored and "cache_time" in stored:
            config["result_id_interval"] = stored["cache_time"]
            config.pop("cache_time", None)
            self._config.set(config)

        return config

    def update_guest_config(self, **changes: Any) -> dict[str, Any]:
        config = self.guest_config()
        for key in DEFAULT_GUEST_CONFIG:
            if key in changes:
                config[key] = changes[key]
        self._config.set(config)
        return config

    def reset_guest_config(self) -> dict[str, Any]:
        self._config.set(deepcopy(DEFAULT_GUEST_CONFIG))
        return self.guest_config()


def _interval(config: dict[str, Any]) -> int:
    try:
        return max(1, int(config["result_id_interval"]))
    except (TypeError, ValueError):
        return DEFAULT_GUEST_CONFIG["result_id_interval"]


def _mode_text(
    store: ModeStore,
    guest_available: bool,
    *,
    styled_available: bool = False,
) -> str:
    mode = store.get_mode()
    config = store.guest_config()
    status = "available" if guest_available else "unavailable"
    preview = "native RichMessage article"
    if styled_available:
        buttons_status = "styled switch-inline"
    elif guest_available:
        buttons_status = "plain switch-inline"
    else:
        buttons_status = "unavailable"
    return (
        "Universal runtime mode\n\n"
        f"Current mode: {mode.value}\n"
        f"Guest query capability: {status}\n"
        "Set mode with: .mode business | guest | normal\n"
        "Configure with: .mode buttons on|off | interval SECONDS | reset\n\n"
        f"Guest title: {config['article_title']}\n"
        f"Guest description: {config['article_description']}\n"
        f"Guest URL button: {'on' if config['show_buttons'] else 'off'}\n"
        f"Guest result-ID interval: {_interval(config)} seconds\n"
        f"Guest preview: {preview}\n"
        f"Guest buttons: {buttons_status}"
    )


def _parse_command(store: ModeStore, args: str) -> str | None:
    parts = args.strip().split(maxsplit=1)
    if not parts or parts[0].lower() in {"status", "config"}:
        return None
    action = parts[0].lower()
    if action in {mode.value for mode in BotMode}:
        store.set_mode(action)
        return None
    if action == "reset":
        store.reset_guest_config()
        return None
    if action == "buttons" and len(parts) == 2 and parts[1].lower() in {"on", "off"}:
        store.update_guest_config(show_buttons=parts[1].lower() == "on")
        return None
    if action in {"interval", "cache"} and len(parts) == 2:
        try:
            interval = max(1, int(parts[1]))
        except (TypeError, ValueError):
            return "Interval must be a whole number of seconds."
        store.update_guest_config(result_id_interval=interval)
        return None
    return "Usage: .mode [business|guest|normal|buttons on|off|interval SECONDS|reset]"


def _safe_url_markup(context: RuntimeContext, types: Any, enabled: bool):
    """Optional URL-only button; no callback buttons are emitted by this layer."""
    if not enabled:
        return None
    username = getattr(getattr(context.assistant, "me", None), "username", None)
    row_type = getattr(types, "KeyboardInlineButtonRow", None)
    url_type = getattr(types, "InlineButtonTypeUrl", None)
    inline_button_type = getattr(types, "KeyboardInlineButton", None)
    markup_type = getattr(types, "ReplyInlineMarkup", None)
    if not username or not all((row_type, url_type, inline_button_type, markup_type)):
        return None
    return markup_type(
        rows=[
            row_type(
                buttons=[
                    inline_button_type(
                        "Open bot", url_type(f"https://t.me/{username}")
                    )
                ]
            )
        ]
    )


def _switch_inline_button(text: str, query: str, *, styled: bool):
    style = icon = None
    if styled:
        try:  # the styled-button extension is optional in this adapter
            from pyUltroid._core.buttonkit import STYLE_PRESETS
        except ImportError:
            STYLE_PRESETS = {}
        style, icon = STYLE_PRESETS.get("primary", (None, None))
    try:
        return Button.switch_inline(
            text, query=query, same_peer=False, style=style, icon=icon
        )
    except TypeError:
        # Hypothetical older/narrower switch_inline signature without style/icon.
        return Button.switch_inline(text, query=query, same_peer=False)


def _rich_article_html(config: dict[str, Any]) -> str:
    """Build a minimal HTML body for the rich guest-chat article preview."""
    title = _html.escape(str(config.get("article_title", "")))
    body = _html.escape(str(config.get("welcome_text", "")))
    if title:
        return f"<h3>{title}</h3><p>{body}</p>"
    return f"<p>{body}</p>"


def _guest_reply_markup(
    context: RuntimeContext, types: Any, config: dict[str, Any], *, styled_buttons: bool
):
    if not config.get("show_buttons"):
        return None
    row_type = getattr(types, "KeyboardInlineButtonRow", None)
    markup_type = getattr(types, "ReplyInlineMarkup", None)

    if row_type is None or markup_type is None:
        return None
    rows = []
    username = getattr(getattr(context.assistant, "me", None), "username", None)
    url_type = getattr(types, "InlineButtonTypeUrl", None)
    inline_button_type = getattr(types, "KeyboardInlineButton", None)

    if username and url_type is not None and inline_button_type is not None:
        rows.append(
            row_type(
                buttons=[
                    inline_button_type(
                        "Open bot", url_type(f"https://t.me/{username}")
                    )
                ]
            )
        )
    switch_button = _switch_inline_button("Try it inline", "", styled=styled_buttons)
    if switch_button is not None:
        rows.append(row_type(buttons=[switch_button]))
    if not rows:
        return None
    return markup_type(rows=rows)


def _guest_result(
    context: RuntimeContext,
    types: Any,
    config: dict[str, Any],
    *,
    styled_buttons: bool,
):
    """Build the guest-chat inline article using native layer-230 RichMessage."""
    result_id = f"guest-{int(time.monotonic() // _interval(config))}"
    markup = _guest_reply_markup(context, types, config, styled_buttons=styled_buttons)
    send_message = types.InputBotInlineMessageRichMessage(
        rich_message=types.InputRichMessageHTML(html=_rich_article_html(config)),
        reply_markup=markup,
    )
    return types.InputBotInlineResult(
        id=result_id,
        type="article",
        title=str(config["article_title"]),
        description=str(config["article_description"]),
        send_message=send_message,
    )


class ModePlugin:
    id = "universal.mode"

    def register(self, context: RuntimeContext) -> None:
        store = ModeStore(context.db)
        surfaces = guest_chat_surfaces()
        guest_available = bool(surfaces and context.capabilities.guest_chat)
        styled_buttons = bool(guest_available and context.capabilities.styled_buttons)

        @context.dispatcher.command("mode", priority=20)
        async def mode_command(update, args: str, reply):
            error = _parse_command(store, args)
            return error or _mode_text(
                store,
                guest_available,
                styled_available=styled_buttons,
            )

        if not guest_available:
            return
        types, functions = surfaces

        @context.dispatcher.listen(types.UpdateBotGuestChatQuery, priority=20)
        async def guest_query(update):
            if store.get_mode() is not BotMode.GUEST:
                return
            config = store.guest_config()
            result = _guest_result(
                context,
                types,
                config,
                styled_buttons=styled_buttons,
            )
            await context.client(
                functions.messages.SetBotGuestChatResultRequest(
                    query_id=update.query_id,
                    result=result,
                )
            )

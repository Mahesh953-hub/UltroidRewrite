# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""Assistant-side decorators: the plugin-facing half of ``_misc``.

Everything below the three decorator factories is unchanged and must stay
that way: ``asst_cmd``, ``callback`` and ``in_pattern`` are applied 213
times across ``plugins/`` and ``assistant/``, and they are the plugin
surface (handbook rule 1). What changed is the *helpers* they used to call,
which were six module-level functions; they are now three classes with
one job each:

* :class:`ButtonMapper` - turn whatever a plugin passed as ``buttons`` into
  something :func:`custom.tg.ikm` accepts, including the fork's styled
  buttons.
* :class:`MessageFormatter` - decide whether the caller picked their own
  parse mode, and HTML-format the text when they did not.
* :class:`AssistantEventPatcher` - install the patched ``reply`` /
  ``respond`` / ``edit`` onto a per-event instance.

The three classes are independent on purpose and share no base: the button
mapping does not know about formatting, and the formatter does not know it
is called from a patched event.
"""

import inspect
import re
from traceback import format_exc
from typing import Any, ClassVar, Final

from telethon.errors import QueryIdInvalidError
from telethon.events import CallbackQuery, InlineQuery, NewMessage
from custom.tg import ikm
from custom.fmt import fmtmsg
from telethon.tl.types import InputWebDocument

from .. import LOGS, asst, udB, ultroid_bot
from ..fns import fns
from . import append_or_update, owner_and_sudos

URLS = []
STICKERS = [
    "BAADBQADmxoAAjqPkVSMlwlZke75ywI",
    "BAADBQADqBoAAqw60FUpgkQC4aR44QI",
    "BAADBQADdBgAAlO2kVcLEPj194RoNwI",
]


class Branding:
    """Assistant identity strings, resolved against the live client.

    Module import happens before any client exists (the decorator files
    load cold in tests), so the owner name and the greeting card are
    computed at call time from whoever is actually logged in - one typed
    home for the two strings the inline surfaces display.
    """

    __slots__ = ()

    @property
    def owner(self) -> str:
        bot = ultroid_bot or asst
        return getattr(bot, "full_name", None) or "Ultroid"

    @property
    def uid(self):
        return getattr(ultroid_bot, "uid", None)

    @property
    def msg(self) -> str:
        uid = self.uid
        owner = self.owner
        if uid is not None:
            owner = f"[{owner}](tg://user?id={uid})"
        return f"""
**Ultroid - UserBot**
➖➖➖➖➖➖➖➖➖➖
**Owner**: {owner}
**Support**: @TeamUltroid
➖➖➖➖➖➖➖➖➖➖
"""


#: the process-wide branding.
BRAND = Branding()

#: longest first line still treated as a heading
MAX_HEADING: Final[int] = 40
#: prefixes that mean "the caller already produced HTML"
HTML_PREFIXES: Final[tuple[str, ...]] = ("<b>", "<div", "<code>")
#: style probes that identify a fork "styled" button
STYLE_FIELDS: Final[tuple[str, ...]] = ("bg_primary", "bg_danger", "bg_success", "icon")


class ButtonMapper:
    """Normalises plugin-supplied ``buttons`` into an ``ikm`` result.

    Plugins pass buttons in three shapes: a flat list, a list of rows, or a
    single button object - and each button may be a tuple (already valid),
    a Telethon button (needs a style inferred from its label), or a fork
    styled button (already valid, pass through).
    """

    #: label fragments that imply a danger style
    DANGER_HINTS: ClassVar[tuple[str, ...]] = ("back", "«", "cancel")
    #: label fragments that imply a success style
    SUCCESS_HINTS: ClassVar[tuple[str, ...]] = ("on", "approve")

    def __init__(self, build: Any = None) -> None:
        self._build = build if build is not None else ikm

    # -- probing -----------------------------------------------------------

    @staticmethod
    def is_styled(button: Any) -> bool:
        """True for a fork styled button (it must not be rebuilt)."""
        style = getattr(button, "style", None)
        if style is None:
            return False
        return any(getattr(style, field, None) for field in STYLE_FIELDS)

    @classmethod
    def style_for(cls, text: str) -> str:
        """Infer a style from the button label."""
        lowered = text.lower()
        if any(hint in lowered for hint in cls.DANGER_HINTS):
            return "danger"
        if any(hint in lowered for hint in cls.SUCCESS_HINTS):
            return "success"
        return "primary"

    # -- normalising -------------------------------------------------------

    @staticmethod
    def as_rows(raw: Any) -> list[list[Any]]:
        """Coerce any accepted shape into a list of rows."""
        if not isinstance(raw, list):
            return [[raw]]
        if raw and not isinstance(raw[0], list):
            return [raw]
        return raw

    def convert(self, button: Any) -> Any:
        """One button -> one entry usable by :func:`ikm`."""
        if isinstance(button, tuple):
            return button
        if not (hasattr(button, "text") and hasattr(button, "data")):
            return button
        if self.is_styled(button):
            return button
        text = button.text
        style = self.style_for(text)
        if getattr(button, "url", None):
            return (text, "url", button.url, style)
        return (text, button.data, style)

    def map(self, raw: Any) -> Any:
        """The whole job. Returns ``None`` for empty input, as before."""
        if not raw:
            return None
        return self._build(
            [[self.convert(button) for button in row] for row in self.as_rows(raw)]
        )


class MessageFormatter:
    """Decides whether to HTML-format a text, and formats it.

    The two questions are separate on purpose: a plugin that passes its own
    ``parse_mode`` is left completely alone (buttons are still mapped, but
    the text is untouched), which is the behaviour the old pair of functions
    had and which plugins depend on.
    """

    def __init__(self, build: Any = None) -> None:
        self._build = build if build is not None else fmtmsg

    @staticmethod
    def caller_chose_parse_mode(kwargs: dict) -> bool:
        """True when the caller set a non-HTML parse mode themselves."""
        return "parse_mode" in kwargs and kwargs["parse_mode"] != "html"

    @staticmethod
    def already_html(text: str) -> bool:
        return text.startswith(HTML_PREFIXES)

    def format(self, text: Any, heading_default: str = "Assistant") -> Any:
        """Promote the first line to a heading and wrap the rest.

        A first line longer than :data:`MAX_HEADING` is assumed not to be a
        heading: the whole text becomes the body and the default heading is
        used. Already-HTML text is returned untouched.
        """
        if not text:
            return text
        if self.already_html(text):
            return text
        lines = text.strip().split("\n", 1)
        head = lines[0].strip().replace("~", "").replace(":", "").strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        if len(head) > MAX_HEADING:
            return self._build(heading_default, msgs=text)
        return self._build(head, msgs=body)


class AssistantEventPatcher:
    """Installs HTML-aware ``reply``/``respond``/``edit`` on one event.

    Patching is per-event *instance*, not per class, so two concurrent
    commands cannot leak formatting into each other. Each patched method
    keeps the original's signature and is only installed when the original
    attribute actually existed.
    """

    #: the three methods this patcher wraps, in install order
    METHODS: ClassVar[tuple[str, ...]] = ("reply", "respond", "edit")

    def __init__(
        self,
        buttons: "ButtonMapper | None" = None,
        formatter: "MessageFormatter | None" = None,
    ) -> None:
        self.buttons = buttons if buttons is not None else ButtonMapper()
        self.formatter = formatter if formatter is not None else MessageFormatter()

    def __call__(self, event: Any, heading_default: str = "Assistant") -> Any:
        """Patch *event* in place and return it."""
        for name in self.METHODS:
            original = getattr(event, name, None)
            if original:
                setattr(event, name, self._wrap(original, heading_default))
        return event

    def _wrap(self, original: Any, heading_default: str):
        buttons, formatter = self.buttons, self.formatter

        async def patched(text=None, *args, **kwargs):
            if "buttons" in kwargs:
                kwargs["buttons"] = buttons.map(kwargs["buttons"])
            if formatter.caller_chose_parse_mode(kwargs):
                return await original(text, *args, **kwargs)
            kwargs["parse_mode"] = "html"
            return await original(
                formatter.format(text, heading_default), *args, **kwargs
            )

        return patched


#: the shared patcher used by the decorators below
PATCHER = AssistantEventPatcher()
#: shared helpers, so the decorators do not rebuild them per call
BUTTONS = ButtonMapper()
FORMATTER = MessageFormatter()


def get_bttns():
    return ikm(
        [
            [
                (
                    "repo",
                    "url",
                    "https://github.com/TeamUltroid/Ultroid",
                    "primary",
                ),
                ("Support", "url", "https://t.me/UltroidSupportChat", "blue"),
            ],
            [("help?", "switch", "help", "red"), ("telethon?", "switch", "tl", "red")],
            [("copy", "copy", "im gay!:)", "green")],
            [("me?", "user", ultroid_bot.uid, "primary"), ("hehe", "switch", "test_1")],
        ]
    )


def _button_is_styled(btn):
    return ButtonMapper.is_styled(btn)


def _map_buttons_to_ikm(raw_buttons):
    return ButtonMapper().map(raw_buttons)


def _caller_chose_parse_mode(kwargs):
    return MessageFormatter.caller_chose_parse_mode(kwargs)


def _format_assistant_message(text, heading_default="Assistant"):
    return MessageFormatter().format(text, heading_default)


def _patch_assistant_event(event, heading_default="Assistant"):
    return AssistantEventPatcher()(event, heading_default)


# ── the plugin-facing decorators (frozen surface) ─────────────────────
def asst_cmd(pattern=None, load=None, owner=False, **kwargs):
    """Decorator for assistant's command"""
    name = inspect.stack()[1].filename.split("/")[-1].replace(".py", "")
    kwargs["forwards"] = False

    def ult(func):
        if pattern:
            kwargs["pattern"] = re.compile(rf"^/{pattern}(?:@\w+)?(?:[ \t]|$)")

        async def handler(event):
            if owner and event.sender_id not in owner_and_sudos():
                return
            try:
                _patch_assistant_event(event, "Assistant Command")
                await func(event)
            except Exception as er:
                LOGS.exception(er)

        asst.add_event_handler(handler, NewMessage(**kwargs))
        if load is not None:
            append_or_update(load, func, name, kwargs)

    return ult


def callback(data=None, from_users=[], admins=False, owner=False, **kwargs):
    """Assistant's callback decorator"""
    if "me" in from_users:
        from_users.remove("me")
        from_users.append(ultroid_bot.uid)

    def ultr(func):
        async def wrapper(event):
            if admins and not await fns.admin_check(event):
                return
            if from_users and event.sender_id not in from_users:
                return await event.answer("Not for You!", alert=True)
            if owner and event.sender_id not in owner_and_sudos():
                return await event.answer("Not allowed!")
            try:
                _patch_assistant_event(event, "Settings Callback")
                await func(event)
            except Exception as er:
                LOGS.exception(er)

        asst.add_event_handler(wrapper, CallbackQuery(data=data, **kwargs))

    return ultr


def in_pattern(pattern=None, owner=False, **kwargs):
    """Assistant's inline decorator."""

    def don(func):
        async def wrapper(event):
            if owner and event.sender_id not in owner_and_sudos():
                res = [
                    await event.builder.article(
                        title="Ultroid Userbot",
                        url="https://t.me/TeamUltroid",
                        description="(c) TeamUltroid",
                        text=BRAND.msg,
                        thumb=InputWebDocument(
                            "https://graph.org/file/dde85d441fa051a0d7d1d.jpg",
                            0,
                            "image/jpeg",
                            [],
                        ),
                        buttons=get_bttns(),
                    )
                ]
                return await event.answer(
                    res,
                    switch_pm=f"🤖: Assistant of {BRAND.owner}",
                    switch_pm_param="start",
                )
            try:
                await func(event)
            except QueryIdInvalidError:
                pass
            except Exception as er:
                err = format_exc()

                def error_text():
                    return f"**#ERROR #INLINE**\n\nQuery: `{asst.me.username} {pattern}`\n\n**Traceback:**\n`{format_exc()}`"

                LOGS.exception(er)
                try:
                    await event.answer(
                        [
                            await event.builder.article(
                                title="Unhandled Exception has Occured!",
                                text=error_text(),
                                buttons=ikm(
                                    [
                                        [
                                            (
                                                "Report",
                                                "url",
                                                "https://t.me/UltroidSupportChat",
                                                "green",
                                            )
                                        ]
                                    ]
                                ),
                            )
                        ]
                    )
                except QueryIdInvalidError:
                    LOGS.exception(err)
                except Exception as er:
                    LOGS.exception(er)
                    await asst.send_message(udB.get_key("LOG_CHANNEL"), error_text())

        asst.add_event_handler(wrapper, InlineQuery(pattern=pattern, **kwargs))

    return don

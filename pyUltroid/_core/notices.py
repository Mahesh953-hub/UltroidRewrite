"""Typed, class-based handling for the three runtime-notice defects.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import html as _html
import re as _re
from dataclasses import dataclass
from enum import Enum
from typing import Any

__all__ = [
    "EDIT_UNSUPPORTED",
    "ErrorNotice",
    "ErrorSeverity",
    "MessageBody",
    "NoticeEdit",
    "ParseMode",
    "as_text",
    "command_error_messages",
    "error_caption",
    "sanitize_error_text",
]


_CREDENTIAL_ASSIGNMENT = _re.compile(
    r"(?i)\b(api[_-]?(?:hash|id|key)|bot[_-]?token|token|session(?:[_-]?string)?|password|secret)\b(\s*[:=]\s*)(?:['\"]([^'\"]*)['\"]|([^\s,;]+))"
)
_TELEGRAM_TOKEN = _re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_BEARER = _re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*")
_MAPPING_CREDENTIAL = _re.compile(
    r"(?i)(['\"](?:api[_-]?(?:hash|id|key)|bot[_-]?token|token|session(?:[_-]?string)?|password|secret)['\"]\s*:\s*)['\"][^'\"]*['\"]"
)
_SESSION_BLOCK = _re.compile(
    r"(?is)(\b(?:session(?:[_ -]?string)?|string[_ -]?session)\b\s*[:=]\s*)(['\"]?)([A-Za-z0-9+/=_-]{48,})(['\"]?)"
)


def sanitize_error_text(value: Any) -> str:
    """Redact registered secrets and recognizable credentials from diagnostic text."""
    text = as_text(value)
    try:
        from ..webserver.security import REDACTOR

        text = REDACTOR.redact(text)
    except Exception:  # noqa: BLE001, S110 - failure notices must survive without logging credential-bearing exceptions
        pass
    text = _BEARER.sub(r"\1[REDACTED]", text)
    text = _MAPPING_CREDENTIAL.sub(r"\1'[REDACTED]'", text)
    text = _SESSION_BLOCK.sub(r"\1[REDACTED]", text)
    text = _TELEGRAM_TOKEN.sub("[REDACTED]", text)
    return _CREDENTIAL_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", text
    )


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _fit_preformatted(text: str, budget: int, *, marker: bool = False) -> str:
    """Escape text into a complete HTML pre block within Telegram's UTF-16 limit."""
    safe = sanitize_error_text(text)
    suffix = "…" if marker else ""
    opening, closing = "<pre>", "</pre>"
    fixed = _utf16_length(opening + closing + suffix)
    out = []
    used = 0
    consumed = 0
    for char in safe:
        escaped = _html.escape(char, quote=False)
        width = _utf16_length(escaped)
        if used + width + fixed > budget:
            break
        out.append(escaped)
        used += width
        consumed += 1
    truncated = consumed < len(safe)
    if truncated and not marker:
        return _fit_preformatted(safe, budget, marker=True)
    return opening + "".join(out) + ("…" if truncated else "") + closing


def error_caption(error: Any, max_chars: int = 1000) -> str:
    """Build a bounded image/log caption with escaped, sanitized exception text."""
    prefix = "<b>Ultroid Client Error:</b>\n"
    budget = max(0, max_chars - _utf16_length(prefix))
    return prefix + _fit_preformatted(as_text(error), budget)


def command_error_messages(trigger: Any, error: Any, link: Any = None) -> list[str]:
    """Return HTML notices carrying all sanitized exception text in 4096-unit chunks."""
    trigger_text = sanitize_error_text(as_text(trigger))
    error_text = sanitize_error_text(as_text(error))
    reference = (f'<a href="{_html.escape(as_text(link), quote=True)}">'
                "[see in the logs channel]</a>" if link else "[see in the logs channel]")
    prefix = "<b>Command failed</b>\n" + _fit_preformatted(trigger_text[:160], 300)
    first_fixed = prefix + "\n<pre></pre>\n" + reference
    first_budget = 4096 - _utf16_length(first_fixed)
    chunks: list[str] = []
    remaining = error_text
    while remaining:
        budget = first_budget if not chunks else 4096 - _utf16_length("<pre></pre>")
        consumed = 0
        encoded_text = ""
        while consumed < len(remaining):
            item = _html.escape(remaining[consumed], quote=False)
            if _utf16_length(encoded_text + item) > budget:
                break
            encoded_text += item
            consumed += 1
        if not consumed:
            raise ValueError("Telegram message budget cannot fit escaped character")
        chunks.append(encoded_text)
        remaining = remaining[consumed:]
    if not chunks:
        chunks = [""]
    first = prefix + "\n<pre>" + chunks[0] + "</pre>\n" + reference
    return [first] + ["<pre>" + chunk + "</pre>" for chunk in chunks[1:]]


class ParseMode(str, Enum):
    """The parse modes this repository's notices are allowed to use.

    The client is constructed with ``parse_mode = "v2"`` (see
    ``pyUltroid/startup/BaseClient.py``), which maps to the fork's
    ``telethon.extensions.markdownv2.Markdown``. Hand-built HTML therefore has
    to be sent with an explicit ``parse_mode="html"``, or the parser will
    treat the tags as literal text.
    """

    V2 = "v2"
    HTML = "html"
    NONE = ""

    @classmethod
    def coerce(cls, value: Any) -> "ParseMode":
        """Accept a member, its value, or an arbitrary string."""
        if isinstance(value, cls):
            return value
        text = str(value or "").strip().lower()
        for member in cls:
            if member.value == text:
                return member
        raise ValueError(f"unsupported parse mode: {value!r}")


def as_text(value: Any) -> str:
    """Coerce any message-body-ish value to plain ``str``.

    Handles the three shapes seen in this codebase:

    * a plain string - returned unchanged;
    * a ``(text, entities)`` tuple as produced by ``combine()`` in
      ``custom/entities.py`` and by the fork's rich-text handling -
      only the text half is taken, because the caller asking for text cannot
      use the entities;
    * a Telethon object exposing ``.text`` (``TextWithEntities`` and friends).

    The last case is what caused ``AttributeError: 'tuple' object has no
    attribute 'split'``: the tuple was handed straight to ``str.split()``.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (tuple, list)):
        # (text, entities) - possibly an empty pair.
        return as_text(value[0]) if value else ""
    inner = getattr(value, "text", None)
    if isinstance(inner, str):
        return inner
    if inner is not None and inner is not value:
        # e.g. TextWithEntities(text=..., entities=...)
        return as_text(inner)
    # Telethon rich-message objects (InputRichMessageHTML and friends) carry
    # their body on a per-variant field. Without this, str() would yield a
    # repr() like "InputRichMessageHTML(html='<b>x</b>', rtl=None, ...)",
    # which parses cleanly but tells the user nothing.
    for attr in _RICH_TEXT_FIELDS:
        body = getattr(value, attr, None)
        if isinstance(body, str) and body:
            return body
    return str(value)


#: Fields a Telethon rich-message object may use for its body, in the order
#: the API declares them.
_RICH_TEXT_FIELDS = ("html", "text", "markdown")


@dataclass(frozen=True)
class MessageBody:
    """A normalised message body: text plus its formatting entities.

    Constructing one is the *only* supported way to hand text plus entities
    to Telethon. ``parse_mode`` is forced to ``None`` because supplying both
    entities and a parse mode is ambiguous - Telethon documents that entities
    win, but passing a parser additionally runs it over text that is already
    final, which can corrupt it.
    """

    text: str
    entities: tuple[Any, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "text", as_text(self.text))
        object.__setattr__(self, "entities", tuple(self.entities or ()))

    @classmethod
    def of(cls, body: Any) -> "MessageBody":
        """Build from a string, a ``(text, entities)`` pair, or a MessageBody."""
        if isinstance(body, cls):
            return body
        if isinstance(body, (tuple, list)) and len(body) == 2:
            first, second = body
            if isinstance(second, (list, tuple)):
                return cls(as_text(first), tuple(second))
        return cls(as_text(body))

    @property
    def has_entities(self) -> bool:
        return bool(self.entities)

    def send_kwargs(self) -> dict[str, Any]:
        """Kwargs for ``send_message``/``edit``."""
        if not self.entities:
            return {"parse_mode": ParseMode.V2.value}
        return {"formatting_entities": list(self.entities), "parse_mode": None}


class EDIT_UNSUPPORTED(RuntimeError):
    """Raised when a notice that must edit an existing message cannot.

    This exists so a caller can never silently downgrade "edit" into "send a
    new message". That downgrade is exactly the ``.ping`` defect: the
    placeholder stayed on screen and the result arrived underneath it.
    """


@dataclass
class NoticeEdit:
    """An in-place notice update bound to one message.

    Every method edits the bound message. Nothing here sends. If the edit
    cannot be performed the failure is raised as :class:`EDIT_UNSUPPORTED`
    rather than being papered over with a fresh message.
    """

    message: Any
    parse_mode: ParseMode = ParseMode.V2

    def __post_init__(self) -> None:
        self.parse_mode = ParseMode.coerce(self.parse_mode)

    # -- payload coercion ------------------------------------------------

    def _kwargs(self, extra: dict[str, Any]) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"parse_mode": self.parse_mode.value}
        kwargs.update(extra)
        return kwargs

    async def edit(self, body: Any, **extra: Any) -> Any:
        """Edit the bound message with *body*.

        Raises :class:`EDIT_UNSUPPORTED` if the underlying edit fails, so the
        caller decides what to do instead of a second message appearing.
        """
        rendered = MessageBody.of(body)
        editor = getattr(self.message, "edit", None)
        if editor is None:
            raise EDIT_UNSUPPORTED(
                f"{type(self.message).__name__} has no edit() to update in place"
            )
        try:
            return await editor(
                rendered.text, **self._kwargs(rendered.send_kwargs()), **extra
            )
        except EDIT_UNSUPPORTED:
            raise
        except Exception as exc:  # noqa: BLE001 - surfaced to the caller
            raise EDIT_UNSUPPORTED(
                f"could not edit the existing notice: {type(exc).__name__}: {exc}"
            ) from exc

    async def try_edit(self, body: Any, **extra: Any) -> bool:
        """Best-effort edit. Returns whether it succeeded.

        The one place a failure may be swallowed, and only for callers that
        have explicitly accepted a missing update.
        """
        try:
            await self.edit(body, **extra)
        except EDIT_UNSUPPORTED:
            return False
        return True


class ErrorSeverity(str, Enum):
    """How loudly a failure should be reported to the user."""

    #: Malformed input the user can fix - e.g. a Python SyntaxError.
    USER = "user"
    #: A dependency or network problem; not the user's fault.
    ENVIRONMENT = "environment"
    #: Anything else.
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ErrorNotice:
    """A user-facing failure summary, kept separate from the full traceback.

    The ``.evl`` defect was that the *whole* generated card went to the
    channel while the user was left with ``[An error occurred]``. This class
    enforces the split:

    * :attr:`summary` - one line, always shown in the chat;
    * :attr:`detail` - the collapsed reason, shown in the chat;
    * :attr:`traceback_text` - the full traceback, for the log channel only.

    Constructing an instance from a traceback summarises the exception name
    and message, so a ``SyntaxError`` reads as such instead of as a wall of
    frames.
    """

    summary: str
    detail: str = ""
    traceback_text: str = ""
    severity: ErrorSeverity = ErrorSeverity.UNKNOWN

    @classmethod
    def from_exception(
        cls,
        exc: BaseException,
        *,
        what: str = "run that",
        traceback_text: str = "",
    ) -> "ErrorNotice":
        """Summarise *exc* into a short, actionable notice."""
        name = type(exc).__name__
        message = _html.unescape(str(exc)).strip()
        if isinstance(exc, SyntaxError):
            severity = ErrorSeverity.USER
            where = f"line {exc.lineno}" if exc.lineno else "the input"
            detail = (
                f"{name} at {where}: invalid syntax. "
                "Check for a missing colon, bracket or comma."
            )
        elif isinstance(exc, (TypeError, AttributeError, ValueError, KeyError)):
            severity = ErrorSeverity.USER
            detail = f"{name}: {message}"
        else:
            severity = ErrorSeverity.UNKNOWN
            detail = f"{name}: {message}" if message else name

        return cls(
            summary=f"Couldn't {what}",
            detail=detail,
            traceback_text=traceback_text or f"{name}: {message}",
            severity=severity,
        )

    @classmethod
    def from_traceback(
        cls,
        traceback_text: str,
        *,
        what: str = "run that",
        fallback: str = "unknown error",
    ) -> "ErrorNotice":
        """Build a notice from a formatted traceback string.

        The last meaningful line carries the exception; the frames above it
        are noise in a chat message.
        """
        lines = [ln.rstrip() for ln in (traceback_text or "").splitlines() if ln.strip()]
        if not lines:
            return cls(summary=f"Couldn't {what}", detail=fallback)

        tail = lines[-1].strip()
        name = tail.split(":", 1)[0].strip() if ":" in tail else tail
        severity = (
            ErrorSeverity.USER
            if name in {"SyntaxError", "IndentationError", "TypeError",
                        "AttributeError", "ValueError", "KeyError", "NameError"}
            else ErrorSeverity.UNKNOWN
        )
        return cls(
            summary=f"Couldn't {what}",
            detail=tail,
            traceback_text="\n".join(lines),
            severity=severity,
        )

    @property
    def is_user_error(self) -> bool:
        return self.severity is ErrorSeverity.USER

    def chat_text(self, limit: int = 300) -> str:
        """The message shown in the chat - short, and never a full traceback."""
        detail = self.detail
        if limit and len(detail) > limit:
            detail = detail[: max(0, limit - 1)] + "…"
        return f"**{_md(self.summary)}**\n\n  `Error` : `{_md(detail)}`"

    def log_text(self, limit: int = 2000) -> str:
        """The message sent to the log channel - may include the traceback."""
        parts = [f"❌ {_html.escape(self.summary)}"]
        if self.detail:
            parts.append(f"Error: {_html.escape(self.detail)}")
        if self.traceback_text:
            body = self.traceback_text[:limit]
            parts.append(f"<pre language='python'>{_html.escape(body)}</pre>")
        return "\n\n".join(parts)


#: Characters the fork's v2 parser drops or treats as a span delimiter.
#: Kept in sync with ``vc/_messages.py``; both must escape, not backslash.
_MD_ESCAPES = {
    "&": "&amp;",
    "<": "&lt;",
    "`": "&#96;",
    "*": "&#42;",
    "_": "&#95;",
    "~": "&#126;",
    "|": "&#124;",
    "[": "&#91;",
    "]": "&#93;",
}


def _md(value: Any) -> str:
    """Escape for the v2 parse mode (entities, not backslashes)."""
    return "".join(_MD_ESCAPES.get(ch, ch) for ch in as_text(value))

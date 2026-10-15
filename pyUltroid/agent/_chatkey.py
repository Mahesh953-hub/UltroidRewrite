"""Structural, type-stable keys for agent conversation scopes.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

import enum
import re
from typing import Any, Dict, Generic, Optional, Sequence, Tuple, Type, TypeVar

__all__ = [
    "CATEGORY_GROUP",
    "CATEGORY_SESSION",
    "CATEGORY_USER",
    "CHAT_ID_PATTERN",
    "ChatKeyCatalog",
    "ChatKey",
    "ChatKeyCategory",
    "DM_SESSION_DELIMITER",
    "DEFAULT_CHATKEY_REGISTRY",
    "DmSessionKey",
    "ChatKeyRegistry",
    "InvalidKeyFormat",
    "ChatKeyFormatter",
    "ChatKeyNormalizer",
    "NumericChatKey",
    "OpaqueStringKey",
    "build_chat_key",
    "normalize_chat_id",
]

# ── sentinels ──────────────────────────────────────────────────────────────

#: Separator between the ``dm`` prefix and its two numeric components.
DM_SESSION_DELIMITER: str = ":"

#: Canonical shape of a DM session key: ``dm:<user_id>:<anchor>``.
CHAT_ID_PATTERN: re.Pattern[str] = re.compile(r"^dm:(\d+):(\d+)$")

#: Category identifiers carried on every :class:`ChatKey`.
CATEGORY_GROUP: str = "group"
CATEGORY_USER: str = "user"
CATEGORY_SESSION: str = "dm-session"


# ── failure types ──────────────────────────────────────────────────────────


class InvalidKeyFormat(ValueError):
    """A string key matched its category's prefix but not its full shape.

    Raised by :meth:`ChatKeyFormatter.parse` so a malformed value is rejected at
    the boundary instead of being silently accepted and misrouted. Distinct
    from :class:`TypeError`: this is a *content* problem, not a *kind* one.
    """


# ── concrete key types ────────────────────────────────────────────────────


T = TypeVar("T")


class ChatKey(Generic[T]):
    """Immutable, category-aware conversation-scope identifier.

    Every ``chat_id``-consuming site takes a :class:`ChatKey` rather than a
    bare ``int`` or ``str``. The key discovers its own category at
    construction and never changes it, so downstream code branches on
    ``category`` without re-parsing the value.
    """

    __slots__ = ("_original", "_canonical", "_category")

    def __init__(self, original: Any, canonical: str, category: str) -> None:
        self._original: Any = original
        self._canonical: str = canonical
        self._category: str = category

    @property
    def original(self) -> Any:
        """The unconverted value the key was built from."""
        return self._original

    @property
    def value(self) -> str:
        """Canonical string form - the safe dict/storage key."""
        return self._canonical

    @property
    def category(self) -> str:
        return self._category

    def is_session(self) -> bool:
        return self._category == CATEGORY_SESSION

    def is_group(self) -> bool:
        return self._category == CATEGORY_GROUP

    def is_user(self) -> bool:
        return self._category == CATEGORY_USER

    def to_dict_key(self) -> str:
        """The form to use as an in-memory ``dict`` key."""
        return self._canonical

    def __str__(self) -> str:
        return self._canonical

    def __repr__(self) -> str:  # pragma: no cover - diagnostics
        return f"<ChatKey category={self._category} value={self._canonical!r}>"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, ChatKey):
            return NotImplemented
        return self._canonical == other._canonical

    def __hash__(self) -> int:
        return hash(self._canonical)


class NumericChatKey(ChatKey[int]):
    """A plain integer Telegram chat/sender id - the common case."""

    __slots__ = ("_as_int",)

    def __init__(self, original: Any) -> None:
        as_int = int(original)
        canonical = str(as_int)
        super().__init__(as_int, canonical, CATEGORY_GROUP)
        self._as_int: int = as_int

    @property
    def user_id(self) -> int:
        """The numeric id, for callers that genuinely need ``int`` semantics."""
        return self._as_int

    def __int__(self) -> int:
        return self._as_int


class DmSessionKey(ChatKey[str]):
    """A ``dm:<user_id>:<anchor>`` session key - one per reply thread."""

    __slots__ = ("_user_id", "_anchor")

    def __init__(self, user_id: int, anchor: int) -> None:
        serialized = (
            f"dm{DM_SESSION_DELIMITER}{int(user_id)}"
            f"{DM_SESSION_DELIMITER}{int(anchor)}"
        )
        super().__init__(serialized, serialized, CATEGORY_SESSION)
        self._user_id: int = int(user_id)
        self._anchor: int = int(anchor)

    @property
    def user_id(self) -> int:
        return self._user_id

    @property
    def anchor(self) -> int:
        return self._anchor


class OpaqueStringKey(ChatKey[str]):
    """A string key nobody else claimed - carried verbatim, never coerced."""

    __slots__ = ()

    def __init__(self, original: Any) -> None:
        text = str(original)
        super().__init__(text, text, CATEGORY_USER)


# ── formatters ─────────────────────────────────────────────────────────────


def _looks_like_int(text: str) -> bool:
    """True when *text* parses as an integer id.

    Guarded so a stray sign or whitespace never yields a false positive; a
    chat id is a bare run of digits in the Telegram schema.
    """
    if not text:
        return False
    return text.lstrip("+-").isdigit()


class ChatKeyFormatter:
    """Base class for the strategy objects owning one key category's round trip.

    Subclasses bind, via keyword arguments to ``__init_subclass__``, both the
    category string ``category`` and the :class:`ChatKey` subclass
    ``key_type`` they render. That binding is what lets
    :class:`ChatKeyRegistry` dispatch :meth:`ChatKeyRegistry.canonical`
    by *object identity* rather than by a string lookup a typo could silently
    misroute.

    The base class is public; the concrete formatters are private (prefixed
    with ``_``) so the extension point is explicit rather than accidental.
    """

    key_type: Type[ChatKey]
    category: str

    def __init_subclass__(cls, *, category: str, key_type: Type[ChatKey]) -> None:
        super().__init_subclass__()
        cls.category = category
        cls.key_type = key_type

    def canonical(self, key: ChatKey) -> str:
        """Return the stable string form used for dict keys and storage."""
        raise NotImplementedError

    def parse(self, text: str) -> Optional[ChatKey]:
        """Build a key from *text*, or return ``None`` if it is not ours.

        Returning ``None`` (rather than raising) for a foreign string lets the
        registry probe formatters in order without try/except at each hop.
        A string that matches this formatter's prefix but not its full shape
        is :class:`InvalidKeyFormat`, because accepting it would misroute
        updates.
        """
        raise NotImplementedError


class _NumericFormatter(ChatKeyFormatter, category=CATEGORY_GROUP, key_type=NumericChatKey):
    """Formatter for plain integer Telegram chat ids."""

    def canonical(self, key: ChatKey) -> str:
        return str(int(key.original))

    def parse(self, text: str) -> Optional[ChatKey]:
        if _looks_like_int(text):
            return NumericChatKey(int(text))
        return None


class _SessionFormatter(ChatKeyFormatter, category=CATEGORY_SESSION, key_type=DmSessionKey):
    """Formatter for ``dm:<user_id>:<anchor>`` session keys."""

    def canonical(self, key: ChatKey) -> str:
        assert isinstance(key, DmSessionKey)
        return (
            f"dm{DM_SESSION_DELIMITER}{key.user_id}"
            f"{DM_SESSION_DELIMITER}{key.anchor}"
        )

    def parse(self, text: str) -> Optional[ChatKey]:
        if not text.startswith("dm:"):
            return None
        match = CHAT_ID_PATTERN.match(text)
        if match is None:
            raise InvalidKeyFormat(
                f"DM session key {text!r} has non-numeric user_id or anchor"
            )
        user_id, anchor = (int(group) for group in match.groups())
        return DmSessionKey(user_id=user_id, anchor=anchor)


class _OpaqueFormatter(ChatKeyFormatter, category=CATEGORY_USER, key_type=OpaqueStringKey):
    """Fallback for structurally valid strings nobody else claimed."""

    def canonical(self, key: ChatKey) -> str:
        return str(key.original)

    def parse(self, text: str) -> Optional[ChatKey]:
        # A fallback claims everything that is left. Returning ``None`` for
        # an empty string prevents an empty-chat-key from shadowing a real
        # private-chat session.
        if not text:
            return None
        return OpaqueStringKey(text)


# A catalog mirrors the registry but keyed by formatter name, for callers
# that want to ask "what shapes are supported" without constructing a key.
ChatKeyCatalog: Dict[str, ChatKeyFormatter] = {
    fmt.category: fmt
    for fmt in (_NumericFormatter(), _SessionFormatter(), _OpaqueFormatter())
}


# ── registry ───────────────────────────────────────────────────────────────


class ChatKeyRegistry:
    """Ordered collection of :class:`ChatKeyFormatter` strategies.

    ``probe`` returns the first formatter whose parser claims a string. The
    dispatch for :meth:`canonical` is by *object identity*: each formatter
    is bound to its :class:`ChatKey` subclass at class-definition time, and
    the registry holds a reverse map from key type to formatter. A typo in
    a key class therefore raises at the call site instead of silently
    rendering with the wrong format.
    """

    def __init__(self, formatters: Sequence[ChatKeyFormatter]) -> None:
        self._ordered: Tuple[ChatKeyFormatter, ...] = tuple(formatters)
        self._by_category: Dict[str, ChatKeyFormatter] = {
            fmt.category: fmt for fmt in formatters
        }
        self._by_key_type: Dict[type, ChatKeyFormatter] = {
            fmt.key_type: fmt for fmt in formatters
        }

    @property
    def ordered(self) -> Tuple[ChatKeyFormatter, ...]:
        return self._ordered

    def probe(self, text: str) -> Optional[ChatKeyFormatter]:
        """Return the first formatter that claims *text*.

        Raises:
            InvalidKeyFormat: a formatter recognised *text*'s prefix but its
                full shape did not match - the key is malformed, not unknown.
        """
        for fmt in self._ordered:
            try:
                claim = fmt.parse(text)
            except InvalidKeyFormat:
                # Surface immediately so ``dm:garbage:1`` is not silently
                # swallowed by the catch-all formatter and misrouted.
                raise
            if claim is not None:
                return fmt
        return None

    def for_category(self, category: str) -> ChatKeyFormatter:
        """Look up a formatter by its declared category string."""
        try:
            return self._by_category[category]
        except KeyError:
            raise InvalidKeyFormat(
                f"no formatter registered for category {category!r}"
            )

    def formatter_for(self, key: ChatKey) -> ChatKeyFormatter:
        """Return the formatter bound to *key*'s concrete class."""
        try:
            return self._by_key_type[type(key)]
        except KeyError:
            raise InvalidKeyFormat(
                f"no formatter bound to key type {type(key).__name__!r}"
            )

    def canonical(self, key: ChatKey) -> str:
        """Render *key* via the formatter bound to its concrete class."""
        return self.formatter_for(key).canonical(key)


#: Default chain: numeric ids, then DM sessions, then catch-all string.
DEFAULT_CHATKEY_REGISTRY: ChatKeyRegistry = ChatKeyRegistry(
    [_NumericFormatter(), _SessionFormatter(), _OpaqueFormatter()]
)


# ── normalizer ────────────────────────────────────────────────────────────


class ChatKeyNormalizer:
    """Turns a raw ``chat_id`` into a :class:`ChatKey`.

    Holds a :class:`ChatKeyRegistry` so the strategy chain is swappable -
    tests can construct a normalizer over a restricted registry to assert
    that rejection paths fire.
    """

    def __init__(self, registry: Optional[ChatKeyRegistry] = None) -> None:
        self._registry: ChatKeyRegistry = registry or DEFAULT_CHATKEY_REGISTRY

    @property
    def registry(self) -> ChatKeyRegistry:
        return self._registry

    def normalize(self, chat_id: Any) -> ChatKey:
        """Build a :class:`ChatKey` from anything a plugin or DM flow passes.

        Dispatch order:

        1. ``None`` -> :class:`InvalidKeyFormat` (a None id is a caller
           bug, not a new key shape).
        2. An existing :class:`ChatKey` is returned untouched (idempotent).
        3. A ``type`` reaching here is almost always a mistaken ``listen=``
           passed to the wrong parameter; reject it loudly.
        4. ``int`` -> :class:`NumericChatKey`.
        5. ``str`` -> the registry probes formatters in order.

        ``bool`` is rejected explicitly because ``bool`` is a subclass of
        ``int`` and ``int(True) == 1`` is a real Telegram id; a boolean
        arriving here is a flag in the wrong argument position, which is
        easier to debug if it fails than if it silently registers against id 1.
        """
        if chat_id is None:
            raise InvalidKeyFormat("chat_id is None")
        if isinstance(chat_id, ChatKey):
            return chat_id
        if isinstance(chat_id, type):
            raise InvalidKeyFormat(f"chat_id {chat_id!r} is a class, not a value")
        if isinstance(chat_id, bool):
            raise InvalidKeyFormat(f"chat_id {chat_id!r} is a bool, not an id")
        if isinstance(chat_id, int):
            return NumericChatKey(chat_id)
        if isinstance(chat_id, str):
            return self._from_string(chat_id)
        raise InvalidKeyFormat(
            f"chat_id {chat_id!r} ({type(chat_id).__name__}) "
            f"is not a supported key form"
        )

    def _from_string(self, text: str) -> ChatKey:
        """Resolve a string via the registry, failing closed on ambiguity."""
        fmt = self._registry.probe(text)
        if fmt is None:
            return OpaqueStringKey(text)
        return fmt.parse(text)  # type: ignore[return-value]


#: Shared normalizer; injected where needed so the chain is swappable.
_default_chatkey_normalizer: ChatKeyNormalizer = ChatKeyNormalizer()


def build_chat_key(chat_id: Any) -> ChatKey:
    """Construct a :class:`ChatKey`, sharing the default normalizer."""
    return _default_chatkey_normalizer.normalize(chat_id)


def normalize_chat_id(chat_id: Any) -> str:
    """Return the canonical string form of *chat_id*.

    Convenience for call sites that already store strings and want the
    normalized form without holding a :class:`ChatKey` object.
    """
    return build_chat_key(chat_id).value


# ── enum façade over the string categories ─────────────────────────────────


class ChatKeyCategory(enum.Enum):
    """Stable enum over the string categories.

    Exposed for callers that prefer enums to strings (e.g. typed config).
    """

    GROUP = CATEGORY_GROUP
    USER = CATEGORY_USER
    SESSION = CATEGORY_SESSION

    @classmethod
    def of(cls, value: str) -> "ChatKeyCategory":
        try:
            return cls(value)
        except ValueError:
            raise InvalidKeyFormat(f"unknown chat-key category {value!r}")

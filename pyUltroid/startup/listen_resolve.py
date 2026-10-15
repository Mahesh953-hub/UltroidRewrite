"""Structural resolution of ``ultroid_cmd(listen=...)`` arguments.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

import enum
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

__all__ = [
    "EVENT_BUILDER_BASE",
    "BuilderCatalog",
    "FilterPredicate",
    "InvalidListenArgumentError",
    "ListenArgumentParser",
    "ListenKind",
    "ListenResolutionError",
    "ResolvedListen",
    "UnknownBuilderError",
    "default_parser",
]


# ── failure taxonomy ───────────────────────────────────────────────────────


class ListenResolutionError(TypeError):
    """Base class for every ``listen=`` resolution failure.

    Subclasses ``TypeError`` so callers that already wrap plugin loading in
    ``except TypeError`` keep working without modification.
    """


class InvalidListenArgumentError(ListenResolutionError):
    """The value can never serve as an ``isinstance`` key.

    Raised for strings, ``None``, integers, and other values carrying no
    listener semantics. The message names the value *and* its type, because
    the value alone is frequently unprintable or ambiguous.
    """


class UnknownBuilderError(ListenResolutionError):
    """A real event builder with no expansion rule in this build.

    Distinct from :class:`InvalidListenArgumentError`: the argument is
    legitimate Telethon, it simply has no
    :attr:`BuilderCatalog.TABLE` entry. The remedy is to extend the catalog,
    not to fix a typo.
    """


# ── value objects ──────────────────────────────────────────────────────────


class ListenKind(enum.Enum):
    """What one ``listen=`` element was, before flattening.

    Retained on :class:`ResolvedListen` so a failure report can say "the plugin
    passed a builder" instead of restating whatever the builder flattened
    into.
    """

    TL_CLASS = "tl_class"
    """A TL update class - passed straight through."""

    EVENT_BUILDER = "event_builder"
    """A Telethon event-builder instance - expanded and filtered."""

    BUILDER_CLASS = "builder_class"
    """The event-builder *class* rather than an instance.

    Legal, and deliberately distinct: ``listen=events.NewMessage`` (the class)
    is the author asking to receive built events, not something this module
    can flatten. It is preserved verbatim.
    """

    def describe(self) -> str:
        """Stable string form used in diagnostics."""
        return self.value


class FilterPredicate:
    """A composable, raise-guarded listener predicate.

    Filter predicates are user-supplied code evaluated on every update for
    every listener. Two properties are mandatory, and both are enforced by
    construction rather than by convention:

    1. A predicate that raises must mean *skip*, never *crash*. Letting one
       propagate aborts the dispatcher's fan-out and takes down every other
       listener on the same client - the same class of failure this module
       exists to prevent.
    2. Composition is boolean AND. A plugin supplying both a builder filter
       and a ``listen_func`` asked for both; returning "either" would
       silently widen the listener beyond what was requested.
    """

    __slots__ = ("_predicate", "_origin")

    def __init__(
        self, predicate: Callable[[Any], Any], *, origin: str = "direct"
    ) -> None:
        self._predicate: Callable[[Any], Any] = predicate
        self._origin: str = origin

    @property
    def origin(self) -> str:
        """Human-readable provenance, used in error messages."""
        return self._origin

    def __call__(self, update: Any) -> bool:
        """Evaluate, converting any exception into ``False``."""
        try:
            return bool(self._predicate(update))
        except Exception:  # noqa: BLE001 - a failing filter means "skip"
            return False

    def __and__(self, other: "FilterPredicate") -> "FilterPredicate":
        """Return a predicate satisfied only when both operands are."""
        if not isinstance(other, FilterPredicate):
            return NotImplemented
        combined = FilterPredicate(
            self._conjunction(other),
            origin=f"({self._origin} AND {other._origin})",
        )
        return combined

    def _conjunction(self, other: "FilterPredicate") -> Callable[[Any], bool]:
        """Build the raw conjunction callable for ``self & other``.

        Short-circuits on the left operand, which also avoids evaluating an
        expensive plugin filter when the cheap builder filter already
        rejected the update.
        """
        left, right = self._predicate, other._predicate

        def _both(update: Any) -> bool:
            if not left(update):
                return False
            return right(update)

        return _both

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"<FilterPredicate origin={self._origin!r}>"


class ResolvedListen:
    """Immutable outcome of resolving one ``listen=`` element.

    Attributes:
        update_types: TL update classes usable directly as ``isinstance``
            keys. Never contains a non-class.
        predicate: the composed, raise-guarded filter, or ``None`` when the
            element declared no filter.
        kind: the :class:`ListenKind` of the originating argument.
        origin: the original argument, retained for diagnostics only.
    """

    __slots__ = ("update_types", "predicate", "kind", "origin")

    def __init__(
        self,
        update_types: Sequence[type],
        predicate: Optional[FilterPredicate] = None,
        kind: ListenKind = ListenKind.TL_CLASS,
        origin: Any = None,
    ) -> None:
        self.update_types: Tuple[type, ...] = tuple(update_types)
        self.predicate: Optional[FilterPredicate] = predicate
        self.kind: ListenKind = kind
        self.origin: Any = origin

    @property
    def is_filtered(self) -> bool:
        """True when this element carries a filter."""
        return self.predicate is not None

    def matches(self, update: Any) -> bool:
        """Evaluate this element's filter, or ``True`` when unfiltered."""
        if self.predicate is None:
            return True
        return self.predicate(update)

    def type_names(self) -> Tuple[str, ...]:
        """Class names of :attr:`update_types`, for log and error output."""
        return tuple(t.__name__ for t in self.update_types)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"<ResolvedListen kind={self.kind.describe()} "
            f"types={self.type_names()} filtered={self.is_filtered}>"
        )


# ── builder catalog ────────────────────────────────────────────────────────


def _locate_event_builder_base() -> Optional[type]:
    """Find Telethon's ``EventBuilder`` base class.

    This fork exposes it from ``telethon.events.common``; upstream Telethon
    uses ``telethon.events.eventbuilder``; some versions re-export it from
    ``telethon.events``. All three are probed so the catalog survives version
    drift without a code change.

    Returns:
        The base class, or ``None`` when Telethon is unavailable - which
        keeps this module importable for documentation and for unit tests
        that do not require a live Telethon.
    """
    candidates = (
        ("telethon.events.common", "EventBuilder"),
        ("telethon.events", "EventBuilder"),
        ("telethon.events.eventbuilder", "EventBuilder"),
    )
    for module_name, attribute in candidates:
        try:
            module = __import__(module_name, fromlist=[attribute])
        except Exception:  # noqa: BLE001 - probe the next location
            continue
        resolved = getattr(module, attribute, None)
        if isinstance(resolved, type):
            return resolved
    return None


#: Resolved once at import time; ``None`` signals "Telethon not importable".
EVENT_BUILDER_BASE: Optional[type] = _locate_event_builder_base()


class BuilderCatalog:
    """Maps Telethon event-builder names to the TL updates they subscribe to.

    The table is explicit rather than introspected from
    ``EventBuilder.build()`` on purpose. That method is private, and the set of
    updates it accepts has shifted between Telethon releases. A wrong answer
    derived from a moving private API fails *silently* - the listener simply
    never fires and nothing reports an error. A wrong answer in this table is
    a visible diff that review can catch.
    """

    #: Populated by :meth:`_build_table` at class definition time.
    TABLE: Dict[str, Tuple[type, ...]] = {}

    @staticmethod
    def _tl(*names: str) -> Tuple[type, ...]:
        """Import named ``telethon.tl.types`` classes, skipping absent ones.

        A name missing from this Telethon build is dropped rather than
        raising, so a rename degrades to "listen to the updates that still
        exist" instead of breaking plugin import outright.
        """
        from telethon.tl import types as types_module

        found: List[type] = []
        for name in names:
            candidate = getattr(types_module, name, None)
            if isinstance(candidate, type):
                found.append(candidate)
        return tuple(found)

    @classmethod
    def _build_table(cls) -> Dict[str, Tuple[type, ...]]:
        """Construct the builder-name -> update-types mapping.

        Keys are class *names* rather than class objects so the table can be
        built without importing every builder, and so an unknown builder
        does not raise at module import.
        """
        return {
            "NewMessage": cls._tl(
                "UpdateNewMessage",
                "UpdateNewChannelMessage",
                "UpdateShortMessage",
                "UpdateShortChatMessage",
            ),
            "NewChannelMessage": cls._tl(
                "UpdateNewChannelMessage",
                "UpdateNewMessage",
            ),
            "MessageEdited": cls._tl(
                "UpdateEditMessage",
                "UpdateEditChannelMessage",
            ),
            "MessageDeleted": cls._tl(
                "UpdateDeleteMessages",
                "UpdateDeleteChannelMessages",
            ),
            "ChatAction": cls._tl("UpdateUserTyping", "UpdateChatUserTyping"),
            "UserStatus": cls._tl("UpdateUserStatus"),
            "InlineQuery": cls._tl("UpdateBotInlineQuery"),
            "InlineResult": cls._tl("UpdateBotInlineSend"),
            "CallbackQuery": cls._tl("UpdateBotCallbackQuery"),
            "Conversation": cls._tl(
                "UpdateNewMessage",
                "UpdateNewChannelMessage",
                "UpdateEditMessage",
                "UpdateEditChannelMessage",
            ),
            "Reaction": cls._tl(
                "UpdateMessageReactions",
                "UpdateChannelMessageReactions",
            ),
        }

    @classmethod
    def lookup(cls, builder: Any) -> Tuple[type, ...]:
        """Resolve a builder instance's update types, walking its MRO.

        The walk matters because plugin authors subclass the stock builders
        to add their own filters. Without it such a subclass would resolve to
        nothing and register a listener that can never fire.

        Raises:
            UnknownBuilderError: no ancestor of *builder* appears in
                :attr:`TABLE`, or the matched entry is empty.
        """
        for ancestor in type(builder).__mro__:
            entry = cls.TABLE.get(ancestor.__name__)
            if entry:
                return entry
        raise UnknownBuilderError(cls.unknown_message(builder))

    @classmethod
    def unknown_message(cls, builder: Any) -> str:
        """Build the error text for an unexpandable builder."""
        name = type(builder).__name__
        return (
            f"listen=: no TL update mapping for Telethon event builder "
            f"{name!r}. Add a BuilderCatalog.TABLE entry for it in "
            f"pyUltroid/startup/listen_resolve.py, or pass the TL update "
            f"class(es) directly (e.g. types.UpdateNewMessage). Refusing to "
            f"guess: a wrong expansion would either never fire or fire on "
            f"unrelated updates."
        )

    @classmethod
    def known_names(cls) -> Tuple[str, ...]:
        """Every builder name present in the table."""
        return tuple(cls.TABLE)

    @classmethod
    def empty_entries(cls) -> Tuple[str, ...]:
        """Builder names whose entry resolved to zero update types.

        These are latent load-time failures on a Telethon build where the
        referenced update types do not exist.
        """
        return tuple(name for name, types_ in cls.TABLE.items() if not types_)


BuilderCatalog.TABLE = BuilderCatalog._build_table()


# ── argument classification ────────────────────────────────────────────────


class ListenArgumentClassifier:
    """Decides what a single ``listen=`` element is.

    Kept separate from the parser so classification is testable against the
    registry without constructing a resolution, and so the parser reads as a
    sequence of decisions rather than a chain of inline type checks.
    """

    def is_event_builder_instance(self, value: Any) -> bool:
        """True for a builder *instance*.

        A builder *class* returns ``False`` here and is classified separately:
        ``listen=events.NewMessage`` asks for built events and must not be
        flattened into raw TL types.
        """
        base = EVENT_BUILDER_BASE
        if base is None or isinstance(value, type):
            return False
        return isinstance(value, base)

    def is_event_builder_class(self, value: Any) -> bool:
        """True for the event-builder *class* rather than an instance."""
        base = EVENT_BUILDER_BASE
        if base is None or not isinstance(value, type):
            return False
        return issubclass(value, base)

    def classify(self, value: Any) -> ListenKind:
        """Return the :class:`ListenKind` of *value*.

        Raises:
            InvalidListenArgumentError: *value* is not a usable listener key.
        """
        if self.is_event_builder_instance(value):
            return ListenKind.EVENT_BUILDER
        if self.is_event_builder_class(value):
            return ListenKind.BUILDER_CLASS
        if isinstance(value, type):
            return ListenKind.TL_CLASS
        raise InvalidListenArgumentError(self.invalid_message(value))

    @staticmethod
    def invalid_message(value: Any) -> str:
        """Build the error text for an unusable ``listen=`` value."""
        return (
            f"listen=: expected a TL update class or a Telethon event "
            f"builder, got {value!r} ({type(value).__name__}). A listener key "
            f"must be a type, because dispatch matches updates with "
            f"isinstance()."
        )


# ── the parser ─────────────────────────────────────────────────────────────


class ListenArgumentParser:
    """Turns a ``listen=`` argument into resolved, registrable specs.

    Holds no per-call state, so one instance may be shared across plugins
    without results leaking between them.

    Resolution is total: it either returns specs whose every
    ``update_types`` member is a class, or raises. It never returns a spec
    that would fail later at dispatch time.
    """

    def __init__(self, catalog: Optional[type] = None) -> None:
        self._catalog: type = catalog or BuilderCatalog
        self._classifier: ListenArgumentClassifier = ListenArgumentClassifier()

    @property
    def catalog(self) -> type:
        """The builder catalog this parser consults."""
        return self._catalog

    def resolve(
        self,
        listen: Any,
        predicate: Optional[FilterPredicate] = None,
    ) -> Tuple[ResolvedListen, ...]:
        """Resolve *listen* into a tuple of :class:`ResolvedListen`.

        Args:
            listen: A TL update class, an event-builder instance or class, or
                a list/tuple mixing any of those.
            predicate: The plugin-level filter supplied alongside ``listen``,
                combined with any builder filter using AND, never OR.

        Returns:
            One spec per recognised element, in declaration order.

        Raises:
            InvalidListenArgumentError: an element is neither a class nor a
                builder.
            UnknownBuilderError: an element is a builder this build cannot
                expand.
        """
        resolved: List[ResolvedListen] = [
            self._resolve_element(element, predicate)
            for element in self._elements_of(listen)
        ]
        return tuple(resolved)

    @staticmethod
    def _elements_of(listen: Any) -> Iterable[Any]:
        """Normalise *listen* to an iterable without consuming strings.

        A bare string must be treated as one (invalid) element rather than
        iterated character by character, or the error would name a single
        letter and hide the typo.
        """
        if isinstance(listen, (list, tuple)):
            return listen
        return (listen,)

    def _resolve_element(
        self,
        element: Any,
        predicate: Optional[FilterPredicate],
    ) -> ResolvedListen:
        """Classify *element* and dispatch to the matching expansion."""
        kind: ListenKind = self._classifier.classify(element)

        if kind is ListenKind.EVENT_BUILDER:
            return self._resolve_builder(element, predicate)
        # Both TL classes and event-builder classes are already types, which
        # is exactly what the dispatcher needs. Pass them through untouched.
        return ResolvedListen((element,), predicate, kind, element)

    def _resolve_builder(
        self,
        builder: Any,
        predicate: Optional[FilterPredicate],
    ) -> ResolvedListen:
        """Expand an event-builder instance into TL types plus a filter."""
        update_types: Tuple[type, ...] = self._catalog.lookup(builder)
        composed: Optional[FilterPredicate] = self._compose(
            self._builder_predicate(builder), predicate
        )
        return ResolvedListen(
            update_types, composed, ListenKind.EVENT_BUILDER, builder
        )

    @staticmethod
    def _builder_predicate(builder: Any) -> Optional[FilterPredicate]:
        """Extract and wrap the builder's own ``func``.

        ``events.NewMessage(incoming=True, func=...)`` stores the author's
        predicate on ``.func``. Carrying it into the listener's filter keeps
        the raw listener no broader than the builder the author wrote.
        """
        declared = getattr(builder, "func", None)
        if not callable(declared):
            return None
        return FilterPredicate(declared, origin=f"{type(builder).__name__}.func")

    @staticmethod
    def _compose(
        builder_filter: Optional[FilterPredicate],
        plugin_filter: Optional[FilterPredicate],
    ) -> Optional[FilterPredicate]:
        """Combine the builder's filter with the plugin's, using AND.

        Both must hold. When either side is absent the other is returned
        unchanged, so a lone builder filter is still raise-guarded by
        :class:`FilterPredicate` itself.
        """
        if builder_filter is None:
            return plugin_filter
        if plugin_filter is None:
            return builder_filter
        return builder_filter & plugin_filter


#: Shared, stateless parser. Correct to reuse across every plugin load.
default_parser: ListenArgumentParser = ListenArgumentParser()

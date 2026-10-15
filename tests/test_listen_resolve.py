"""Tests for ``listen=`` resolution of Telethon event-builder instances.

The production failure these cover:

    TypeError: register_listener: update_type must be a class, got
    <telethon.events.newmessage.NewMessage object at 0x...> for listener 'ai_chatbot'
    [loader] plugin 'plugins.brain' failed to load

``plugins/brain.py`` passes ``events.NewMessage(incoming=True, func=...)`` -
a builder *instance* - where the dispatcher's isinstance-based fan-out needs a
TL update *class*. An earlier guard rejected the builder, which converted a
runtime dispatch crash into a plugin that fails to load at all. These tests
pin the class-based resolution contract: the builder expands to the TL types
it subscribes to, its filter is carried forward and composed with the
plugin's, and the failure taxonomy stays distinguishable.
"""

from __future__ import annotations

import pytest
from telethon import events
from telethon.tl import types

from pyUltroid.startup.listen_resolve import (
    BuilderCatalog,
    FilterPredicate,
    InvalidListenArgumentError,
    ListenArgumentClassifier,
    ListenArgumentParser,
    ListenKind,
    ListenResolutionError,
    ResolvedListen,
    UnknownBuilderError,
    default_parser,
)


@pytest.fixture
def parser() -> ListenArgumentParser:
    return ListenArgumentParser()


# ── the production case ────────────────────────────────────────────────────


def test_new_message_builder_resolves_to_tl_types(parser):
    """The exact argument that broke plugin load in production."""
    builder = events.NewMessage(incoming=True, func=lambda e: not e.out)
    specs = parser.resolve(builder)

    assert len(specs) == 1
    names = set(specs[0].type_names())
    assert "UpdateNewMessage" in names
    assert "UpdateNewChannelMessage" in names
    # Every key must be usable with isinstance().
    assert all(isinstance(t, type) for t in specs[0].update_types)


def test_builder_resolves_even_without_a_filter(parser):
    builder = events.NewMessage()
    spec = parser.resolve(builder)[0]

    assert spec.update_types
    assert all(isinstance(t, type) for t in spec.update_types)
    assert spec.predicate is None
    assert spec.is_filtered is False


def test_builder_kind_is_recorded(parser):
    spec = parser.resolve(events.NewMessage())[0]
    assert spec.kind is ListenKind.EVENT_BUILDER
    assert spec.kind.describe() == "event_builder"


def test_builder_filter_is_carried_forward(parser):
    builder = events.NewMessage(incoming=True, func=lambda e: not e.out)
    spec = parser.resolve(builder)[0]

    assert spec.is_filtered
    assert spec.predicate.origin.endswith(".func")


def test_unfiltered_spec_matches_everything(parser):
    spec = parser.resolve(types.UpdateNewMessage)[0]
    assert spec.matches(object()) is True


# ── FilterPredicate composition ────────────────────────────────────────────


def test_filters_compose_with_and(parser):
    builder = events.NewMessage(func=lambda e: True)
    strict = FilterPredicate(lambda e: False, origin="listen_func")

    spec = parser.resolve(builder, strict)[0]
    assert spec.matches(object()) is False


def test_both_filters_true_passes(parser):
    builder = events.NewMessage(func=lambda e: True)
    lenient = FilterPredicate(lambda e: True, origin="listen_func")

    spec = parser.resolve(builder, lenient)[0]
    assert spec.matches(object()) is True


def test_and_operator_builds_a_new_predicate():
    left = FilterPredicate(lambda e: True, origin="left")
    right = FilterPredicate(lambda e: False, origin="right")

    combined = left & right
    assert combined is not left and combined is not right
    assert combined(object()) is False
    assert "AND" in combined.origin


def test_and_operator_rejects_foreign_operand():
    left = FilterPredicate(lambda e: True)
    with pytest.raises(TypeError):
        left & "not a predicate"


def test_a_raising_filter_means_no_match():
    """A throwing filter must skip, not abort the fan-out."""

    def boom(update):
        raise ValueError("bad filter")

    predicate = FilterPredicate(boom)
    assert predicate(object()) is False


def test_a_raising_plugin_filter_means_no_match(parser):
    def boom(update):
        raise ValueError("bad filter")

    builder = events.NewMessage(func=lambda e: True)
    spec = parser.resolve(builder, FilterPredicate(boom))[0]
    assert spec.matches(object()) is False


# ── plain classes keep working ─────────────────────────────────────────────


def test_single_class_resolves_unchanged(parser):
    specs = parser.resolve(types.UpdateNewMessage)
    assert specs[0].update_types == (types.UpdateNewMessage,)
    assert specs[0].kind is ListenKind.TL_CLASS


def test_tuple_of_classes_resolves(parser):
    specs = parser.resolve((types.UpdateNewMessage, types.UpdateNewChannelMessage))
    assert {s.update_types[0] for s in specs} == {
        types.UpdateNewMessage,
        types.UpdateNewChannelMessage,
    }


def test_tuple_mixing_classes_and_builders(parser):
    specs = parser.resolve((types.UpdateNewEphemeralMessage, events.NewMessage()))
    flat = {t for s in specs for t in s.update_types}

    assert types.UpdateNewEphemeralMessage in flat
    assert types.UpdateNewMessage in flat


def test_builder_class_is_passed_through_untouched(parser):
    """``listen=NewMessage`` (the class) asks for built events."""
    spec = parser.resolve(events.NewMessage)[0]

    assert spec.update_types == (events.NewMessage,)
    assert spec.kind is ListenKind.BUILDER_CLASS
    assert spec.origin is events.NewMessage


def test_class_receives_the_plugin_filter(parser):
    strict = FilterPredicate(lambda e: False, origin="listen_func")
    spec = parser.resolve(types.UpdateNewMessage, strict)[0]

    assert spec.predicate is strict
    assert spec.matches(object()) is False


# ── failure taxonomy ───────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["UpdateNewMessage", None, 42, object()])
def test_non_class_non_builder_is_rejected(parser, bad):
    with pytest.raises(InvalidListenArgumentError):
        parser.resolve(bad)


def test_invalid_argument_is_a_type_error(parser):
    """Existing ``except TypeError`` around plugin load keeps working."""
    with pytest.raises(TypeError):
        parser.resolve(None)


def test_invalid_error_names_value_and_type(parser):
    with pytest.raises(InvalidListenArgumentError) as ei:
        parser.resolve("UpdateNewMessage")

    message = str(ei.value)
    assert "UpdateNewMessage" in message
    assert "str" in message


def test_invalid_error_is_not_confused_with_unknown_builder(parser):
    with pytest.raises(InvalidListenArgumentError) as ei:
        parser.resolve("UpdateNewMessage")
    assert not isinstance(ei.value, UnknownBuilderError)


def test_both_failures_share_a_base_class():
    assert issubclass(InvalidListenArgumentError, ListenResolutionError)
    assert issubclass(UnknownBuilderError, ListenResolutionError)
    assert issubclass(ListenResolutionError, TypeError)


def test_unknown_builder_raises_with_guidance():
    class Orphan(events.NewMessage):
        pass

    saved = dict(BuilderCatalog.TABLE)
    try:
        for name in ("Orphan", "NewMessage", "EventBuilder"):
            BuilderCatalog.TABLE.pop(name, None)

        with pytest.raises(UnknownBuilderError) as ei:
            ListenArgumentParser().resolve(Orphan())

        message = str(ei.value)
        assert "Orphan" in message
        assert "BuilderCatalog.TABLE" in message
    finally:
        BuilderCatalog.TABLE.clear()
        BuilderCatalog.TABLE.update(saved)


def test_empty_table_entry_is_treated_as_unknown():
    """An entry with no update types is as unusable as a missing one."""
    saved = dict(BuilderCatalog.TABLE)
    try:
        BuilderCatalog.TABLE["NewMessage"] = ()
        with pytest.raises(UnknownBuilderError):
            ListenArgumentParser().resolve(events.NewMessage())
    finally:
        BuilderCatalog.TABLE.clear()
        BuilderCatalog.TABLE.update(saved)


# ── classifier ─────────────────────────────────────────────────────────────


def test_classifier_recognises_the_three_kinds():
    classifier = ListenArgumentClassifier()

    assert classifier.classify(types.UpdateNewMessage) is ListenKind.TL_CLASS
    assert classifier.classify(events.NewMessage()) is ListenKind.EVENT_BUILDER
    assert classifier.classify(events.NewMessage) is ListenKind.BUILDER_CLASS


def test_classifier_rejects_a_bare_string():
    with pytest.raises(InvalidListenArgumentError):
        ListenArgumentClassifier().classify("UpdateNewMessage")


# ── catalog integrity ──────────────────────────────────────────────────────


def test_every_table_entry_is_a_real_type():
    for name, types_ in BuilderCatalog.TABLE.items():
        assert types_, f"{name} maps to no update types"
        for t in types_:
            assert isinstance(t, type), f"{name} -> {t!r} is not a class"


def test_catalog_reports_empty_entries():
    assert BuilderCatalog.empty_entries() == ()


def test_catalog_knows_new_message():
    assert "NewMessage" in BuilderCatalog.known_names()


def test_lookup_walks_the_mro():
    class MyMessages(events.NewMessage):
        pass

    names = {t.__name__ for t in BuilderCatalog.lookup(MyMessages())}
    assert "UpdateNewMessage" in names


# ── ResolvedListen ─────────────────────────────────────────────────────────


def test_resolved_listen_repr_is_informative():
    spec = ResolvedListen((types.UpdateNewMessage,), None, ListenKind.TL_CLASS, None)
    assert "UpdateNewMessage" in repr(spec)
    assert "tl_class" in repr(spec)


def test_shared_parser_is_reusable():
    """The module-level parser holds no per-call state."""
    first = default_parser.resolve(types.UpdateNewMessage)
    second = default_parser.resolve(events.NewMessage())

    assert first[0].kind is ListenKind.TL_CLASS
    assert second[0].kind is ListenKind.EVENT_BUILDER
    assert default_parser.catalog is BuilderCatalog

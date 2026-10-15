"""Tests for the ChatKey normalization model and the chat_system fix.

The production failure these cover:

    ValueError: invalid literal for int() with base 10: 'dm:6802929470:305'
    at pyUltroid/agent/_agent.py:628 in chat_system

a DM session key was handed to ``int()`` and aborted the listener. These pin
the new behavior: string session keys and integer chat ids are distinct
slots, malformed keys are rejected at the boundary, and the formatter
registry dispatches by object identity.
"""

from __future__ import annotations

import pytest

from pyUltroid.agent._chatkey import (
    CATEGORY_GROUP,
    CATEGORY_SESSION,
    CATEGORY_USER,
    ChatKeyCatalog,
    ChatKey,
    ChatKeyCategory,
    DmSessionKey,
    ChatKeyRegistry,
    InvalidKeyFormat,
    ChatKeyNormalizer,
    NumericChatKey,
    OpaqueStringKey,
    build_chat_key,
    normalize_chat_id,
)
from pyUltroid.agent._agent import UltroidAgent


# ── the production case ────────────────────────────────────────────────────


def test_dm_session_key_does_not_crash():
    """The exact value that raised ValueError in production."""
    key = build_chat_key("dm:6802929470:305")
    assert isinstance(key, DmSessionKey)
    assert key.value == "dm:6802929470:305"
    assert key.is_session()
    assert key.user_id == 6802929470
    assert key.anchor == 305


def test_numeric_and_string_keys_do_not_collide():
    """The collision behind the original bug."""
    dm = build_chat_key("dm:6802929470:305").value
    num = build_chat_key(6802929470).value
    assert dm != num


def test_chat_system_holds_distinct_slots():
    """set/get with a dm key and an int must not clobber each other."""
    agent = UltroidAgent()
    agent.set_chat_system("dm:6802929470:305", "session prompt")
    agent.set_chat_system(8402929470, "group prompt")

    assert agent.chat_system("dm:6802929470:305") == "session prompt"
    assert agent.chat_system(8402929470) == "group prompt"


# ── normalization ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (12345, "12345"),
        (-1, "-1"),
        ("12345", "12345"),
        (True, None),  # bool rejected
        (None, None),  # None rejected
    ],
)
def test_normalize_chat_id(raw, expected):
    if expected is None:
        with pytest.raises(InvalidKeyFormat):
            normalize_chat_id(raw)
    else:
        assert normalize_chat_id(raw) == expected


def test_a_chatkey_passes_through_idempotent():
    original = build_chat_key("dm:1:2")
    again = build_chat_key(original)
    assert again is original


def test_a_class_is_rejected_not_coerced():
    with pytest.raises(InvalidKeyFormat):
        build_chat_key(int)  # a type, not a value


def test_an_int_is_numeric():
    key = build_chat_key(42)
    assert isinstance(key, NumericChatKey)
    assert key.is_group()
    assert int(key) == 42


def test_a_plain_string_is_opaque():
    key = build_chat_key("a-funky-name")
    assert isinstance(key, OpaqueStringKey)
    assert key.is_user()


def test_malformed_dm_key_rejected():
    with pytest.raises(InvalidKeyFormat):
        build_chat_key("dm:garbage:1")


def test_dm_key_round_trips():
    original = "dm:6802929470:305"
    rebuilt = build_chat_key(original).value
    assert rebuilt == original


# ── ChatKey value semantics ────────────────────────────────────────────────


def test_keys_are_equal_by_canonical_form():
    assert build_chat_key(42) == build_chat_key(42)
    assert build_chat_key("dm:1:2") == build_chat_key("dm:1:2")
    assert build_chat_key(42) != build_chat_key("dm:1:2")


def test_keys_are_hashable_and_usable_as_dict_keys():
    table = {build_chat_key("dm:1:2"): "x"}
    assert table[build_chat_key("dm:1:2")] == "x"


def test_repr_is_informative():
    key = build_chat_key("dm:1:2")
    text = repr(key)
    assert "dm-session" in text
    assert "dm:1:2" in text


# ── ChatKeyCategory enum ───────────────────────────────────────────────────


def test_category_enum_round_trips():
    assert ChatKeyCategory.of(CATEGORY_SESSION) is ChatKeyCategory.SESSION
    assert ChatKeyCategory.SESSION.value == CATEGORY_SESSION


def test_unknown_category_raises():
    with pytest.raises(InvalidKeyFormat):
        ChatKeyCategory.of("nope")


# ── formatter registry ─────────────────────────────────────────────────────


def test_registry_round_trips_all_categories():
    from pyUltroid.agent._chatkey import DEFAULT_CHATKEY_REGISTRY

    for raw in (42, "dm:1:2", "opaque-string"):
        key = build_chat_key(raw)
        assert DEFAULT_CHATKEY_REGISTRY.canonical(key) == key.value


def test_registry_probe_returns_first_claimant():
    from pyUltroid.agent._chatkey import DEFAULT_CHATKEY_REGISTRY

    session_fmt = DEFAULT_CHATKEY_REGISTRY.probe("dm:1:2")
    assert session_fmt is not None
    assert session_fmt.category == CATEGORY_SESSION

    numeric_fmt = DEFAULT_CHATKEY_REGISTRY.probe("42")
    assert numeric_fmt is not None
    assert numeric_fmt.category == CATEGORY_GROUP

    opaque_fmt = DEFAULT_CHATKEY_REGISTRY.probe("weird")
    assert opaque_fmt is not None
    assert opaque_fmt.category == CATEGORY_USER


def test_registry_rejects_malformed_dm_prefix():
    from pyUltroid.agent._chatkey import DEFAULT_CHATKEY_REGISTRY

    with pytest.raises(InvalidKeyFormat):
        DEFAULT_CHATKEY_REGISTRY.probe("dm:notanumber:1")


def test_registry_rejects_unknown_category():
    from pyUltroid.agent._chatkey import DEFAULT_CHATKEY_REGISTRY

    with pytest.raises(InvalidKeyFormat):
        DEFAULT_CHATKEY_REGISTRY.for_category("does-not-exist")


def test_registry_rejects_unbound_key_type():
    """A ChatKey subclass with no formatter raises, it does not guess."""
    from pyUltroid.agent._chatkey import DEFAULT_CHATKEY_REGISTRY

    class WeirdKey(ChatKey):
        __slots__ = ()
        def __init__(self) -> None:
            super().__init__("w", "w", "weird")

    with pytest.raises(InvalidKeyFormat):
        DEFAULT_CHATKEY_REGISTRY.canonical(WeirdKey())  # type: ignore[arg-type]


# ── catalog ────────────────────────────────────────────────────────────────


def test_catalog_lists_all_supported_shapes():
    names = ChatKeyCatalog.keys()
    assert CATEGORY_GROUP in names
    assert CATEGORY_SESSION in names
    assert CATEGORY_USER in names


def test_catalog_session_formatter_resolves_production_key():
    session_fmt = ChatKeyCatalog[CATEGORY_SESSION]
    key = session_fmt.parse("dm:6802929470:305")
    assert isinstance(key, DmSessionKey)
    assert key.user_id == 6802929470
    assert key.anchor == 305


def test_catalog_numeric_formatter_handles_negative():
    numeric_fmt = ChatKeyCatalog[CATEGORY_GROUP]
    key = numeric_fmt.parse("-1")
    assert isinstance(key, NumericChatKey)
    assert int(key) == -1


# ── ChatKeyNormalizer isolation ┧───────────────────────────────────────────────


def test_normalizer_can_use_a_restricted_registry():
    """A custom registry changes probe behavior but not int dispatch.

    ``int`` never goes through ``probe`` - it is handled by the explicit type
    branch - so a registry lacking the numeric formatter still parses bare
    integers. What the registry *does* control is which string shapes get
    claimed before falling through to the catch-all: with only the session
    formatter wired, a plain string is not claimed by probe and becomes an
    :class:`OpaqueStringKey`, and a malformed ``dm:`` string raises.
    """
    from pyUltroid.agent._chatkey import _SessionFormatter

    registry = ChatKeyRegistry([_SessionFormatter()])
    normalizer = ChatKeyNormalizer(registry=registry)

    # int is claimed by the type branch, not the registry.
    assert isinstance(normalizer.normalize(42), NumericChatKey)
    # A plain string is not claimed by the session-only registry, so it falls
    # through to the catch-all rather than being misread as a session.
    assert isinstance(normalizer.normalize("weird"), OpaqueStringKey)
    # A malformed session string still raises even with a custom registry.
    with pytest.raises(InvalidKeyFormat):
        normalizer.normalize("dm:garbage:1")


# ── chat_system persistence format ┧────────────────────────────────────────


def test_chat_system_survives_str_round_trip_through_dict():
    """The in-memory and persisted forms agree on canonical strings."""
    agent = UltroidAgent()
    agent.set_chat_system("dm:6802929470:305", "persisted")
    agent.set_chat_system(7, "also persisted")

    serialized = agent._chat_systems
    assert serialized == {
        "dm:6802929470:305": "persisted",
        "7": "also persisted",
    }
    assert "6802929470" not in serialized

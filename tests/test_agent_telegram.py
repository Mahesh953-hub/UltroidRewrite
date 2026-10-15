"""Fork-parity coverage for the agent's Telegram read/write surface.

The pinned Telethon fork hands back more than one shape for a message, and the
two configured clients disagree. These tests pin every observed shape so a
prompt can never receive ``"('hi', [...])"``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from telethon.tl import types
from telethon.tl.custom import Button

from pyUltroid.agent._telegram import (
    BUTTON_STYLES,
    entities_of,
    inline_rows,
    raw_message,
    text_of,
    unwrap_pair,
)

BOLD = types.MessageEntityBold(0, 4)


# ── the shapes a message can be ─────────────────────────────────────────────


def test_plain_string_passes_through():
    assert text_of("hello") == "hello"
    assert unwrap_pair("hello") == ("hello", [])


def test_fork_parse_mode_pair_is_unwrapped():
    """RICH_MD.parse returns ``(text, entities)``; the agent must see text."""
    pair = ("bold text", [BOLD])
    assert unwrap_pair(pair) == ("bold text", [BOLD])
    assert text_of(pair) == "bold text"
    assert entities_of(pair) == [BOLD]


def test_pair_without_entities():
    assert unwrap_pair(("just text",)) == ("just text", [])
    assert text_of(("just text", None)) == "just text"


def test_pair_with_none_entities_is_list_not_none():
    _, entities = unwrap_pair(("t", None))
    assert entities == []


def test_empty_and_none():
    assert unwrap_pair(None) == ("", [])
    assert unwrap_pair(None, fallback="fb") == ("fb", [])
    assert unwrap_pair(()) == ("", [])
    assert text_of(None) == ""


def test_non_string_head_degrades_to_fallback():
    """A pair whose head is not a str must not leak the repr into a prompt."""
    assert unwrap_pair((123, [BOLD])) == ("", [BOLD])
    assert unwrap_pair((None, None), fallback="?") == ("?", [])


def test_entities_are_filtered_of_none():
    _, entities = unwrap_pair(("t", [BOLD, None, BOLD]))
    assert entities == [BOLD, BOLD]


def test_message_object_prefers_raw_body():
    msg = SimpleNamespace(message="raw body", entities=[BOLD], text=("formatted", []))
    assert text_of(msg) == "raw body"
    assert entities_of(msg) == [BOLD]
    assert raw_message(msg) == "raw body"


def test_message_object_without_raw_body():
    msg = SimpleNamespace(text=("only formatted", [BOLD]))
    assert text_of(msg) == "only formatted"


def test_message_service_is_none():
    assert text_of(SimpleNamespace(message=None, entities=None, text=None)) == ""


def test_int_id_is_not_mistaken_for_a_message():
    """A peer id must come back empty, not as ``"12345"``."""
    assert text_of(12345) == ""


# ── the fork's coloured buttons ─────────────────────────────────────────────


def test_fork_supports_colour_styles():
    """Guards the premise: the pinned fork styles every Button.*."""
    for style in sorted(BUTTON_STYLES):
        button = Button.inline("x", b"d", style=style, icon="*")
        assert isinstance(button.style, types.KeyboardButtonStyle)


def test_rows_build_styled_callback_buttons():
    rows = inline_rows([[{"text": "Yes", "data": "y", "style": "success"}]])
    button = rows[0][0]
    assert button.text == "Yes"
    assert button.style.bg_success is True


def test_rows_default_style_applies_only_when_absent():
    rows = inline_rows(
        [[{"text": "A", "data": "a"}, {"text": "B", "data": "b", "style": "danger"}]],
        default_style="primary",
    )
    assert rows[0][0].style.bg_primary is True
    assert rows[0][1].style.bg_danger is True


def test_rows_accept_ikm_tuple_shapes():
    rows = inline_rows([[("Docs", "url", "https://example.com", "primary", "L")]])
    assert isinstance(rows[0][0], types.KeyboardInlineButton)


def test_rows_accept_bare_string_cells():
    rows = inline_rows(["Yes", "No"])
    assert len(rows) == 1 and len(rows[0]) == 2


def test_rows_pass_prebuilt_buttons_through_untouched():
    original = Button.inline("Pre", b"p", style="danger")
    rows = inline_rows([[original]])
    assert rows[0][0] is original


def test_prebuilt_button_is_not_mangled_by_tuple_handling():
    """A built button is not a sequence; detection must precede len()/index."""
    original = Button.url("Site", "https://example.com")
    rows = inline_rows([[original, Button.inline("B", b"b")]])
    assert rows[0][0] is original
    assert rows[0][1].text == "B"


def test_unknown_style_is_rejected_with_a_useful_message():
    with pytest.raises(ValueError, match="unknown button style"):
        inline_rows([[{"text": "X", "data": "x", "style": "neon"}]])


def test_empty_rows_are_dropped():
    assert inline_rows([[], [{"text": "A", "data": "a"}]]) == [
        [Button.inline("A", b"a")]
    ] or len(inline_rows([[], [{"text": "A", "data": "a"}]])) == 1


def test_row_of_helper():
    rows = inline_rows([list(({"text": "A", "data": "a"}, {"text": "B", "data": "b"}))])
    assert len(rows[0]) == 2

"""Tests for rich messaging (``custom/tg.py`` + ``custom/rich.py``).

Covers:
- Deliberate ``ikm()`` request-peer handling — the capability is probed once
  at import time; the pinned fork has no ``Button.request_peer``, so the
  "user" tuple type deliberately falls back to an inline callback, and
  explicit ``request_*`` tuple types build raw ``KeyboardButtonRequestPeer``.
- Premium-emoji helpers over ``MessageEntityCustomEmoji`` with
  Telegram-correct UTF-16 offsets.
- Colour/icon propagation for inline AND url buttons, plus named style
  presets and default icon auto-selection.
- Rich parse helpers (MarkdownV3 module, CustomMarkdown, HTML).
- Backward compatibility of every pre-existing ``ikm()`` tuple shape.

``custom.tg`` / ``custom.rich`` only depend on stdlib + telethon, so these
tests import them directly without the pyUltroid bootstrap (the repo-root
``sys.path`` insertion lives in ``tests/conftest.py``).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from telethon import Button
from telethon.tl import types
from telethon.utils import sanitize_parse_mode

import custom
import custom.tg as tg
from custom import rich
from custom.rich import RICH_MD, STYLE_PRESETS
from custom.tg import _normalize_icon, _normalize_style, ikm, request_peer_button

ROOT = Path(__file__).resolve().parents[1]


def _styles(btn):
    """Return (bg_primary, bg_danger, bg_success, icon) for a built button."""
    style = btn.style
    assert isinstance(style, types.KeyboardButtonStyle)
    return style.bg_primary, style.bg_danger, style.bg_success, style.icon


# ──────────────────────────────────────────────────────────────────────────
# style / icon normalisation
# ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("default", None),
        ("primary", "primary"),
        ("success", "success"),
        ("danger", "danger"),
        ("DANGER", "danger"),  # case-insensitive
        ("Primary", "primary"),
        (0, None),
        (1, "primary"),
        (2, "success"),
        (3, "danger"),
        (99, None),  # unknown int -> default
        ("nonsense", None),  # unknown string -> default
        (1.5, None),  # wrong type -> default
        (True, None),  # bool is not a style
    ],
)
def test_normalize_style(raw, expected):
    assert _normalize_style(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        (123, 123),
        ("456", 456),
        ("abc", None),
        (1.5, None),
        (True, None),
    ],
)
def test_normalize_icon(raw, expected):
    assert _normalize_icon(raw) == expected


# ──────────────────────────────────────────────────────────────────────────
# ikm() — backward-compatible tuple grammar
# ──────────────────────────────────────────────────────────────────────────


def test_ikm_basic_callback_button():
    rows = ikm([("A", "cb")])
    assert isinstance(rows, list) and len(rows) == 1
    btn = rows[0][0]
    assert isinstance(btn, types.KeyboardButtonCallback)
    assert btn.text == "A"
    assert btn.data == b"cb"


def test_ikm_empty_and_short_inputs_return_none():
    assert ikm([]) is None
    assert ikm([("lonely",)]) is None  # len < 2 tuples are skipped


def test_ikm_single_tuple_is_one_button_row():
    rows = ikm(("A", "cb"))
    assert len(rows) == 1 and len(rows[0]) == 1


def test_ikm_prebuilt_button_passthrough():
    prebuilt = Button.inline("PB", data="pb")
    rows = ikm([[prebuilt], [("A", "cb")]])
    assert rows[0][0] is prebuilt
    assert isinstance(rows[1][0], types.KeyboardButtonCallback)


def test_ikm_bare_button_as_row_passes_through():
    """A pre-built button used as a whole row must not be iterated/len()ed."""
    prebuilt = Button.inline("Solo", data="solo")
    rows = ikm([prebuilt, ("B", "cb")])
    assert rows[0] == [prebuilt]
    assert rows[0][0] is prebuilt
    assert isinstance(rows[1][0], types.KeyboardButtonCallback)
    assert rows[1][0].data == b"cb"


def test_ikm_bare_button_as_whole_input_passes_through():
    prebuilt = Button.url("Docs", url="https://example.com")
    rows = ikm(prebuilt)
    assert rows == [[prebuilt]]
    assert rows[0][0] is prebuilt


def test_ikm_button_helper_wrapper_passes_through():
    """Button.text() & friends return Button wrapper instances, not TL objects."""
    wrapped = Button.text("Keyboard", resize=True)
    rows = ikm([wrapped])
    assert rows[0][0] is wrapped
    assert rows[0][0].resize is True


def test_ikm_request_peer_built_button_passes_through():
    prebuilt = request_peer_button("Pick", "user", button_id=2)
    rows = ikm([prebuilt])
    assert rows[0][0] is prebuilt


def test_ikm_url_button():
    rows = ikm([("G", "url", "https://example.com")])
    btn = rows[0][0]
    assert isinstance(btn, types.KeyboardInlineButton)
    assert btn.type.url == "https://example.com"


def test_ikm_url_button_without_url_falls_back_to_text():
    # Legacy behaviour, kept: the fork's Button.url does ``url or text``.
    rows = ikm([("G", "url")])
    assert rows[0][0].type.url == "G"


def test_ikm_style_names_and_int_codes():
    rows = ikm(
        [
            [("p", "a", "primary"), ("d", "b", "danger"), ("s", "c", "success")],
            [("P", "d", 1), ("D", "e", 3), ("S", "f", 2), ("-", "g", 0)],
        ]
    )
    assert _styles(rows[0][0]) == (True, None, None, None)
    assert _styles(rows[0][1]) == (None, True, None, None)
    assert _styles(rows[0][2]) == (None, None, True, None)
    assert _styles(rows[1][0]) == (True, None, None, None)
    assert _styles(rows[1][1]) == (None, True, None, None)
    assert _styles(rows[1][2]) == (None, None, True, None)
    assert _styles(rows[1][3]) == (None, None, None, None)


def test_ikm_unknown_style_degrades_to_default():
    btn = ikm([("x", "cb", "rainbow")])[0][0]
    assert _styles(btn) == (None, None, None, None)


def test_ikm_auth_buy_game_branch_unchanged():
    rows = ikm([("pay", "buy", "invoice")])
    btn = rows[0][0]
    assert isinstance(btn, types.KeyboardButtonCallback)
    assert btn.data == b"invoice"


def test_ikm_smoke_markup_still_builds():
    """The exact markup asserted by smoke_test.py must keep working."""
    markup = ikm(
        [
            [("Normal Button", "click_1")],
            [("Red Button", "click_2", "danger")],
            [("Blue Button with Icon", "click_3", "primary", 123)],
            [("Google Link", "url", "https://google.com", "success", 456)],
        ]
    )
    assert isinstance(markup, list) and markup
    assert all(isinstance(row, list) for row in markup)


# ──────────────────────────────────────────────────────────────────────────
# icon + URL-button style propagation
# ──────────────────────────────────────────────────────────────────────────


def test_ikm_inline_icon_is_propagated():
    """Regression: the documented 4th tuple element (icon) was silently dropped."""
    btn = ikm([("Blue", "cb", "primary", 123)])[0][0]
    assert _styles(btn) == (True, None, None, 123)


def test_ikm_inline_icon_accepts_digit_string():
    btn = ikm([("Blue", "cb", "primary", "123")])[0][0]
    assert _styles(btn)[3] == 123


def test_ikm_bad_icon_degrades_to_none():
    btn = ikm([("Blue", "cb", "primary", "not-an-id")])[0][0]
    assert _styles(btn) == (True, None, None, None)


def test_ikm_url_button_style_and_icon():
    """The fork styles URL buttons too; ikm must pass style/icon through."""
    btn = ikm([("Link", "url", "https://x.com", "success", 456)])[0][0]
    assert isinstance(btn, types.KeyboardInlineButton)
    assert _styles(btn) == (None, None, True, 456)


def test_ikm_url_button_style_without_icon():
    btn = ikm([("Link", "url", "https://x.com", "danger")])[0][0]
    assert _styles(btn) == (None, True, None, None)


# ──────────────────────────────────────────────────────────────────────────
# deliberate request-peer handling
# ──────────────────────────────────────────────────────────────────────────


def test_request_peer_capability_probed_once_at_import():
    assert tg._HAS_REQUEST_PEER == hasattr(Button, "request_peer")
    # The pinned Mahesh953-hub/Telethon fork has no high-level helper.
    assert tg._HAS_REQUEST_PEER is False


def test_ikm_user_tuple_deliberate_inline_fallback():
    """On the pinned fork, ("…", "user", uid) is an inline callback carrying
    the user id as callback data — the historical behaviour, now deliberate."""
    btn = ikm([("me?", "user", 42)])[0][0]
    assert isinstance(btn, types.KeyboardButtonCallback)
    assert btn.data == b"42"


def test_ikm_user_tuple_without_id_falls_back_to_literal():
    btn = ikm([("me?", "user")])[0][0]
    assert btn.data == b"user"


def test_ikm_user_tuple_uses_native_helper_when_available(monkeypatch):
    """If a future Telethon build grows Button.request_peer, ikm uses it."""
    sentinel = object()
    calls = {}

    def fake_request_peer(text, button_id):
        calls["args"] = (text, button_id)
        return sentinel

    monkeypatch.setattr(tg, "_HAS_REQUEST_PEER", True)
    monkeypatch.setattr(
        tg.Button, "request_peer", staticmethod(fake_request_peer), raising=False
    )

    rows = tg.ikm([("Pick", "user", 7)])
    assert rows == [[sentinel]]
    assert calls["args"] == ("Pick", 7)


def test_request_peer_button_tl_shape():
    btn = request_peer_button("Pick a user", "user", button_id=3)
    assert isinstance(btn, types.KeyboardButtonRequestPeer)
    assert btn.text == "Pick a user"
    assert btn.button_id == 3
    assert btn.max_quantity == 1
    assert isinstance(btn.peer_type, types.RequestPeerTypeUser)
    assert btn.style is None


def test_request_peer_button_kinds_and_prefix():
    chat = request_peer_button("c", "request_chat")
    assert isinstance(chat.peer_type, types.RequestPeerTypeChat)
    bcast = request_peer_button("b", "broadcast")
    assert isinstance(bcast.peer_type, types.RequestPeerTypeBroadcast)


def test_request_peer_button_peer_kwargs_passthrough():
    btn = request_peer_button("Bots", "user", bot=True, premium=True)
    assert btn.peer_type.bot is True
    assert btn.peer_type.premium is True


def test_request_peer_button_style_and_icon():
    btn = request_peer_button("Pick", "user", style="primary", icon=9)
    assert isinstance(btn.style, types.KeyboardButtonStyle)
    assert (btn.style.bg_primary, btn.style.icon) == (True, 9)


def test_request_peer_button_unknown_kind_raises():
    with pytest.raises(ValueError):
        request_peer_button("x", "group")


def test_ikm_request_tuple_builds_raw_tl_button():
    btn = ikm([("Pick", "request_user", 5)])[0][0]
    assert isinstance(btn, types.KeyboardButtonRequestPeer)
    assert btn.button_id == 5
    assert btn.max_quantity == 1


def test_ikm_request_tuple_full_form():
    btn = ikm([("Pick", "request_chat", 5, 2, "primary", 9)])[0][0]
    assert isinstance(btn, types.KeyboardButtonRequestPeer)
    assert btn.max_quantity == 2
    assert isinstance(btn.peer_type, types.RequestPeerTypeChat)
    assert (btn.style.bg_primary, btn.style.icon) == (True, 9)


# ──────────────────────────────────────────────────────────────────────────
# premium emoji helpers
# ──────────────────────────────────────────────────────────────────────────


def test_emoji_entity_tl_shape():
    ent = rich.emoji_entity(2, 4, 5210952310187248189)
    assert isinstance(ent, types.MessageEntityCustomEmoji)
    assert ent.to_dict() == {
        "_": "MessageEntityCustomEmoji",
        "offset": 2,
        "length": 4,
        "document_id": 5210952310187248189,
    }
    # TL round-trip through bytes keeps the constructor id intact.
    assert bytes(ent)[:4] == types.MessageEntityCustomEmoji.CONSTRUCTOR_ID.to_bytes(
        4, "little"
    )


def test_premium_emoji_default_fallback():
    text, entities = rich.premium_emoji(123)
    assert text == "⭐"
    assert len(entities) == 1
    ent = entities[0]
    assert (ent.offset, ent.length, ent.document_id) == (0, 1, 123)


def test_premium_emoji_astral_fallback_uses_utf16_length():
    text, entities = rich.premium_emoji(123, "😀")  # U+1F600: 1 py char, 2 UTF-16 units
    assert text == "😀"
    assert entities[0].length == 2


def test_premium_emoji_modifier_sequence_fallback():
    # "👍🏽" = U+1F44D U+1F3FD -> 2 py chars, 4 UTF-16 units.
    text, entities = rich.premium_emoji(7, "👍🏽")
    assert text == "👍🏽"
    assert entities[0].length == 4


def test_with_emojis_offsets_and_fallbacks():
    text, entities = rich.with_emojis(
        "😀 Status {ok} and {sparkle}",
        {"{ok}": (111, "✅"), "{sparkle}": 222, "{missing}": 333},
    )
    assert text == "😀 Status ✅ and ⭐"
    assert [e.document_id for e in entities] == [111, 222]
    # offsets are UTF-16 code units: "😀 " is 3 units, "Status " 7 more.
    assert (entities[0].offset, entities[0].length) == (10, 1)
    assert (entities[1].offset, entities[1].length) == (16, 1)
    assert entities == sorted(entities, key=lambda e: e.offset)


def test_with_emojis_skips_missing_placeholders_and_empty_map():
    text, entities = rich.with_emojis("plain", {"{x}": 1})
    assert text == "plain" and entities == []
    assert rich.with_emojis("plain", {}) == ("plain", [])
    assert rich.with_emojis("plain", None) == ("plain", [])


# ──────────────────────────────────────────────────────────────────────────
# rich parse helpers
# ──────────────────────────────────────────────────────────────────────────


def test_mdv3_parse_mode_registration():
    """The fork (pin f029220+) registers "mdv3"/"markdownv3" as parse-mode
    strings; older pins raise ValueError. Instance-passing (``RICH_MD``)
    must work on every pin, so it stays the canonical path in this repo."""
    try:
        mode = sanitize_parse_mode("mdv3")
    except ValueError:
        mode = None  # older fork pin — instance-passing still required
    else:
        assert hasattr(mode, "parse") and hasattr(mode, "unparse")
    assert sanitize_parse_mode(RICH_MD) is RICH_MD


def test_parse_rich_mdv3_premium_emoji_link():
    text, entities = rich.parse_rich("[😀](emoji/5210952310187248189)", "mdv3")
    assert text == "😀"
    custom = [e for e in entities if isinstance(e, types.MessageEntityCustomEmoji)]
    assert len(custom) == 1
    assert custom[0].document_id == 5210952310187248189
    assert (custom[0].offset, custom[0].length) == (0, 2)


def test_parse_rich_mdv3_styles_and_spoiler():
    # New fork contract: "||...||" is the native spoiler delimiter.
    text, entities = rich.parse_rich("**b** ||secret||", "mdv3")
    assert text == "b secret"
    assert any(isinstance(e, types.MessageEntityBold) for e in entities)
    assert any(isinstance(e, types.MessageEntitySpoiler) for e in entities)


def test_parse_rich_mdv3_pipe_spoiler_delim_is_plain_text():
    # The old [text](spoiler) link form no longer produces a spoiler entity
    # — it now yields a plain MessageEntityTextUrl (a literal "spoiler" URL).
    text, entities = rich.parse_rich("[secret](spoiler)", "mdv3")
    assert text == "secret"
    assert not any(isinstance(e, types.MessageEntitySpoiler) for e in entities)
    assert any(isinstance(e, types.MessageEntityTextUrl) for e in entities)


def test_parse_rich_mdv3_blockquote():
    text, entities = rich.parse_rich("> quoted line", "mdv3")
    assert "quoted line" in text
    assert any(isinstance(e, types.MessageEntityBlockquote) for e in entities)


def test_parse_rich_html_tg_emoji():
    text, entities = rich.parse_rich('<tg-emoji emoji-id="999">😀</tg-emoji>', "html")
    assert text == "😀"
    assert entities[0].document_id == 999
    assert isinstance(entities[0], types.MessageEntityCustomEmoji)


def test_parse_rich_custom_markdown_emoji_link():
    text, entities = rich.parse_rich("[😀](emoji/123)", "custom")
    assert text == "😀"
    assert isinstance(entities[0], types.MessageEntityCustomEmoji)
    assert entities[0].document_id == 123


def test_parse_rich_none_mode_and_passthrough_parser():
    assert rich.parse_rich("**raw**", None) == ("**raw**", [])
    assert rich.parse_rich("**b**", RICH_MD)[0] == "b"


def test_parse_rich_unknown_mode_raises():
    with pytest.raises(ValueError):
        rich.parse_rich("x", "bbcode")


def test_unparse_rich_roundtrip_bold():
    text, entities = rich.parse_rich("**bold**", "mdv3")
    assert rich.unparse_rich(text, entities, "mdv3") == "**bold**"


def test_unparse_rich_mdv3_custom_emoji_link():
    out = rich.unparse_rich("😀", [rich.emoji_entity(0, 2, 42)], "mdv3")
    assert out == "[😀](emoji/42)"


# ──────────────────────────────────────────────────────────────────────────
# style presets + button()/rich_ikm()
# ──────────────────────────────────────────────────────────────────────────


def test_style_presets_resolve():
    assert rich.resolve_style(preset="ok") == ("success", None)
    assert rich.resolve_style(preset="DANGER") == ("danger", None)
    assert rich.resolve_style(preset="nonexistent") == (None, None)
    assert rich.resolve_style() == (None, None)


def test_resolve_style_explicit_overrides_preset():
    assert rich.resolve_style(style="primary", preset="ok") == ("primary", None)
    assert rich.resolve_style(icon=5, preset="ok") == ("success", 5)
    assert rich.resolve_style(style=2, preset="back") == ("success", None)


def test_button_inline_with_preset():
    btn = rich.button("Confirm", "yes", preset="ok")
    assert isinstance(btn, types.KeyboardButtonCallback)
    assert btn.data == b"yes"
    assert _styles(btn) == (None, None, True, None)


def test_button_url_with_preset_and_icon():
    btn = rich.button("Docs", url="https://example.com", preset="info", icon=11)
    assert isinstance(btn, types.KeyboardInlineButton)
    assert _styles(btn) == (True, None, None, 11)


def test_button_request_peer_with_peer_kwargs():
    btn = rich.button("Pick bot", request="user", button_id=4, bot=True)
    assert isinstance(btn, types.KeyboardButtonRequestPeer)
    assert btn.button_id == 4
    assert btn.peer_type.bot is True


def test_rich_ikm_mixed_dicts_tuples_and_buttons():
    prebuilt = Button.switch_inline("Find", query="q", same_peer=True)
    rows = rich.rich_ikm(
        [
            [
                {"text": "Confirm", "data": "yes", "preset": "ok"},
                {"text": "Cancel", "data": "no", "preset": "cancel"},
            ],
            [("Docs", "url", "https://example.com", "primary")],
            [prebuilt],
        ]
    )
    assert _styles(rows[0][0]) == (None, None, True, None)
    assert _styles(rows[0][1]) == (None, True, None, None)
    assert isinstance(rows[1][0], types.KeyboardInlineButton)
    assert _styles(rows[1][0]) == (True, None, None, None)
    assert rows[2][0] is prebuilt


def test_rich_ikm_single_dict_row():
    rows = rich.rich_ikm({"text": "Solo", "data": "cb", "style": "primary"})
    assert len(rows) == 1 and len(rows[0]) == 1
    assert _styles(rows[0][0]) == (True, None, None, None)


def test_rich_ikm_bare_button_as_row_passes_through():
    prebuilt = Button.inline("Solo", data="solo")
    rows = rich.rich_ikm([prebuilt])
    assert rows == [[prebuilt]]
    assert rows[0][0] is prebuilt


def test_rich_ikm_bare_button_as_whole_input_passes_through():
    prebuilt = Button.switch_inline("Find", query="q", same_peer=True)
    rows = rich.rich_ikm(prebuilt)
    assert rows == [[prebuilt]]
    assert rows[0][0] is prebuilt


# ──────────────────────────────────────────────────────────────────────────
# package surface + import-time safety
# ──────────────────────────────────────────────────────────────────────────


def test_custom_package_reexports_base_and_rich_names():
    for name in ("ids", "ikm", "request_peer_button"):
        assert name in custom.__all__
        assert callable(getattr(custom, name))
    for name in (
        "RICH_MD",
        "STYLE_PRESETS",
        "button",
        "emoji_entity",
        "parse_rich",
        "premium_emoji",
        "resolve_style",
        "rich_ikm",
        "unparse_rich",
        "with_emojis",
    ):
        assert name in custom.__all__
        assert hasattr(custom, name)
    assert isinstance(STYLE_PRESETS, dict) and STYLE_PRESETS


def test_import_time_safety_no_pyultroid_bootstrap():
    """custom.* must import without triggering the pyUltroid bootstrap —
    assistant/start.py builds ikm keyboards at module import time."""
    code = (
        "import sys\n"
        "import custom, custom.tg, custom.rich\n"
        "leaked = [m for m in sys.modules if m == 'pyUltroid' or m.startswith('pyUltroid.')]\n"
        "assert not leaked, f'pyUltroid bootstrap leaked: {leaked}'\n"
        "print('ok')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout

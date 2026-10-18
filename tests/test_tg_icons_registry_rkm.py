"""Tests for ``custom/tg.py``'s default icon selection, custom button-type
registry, and the reply-keyboard builder ``rkm``.

Covers:
- Default icon auto-selection (``icon=True``): callback-data (cbs) colon
  segment matches (primary), stylized-title matches (secondary, via
  small-caps-to-ASCII folding), type-based fallbacks for structurally
  reliable kinds, per-button ``icon=False``/explicit-id overrides, the
  "no match -> no icon" (no generic gear) rule, and that the feature is a
  no-op unless a caller opts in.
- ``register_button_type`` — decorator and direct-call forms, the built-in
  ``"copy"``/``"toggle"`` registrations, and that it never shadows a
  built-in ikm/rkm type name.
- ``rkm`` — the reply-keyboard sibling of ``ikm``: tuple grammar, pre-built
  passthrough, and the ``resize``/``single_use``/``selective``/``placeholder``
  options.

``custom.tg`` only depends on stdlib + telethon, so these tests import it
directly without the pyUltroid bootstrap (see ``tests/conftest.py``).
"""

from __future__ import annotations

from telethon import Button
from telethon.tl import types

import custom.tg as tg
from custom.tg import (
    DEFAULT_ICON_UNICODE,
    TG_ANDROID_ICON_DOCUMENT_IDS,
    ikm,
    register_button_type,
    rkm,
)


def _styles(btn):
    style = btn.style
    assert isinstance(style, types.KeyboardButtonStyle)
    return style.bg_primary, style.bg_danger, style.bg_success, style.icon


# ──────────────────────────────────────────────────────────────────────────
# default icon tables
# ──────────────────────────────────────────────────────────────────────────


def test_icon_tables_share_the_same_keys():
    assert set(DEFAULT_ICON_UNICODE) == set(TG_ANDROID_ICON_DOCUMENT_IDS)


def test_custom_emoji_ids_are_undiscovered_placeholders():
    """No real TgAndroidIcons document ids are known in this sandbox — every
    entry must stay an honest ``None`` placeholder, never an invented id."""
    assert all(v is None for v in TG_ANDROID_ICON_DOCUMENT_IDS.values())


# ──────────────────────────────────────────────────────────────────────────
# ikm() default icon selection — opt-in via icon=True
# ──────────────────────────────────────────────────────────────────────────


def test_default_icon_is_a_no_op_unless_opted_in():
    """Without icon=True, ikm() behaves exactly as before — no auto icons."""
    btn = ikm([("Back", "cb")])[0][0]
    assert btn.text == "Back"
    assert _styles(btn)[3] is None


def test_default_icon_word_match_prefixes_title():
    btn = ikm([("Back", "cb")], icon=True)[0][0]
    assert btn.text == f"{DEFAULT_ICON_UNICODE['back']} Back"


def test_default_icon_word_match_is_case_insensitive_and_checks_data():
    # "noop" is a common disabled-button callback-data convention; the word
    # match must also look at the callback data, not just the title.
    btn = ikm([(" ", "noop")], icon=True)[0][0]
    assert btn.text == f"{DEFAULT_ICON_UNICODE['noop']}  "


def test_default_icon_type_fallback_for_url():
    btn = ikm([("Docs", "url", "https://example.com")], icon=True)[0][0]
    assert btn.text == f"{DEFAULT_ICON_UNICODE['redirect']} Docs"


def test_default_icon_type_fallback_for_switch_inline():
    btn = ikm([("Find", "switch_inline", "query")], icon=True)[0][0]
    assert isinstance(btn, types.KeyboardInlineButton)
    assert isinstance(btn.type, types.InlineButtonTypeSwitchInline)
    assert btn.text == f"{DEFAULT_ICON_UNICODE['search']} Find"
    assert btn.type.query == "query"


def test_default_icon_generic_callback_with_no_match_gets_no_icon():
    """There is no generic gear/settings fallback: a callback whose data and
    title match nothing recognisable gets no icon at all, not a default one
    — auto-selection must be safe to enable call-wide without icon spam."""
    btn = ikm([("Random", "xyz")], icon=True)[0][0]
    assert btn.text == "Random"
    assert _styles(btn)[3] is None


def test_default_icon_cbs_colon_segment_match_takes_priority_over_title():
    """Structured colon-model callback data (e.g. ``"assistant:games:aki"``)
    is matched segment by segment — this is the primary source, checked
    before the title. Here the title has no matching word at all."""
    btn = ikm([("Aki", "assistant:games:aki")], icon=True)[0][0]
    assert btn.text == f"{DEFAULT_ICON_UNICODE['game']} Aki"


def test_default_icon_cbs_leading_segment_wins_over_later_generic_segment():
    # "back:main:noop" — the first segment ("back") is the semantically
    # dominant one and must win over later segments ("main"/"noop").
    btn = ikm([("Continue", "back:main:noop")], icon=True)[0][0]
    assert btn.text == f"{DEFAULT_ICON_UNICODE['back']} Continue"


def test_default_icon_cbs_unrelated_segments_get_no_icon():
    # None of "assistant"/"cbstuffs"/"menu"/"apiui" is a recognised keyword.
    btn = ikm([("Random", "assistant:cbstuffs:menu:apiui")], icon=True)[0][0]
    assert btn.text == "Random"
    assert _styles(btn)[3] is None


def test_default_icon_title_word_match_folds_stylized_smallcaps():
    """Button captions in this codebase are stylized small-caps Unicode
    (e.g. "« Bᴀᴄᴋ") — the title matcher must fold those to plain ASCII
    before checking keywords, so callback data with no useful words still
    gets an icon from the caption alone."""
    btn = ikm([("« Bᴀᴄᴋ", "assistant:cbstuffs:menu:otvars", "danger")], icon=True)[0][0]
    assert btn.text == f"{DEFAULT_ICON_UNICODE['back']} « Bᴀᴄᴋ"


def test_fold_stylized_folds_small_caps_to_ascii():
    assert tg._fold_stylized("Aᴅᴅᴏɴs") == "Addons"
    assert tg._fold_stylized("« Bᴀᴄᴋ") == "« Back"
    # plain ASCII / unrelated non-ASCII text passes through untouched.
    assert tg._fold_stylized("Plain Text 123") == "Plain Text 123"
    assert tg._fold_stylized("日本語") == "日本語"


def test_default_icon_explicit_icon_element_always_wins():
    """An explicit numeric icon always wins over auto-selection — no title
    mutation, the caller's document id is used verbatim."""
    btn = ikm([("Back", "cb", "primary", 999)], icon=True)[0][0]
    assert btn.text == "Back"
    assert _styles(btn) == (True, None, None, 999)


def test_default_icon_per_button_false_disables_it_even_with_call_level_true():
    btn = ikm([("Back", "cb", "primary", False)], icon=True)[0][0]
    assert btn.text == "Back"
    assert _styles(btn)[3] is None


def test_default_icon_does_not_double_prefix_already_iconified_text():
    btn = ikm([("⬅️ Back", "cb")], icon=True)[0][0]
    assert btn.text == "⬅️ Back"


def test_default_icon_leaves_unrelated_titles_untouched():
    # "A"/"cb" match no known word and no known type — no icon, no gear.
    btn = ikm([("A", "cb")], icon=True)[0][0]
    assert btn.text == "A"
    assert _styles(btn)[3] is None


# ──────────────────────────────────────────────────────────────────────────
# switch_inline — new native ikm tuple type
# ──────────────────────────────────────────────────────────────────────────


def test_ikm_switch_inline_tuple_builds_native_button():
    btn = ikm([("Search", "switch_inline", "cats", True, "primary", 5)])[0][0]
    assert isinstance(btn, types.KeyboardInlineButton)
    assert isinstance(btn.type, types.InlineButtonTypeSwitchInline)
    assert btn.type.query == "cats"
    assert btn.type.same_peer is True
    assert _styles(btn) == (True, None, None, 5)


# ──────────────────────────────────────────────────────────────────────────
# auth/buy/game tuples — icon must not be silently dropped
# ──────────────────────────────────────────────────────────────────────────


def test_ikm_buy_tuple_passes_resolved_icon_through():
    # (text, "buy", data[, icon]) — no style slot in this tuple form.
    btn = ikm([("Buy now", "buy", "sku123", 55)])[0][0]
    assert _styles(btn)[3] == 55


def test_ikm_game_tuple_passes_resolved_icon_through():
    btn = ikm([("Play", "game", "start_game", 77)])[0][0]
    assert _styles(btn)[3] == 77


def test_ikm_auth_tuple_passes_resolved_icon_through():
    btn = ikm([("Login", "auth", "session", 88)])[0][0]
    assert _styles(btn)[3] == 88


def test_ikm_buy_tuple_auto_icon_selection_still_applies():
    # No explicit icon: "buy" is both the type_hint and a self-matching
    # keyword, so auto-selection (icon=True) still resolves an icon name
    # even though this branch has no style slot of its own.
    btn = ikm([("Buy now", "buy", "sku123")], icon=True)[0][0]
    assert btn.text == f"{DEFAULT_ICON_UNICODE['buy']} Buy now"


# ──────────────────────────────────────────────────────────────────────────
# register_button_type — extensible tuple-form registry
# ──────────────────────────────────────────────────────────────────────────


def test_register_button_type_direct_call_form():
    calls = []

    def factory(text, val, btn_tuple):
        calls.append((text, val, btn_tuple))
        return Button.inline(text, data=b"registered")

    register_button_type("my_custom_kind", factory)
    try:
        btn = ikm([("Custom", "my_custom_kind", "payload")])[0][0]
    finally:
        tg._BUTTON_TYPE_REGISTRY.pop("my_custom_kind", None)

    assert isinstance(btn, types.KeyboardInlineButton)
    assert isinstance(btn.type, types.InlineButtonTypeCallback)
    assert btn.type.data == b"registered"
    assert calls == [("Custom", "payload", ("Custom", "my_custom_kind", "payload"))]


def test_register_button_type_decorator_form():
    @register_button_type("my_decorated_kind")
    def factory(text, val, btn_tuple):
        return Button.inline(text, data=b"decorated")

    try:
        btn = ikm([("X", "my_decorated_kind")])[0][0]
    finally:
        tg._BUTTON_TYPE_REGISTRY.pop("my_decorated_kind", None)

    assert btn.type.data == b"decorated"


def test_register_button_type_never_shadows_builtin_url():
    """Registering "url" must not affect ikm's native URL handling."""
    register_button_type("url", lambda *a: Button.inline("hijacked", data=b"x"))
    try:
        btn = ikm([("Docs", "url", "https://example.com")])[0][0]
    finally:
        tg._BUTTON_TYPE_REGISTRY.pop("url", None)
    assert isinstance(btn, types.KeyboardInlineButton)
    assert isinstance(btn.type, types.InlineButtonTypeUrl)
    assert btn.type.url == "https://example.com"


def test_builtin_copy_registration_uses_native_keyboard_button_copy():
    btn = ikm([("Copy code", "copy", "ABC123")])[0][0]
    assert isinstance(btn, types.KeyboardInlineButton)
    assert isinstance(btn.type, types.InlineButtonTypeCopy)
    assert btn.text == "Copy code"
    assert btn.type.copy_text == "ABC123"


def test_builtin_copy_registration_defaults_copy_text_to_button_text():
    btn = ikm([("ABC123", "copy")])[0][0]
    assert isinstance(btn, types.KeyboardInlineButton)
    assert isinstance(btn.type, types.InlineButtonTypeCopy)
    assert btn.type.copy_text == "ABC123"


def test_builtin_toggle_registration_reflects_on_off_state():
    on_btn = ikm([("Notifications", "toggle", "notif_cb", True)])[0][0]
    off_btn = ikm([("Notifications", "toggle", "notif_cb", False)])[0][0]
    assert on_btn.text == "☑️ Notifications"
    assert off_btn.text == "⬜ Notifications"
    assert on_btn.type.data == off_btn.type.data == b"notif_cb"


def test_registered_kind_works_from_rkm_too():
    register_button_type("shared_kind", lambda text, val, btn: Button.text(text))
    try:
        rows = rkm([("Hi", "shared_kind")])
    finally:
        tg._BUTTON_TYPE_REGISTRY.pop("shared_kind", None)
    assert isinstance(rows[0][0], Button)


# ──────────────────────────────────────────────────────────────────────────
# rkm() — reply-keyboard builder
# ──────────────────────────────────────────────────────────────────────────


def test_rkm_bare_strings_become_one_button_rows():
    rows = rkm(["Yes", "No"])
    assert len(rows) == 2
    assert all(isinstance(r[0], Button) for r in rows)
    assert rows[0][0].button.text == "Yes"
    assert rows[1][0].button.text == "No"


def test_rkm_single_string_input():
    rows = rkm("Solo")
    assert len(rows) == 1 and rows[0][0].button.text == "Solo"


def test_rkm_text_tuple_with_style_and_icon():
    # Documented arity: (text, "text", style[, icon]) — no extra placeholder.
    btn = rkm([("Confirm", "text", "success", 42)])[0][0]
    assert isinstance(btn, Button)
    assert _styles(btn.button) == (None, None, True, 42)


def test_rkm_prebuilt_button_passthrough():
    prebuilt = Button.text("Prebuilt", resize=True)
    rows = rkm([prebuilt, "Other"])
    assert rows[0][0] is prebuilt


def test_rkm_location_phone_poll_tuples():
    loc = rkm([("Share location", "location")])[0][0]
    phone = rkm([("Share phone", "phone")])[0][0]
    poll = rkm([("Make a poll", "poll", True)])[0][0]
    assert isinstance(loc.button, types.KeyboardButton)
    assert isinstance(loc.button.type, types.ButtonTypeRequestGeoLocation)
    assert isinstance(phone.button, types.KeyboardButton)
    assert isinstance(phone.button.type, types.ButtonTypeRequestPhone)
    assert isinstance(poll.button, types.KeyboardButton)
    assert isinstance(poll.button.type, types.ButtonTypeRequestPoll)
    assert poll.button.type.quiz is True


def test_rkm_request_peer_tuple_builds_raw_tl_button():
    btn = rkm([("Pick", "request_user", 5, 2, "primary", 9)])[0][0]
    assert isinstance(btn, types.KeyboardButton)
    assert isinstance(btn.type, types.ButtonTypeRequestPeer)
    assert btn.type.max_quantity == 2
    assert (btn.style.bg_primary, btn.style.icon) == (True, 9)


def test_rkm_web_app_tuple_forces_simple_webview():
    btn = rkm([("Open", "web_app", "https://example.com/app")])[0][0]
    assert isinstance(btn, types.KeyboardButton)
    assert isinstance(btn.type, types.ButtonTypeSimpleWebView)
    assert btn.type.url == "https://example.com/app"


def test_rkm_unknown_kind_degrades_to_text_button():
    btn = rkm([("Whatever", "not_a_real_kind")])[0][0]
    assert isinstance(btn, Button)
    assert btn.button.text == "Whatever"


def test_rkm_keyboard_options_propagate_to_every_button():
    rows = rkm(
        ["A", "B"], resize=True, single_use=True, selective=True, placeholder="Pick one"
    )
    for row in rows:
        btn = row[0]
        assert btn.resize is True
        assert btn.single_use is True
        assert btn.selective is True
        assert btn.placeholder == "Pick one"


def test_rkm_keyboard_options_survive_on_raw_tl_buttons_too():
    """request_peer_button()/web_app_button(simple=True) return raw TL
    objects, not Button wrappers — resize/single_use/selective/placeholder
    must still take effect (Telethon's build_reply_markup only reads these
    off Button wrappers, so a row made entirely of raw TL buttons would
    otherwise serialise with all four unset)."""
    rows = rkm(
        [
            [("Pick", "request_user", 1)],
            [("Open", "web_app", "https://example.com/app")],
        ],
        resize=True,
        single_use=True,
        selective=True,
        placeholder="Pick one",
    )
    peer_btn, web_btn = rows[0][0], rows[1][0]

    assert isinstance(peer_btn, Button)
    assert isinstance(peer_btn.button, types.KeyboardButton)
    assert isinstance(peer_btn.button.type, types.ButtonTypeRequestPeer)
    assert (
        peer_btn.resize,
        peer_btn.single_use,
        peer_btn.selective,
        peer_btn.placeholder,
    ) == (
        True,
        True,
        True,
        "Pick one",
    )

    assert isinstance(web_btn, Button)
    assert isinstance(web_btn.button, types.KeyboardButton)
    assert isinstance(web_btn.button.type, types.ButtonTypeSimpleWebView)
    assert (
        web_btn.resize,
        web_btn.single_use,
        web_btn.selective,
        web_btn.placeholder,
    ) == (
        True,
        True,
        True,
        "Pick one",
    )


def test_rkm_keyboard_options_do_not_clear_prebuilt_per_button_values():
    """A call-level option left as ``None`` must not clear a pre-built
    button's own already-set value for that same option."""
    prebuilt = Button.text("Prebuilt", resize=True, selective=True)
    rows = rkm([prebuilt, "Other"], single_use=True)
    btn = rows[0][0]
    assert isinstance(btn, Button)
    assert btn.single_use is True  # applied from the call-level option
    assert btn.resize is True  # preserved, not cleared to None
    assert btn.selective is True  # preserved, not cleared to None


def test_rkm_default_icon_opt_in():
    btn = rkm([("Back", "text")], icon=True)[0][0]
    assert btn.button.text == f"{DEFAULT_ICON_UNICODE['back']} Back"


def test_rkm_empty_input_returns_none():
    assert rkm([]) is None

from __future__ import annotations

from pyUltroid._core.notices import (
    ErrorNotice,
    command_error_messages,
    error_caption,
    sanitize_error_text,
)


def test_error_text_redacts_credentials_and_telegram_tokens():
    text = (
        "bad API_HASH=super-secret-value BOT_TOKEN:123456:"
        "abcdefghijklmnopqrstuvwxyzABCDE SESSION_STRING='abcdef'"
    )
    safe = sanitize_error_text(text)
    assert "super-secret-value" not in safe
    assert "123456:abcdefghijklmnopqrstuvwxyzABCDE" not in safe
    assert "abcdef" not in safe
    assert "API_HASH=[REDACTED]" in safe


def test_error_notice_uses_native_preformatted_detail_and_sanitizes_traceback():
    notice = ErrorNotice(
        summary="Command failed",
        detail="ValueError: invalid input",
        traceback_text=sanitize_error_text("ValueError: BOT_TOKEN=secret-token"),
    )
    rendered = notice.log_text()
    assert "<pre language='python'>" in rendered
    assert "secret-token" not in rendered
    assert "[REDACTED]" in rendered


def test_error_text_redacts_quoted_mapping_bearer_and_multiline_session():
    text = (
        "{'API_KEY': 'quoted-secret'} Authorization: Bearer bearer-secret\n"
        "SESSION_STRING=\n"
        "AbCdEf0123456789+/=" * 8
    )
    safe = sanitize_error_text(text)
    for secret in ("quoted-secret", "bearer-secret", "AbCdEf0123456789"):
        assert secret not in safe


def test_image_caption_markup_is_bounded_after_escaping():
    exception = "<>&" * 100
    caption = error_caption(exception)
    assert len(caption.encode("utf-16-le")) // 2 <= 1000
    assert "&lt;" in caption
    assert "&gt;" in caption
    assert "&amp;" in caption
    assert "…" in caption


def test_command_error_message_preserves_escaped_short_error_and_trigger():
    messages = command_error_messages((".broken arg", [object()]), "ValueError: <bad & input>", "https://t.me/c/1/2")
    assert len(messages) == 1
    assert "<pre>.broken arg</pre>" in messages[0]
    assert "ValueError: &lt;bad &amp; input&gt;" in messages[0]
    assert "[see in the logs channel]" in messages[0]


def test_command_error_long_text_is_delivered_in_bounded_complete_chunks():
    import html

    exception = "ValueError: " + ("<>&😀" * 3000)
    messages = command_error_messages(".boom", exception)
    assert len(messages) > 1
    assert all(len(message.encode("utf-16-le")) // 2 <= 4096 for message in messages)
    decoded = "".join(
        html.unescape(message.split("<pre>")[-1].split("</pre>")[0])
        for message in messages
    )
    assert decoded == exception

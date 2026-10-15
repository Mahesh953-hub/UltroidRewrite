"""Tests for the unified inline-article rendering (``article_rich``).

Covers the follow-up that made ``pyUltroid/_core/inlineresults.py``'s
``article_rich`` delegate its block -> HTML/text rendering entirely to
``pyUltroid/_core/richmsg.py`` (the ``RichBlocks`` block model), instead of
keeping a second, "deliberately minimal" renderer in ``inlineresults.py``,
and dropped ``assistant/inlinestuff.py``'s ``_simple_blocks`` degradation
adapter now that ``article_rich`` understands every ``RichBlocks`` block
type (including ``table``/``expandable``) on its own.

What must keep working, regardless of which module renders it:

* a raw HTML string handed to ``article_rich``/its helpers passes through
  verbatim (never re-escaped or reinterpreted as a block);
* plain strings inside a block list/iterable still work (no need to
  hand-build a dict or a :class:`RichBlocks`);
* the loose dict shapes the old minimal renderer accepted --
  ``{"type": "header"|"paragraph"|"code", "text": ...}`` and
  ``{"type": "list", "items": [...], "ordered": bool}`` -- still render;
* a full :class:`RichBlocks` (including ``table()``/``expandable()``,
  which the old minimal renderer could not render at all) renders
  correctly on both the native-HTML and plain-text-fallback paths.

``pyUltroid._core.inlineresults``/``pyUltroid._core.richmsg`` only depend
on stdlib + telethon, so this imports them directly without the pyUltroid
production bootstrap (the repo-root ``sys.path`` insertion lives in
``tests/conftest.py``).
"""

from __future__ import annotations

import asyncio

import pytest
from telethon.tl import types

import pyUltroid._core.inlineresults as inlineresults
import pyUltroid._core.richmsg as richmsg
from pyUltroid._core.inlineresults import (
    InlineResults,
    _blocks_to_html,
    _blocks_to_text,
)
from pyUltroid._core.richmsg import RichBlocks

# ──────────────────────────────────────────────────────────────────────────
# a minimal fake client good enough for InlineBuilder.article()/article_rich()
# ──────────────────────────────────────────────────────────────────────────


class _FakeClient:
    """Just enough of ``TelegramClient`` for ``InlineBuilder.article()``
    (via ``InlineResults.article_rich``'s plain-``article`` fallback) and
    for ``InlineResults.article_rich``'s own native-path ``reply_markup``
    build -- no network, no real parsing."""

    def build_reply_markup(self, buttons):
        return None

    async def _parse_message_text(self, text, parse_mode):
        # No real MarkdownV3/HTML parsing needed for these tests -- return
        # the text untouched with no entities, mirroring what happens when
        # parse_mode is falsy/unavailable.
        return text, []


def _run(coro):
    return asyncio.run(coro)


# ──────────────────────────────────────────────────────────────────────────
# 1. raw HTML string passes through verbatim
# ──────────────────────────────────────────────────────────────────────────


def test_blocks_to_html_passes_raw_html_verbatim():
    raw = "<p>already &amp; valid <b>HTML</b></p>"
    assert _blocks_to_html(raw) == raw


def test_blocks_to_text_passes_raw_html_verbatim():
    raw = "<p>already &amp; valid <b>HTML</b></p>"
    assert _blocks_to_text(raw) == raw


def test_article_rich_html_passthrough_end_to_end():
    ires = InlineResults(_FakeClient())
    raw = "<h1>Title</h1><p>Body &amp; more</p>"
    result = _run(ires.article_rich("T", raw))
    assert isinstance(result, types.InputBotInlineResult)
    rich_message = result.send_message.rich_message
    assert isinstance(rich_message, types.InputRichMessageHTML)
    assert rich_message.html == raw


# ──────────────────────────────────────────────────────────────────────────
# 2. plain strings inside a block list
# ──────────────────────────────────────────────────────────────────────────


def test_blocks_to_html_accepts_plain_strings():
    html = _blocks_to_html(["Hello", "World"])
    assert html == "<p>Hello</p><p>World</p>"


def test_blocks_to_text_accepts_plain_strings():
    text = _blocks_to_text(["Hello", "World"])
    assert text == "Hello\n\nWorld"


def test_blocks_to_html_escapes_plain_strings():
    html = _blocks_to_html(["<script>evil()</script>"])
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


# ──────────────────────────────────────────────────────────────────────────
# 3. loose dict shapes {"type": "header"|"paragraph"|"code", "text": ...}
#    and {"type": "list", "items": [...], "ordered": bool}
# ──────────────────────────────────────────────────────────────────────────


def test_blocks_to_html_loose_header_paragraph_code():
    blocks = [
        {"type": "header", "text": "Heading"},
        {"type": "paragraph", "text": "Body text"},
        {"type": "code", "text": "x = 1", "lang": "py"},
    ]
    html = _blocks_to_html(blocks)
    assert "<h1>Heading</h1>" in html
    assert "<p>Body text</p>" in html
    assert '<code class="language-py">x = 1</code>' in html


def test_blocks_to_html_loose_list_ordered_and_unordered():
    ordered = _blocks_to_html([{"type": "list", "items": ["a", "b"], "ordered": True}])
    assert ordered == "<ol><li>a</li><li>b</li></ol>"

    bulleted = _blocks_to_html([{"type": "list", "items": ["a", "b"]}])
    assert bulleted == "<ul><li>a</li><li>b</li></ul>"


def test_blocks_to_text_loose_shapes():
    blocks = [
        {"type": "header", "text": "Heading"},
        {"type": "paragraph", "text": "Body text"},
        {"type": "list", "items": ["a", "b"], "ordered": False},
        {"type": "code", "text": "x = 1"},
    ]
    text = _blocks_to_text(blocks)
    assert "Heading" in text
    assert "Body text" in text
    assert "a" in text and "b" in text
    assert "x = 1" in text


def test_loose_dict_missing_optional_keys_does_not_raise():
    # No "level" on header, no "lang" on code, no "ordered" on list --
    # every optional key must fall back to a sane default rather than
    # KeyError/TypeError.
    blocks = [
        {"type": "header", "text": "H"},
        {"type": "code", "text": "c"},
        {"type": "list", "items": ["x"]},
    ]
    assert _blocks_to_html(blocks)
    assert _blocks_to_text(blocks)


# ──────────────────────────────────────────────────────────────────────────
# 4. a full RichBlocks (the single block model) renders correctly
# ──────────────────────────────────────────────────────────────────────────


def test_blocks_to_html_richblocks_matches_richmsg_native_render():
    blocks = RichBlocks().header("Stats", level=2).paragraph("hello").list(["a", "b"])
    expected, _files = richmsg.render_blocks_native_html(
        richmsg.normalize_blocks(blocks)
    )
    assert _blocks_to_html(blocks) == expected


def test_blocks_to_text_richblocks_matches_richmsg_plain_text():
    blocks = RichBlocks().header("Stats", level=2).paragraph("hello").list(["a", "b"])
    expected = richmsg.blocks_to_plain_text(richmsg.normalize_blocks(blocks))
    assert _blocks_to_text(blocks) == expected


def test_article_rich_richblocks_end_to_end():
    ires = InlineResults(_FakeClient())
    blocks = RichBlocks().header("Title").paragraph("Body")
    result = _run(ires.article_rich("T", blocks))
    rich_message = result.send_message.rich_message
    assert isinstance(rich_message, types.InputRichMessageHTML)
    assert rich_message.html == "<h1>Title</h1><p>Body</p>"


# ──────────────────────────────────────────────────────────────────────────
# 5. table()/expandable() -- the degradation path _simple_blocks used to
#    special-case in assistant/inlinestuff.py -- now render natively (HTML)
#    and degrade sensibly on the plain-text fallback, with no adapter.
# ──────────────────────────────────────────────────────────────────────────


def test_table_block_renders_natively_as_html_table():
    blocks = RichBlocks().table([["Field", "Value"], ["A", "1"]], caption="Overview")
    html = _blocks_to_html(blocks)
    assert "<table" in html
    assert "<caption>Overview</caption>" in html
    assert "<th>Field</th>" in html
    assert "<td>A</td>" in html


def test_table_block_degrades_to_aligned_text_grid_on_fallback():
    blocks = RichBlocks().table([["Field", "Value"], ["A", "1"]], caption="Overview")
    text = _blocks_to_text(blocks)
    # blocks_to_plain_text renders a table via richmsg.table_to_text --
    # caption + aligned columns, never blank/dropped.
    assert "Overview" in text
    assert "Field" in text and "Value" in text
    assert "A" in text and "1" in text


def test_expandable_block_renders_natively_as_blockquote():
    blocks = RichBlocks().expandable("Details", "Everything is fine.")
    html = _blocks_to_html(blocks)
    assert "<blockquote expandable>" in html
    assert "Details" in html
    assert "Everything is fine." in html


def test_expandable_block_degrades_to_title_and_body_on_fallback():
    blocks = RichBlocks().expandable("Details", "Everything is fine.")
    text = _blocks_to_text(blocks)
    assert "Details" in text
    assert "Everything is fine." in text


def test_article_rich_table_and_expandable_end_to_end_native():
    ires = InlineResults(_FakeClient())
    blocks = (
        RichBlocks()
        .header("Report")
        .table([["Plugins", "42"], ["Addons", "7"]], caption="Overview")
        .expandable("More", "details here")
    )
    result = _run(ires.article_rich("T", blocks))
    html = result.send_message.rich_message.html
    assert "<table" in html
    assert "Overview" in html
    assert "<blockquote expandable>" in html


def test_article_rich_table_and_expandable_fallback_when_rich_unavailable(monkeypatch):
    # Force the "no native Rich Message support" branch so article_rich
    # degrades to a plain mdv3 article -- the same branch that used to
    # rely on assistant/inlinestuff.py's _simple_blocks pre-degrading
    # table()/expandable() blocks before handing them to the (deleted)
    # minimal renderer. It must now render a legible summary on its own.
    monkeypatch.setattr(inlineresults, "has", lambda kind: False)
    ires = InlineResults(_FakeClient())
    blocks = (
        RichBlocks()
        .header("Report")
        .table([["Plugins", "42"], ["Addons", "7"]], caption="Overview")
        .expandable("More", "details here")
    )
    result = _run(ires.article_rich("T", blocks))
    assert isinstance(result, types.InputBotInlineResult)
    text = result.send_message.message
    assert "Overview" in text
    assert "Plugins" in text and "42" in text
    assert "More" in text
    assert "details here" in text


# ──────────────────────────────────────────────────────────────────────────
# assistant/inlinestuff.py no longer defines _simple_blocks at all
# ──────────────────────────────────────────────────────────────────────────


def test_simple_blocks_adapter_is_gone():
    import ast
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "assistant" / "inlinestuff.py"
    tree = ast.parse(src.read_text())
    names = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    assert "_simple_blocks" not in names


def test_inlineresults_minimal_renderers_are_gone():
    for name in ("_block_to_html", "_escape"):
        assert not hasattr(inlineresults, name)


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-q"]))

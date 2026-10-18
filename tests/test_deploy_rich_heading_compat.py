"""The observed RICH_MESSAGE_BLOCK_UNSUPPORTED guide must preserve native details."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
from telethon import types

from pyUltroid._core.richmsg import RichBlocks, rich_message

ROOT = Path(__file__).resolve().parents[1]


def _guide():
    path = ROOT / "plugins/_inline.py"
    nodes = [node for node in ast.parse(path.read_text()).body if isinstance(node, ast.FunctionDef) and node.name == "_deploy_guide_blocks"]
    namespace = {"RichBlocks": RichBlocks}
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), str(path), "exec"), namespace)  # noqa: S102 - isolated guide helper
    return namespace["_deploy_guide_blocks"]("Example User")


def _descendants(blocks, kind):
    for block in blocks:
        if isinstance(block, kind):
            yield block
        yield from _descendants(getattr(block, "blocks", ()) or (), kind)


def test_guide_uses_compatible_title_and_preserves_all_seven_details_and_code():
    native = rich_message(_guide())
    assert isinstance(native, types.InputRichMessage)
    assert isinstance(native.blocks[0], types.PageBlockParagraph)
    assert isinstance(native.blocks[0].text, types.TextBold)
    assert "Deploy your own Ultroid" in native.blocks[0].text.text.text
    details = [block for block in native.blocks if isinstance(block, types.PageBlockDetails)]
    assert len(details) == 7
    assert details[3].open is False
    assert sum(1 for block in _descendants(native.blocks, types.PageBlockPreformatted)) >= 7
    assert isinstance(native.blocks[-1].text, types.TextItalic)
    assert bytes(native)


@pytest.mark.parametrize("level", range(1, 7))
def test_native_richblocks_heading_is_bold_paragraph_not_instant_view_title(level):
    native = rich_message(RichBlocks(native=True).header("Sample heading", level=level))
    assert len(native.blocks) == 1
    assert isinstance(native.blocks[0], types.PageBlockParagraph)
    assert isinstance(native.blocks[0].text, types.TextBold)
    assert native.blocks[0].text.text.text == "Sample heading"

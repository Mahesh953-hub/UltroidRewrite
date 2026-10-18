"""Structural regression tests for the native nested deploy guide."""
from __future__ import annotations

import ast
from pathlib import Path

from telethon import types
from telethon.extensions import BinaryReader

ROOT = Path(__file__).resolve().parents[1]


def _guide():
    path = ROOT / "plugins/_inline.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_deploy_guide_blocks")
    namespace = {}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), str(path), "exec"), namespace)  # noqa: S102
    return namespace["_deploy_guide_blocks"]("Example User")


def _text(node):
    if isinstance(node, str):
        return node
    value = getattr(node, "text", None)
    if isinstance(value, str):
        return value
    return "".join(_text(child) for child in (value if isinstance(value, list) else [value]) if child is not None)


def _descendants(blocks, kind):
    for block in blocks:
        if isinstance(block, kind):
            yield block
        yield from _descendants(getattr(block, "blocks", ()) or (), kind)


def test_seven_numbered_outer_steps_and_install_start_addon_open_states():
    native = _guide()
    assert isinstance(native, types.InputRichMessage)
    steps = [block for block in native.blocks if isinstance(block, types.PageBlockDetails)]
    assert len(steps) == 7
    assert [_text(step.title) for step in steps] == [f"Step {i} ·" + title for i, title in enumerate((" Clone the repository", " Install requirements", " Fill in your variables", " Start Ultroid", " Complete private first-run login", " Keep it running", " Use custom addons"), 1)]
    assert [step.open for step in steps] == [True, False, False, False, False, False, False]
    decoded = BinaryReader(bytes(native)).tgread_object()
    decoded_steps = [block for block in decoded.blocks if isinstance(block, types.PageBlockDetails)]
    assert [bool(step.open) for step in decoded_steps] == [True, False, False, False, False, False, False]
    install = [b for b in _descendants(steps, types.PageBlockDetails) if _text(b.title) in {"2a · pip", "2b · uv"}]
    assert {_text(b.title) for b in install} == {"2a · pip", "2b · uv"}
    assert all(not b.open for b in install)
    opened = {_text(b.title) for b in _descendants(steps, types.PageBlockDetails) if b.open}
    assert opened == {"Step 1 · Clone the repository", "4a · Run as a Python module", "4b · Run with uv", "7a · Enable addons", "7b · Use a channel", "7c · Use a repository"}


def test_code_copy_rows_are_immediate_and_exact_and_urls_are_in_details():
    guide = _guide()
    for code in _descendants(guide.blocks, types.PageBlockPreformatted):
        assert code.language
        parent_children = None
        for details in _descendants(guide.blocks, types.PageBlockDetails):
            for index, child in enumerate(details.blocks):
                if child is code:
                    parent_children = details.blocks
                    assert index + 1 < len(parent_children)
                    row = parent_children[index + 1]
                    assert isinstance(row, types.PageBlockButtonRow)
                    copy = [button for button in row.buttons if isinstance(button.type, types.InlineButtonTypeCopy)]
                    assert len(copy) == 1
                    assert copy[0].type.copy_text == code.text.text
                    break
        assert parent_children is not None
    for details in _descendants(guide.blocks, types.PageBlockDetails):
        for child in details.blocks:
            if isinstance(child, types.PageBlockButtonRow):
                assert all(isinstance(button.type, (types.InlineButtonTypeUrl, types.InlineButtonTypeCopy)) for button in child.buttons)
    urls = list(_descendants(guide.blocks, types.PageBlockButtonRow))
    assert any(any(isinstance(button.type, types.InlineButtonTypeUrl) for button in row.buttons) for row in urls)
    table = next(_descendants(guide.blocks, types.PageBlockTable))
    cells = [cell for row in table.rows for cell in row.cells]
    assert any(isinstance(cell.text, types.TextUrl) for cell in cells)


def test_examples_security_accuracy_and_native_roundtrip():
    guide = _guide()
    encoded = bytes(guide)
    assert encoded
    decoded = BinaryReader(encoded).tgread_object()
    assert isinstance(decoded, types.InputRichMessage)
    assert bytes(decoded) == encoded
    all_text = "\n".join(_text(block) for block in _descendants(guide.blocks, types.PageBlockParagraph)) + "\n" + "\n".join(code.text.text for code in _descendants(guide.blocks, types.PageBlockPreformatted))
    assert "YOUR_API_ID" in all_text and "YOUR_API_HASH" in all_text
    assert "YOUR_SESSION" not in all_text
    assert "never paste a token into the clone URL" in all_text
    assert "one instance per session" in all_text and "ONE method" in all_text
    assert "PLUGIN_CHANNEL is a database list, not an .env variable" in all_text
    assert "ADDONS_URL" in all_text and "database key, not an .env variable" in all_text
    warning = next(block for block in guide.blocks if isinstance(block, types.PageBlockParagraph) and isinstance(block.text, types.TextMarked))
    assert isinstance(warning.text.text, types.TextBold)
    assert "SESSION string" in _text(warning) and "MONGO_URI" in _text(warning)

"""Branding + /agent menu contract tests.

The assistant /agent menu previously carried a ``.ᴛᴇʟᴇᴛʜᴏɴ`` small-caps
badge on every screen; this suite pins its removal and the fact that every
cloud provider the agent runtime knows about (``pyUltroid.agent._config``)
is configurable from the menu.

``assistant/agent.py`` imports the full runtime (``from . import *``), so
these checks parse the source instead of importing it.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT_SRC = ROOT / "assistant" / "agent.py"
HELP_SRC = ROOT / "plugins" / "_help.py"


def _module(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def test_no_telethon_badge_remains():
    for path in (AGENT_SRC, HELP_SRC):
        assert "ᴛᴇʟᴇᴛʜᴏɴ" not in path.read_text(encoding="utf-8"), (
            f"{path} still carries the .telethon small-caps badge"
        )


def _cloud_providers() -> dict:
    for node in ast.walk(_module(AGENT_SRC)):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "CLOUD_PROVIDERS":
                    return ast.literal_eval(node.value)
    raise AssertionError("CLOUD_PROVIDERS not found in assistant/agent.py")


def test_menu_covers_every_runtime_cloud_provider():
    """Every provider _config understands and the menu exposes must be
    configurable from /agent; a provider the runtime supports but the menu
    omits is unconfigurable for non-technical owners."""
    menu = set(_cloud_providers())
    # Every menu provider must be known to the runtime config...
    from pyUltroid.agent._config import PROVIDERS

    assert menu <= set(PROVIDERS), f"menu offers unknown providers: {menu - set(PROVIDERS)}"
    # ...and the key-based runtime providers must all appear in the menu.
    key_based = {
        name
        for name, spec in PROVIDERS.items()
        if spec.get("needs_key") and name not in ("openai_compat", "claude")
    }
    missing = key_based - menu
    assert not missing, f"runtime providers missing from the /agent menu: {missing}"


def test_llama_fetch_does_not_double_append_v1():
    """OpenAI-compatible local bases that already end in /v1 (LM Studio,
    TGI, vLLM defaults) must not grow a second /v1 on model fetch."""
    src = AGENT_SRC.read_text(encoding="utf-8")
    assert 'base.endswith("/v1")' in src
    assert '/v1/v1/models' not in src

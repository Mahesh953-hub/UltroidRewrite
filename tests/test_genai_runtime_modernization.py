"""Static regression checks for provider-neutral GenAI runtime wiring."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_plugins_use_shared_runtime_without_gemini_rest_or_key_gate():
    for name in ("genai.py", "genai_module.py"):
        source = (ROOT / "plugins" / name).read_text()
        assert "AgentRequest" in source and "runtime_run" in source
        assert "generativelanguage.googleapis.com" not in source
        assert "GEMINI_API_KEY" not in source and "api_key" not in source


def test_runtime_history_is_scoped_to_chat_and_sender_and_cleared():
    source = (ROOT / "plugins" / "genai.py").read_text()
    assert 'f"genai:{chat_id}:{user_id' in source
    assert "clear_history(self.history_key" in source


def test_reply_context_ignore_flag_and_explicit_media_limit_are_present():
    source = (ROOT / "plugins" / "genai.py").read_text()
    assert "Context from the replied message" in source
    assert 'endswith("-i")' in source
    assert "cannot interpret attached media" in source

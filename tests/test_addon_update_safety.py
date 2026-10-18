import ast
import base64
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace


def _load_update_helper():
    path = Path(__file__).resolve().parents[1] / "pyUltroid" / "startup" / "loader.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_pull_addons_if_safe"
    )

    class Logs:
        def __init__(self):
            self.warnings = []

        def warning(self, *args):
            self.warnings.append(args)

    logs = Logs()
    namespace = {"base64": base64, "os": os, "subprocess": subprocess, "LOGS": logs}
    module = ast.Module(body=[helper], type_ignores=[])
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102
    return namespace["_pull_addons_if_safe"], logs


def _result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_dirty_addon_checkout_is_never_pulled(monkeypatch, tmp_path):
    update, logs = _load_update_helper()
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return _result(stdout=" M plugins/local.py\n?? local-note.txt\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert update(str(tmp_path), "test-token") is False
    assert [args for args, _ in calls] == [["git", "status", "--porcelain"]]
    assert logs.warnings[0][0] == (
        "[loader] skipping add-on update: preserving %d local worktree paths"
    )
    assert logs.warnings[0][1] == 2


def test_clean_addon_checkout_skips_pull_without_token(monkeypatch, tmp_path):
    update, logs = _load_update_helper()
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return _result(stdout="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert update(str(tmp_path), None) is False
    assert [args for args, _ in calls] == [["git", "status", "--porcelain"]]
    assert "GIT_TOKEN is not configured" in logs.warnings[0][0]


def test_clean_addon_pull_uses_ephemeral_auth_and_captures_failure(
    monkeypatch, tmp_path
):
    update, logs = _load_update_helper()
    monkeypatch.setenv("GIT_CONFIG_COUNT", "0")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        if args == ["git", "status", "--porcelain"]:
            return _result(stdout="")
        return _result(returncode=128, stderr="authentication detail must not leak")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert update(str(tmp_path), "test-token") is False
    assert [args for args, _ in calls] == [
        ["git", "status", "--porcelain"],
        ["git", "pull", "-q"],
    ]
    pull_kwargs = calls[1][1]
    assert pull_kwargs["capture_output"] is True
    assert pull_kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert (
        pull_kwargs["env"]["GIT_CONFIG_KEY_0"] == "http.https://github.com/.extraheader"
    )
    expected = base64.b64encode(b"x-access-token:test-token").decode()
    assert (
        pull_kwargs["env"]["GIT_CONFIG_VALUE_0"] == f"AUTHORIZATION: basic {expected}"
    )
    assert logs.warnings[0][0].startswith("[loader] add-on update failed")
    assert "authentication detail must not leak" not in repr(logs.warnings)

"""Plugin-load non-regression harness.

Promoted from the Wave-0 exploration harness (``/tmp/plugload_test.py``):
runs the real :class:`pyUltroid._core.loader.AsyncPluginLoader` over every
plugin tree (``plugins/``, ``assistant/``, ``assistant/manager/``, ``vc/``)
with mocked Telethon clients and asserts the loaded-counts never drop below
the recorded baseline.

Design notes
------------
* **Subprocess isolation.** The bootstrap imports the full ``pyUltroid``
  package, which mutates process-global state (``sys.modules``, Telethon
  session classes, ``os.environ``). To keep the rest of the suite hermetic,
  this file doubles as the worker: the pytest fixtures re-execute it as
  ``python tests/test_plugin_load.py --worker`` in a subprocess, and the
  worker emits its result as one ``PLUGLOAD_JSON:``-prefixed JSON line.
* **Skip-tolerant.** A plugin that fails because an *optional* dependency
  is not installed (``ModuleNotFoundError: ...`` or the loader's
  ``Missing plugin dependencies: ...`` gate) counts as skipped-not-regressed,
  not as a failure. Only the loaded-count floors below are asserted, plus
  the rule that every load error is either a skip or an allow-listed known
  issue. See ``tests/README.md`` for the baseline-count philosophy.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SENTINEL = "PLUGLOAD_JSON:"

# (path, loader key) pairs, mirroring how ``pyUltroid/__main__.py`` loads them.
TREES = (
    ("plugins", "Official"),
    ("assistant", "Assistant"),
    ("assistant/manager", "Group Manager"),
    ("vc", "VC"),
)

# Recorded loaded-count floors (exploration baseline, main@a5e678b).
# "Never drop below" — a *higher* count is always fine; raise the floor in
# the same commit that makes more plugins load.
LOADED_FLOOR = {
    # 88 files; 71 load with only requirements.txt installed, 73 once the
    # W0-A vc/plugin deps (psutil, yt-dlp) are present.
    "plugins": 71,
    # 8 files; 4 load with only requirements.txt installed.
    "assistant": 4,
    # 6/6 — no optional dependencies involved; must stay exact.
    "assistant/manager": 6,
    # 14 modules; floor applies only when VC_STACK_DEPS are all importable.
    # Raised 13 → 14 by W1-A "vc-stack": vc/_youtube.py now defines
    # YouTubeData, so vc/_pytgcalls.py imports cleanly (acceptance: 14/14).
    "vc": 14,
}

# The vc/ import closure beyond stdlib/repo code. requirements.txt supplies
# py-tgcalls (pytgcalls/ntgcalls), pydantic, aiofiles; aiohttp/httpx/Crypto
# arrive transitively; mutagen/psutil/yt-dlp/youtube-search-python are the
# W0-A additions. The vc floor is gated on ALL of these being importable.
VC_STACK_DEPS = (
    "mutagen",
    "psutil",
    "yt_dlp",
    "youtubesearchpython",
    "pytgcalls",
    "ntgcalls",
    "httpx",
    "aiohttp",
    "aiofiles",
    "pydantic",
    "Crypto",
)

# Pre-existing code bugs that are NOT optional-dependency skips. Maps the
# loader's plugin id to a substring that must appear in its error string.
# Each entry names its owning wave; remove the entry (and raise the floor)
# when the owning wave lands the fix. (Empty since W1-A fixed the
# vc._pytgcalls YouTubeData import; kept for future waves.)
KNOWN_ISSUES = {
    # This env's pydantic 1.10.26 build (py3.14) does not expose TypeAdapter,
    # so the fastapi-dependent webserver import chain fails before the plugin
    # body runs. An env with a spec-compliant pydantic loads the plugin; the
    # entry only masks this exact build gap.
    "plugins.webtools": "TypeAdapter",
}


def _is_optional_dep_skip(error: str) -> bool:
    """True when *error* is a missing-optional-dependency skip, not a bug."""
    return error.startswith("ModuleNotFoundError: ") or error.startswith(
        "Missing plugin dependencies: "
    )


def _unexplained_errors(tree: dict) -> list:
    """Load errors that are neither dependency skips nor known issues."""
    unexplained = []
    for entry in tree["errors"]:
        plugin, error = entry["plugin"], entry["error"]
        if _is_optional_dep_skip(error):
            continue
        known = KNOWN_ISSUES.get(plugin)
        if known and known in error:
            continue
        unexplained.append(f"{plugin}: {error}")
    return unexplained


def _format_errors(tree: dict) -> str:
    return "; ".join(f"{e['plugin']}: {e['error']}" for e in tree["errors"]) or "none"


def _worker_main() -> int:
    """Run the real loader over all plugin trees and print a JSON report.

    Executed in a subprocess (see module docstring). Always prints exactly
    one SENTINEL-prefixed JSON line; a hard crash is reported under the
    ``fatal`` key so the parent test can fail with diagnostics.
    """
    import traceback
    from unittest.mock import AsyncMock

    os.chdir(ROOT)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    # Forced dummy credentials: the bootstrap only validates their *shape*
    # (no network happens — client start/connect are mocked below), and
    # forcing dummies keeps the run deterministic on machines that export
    # real secrets. Never log the inherited environment in this worker.
    os.environ["SESSION"] = "1" + "A" * 352
    os.environ["API_ID"] = "12345"
    os.environ["API_HASH"] = "abcdef"
    os.environ["BOT_TOKEN"] = "123:abc"
    os.environ["LOG_CHANNEL"] = "-10012345"

    report: dict = {}
    try:
        import telethon.sessions

        def dummy_string_session_init(self, string=None):
            self._dc_id = 2
            self._server_address = "149.154.167.51"
            self._port = 443
            self._auth_key = b"A" * 256

        telethon.sessions.StringSession.__init__ = dummy_string_session_init

        from telethon import TelegramClient

        class DummyUser:
            id = 123456789
            first_name = "Mocked User"
            last_name = "Bot"
            username = "mocked_user_bot"
            bot = False
            bot_inline_placeholder = "Placeholder"

        async def mock_start(self, *args, **kwargs):
            self.me = DummyUser()
            bot_token = kwargs.get("bot_token") or (args[0] if args else None)
            self.me.bot = bool(bot_token)
            self._bot = bool(bot_token)
            return self

        TelegramClient.start = mock_start
        TelegramClient.connect = AsyncMock()
        TelegramClient.get_me = AsyncMock(return_value=DummyUser())
        TelegramClient.get_entity = AsyncMock(return_value=DummyUser())

        # pyUltroid/configs.py reads sys.argv[1:] as API_ID/API_HASH/SESSION
        # overrides; replace argv wholesale so the harness's own "--worker"
        # flag is never parsed as a credential.
        sys.argv = ["-m"]
        import pyUltroid  # noqa: F401 - the shell; imports boot nothing

        from pyUltroid.ultroid import core

        core.run()
        from pyUltroid import ultroid_bot
        from pyUltroid._core.loader import AsyncPluginLoader

        async def run() -> dict:
            results = {}
            for path, key in TREES:
                loader = AsyncPluginLoader(path=path, key=key)
                try:
                    ok, err, errors = await loader.load_all(log=False)
                    results[path] = {"loaded": ok, "errored": err, "errors": errors}
                except Exception as exc:  # noqa: BLE001 - report, don't hide
                    results[path] = {
                        "loaded": -1,
                        "errored": -1,
                        "errors": [
                            {
                                "plugin": path,
                                "error": f"FATAL {exc.__class__.__name__}: {exc}",
                            }
                        ],
                    }
            return results

        report = ultroid_bot.run_in_loop(run())
    except Exception:  # noqa: BLE001 - surface the crash to the parent test
        report = {"fatal": traceback.format_exc()[-3000:]}
    print(SENTINEL + json.dumps(report))
    return 0


@pytest.fixture(scope="module")
def load_report(repo_root: Path) -> dict:
    """Run the harness worker once per test module and parse its report."""
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--worker"],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        timeout=600,
    )
    line = next((l for l in proc.stdout.splitlines() if l.startswith(SENTINEL)), None)
    assert line is not None, (
        "plugin-load worker produced no report line "
        f"(exit={proc.returncode})\n--- stdout tail ---\n{proc.stdout[-3000:]}"
        f"\n--- stderr tail ---\n{proc.stderr[-3000:]}"
    )
    report = json.loads(line[len(SENTINEL) :])
    assert not report.get("fatal"), f"plugin-load worker crashed:\n{report['fatal']}"
    missing = {path for path, _ in TREES} - set(report)
    assert not missing, f"worker report missing trees: {sorted(missing)}"
    return report


class TestPluginLoadBaseline:
    def test_plugins_loaded_never_below_baseline(self, load_report):
        tree = load_report["plugins"]
        assert tree["loaded"] >= LOADED_FLOOR["plugins"], (
            f"plugins loaded {tree['loaded']} < baseline {LOADED_FLOOR['plugins']}"
            f"\nerrors: {_format_errors(tree)}"
        )

    def test_assistant_loaded_never_below_baseline(self, load_report):
        tree = load_report["assistant"]
        assert tree["loaded"] >= LOADED_FLOOR["assistant"], (
            f"assistant loaded {tree['loaded']} < baseline {LOADED_FLOOR['assistant']}"
            f"\nerrors: {_format_errors(tree)}"
        )

    def test_assistant_manager_loads_completely(self, load_report):
        tree = load_report["assistant/manager"]
        assert tree["loaded"] == 6 and tree["errored"] == 0, (
            f"assistant/manager must load 6/6, got loaded={tree['loaded']} "
            f"errored={tree['errored']}\nerrors: {_format_errors(tree)}"
        )

    def test_vc_loaded_never_below_baseline(self, load_report):
        tree = load_report["vc"]
        missing_deps = [
            dep for dep in VC_STACK_DEPS if importlib.util.find_spec(dep) is None
        ]
        if missing_deps:
            # vc/ cannot load without its stack; every module must then be a
            # dependency skip, which test_no_unexplained_load_errors enforces.
            # The floor only applies once the stack is importable.
            pytest.skip(f"vc stack not fully installed ({', '.join(missing_deps)})")
        assert tree["loaded"] >= LOADED_FLOOR["vc"], (
            f"vc loaded {tree['loaded']} < baseline {LOADED_FLOOR['vc']} "
            f"(vc stack fully importable)\nerrors: {_format_errors(tree)}"
        )

    def test_no_unexplained_load_errors(self, load_report):
        failures = {}
        for path, _ in TREES:
            unexplained = _unexplained_errors(load_report[path])
            if unexplained:
                failures[path] = unexplained
        assert not failures, (
            "load errors that are neither optional-dependency skips nor "
            f"allow-listed known issues: {failures}"
        )


if __name__ == "__main__":
    if "--worker" in sys.argv[1:]:
        raise SystemExit(_worker_main())
    raise SystemExit(
        "internal harness worker — run via `pytest tests/` (see tests/README.md)"
    )

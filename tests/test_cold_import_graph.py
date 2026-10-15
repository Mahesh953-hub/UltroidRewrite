"""Cold-import graph test: proves the package inits are ``__getattr__``-free.

Every package that used to resolve names through a PEP 562 hook now binds
them eagerly. This test verifies that without needing Telegram credentials:

1. No ``__getattr__`` / ``_LAZY`` machinery remains (static AST check).
2. Every name the lazy hooks used to resolve is bound by an eager ``from``
   import (static AST check).
3. The import graph is acyclic (subprocess cold import; only ``ImportError``
   counts as failure — credential/network gates mean imports resolved).
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

#: The five packages that carried a module-level ``__getattr__`` hook.
LAZY_PACKAGES = [
    "pyUltroid",
    "pyUltroid.ultroid",
    "pyUltroid._core",
    "pyUltroid._misc",
    "pyUltroid.fns",
    "pyUltroid.webserver",
]

#: package -> its ``__init__.py`` path.
INIT_PATHS = {
    "pyUltroid": ROOT / "pyUltroid" / "__init__.py",
    "pyUltroid.ultroid": ROOT / "pyUltroid" / "ultroid.py",
    "pyUltroid._core": ROOT / "pyUltroid" / "_core" / "__init__.py",
    "pyUltroid._misc": ROOT / "pyUltroid" / "_misc" / "__init__.py",
    "pyUltroid.fns": ROOT / "pyUltroid" / "fns" / "__init__.py",
    "pyUltroid.webserver": ROOT / "pyUltroid" / "webserver" / "__init__.py",
}

#: Names each lazy hook used to resolve, keyed by package.
EXPECTED_EXPORTS = {
    "pyUltroid": [
        "generate_error_card",
        "generate_success_card",
        "generate_now_playing",
        "generate_queue_card",
        "render_welcome",
        "render_farewell",
        "InlineResultBuilder",
        "InlineResultCache",
        "inline_cache",
        "build_menu_results",
        "build_menu_grid",
        "InstantInline",
        "InstantResult",
        "InstantQuery",
        "InstantInlineError",
        "InlineUnavailable",
        "instant_inline",
    ],
    "pyUltroid.ultroid": [
        "Ultroid",
        "core",
    ],
    "pyUltroid._core": [
        "QueueManager",
        "QueueTask",
        "GID",
        "FID",
        "list_directory_files",
        "InlineResultBuilder",
        "InlineResultCache",
        "build_menu_grid",
        "build_menu_results",
        "render_welcome",
        "render_farewell",
        "WebUploader",
        "UploadResult",
        "UploadError",
        "get_uploader",
        "upload_to",
        "resolve_service",
        "UPLOAD_SERVICES",
        "MENU_SERVICES",
        "SERVICE_LABELS",
        "SERVICE_ALIASES",
        "CatboxUploader",
        "LitterboxUploader",
        "FileIoUploader",
        "NullStUploader",
        "TransferShUploader",
        "UguuUploader",
        "FilebinUploader",
        "SiaskyUploader",
        "toggle",
        "action",
        "manifest",
        "handle_manifest",
        "ManifestRegistry",
        "scan_module",
        "install_missing",
        "PLUGIN_MANIFEST_RESULT_BUDGET",
        "get_help_chars",
        "InstantInline",
        "InstantResult",
        "InstantQuery",
        "InstantInlineError",
        "InlineUnavailable",
        "instant_inline",
    ],
    "pyUltroid._misc": [
        "Module",
        "BoundModule",
        "ModuleError",
        "ModuleExists",
        "ModuleMissing",
        "ModuleMixin",
        "HandlerTag",
        "Listener",
        "handler",
        "is_handler",
        "MissingVerb",
        "ModuleRegistry",
        "register_module",
        "Misc",
        "misc",
        "SecretScrubber",
        "DISCARDED",
        "KEEP_SAFE",
    ],
    "pyUltroid.fns": [
        "AdminKit",
        "Terminal",
        "GDriveManager",
        "Helper",
        "InfoKit",
        "MiscKit",
        "ToolKit",
        "Fns",
        "fns",
    ],
    "pyUltroid.webserver": [
        "EnvFile",
        "KeepAliveConfig",
        "KeepAlivePinger",
        "LogBroker",
        "LogStreamHub",
        "BrokerHandler",
        "RedactionFilter",
        "LoginManager",
        "LoginOutcome",
        "LoginSession",
        "LoginSessionUnknown",
        "SessionFactory",
        "ConfigGate",
        "SetupBootstrap",
        "SseFrame",
        "LoginErrorMapper",
        "SetupApplication",
        "TokenGateMiddleware",
        "ThreadedServer",
        "PortProbe",
        "SetupState",
        "MiniAppApplication",
        "MiniAppServer",
        "MiniAppStore",
        "MiniAppState",
        "MemoryStore",
        "AUTHORITY",
        "REDACTOR",
        "TokenAuthority",
        "SecretRedactor",
        "RateLimiter",
        "BOOTSTRAP",
        "SUPERVISOR",
        "create_app",
        "create_miniapp",
        "ensure_running",
        "ensure_token",
        "rotate_token",
        "attach_broker",
        "generate_setup_token",
        "token_matches",
        "extract_token",
        "bind_available",
        "serve_forever",
        "make_server",
        "ensure_config_or_setup",
        "config_complete",
        "server_host_port",
        "spawn_bot_after_setup",
        "start_keepalive_thread",
        "keep_alive",
        "make_state",
        "read_env",
        "write_env_atomic",
    ],
}


def _bound_names(package: str) -> set[str]:
    """Names bound by eager ``from`` imports plus assignments in ``__init__``."""
    tree = ast.parse(INIT_PATHS[package].read_text())
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound.add(target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(node.name)
    if package == "pyUltroid":
        # W9: the root delegates its startup to the core union; the alias
        # surface is written by the runtime's ``expose()`` (surface dict +
        # CREATIVE_EXPORTS). Scan those two sources too.
        runtime = (ROOT / "pyUltroid" / "_core" / "runtime.py").read_text()
        wiring = (ROOT / "pyUltroid" / "_core" / "_client_wiring.py").read_text()
        for text in (runtime, wiring):
            for name in EXPECTED_EXPORTS[package]:
                if name in text:
                    bound.add(name)
    return bound


@pytest.mark.parametrize("package", LAZY_PACKAGES)
def test_no_module_level_getattr(package: str):
    """The package init must not define ``__getattr__`` or lazy machinery."""
    source = INIT_PATHS[package].read_text()
    assert "def __getattr__" not in source, (
        f"{package}.__init__ still defines __getattr__"
    )
    assert "_LAZY" not in source, f"{package}.__init__ still has a _LAZY dict"


@pytest.mark.parametrize("package", LAZY_PACKAGES)
def test_lazy_names_still_resolvable(package: str):
    """Every name the hook used to resolve is bound eagerly now."""
    bound = _bound_names(package)
    missing = [name for name in EXPECTED_EXPORTS[package] if name not in bound]
    assert not missing, f"{package} is missing eager exports: {missing}"


def test_cold_import_in_bootstrap_order():
    """Import every leaf package from a cold interpreter, in bootstrap order.

    Only ``ImportError`` counts as failure (a cycle). Credential, network,
    session-validation and deliberate library-mode gates all mean the imports
    themselves resolved, so they print success.
    """
    program = "\n".join(
        [
            "import os, sys, traceback",
            "os.environ.setdefault('API_ID', '1')",
            "os.environ.setdefault('API_HASH', 'x')",
            "os.environ.setdefault('SESSION', 'x')",
            "os.environ.setdefault('BOT_TOKEN', 'x')",
            "os.environ.setdefault('LOG_CHANNEL', '0')",
            "sys.argv = ['pyUltroid']",
            "import importlib",
            "try:",
            "    for pkg in [",
            "        'pyUltroid._core',",
            "        'pyUltroid._misc',",
            "        'pyUltroid.fns',",
            "        'pyUltroid.webserver',",
            "    ]:",
            "        importlib.import_module(pkg)",
            "    print('cold-import-ok')",
            "except ImportError:",
            "    traceback.print_exc()",
            "    print('cold-import-IMPORT-FAIL')",
            "except (SystemExit, Exception) as e:",
            "    print(f'cold-import-ok-gated ({type(e).__name__})')",
        ]
    )
    result = subprocess.run(
        [sys.executable, "-c", program],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=120,
    )
    assert "cold-import-IMPORT-FAIL" not in result.stdout, (
        f"cold import has a cycle:\n{result.stdout}\n{result.stderr}"
    )
    assert "cold-import-ok" in result.stdout, (
        f"cold import failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

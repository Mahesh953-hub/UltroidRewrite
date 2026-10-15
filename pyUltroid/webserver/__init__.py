# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""The web setup / Mini App servers, as one class layer.

Import contract (unchanged, and the reason optional deps are capability-
gated): touching ``pyUltroid.webserver`` must never drag in fastapi,
uvicorn, telethon or ``pyUltroid`` itself. Every name below is bound
eagerly; the submodules that need fastapi or uvicorn are imported inside
``try`` so the package init never fails when those deps are absent.
"""

from __future__ import annotations

from .bootstrap import (
    BOOTSTRAP,
    ConfigGate,
    SetupBootstrap,
    config_complete,
    ensure_config_or_setup,
    server_host_port,
    spawn_bot_after_setup,
)
from .envfile import EnvFile, read_env, write_env_atomic
from .keepalive import (
    KeepAliveConfig,
    KeepAlivePinger,
    keep_alive,
    start_keepalive_thread,
)
from .login import (
    LoginManager,
    LoginOutcome,
    LoginSession,
    LoginSessionUnknown,
    SessionFactory,
)
from .logs import (
    BrokerHandler,
    LogBroker,
    LogStreamHub,
    RedactionFilter,
    attach_broker,
)
from .security import (
    AUTHORITY,
    REDACTOR,
    RateLimiter,
    SecretRedactor,
    TokenAuthority,
    extract_token,
    generate_setup_token,
    token_matches,
)
from .state import SetupState, make_state

try:
    from .app import (
        LoginErrorMapper,
        SetupApplication,
        SseFrame,
        TokenGateMiddleware,
        create_app,
    )
except ImportError:
    LoginErrorMapper = SetupApplication = None
    SseFrame = TokenGateMiddleware = None
    create_app = None

try:
    from .miniapp import (
        SUPERVISOR,
        MemoryStore,
        MiniAppApplication,
        MiniAppServer,
        MiniAppState,
        MiniAppStore,
        create_miniapp,
        ensure_running,
        ensure_token,
        rotate_token,
    )
except ImportError:
    SUPERVISOR = MemoryStore = None
    MiniAppApplication = MiniAppServer = None
    MiniAppState = MiniAppStore = None
    create_miniapp = ensure_running = None
    ensure_token = rotate_token = None

try:
    from .runner import PortProbe, ThreadedServer, bind_available, make_server, serve_forever
except ImportError:
    PortProbe = ThreadedServer = None
    bind_available = make_server = serve_forever = None

__all__ = [
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
]

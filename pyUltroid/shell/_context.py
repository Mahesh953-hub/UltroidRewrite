# Ultroid - UserBot Rewrite
# pyUltroid/shell/_context.py
#
# ExecutionContext  -  the identity card every shell action carries.
# TrustLevel       -  human caller hierarchy (guest → owner)
# Capability       -  what an AI agent is explicitly permitted to do
#
# Design rule:
#   Trust controls humans.
#   Capabilities control AI.
#   Hardcoded blocks override both  -  no exception, no override path.

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum, auto
from typing import FrozenSet, Literal, Optional

# ── Trust Levels ───────────────────────────────────────────────────────────
# Assigned once per caller at invocation. Cannot be self-elevated.


class TrustLevel(IntEnum):
    GUEST = 0  # unknown user, read-only, no shell
    SUDO = 1  # trusted user, limited shell (no fs write outside workdir)
    OWNER = 2  # bot owner, full user-space shell
    ULTROID = 3  # the bot itself acting internally (update, resource install)
    AI_AGENT = 4  # AI acting via tool calls  -  trust is capped, caps required


# ── Capabilities ───────────────────────────────────────────────────────────
# Explicit grants for AI agents. Trust alone does NOT grant caps.
# Every cap must be listed here  -  no wildcard, no "all" shorthand.


class Cap(str):
    """Namespace for capability token strings. Use Cap.X, not raw strings."""

    # Shell execution
    SHELL_READ = "shell.read"  # run read-only commands (ls, cat, ps)
    SHELL_WRITE = "shell.write"  # run write commands (mkdir, cp, pip install)
    SHELL_EXEC = "shell.exec"  # run arbitrary executables
    SHELL_NET = "shell.net"  # outbound network from shell (curl, wget)

    # Filesystem
    FS_READ = "fs.read"  # read arbitrary files (not session/env)
    FS_WRITE = "fs.write"  # write to workdir
    FS_MEDIA = "fs.media"  # read/write media files (images, audio)

    # Process control
    PROC_SPAWN = "proc.spawn"  # spawn subprocesses
    PROC_KILL = "proc.kill"  # send signals to processes

    # Ultroid internals
    PLUGIN_READ = "plugin.read"  # read plugin source via code searcher
    PLUGIN_LOAD = "plugin.load"  # hot-load a plugin
    PLUGIN_UNLOAD = "plugin.unload"  # hot-unload a plugin

    # Update system
    UPDATE_READ = "update.read"  # check for updates, read changelog
    UPDATE_APPLY = "update.apply"  # apply an update (hot-reload or shadow)

    # Resource extender
    RES_INSTALL = "res.install"  # install a pip package
    RES_REMOVE = "res.remove"  # remove a pip package

    # Telegram
    TG_SEND = "tg.send"  # send Telegram messages
    TG_SEND_FILE = "tg.file_send"  # send generated text attachments
    TG_READ = "tg.read"  # read Telegram messages/history
    TG_RAW = "tg.raw"  # invoke allowlisted raw MTProto functions

    # Agent orchestration
    AGENT_SPAWN = "agent.spawn"  # delegate subtasks to a bounded sub-agent
    AGENT_ASK = "agent.ask"  # put a question to the user and wait for the answer

    # Image processing
    IMG_READ = "img.read"  # read/decode images
    IMG_WRITE = "img.write"  # write/encode images
    IMG_PROCESS = "img.process"  # run PIL/OpenCV transformations


# ── Hardcoded block list ───────────────────────────────────────────────────
# These paths/patterns are ALWAYS blocked regardless of trust or caps.
# The security module imports this  -  it is the single source of truth.

BLOCKED_PATHS: frozenset[str] = frozenset(
    {
        ".env",
        "*.session",
        "asst.session",
        "pyUltroid/configs.py",
        "pyUltroid/startup/",
        "/etc/passwd",
        "/etc/shadow",
        "~/.ssh",
        "/root/.ssh",
    }
)

BLOCKED_PATTERNS: frozenset[str] = frozenset(
    {
        r"\.session$",  # any .session file
        r"\.env$",  # .env in any directory
        r"id_rsa",  # SSH private keys
        r"authorized_keys",
        r"SECRET",  # env var names with SECRET in them
        r"TOKEN",  # any token exfiltration attempt
        r"API_KEY",
    }
)

# Commands that are blocked regardless of trust level
BLOCKED_COMMANDS: frozenset[str] = frozenset(
    {
        "rm -rf /",
        "dd if=",
        "> /dev/sda",
        "mkfs",
        ":(){ :|:& };:",  # fork bomb
        "chmod 777 /",
        "chown -R",
    }
)

# Max subprocesses an AI agent can spawn per 60 seconds
AI_PROC_RATE_LIMIT = 10


# ── ExecutionContext ───────────────────────────────────────────────────────


@dataclass
class ExecutionContext:
    """
    The identity card carried by every shell / tracer / update / resource call.

    caller:       who is initiating ("user" | "ultroid" | "ai")
    trust:        TrustLevel enum  -  determines what humans can do
    caps:         frozenset of Cap strings  -  determines what AI can do
    session_id:   shell session this belongs to (stateful cwd, env, history)
    user_id:      Telegram user ID (0 for internal Ultroid calls)
    chat_id:      Telegram chat ID context (0 for internal)
    plugin_name:  plugin that triggered this (for trace correlation)
    audit:        always True in production  -  logs to MongoDB + CloudWatch
    created_at:   unix timestamp of context creation
    request_id:   unique ID per invocation (for trace correlation)
    """

    caller: Literal["user", "ultroid", "ai"]
    trust: TrustLevel
    caps: frozenset[str] = field(default_factory=frozenset)
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: int = 0
    chat_id: int = 0
    plugin_name: str | None = None
    audit: bool = True
    created_at: float = field(default_factory=time.time)
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # ── Checks ─────────────────────────────────────────────────────────────

    def has_cap(self, cap: str) -> bool:
        """AI agents must have an explicit cap. Non-AI callers always pass."""
        if self.caller != "ai":
            return True
        return cap in self.caps

    def can_shell_read(self) -> bool:
        return self.trust >= TrustLevel.SUDO and self.has_cap(Cap.SHELL_READ)

    def can_shell_write(self) -> bool:
        return self.trust >= TrustLevel.OWNER and self.has_cap(Cap.SHELL_WRITE)

    def can_shell_exec(self) -> bool:
        return self.trust >= TrustLevel.OWNER and self.has_cap(Cap.SHELL_EXEC)

    def can_fs_read(self) -> bool:
        return self.trust >= TrustLevel.SUDO and self.has_cap(Cap.FS_READ)

    def can_fs_write(self) -> bool:
        return self.trust >= TrustLevel.OWNER and self.has_cap(Cap.FS_WRITE)

    def can_plugin_load(self) -> bool:
        return self.trust >= TrustLevel.OWNER and self.has_cap(Cap.PLUGIN_LOAD)

    def can_update(self) -> bool:
        return self.trust >= TrustLevel.ULTROID and self.has_cap(Cap.UPDATE_APPLY)

    def can_install(self) -> bool:
        return self.trust >= TrustLevel.ULTROID and self.has_cap(Cap.RES_INSTALL)

    # ── Context factories ──────────────────────────────────────────────────

    @classmethod
    def for_owner(
        cls, user_id: int, chat_id: int, plugin_name: str = None
    ) -> ExecutionContext:
        """Full owner context  -  all non-AI caps implied by trust level."""
        return cls(
            caller="user",
            trust=TrustLevel.OWNER,
            caps=frozenset(),  # caps unused for non-AI callers
            user_id=user_id,
            chat_id=chat_id,
            plugin_name=plugin_name,
        )

    @classmethod
    def for_sudo(
        cls, user_id: int, chat_id: int, plugin_name: str = None
    ) -> ExecutionContext:
        return cls(
            caller="user",
            trust=TrustLevel.SUDO,
            caps=frozenset(),
            user_id=user_id,
            chat_id=chat_id,
            plugin_name=plugin_name,
        )

    @classmethod
    def for_ultroid(cls, plugin_name: str = "internal") -> ExecutionContext:
        """Internal Ultroid calls  -  highest trust, no user context."""
        return cls(
            caller="ultroid",
            trust=TrustLevel.ULTROID,
            caps=frozenset(),  # all ops permitted at ULTROID trust
            user_id=0,
            chat_id=0,
            plugin_name=plugin_name,
        )

    @classmethod
    def for_ai(
        cls, caps: frozenset[str], chat_id: int = 0, plugin_name: str = "ai"
    ) -> ExecutionContext:
        """AI agent context  -  trust is capped at AI_AGENT, caps are explicit."""
        return cls(
            caller="ai",
            trust=TrustLevel.AI_AGENT,
            caps=caps,
            user_id=0,
            chat_id=chat_id,
            plugin_name=plugin_name,
        )

    # ── Serialisation (for audit logging) ─────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "caller": self.caller,
            "trust": self.trust.name,
            "caps": sorted(self.caps),
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "plugin_name": self.plugin_name,
            "created_at": self.created_at,
        }

    def __repr__(self) -> str:
        return (
            f"ExecutionContext("
            f"caller={self.caller!r}, "
            f"trust={self.trust.name}, "
            f"caps={len(self.caps)}, "
            f"req={self.request_id[:8]})"
        )

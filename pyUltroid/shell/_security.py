# Ultroid - UserBot Rewrite
# pyUltroid/shell/_security.py
#
# Security layer for the Hybrid Shell System.
#
# Three responsibilities:
#   1. Pre-execution check   -  blocks forbidden commands/paths before they run
#   2. Audit logging         -  every execution recorded to MongoDB + LOGS
#   3. Suspicious detection  -  flags anomalous patterns, alerts LOG_CHANNEL
#
# Order of evaluation:
#   hardcoded blocks → trust check → cap check → pattern scan → allow
#
# Nothing in this file is overridable at runtime.
# No function here accepts a "bypass" or "force" parameter.

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from pyUltroid.startup.logger import LOGS

from ._context import (
    BLOCKED_COMMANDS,
    BLOCKED_PATHS,
    BLOCKED_PATTERNS,
    ExecutionContext,
    TrustLevel,
)

# ── Audit record ───────────────────────────────────────────────────────────


@dataclass
class AuditRecord:
    request_id: str
    session_id: str
    caller: str
    trust: str
    user_id: int
    chat_id: int
    plugin_name: str | None
    command: str
    cwd: str
    allowed: bool
    block_reason: str | None
    suspicious: bool
    timestamp: float
    duration_ms: float = 0.0
    exit_code: int | None = None

    def to_dict(self) -> dict:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "caller": self.caller,
            "trust": self.trust,
            "user_id": self.user_id,
            "chat_id": self.chat_id,
            "plugin_name": self.plugin_name,
            "command": self.command,
            "cwd": self.cwd,
            "allowed": self.allowed,
            "block_reason": self.block_reason,
            "suspicious": self.suspicious,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "exit_code": self.exit_code,
        }


# ── SecurityCheck result ───────────────────────────────────────────────────


@dataclass
class SecurityResult:
    allowed: bool
    reason: str | None = None  # set when allowed=False
    suspicious: bool = False  # True even if allowed (flag for monitoring)
    audit: AuditRecord | None = None


# ── SecurityGuard ──────────────────────────────────────────────────────────


class SecurityGuard:
    """
    Stateless security checker. One instance shared across the shell system.
    Call check() before every execution. Call record() after.
    """

    # Compiled patterns  -  built once at import time
    _BLOCKED_RE = [re.compile(p) for p in BLOCKED_PATTERNS]

    # Suspicious but not blocked  -  logged and flagged, execution continues
    _SUSPICIOUS_PATTERNS = [
        re.compile(r"curl\s+.*\|\s*sh"),  # curl pipe to shell
        re.compile(r"wget\s+.*\|\s*sh"),  # wget pipe to shell
        re.compile(r"base64\s+--decode"),  # base64 decode exfil
        re.compile(r"python\d?\s+-c\s+['\"]import socket"),  # reverse shell
        re.compile(r"nc\s+-[el]"),  # netcat listener/exec
        re.compile(r"/proc/self/environ"),  # env dump via /proc
        re.compile(r"cat\s+/etc/(passwd|shadow)"),  # credential files
        re.compile(r"history\s*$"),  # shell history dump
        re.compile(r"printenv|env\s*$"),  # full env dump
    ]

    # ── Pre-execution check ────────────────────────────────────────────────

    def check(
        self,
        ctx: ExecutionContext,
        command: str,
        cwd: str = "",
    ) -> SecurityResult:
        """
        Run all security checks against a command + context.
        Returns SecurityResult immediately  -  does not execute anything.
        """
        ts = time.time()

        # 1. Hardcoded command blocks  -  absolute, no override
        for blocked in BLOCKED_COMMANDS:
            if blocked in command:
                return self._deny(
                    ctx, command, cwd, ts, f"hardcoded block matched: {blocked!r}"
                )

        # 2. Hardcoded path blocks  -  regex scan on command string
        for pattern in self._BLOCKED_RE:
            if pattern.search(command):
                return self._deny(
                    ctx,
                    command,
                    cwd,
                    ts,
                    f"blocked pattern matched: {pattern.pattern!r}",
                )

        # 3. Hardcoded path literal scan
        for path in BLOCKED_PATHS:
            if path.replace("*", "") in command:
                return self._deny(
                    ctx, command, cwd, ts, f"blocked path referenced: {path!r}"
                )

        # 4. Trust level check
        if not self._trust_allows(ctx, command):
            return self._deny(
                ctx,
                command,
                cwd,
                ts,
                f"insufficient trust: {ctx.trust.name} cannot execute this command",
            )

        # 5. Capability check (AI agents only)
        if ctx.caller == "ai":
            cap_result = self._cap_check(ctx, command)
            if cap_result:
                return self._deny(ctx, command, cwd, ts, cap_result)

        # 6. Suspicious pattern scan  -  flag but allow
        suspicious = any(p.search(command) for p in self._SUSPICIOUS_PATTERNS)

        audit = AuditRecord(
            request_id=ctx.request_id,
            session_id=ctx.session_id,
            caller=ctx.caller,
            trust=ctx.trust.name,
            user_id=ctx.user_id,
            chat_id=ctx.chat_id,
            plugin_name=ctx.plugin_name,
            command=command,
            cwd=cwd,
            allowed=True,
            block_reason=None,
            suspicious=suspicious,
            timestamp=ts,
        )

        return SecurityResult(allowed=True, suspicious=suspicious, audit=audit)

    # ── Trust gate ─────────────────────────────────────────────────────────

    def _trust_allows(self, ctx: ExecutionContext, command: str) -> bool:
        """Minimum trust required to execute any shell command is SUDO."""
        if ctx.trust < TrustLevel.SUDO:
            return False
        # Write/destructive commands require OWNER or above
        _write_indicators = (
            "rm ",
            "mv ",
            "cp ",
            "chmod ",
            "chown ",
            "pip install",
            "pip uninstall",
            "apt ",
            "systemctl ",
        )
        if any(command.lstrip().startswith(w) for w in _write_indicators):
            if ctx.trust < TrustLevel.OWNER:
                return False
        return True

    # ── Capability gate (AI only) ──────────────────────────────────────────

    def _cap_check(self, ctx: ExecutionContext, command: str) -> str | None:
        """
        Returns an error string if the AI agent lacks a required cap.
        Returns None if caps are satisfied.
        """
        from ._context import Cap

        # Any shell execution needs SHELL_EXEC
        if not ctx.has_cap(Cap.SHELL_EXEC):
            return f"AI agent missing cap: {Cap.SHELL_EXEC}"

        if self._has_output_redirection(command) and not ctx.has_cap(Cap.SHELL_WRITE):
            return f"AI agent missing cap: {Cap.SHELL_WRITE} for output redirection"

        # Network commands need SHELL_NET
        _net_cmds = ("curl", "wget", "nc", "nmap", "ssh", "scp", "rsync")
        if any(command.lstrip().startswith(c) for c in _net_cmds):
            if not ctx.has_cap(Cap.SHELL_NET):
                return f"AI agent missing cap: {Cap.SHELL_NET}"

        # Write operations need SHELL_WRITE
        _write_cmds = (
            "rm",
            "mv",
            "cp",
            "mkdir",
            "touch",
            "tee",
            "dd",
            "pip install",
            "pip uninstall",
            "apt",
        )
        if any(command.lstrip().startswith(c) for c in _write_cmds):
            if not ctx.has_cap(Cap.SHELL_WRITE):
                return f"AI agent missing cap: {Cap.SHELL_WRITE}"

        return None

    @staticmethod
    def _has_output_redirection(command: str) -> bool:
        quote = None
        escaped = False
        for char in command:
            if escaped:
                escaped = False
                continue
            if char == "\\" and quote != "'":
                escaped = True
                continue
            if quote:
                if char == quote:
                    quote = None
                continue
            if char in ("'", '"'):
                quote = char
            elif char == ">":
                return True
        return quote is not None or escaped

    # ── Deny builder ───────────────────────────────────────────────────────

    def _deny(
        self,
        ctx: ExecutionContext,
        command: str,
        cwd: str,
        ts: float,
        reason: str,
    ) -> SecurityResult:
        audit = AuditRecord(
            request_id=ctx.request_id,
            session_id=ctx.session_id,
            caller=ctx.caller,
            trust=ctx.trust.name,
            user_id=ctx.user_id,
            chat_id=ctx.chat_id,
            plugin_name=ctx.plugin_name,
            command=command,
            cwd=cwd,
            allowed=False,
            block_reason=reason,
            suspicious=True,
            timestamp=ts,
        )
        LOGS.warning(
            f"[HSS:SECURITY] DENIED  -  {reason} | cmd={command[:80]!r} | ctx={ctx}"
        )
        return SecurityResult(
            allowed=False, reason=reason, suspicious=True, audit=audit
        )

    # ── Post-execution audit persistence ──────────────────────────────────

    async def record(
        self,
        audit: AuditRecord,
        exit_code: int,
        duration_ms: float,
    ) -> None:
        """
        Persist audit record after execution completes.
        Alerts LOG_CHANNEL for suspicious executions.
        Non-blocking  -  failures are logged but never propagate.
        """
        audit.exit_code = exit_code
        audit.duration_ms = duration_ms

        # Persist to MongoDB
        await self._save_to_db(audit)

        # Alert on suspicious
        if audit.suspicious:
            await self._alert(audit)

    async def _save_to_db(self, audit: AuditRecord) -> None:
        try:
            from pyUltroid import udB

            # Store in MongoDB collection "hss_audit"
            if hasattr(udB, "_mongo") and udB._mongo is not None:
                udB._mongo["hss_audit"].insert_one(audit.to_dict())
            else:
                # Redis fallback  -  store last 1000 audit records as list
                import json

                r = self._get_redis()
                if r:
                    r.lpush("hss:audit", json.dumps(audit.to_dict()))
                    r.ltrim("hss:audit", 0, 999)
        except Exception as e:
            LOGS.error(f"[HSS:SECURITY] Audit save failed: {e}")

    async def _alert(self, audit: AuditRecord) -> None:
        try:
            from pyUltroid import asst, udB

            log_ch = udB.get_key("LOG_CHANNEL")
            if not log_ch or not asst:
                return

            flag = "🚨 BLOCKED" if not audit.allowed else "⚠️ SUSPICIOUS"
            text = (
                f"**{flag}  -  Shell Audit**\n\n"
                f"**Caller:** `{audit.caller}` (trust: `{audit.trust}`)\n"
                f"**User ID:** `{audit.user_id}`\n"
                f"**Plugin:** `{audit.plugin_name}`\n"
                f"**Command:** `{audit.command[:200]}`\n"
                f"**CWD:** `{audit.cwd}`\n"
                f"**Reason:** `{audit.block_reason or 'flagged pattern'}`\n"
                f"**Request:** `{audit.request_id[:16]}`"
            )
            await asst.send_message(log_ch, text)
        except Exception as e:
            LOGS.error(f"[HSS:SECURITY] Alert send failed: {e}")

    def _get_redis(self):
        try:
            import redis as _redis

            from pyUltroid.configs import Var

            uri = getattr(Var, "REDIS_URI", None)
            if uri:
                return _redis.from_url(uri, decode_responses=True)
        except Exception:
            pass
        return None


# ── Module singleton ───────────────────────────────────────────────────────

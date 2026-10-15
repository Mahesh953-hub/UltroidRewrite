# Ultroid - UserBot Rewrite
# pyUltroid/shell/_ipc.py
#
# Inter-Process Communication for the Hybrid Shell System.
#
# Used by HSU (Hybrid Update System) to communicate between:
#   Main process   -  running Ultroid, serving users
#   Shadow process  -  handling git pull, pip installs, plugin hot-reload
#
# Transport: Unix domain socket (same EC2 instance, fastest IPC)
# Protocol:  newline-delimited JSON messages
# Framing:   4-byte big-endian length prefix + JSON payload
#
# Message types (main → shadow):
#   {"type": "ping"}
#   {"type": "update_check"}
#   {"type": "update_apply", "targets": ["plugins/foo.py"]}
#   {"type": "install_pkg",  "packages": ["yt-dlp>=2024.1"]}
#   {"type": "shutdown"}
#
# Message types (shadow → main):
#   {"type": "pong"}
#   {"type": "update_available", "commits": [...], "changed_files": [...]}
#   {"type": "update_done",      "reloaded": [...], "needs_restart": bool}
#   {"type": "install_done",     "installed": [...], "failed": [...]}
#   {"type": "error",            "message": "..."}

from __future__ import annotations

import asyncio
import json
import os
import struct
import time
from typing import Any, Dict, Optional
from collections.abc import Callable

from pyUltroid.startup.logger import LOGS

# ── Constants ──────────────────────────────────────────────────────────────

SOCKET_PATH = "/tmp/ultroid_hss.sock"
HEADER_SIZE = 4  # bytes  -  big-endian uint32 message length
MAX_MSG_SIZE = 1 << 20  # 1 MB  -  reject larger messages
CONNECT_RETRY = 3  # connection attempts before giving up
RETRY_DELAY = 1.0  # seconds between retries
RECV_TIMEOUT = 30.0  # seconds to wait for a reply


# ── Framing helpers ────────────────────────────────────────────────────────


def _encode(payload: dict[str, Any]) -> bytes:
    """Encode a dict to length-prefixed JSON bytes."""
    body = json.dumps(payload).encode("utf-8")
    header = struct.pack(">I", len(body))
    return header + body


async def _read_message(reader: asyncio.StreamReader) -> dict[str, Any] | None:
    """
    Read one length-prefixed message from the stream.
    Returns None on EOF or oversized message.
    """
    try:
        header = await reader.readexactly(HEADER_SIZE)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        return None

    length = struct.unpack(">I", header)[0]

    if length > MAX_MSG_SIZE:
        LOGS.error(f"[HSS:IPC] Oversized message: {length} bytes  -  dropping.")
        return None

    try:
        body = await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        return None

    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as e:
        LOGS.error(f"[HSS:IPC] JSON decode error: {e}")
        return None


# ── IPCServer (runs in main process) ──────────────────────────────────────


class IPCServer:
    """
    Listens on the Unix socket for shadow process messages.
    Main process runs this. Calls registered handlers per message type.

    Usage:
        server = IPCServer()
        server.register("update_done", my_handler)
        await server.start()
        ...
        await server.stop()
    """

    def __init__(self) -> None:
        self._handlers: dict[str, Callable] = {}
        self._server: asyncio.AbstractServer | None = None
        self._running = False

    def register(self, msg_type: str, handler: Callable) -> None:
        """Register an async handler for a message type."""
        self._handlers[msg_type] = handler

    async def start(self) -> None:
        """Start the Unix socket server."""
        # Clean up stale socket file
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=SOCKET_PATH,
        )
        self._running = True
        LOGS.info(f"[HSS:IPC] Server listening on {SOCKET_PATH}")

    async def stop(self) -> None:
        """Gracefully shut down the server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
        if os.path.exists(SOCKET_PATH):
            os.unlink(SOCKET_PATH)
        LOGS.info("[HSS:IPC] Server stopped.")

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle one connection from the shadow process."""
        peer = writer.get_extra_info("peername", "unknown")
        LOGS.debug(f"[HSS:IPC] Connection from {peer}")

        try:
            while self._running:
                msg = await _read_message(reader)
                if msg is None:
                    break

                msg_type = msg.get("type", "unknown")
                LOGS.debug(f"[HSS:IPC] Received: {msg_type}")

                # Always ack ping
                if msg_type == "ping":
                    await self._send(writer, {"type": "pong", "ts": time.time()})
                    continue

                # Dispatch to registered handler
                handler = self._handlers.get(msg_type)
                if handler:
                    try:
                        response = await handler(msg)
                        if response:
                            await self._send(writer, response)
                    except Exception as e:
                        LOGS.error(f"[HSS:IPC] Handler error for {msg_type}: {e}")
                        await self._send(
                            writer,
                            {
                                "type": "error",
                                "message": str(e),
                            },
                        )
                else:
                    LOGS.warning(f"[HSS:IPC] No handler for message type: {msg_type!r}")

        except Exception as e:
            LOGS.error(f"[HSS:IPC] Connection error: {e}")
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, payload: dict[str, Any]) -> None:
        try:
            writer.write(_encode(payload))
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError) as e:
            # Benign ack race  -  the shadow already read the reply (or exited)
            # and closed its end. Not an update failure.
            LOGS.debug(f"[HSS:IPC] Peer closed before reply: {e}")
        except Exception as e:
            LOGS.error(f"[HSS:IPC] Send error: {e}")


# ── IPCClient (runs in shadow process) ────────────────────────────────────


class IPCClient:
    """
    Connects to the main process IPC server.
    Shadow process uses this to report back.

    Usage:
        client = IPCClient()
        await client.connect()
        response = await client.send({"type": "update_done", "reloaded": [...]})
        await client.disconnect()
    """

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False

    async def connect(self) -> bool:
        """Connect to the main process IPC server. Returns True on success."""
        for attempt in range(1, CONNECT_RETRY + 1):
            try:
                self._reader, self._writer = await asyncio.open_unix_connection(
                    path=SOCKET_PATH,
                )
                self._connected = True
                LOGS.info(f"[HSS:IPC] Shadow connected to main process.")
                return True
            except (FileNotFoundError, ConnectionRefusedError) as e:
                LOGS.warning(
                    f"[HSS:IPC] Connect attempt {attempt}/{CONNECT_RETRY} failed: {e}"
                )
                if attempt < CONNECT_RETRY:
                    await asyncio.sleep(RETRY_DELAY)

        LOGS.error("[HSS:IPC] Could not connect to main process IPC server.")
        return False

    async def send(
        self,
        payload: dict[str, Any],
        wait_reply: bool = True,
    ) -> dict[str, Any] | None:
        """
        Send a message and optionally wait for a reply.
        Returns the reply dict, or None on error/timeout.
        """
        if not self._connected or not self._writer:
            LOGS.error("[HSS:IPC] Not connected  -  cannot send.")
            return None

        try:
            self._writer.write(_encode(payload))
            await self._writer.drain()
        except Exception as e:
            LOGS.error(f"[HSS:IPC] Send failed: {e}")
            return None

        if not wait_reply:
            return None

        try:
            return await asyncio.wait_for(
                _read_message(self._reader),
                timeout=RECV_TIMEOUT,
            )
        except TimeoutError:
            LOGS.warning("[HSS:IPC] Timeout waiting for reply from main process.")
            return None

    async def ping(self) -> bool:
        """Verify the connection is alive. Returns True if main process replies."""
        reply = await self.send({"type": "ping"})
        return reply is not None and reply.get("type") == "pong"

    async def disconnect(self) -> None:
        """Close the connection."""
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        LOGS.info("[HSS:IPC] Shadow disconnected.")


# ── Convenience: spawn shadow process ────────────────────────────────────


async def _forward_stream(stream, prefix: str) -> None:
    """Continuously drain *stream* into the logger.

    Without this, a chatty shadow (git/pip output) can fill the 64 KB pipe
    buffer and deadlock the child mid-update  -  and its logs were invisible
    to the main process, making crashes undiagnosable.

    Reads fixed-size chunks rather than ``readline()`` so a single line
    longer than the stream limit can never abort the drain.
    """
    if stream is None:
        return
    buf = b""
    try:
        while True:
            chunk = await stream.read(8192)
            if not chunk:
                break
            buf += chunk
            *lines, buf = buf.split(b"\n")
            for line in lines:
                text = line.decode("utf-8", "replace").rstrip()
                if text:
                    LOGS.info(f"{prefix}{text}")
        if buf.strip():
            LOGS.info(f"{prefix}{buf.decode('utf-8', 'replace').rstrip()}")
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


async def spawn_shadow(script: str, *args: str) -> asyncio.subprocess.Process:
    """
    Spawn a shadow process running *script* with *args*.
    The shadow process is detached  -  it outlives its parent call.
    It communicates back via IPCClient.

    script: path to a Python script (e.g. "pyUltroid/shell/_shadow_update.py")
    """
    import sys

    cmd = [sys.executable, script, *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        # No stdin  -  shadow processes don't take input
    )
    # Drain the child's output so the pipe never fills (which would block
    # the shadow forever) and its logs surface in the main process log.
    asyncio.ensure_future(_forward_stream(proc.stdout, "[HSS:SHADOW] "))
    LOGS.info(f"[HSS:IPC] Shadow process spawned: pid={proc.pid} cmd={script}")
    return proc


# ── Module singletons ──────────────────────────────────────────────────────
# Main process imports ipc_server and calls ipc_server.start() at boot.
# Shadow processes import ipc_client and connect after spawn.

ipc_server = IPCServer()
ipc_client = IPCClient()

"""AgentTools  -  the tool schema and executor that gives the AI, its hands, eyes, memory, and voice."""

from __future__ import annotations

import json
import re
import time
from io import BytesIO
from typing import Any, Dict, FrozenSet, List, Optional

from pyUltroid.startup.logger import LOGS
from pyUltroid.shell._context import Cap

from ._interactive import INTERACTIVE_SCHEMAS, InteractiveTools

_SHELL_LAYER_CAPS: frozenset[str] = frozenset(
    {
        Cap.SHELL_EXEC,
        Cap.SHELL_READ,
        Cap.SHELL_WRITE,
        Cap.SHELL_NET,
        Cap.FS_READ,
        Cap.FS_WRITE,
        Cap.FS_MEDIA,
        Cap.PROC_SPAWN,
        Cap.PROC_KILL,
    }
)

# Minimum caps required to invoke each tool at all.
TOOL_REQUIRED_CAPS: dict[str, frozenset[str]] = {
    "shell_execute": frozenset({Cap.SHELL_EXEC}),
    "code_search": frozenset({Cap.PLUGIN_READ}),
    "get_traces": frozenset(),  # self-telemetry read  -  no cap token exists
    "update_check": frozenset({Cap.UPDATE_READ}),
    "install_package": frozenset({Cap.RES_INSTALL}),
    "process_image": frozenset({Cap.IMG_PROCESS}),
    "send_message": frozenset({Cap.TG_SEND}),
    "send_text_file": frozenset({Cap.TG_SEND_FILE}),
    "get_bot_status": frozenset(),  # self-status read
    "remember": frozenset(),  # own memory namespace only (agent:memory:*)
    "web_search": frozenset(),  # outbound read-only HTTP GET
    "tg_route_send": frozenset({Cap.TG_SEND}),
    "tg_read": frozenset({Cap.TG_READ}),
    # listing plugins is read-only; load/unload/reload escalate in
    # _missing_caps below.
    "plugin_manager": frozenset({Cap.PLUGIN_READ}),
    "raw_mtproto": frozenset({Cap.TG_RAW}),
    "web_fetch": frozenset(),  # outbound read-only HTTP GET
    "think": frozenset(),  # scratchpad (no side effects)
    "delegate": frozenset({Cap.AGENT_SPAWN}),
    # asking a human interrupts their day  -  it is a grant, not a default.
    "ask_user": frozenset({Cap.AGENT_ASK}),
    "confirm_action": frozenset({Cap.AGENT_ASK}),
    "collect_fields": frozenset({Cap.AGENT_ASK}),
}


# requires an explicit grant via make_executor(caps=...).
DEFAULT_AGENT_CAPS: frozenset[str] = frozenset(
    {
        Cap.SHELL_EXEC,
        Cap.SHELL_READ,
        Cap.SHELL_WRITE,
        Cap.SHELL_NET,
        Cap.FS_READ,
        Cap.FS_WRITE,
        Cap.PLUGIN_READ,
        Cap.UPDATE_READ,
        Cap.TG_SEND,
        Cap.TG_SEND_FILE,
        Cap.TG_READ,
        Cap.AGENT_SPAWN,
        Cap.IMG_PROCESS,
        Cap.AGENT_ASK,
    }
)

# Deepest nested delegation the runtime permits. A sub-agent created by a
# sub-agent of this depth is refused, so delegation can never recurse.
MAX_AGENT_DEPTH = 2

# never let it exceed the cap regardless of prompt content.
AGENT_SHELL_MIN_TIMEOUT = 1.0
AGENT_SHELL_MAX_TIMEOUT = 120.0


def clamp_shell_timeout(timeout) -> float:
    """Coerce an LLM-supplied timeout into [MIN, MAX]; junk becomes 60s."""
    try:
        value = float(timeout)
    except (TypeError, ValueError):
        return 60.0
    return max(AGENT_SHELL_MIN_TIMEOUT, min(value, AGENT_SHELL_MAX_TIMEOUT))


TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "shell_execute",
            "description": (
                "Execute a shell command on the Ultroid EC2 instance. "
                "Use for file operations, system inspection, git commands, "
                "running scripts, and one-off diagnostics. stdout/stderr are "
                "returned as text (truncated); the exit code tells you whether "
                "the command succeeded. Never use this to read .env or "
                "*.session files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": (
                            "The exact shell command to run, as a single "
                            "string. Quote arguments that contain spaces."
                        ),
                    },
                    "timeout": {
                        "type": "number",
                        "description": (
                            "Maximum seconds to wait before killing the "
                            "command. Clamped to the range [1, 120]; junk "
                            "values fall back to 60. Default 60."
                        ),
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_search",
            "description": (
                "Search the live Ultroid plugin codebase. Map a command "
                "pattern to the plugin that handles it, extract a function's "
                "source, find every DB key a plugin reads/writes, or detect "
                "handler conflicts  -  all against the on-disk source, not "
                "training data."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Natural-language query or command pattern, e.g. "
                            "'who handles .play', 'show source of play_cmd', "
                            "'find all DB keys used by youtube'."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_traces",
            "description": (
                "Read recent execution-trace history. See which handlers ran, "
                "who triggered them, and any failures or slow handlers. "
                "Useful for diagnosing 'why didn't X work' questions before "
                "guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": (
                            "Optional scope: 'recent' (default), 'failures', "
                            "'slow', or a prefix target  -  'plugin:<name>' or "
                            "'user:<id>'."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "Number of traces to return, clamped to [1, 50]. "
                            "Default 10."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_check",
            "description": (
                "Check whether Ultroid has upstream updates available. "
                "Returns commit count, changed files, a restart flag, and a "
                "short changelog. Applying the update requires an elevated "
                "capability grant and is denied by default."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "apply": {
                        "type": "boolean",
                        "description": (
                            "If true, apply the update after checking. "
                            "Refused unless UPDATE_APPLY was explicitly "
                            "granted to the executor."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "install_package",
            "description": (
                "Install a Python package via pip for on-demand plugin "
                "dependencies. Refused unless RES_INSTALL was explicitly "
                "granted  -  on-demand installs are supply-chain sensitive."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "package": {
                        "type": "string",
                        "description": (
                            "Package name with an optional version specifier, "
                            "e.g. 'pillow', 'requests>=2.31', "
                            "'yt-dlp==2024.4.9'."
                        ),
                    },
                },
                "required": ["package"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_image",
            "description": (
                "Process or generate an image in a single operation. "
                "Transform operations need an input image_url; generation "
                "operations (error_card, code_image, text_image) build from "
                "their params. The produced image is returned for delivery."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "grayscale",
                            "blur",
                            "sketch",
                            "cartoon",
                            "rotate",
                            "caption",
                            "meme",
                            "glitch",
                            "vintage",
                            "invert",
                            "pixelate",
                            "error_card",
                            "code_image",
                            "text_image",
                            "collage",
                        ],
                        "description": "The image operation to perform.",
                    },
                    "params": {
                        "type": "object",
                        "description": (
                            "Operation-specific parameters: text/author "
                            "(caption, meme, text_image), radius (blur), "
                            "degrees (rotate), intensity (glitch), size "
                            "(pixelate), position (caption), code+language "
                            "(code_image), title+message+traceback "
                            "(error_card)."
                        ),
                    },
                    "image_url": {
                        "type": "string",
                        "description": (
                            "URL of the input image, required for transform "
                            "operations. Omit for pure generation operations."
                        ),
                    },
                },
                "required": ["operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": (
                "Send an ordinary plain-text Telegram message as the userbot. "
                "This tool never sends a native RichMessage. Use tg_route_send "
                "with rich=true only when native formatting is needed and the "
                "selected account is a bot or confirmed Premium user."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chat_id": {
                        "type": "integer",
                        "description": ("Telegram chat ID to send the message to."),
                    },
                    "text": {
                        "type": "string",
                        "description": "Plain-text message body.",
                    },
                },
                "required": ["chat_id", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_text_file",
            "description": (
                "Create a UTF-8 .txt or .md attachment from the supplied text "
                "and send it only to the current chat. This uses in-memory "
                "content; filesystem paths and other destinations are not accepted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": (
                            "A simple filename ending in .txt or .md; no path."
                        ),
                    },
                    "content": {
                        "type": "string",
                        "description": "The UTF-8 text to put in the attachment.",
                    },
                    "caption": {
                        "type": "string",
                        "description": (
                            "Optional plain-text caption, up to 900 characters."
                        ),
                    },
                },
                "required": ["filename", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_bot_status",
            "description": (
                "Get Ultroid's own running state: current version, git "
                "branch, loaded plugin/handler counts, and an uptime hint. "
                "No parameters."
            ),
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": (
                "Store, recall, or forget a fact that persists across "
                "conversations. The agent's own memory namespace "
                "(agent:memory:*); use to retain user preferences, project "
                "context, or cross-session facts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["store", "recall", "forget"],
                        "description": "What to do with the memory entry.",
                    },
                    "key": {
                        "type": "string",
                        "description": "Memory key name to store/recall/forget.",
                    },
                    "value": {
                        "type": "string",
                        "description": "Value to store (only for action=store).",
                    },
                },
                "required": ["action", "key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information via DuckDuckGo. "
                "Returns titled snippets with URLs. Use before answering "
                "time-sensitive or factual questions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": (
                            "Maximum results to return, clamped to [1, 10]. "
                            "Default 5."
                        ),
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tg_route_send",
            "description": (
                "Send a Telegram message through the master client router. "
                "The route chooses which account sends: 'auto' follows the "
                "ambient routing rules (business connection, then user, then "
                "bot), or pin 'user', 'bot', or 'business'. Sends ordinary plain "
                "text by default. Set rich=true to request a native RichMessage; "
                "bots may send these and user accounts require confirmed Telegram "
                "Premium. Unsupported rich sends are rejected without retry. Set "
                "rich=false whenever plain text is preferred. For plain sends, "
                "parse_mode only converts markup to readable text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chat": {
                        "type": "string",
                        "description": (
                            "Destination chat: numeric id, @username, or "
                            "t.me link."
                        ),
                    },
                    "text": {
                        "type": "string",
                        "description": "Message text to send.",
                    },
                    "route": {
                        "type": "string",
                        "enum": ["auto", "user", "bot", "business"],
                        "description": "Which account sends. Default 'auto'.",
                    },
                    "reply_to": {
                        "type": "integer",
                        "description": "Message id to reply to, optional.",
                    },
                    "parse_mode": {
                        "type": "string",
                        "description": (
                            "RichMessage format: 'html' or 'markdown' ('md' is "
                            "accepted). Omit to detect the format from the text."
                        ),
                    },
                    "rich": {
                        "type": "boolean",
                        "description": (
                            "Use native RichMessage formatting. Default false "
                            "sends ordinary plain text and is always allowed."
                        ),
                    },
                    "link_preview": {
                        "type": "boolean",
                        "description": "Allow link preview. Default true.",
                    },
                },
                "required": ["chat", "text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "tg_read",
            "description": (
                "Read the most recent messages of a Telegram chat. Returns "
                "id, sender, date, text, and reply metadata per message. "
                "Use to ground answers in live conversation state instead of "
                "guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chat": {
                        "type": "string",
                        "description": (
                            "Chat to read: numeric id, @username, or t.me "
                            "link."
                        ),
                    },
                    "limit": {
                        "type": "integer",
                        "description": (
                            "How many messages, clamped to [1, 100]. "
                            "Default 20."
                        ),
                    },
                    "route": {
                        "type": "string",
                        "enum": ["user", "bot", "business"],
                        "description": (
                            "Which account reads. Defaults to the user "
                            "account."
                        ),
                    },
                },
                "required": ["chat"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "plugin_manager",
            "description": (
                "Inspect and drive the live plugin registry. 'list' returns "
                "active plugin names with handler counts. 'load' loads a "
                "plugin file by name, 'unload' removes it from the running "
                "bot, 'reload' does both. Load/unload/reload are privileged "
                "and refused unless the matching capability was granted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "load", "unload", "reload"],
                        "description": "Registry operation. Default 'list'.",
                    },
                    "plugin": {
                        "type": "string",
                        "description": (
                            "Plugin name for load/unload/reload (ignored for "
                            "list)."
                        ),
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "raw_mtproto",
            "description": (
                "Invoke a read-only raw MTProto request through the active "
                "client. Only allowlisted request types are permitted (for "
                "example messages.GetHistoryRequest, "
                "channels.GetFullChannelRequest, users.GetUsersRequest); "
                "everything else, especially any write request, is refused. "
                "Arguments must match the Telethon constructor signature."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "function": {
                        "type": "string",
                        "description": (
                            "Request class name, e.g. "
                            "'messages.GetHistoryRequest'."
                        ),
                    },
                    "arguments": {
                        "type": "object",
                        "description": (
                            "Keyword arguments for the request constructor."
                        ),
                    },
                },
                "required": ["function"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": (
                "Fetch a web page and return readable text (HTML stripped, "
                "truncated). Use after web_search to read a specific result. "
                "http/https only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Absolute http(s) URL to fetch.",
                    },
                    "max_chars": {
                        "type": "integer",
                        "description": (
                            "Maximum characters of text returned, clamped to "
                            "[200, 20000]. Default 4000."
                        ),
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "think",
            "description": (
                "Private scratchpad. Record a short plan, an interim "
                "conclusion, or the next step between tool rounds. No side "
                "effects; the content is echoed back as a tool result so it "
                "stays in the transcript."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "thought": {
                        "type": "string",
                        "description": "The note to record.",
                    },
                },
                "required": ["thought"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delegate",
            "description": (
                "Delegate a bounded subtask to a fresh sub-agent with its "
                "own context window. The sub-agent gets the same tool "
                "permissions minus delegation itself, a small round budget, "
                "and returns only its final answer. Use for parallelizable "
                "research or multi-step work that would bloat this "
                "conversation. Refused at the maximum nesting depth."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": (
                            "The complete, self-contained subtask for the "
                            "sub-agent."
                        ),
                    },
                    "max_rounds": {
                        "type": "integer",
                        "description": (
                            "Tool-round budget for the sub-agent, clamped to "
                            "[1, 5]. Default 3."
                        ),
                    },
                },
                "required": ["task"],
            },
        },
    },
]

#: The interactive tools are part of the agent's surface, not a separate
#: surface: a model that cannot ask is a model that guesses.
TOOL_SCHEMAS.extend(INTERACTIVE_SCHEMAS)


def codex_tool_kwargs(
    tools: list | None = None,
    tool_choice: Any = "auto",
    parallel_tool_calls: bool | None = None,
) -> dict:
    """Build OpenAI/Codex-compatible tool-calling kwargs.

    Returns ``{}`` when *tools* is empty so callers can spread the result
    unconditionally. ``tool_choice`` accepts ``"auto"``/``"none"``/
    ``"required"`` or a specific function-selector dict; pass ``None`` to omit
    it. ``parallel_tool_calls`` is omitted when ``None`` (the API default
    applies)."""
    if not tools:
        return {}
    kwargs: dict[str, Any] = {"tools": tools}
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    if parallel_tool_calls is not None:
        kwargs["parallel_tool_calls"] = parallel_tool_calls
    return kwargs


# ── ToolExecutor
class ToolExecutor:
    """
    Executes tool calls from AI responses.
    Each method returns a JSON-serialisable result dict.

    ``caps`` is the executor's explicit capability grant (default
    DEFAULT_AGENT_CAPS). execute() checks TOOL_REQUIRED_CAPS before any
    dispatch  -  a tool can never run above the executor's granted caps.
    """

    def __init__(
        self,
        chat_id: int = 0,
        caps: frozenset[str] | None = None,
        depth: int = 0,
        interactive: "InteractiveTools | None" = None,
    ):
        self.chat_id = chat_id
        # The interactive tools (ask/confirm/collect). Created on first use so
        # importing the module does not build a bridge for nobody.
        self._interactive = interactive
        # Delegation depth of the agent that owns this executor. The
        # delegate tool refuses to spawn children once MAX_AGENT_DEPTH is
        # reached, so sub-agents cannot recurse without bound.
        self.depth: int = max(0, int(depth))
        self.caps: frozenset[str] = (
            frozenset(caps) if caps is not None else DEFAULT_AGENT_CAPS
        )

    def _missing_caps(self, tool_name: str, arguments: dict) -> list[str]:
        """Cap tokens *tool_name* needs but this executor was not granted."""
        required = TOOL_REQUIRED_CAPS.get(tool_name)
        if required is None:
            return []  # unknown tool  -  handled by the dispatch miss below
        required = set(required)
        # Arg-level escalation: applying an update is privileged; checking is not.
        if tool_name == "update_check" and arguments.get("apply"):
            required.add(Cap.UPDATE_APPLY)
        # plugin_manager: listing needs only PLUGIN_READ, but mutating the
        # loaded plugin set is its own grant (never in DEFAULT_AGENT_CAPS).
        if tool_name == "plugin_manager":
            action = str(arguments.get("action", "list")).lower()
            if action == "load":
                required.add(Cap.PLUGIN_LOAD)
            elif action in ("unload", "reload"):
                required.add(Cap.PLUGIN_UNLOAD)
        return sorted(c for c in required if c not in self.caps)

    async def execute(self, tool_name: str, arguments: dict) -> dict:
        """Dispatch a tool call. Returns result dict."""
        t0 = time.time()
        LOGS.info(f"[AGENT:TOOLS] Executing: {tool_name}({list(arguments.keys())})")

        missing = self._missing_caps(tool_name, arguments)
        if missing:
            LOGS.warning(f"[AGENT:TOOLS] DENIED {tool_name}  -  missing caps: {missing}")
            return {
                "error": (
                    f"Tool '{tool_name}' blocked by capability policy "
                    f"(missing: {', '.join(missing)})"
                ),
                "denied": True,
                "missing_caps": missing,
            }

        try:
            method = getattr(self, f"_tool_{tool_name}", None)
            if method is None:
                return {"error": f"Unknown tool: {tool_name}"}
            result = await method(**arguments)
            result["_duration_ms"] = round((time.time() - t0) * 1000, 1)
            return result
        except Exception as e:
            LOGS.error(f"[AGENT:TOOLS] {tool_name} error: {e}")
            return {"error": str(e)}

    # ── shell_execute
    async def _tool_shell_execute(self, command: str, timeout: float = 60.0) -> dict:
        from pyUltroid.shell import sh, ExecutionContext

        ctx = ExecutionContext.for_ai(
            caps=frozenset(c for c in self.caps if c in _SHELL_LAYER_CAPS),
            chat_id=self.chat_id,
            plugin_name="agent.agent",
        )
        result = await sh.execute(
            ctx,
            command,
            timeout=clamp_shell_timeout(timeout),
        )
        return {
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:500],
            "exit_code": result.exit_code,
            "cwd": result.cwd,
            "ok": result.ok,
        }

    # ── code_search
    async def _tool_code_search(self, query: str) -> dict:
        from pyUltroid.tracer import tr

        result = tr.search.nl_query(query)
        items = []
        for r in result.results[:5]:
            if hasattr(r, "plugin"):
                items.append(
                    {
                        "plugin": r.plugin,
                        "name": getattr(r, "func_name", getattr(r, "name", "")),
                        "pattern": getattr(r, "pattern", ""),
                        "line": getattr(r, "line", 0),
                    }
                )
            elif isinstance(r, str):
                items.append({"source": r[:1000]})
        return {"kind": result.kind, "count": result.count, "results": items}

    # ── get_traces
    async def _tool_get_traces(
        self,
        filter: str = "recent",
        limit: int = 10,
    ) -> dict:
        from pyUltroid.tracer import tr

        limit = max(1, min(limit, 50))
        f = filter.lower()
        if f == "failures":
            traces = await tr.live.failures(n=limit)
        elif f == "slow":
            traces = await tr.live.slow(n=limit)
        elif f.startswith("plugin:"):
            traces = await tr.live.by_plugin(f[7:], n=limit)
        elif f.startswith("user:"):
            traces = await tr.live.by_user(int(f[5:]), n=limit)
        else:
            traces = await tr.live.recent(n=limit)

        return {
            "traces": [
                {
                    "handler": f"{t.plugin}.{t.handler}",
                    "success": t.success,
                    "duration_ms": round(t.duration_ms, 1),
                    "user_id": t.user_id,
                    "exc_type": t.exc_type,
                }
                for t in traces
            ]
        }

    # ── update_check
    async def _tool_update_check(self, apply: bool = False) -> dict:
        from pyUltroid.updater import up

        diff = await up.check()
        if diff is None:
            return {"error": "Could not fetch upstream"}
        result = {
            "has_changes": diff.has_changes,
            "commit_count": diff.commit_count,
            "needs_restart": diff.needs_restart,
            "impact": diff.impact_summary(),
            "changelog": diff.changelog_text(5),
        }
        if apply and diff.has_changes:
            state = await up.apply(diff)
            result["applied"] = state.summary()
        return result

    # ── install_package
    async def _tool_install_package(self, package: str) -> dict:
        from pyUltroid.extender import ex

        result = await ex.resources.install_dep(package, plugin="agent.agent")
        return {
            "installed": result.installed,
            "skipped": result.skipped,
            "failed": result.failed,
            "success": result.success,
        }

    # ── process_image
    async def _tool_process_image(
        self,
        operation: str,
        params: dict = None,
        image_url: str | None = None,
    ) -> dict:
        from pyUltroid.imaging import Imaging

        imaging = Imaging()
        params = params or {}

        # Load input image if URL provided
        base = None
        if image_url:
            base = await imaging.load_url(image_url)
            if not base.ok:
                return {"error": f"Could not load image: {base.error}"}

        op_map = {
            "grayscale": lambda: imaging.grayscale(base),
            "blur": lambda: imaging.blur(base, params.get("radius", 5)),
            "sketch": lambda: imaging.sketch(base),
            "cartoon": lambda: imaging.cartoon(base),
            "rotate": lambda: imaging.rotate(base, params.get("degrees", 90)),
            "caption": lambda: imaging.caption(
                base, params.get("text", ""), params.get("position", "bottom")
            ),
            "meme": lambda: imaging.meme(
                base, params.get("top", ""), params.get("bottom", "")
            ),
            "glitch": lambda: imaging.glitch(base, params.get("intensity", 10)),
            "vintage": lambda: imaging.vintage(base),
            "invert": lambda: imaging.invert(base),
            "pixelate": lambda: imaging.pixelate(base, params.get("size", 20)),
            "error_card": lambda: imaging.error_card(
                params.get("title", "Error"),
                params.get("message", ""),
                params.get("traceback"),
            ),
            "code_image": lambda: imaging.code_image(
                params.get("code", ""),
                params.get("language", ""),
            ),
            "text_image": lambda: imaging.text_image(
                params.get("text", ""),
                params.get("author", ""),
            ),
        }

        fn = op_map.get(operation)
        if fn is None:
            return {"error": f"Unknown operation: {operation}"}

        if operation not in ("error_card", "code_image", "text_image") and base is None:
            return {"error": "image_url required for this operation"}

        result = fn()
        if not result.ok:
            return {"error": result.error}

        data = result.to_bytes("PNG")
        if data is None:
            return {"error": "Failed to encode image"}

        return {
            "ok": True,
            "width": result.width,
            "height": result.height,
            "ops": result.op_chain,
            "size_bytes": len(data),
            "_image_result": result,  # internal  -  not serialised
        }

    # ── send_message
    async def _tool_send_message(self, chat_id: int, text: str) -> dict:
        """Legacy shape kept for compatibility; routes via the master router."""
        from ._tgbridge import send_via_master

        result = await send_via_master(text, chat_id, rich=False)
        return result if result.get("ok") else {"error": result.get("error")}

    # ── send_text_file
    async def _tool_send_text_file(
        self, filename: str, content: str, caption: str | None = None
    ) -> dict:
        """Send bounded generated text as an in-memory file to this chat only."""
        name = str(filename or "").strip()
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._ -]{0,95}\.(?:txt|md)", name, re.I
        ):
            return {"error": "Use a simple .txt or .md filename without a path."}
        if not isinstance(content, str) or not content.strip() or "\x00" in content:
            return {"error": "The attachment must contain non-empty plain text."}
        payload = content.encode("utf-8")
        if len(payload) > 256 * 1024:
            return {"error": "Text attachments are limited to 256 KiB."}
        if caption is not None and (
            not isinstance(caption, str) or len(caption) > 900
        ):
            return {"error": "The plain-text caption must be at most 900 characters."}
        if self.chat_id in (None, 0, "", "0"):
            return {"error": "There is no current chat to receive the attachment."}

        attachment = BytesIO(payload)
        attachment.name = name
        from ._tgbridge import send_file_via_master

        result = await send_file_via_master(
            attachment,
            self.chat_id,
            caption=caption,
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "error": "Telegram could not deliver the text attachment.",
            }
        return {
            "ok": True,
            "filename": name,
            "size_bytes": len(payload),
            "message_id": result.get("message_id"),
        }

    # ── tg_route_send
    async def _tool_tg_route_send(
        self,
        chat: str,
        text: str,
        route: str = "auto",
        reply_to: int | None = None,
        parse_mode: str | None = None,
        link_preview: bool = True,
        rich: bool = False,
    ) -> dict:
        from ._tgbridge import send_via_master

        return await send_via_master(
            text,
            chat,
            route=route,
            reply_to=reply_to,
            parse_mode=parse_mode,
            link_preview=link_preview,
            rich=rich,
        )

    # ── tg_read
    async def _tool_tg_read(
        self,
        chat: str,
        limit: int = 20,
        route: str = "user",
    ) -> dict:
        from ._tgbridge import read_recent

        return await read_recent(chat, limit=limit, route=route)

    # ── plugin_manager
    async def _tool_plugin_manager(
        self,
        action: str = "list",
        plugin: str = "",
    ) -> dict:
        from pyUltroid._core import loader as _loader

        action = (action or "list").lower()
        if action == "list":
            from pyUltroid.tracer import tr

            manifests = _loader.registry.get_active()
            stats = tr.index.stats()
            return {
                "ok": True,
                "active": sorted(m.name for m in manifests),
                "count": len(manifests),
                "handler_totals": {
                    k: stats.get(k, 0) for k in ("plugins", "handlers")
                },
            }

        plugin = (plugin or "").strip()
        if not plugin:
            return {"error": f"plugin name required for action='{action}'"}

        if action == "load":
            path = _loader._locate_plugin(plugin)
            if not path:
                return {"error": f"Could not locate plugin file for '{plugin}'"}
            manifest = await _loader._default_loader.load_plugin(path)
            if manifest is None:
                return {"error": f"'{plugin}' resolved to a dunder file"}
            return {
                "ok": manifest.is_active,
                "name": manifest.name,
                "status": manifest.status,
                "error_info": manifest.error_info or None,
            }

        if action == "unload":
            return await _loader.unload_plugin_async(plugin)

        if action == "reload":
            result = await _loader.reload_plugin_async(plugin)
            return {
                "ok": result.get("success", False),
                "name": result.get("name", plugin),
            }

        return {"error": f"Unknown action '{action}'"}

    # ── raw_mtproto
    async def _tool_raw_mtproto(
        self,
        function: str,
        arguments: dict | None = None,
    ) -> dict:
        from pyUltroid import master
        from ._tgbridge import invoke_raw

        client = master.route()
        return await invoke_raw(client, function, arguments)

    # ── web_fetch
    async def _tool_web_fetch(self, url: str, max_chars: int = 4000) -> dict:
        import re as _re

        import aiohttp

        if not (url.startswith("http://") or url.startswith("https://")):
            return {"error": "Only http/https URLs are fetched"}
        max_chars = max(200, min(int(max_chars or 4000), 20000))
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=15),
                    headers={"User-Agent": "Mozilla/5.0 (compatible; UltroidAgent)"},
                ) as r:
                    if r.status >= 400:
                        return {"error": f"HTTP {r.status}", "url": url}
                    body = await r.text(errors="replace")
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}", "url": url}
        # Crude HTML-to-text: drop script/style blocks, then tags, then
        # collapse whitespace. Good enough for the model to read content.
        body = _re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", body)
        body = _re.sub(r"(?s)<[^>]+>", " ", body)
        body = _re.sub(r"\s+", " ", body).strip()
        return {"ok": True, "url": url, "chars": len(body), "text": body[:max_chars]}

    # ── think
    async def _tool_think(self, thought: str) -> dict:
        return {"ok": True, "noted": str(thought)[:2000]}

    # ── interactive: ask the user, wait, return the answer
    @property
    def interactive(self) -> "InteractiveTools":
        """The interactive tool set for this executor's chat.

        Bound to ``chat_id`` on first use, so a per-request executor always
        asks in the right place even if the chat was set after construction.
        """
        if self._interactive is None:
            from ._bridge import shared_bridge
            from ._interactive import InteractiveTools

            self._interactive = InteractiveTools(shared_bridge(), chat_id=self.chat_id)
        elif self._interactive.chat_id != self.chat_id:
            self._interactive.bind(self.chat_id)
        return self._interactive

    async def _tool_ask_user(
        self,
        question: str,
        choices: list | None = None,
        note: str = "",
        ttl: float | None = None,
    ) -> dict:
        return await self.interactive.ask_user(
            question=question, choices=choices, note=note, ttl=ttl
        )

    async def _tool_confirm_action(
        self,
        what: str,
        yes_label: str = "Yes, do it",
        no_label: str = "No",
        ttl: float | None = None,
    ) -> dict:
        return await self.interactive.confirm_action(
            what=what, yes_label=yes_label, no_label=no_label, ttl=ttl
        )

    async def _tool_collect_fields(
        self, fields: list | None = None, ttl: float | None = None
    ) -> dict:
        return await self.interactive.collect_fields(fields=fields, ttl=ttl)

    # ── delegate
    async def _tool_delegate(self, task: str, max_rounds: int = 3) -> dict:
        if self.depth >= MAX_AGENT_DEPTH:
            return {
                "error": (
                    f"Delegation depth limit reached ({MAX_AGENT_DEPTH}); "
                    "handle this task directly."
                ),
                "denied": True,
            }
        from ._agent import UltroidAgent

        # Context isolation: the sub-agent gets its own window/history and a
        # capability set that can never spawn grandchildren.
        sub_caps = frozenset(self.caps - {Cap.AGENT_SPAWN})
        sub = UltroidAgent()
        response = await sub.respond(
            user_message=str(task)[:8000],
            chat_id=self.chat_id,
            use_history=False,
            use_cache=False,
            caps=sub_caps,
            max_rounds=max(1, min(int(max_rounds or 3), 5)),
            _depth=self.depth + 1,
        )
        return {
            "ok": response.ok,
            "content": response.content[:4000],
            "provider": response.provider,
            "tools_used": response.tools_used,
            "tool_rounds": response.tool_rounds,
            "error": response.error,
        }

    # ── get_bot_status
    async def _tool_get_bot_status(self) -> dict:
        try:
            from pyUltroid.updater import up
            from pyUltroid.tracer import tr

            idx = tr.index.stats()
            return {
                "version": up.git.current_sha(),
                "branch": up.git.current_branch(),
                "plugins": idx.get("plugins", 0),
                "handlers": idx.get("handlers", 0),
                "uptime_info": "available via .uptime command",
            }
        except Exception as e:
            return {"error": str(e)}

    # ── remember
    async def _tool_remember(
        self,
        action: str,
        key: str,
        value: str = "",
    ) -> dict:
        r_key = f"agent:memory:{key}"
        try:
            from pyUltroid.shell import sh

            redis = sh.sessions._get_redis()
            if redis is None:
                return {"error": "Redis not available for memory"}
            if action == "store":
                redis.set(r_key, value)
                return {"ok": True, "stored": key}
            elif action == "recall":
                val = redis.get(r_key)
                return {"ok": True, "key": key, "value": val or "(not found)"}
            elif action == "forget":
                redis.delete(r_key)
                return {"ok": True, "forgotten": key}
        except Exception as e:
            return {"error": str(e)}

    # ── web_search
    async def _tool_web_search(self, query: str, max_results: int = 5) -> dict:
        try:
            import aiohttp

            max_results = max(1, min(max_results, 10))
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    "https://ddg-api.awam.repl.co/api/search",
                    params={"query": query, "maxNumResults": max_results},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as r:
                    data = await r.json()
                    results = [
                        {
                            "title": d.get("Title", ""),
                            "snippet": d.get("Snippet", ""),
                            "url": d.get("Link", ""),
                        }
                        for d in data[:max_results]
                    ]
                    return {"query": query, "results": results}
        except Exception as e:
            return {"error": str(e), "query": query}


# ── Module singleton factory
def make_executor(
    chat_id: int = 0,
    caps: frozenset[str] | None = None,
    depth: int = 0,
) -> ToolExecutor:
    """
    Build a ToolExecutor. Pass ``caps`` to grant beyond (or restrict below)
    DEFAULT_AGENT_CAPS  -  e.g. frozenset(DEFAULT_AGENT_CAPS | {Cap.UPDATE_APPLY}).
    """
    executor = ToolExecutor(chat_id=chat_id, caps=caps)
    executor.depth = max(0, int(depth))
    return executor


# ── ToolPipeline
class ToolPipeline:
    """Chain tool calls through a single :class:`ToolExecutor`, optionally
    feeding each step's output into the next step's arguments.

    Example::

        pipe = ToolPipeline(executor)
        pipe.add("web_search", {"query": "rust async runtime"})
        pipe.add("remember", {"action": "store", "key": "rust"},
                 feed={"results": "value"})
        outcomes = await pipe.run()
    """

    def __init__(self, executor: ToolExecutor | None = None):
        self.executor = executor or ToolExecutor()
        self._steps: list[dict] = []

    def add(
        self,
        tool: str,
        arguments: dict | None = None,
        feed: dict[str, str] | None = None,
    ) -> ToolPipeline:
        """Append a step. Returns self for chaining."""
        self._steps.append(
            {
                "tool": tool,
                "arguments": dict(arguments or {}),
                "feed": dict(feed or {}),
            }
        )
        return self

    async def run(self) -> list[dict]:
        """Execute every step in order, returning per-step outcomes.

        Each outcome is ``{"tool", "arguments", "result"}`` where
        ``arguments`` is the dict actually dispatched (declared args plus any
        fed-forward values)."""
        outcomes: list[dict] = []
        prev: dict | None = None
        for step in self._steps:
            args = dict(step["arguments"])
            if prev is not None:
                for result_key, arg_name in step["feed"].items():
                    if result_key in prev:
                        args[arg_name] = prev[result_key]
            result = await self.executor.execute(step["tool"], args)
            outcomes.append(
                {
                    "tool": step["tool"],
                    "arguments": args,
                    "result": result,
                }
            )
            prev = result
        return outcomes

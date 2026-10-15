# Ultroid - UserBot Rewrite
# pyUltroid/tracer/_searcher.py
#
# CodeSearcher  -  the query engine over PluginIndex.
#
# The searcher wraps plugin_index with:
#   - Natural language-ish query parsing ("who handles play?")
#   - Cross-reference queries ("which plugins use the same DB key?")
#   - Source extraction (the "yellow from egg" operation)
#   - Conflict detection (two handlers on same pattern)
#   - Dependency graph (plugin A calls functions in plugin B)
#   - Performance queries (slowest handlers via TraceTracker)
#
# Designed to be called by:
#   - The plugin command interface (human queries)
#   - The AI agent (programmatic queries for self-awareness)
#   - The trace tracker (correlating code to runtime behavior)

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pyUltroid.startup.logger import LOGS
from ._indexer import (
    FunctionRecord,
    HandlerRecord,
    ImportRecord,
    PluginRecord,
)
from ._tracker import TraceTracker

# ── SearchResult ───────────────────────────────────────────────────────────


@dataclass
class SearchResult:
    """Unified result type for all searcher queries."""

    query: str
    kind: str  # "handler" | "function" | "import" | "source" | "conflict" | "stats"
    results: list[Any]  # typed lists depending on kind
    took_ms: float
    count: int

    def is_empty(self) -> bool:
        return self.count == 0

    def __str__(self) -> str:
        return f"SearchResult(kind={self.kind}, count={self.count}, took={self.took_ms:.1f}ms)"


# ── CodeSearcher ───────────────────────────────────────────────────────────


class CodeSearcher:
    """
    High-level search interface over the live plugin index and trace history.

    Usage:
        from pyUltroid.tracer import searcher

        result = searcher.find("play")           # find handler
        result = searcher.source("play", "play_cmd")  # get source
        result = searcher.conflicts()            # find handler conflicts
        result = await searcher.performance()    # slowest handlers
    """

    def __init__(self, index=None, live=None, **kw):
        super().__init__(**kw)
        from ._indexer import PluginIndex

        self._index = index if index is not None else PluginIndex()
        self._live = live if live is not None else TraceTracker()


    # ── Handler search ─────────────────────────────────────────────────────

    def find(self, query: str) -> SearchResult:
        """
        Find handlers matching a pattern query.
        Tries exact match first, then partial, then fuzzy.
        """
        t0 = time.time()
        query = query.strip().lstrip(".")

        results = self._index.find_handler(query)

        # Also check function names if no handler match
        if not results:
            fn_results = self._index.find_function(query)
            return SearchResult(
                query=query,
                kind="function",
                results=fn_results,
                took_ms=(time.time() - t0) * 1000,
                count=len(fn_results),
            )

        return SearchResult(
            query=query,
            kind="handler",
            results=results,
            took_ms=(time.time() - t0) * 1000,
            count=len(results),
        )

    def find_handler(self, pattern: str) -> SearchResult:
        """Explicit handler search by pattern."""
        t0 = time.time()
        results = self._index.find_handler(pattern.lstrip("."))
        return SearchResult(
            query=pattern,
            kind="handler",
            results=results,
            took_ms=(time.time() - t0) * 1000,
            count=len(results),
        )

    def find_function(self, name: str) -> SearchResult:
        """Find functions by name across all plugins."""
        t0 = time.time()
        results = self._index.find_function(name)
        return SearchResult(
            query=name,
            kind="function",
            results=results,
            took_ms=(time.time() - t0) * 1000,
            count=len(results),
        )

    def find_db_key(self, key: str) -> SearchResult:
        """Find all functions that access a DB key."""
        t0 = time.time()
        results = self._index.find_db_key(key)
        return SearchResult(
            query=key,
            kind="function",
            results=results,
            took_ms=(time.time() - t0) * 1000,
            count=len(results),
        )

    def find_import(self, module: str) -> SearchResult:
        """Find all plugins importing a module."""
        t0 = time.time()
        results = self._index.find_import(module)
        return SearchResult(
            query=module,
            kind="import",
            results=results,
            took_ms=(time.time() - t0) * 1000,
            count=len(results),
        )

    def plugin_info(self, name: str) -> PluginRecord | None:
        """Get the full index record for a plugin."""
        return self._index.get_plugin(name)

    # ── Source extraction ──────────────────────────────────────────────────

    def source(self, plugin: str, func_name: str) -> SearchResult:
        """
        Extract live source code of a specific function.
        The artistic "pull yellow from egg" operation.
        """
        t0 = time.time()
        code = self._index.get_source_snippet(plugin, func_name)
        return SearchResult(
            query=f"{plugin}.{func_name}",
            kind="source",
            results=[code] if code else [],
            took_ms=(time.time() - t0) * 1000,
            count=1 if code else 0,
        )

    def source_by_handler(self, pattern: str) -> SearchResult:
        """
        Find which plugin+function handles a pattern, then extract its source.
        Single call: pattern → source.
        """
        t0 = time.time()
        handlers = self._index.find_handler(pattern.lstrip("."))
        if not handlers:
            return SearchResult(
                query=pattern,
                kind="source",
                results=[],
                took_ms=(time.time() - t0) * 1000,
                count=0,
            )

        h = handlers[0]
        code = self._index.get_source_snippet(h.plugin, h.func_name)
        return SearchResult(
            query=pattern,
            kind="source",
            results=[(h, code)] if code else [],
            took_ms=(time.time() - t0) * 1000,
            count=1 if code else 0,
        )

    # ── Conflict detection ────────────────────────────────────────────────

    def conflicts(self) -> SearchResult:
        """Find all handler pattern conflicts (two plugins on same pattern)."""
        t0 = time.time()
        found = self._index.conflicts()
        return SearchResult(
            query="conflicts",
            kind="conflict",
            results=found,
            took_ms=(time.time() - t0) * 1000,
            count=len(found),
        )

    # ── Cross-reference ────────────────────────────────────────────────────

    def db_key_map(self) -> dict[str, list[str]]:
        """
        Returns {db_key → [plugin.function, ...]} for all DB key accesses.
        Useful for understanding what code touches what data.
        """
        result: dict[str, list[str]] = {}
        for rec in [self._index.get_plugin(n) for n in self._index.all_plugins()]:
            if not rec:
                continue
            for fn in rec.functions:
                for key in fn.db_keys:
                    result.setdefault(key, []).append(f"{rec.name}.{fn.name}")
        return result

    def who_calls(self, func_name: str) -> list[tuple[str, str]]:
        """
        Find all (plugin, caller_function) pairs that call func_name.
        Returns [(plugin_name, calling_function_name), ...]
        """
        results = []
        for name in self._index.all_plugins():
            rec = self._index.get_plugin(name)
            if not rec:
                continue
            for fn in rec.functions:
                if any(func_name in call for call in fn.calls):
                    results.append((name, fn.name))
        return results

    def dependency_graph(self) -> dict[str, list[str]]:
        """
        Returns {plugin → [modules it imports from]} for all plugins.
        A rough dependency map.
        """
        graph: dict[str, list[str]] = {}
        for name in self._index.all_plugins():
            rec = self._index.get_plugin(name)
            if not rec:
                continue
            imports = list(
                {
                    imp.module
                    for imp in rec.imports
                    if imp.module and not imp.module.startswith(".")
                }
            )
            graph[name] = imports
        return graph

    # ── Index stats ────────────────────────────────────────────────────────

    def index_stats(self) -> dict:
        """Current state of the plugin index."""
        return self._index.stats()

    def handler_map(self) -> dict[str, str]:
        """All patterns → plugin_name, with CONFLICT flags."""
        return self._index.handler_map()

    def all_plugins(self) -> list[str]:
        return self._index.all_plugins()

    # ── Performance (async  -  needs trace data) ────────────────────────────

    async def performance(self, n: int = 10) -> dict[str, Any]:
        """
        Top n slowest and most-failed handlers from trace history.
        Correlates live code index with runtime trace data.
        """
        stats = await self._live.stats()
        slow = await self._live.slow(threshold_ms=500, n=n)
        failures = await self._live.failures(n=n)

        slow_summary = [
            {
                "handler": t.handler,
                "plugin": t.plugin,
                "duration_ms": t.duration_ms,
                "user_id": t.user_id,
            }
            for t in slow
        ]
        fail_summary = [
            {
                "handler": t.handler,
                "plugin": t.plugin,
                "exc_type": t.exc_type,
                "exception": (t.exception or "")[:200],
            }
            for t in failures
        ]

        return {
            "overall": stats,
            "slowest": slow_summary,
            "failures": fail_summary,
        }

    # ── Natural language interface (for AI agent) ─────────────────────────

    def nl_query(self, question: str) -> SearchResult:
        """
        Parse a natural-language query into a search operation.
        Used by the AI agent to search code without knowing exact API.

        Supports:
            "who handles .play"          → find_handler("play")
            "which plugin imports yt_dlp" → find_import("yt_dlp")
            "show source of play_cmd"    → source_by_handler("play")
            "what uses MUSIC_QUEUE key"  → find_db_key("MUSIC_QUEUE")
            "find function download"     → find_function("download")
            "list conflicts"             → conflicts()
        """
        q = question.lower().strip()

        patterns = [
            (r"who handles?[.\s]+(\w+)", lambda m: self.find_handler(m.group(1))),
            (
                r"which plugin (?:handles?|does?) (\w+)",
                lambda m: self.find_handler(m.group(1)),
            ),
            (r"which plugin imports? ([\w_]+)", lambda m: self.find_import(m.group(1))),
            (
                r"show source (?:of|for)? ([\w.]+)",
                lambda m: self.source_by_handler(m.group(1)),
            ),
            (r"what uses? ([\w_]+) key", lambda m: self.find_db_key(m.group(1))),
            (r"find function ([\w_]+)", lambda m: self.find_function(m.group(1))),
            (r"list conflicts?", lambda m: self.conflicts()),
            (r"find ([\w_]+)", lambda m: self.find(m.group(1))),
        ]

        for pat, action in patterns:
            m = re.search(pat, q)
            if m:
                try:
                    return action(m)
                except Exception as e:
                    LOGS.error(f"[HCS:SEARCH] nl_query error: {e}")

        # Fallback  -  treat whole query as a handler search
        return self.find(question.strip())



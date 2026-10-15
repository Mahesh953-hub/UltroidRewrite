# Ultroid - UserBot Rewrite
# pyUltroid/tracer/_indexer.py
#
# PluginIndex  -  AST-based live index of all loaded plugins.
#
# What it does:
#   - Parses every loaded plugin's source into an AST at load time
#   - Extracts: handlers, patterns, functions, imports, DB key accesses,
#     external API calls, decorator chains, docstrings
#   - Keeps the index hot  -  updates on plugin load/unload
#   - Answers queries: "which plugin handles .play?",
#     "show me all functions that call udB", "what imports requests?"
#
# This runs on LIVE loaded code  -  not files on disk.
# If a plugin was hot-reloaded, the index reflects the new version.
#
# Design:
#   - Built at import time, populated by index_all() called from __main__
#   - Thread-safe reads (GIL), write-locked on updates
#   - In-process only  -  no Redis, no DB  -  speed is the point

from __future__ import annotations

import ast
import inspect
import re
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# ── Data types ─────────────────────────────────────────────────────────────


@dataclass
class HandlerRecord:
    """One @ultroid_cmd / @asst_cmd / @vc_cmd registered handler."""

    plugin: str  # plugin name (stem)
    func_name: str  # Python function name
    pattern: str | None  # raw regex pattern string
    decorator: str  # "ultroid_cmd" | "asst_cmd" | "vc_cmd" | "callback"
    line: int  # line number in source
    owner_only: bool = False
    fullsudo: bool = False
    only_devs: bool = False
    docstring: str | None = None


@dataclass
class FunctionRecord:
    """One function/coroutine defined in a plugin."""

    plugin: str
    name: str
    line: int
    is_async: bool
    args: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)  # functions it calls
    db_keys: list[str] = field(default_factory=list)  # udB.get_key("X") → "X"
    api_calls: list[str] = field(default_factory=list)  # external URLs/services
    docstring: str | None = None


@dataclass
class ImportRecord:
    """One import statement in a plugin."""

    plugin: str
    module: str
    names: list[str]  # specific names imported, empty = bare import
    line: int
    is_from: bool


@dataclass
class PluginRecord:
    """Complete index of one plugin."""

    name: str
    file_path: str
    indexed_at: float
    handlers: list[HandlerRecord] = field(default_factory=list)
    functions: list[FunctionRecord] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    source_hash: str = ""
    line_count: int = 0
    parse_error: str | None = None


# ── AST visitors ───────────────────────────────────────────────────────────


class _HandlerVisitor(ast.NodeVisitor):
    """Extract @ultroid_cmd / @asst_cmd / @vc_cmd decorated functions."""

    DECORATOR_NAMES = {"ultroid_cmd", "asst_cmd", "vc_cmd", "callback", "in_pattern"}

    def __init__(self, plugin_name: str):
        self.plugin = plugin_name
        self.handlers: list[HandlerRecord] = []

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self._check_decorators(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self._check_decorators(node)
        self.generic_visit(node)

    def _check_decorators(self, node):
        for dec in node.decorator_list:
            dec_name, kwargs = self._parse_decorator(dec)
            if dec_name not in self.DECORATOR_NAMES:
                continue

            pattern = kwargs.get("pattern")
            owner_only = kwargs.get("owner_only", False)
            fullsudo = kwargs.get("fullsudo", False)
            only_devs = kwargs.get("only_devs", False)

            docstring = ast.get_docstring(node)

            self.handlers.append(
                HandlerRecord(
                    plugin=self.plugin,
                    func_name=node.name,
                    pattern=pattern,
                    decorator=dec_name,
                    line=node.lineno,
                    owner_only=owner_only,
                    fullsudo=fullsudo,
                    only_devs=only_devs,
                    docstring=docstring,
                )
            )

    def _parse_decorator(self, node) -> tuple[str, dict]:
        """Extract decorator name and keyword arguments."""
        kwargs = {}

        if isinstance(node, ast.Name):
            return node.id, kwargs

        if isinstance(node, ast.Call):
            # Get name
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            else:
                return "", kwargs

            # Get keyword arguments
            for kw in node.keywords:
                val = self._literal(kw.value)
                if val is not None:
                    kwargs[kw.arg] = val

            # pattern may be positional arg[0]
            if node.args and "pattern" not in kwargs:
                val = self._literal(node.args[0])
                if val is not None:
                    kwargs["pattern"] = val

            return name, kwargs

        return "", kwargs

    def _literal(self, node) -> Any:
        try:
            return ast.literal_eval(node)
        except Exception:
            return None


class _FunctionVisitor(ast.NodeVisitor):
    """Extract functions, their calls, DB key accesses, API calls."""

    _DB_METHODS = {"get_key", "set_key", "del_key"}
    _API_PATTERN = re.compile(r"https?://[\w./%-]{4,}")

    def __init__(self, plugin_name: str, source: str):
        self.plugin = plugin_name
        self.source = source
        self.functions: list[FunctionRecord] = []

    def visit_AsyncFunctionDef(self, node):
        self._visit_func(node, is_async=True)

    def visit_FunctionDef(self, node):
        self._visit_func(node, is_async=False)

    def _visit_func(self, node, is_async: bool):
        args = [a.arg for a in node.args.args]
        calls = []
        db_keys = []
        api_calls = []

        for child in ast.walk(node):
            # Function calls
            if isinstance(child, ast.Call):
                call_name = self._call_name(child)
                if call_name:
                    calls.append(call_name)

                # DB key extraction: udB.get_key("FOO") → "FOO"
                if (
                    isinstance(child.func, ast.Attribute)
                    and child.func.attr in self._DB_METHODS
                    and child.args
                ):
                    key_val = None
                    try:
                        key_val = ast.literal_eval(child.args[0])
                    except Exception:
                        pass
                    if key_val:
                        db_keys.append(str(key_val))

            # String literals that look like API URLs
            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                for m in self._API_PATTERN.findall(child.value):
                    api_calls.append(m)

        docstring = ast.get_docstring(node)

        self.functions.append(
            FunctionRecord(
                plugin=self.plugin,
                name=node.name,
                line=node.lineno,
                is_async=is_async,
                args=args,
                calls=list(dict.fromkeys(calls)),  # dedup, preserve order
                db_keys=list(dict.fromkeys(db_keys)),
                api_calls=list(dict.fromkeys(api_calls)),
                docstring=docstring,
            )
        )

        self.generic_visit(node)

    def _call_name(self, node: ast.Call) -> str | None:
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return f"{self._attr_chain(node.func)}"
        return None

    def _attr_chain(self, node) -> str:
        if isinstance(node, ast.Attribute):
            return f"{self._attr_chain(node.value)}.{node.attr}"
        if isinstance(node, ast.Name):
            return node.id
        return "?"


class _ImportVisitor(ast.NodeVisitor):
    """Extract all import statements."""

    def __init__(self, plugin_name: str):
        self.plugin = plugin_name
        self.imports: list[ImportRecord] = []

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(
                ImportRecord(
                    plugin=self.plugin,
                    module=alias.name,
                    names=[],
                    line=node.lineno,
                    is_from=False,
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom):
        self.imports.append(
            ImportRecord(
                plugin=self.plugin,
                module=node.module or "",
                names=[a.name for a in node.names],
                line=node.lineno,
                is_from=True,
            )
        )


# ── PluginIndex ────────────────────────────────────────────────────────────


class PluginIndex:
    """
    Live AST index of all loaded plugins.

    Usage:
        from pyUltroid.tracer import plugin_index

        # Find which plugin handles .play
        results = plugin_index.find_handler("play")

        # Find all functions touching the CHATBOT_DB key
        results = plugin_index.find_db_key("CHATBOT_DB")

        # Get the full record for a plugin
        rec = plugin_index.get_plugin("play")

        # Find all plugins importing yt_dlp
        results = plugin_index.find_import("yt_dlp")
    """

    def __init__(self):
        self._lock: threading.RLock = threading.RLock()
        self._plugins: dict[str, PluginRecord] = {}
        self._built_at: float = 0.0

    def index_plugin(self, name: str, file_path: str) -> PluginRecord:
        """
        Parse and index one plugin file.
        Replaces any existing record for this plugin name.
        """
        record = self._parse(name, file_path)
        with self._lock:
            self._plugins[name] = record
        return record

    def index_from_module(self, name: str, module) -> PluginRecord | None:
        """
        Index a plugin from its already-loaded module object.
        Uses inspect.getfile() to find source path.
        """
        try:
            file_path = inspect.getfile(module)
            return self.index_plugin(name, file_path)
        except (TypeError, OSError):
            return None

    def index_all(self) -> int:
        """
        Index all currently loaded plugins from sys.modules.
        Returns number of plugins indexed.
        Called once at startup, then incrementally on load/unload.
        """
        count = 0
        # Walk sys.modules for anything that looks like a plugin
        for mod_name, mod in list(sys.modules.items()):
            if mod is None:
                continue
            # Plugins are top-level modules with a file in plugins/
            try:
                file_path = inspect.getfile(mod)
            except (TypeError, OSError):
                continue
            if "/plugins/" not in file_path and "\\plugins\\" not in file_path:
                continue
            stem = Path(file_path).stem
            self.index_plugin(stem, file_path)
            count += 1

        self._built_at = time.time()
        return count

    def remove_plugin(self, name: str) -> bool:
        """Remove a plugin from the index (called on unload)."""
        with self._lock:
            existed = name in self._plugins
            self._plugins.pop(name, None)
        return existed

    # ── Queries ─────────────────────────────────────────────────────────────

    def find_handler(self, pattern_query: str) -> list[HandlerRecord]:
        """
        Find handlers whose pattern matches or contains pattern_query.
        Case-insensitive. Supports partial match.

        find_handler("play")    → handlers with "play" in their pattern
        find_handler(r"^play$") → exact regex match
        """
        q = pattern_query.lower().strip()
        results = []
        with self._lock:
            for rec in self._plugins.values():
                for h in rec.handlers:
                    if h.pattern and q in h.pattern.lower():
                        results.append(h)
        return results

    def find_function(self, name_query: str) -> list[FunctionRecord]:
        """Find functions by name (partial match, case-insensitive)."""
        q = name_query.lower()
        results = []
        with self._lock:
            for rec in self._plugins.values():
                for fn in rec.functions:
                    if q in fn.name.lower():
                        results.append(fn)
        return results

    def find_db_key(self, key: str) -> list[FunctionRecord]:
        """Find all functions that access a specific DB key."""
        results = []
        with self._lock:
            for rec in self._plugins.values():
                for fn in rec.functions:
                    if key in fn.db_keys:
                        results.append(fn)
        return results

    def find_import(self, module_query: str) -> list[ImportRecord]:
        """Find all import statements referencing a module."""
        q = module_query.lower()
        results = []
        with self._lock:
            for rec in self._plugins.values():
                for imp in rec.imports:
                    if q in imp.module.lower():
                        results.append(imp)
        return results

    def find_api_call(self, url_query: str) -> list[FunctionRecord]:
        """Find all functions that reference a URL/API endpoint."""
        q = url_query.lower()
        results = []
        with self._lock:
            for rec in self._plugins.values():
                for fn in rec.functions:
                    if any(q in u.lower() for u in fn.api_calls):
                        results.append(fn)
        return results

    def get_plugin(self, name: str) -> PluginRecord | None:
        with self._lock:
            return self._plugins.get(name)

    def all_plugins(self) -> list[str]:
        with self._lock:
            return list(self._plugins.keys())

    def all_handlers(self) -> list[HandlerRecord]:
        with self._lock:
            return [h for rec in self._plugins.values() for h in rec.handlers]

    def handler_map(self) -> dict[str, str]:
        """
        Returns {pattern → plugin_name} for quick lookup.
        If two plugins share a pattern, both are included with a note.
        """
        result = {}
        with self._lock:
            for rec in self._plugins.values():
                for h in rec.handlers:
                    if h.pattern:
                        existing = result.get(h.pattern)
                        if existing:
                            result[h.pattern] = f"{existing} + {rec.name} [CONFLICT]"
                        else:
                            result[h.pattern] = rec.name
        return result

    def conflicts(self) -> list[tuple[str, str, str]]:
        """Return [(pattern, plugin_a, plugin_b)] for conflicting handlers."""
        seen: dict[str, str] = {}
        found = []
        with self._lock:
            for rec in self._plugins.values():
                for h in rec.handlers:
                    if not h.pattern:
                        continue
                    if h.pattern in seen:
                        found.append((h.pattern, seen[h.pattern], rec.name))
                    else:
                        seen[h.pattern] = rec.name
        return found

    def stats(self) -> dict:
        with self._lock:
            total_handlers = sum(len(r.handlers) for r in self._plugins.values())
            total_functions = sum(len(r.functions) for r in self._plugins.values())
            total_imports = sum(len(r.imports) for r in self._plugins.values())
            errors = [r.name for r in self._plugins.values() if r.parse_error]
        return {
            "plugins": len(self._plugins),
            "handlers": total_handlers,
            "functions": total_functions,
            "imports": total_imports,
            "errors": errors,
            "built_at": self._built_at,
        }

    # ── Source extraction ("pull yellow from egg") ─────────────────────────

    def get_source_snippet(
        self,
        plugin: str,
        func_name: str,
        context_lines: int = 3,
    ) -> str | None:
        """
        Extract the exact source code of a function from a running plugin.

        This is the "pulling yellow from egg" operation  - 
        surgical extraction from live running code.
        Returns the function source with context_lines of surrounding context.
        """
        rec = self.get_plugin(plugin)
        if not rec or rec.parse_error:
            return None

        try:
            mod = sys.modules.get(plugin)
            if mod is None:
                return None
            func = getattr(mod, func_name, None)
            if func is None:
                # Search nested (wrapped functions)
                for attr in dir(mod):
                    obj = getattr(mod, attr, None)
                    if callable(obj) and getattr(obj, "__name__", "") == func_name:
                        func = obj
                        break
            if func is None:
                return None
            return inspect.getsource(func)
        except (OSError, TypeError):
            # Fall back to file read
            try:
                src = Path(rec.file_path).read_text(encoding="utf-8")
                lines = src.splitlines()
                # Find the function in the file
                fn_records = [f for f in rec.functions if f.name == func_name]
                if not fn_records:
                    return None
                start = max(0, fn_records[0].line - 1 - context_lines)
                # Find end by indentation
                end = fn_records[0].line
                indent = len(lines[fn_records[0].line - 1]) - len(
                    lines[fn_records[0].line - 1].lstrip()
                )
                for i in range(fn_records[0].line, len(lines)):
                    line = lines[i]
                    if line.strip() and (len(line) - len(line.lstrip())) <= indent:
                        if i > fn_records[0].line:
                            end = i
                            break
                    end = i + 1
                return "\n".join(lines[start:end])
            except Exception:
                return None

    # ── Internal ────────────────────────────────────────────────────────────

    def _parse(self, name: str, file_path: str) -> PluginRecord:
        try:
            source = Path(file_path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return PluginRecord(
                name=name,
                file_path=file_path,
                indexed_at=time.time(),
                parse_error=str(e),
            )

        import hashlib

        src_hash = hashlib.md5(source.encode()).hexdigest()

        # Skip re-index if source unchanged
        with self._lock:
            existing = self._plugins.get(name)
        if existing and existing.source_hash == src_hash:
            return existing

        try:
            tree = ast.parse(source, filename=file_path)
        except SyntaxError as e:
            return PluginRecord(
                name=name,
                file_path=file_path,
                indexed_at=time.time(),
                parse_error=str(e),
                source_hash=src_hash,
            )

        h_visitor = _HandlerVisitor(name)
        f_visitor = _FunctionVisitor(name, source)
        i_visitor = _ImportVisitor(name)

        h_visitor.visit(tree)
        f_visitor.visit(tree)
        i_visitor.visit(tree)

        return PluginRecord(
            name=name,
            file_path=file_path,
            indexed_at=time.time(),
            handlers=h_visitor.handlers,
            functions=f_visitor.functions,
            imports=i_visitor.imports,
            source_hash=src_hash,
            line_count=len(source.splitlines()),
        )




# ── Module singleton ───────────────────────────────────────────────────────

#: The process-wide plugin AST index. ``startup/loader.py`` warms it with
#: ``index_all()`` after the plugin trees load; the code
#: search read from it.
plugin_index = PluginIndex()

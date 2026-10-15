"""Render registered command regexes as readable command syntax.

The runtime registry stores regexes because handlers need matching rules. User-
facing help should show command names and a single ``[arguments]`` hint instead
of exposing those implementation details.
"""

from __future__ import annotations

import re

_SIMPLE_ALTERNATIVES = re.compile(r"(?:\?:)?([A-Za-z0-9_+-]+(?:\|[A-Za-z0-9_+-]+)+)")


def _group_end(text: str, start: int) -> int | None:
    """Return the matching ')' for an opening group at *start*."""
    depth = 0
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index
    return None


def command_aliases(pattern: str) -> tuple[str, ...]:
    """Return literal command-name variants represented by a handler regex.

    Only simple alternatives in the command name are expanded. Argument
    captures, anchors, whitespace expressions, and other regex syntax are
    intentionally omitted from the aliases.
    """
    source = str(pattern or "").strip()
    if source.startswith("^"):
        source = source[1:]
    source = source.removesuffix("$")
    if source.startswith(r"/"):
        source = source[1:]

    variants = [""]
    index = 0
    has_command = False
    while index < len(source):
        char = source[index]
        if char.isspace():
            break
        if char == "\\":
            # A whitespace/class escape starts the argument part; escaped
            # punctuation is retained as a literal where it is unambiguous.
            if index + 1 >= len(source):
                break
            escaped = source[index + 1]
            if escaped.isalpha() or escaped.isdigit():
                break
            variants = [value + escaped for value in variants]
            has_command = True
            index += 2
            continue
        if char == "(":
            end = _group_end(source, index)
            if end is None:
                break
            content = source[index + 1 : end]
            match = _SIMPLE_ALTERNATIVES.fullmatch(content)
            if not match:
                # ``(?: ... )`` and captured argument groups are syntax, not
                # part of the display name.
                break
            options = match.group(1).split("|")
            variants = [prefix + option for prefix in variants for option in options]
            has_command = True
            index = end + 1
            continue
        if char in "?$*+[{":
            break
        if char in ".^$|]}":
            break
        variants = [value + char for value in variants]
        has_command = True
        index += 1

    if not has_command:
        return ()
    # Avoid a pathological regex creating a huge menu/index entry.
    return tuple(dict.fromkeys(variants[:24]))


def command_syntax(pattern: str, prefix: str = ".") -> str:
    """Format a registered pattern as a concise, human-readable command."""
    source = str(pattern or "").strip()
    aliases = command_aliases(source)
    # Captures and common whitespace/argument constructs belong in a single
    # readable placeholder, never in the user-facing command roster.
    has_arguments = bool(
        re.search(
            r"\s|\\[sSdDwW]|\.\*|\.\+|\\d|\\w|\[\^?.*?\]|\(\?[:P=!]",
            source,
        )
    )
    command_prefix = "" if prefix in {"", " ", "NO_HNDLR"} else str(prefix)
    names = aliases or ("command",)
    label = " / ".join(f"{command_prefix}{name}" for name in names)
    return f"{label} [arguments]" if has_arguments else label


def command_matches(pattern: str, query: str) -> bool:
    """Whether *query* names one of the literal commands in *pattern*."""
    return str(query or "").strip().lower() in {
        alias.lower() for alias in command_aliases(pattern)
    }


def command_patterns(plugin: str, registry) -> tuple[str, ...]:
    """Resolve a plugin's registered patterns from canonical or stem names."""
    name = str(plugin or "").replace("\\", "/").strip("/").removesuffix(".py")
    candidates = [name]
    if "/" in name:
        candidates.append(name.rsplit("/", 1)[-1])
    if "." in name:
        candidates.append(name.rsplit(".", 1)[-1])
    for candidate in dict.fromkeys(candidates):
        patterns = registry.get(candidate)
        if patterns is not None:
            return tuple(patterns or ())
    return ()

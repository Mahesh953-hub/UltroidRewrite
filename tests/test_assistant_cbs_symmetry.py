"""Static symmetry audit for the assistant/ callback-data model ("cbs").

Every ``cb(...)``/``cb_str(...)`` call (directly, or through a local
wrapper — see below) is an *emitter* of a callback-data token; every
``cb_pattern(...)``/``cb_exact_pattern(...)`` call is a *handler* of one
(see ``assistant/_cbs.py``'s module docstring, "Symmetry rule"). This test
reconstructs both sides by parsing the **source text** of every
``assistant/**.py`` file with :mod:`ast` instead of importing the modules.

Importing them for real would pull in ``pyUltroid``'s full bootstrap plus
several optional third-party packages individual assistant/ modules use
(``youtubesearchpython`` for ``ytdl.py``, ``akipy`` for ``games.py``, ...)
that this sandbox is not guaranteed to have installed — a purely static
audit needs none of that and still catches the actual bug class this test
exists for: a button emits callback data with no ``@callback`` handler
registered for it (dead button, silently times out), or a handler pattern
that no button ever emits (dead code / handler that never fires).

Two things earlier versions of this scan got wrong, both fixed here:

1. **Local wrapper functions.** ``callbackstuffs.py`` (and a couple of
   other files) define trivial one-line wrappers around ``cb``/
   ``cb_exact_pattern`` — ``_tok(*parts)``, ``_handler(*parts)``,
   ``_menu(key)``, ``_conv(key)``, ``start.py``'s ``_cbstuffs_menu(key)``,
   the ``_a(ans)`` closure in ``games.py``'s ``_aka_buttons`` — that cover
   most of the real emit/handle call sites in this codebase. A scanner
   that only recognizes bare ``cb(...)``/``cb_pattern(...)`` calls never
   sees any of these, so it is nearly blind on the file that matters most.
   :func:`_resolve_local_aliases` finds every such wrapper (a function
   whose body is just ``return <core-func>(<literal-or-forwarded-args>)``)
   and builds a *template* for it, so a call like ``_tok("taglog")`` is
   understood as equivalent to
   ``cb("assistant", "cbstuffs", "taglog")``.

2. **Prefix-sharing is not compatibility.** Comparing only the first two
   literal args ("namespace:name") means an emitter
   ``assistant:cbstuffs:foo`` and a handler ``assistant:cbstuffs:bar`` —
   totally unrelated buttons/handlers that happen to share a namespace —
   would count as "matched". Instead, every emitter/handler call is
   resolved into a full token *spec*: an ordered tuple of segments, each
   either a literal string or :data:`WILD` (a segment whose value can't be
   determined statically — a variable, loop index, f-string, ...— but
   which *is* a real segment, occupying a real position). Two specs are
   :func:`_compatible` only if every literal-vs-literal segment pair
   actually agrees; a ``cb_exact_pattern`` handler additionally requires
   the lengths to match exactly (it compiles to a byte-exact pattern, no
   free tail), while a ``cb_pattern`` handler only requires its own
   segments to be a compatible *prefix* of the emitted spec (it compiles
   to ``b"<prefix>:(.*)"``).

Calls whose target function can't be resolved at all (neither a directly
imported core function nor a recognized local alias) are skipped, same as
before — this is a static approximation, not a full interpreter.
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ASSISTANT_DIR = ROOT / "assistant"

#: The cross-boundary callback tokens are emitted in ``plugins/`` or
#: ``pyUltroid`` core and handled in ``assistant/`` (or vice versa). After the
#: migration those files also use the cb/cb_pattern/cb_exact_pattern primitives
#: (imported from the dependency-free leaf ``pyUltroid._core._cbs``), so the
#: static scan below must walk them too or the migrated emitters/handlers would
#: each look one-sided.
CROSS_BOUNDARY_FILES = (
    ROOT / "plugins" / "_inline.py",
    ROOT / "plugins" / "_help.py",
    ROOT / "plugins" / "_ultroid.py",
    ROOT / "pyUltroid" / "fns" / "admins.py",
    ROOT / "pyUltroid" / "startup" / "funcs.py",
)

CORE_EMITTER_FUNCS = {"cb", "cb_str"}
CORE_HANDLER_FUNCS = {"cb_pattern", "cb_exact_pattern"}
CORE_FUNCS = CORE_EMITTER_FUNCS | CORE_HANDLER_FUNCS


def _load_cbs_module():
    """Import the dependency-free leaf ``pyUltroid/_core/_cbs.py`` in isolation.

    ``import assistant._cbs`` (or any ``pyUltroid`` package import) would pull
    in the full bot bootstrap — exactly what this test avoids. The leaf only
    imports stdlib ``re``, so loading it directly by file path (same technique
    ``tests/test_universal_runtime.py`` uses) is safe and dependency-free.
    ``assistant/_cbs.py`` is a thin re-export of this same leaf.
    """
    leaf = ROOT / "pyUltroid" / "_core" / "_cbs.py"
    spec = importlib.util.spec_from_file_location("pyUltroid._core._cbs", leaf)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.modules["pyUltroid._core._cbs"] = module
    return module


_cbs = _load_cbs_module()


def _iter_assistant_files():
    for path in sorted(ASSISTANT_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _iter_scanned_files():
    """All files the symmetry audit walks: every ``assistant/**`` module plus
    the handful of ``plugins/``/``pyUltroid`` files that participate in the
    cross-boundary callback tokens (migrated to the colon model — their
    emitters/handlers must pair up against the assistant side).
    """
    yield from _iter_assistant_files()
    for path in CROSS_BOUNDARY_FILES:
        if path.exists():
            yield path


class _Wild:
    """Sentinel: a callback-data segment that is a real, present segment
    (it occupies a position and will hold *some* value at runtime) whose
    literal value this static scan can't determine — a variable, a loop
    index, an f-string, etc. Always treated as compatible with whatever
    the other side has in the same position, since we can't prove a
    mismatch.
    """

    def __repr__(self):
        return "*"


WILD = _Wild()
_DROP = (
    object()
)  # sentinel: this arg evaluates away entirely (None/""), like _cbs._clean does


def _resolve_arg(node) -> object:
    """Resolve one positional argument node of a ``cb``-family call (or of
    an alias call standing in for one) into a token segment: a literal
    ``str``, :data:`WILD`, or :data:`_DROP` (mirrors ``_cbs._clean``
    dropping ``None``/``""`` parts).
    """
    if isinstance(node, ast.Constant):
        if node.value is None or node.value == "":
            return _DROP
        return str(node.value)
    return WILD


def _segments_from_args(arg_nodes) -> tuple:
    segs = []
    for node in arg_nodes:
        if isinstance(node, ast.Starred):
            # Unpacking some runtime iterable directly into a bare
            # cb()/cb_pattern() call (not through a recognized alias) —
            # we don't know how many segments that expands to, so
            # represent it as exactly one "at least something here"
            # wildcard. No call site in this codebase does this, but
            # degrade gracefully rather than crash if one ever does.
            segs.append(WILD)
            continue
        resolved = _resolve_arg(node)
        if resolved is _DROP:
            continue
        segs.append(resolved)
    return tuple(segs)


@dataclass
class AliasSpec:
    """Template for a local wrapper function found to be a thin proxy for
    one of the four core ``_cbs`` functions — e.g.
    ``def _menu(key): return cb("assistant", "cbstuffs", "menu", key)``.

    ``template`` is an ordered list of slots:
      - ``("lit", value)``      — a literal segment baked into the wrapper.
      - ``("drop",)``           — a literal ``None``/``""`` baked in (dropped).
      - ``("param", index)``    — forward the wrapper's own *positional*
                                   parameter at ``index`` (resolved against
                                   the actual call-site argument at that
                                   same position).
      - ``("vararg",)``         — forward *all* remaining call-site
                                   positional arguments from
                                   ``vararg_start`` onward (a ``*parts``
                                   passthrough).
      - ``("wild",)``           — a fixed segment whose value the wrapper
                                   itself can't express literally (e.g. a
                                   closure variable), independent of the
                                   call site.
    """

    kind: str  # "emit" or "handle"
    exact: bool
    template: list = field(default_factory=list)
    vararg_start: int = 0


def _build_alias_spec(func_def: ast.FunctionDef, import_map: dict, known_aliases: dict):
    """Return an :class:`AliasSpec` if ``func_def`` is a trivial one-line
    proxy for a core ``_cbs`` function (or for an already-resolved alias),
    else ``None``.
    """
    body = [
        n
        for n in func_def.body
        if not (
            isinstance(n, ast.Expr)
            and isinstance(n.value, ast.Constant)
            and isinstance(n.value.value, str)
        )
    ]
    if len(body) != 1 or not isinstance(body[0], ast.Return) or body[0].value is None:
        return None
    call = body[0].value
    if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Name):
        return None
    target_name = call.func.id
    if target_name in import_map:
        core = import_map[target_name]
        kind = "emit" if core in CORE_EMITTER_FUNCS else "handle"
        exact = core == "cb_exact_pattern"
    elif target_name in known_aliases:
        base = known_aliases[target_name]
        kind, exact = base.kind, base.exact
    else:
        return None

    # Map the wrapper's own positional parameter names to their index.
    args = func_def.args
    positional_names = [a.arg for a in (*args.posonlyargs, *args.args)]
    param_index = {name: i for i, name in enumerate(positional_names)}
    vararg_name = args.vararg.arg if args.vararg else None

    template = []
    for node in call.args:
        if (
            isinstance(node, ast.Starred)
            and isinstance(node.value, ast.Name)
            and node.value.id == vararg_name
        ):
            template.append(("vararg",))
            continue
        if isinstance(node, ast.Constant):
            if node.value is None or node.value == "":
                template.append(("drop",))
            else:
                template.append(("lit", str(node.value)))
            continue
        if isinstance(node, ast.Name) and node.id in param_index:
            template.append(("param", param_index[node.id]))
            continue
        template.append(("wild",))

    return AliasSpec(
        kind=kind, exact=exact, template=template, vararg_start=len(positional_names)
    )


def _expand_alias(spec: AliasSpec, call_args) -> tuple:
    segs = []
    for slot in spec.template:
        stype = slot[0]
        if stype == "lit":
            segs.append(slot[1])
        elif stype == "drop":
            continue
        elif stype == "wild":
            segs.append(WILD)
        elif stype == "param":
            idx = slot[1]
            if idx < len(call_args):
                resolved = _resolve_arg(call_args[idx])
                if resolved is not _DROP:
                    segs.append(resolved)
            else:
                segs.append(WILD)
        elif stype == "vararg":
            for node in call_args[spec.vararg_start :]:
                resolved = _resolve_arg(node)
                if resolved is not _DROP:
                    segs.append(resolved)
    return tuple(segs)


def _import_map(tree: ast.Module) -> dict:
    """``{local_name: core_func_name}`` for every core ``_cbs`` function
    imported into this file (``from ._cbs import cb, ...`` /
    ``from .._cbs import cb, ...``).
    """
    mapping = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and node.module
            and node.module.endswith("_cbs")
        ):
            for alias in node.names:
                if alias.name in CORE_FUNCS:
                    mapping[alias.asname or alias.name] = alias.name
    return mapping


@dataclass
class Event:
    kind: str  # "emit" or "handle"
    exact: bool
    segments: tuple
    file: str
    lineno: int

    def render(self) -> str:
        parts = [s if isinstance(s, str) else "*" for s in self.segments]
        return ":".join(parts) if parts else "<empty>"


def _scan_file(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    import_map = _import_map(tree)

    aliases: dict = {}
    # Fixed-point loop so an alias that proxies another alias (not used
    # today, but kept general) still resolves within a few passes.
    template_source_ids = set()
    for _ in range(5):
        added = False
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            if node.name in aliases:
                continue
            resolved = _build_alias_spec(node, import_map, aliases)
            if resolved is not None:
                aliases[node.name] = resolved
                body = [
                    n
                    for n in node.body
                    if not (
                        isinstance(n, ast.Expr)
                        and isinstance(n.value, ast.Constant)
                        and isinstance(n.value.value, str)
                    )
                ]
                template_source_ids.add(id(body[0].value))
                added = True
        if not added:
            break

    events = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if id(node) in template_source_ids:
            # This call is the *definition* of a local alias's template
            # (e.g. the ``cb("assistant", "cbstuffs", *parts)`` inside
            # ``_tok``'s own body) — plumbing, not a real emit/handle
            # event. Counting it directly would let one wrapper
            # definition "namespace-match" against everything under it,
            # masking exactly the kind of one-sided real call sites this
            # scan exists to catch.
            continue
        name = node.func.id
        if name in import_map:
            core = import_map[name]
            kind = "emit" if core in CORE_EMITTER_FUNCS else "handle"
            exact = core == "cb_exact_pattern"
            segments = _segments_from_args(node.args)
            events.append(
                Event(kind, exact, segments, str(path.relative_to(ROOT)), node.lineno)
            )
        elif name in aliases:
            spec = aliases[name]
            segments = _expand_alias(spec, node.args)
            events.append(
                Event(
                    spec.kind,
                    spec.exact,
                    segments,
                    str(path.relative_to(ROOT)),
                    node.lineno,
                )
            )
    return events


def _collect_all():
    events = []
    for path in _iter_scanned_files():
        events.extend(_scan_file(path))
    emitted = [e for e in events if e.kind == "emit"]
    handled = [e for e in events if e.kind == "handle"]
    return emitted, handled, events


EMITTED, HANDLED, ALL_EVENTS = _collect_all()


def _compatible(handler: Event, emitted: Event) -> bool:
    h, e = handler.segments, emitted.segments
    if handler.exact:
        if len(h) != len(e):
            return False
        return all(a is WILD or b is WILD or a == b for a, b in zip(h, e))
    if len(e) < len(h):
        return False
    return all(a is WILD or b is WILD or a == b for a, b in zip(h, e))


def _namespace(ev: Event):
    """First two segments joined with ``SEP``, if both are literal — the
    coarse "namespace:name" used only by the sanity/legacy checks below
    (the real symmetry tests compare full specs, not this).
    """
    if len(ev.segments) < 2:
        return None
    a, b = ev.segments[0], ev.segments[1]
    if a is WILD or b is WILD:
        return None
    return f"{a}{_cbs.SEP}{b}"


EMITTED_NAMESPACES = {ns for ns in (_namespace(e) for e in EMITTED) if ns}
HANDLED_NAMESPACES = {ns for ns in (_namespace(e) for e in HANDLED) if ns}


def test_scan_actually_found_call_sites():
    """Sanity check the scan itself isn't silently finding nothing (e.g.
    from a rename of cb()/cb_pattern() breaking this test's assumptions),
    and that alias resolution is actually contributing events, not just
    the handful of bare cb()/cb_pattern() call sites.
    """
    assert EMITTED, "expected at least one cb()/cb_str() (direct or aliased) call site"
    assert (
        HANDLED
    ), "expected at least one cb_pattern()/cb_exact_pattern() (direct or aliased) call site"
    # Baseline established when this scan was rewritten to resolve local
    # wrapper aliases (callbackstuffs.py's _tok/_handler/_menu/_conv alone
    # account for ~90 call sites) — a drop far below this means alias
    # resolution silently broke.
    assert len(EMITTED) >= 60, f"expected >=60 emitted call sites, found {len(EMITTED)}"
    assert len(HANDLED) >= 20, f"expected >=20 handler call sites, found {len(HANDLED)}"


def test_local_wrapper_aliases_are_resolved():
    """Spot-check that specific local-wrapper call sites (invisible to a
    scanner that only recognizes bare cb()/cb_pattern() calls) are
    actually being resolved into real events with the expected segments.
    """
    rendered_emitted = {e.render() for e in EMITTED}
    rendered_handled = {e.render() for e in HANDLED}
    # callbackstuffs.py: _tok("taglog") / _handler("taglog")
    assert "assistant:cbstuffs:taglog" in rendered_emitted
    assert "assistant:cbstuffs:taglog" in rendered_handled
    # callbackstuffs.py: _menu("otvars")
    assert "assistant:cbstuffs:menu:otvars" in rendered_emitted
    # start.py: _cbstuffs_menu("apiset")
    assert "assistant:cbstuffs:menu:apiset" in rendered_emitted


def test_every_emitted_prefix_has_a_handler():
    missing = sorted(
        {e.render() for e in EMITTED if not any(_compatible(h, e) for h in HANDLED)}
    )
    assert not missing, (
        "callback data emitted with no compatible @callback handler "
        f"(button would silently time out): {missing}"
    )


def test_every_handled_prefix_has_an_emitter():
    missing = sorted(
        {h.render() for h in HANDLED if not any(_compatible(h, e) for e in EMITTED)}
    )
    assert not missing, (
        "@callback handler registered for callback data nothing ever "
        f"emits (dead handler code): {missing}"
    )


def test_known_namespaces_are_present():
    """A handful of namespaces the maintainer's spec calls out by name —
    guards against this test accidentally scanning the wrong directory or
    a future rewrite silently dropping one of them.
    """
    expected = {
        "help:manager",
        "help:Admintools",
        "assistant:cbstuffs",
        "assistant:games",
        "assistant:initial",
        "assistant:lang",
        "assistant:start",
        "assistant:fl2lnk",
        "assistant:fdroid",
    }
    missing = expected - EMITTED_NAMESPACES - HANDLED_NAMESPACES
    assert not missing, f"expected namespaces not found by the scan at all: {missing}"


def test_cross_boundary_tokens_are_colon_migrated_and_balanced():
    """The six legacy cross-boundary tokens (``sndplug_``, ``uh_``, ``mngbtn``,
    ``open``, ``cc_``, ``initft_2``) are now fully migrated to the colon model.
    The old raw tokens must no longer appear as ``namespace:name`` prefixes,
    and every migrated prefix must have *both* an emitter and a handler in the
    (now expanded) assistant/ + plugins/ + pyUltroid scan. The pair-balance
    itself is enforced by ``test_every_emitted_prefix_has_a_handler`` /
    ``test_every_handled_prefix_has_an_emitter``; this test pins the names so a
    silent re-literalization can't slip through.
    """
    migrated = {
        "sndplug_": ("assistant:inline:sndplug",),
        "uh_": ("assistant:inline:uh",),
        "mngbtn": ("help:manager:mngbtn",),
        "open": ("assistant:inline:main",),
        "cc_": ("assistant:admins:cc",),
        "initft_2": (
            "assistant:initial",
        ),  # served by PAGE_PATTERN's next:/prev: handler
    }

    emitted_rendered = {e.render() for e in EMITTED}
    handled_rendered = {h.render() for h in HANDLED}
    for old_token, prefixes in migrated.items():
        assert old_token not in EMITTED_NAMESPACES, f"{old_token} still a live emitter"
        assert old_token not in HANDLED_NAMESPACES, f"{old_token} still a live handler"
        for prefix in prefixes:
            assert any(
                r.startswith(prefix) for r in emitted_rendered
            ), f"{prefix!r} has no emitter in the scanned tree"
            assert any(
                r.startswith(prefix) for r in handled_rendered
            ), f"{prefix!r} has no handler in the scanned tree"

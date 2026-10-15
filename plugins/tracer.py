"""
# Commands:
->    .find <query>          — find handler/function by name or pattern
->  .src <plugin> <func>   — extract live source of a function
->  .srcof <pattern>       — find handler + extract its source (e.g. .srcof play)
->  .whocalls <func>       — find all callers of a function
->  .dbkey <KEY>           — find all functions touching a DB key
->  .hmap                  — show full handler map (pattern → plugin)
->  .conflicts             — show handler pattern conflicts
->  .traces [n]            — show last n execution traces
->  .tfail [plugin]        — show recent failures (optionally for one plugin)
->  .tslow                 — show slowest handlers from trace history
->  .tuser <user_id>       — show trace history for a user
->  .tstats                — trace statistics (success rate, top plugins, etc.)
->  .idxstats              — code index statistics
->  .reindex               — rebuild the plugin index from scratch
"""


import asyncio
import time
from io import BytesIO

from . import ultroid_cmd, eod, eor
from pyUltroid.tracer import tr

# ── Formatting helpers ─────────────────────────────────────────────────────


def _fmt_handler(h) -> str:
    flags = []
    if h.owner_only:
        flags.append("owner")
    if h.fullsudo:
        flags.append("fullsudo")
    if h.only_devs:
        flags.append("devs")
    flag_str = f" [{', '.join(flags)}]" if flags else ""
    doc = f"\n  _{h.docstring[:80]}_" if h.docstring else ""
    return (
        f"• `{h.plugin}.{h.func_name}` (line {h.line}){flag_str}\n"
        f"  pattern: `{h.pattern}`{doc}"
    )


def _fmt_function(fn) -> str:
    async_tag = "async " if fn.is_async else ""
    db = f"\n  DB: `{', '.join(fn.db_keys)}`" if fn.db_keys else ""
    calls = f"\n  calls: `{', '.join(fn.calls[:5])}`" if fn.calls else ""
    return f"• `{fn.plugin}.{async_tag}{fn.name}()` (line {fn.line})" f"{db}{calls}"


def _fmt_trace(t) -> str:
    status = "✅" if t.success else f"❌ `{t.exc_type}`"
    elapsed = f"{t.duration_ms:.0f}ms"
    db_str = (
        f" | DB: `{', '.join((t.db_reads + t.db_writes)[:3])}`"
        if (t.db_reads or t.db_writes)
        else ""
    )
    return (
        f"{status} `{t.plugin}.{t.handler}` — {elapsed}"
        f"\n  user=`{t.user_id}` chat=`{t.chat_id}`{db_str}"
    )


async def _send_as_file(event, content: str, filename: str = "result.txt") -> None:
    buf = BytesIO(content.encode("utf-8"))
    buf.name = filename
    await event.client.send_file(
        event.chat_id,
        buf,
        caption=f"`{filename}`",
        reply_to=event.id,
    )


# ── .find — handler / function search ─────────────────────────────────────


@ultroid_cmd(pattern=r"find(?: |$)([\s\S]*)", owner_only=True)
async def find_cmd(event):
    """
    .find <query>
    Search for a handler or function by name/pattern.
    """
    query = event.pattern_match.group(1).strip()
    if not query:
        return await eod(event, "`Usage: .find <pattern or function name>`")

    msg = await eor(event, "`🔍 searching…`")
    result = tr.search.find(query)

    if result.is_empty():
        return await msg.edit(f"Nothing found for `{query}`.")

    header = f"**🔍 {result.kind.title()} results for `{query}` ({result.count})**\n\n"

    if result.kind == "handler":
        lines = [_fmt_handler(h) for h in result.results[:15]]
    else:
        lines = [_fmt_function(fn) for fn in result.results[:15]]

    text = header + "\n".join(lines)
    if result.count > 15:
        text += f"\n\n_…and {result.count - 15} more_"

    if len(text) > 4000:
        await msg.delete()
        await _send_as_file(event, text, "search_results.txt")
    else:
        await msg.edit(text)


# ── .src — extract live source ────────────────────────────────────────────


@ultroid_cmd(pattern=r"src(?: |$)(\w+)\s+(\w+)", owner_only=True)
async def src_cmd(event):
    """
    .src <plugin> <function>
    Extract the live source code of a specific function.
    """
    plugin = event.pattern_match.group(1).strip()
    func = event.pattern_match.group(2).strip()
    msg = await eor(event, "`🥚 extracting…`")
    result = tr.search.source(plugin, func)

    if result.is_empty():
        return await msg.edit(
            f"Could not extract `{func}` from `{plugin}`.\n"
            f"Check `.find {func}` to verify the plugin and function name."
        )

    code = result.results[0]
    text = f"**📄 `{plugin}.{func}`** (live)\n\n```python\n{code[:3500]}\n```"

    if len(code) > 3500:
        await msg.delete()
        await _send_as_file(event, code, f"{plugin}_{func}.py")
    else:
        await msg.edit(text)


# ── .srcof — find handler + extract source ───────────────────────────────


@ultroid_cmd(pattern=r"srcof(?: |$)([\w.]+)", owner_only=True)
async def srcof_cmd(event):
    """
    .srcof <pattern>
    Find the handler for a pattern and extract its source.
    Example: .srcof play  →  shows live source of the play handler.
    """
    pattern = event.pattern_match.group(1).strip().lstrip(".")
    msg = await eor(event, "`🥚 pulling yellow from egg…`")
    result = tr.search.source_by_handler(pattern)

    if result.is_empty():
        return await msg.edit(f"No handler found matching `{pattern}`.")

    h, code = result.results[0]
    header = (
        f"**📄 Handler: `.{h.pattern}`**\n"
        f"`{h.plugin}.{h.func_name}` — line {h.line}\n\n"
    )
    text = header + f"```python\n{code[:3000]}\n```"

    if len(code) > 3000:
        await msg.delete()
        full = header + code
        await _send_as_file(event, full, f"{h.plugin}_{h.func_name}.py")
    else:
        await msg.edit(text)


# ── .whocalls — find all callers ──────────────────────────────────────────


@ultroid_cmd(pattern=r"whocalls(?: |$)([\w_]+)", owner_only=True)
async def whocalls_cmd(event):
    """
    .whocalls <function_name>
    Find every plugin and function that calls this function.
    """
    func = event.pattern_match.group(1).strip()
    msg = await eor(event, f"`🔍 tracing callers of {func}…`")
    callers = tr.search.who_calls(func)

    if not callers:
        return await msg.edit(f"No callers found for `{func}`.")

    lines = [f"• `{plugin}.{caller}`" for plugin, caller in callers]
    text = f"**🔗 Callers of `{func}` ({len(callers)})**\n\n" + "\n".join(lines)
    await msg.edit(text)


# ── .dbkey — find DB key usage ────────────────────────────────────────────


@ultroid_cmd(pattern=r"dbkey(?: |$)([\w_]+)", owner_only=True)
async def dbkey_cmd(event):
    """
    .dbkey <KEY>
    Find all functions that read/write a DB key.
    """
    key = event.pattern_match.group(1).strip()
    msg = await eor(event, f"`🔍 searching for {key}…`")
    result = tr.search.find_db_key(key)

    if result.is_empty():
        return await msg.edit(f"No functions found accessing key `{key}`.")

    lines = [_fmt_function(fn) for fn in result.results[:20]]
    text = f"**🗄 Functions using `{key}` ({result.count})**\n\n" + "\n".join(lines)
    await msg.edit(text)


# ── .hmap — handler map ───────────────────────────────────────────────────


@ultroid_cmd(pattern=r"hmap$", owner_only=True)
async def hmap_cmd(event):
    """
    .hmap
    Show full map of all registered handlers (pattern → plugin).
    """
    msg = await eor(event, "`📋 building handler map…`")
    hmap = tr.search.handler_map()

    if not hmap:
        return await msg.edit("No handlers indexed yet. Try `.reindex`.")

    lines = sorted(f"`{pat}` → `{plugin}`" for pat, plugin in hmap.items())
    text = f"**📋 Handler Map ({len(hmap)} patterns)**\n\n" + "\n".join(lines)

    if len(text) > 4000:
        await msg.delete()
        raw = "\n".join(f"{p}\t{plugin}" for p, plugin in sorted(hmap.items()))
        await _send_as_file(event, raw, "handler_map.txt")
    else:
        await msg.edit(text)


# ── .conflicts — conflict detection ──────────────────────────────────────


@ultroid_cmd(pattern=r"conflicts$", owner_only=True)
async def conflicts_cmd(event):
    """
    .conflicts
    Show all handler pattern conflicts (two plugins on the same pattern).
    """
    msg = await eor(event, "`🔍 checking conflicts…`")
    result = tr.search.conflicts()

    if result.is_empty():
        return await msg.edit("✅ No handler conflicts detected.")

    lines = [f"⚠️ `{pat}`\n  `{a}` ←→ `{b}`" for pat, a, b in result.results]
    await msg.edit(
        f"**⚠️ Handler Conflicts ({result.count})**\n\n" + "\n\n".join(lines)
    )


# ── .traces — recent trace history ────────────────────────────────────────


@ultroid_cmd(pattern=r"traces(?: |$)(\d*)", owner_only=True)
async def traces_cmd(event):
    """
    .traces [n]
    Show last n execution traces (default 10).
    """
    n = int(event.pattern_match.group(1) or 10)
    n = max(1, min(n, 50))
    msg = await eor(event, "`📊 fetching traces…`")
    recent = await tr.live.recent(n)

    if not recent:
        return await msg.edit("No traces recorded yet.")

    lines = [f"{i}. {_fmt_trace(t)}" for i, t in enumerate(recent, 1)]
    text = f"**📊 Last {len(recent)} Traces**\n\n" + "\n\n".join(lines)

    if len(text) > 4000:
        await msg.delete()
        await _send_as_file(event, text, "traces.txt")
    else:
        await msg.edit(text)


# ── .tfail — failure traces ────────────────────────────────────────────────


@ultroid_cmd(pattern=r"tfail(?: |$)([\w_]*)", owner_only=True)
async def tfail_cmd(event):
    """
    .tfail [plugin]
    Show recent failures, optionally filtered by plugin name.
    """
    plugin = event.pattern_match.group(1).strip() or None
    msg = await eor(event, "`📊 fetching failures…`")
    fails = await tr.live.failures(plugin=plugin, n=15)

    if not fails:
        label = f"plugin `{plugin}`" if plugin else "any plugin"
        return await msg.edit(f"No failures recorded for {label}.")

    lines = []
    for t in fails:
        exc_preview = (t.exception or "")[:200].replace("\n", " ")
        lines.append(
            f"❌ `{t.plugin}.{t.handler}`\n"
            f"  `{t.exc_type}`: `{exc_preview}`\n"
            f"  user=`{t.user_id}` | `{t.duration_ms:.0f}ms`"
        )

    text = f"**❌ Failures ({len(fails)})**\n\n" + "\n\n".join(lines)
    if len(text) > 4000:
        await msg.delete()
        await _send_as_file(event, text, "failures.txt")
    else:
        await msg.edit(text)


# ── .tslow — slow traces ──────────────────────────────────────────────────


@ultroid_cmd(pattern=r"tslow$", owner_only=True)
async def tslow_cmd(event):
    """
    .tslow
    Show the slowest handler executions from trace history.
    """
    msg = await eor(event, "`⏱ fetching slow traces…`")
    slow = await tr.live.slow(threshold_ms=500, n=15)

    if not slow:
        return await msg.edit("No slow traces (>500ms) recorded.")

    lines = [
        f"⏱ `{t.plugin}.{t.handler}` — **{t.duration_ms:.0f}ms**\n"
        f"  user=`{t.user_id}` | success=`{t.success}`"
        for t in slow
    ]
    await msg.edit(f"**⏱ Slow Traces ({len(slow)})**\n\n" + "\n\n".join(lines))


# ── .tuser — user trace history ───────────────────────────────────────────


@ultroid_cmd(pattern=r"tuser(?: |$)(\d+)", owner_only=True)
async def tuser_cmd(event):
    """
    .tuser <user_id>
    Show trace history for a specific user.
    """
    uid = int(event.pattern_match.group(1))
    msg = await eor(event, f"`📊 fetching traces for {uid}…`")
    hist = await tr.live.by_user(uid, n=15)

    if not hist:
        return await msg.edit(f"No traces for user `{uid}`.")

    lines = [f"{i}. {_fmt_trace(t)}" for i, t in enumerate(hist, 1)]
    await msg.edit(f"**📊 Traces for `{uid}` ({len(hist)})**\n\n" + "\n\n".join(lines))


# ── .tstats — trace statistics ────────────────────────────────────────────


@ultroid_cmd(pattern=r"tstats$", owner_only=True)
async def tstats_cmd(event):
    """
    .tstats
    Show overall trace statistics.
    """
    msg = await eor(event, "`📊 computing stats…`")
    stats = await tr.live.stats()

    top = "\n".join(f"  • `{p}`: {c}" for p, c in stats.get("top_plugins", []))
    exc = "\n".join(f"  • `{t}`: {c}" for t, c in stats.get("exc_types", {}).items())

    text = (
        f"**📊 Trace Statistics**\n\n"
        f"Total traces:     `{stats.get('total', 0)}`\n"
        f"Failures:         `{stats.get('failures', 0)}`\n"
        f"Success rate:     `{stats.get('success_rate', 'N/A')}`\n"
        f"Avg duration:     `{stats.get('avg_duration_ms', 'N/A')}ms`\n\n"
        f"**Top plugins:**\n{top or '  (none)'}\n\n"
        f"**Exception types:**\n{exc or '  (none)'}"
    )
    await msg.edit(text)


# ── .idxstats — index statistics ──────────────────────────────────────────


@ultroid_cmd(pattern=r"idxstats$", owner_only=True)
async def idxstats_cmd(event):
    """
    .idxstats
    Show the code index statistics.
    """
    stats = tr.search.index_stats()
    errors = stats.get("errors", [])
    err_str = (
        "\n⚠️ Parse errors: " + ", ".join(f"`{e}`" for e in errors) if errors else ""
    )
    text = (
        f"**📋 Code Index**\n\n"
        f"Plugins:   `{stats.get('plugins', 0)}`\n"
        f"Handlers:  `{stats.get('handlers', 0)}`\n"
        f"Functions: `{stats.get('functions', 0)}`\n"
        f"Imports:   `{stats.get('imports', 0)}`\n"
        f"Built:     `{stats.get('built_at', 0):.0f}`"
        f"{err_str}"
    )
    await eor(event, text)


# ── .reindex — rebuild index ──────────────────────────────────────────────


@ultroid_cmd(pattern=r"reindex$", owner_only=True)
async def reindex_cmd(event):
    """
    .reindex
    Rebuild the entire plugin index from scratch.
    Use after adding plugins without hot-reload.
    """
    msg = await eor(event, "`🔄 reindexing all plugins…`")
    t0 = time.time()
    count = await asyncio.get_event_loop().run_in_executor(None, tr.index.index_all)
    elapsed = (time.time() - t0) * 1000
    await msg.edit(
        f"✅ Indexed `{count}` plugins in `{elapsed:.0f}ms`.\n"
        f"Run `.idxstats` to see the breakdown."
    )

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from . import get_help

__doc__ = get_help("help_listeners")

from pyUltroid.startup.dispatcher import iter_dispatchers

from . import ultroid_cmd
from strings import get_string


def _listener_states():
    """All registered listeners across every client dispatcher, deduped by key."""
    seen, states = set(), []
    for dispatcher in iter_dispatchers():
        for state in dispatcher.listener_states():
            if state["key"] in seen:
                continue
            seen.add(state["key"])
            states.append(state)
    return states


def _invalid_listener_keys():
    """Listener groups registered under a non-class key, across all clients.

    A non-class key used to make ``_dispatch`` raise on every single update
    (``isinstance() arg 2 must be a type``). Listing them makes the plugin
    responsible for the bad ``listen=`` argument identifiable.
    """
    found, seen = [], set()
    for dispatcher in iter_dispatchers():
        for key in getattr(dispatcher, "_listeners", {}):
            if isinstance(key, type):
                continue
            label = f"{key!r} ({type(key).__name__})"
            if label not in seen:
                seen.add(label)
                found.append(label)
    return found


def _set_enabled(key: str, enabled: bool) -> None:
    # The toggle store is the shared udB; writing through every dispatcher
    # keeps any db-less in-memory fallbacks coherent too.
    for dispatcher in iter_dispatchers():
        dispatcher.set_listener_enabled(key, enabled)


@ultroid_cmd(pattern="listeners?( (.*)|$)", owner_only=True)
async def listener_controls(event):
    """Owner surface for E12: list raw-update listeners and persistently
    enable/disable them (state lives in udB under LISTENER_TOGGLES)."""
    args = (event.pattern_match.group(1) or "").strip()
    states = _listener_states()

    if not args:
        if not states:
            return await event.eor(get_string("lstnr_6"))
        lines = [get_string("lstnr_1").format(len(states))]
        for state in states:
            mark = "✅" if state["enabled"] else "❌"
            lines.append(f"{mark} `{state['key']}` ({state['type']})")
        # Surface any listener group registered under a non-class key. These
        # are dead weight that used to break dispatch for EVERY update, and
        # naming them here makes the offending plugin obvious.
        invalid = _invalid_listener_keys()
        if invalid:
            lines.append("")
            lines.append("⚠️ **Invalid listener types registered:**")
            for key in invalid:
                lines.append(f"  • `{key}`")
        return await event.eor("\n".join(lines))

    parts = args.split(maxsplit=1)
    action = parts[0].lower()
    if action not in ("enable", "disable") or len(parts) != 2:
        return await event.eor(get_string("lstnr_5"))

    target = parts[1].strip()
    matches = [s for s in states if s["key"] == target]
    if not matches:
        # convenience: match on the trailing qualname fragment
        matches = [
            s
            for s in states
            if s["key"].endswith(f".{target}") or s["key"].split(".")[-1] == target
        ]
    if not matches:
        return await event.eor(get_string("lstnr_4").format(target))

    key = matches[0]["key"]
    enabled = action == "enable"
    _set_enabled(key, enabled)
    await event.eor(get_string("lstnr_2" if enabled else "lstnr_3").format(key))

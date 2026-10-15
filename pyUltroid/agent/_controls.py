"""Per-chat agentic controls: tool use, tool rounds, temperature, model.

Stored as one database mapping so a chat's settings survive restarts, with safe
defaults that match the behaviour before this module existed. The userbot
commands in ``plugins/brain.py`` read these before every call, so a chat can
turn tools off, cap the loop, or pin a model without touching global config.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = (
    "CHAT_SETTINGS_KEY",
    "DEFAULT_CONTROLS",
    "dm_tools_enabled",
    "get_controls",
    "set_control",
    "reset_controls",
    "describe_controls",
)

CHAT_SETTINGS_KEY = "AGENT_CHAT_SETTINGS"

#: Matches the pre-controls behaviour exactly: tools available to ``.agent``,
#: the built-in round cap, the default sampling temperature, no model pin.
DEFAULT_CONTROLS: dict[str, Any] = {
    "tools": True,
    "rounds": None,  # None -> the agent's own MAX_TOOL_ROUNDS
    "temp": 0.7,
    "model": None,  # None -> the provider's configured default
}

_CONTROL_FIELDS = ("tools", "rounds", "temp", "model")


def _all_settings() -> dict[str, dict[str, Any]]:
    try:
        from pyUltroid import udB
    except Exception:
        return {}
    if udB is None:
        return {}
    try:
        stored = udB.get_key(CHAT_SETTINGS_KEY)
    except Exception:
        return {}
    if not isinstance(stored, dict):
        return {}
    return stored


def _save_settings(settings: dict[str, dict[str, Any]]) -> bool:
    try:
        from pyUltroid import udB
    except Exception:
        return False
    if udB is None:
        return False
    try:
        udB.set_key(CHAT_SETTINGS_KEY, settings)
        return True
    except Exception:
        return False


def get_controls(chat_id: Any) -> dict[str, Any]:
    """Effective controls for *chat_id*, defaults merged in."""
    controls = dict(DEFAULT_CONTROLS)
    stored = _all_settings().get(str(chat_id))
    if isinstance(stored, dict):
        for field in _CONTROL_FIELDS:
            if field in stored and stored[field] is not None:
                controls[field] = stored[field]
    return controls


def set_control(chat_id: Any, field: str, value: Any) -> bool:
    """Set one control for *chat_id*. ``value=None`` clears it to the default."""
    if field not in _CONTROL_FIELDS:
        raise ValueError(
            f"Unknown control {field!r}; expected one of: " + ", ".join(_CONTROL_FIELDS)
        )
    settings = _all_settings()
    chat = settings.get(str(chat_id))
    if not isinstance(chat, dict):
        chat = {}
    if value is None:
        chat.pop(field, None)
    else:
        chat[field] = value
    if chat:
        settings[str(chat_id)] = chat
    else:
        settings.pop(str(chat_id), None)
    return _save_settings(settings)


def reset_controls(chat_id: Any) -> bool:
    settings = _all_settings()
    if str(chat_id) in settings:
        settings.pop(str(chat_id), None)
        return _save_settings(settings)
    return True


def describe_controls(chat_id: Any) -> str:
    """One-line human summary, for ``.aiconf``."""
    controls = get_controls(chat_id)
    rounds = controls["rounds"] if controls["rounds"] is not None else "default"
    model = controls["model"] or "default"
    return (
        f"tools={'on' if controls['tools'] else 'off'} · "
        f"rounds={rounds} · temp={controls['temp']} · model={model}"
    )


def _owner_and_sudos():
    """The owner and sudo ids, or an empty set when they cannot be read.

    Fails closed: with no bot runtime (tests, import-time), the set is empty
    and DM tools stay off.
    """
    try:
        from pyUltroid._misc import owner_and_sudos

        return {int(u) for u in owner_and_sudos() if u is not None}
    except Exception:
        return set()


def dm_tools_enabled(user_id: Any, controls: dict[str, Any] | None = None) -> bool:
    """Whether *user_id* may run tools in a DM session.

    DM sessions are reachable by anyone who can message the bot, and the tool
    set includes shell execution and file access, so tools are restricted to
    the owner and sudos no matter what the chat controls say. This is a hard
    boundary, not a default: the controls alone can never grant it.
    """
    if controls is None:
        controls = get_controls(f"dm:{user_id}")
    if not controls.get("tools"):
        return False
    try:
        return int(user_id) in _owner_and_sudos()
    except (TypeError, ValueError):
        return False

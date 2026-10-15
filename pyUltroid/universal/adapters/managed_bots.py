"""Managed-bot commands with persistent metadata and secret-safe operations."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from telethon import functions, types

from ..runtime import RuntimeContext
from ..storage import UDBStore
from ..tl import managed_bot_deep_link, rotate_managed_bot_token


class ManagedBotStore:
    KEY = "UNIVERSAL_MANAGED_BOTS"

    def __init__(self, db: Any) -> None:
        self._store = UDBStore(db, self.KEY, {})

    def all(self) -> dict[str, dict[str, Any]]:
        return self._store.get()

    def record(self, bot_id: int, **metadata: Any) -> dict[str, Any]:
        bots = self.all()
        item = bots.setdefault(str(bot_id), {"bot_id": bot_id})
        item.update({key: value for key, value in metadata.items() if key != "token"})
        bots[str(bot_id)] = item
        self._store.set(bots)
        return deepcopy(item)


# Plain-text table
_TABLE_HEADER = ("ID", "Username", "Owner", "Token rotated")


def _bots_table_text(bots: dict[str, dict[str, Any]]) -> str:
    """Render tracked bots as a simple aligned text table via
    :func:`pyUltroid._core.richmsg.table_to_text` (the canonical renderer)."""
    from pyUltroid._core.richmsg import table_to_text

    rows = [_TABLE_HEADER]
    for bot_id, info in sorted(bots.items()):
        rows.append(
            (
                str(bot_id),
                f"@{info.get('username') or 'unknown'}",
                str(info.get("owner_id") or "-"),
                "yes" if info.get("token_rotated") else "no",
            )
        )
    return table_to_text(rows)


def _dashboard(store: ManagedBotStore, context: RuntimeContext) -> str:
    bots = store.all()
    lines = ["Managed bots (card view)", f"Tracked bots: {len(bots)}", ""]
    if not bots:
        lines.append("No managed bots recorded yet.")
    else:
        lines.append(_bots_table_text(bots))
    lines.append("")
    if context.capabilities.token_rotation:
        lines.append("Token rotation: available  -  .managedbot rotate BOT_ID confirm")
    else:
        lines.append(
            "Token rotation: unavailable on this Telethon build (no "
            "bots.ExportBotTokenRequest)  -  use the t.me/newbot deep "
            "link from .managedbot create instead."
        )
    feed = context.db.get_key("LOG_CHANNEL") if context.db is not None else None
    lines.append(
        "Live updates: mirrored to LOG_CHANNEL"
        if feed
        else "Live updates: none configured (set LOG_CHANNEL to mirror new/rotated bots here)"
    )
    lines += ["", ".managedbot create", ".managedbot rotate BOT_ID confirm"]
    return "\n".join(lines)


# Rich card mirror
async def _mirror_managed_bot_event(
    context: RuntimeContext, info: dict[str, Any]
) -> None:
    """Best-effort rich-card mirror of one ``UpdateManagedBot`` event."""
    db = context.db
    channel = db.get_key("LOG_CHANNEL") if db is not None else None
    if not channel:
        return
    try:  # the rich-card stack is optional in this adapter
        from pyUltroid._core.buttonkit import button, row
        from pyUltroid._core.richmsg import RichBlocks, send_rich
    except ImportError:
        return
    manager = getattr(getattr(context.assistant, "me", None), "username", None)
    blocks = (
        RichBlocks()
        .header("Managed bot updated")
        .table(
            [
                ["Field", "Value"],
                ["Bot ID", str(info.get("bot_id", ""))],
                ["Owner", str(info.get("owner_id") or "unknown")],
                ["Username changed", "yes" if info.get("username_changed") else "no"],
                ["Token rotated", "yes" if info.get("token_rotated") else "no"],
            ],
            caption="UpdateManagedBot",
        )
    )
    buttons = None
    if manager:
        buttons = row(
            button("Manage via t.me/newbot", url=managed_bot_deep_link(manager))
        )
    try:
        await send_rich(context.client, channel, blocks, buttons=buttons)
    except Exception:
        pass


class ManagedBotsPlugin:
    id = "universal.managed_bots"

    def register(self, context: RuntimeContext) -> None:
        store = ManagedBotStore(context.db)

        @context.dispatcher.command("managedbot", priority=20)
        async def managed_bot_command(update, args: str, reply):
            parts = args.strip().split()
            action = parts[0].lower() if parts else "status"
            if action in {"status", "list"}:
                return _dashboard(store, context)
            if action == "create":
                username = getattr(
                    getattr(context.assistant, "me", None), "username", None
                )
                if not username:
                    return (
                        "Managed-bot creation requires a configured assistant username."
                    )
                return f"Open Telegram's managed-bot flow: {managed_bot_deep_link(username)}"
            if action == "rotate":
                if len(parts) not in (2, 3) or not parts[1].isdigit():
                    return "Usage: .managedbot rotate BOT_ID [confirm]"
                if not context.capabilities.token_rotation:
                    return (
                        "Managed-bot token rotation is unavailable on this bot runtime."
                    )
                # Rotation is destructive and one-way: Telegram returns the
                # replacement token only in the rotation response, and this
                # command never displays or stores secrets. So the operator
                # must opt in explicitly and is told what they lose.
                if len(parts) != 3 or parts[2].lower() != "confirm":
                    return (
                        f"`.managedbot rotate {parts[1]}` invalidates the bot's "
                        "current token. Telegram returns the replacement only at "
                        "that moment and this command does not display secrets, "
                        "so the bot cannot connect again until you re-issue a "
                        "token from its own settings.\n"
                        f"Re-run `.managedbot rotate {parts[1]} confirm` to "
                        "proceed."
                    )
                rotated = await rotate_managed_bot_token(
                    context.assistant, int(parts[1]), functions
                )
                if not rotated:
                    return "This Telethon build does not support managed-bot token rotation."
                store.record(int(parts[1]), token_rotated=True)
                return (
                    "Token revoked. The previous token is now invalid and the "
                    "replacement was not displayed, so the bot stays offline "
                    "until you re-issue a token from its own settings."
                )
            return "Usage: .managedbot [status|list|create|rotate BOT_ID [confirm]]"

        if not context.capabilities.managed_bot_updates:
            return
        update_type = getattr(types, "UpdateManagedBot", None)
        if update_type is None:
            return

        @context.dispatcher.listen(update_type, priority=20)
        async def managed_bot_update(update):
            bot_id = getattr(update, "bot_id", None)
            if not isinstance(bot_id, int):
                return
            info = store.record(
                bot_id,
                owner_id=getattr(update, "user_id", None),
                created=True,
                username_changed=bool(getattr(update, "username_changed", False)),
                token_rotated=bool(getattr(update, "token_was_changed", False)),
            )
            await _mirror_managed_bot_event(context, info)

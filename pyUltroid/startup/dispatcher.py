"""Ultroid command/listener dispatcher.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

import bisect
import dataclasses
import logging
import typing

from telethon import TelegramClient
from telethon.events import Raw
from telethon.types import (
    MessageService,
    PeerUser,
    UpdateBotNewBusinessMessage,
    UpdateNewMessage,
)

logger = logging.getLogger(__name__)

TOGGLES_KEY = "LISTENER_TOGGLES"

# Raw update types that carry command text.
_COMMAND_UPDATE_TYPES = (UpdateBotNewBusinessMessage, UpdateNewMessage)


@dataclasses.dataclass
class CommandEntry:
    """One registered command handler."""

    name: str
    func: typing.Callable
    priority: int = 99
    filters: typing.Callable | None = None

    def __lt__(self, other: "CommandEntry") -> bool:
        return self.priority < other.priority

    def __eq__(self, other: object) -> bool:
        # need more context
        if not isinstance(other, CommandEntry):
            return NotImplemented
        return self.func is other.func


@dataclasses.dataclass
class ListenerEntry:
    """One registered update-type listener."""

    update_type: type
    func: typing.Callable
    priority: int = 99
    match: typing.Callable | None = None
    filters: typing.Callable | None = None
    once: bool = False
    name: str = ""

    def __lt__(self, other: "ListenerEntry") -> bool:
        return self.priority < other.priority

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ListenerEntry):
            return NotImplemented
        return self.func is other.func

    @property
    def key(self) -> str:
        """Restart-stable identity used for persisted enable/disable toggles.

        Explicit ``name`` wins; otherwise ``module.qualname`` of the callback
        (``id(func)`` would not survive a restart, so it is never used here).
        """
        if self.name:
            return self.name
        func = self.func
        module = getattr(func, "__module__", "") or ""
        qualname = getattr(func, "__qualname__", None) or getattr(
            func, "__name__", "listener"
        )
        return f"{module}.{qualname}" if module else qualname


class CommandDispatcher:
    def __init__(
        self, client: TelegramClient, prefix: str | tuple[str, ...] = "?"
    ) -> None:
        self._prefix = (prefix,) if isinstance(prefix, str) else tuple(prefix)
        self._client = client

        # command name  -> sorted list[CommandEntry]
        self._commands: dict[str, list[CommandEntry]] = {}

        # update type   -> sorted list[ListenerEntry]
        self._listeners: dict[type, list[ListenerEntry]] = {}

        # the single raw entrypoint is bound to the client exactly once
        # (one Raw handler per client); register/unregister only mutate the
        # dicts above  -  no handler churn, no snapshot diffing.
        self._bound = False

        # identity-set dedup: (id(func), update_type) pairs already registered
        self._registered_ids: set[tuple[int, type]] = set()

        # in-memory toggle fallback used only when the client has no udB
        self._local_toggles: dict[str, bool] = {}

    # ── lifecycle
    @property
    def _cmd_handler(self) -> typing.Callable:
        return self._dispatch

    def _bind_raw(self) -> None:
        """Attach the single Raw handler to the client exactly once."""
        if self._bound:
            return
        self._client.add_event_handler(self._dispatch, Raw())
        self._bound = True

    def start(self) -> None:
        self._bind_raw()
        logger.info("CommandDispatcher started (prefix=%r)", self._prefix)

    def stop(self) -> None:
        if self._bound:
            self._client.remove_event_handler(self._dispatch)
            self._bound = False

    # Former per-registration re-sync hooks. Handler registration is now
    # bind-once, so these only ensure the raw entrypoint is attached; they
    # are kept so out-of-tree callers do not break.
    def _sync_commands(self) -> None:
        self._bind_raw()

    def _sync_listener(self, update_type: type) -> None:
        self._bind_raw()

    # ── command registration
    def _command_key(self, name: str) -> str:
        """Normalize a command name: lowercase, strip at most ONE configured
        prefix (character-prefix, not character-set)  -  used by both
        registration and removal so the two can never diverge."""
        key = name.lower()
        for p in self._prefix:
            if p and key.startswith(p):
                return key[len(p) :]
        return key

    def command(
        self,
        name: str,
        *,
        priority: int = 99,
        filters: typing.Callable | None = None,
    ) -> typing.Callable:
        """Decorator: register a command handler."""

        def decorator(func: typing.Callable) -> typing.Callable:
            self.register_command(name, func, priority=priority, filters=filters)
            return func

        return decorator

    def register_command(
        self,
        name: str,
        func: typing.Callable,
        *,
        priority: int = 99,
        filters: typing.Callable | None = None,
    ) -> None:
        key = self._command_key(name)
        entries = self._commands.setdefault(key, [])
        if any(e.func is func for e in entries):
            return  # exact-duplicate guard: double import must not double-fire
        bisect.insort(
            entries,
            CommandEntry(name=key, func=func, priority=priority, filters=filters),
        )
        self._bind_raw()

    def unregister_command(self, name: str, func: typing.Callable) -> None:
        key = self._command_key(name)
        self._commands[key] = [
            e for e in self._commands.get(key, []) if e.func is not func
        ]
        if not self._commands[key]:
            del self._commands[key]

    # ── listener registration
    def listen(
        self,
        update_type: type,
        *,
        match: typing.Callable | None = None,
        priority: int = 99,
        filters: typing.Callable | None = None,
        once: bool = False,
        name: str = "",
    ) -> typing.Callable:
        """Decorator: register a raw-update listener.

        ``once=True`` auto-unregisters after the first invocation; ``name``
        pins the persisted-toggle key (defaults to ``module.qualname``).
        """

        def decorator(func: typing.Callable) -> typing.Callable:
            self.register_listener(
                update_type,
                func,
                match=match,
                priority=priority,
                filters=filters,
                once=once,
                name=name,
            )
            return func

        return decorator

    def register_listener(
        self,
        update_type: type,
        func: typing.Callable,
        *,
        match: typing.Callable | None = None,
        priority: int = 99,
        filters: typing.Callable | None = None,
        once: bool = False,
        name: str = "",
    ) -> None:
        # Reject anything that is not a class BEFORE it becomes a key in
        # ``_listeners``. A non-type key (a mistyped ``listen="UpdateX"``
        # string, a typing alias, None) survives registration and then blows
        # up on EVERY subsequent update in ``_dispatch`` with
        # "isinstance() arg 2 must be a type" - one bad registration
        # breaks the whole raw-update pipeline for the process lifetime.
        if not isinstance(update_type, type):
            raise TypeError(
                f"register_listener: update_type must be a class, got "
                f"{update_type!r} ({type(update_type).__name__})"
                + (f" for listener {name!r}" if name else "")
            )
        ident = (id(func), update_type)
        if ident in self._registered_ids:
            return  # identity-set dedup: same func object never registers twice
        self._registered_ids.add(ident)
        self._listeners.setdefault(update_type, [])
        bisect.insort(
            self._listeners[update_type],
            ListenerEntry(
                update_type=update_type,
                func=func,
                priority=priority,
                match=match,
                filters=filters,
                once=once,
                name=name,
            ),
        )
        self._bind_raw()

    def unregister_listener(self, update_type: type, func: typing.Callable) -> None:
        self._registered_ids.discard((id(func), update_type))
        self._listeners[update_type] = [
            e for e in self._listeners.get(update_type, []) if e.func is not func
        ]
        if not self._listeners[update_type]:
            del self._listeners[update_type]

    # ── persisted listener toggles
    def _db(self):
        return getattr(self._client, "udB", None)

    def _toggles(self) -> dict:
        db = self._db()
        if db is None:
            return dict(self._local_toggles)
        try:
            return dict(db.get_key(TOGGLES_KEY) or {})
        except Exception:
            logger.exception(
                "Failed to read %s; treating all listeners as enabled", TOGGLES_KEY
            )
            return {}

    def is_listener_enabled(self, key: str) -> bool:
        """Listeners default to enabled unless explicitly toggled off."""
        return bool(self._toggles().get(key, True))

    def set_listener_enabled(self, key: str, enabled: bool) -> None:
        enabled = bool(enabled)
        db = self._db()
        if db is None:
            self._local_toggles[key] = enabled
            return
        toggles = self._toggles()
        toggles[key] = enabled
        db.set_key(TOGGLES_KEY, toggles)

    def listener_states(self) -> list[dict]:
        """Snapshot of every registered listener with its toggle state  - 
        the data source for the owner command surface."""
        states = []
        for update_type, entries in self._listeners.items():
            for entry in entries:
                states.append(
                    {
                        "key": entry.key,
                        "type": getattr(update_type, "__name__", str(update_type)),
                        "enabled": self.is_listener_enabled(entry.key),
                        "once": entry.once,
                        "priority": entry.priority,
                    }
                )
        return states

    # ── dispatch
    async def _dispatch(self, update) -> None:
        """Single raw entrypoint: route command-bearing updates to the command
        router, then fan every update out to its type-matching listeners."""
        if self._commands and isinstance(update, _COMMAND_UPDATE_TYPES):
            await self._dispatch_command(update)

        if not self._listeners:
            return
        seen: set[int] = set()
        for update_type, entries in list(self._listeners.items()):
            # Defence in depth: a non-type key can only predate the guard
            # above, but if one ever lands here it must not abort dispatch
            # for every OTHER listener. Skip it, and drop it so the bad
            # registration cannot keep failing on each update.
            if not isinstance(update_type, type):
                logger.error(
                    "Dropping listener group with non-type key %r "
                    "(%s); it would raise on every update.",
                    update_type,
                    type(update_type).__name__,
                )
                self._listeners.pop(update_type, None)
                self._registered_ids = {
                    i for i in self._registered_ids if i[1] is not update_type
                }
                continue
            if not isinstance(update, update_type):
                continue
            for entry in list(entries):
                if id(entry) in seen:
                    continue
                seen.add(id(entry))
                await self._run_listener_entry(entry, update)

    async def _run_listener_entry(self, entry: ListenerEntry, update) -> None:
        if not self.is_listener_enabled(entry.key):
            return
        try:
            if entry.match is not None and not entry.match(update):
                return
            if entry.filters is not None and not await entry.filters(update):
                return
            if not hasattr(update, "_client"):
                update._client = self._client
            from pyUltroid._core.client import bind_origin

            with bind_origin(self._client):
                await entry.func(update)
        except Exception:
            logger.exception(
                "Error in listener %r for %s",
                getattr(entry.func, "__name__", entry.func),
                update.__class__.__name__,
            )
        if entry.once:
            # fired (or attempted) exactly once
            # ListenerManager semantics: match/filter misses do not consume
            # the once, an exception does not retain it.
            self.unregister_listener(entry.update_type, entry.func)

    async def _dispatch_command(self, update) -> None:
        msg = getattr(update, "message", None)
        if msg is None or isinstance(msg, MessageService):
            # skip service messages (join/pin/etc. actions) before any
            # owner checks or helper patching.
            return

        from_id = getattr(msg, "from_id", None)
        if not isinstance(from_id, PeerUser):
            return

        from pyUltroid._misc import owner_and_sudos

        if from_id.user_id not in owner_and_sudos():
            return

        # Ensure respond, reply and get_reply_message helper functions exist on the raw update
        if not hasattr(update, "respond"):

            async def _respond(*args, **kwargs):
                conn_id = getattr(update, "connection_id", None)
                if conn_id:
                    from pyUltroid.startup.business import business_connection_id

                    token = business_connection_id.set(conn_id)
                    try:
                        return await self._client.send_message(
                            msg.peer_id, *args, **kwargs
                        )
                    finally:
                        business_connection_id.reset(token)
                else:
                    return await self._client.send_message(msg.peer_id, *args, **kwargs)

            update.respond = _respond

        if not hasattr(update, "reply"):

            async def _reply(*args, **kwargs):
                kwargs["reply_to"] = msg.id
                conn_id = getattr(update, "connection_id", None)
                if conn_id:
                    from pyUltroid.startup.business import business_connection_id

                    token = business_connection_id.set(conn_id)
                    try:
                        return await self._client.send_message(
                            msg.peer_id, *args, **kwargs
                        )
                    finally:
                        business_connection_id.reset(token)
                else:
                    return await self._client.send_message(msg.peer_id, *args, **kwargs)

            update.reply = _reply

        if not hasattr(update, "get_reply_message"):

            async def _get_reply_message():
                if getattr(msg, "reply_to_msg_id", None):
                    return await self._client.get_messages(
                        msg.peer_id, ids=msg.reply_to_msg_id
                    )
                return None

            update.get_reply_message = _get_reply_message

        text: str = msg.message or ""
        prefix = self._prefix
        matched_prefix = next((p for p in prefix if text.startswith(p)), None)
        if matched_prefix is None:
            return

        parts = text.strip().split(maxsplit=1)
        cmd_name = parts[0][len(matched_prefix) :].lower()
        args = parts[1] if len(parts) > 1 else ""

        entries = self._commands.get(cmd_name)
        if not entries:
            return

        for entry in list(entries):
            try:
                if entry.filters is not None and not await entry.filters(update):
                    continue

                reply_msg = (
                    await update.get_reply_message()
                    if hasattr(update, "get_reply_message")
                    else None
                )
                from pyUltroid._core.client import bind_origin

                with bind_origin(self._client):
                    result = await entry.func(update, args, reply_msg)

                if result is None:
                    continue

                if isinstance(result, tuple):
                    text_out, buttons = result
                    await update.respond(text_out, buttons=buttons)
                else:
                    await update.respond(result)
                break

            except Exception as exc:
                logger.exception("Error in command %r%s", matched_prefix, cmd_name)
                await update.respond(
                    f"`{matched_prefix}{cmd_name}` → `{exc.__class__.__name__}: {exc}`"
                )
                break

    async def _dispatch_listener(
        self,
        update: object,
        entries: list[ListenerEntry] | None = None,
    ) -> None:
        """Back-compat shim: fan ``update`` out to listener entries.

        New code routes through :meth:`_dispatch`; this stays so any caller
        holding an old-style per-type entry list keeps working (the entries
        argument is optional and defaults to all live listeners)."""
        if entries is None:
            entries = [e for lst in self._listeners.values() for e in lst]
        for entry in list(entries):
            if isinstance(update, entry.update_type):
                await self._run_listener_entry(entry, update)


# Module-level helpers (shared by the ultroid_cmd `listen=` kwarg and the
# owner command surface). Function-level imports only: this module is imported
# during ``pyUltroid`` package init and must not create import cycles.
def iter_dispatchers() -> typing.Iterator[CommandDispatcher]:
    """Yield every live client's dispatcher exactly once"""
    try:
        from pyUltroid import master

        clients = list(getattr(master, "telegram_clients", ()))
    except Exception:
        from pyUltroid import asst, ultroid_bot

        clients = [ultroid_bot, asst]

    seen: set[int] = set()
    for client in clients:
        if client is None:
            continue
        dispatcher = getattr(client, "dispatcher", None)
        if dispatcher is not None and id(dispatcher) not in seen:
            seen.add(id(dispatcher))
            yield dispatcher


def register_listen(
    func: typing.Callable,
    *,
    client,
    listen,
    match: typing.Callable | None = None,
    priority: int = 99,
    once: bool = False,
    name: str = "",
) -> typing.Callable:
    """Implementation of the ``ultroid_cmd(..., listen=...)`` kwarg.

    Registers ``func`` as a raw-update listener for one update type or a
    list/tuple of them, preferring the client dispatcher (deduped, toggleable,
    unload-tracked).  When the dispatcher is not attached yet (very early
    import), falls back to a guarded ``events.Raw`` handler directly on the
    client, mirroring the same match/once semantics.
    """
    listen_types = tuple(listen) if isinstance(listen, (list, tuple)) else (listen,)

    # A plugin may pass a Telethon event-builder *instance*
    # (``events.NewMessage(incoming=True)``) rather than a TL update class.
    # The dispatcher's fan-out is isinstance-based, so a builder instance can
    # never be a valid key; it must be expanded into the TL update types it
    # subscribes to, with its own filter composed into the listener's match.
    from .listen_resolve import FilterPredicate, default_parser

    plugin_filter = (
        None if match is None else FilterPredicate(match, origin="listen_func")
    )
    specs = default_parser.resolve(listen, plugin_filter)

    dispatcher = getattr(client, "dispatcher", None)
    if dispatcher is not None:
        for spec in specs:
            for update_type in spec.update_types:
                dispatcher.register_listener(
                    update_type,
                    func,
                    match=spec.predicate,
                    priority=priority,
                    once=once,
                    name=name,
                )
        return func

    if client is None:
        return func

    from telethon import events

    # Flatten the resolved types for the Raw fallback path.
    resolved_types = tuple(
        t for spec in specs for t in spec.update_types
    )
    matches = [spec.predicate for spec in specs if spec.is_filtered]

    async def _listen_wrapp(update):
        if not isinstance(update, resolved_types):
            return
        # Every spec's own predicate must pass. `FilterPredicate` already
        # guards against a raising filter, and the plugin `match` has
        # already been composed into each spec's predicate above, so it is
        # deliberately not re-applied here.
        for predicate in matches:
            if not predicate(update):
                return
        # Same semantics as CommandDispatcher._run_listener_entry: a raising
        # listener is caught and logged (never propagated), and a `once`
        # listener is consumed by the attempt  -  match/type misses are not.
        try:
            await func(update)
        except Exception:
            logger.exception(
                "Error in listener %r for %s",
                getattr(func, "__name__", func),
                update.__class__.__name__,
            )
        if once:
            client.remove_event_handler(_listen_wrapp)

    client.add_event_handler(_listen_wrapp, events.Raw())
    return func

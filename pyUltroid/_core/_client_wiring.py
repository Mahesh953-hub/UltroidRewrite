"""Client wiring + dispatch installation for the runtime steps.

The Telethon-facing halves of ``compose_master`` / ``install_dispatch``,
extracted from the old procedural block: wire the client cross-references,
build the MasterClient router, then give every client its dispatcher and
update pipeline and start the universal runtime.
"""

from __future__ import annotations

from typing import Any

CREATIVE_EXPORTS = {
    "cards": (
        "generate_error_card",
        "generate_now_playing",
        "generate_queue_card",
        "generate_success_card",
    ),
    "events": (
        "InlineResultBuilder",
        "InlineResultCache",
        "build_menu_grid",
        "build_menu_results",
        "render_farewell",
        "render_welcome",
    ),
    "instant": (
        "InlineUnavailable",
        "InstantInline",
        "InstantInlineError",
        "InstantQuery",
        "InstantResult",
        "instant_inline",
    ),
}


def wire_clients(
    db: Any,
    asst: Any,
    ultroid_bot: Any,
    user_client: Any,
    app: Any,
    extra_bots: list,
    extra_users: list,
) -> None:
    """The cross-references every client carries (selfbot pattern).

    ``bot._client = user_client`` (the user client), ``user._bot = asst``
    (the bot), and every client sees the primary trio + the database.
    """
    all_clients = list(
        dict.fromkeys(
            filter(None, [ultroid_bot, asst] + extra_users + extra_bots)
        )
    )

    if asst:
        asst._client = user_client
        asst._bot = asst
        db.set_key("ASST_USERNAME", asst.me.username)

    for extra_user in extra_users:
        # Secondary user clients see the primary bot and are their own app.
        extra_user._bot = asst
        extra_user._client = extra_user

    for extra_bot in extra_bots:
        # Secondary bots also route back to the primary user client.
        extra_bot._client = user_client
        extra_bot._bot = asst

    for client in all_clients:
        client.bot = asst
        client.user = ultroid_bot
        client.app = app
        client._bot = asst
        client._client = getattr(client, "_client", user_client)
        client.udB = db
        client.asst = asst
        client.ultroid_bot = ultroid_bot


def build_master(
    asst: Any,
    user_client: Any,
    app: Any,
    db: Any,
    extra_bots: list,
    extra_users: list,
) -> Any:
    """The MasterClient router plus the per-client origin facade."""
    from .client import MasterClient, OriginMasterClient

    master = MasterClient(bot=asst, user=user_client, app=app, udB=db)
    for extra_user in extra_users:
        master.add_client("user", extra_user)
    for extra_bot in extra_bots:
        master.add_client("bot", extra_bot)

    for client in list(
        dict.fromkeys(
            filter(None, [master.user, asst] + extra_users + extra_bots)
        )
    ):
        client.master = OriginMasterClient(master, client)
    return master


def install_dispatch(
    union: Any,
    var: Any,
    master: Any,
    asst: Any,
    ultroid_bot: Any,
    extra_bots: list,
    extra_users: list,
) -> tuple[str, str, str]:
    """Version migration, handler prefixes, dispatchers, pipelines, runtime.

    Returns the ``(HNDLR, DUAL_HNDLR, SUDO_HNDLR)`` prefix triple.
    """
    from ..startup.dispatcher import CommandDispatcher
    from ..startup.funcs import _version_changes
    from .pipeline import install_pipeline

    db = union.db
    _version_changes(db)

    prefix = (
        db.get_key("HNDLR") or ".",
        db.get_key("DUAL_HNDLR") or "/",
        db.get_key("SUDO_HNDLR") or db.get_key("HNDLR") or ".",
    )

    for client in list(
        dict.fromkeys(
            filter(None, [ultroid_bot, asst] + extra_users + extra_bots)
        )
    ):
        client.asst = asst
        client.ultroid_bot = ultroid_bot
        client.udB = db
        client.HNDLR = prefix[0]
        client.DUAL_HNDLR = prefix[1]
        client.SUDO_HNDLR = prefix[2]
        client.dispatcher = CommandDispatcher(client, prefix=prefix[:2])
        client.dispatcher.start()
        # One ordered update pipeline per real client, installed in front of
        # Telethon's own dispatch.
        install_pipeline(client)

    # Prefer the assistant; use the primary client only when no assistant is
    # configured. Patches were applied before any pipeline existed; bind the
    # primary client now so their ``routes=`` specs install.
    primary = asst or ultroid_bot
    if primary is not None:
        from ..startup.patching import bind_client

        bind_client(primary)

    union._universal_lifecycle = None
    if primary:
        from ..universal import RuntimeSettings, install_universal_runtime

        union._universal_lifecycle = install_universal_runtime(
            client=primary,
            assistant=asst,
            user=ultroid_bot,
            app=union._voice,
            db=db,
            dispatcher=primary.dispatcher,
            settings=RuntimeSettings(
                app_version=var.APP_VERSION,
                device_model=var.DEVICE_MODEL,
            ),
        )
    return prefix

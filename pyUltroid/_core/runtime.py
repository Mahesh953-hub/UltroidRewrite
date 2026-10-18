"""The startup lifecycle, as methods on the core union.

One object, one flow: ``core.run()`` executes the typed steps in order and
binds every product to the union; ``core.stop()`` is the matching shutdown.
The steps read user configuration (decouple, the database, Telegram
credentials) so their imports resolve at call time - reading user config is
runtime work, not import work.

``expose()`` writes the module-level aliases onto ``pyUltroid`` at the end:
the plugin surface (handbook rule 1) stays byte-identical - ``udB``,
``ultroid_bot``, ``asst``, ``master``, the handler prefixes, the mode flags
and the creative-feature re-exports are all assigned there, exactly as the
old procedural block did.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any

LOGS = logging.getLogger("pyUltroid")


class RuntimeMixin:
    """The startup/shutdown lifecycle of the process, bound to the union."""

    __slots__ = ()

    # ── the ordered flow ─────────────────────────────────────────────────

    def run(self) -> None:
        """Execute the startup, top to bottom, binding everything to self."""
        self._boot_at = time.time()
        var = self.gate_web_setup()
        self.read_banner()
        self.require_plugins_tree()
        self.open_database()
        self.bind_union_state(var)
        modules_runtime = self.apply_dynamic_patches()
        bot_mode, dual_mode, user_mode = self.resolve_modes()
        asst, extra_bots = self.start_bot_clients(var)
        ultroid_bot, extra_users, user_client = self.start_user_clients(
            var, asst, bot_mode, dual_mode
        )
        self.start_voice(user_client)
        master = self.compose_master(
            asst, ultroid_bot, user_client, extra_bots, extra_users, modules_runtime
        )
        self.bind_master(master)
        self.install_dispatch(var, master, asst, ultroid_bot, extra_bots, extra_users)
        self.enable_inline_mode(asst, ultroid_bot, user_client, bot_mode)
        self.expose(
            var, asst, ultroid_bot, master, bot_mode, dual_mode, user_mode,
            modules_runtime,
        )

    # ── steps ────────────────────────────────────────────────────────────

    def gate_web_setup(self) -> Any:
        """Serve the token-gated web setup UI when mandatory config is absent.

        Must run before ``configs`` is imported: decouple raises
        ``UndefinedValueError`` when API_ID/SESSION/BOT_TOKEN/LOG_CHANNEL
        are unset, and the setup server writes them. No-op when the
        configuration is complete.
        """
        from ..webserver.bootstrap import BOOTSTRAP

        BOOTSTRAP.ensure()
        from ..configs import Var

        return Var

    def read_banner(self) -> None:
        """Configure logging, print the banner, surface the host flag.

        ``HOSTED_ON`` must be on the package before ``UltroidDB()`` reads it,
        and ``startup``'s logger replaces the shell logger.
        """
        import pyUltroid
        from ..startup import HOSTED_ON, LOGS as _startup_logs, configure_logging

        configure_logging()
        self._hosted_on = HOSTED_ON
        setattr(pyUltroid, "HOSTED_ON", HOSTED_ON)
        setattr(pyUltroid, "LOGS", _startup_logs)
        global LOGS
        LOGS = _startup_logs

    def require_plugins_tree(self) -> None:
        if not os.path.exists("./plugins"):
            LOGS.error("'plugins' folder not found! Make sure you are in the correct path.")
            sys.exit(1)

    def open_database(self) -> None:
        """Create the database, sync env vars, migrate legacy AI settings."""
        from ..startup._database import UltroidDB
        from ..startup.funcs import update_envs

        db = UltroidDB()
        self.bind_db(db)
        # The alias lands the moment the object exists - ``startup.funcs``
        # (imported below) does ``from .. import udB`` at module level and
        # everything downstream reads the package name.
        import pyUltroid

        setattr(pyUltroid, "udB", db)
        update_envs()

        # One-time migration of legacy AI environment variables into the
        # unified ``AGENT_*`` database namespace.
        try:
            from ..agent._config import migrate_env_config as _migrate_ai_env

            migrated = _migrate_ai_env()
            if migrated:
                LOGS.info(
                    "agent: migrated %d legacy AI setting(s) into AGENT_* keys",
                    len(migrated),
                )
        except Exception as exc:
            LOGS.debug("agent: environment migration skipped (%s)", exc)

        LOGS.info(f"Connecting to {db.name}...")
        if db.ping():
            LOGS.info(f"Connected to {db.name} Successfully!")

    def bind_union_state(self, var: Any) -> None:
        """Bind the config namespace and the helper/services unions."""
        from ..fns import fns
        from .._misc import misc

        self.bind_var(var)
        self.bind_fns(fns)
        self.bind_misc(misc)

    def apply_dynamic_patches(self) -> Any:
        """Bind + apply the dynamic patch registry and the built-in modules."""
        from ..startup.patching import apply_patches, bind_database

        bind_database(self.db)
        apply_patches()

        # Built-in modules carry additional Telethon class addons and raw
        # update handlers; bootstrap and attach are two halves of the same
        # startup and must share one Modules instance.
        try:
            from ..modules import Modules

            runtime = Modules()
            count = runtime.bootstrap()
            LOGS.info(f"Successfully applied {count} built-in module patches.")
            return runtime
        except ImportError as exc:
            LOGS.error(f"Built-in module patches unavailable: {exc}")
            return None

    def resolve_modes(self) -> tuple[Any, Any, Any]:
        """BOT_MODE / DUAL_MODE / USER_MODE from the database."""
        bot_mode = self.db.get_key("BOTMODE")
        dual_mode = self.db.get_key("DUAL_MODE")
        user_mode = self.db.get_key("USER_MODE")
        if user_mode:
            dual_mode = False
        return bot_mode, dual_mode, user_mode

    def start_bot_clients(self, var: Any) -> tuple[Any, list]:
        """The primary assistant bot plus any extra bot tokens."""
        from ..startup.BaseClient import UltroidClient

        bot_token = self.db.get_key("BOT_TOKEN") or var.BOT_TOKEN
        bot_tokens = [t for t in (bot_token, var.BOT_TOKEN2, var.BOT_TOKEN3) if t]

        asst = None
        extra_bots: list = []
        if bot_tokens:
            asst = UltroidClient("asst", bot_token=bot_tokens[0], udB=self.db)
            for idx, token in enumerate(bot_tokens[1:], start=2):
                try:
                    extra_bots.append(
                        UltroidClient(f"asst{idx}", bot_token=token, udB=self.db)
                    )
                    LOGS.info(f"Connected extra bot token #{idx}.")
                except Exception as exc:
                    LOGS.error(f"Extra bot token #{idx} failed to start: {exc}")
        else:
            LOGS.warning("No BOT_TOKEN found  -  assistant bot not started.")
        return asst, extra_bots

    def start_user_clients(
        self, var: Any, asst: Any, bot_mode: Any, dual_mode: Any
    ) -> tuple[Any, list, Any]:
        """The primary user client (the VC client) plus any extra sessions.

        Returns ``(ultroid_bot, extra_users, user_client)``. In BOT_MODE the
        bot IS the primary client: ``ultroid_bot`` is the bot and
        ``user_client`` is None.
        """
        from ..startup.BaseClient import PyTgCallsClient, UltroidClient
        from ..startup.connections import validate_session
        from ..startup.funcs import autobot

        user_sessions = [s for s in (var.SESSION, var.SESSION2, var.SESSION3) if s]
        extra_users: list = []

        if bot_mode:
            if dual_mode:
                self.db.del_key("DUAL_MODE")
            LOGS.info("BOT_MODE  -  user client disabled.")
            return asst, extra_users, None

        if not user_sessions:
            LOGS.error("No user SESSION found and BOT_MODE is not set.")
            sys.exit(1)

        ultroid_bot = PyTgCallsClient(
            validate_session(user_sessions[0], LOGS),
            udB=self.db,
            app_version=var.APP_VERSION,
            device_model=var.DEVICE_MODEL,
        )
        ultroid_bot.run_in_loop(autobot())
        for idx, session in enumerate(user_sessions[1:], start=2):
            try:
                extra_users.append(
                    UltroidClient(
                        f"ultroid{idx}",
                        validate_session(session, LOGS),
                        udB=self.db,
                        app_version=var.APP_VERSION,
                        device_model=var.DEVICE_MODEL,
                    )
                )
                LOGS.info(f"Connected extra user session #{idx}.")
            except Exception as exc:
                LOGS.error(f"Extra user session #{idx} failed to start: {exc}")
        return ultroid_bot, extra_users, ultroid_bot

    def start_voice(self, user_client: Any) -> None:
        """Wire + start PyTgCalls on the primary user client (no 2nd session)."""
        self._voice = None
        if user_client is None:
            LOGS.info("PyTgCalls app not available (BOT_MODE, no user client).")
            return
        try:
            from pytgcalls import PyTgCalls
        except ImportError:
            LOGS.warning("py-tgcalls is not installed  -  voice chat is disabled.")
            return
        try:
            self._voice = PyTgCalls(user_client)
            LOGS.info("PyTgCalls app wired to user client (no extra session needed).")
        except Exception as exc:
            LOGS.error(
                "PyTgCalls is incompatible with this UltroidClient build; "
                "voice chat is disabled: %s",
                exc,
            )
            return

        from ..startup._loops import get_event_loop

        async def _start():
            try:
                await self._voice.start()
                LOGS.info("PyTgCalls app started successfully.")
            except Exception as exc:
                LOGS.error(f"PyTgCalls failed to start: {exc}")

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_start())
        except RuntimeError:
            get_event_loop().run_until_complete(_start())

    def compose_master(
        self,
        asst: Any,
        ultroid_bot: Any,
        user_client: Any,
        extra_bots: list,
        extra_users: list,
        modules_runtime: Any,
    ) -> Any:
        """Wire every client together and build the MasterClient router."""
        from ._client_wiring import wire_clients, build_master

        app = self._voice
        wire_clients(self.db, asst, ultroid_bot, user_client, app, extra_bots, extra_users)
        master = build_master(asst, user_client, app, self.db, extra_bots, extra_users)

        # @events.register only records client-less handler metadata. Attach
        # every handler discovered during the module bootstrap to bot clients.
        if modules_runtime is not None:
            count = modules_runtime.attach(([asst] + extra_bots) if asst else [])
            LOGS.info(f"Successfully attached {count} built-in module handlers.")
        return master

    def install_dispatch(
        self,
        var: Any,
        master: Any,
        asst: Any,
        ultroid_bot: Any,
        extra_bots: list,
        extra_users: list,
    ) -> None:
        """Handler prefixes, per-client dispatchers, pipelines, the runtime."""
        from ._client_wiring import install_dispatch as wire_dispatch

        self._prefix = wire_dispatch(
            self, var, master, asst, ultroid_bot, extra_bots, extra_users
        )

    def enable_inline_mode(
        self, asst: Any, ultroid_bot: Any, user_client: Any, bot_mode: Any
    ) -> None:
        if asst and user_client and not bot_mode:
            try:
                from ..startup.funcs import enable_inline

                if not asst.me.bot_inline_placeholder:
                    ultroid_bot.run_in_loop(
                        enable_inline(ultroid_bot, asst.me.username)
                    )
            except Exception:
                pass

    # ── the alias surface (plugin contract) ─────────────────────────────

    def expose(
        self,
        var: Any,
        asst: Any,
        ultroid_bot: Any,
        master: Any,
        bot_mode: Any,
        dual_mode: Any,
        user_mode: Any,
        modules_runtime: Any,
    ) -> None:
        """Write the module aliases and the re-exports plugins consume."""
        import pyUltroid
        from ._client_wiring import CREATIVE_EXPORTS

        app = self._voice
        surface = {
            "start_time": self._boot_at,
            "_ult_cache": {},
            "_ignore_eval": [],
            "Var": var,
            "HOSTED_ON": self._hosted_on,
            "udB": self.db,
            "asst": asst,
            "ultroid_bot": ultroid_bot,
            "master": master,
            "app": app,
            "HNDLR": self._prefix[0],
            "DUAL_HNDLR": self._prefix[1],
            "SUDO_HNDLR": self._prefix[2],
            "BOT_MODE": bot_mode,
            "DUAL_MODE": dual_mode,
            "USER_MODE": user_mode,
            "modules_runtime": modules_runtime,
            "_universal_lifecycle": self._universal_lifecycle,
        }
        for name, value in surface.items():
            setattr(pyUltroid, name, value)

        # The creative-features re-exports (plugin surface, rule 1).
        import pyUltroid._core.cards as _cards
        import pyUltroid._core.events as _events
        import pyUltroid._core.instant as _instant

        setattr(pyUltroid, "inline_cache", self.inline_cache)
        for module, names in (
            (_cards, CREATIVE_EXPORTS["cards"]),
            (_events, CREATIVE_EXPORTS["events"]),
            (_instant, CREATIVE_EXPORTS["instant"]),
        ):
            for name in names:
                setattr(pyUltroid, name, getattr(module, name))

        # The re-point pass: modules imported during the steps captured the
        # placeholders; walk every already-imported module and rebind each
        # tracked name to the final object so early captures and late plugin
        # imports see one truth. Idempotent.
        repoint = (
            "udB", "asst", "ultroid_bot", "master", "app",
            "HNDLR", "DUAL_HNDLR", "SUDO_HNDLR",
            "DUAL_MODE", "USER_MODE", "BOT_MODE", "_ignore_eval",
        )
        final = {name: getattr(pyUltroid, name) for name in repoint}
        for mod_name, mod in list(sys.modules.items()):
            if mod is None or not mod_name.startswith(
                ("pyUltroid", "strings", "custom", "assistant", "vc", "plugins")
            ):
                continue
            for name in repoint:
                if name in vars(mod):
                    setattr(mod, name, final[name])

    # ── deployment (post-startup: talks to Telegram) ────────────────────

    def deploy(self) -> None:
        """The post-startup flow: IPC, updates, identity, plugins, greeting."""
        self.start_ipc()
        self.auto_update_on_restart()
        self.run_startup_stuff()
        self.finalize_identity()
        self.invite_assistant_to_log()
        self.run_autopilot()
        self.load_plugin_trees()
        self.customize_assistant()
        self.load_channel_plugins()
        self.announce_deploy()
        self.edit_restart_message()
        self.start_universal_plugins()
        self.scrub_secrets()
        self.log_deploy_complete()

    @property
    def _primary(self) -> Any:
        """The client that drives the loop: user first, bot in BOT_MODE."""
        return self.user or self.bot

    def start_ipc(self) -> None:
        """The IPC server for shadow-update processes (HSS/HSU, W1-B)."""
        primary = self._primary
        if primary is None:
            return
        try:
            from ..shell import ipc_server

            primary.run_in_loop(ipc_server.start())
        except Exception as exc:
            LOGS.warning(f"IPC server not started  -  shadow updates disabled: {exc}")

    def auto_update_on_restart(self) -> None:
        """Apply pending updates before serving (A9: the real engine hook)."""
        import os
        import sys

        if not (self.db.get_key("UPDATE_ON_RESTART") and os.path.exists(".git")):
            return
        primary = self._primary
        if primary is None:
            return

        async def _apply():
            from ..updater import up

            diff = await up.check()
            if not (diff and diff.has_changes):
                return False
            state = await up.apply(diff)
            return state.status == "done"

        try:
            if primary.run_in_loop(_apply()):
                LOGS.info("Auto-update applied  -  rebooting into new code…")
                os.execl(sys.executable, sys.executable, "-m", "pyUltroid")
        except Exception as exc:
            LOGS.exception(f"Auto-update on restart failed: {exc}")

    def run_startup_stuff(self) -> None:
        from ..startup.funcs import startup_stuff

        primary = self._primary
        if primary is not None:
            primary.run_in_loop(startup_stuff())

    def finalize_identity(self) -> None:
        """Scrub the phone number and latch the owner id."""
        primary = self._primary
        if primary is None or primary.me is None:
            return
        primary.me.phone = None
        if not primary.me.bot:
            self.db.set_key("OWNER_ID", primary.uid)

    async def _invite_assistant(self) -> None:
        log_channel_id = self.db.get_key("LOG_CHANNEL")
        primary = self._primary
        if not (log_channel_id and self.bot and primary and not primary.me.bot):
            return
        try:
            from telethon.errors import UserAlreadyParticipantError
            from telethon.tl.functions.channels import InviteToChannelRequest

            bot_entity = await primary.get_input_entity(self.bot.me.username)
            await primary(InviteToChannelRequest(log_channel_id, [bot_entity]))
            LOGS.info(
                f"Successfully invited assistant @{self.bot.me.username} to LOG_CHANNEL."
            )
        except UserAlreadyParticipantError:
            pass
        except Exception as exc:
            LOGS.warning(f"Failed to auto-invite assistant to LOG_CHANNEL: {exc}")

    def invite_assistant_to_log(self) -> None:
        primary = self._primary
        if primary is not None:
            primary.run_in_loop(self._invite_assistant())

    def run_autopilot(self) -> None:
        LOGS.info("Initialising...")
        from ..startup.funcs import autopilot

        primary = self._primary
        if primary is not None:
            primary.run_in_loop(autopilot())

    def load_plugin_trees(self) -> None:
        """The full plugin load: official, assistant, addons, manager."""
        from ..startup.loader import load_other_plugins

        hosted = self._hosted_on
        addons = self.db.get_key("ADDONS") or self.var.ADDONS
        pmbot = self.db.get_key("PMBOT")
        manager = self.db.get_key("MANAGER")
        if hosted == "okteto":
            # okteto can't host the VC stack; the loader reads VCBOT itself
            self.db.set_key("VCBOT", False)
        if (hosted == "termux" or self.db.get_key("LITE_DEPLOY")) and (
            self.db.get_key("EXCLUDE_OFFICIAL") is None
        ):
            self.db.set_key(
                "EXCLUDE_OFFICIAL",
                "autocorrect autopic audiotools compressor forcesubscribe "
                "fedutils gdrive glitch instagram nsfwfilter nightmode "
                "pdftools profanityfilter writer youtube",
            )
        load_other_plugins(addons=addons, pmbot=pmbot, manager=manager)

    def customize_assistant(self) -> None:
        from ..startup.funcs import customize

        primary = self._primary
        if primary is not None:
            primary.run_in_loop(customize())

    def load_channel_plugins(self) -> None:
        channels = self.db.get_key("PLUGIN_CHANNEL")
        if not channels:
            return
        from ..startup.funcs import plug

        primary = self._primary
        if primary is not None:
            primary.run_in_loop(plug(channels))

    def announce_deploy(self) -> None:
        """The deploy message (skipped when LOG_OFF is set)."""
        if self.db.get_key("LOG_OFF"):
            return
        from ..startup.funcs import ready

        primary = self._primary
        if primary is not None:
            primary.run_in_loop(ready())

    def edit_restart_message(self) -> None:
        from ..startup.funcs import WasItRestart

        primary = self._primary
        if primary is not None:
            primary.run_in_loop(WasItRestart(self.db))

    def start_universal_plugins(self) -> None:
        """Start the explicit universal hooks once all trees are loaded."""
        carrier = self.bot or self.user
        hooks = getattr(carrier, "universal_plugins", None)
        primary = self._primary
        if hooks is not None and primary is not None:
            primary.run_in_loop(hooks.start())

    def scrub_secrets(self) -> None:
        """Scrub sensitive values from every reachable namespace."""
        from .._misc.keep_safe import cleanup_cache

        try:
            cleanup_cache()
        except Exception:
            pass

    def log_deploy_complete(self) -> None:
        LOGS.info(
            f"Took {self.fns.time_formatter((time.time() - self._boot_at) * 1000)}"
            " to start •ULTROID•"
        )
        LOGS.info(
            """
            ----------------------------------------------------------------------
                Ultroid has been deployed! Visit @TheUltroid for updates!!
            ----------------------------------------------------------------------
            """
        )

    # ── serve ─────────────────────────────────────────────────────────────

    def serve_forever(self) -> None:
        """Run every real Telegram client until they all stop serving.

        ``run_until_disconnected`` raises ``ConnectionError`` when the
        client is between connections - Android's network stack routinely
        aborts sockets (``[Errno 103]`` on termux) and Telethon's sender
        reconnects on its own, so a refused start is transient, not the
        end of the client. Each client is served through a retry wrapper;
        one client's flap must not cancel its siblings in the gather.
        """
        from ..startup._loops import ensure_running_loop

        runners = [c for c in self.clients if c is not None]
        if not runners:
            LOGS.error("No BOT_TOKEN and no user client  -  nothing to run.")
            import sys

            sys.exit(1)

        loop = ensure_running_loop()

        async def _serve_one(client) -> None:
            while True:
                try:
                    await client.run_until_disconnected()
                    return
                except ConnectionError:
                    # Mid-reconnect: give the sender's auto-reconnect loop
                    # a window, then resume serving. A revoked session ends
                    # the client through a normal return, not this path.
                    await asyncio.sleep(5)

        async def _run_all():
            await asyncio.gather(*(_serve_one(c) for c in runners))

        loop.run_until_complete(_run_all())

    # ── shutdown ────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Gracefully disconnect every active client (restart/shutdown)."""
        from ..startup._loops import ensure_running_loop

        loop = ensure_running_loop()

        async def _disconnect():
            try:
                lifecycle = self._universal_lifecycle
                if lifecycle:
                    try:
                        await lifecycle.stop()
                    except Exception:
                        pass
                for client in list(getattr(self.master, "telegram_clients", ())):
                    if client is None:
                        continue
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                    try:
                        http = getattr(client, "http", None)
                        if http is not None and not getattr(http, "closed", True):
                            await http.close()
                    except Exception:
                        pass
                if self.app:
                    try:
                        await self.app.stop()
                    except Exception:
                        pass
            except Exception:
                pass

        if loop.is_running():
            asyncio.ensure_future(_disconnect())
        else:
            try:
                loop.run_until_complete(_disconnect())
            except Exception:
                pass

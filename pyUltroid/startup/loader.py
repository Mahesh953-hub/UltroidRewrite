# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

import os
import subprocess
import sys
from shutil import rmtree

from decouple import config

from .. import LOGS, USER_MODE, asst, udB, ultroid_bot
from .._core.loader import (
    AsyncPluginLoader,
    register_errors_command,
    retry_persisted_errors,
)
from . import *
from .utils import load_addons

GIT = os.getenv("GIT_TOKEN")


def _async_load(loader, **kwargs):
    """Run an AsyncPluginLoader coroutine on the Ultroid client's loop."""
    coro = loader.load_all(**kwargs)
    try:
        return ultroid_bot.run_in_loop(coro)
    except Exception:
        # Fall back to asst's loop if the user client is unavailable.
        if asst is not None:
            return asst.run_in_loop(coro)
        raise


def load_other_plugins(
    addons=None,
    pmbot=None,
    manager=None,
):
    try:
        from ..extender import hre

        hre.bootstrap()
    except Exception as exc:
        LOGS.debug(f"[loader] hre.bootstrap failed: {exc}")

    # ── Official plugins
    _exclude = udB.get_key("EXCLUDE_OFFICIAL") or config("EXCLUDE_OFFICIAL", None)
    _exclude = _exclude.split() if _exclude else []

    # "INCLUDE_ONLY" was added to reduce Big List in "EXCLUDE_OFFICIAL" Plugin
    _in_only = udB.get_key("INCLUDE_ONLY") or config("INCLUDE_ONLY", None)
    _in_only = _in_only.split() if _in_only else []

    _async_load(
        AsyncPluginLoader(path="plugins", key="Official"),
        include=_in_only,
        exclude=_exclude,
    )

    # ── Assistant plugins
    if not USER_MODE and not udB.get_key("DISABLE_AST_PLUGINS"):
        _ast_exc = ["pmbot"]
        if _in_only and "games" not in _in_only:
            _ast_exc.append("games")

        _async_load(
            AsyncPluginLoader(path="assistant", key="Assistant"),
            log=False,
            exclude=_ast_exc,
        )

    # ── Addons
    # i prefered to keep addons sepratly, can keep on same repo tho
    if addons:
        os.environ["GIT_TERMINAL_PROMPT"] = "0"
        if url := udB.get_key("ADDONS_URL"):
            subprocess.run(f"git clone -q {url} addons", shell=True)
        if os.path.exists("addons") and not os.path.exists("addons/.git"):
            rmtree("addons")

        git_token = GIT if (GIT and GIT != "None") else None

        # Must change it
        if not os.path.exists("addons"):
            if git_token:
                subprocess.run(
                    f"git clone -b main https://{git_token}@github.com/Mahesh953-hub/MYADDONS addons",
                    shell=True,
                )
            else:
                subprocess.run(
                    "git clone -b main https://github.com/Mahesh953-hub/MYADDONS addons",
                    shell=True,
                )
        else:
            subprocess.run("cd addons && git pull -q && cd ..", shell=True)

        # Must change it
        if not os.path.exists("addons"):
            if git_token:
                subprocess.run(
                    f"git clone https://{git_token}@github.com/Mahesh953-hub/MYADDONS addons",
                    shell=True,
                )
            else:
                subprocess.run(
                    "git clone https://github.com/Mahesh953-hub/MYADDONS addons",
                    shell=True,
                )
        if os.path.exists("addons/addons.txt"):
            # generally addons req already there so it won't take much time
            # subprocess.run(
            #        "rm -rf /usr/local/lib/python3.*/site-packages/pip/_vendor/.wh*"
            #    )
            subprocess.run(
                f"{sys.executable} -m pip install --no-cache-dir -q --break-system-packages -r ./addons/addons.txt",
                shell=True,
            )

        _exclude = udB.get_key("EXCLUDE_ADDONS")
        _exclude = _exclude.split() if _exclude else []
        _in_only = udB.get_key("INCLUDE_ADDONS")
        _in_only = _in_only.split() if _in_only else []

        _async_load(
            AsyncPluginLoader(path="addons", key="Addons"),
            func=load_addons,
            include=_in_only,
            exclude=_exclude,
            load_all_files=True,
        )

    if not USER_MODE:
        # group manager
        if manager:
            _async_load(
                AsyncPluginLoader(path="assistant/manager", key="Group Manager"),
            )

        # chat via assistant
        if pmbot:
            _async_load(
                AsyncPluginLoader(path="assistant/pmbot.py", key="Assistant"),
                log=False,
            )

    # ── Fallback system: retry errored plugins (max 3 attempts this startup)
    try:
        ultroid_bot.run_in_loop(retry_persisted_errors(max_retries=3))
    except Exception as exc:
        LOGS.debug(f"[loader] retry_persisted_errors failed: {exc}")

    # ── Expose errored plugins via the .errors command ───────────────────
    try:
        register_errors_command()
    except Exception as exc:
        LOGS.debug(f"[loader] register_errors_command failed: {exc}")

    # ── Tracer code index
    try:
        from ..tracer import plugin_index

        indexed = plugin_index.index_all()
        LOGS.info(f"[loader] Plugin index warmed  -  {indexed} plugins indexed.")
    except Exception as exc:
        LOGS.debug(f"[loader] plugin index warm-up failed: {exc}")

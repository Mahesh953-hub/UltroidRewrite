# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

import os
import platform
import sys
from logging import INFO, WARNING, FileHandler, StreamHandler, basicConfig, getLogger

from ._extra import _ask_input

try:
    from ..configs import Var
except Exception:
    # configs reads user configuration via decouple; a bare library import
    # (no .env, no argv credentials) has nothing to read, so the surface
    # degrades to ``None`` instead of raising. The runtime imports configs
    # first (``gate_web_setup``), so a real run always has the real Var.
    Var = None


def where_hosted():
    if os.getenv("ON_NASA"):
        return "NASA 🚀"
    if os.getenv("DYNO"):
        return "heroku"
    if os.getenv("RAILWAY_STATIC_URL"):
        return "railway"
    if os.getenv("OKTETO_TOKEN"):
        return "okteto"
    if os.getenv("KUBERNETES_PORT"):
        return "qovery | kubernetes"
    if os.getenv("RUNNER_USER") or os.getenv("HOSTNAME"):
        if os.getenv("USER") == "codespace":
            return "codespace"
        return "github actions"
    if os.getenv("ANDROID_ROOT"):
        return "termux"
    if os.getenv("FLY_APP_NAME"):
        return "fly.io"

    return "local"


#: the host this deployment runs on (``where_hosted()`` at import time).
HOSTED_ON = where_hosted()

#: the process logger; ``configure_logging()`` installs its handlers.
LOGS = getLogger("pyUltLogs")


def configure_logging() -> str:
    """Install log handlers and print the deployment banner.

    Called by the core runtime (``read_banner``); a bare library import
    never touches logging configuration.
    """
    from telethon import __version__
    from telethon.tl.alltlobjects import LAYER

    from ..version import __version__ as __pyUltroid__
    from ..version import ultroid_version

    log_file = f"ultroid{sys.argv[6]}.log" if len(sys.argv) > 6 else "ultroid.log"

    if os.path.exists(log_file):
        os.remove(log_file)

    TelethonLogger = getLogger("Telethon")
    TelethonLogger.setLevel(WARNING)

    _ask_input()

    _LOG_FORMAT = "%(asctime)s,%(msecs)03d [ %(levelname).1s ] %(name)s: %(message)s"
    basicConfig(
        format=_LOG_FORMAT,
        level=INFO,
        datefmt="%b %-d | %-I:%M %p | %-S",
        handlers=[FileHandler(log_file), StreamHandler()],
    )
    try:
        import coloredlogs

        coloredlogs.install(level=None, logger=LOGS, fmt=_LOG_FORMAT)
    except ImportError:
        pass

    LOGS.info("""
                    -----------------------------------
                            Starting Deployment
                    -----------------------------------
    """)

    LOGS.info(f"Python version - {platform.python_version()}")
    LOGS.info(f"py-Ultroid Version - {__pyUltroid__}")
    LOGS.info(f"Telethon Version - {__version__} [Layer: {LAYER}]")
    LOGS.info(f"Ultroid Version - {ultroid_version} [{HOSTED_ON}]")
    return log_file

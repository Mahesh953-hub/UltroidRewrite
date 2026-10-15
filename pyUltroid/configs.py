# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

import sys

from .version import ultroid_version
from decouple import config

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _int_list(var_name: str):
    """Return a decouple cast that parses a comma-separated int list."""

    def _cast(value: str) -> list[int]:
        if not value or not value.strip():
            return []
        try:
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        except ValueError as exc:
            raise ValueError(
                f"{var_name} must be a comma-separated list of integer IDs; "
                f"got {value!r}: {exc}"
            ) from exc

    return _cast


class Var:
    # mandatory
    API_ID = int(sys.argv[1]) if len(sys.argv) > 1 else config("API_ID", cast=int)
    API_HASH = sys.argv[2] if len(sys.argv) > 2 else config("API_HASH", default="")
    SESSION = sys.argv[3] if len(sys.argv) > 3 else config("SESSION")
    SESSION2 = config("SESSION2", default=None)
    SESSION3 = config("SESSION3", default=None)
    REDIS_URI = (
        sys.argv[4]
        if len(sys.argv) > 4
        else (config("REDIS_URI", default=None) or config("REDIS_URL", default=None))
    )
    REDIS_PASSWORD = (
        sys.argv[5] if len(sys.argv) > 5 else config("REDIS_PASSWORD", default=None)
    )
    # extras
    BOT_TOKEN = config("BOT_TOKEN")
    BOT_TOKEN2 = config("BOT_TOKEN2", default=None)
    BOT_TOKEN3 = config("BOT_TOKEN3", default=None)
    LOG_CHANNEL = config("LOG_CHANNEL", cast=int)
    HEROKU_APP_NAME = config("HEROKU_APP_NAME", default=None)
    HEROKU_API = config("HEROKU_API", default=None)
    VC_SESSION = config("VC_SESSION", default=None)
    ADDONS = config("ADDONS", default=False, cast=bool)
    VCBOT = config("VCBOT", default=False, cast=bool)
    # Non-secret client identity metadata. These replace source-specific
    # hardcoded identity values without changing SarahUB authentication.
    APP_VERSION: str = config("APP_VERSION", default=ultroid_version)
    # Preserve SarahUB/Ultroid client identity unless explicitly overridden.
    DEVICE_MODEL: str = config("DEVICE_MODEL", default="Ultroid")
    # for railway
    REDISPASSWORD = config("REDISPASSWORD", default=None)
    REDISHOST = config("REDISHOST", default=None)
    REDISPORT = config("REDISPORT", default=None)
    REDISUSER = config("REDISUSER", default=None)
    # for sql
    DATABASE_URL = config("DATABASE_URL", default=None)
    # for MONGODB users
    # Security (F2): the credential-bearing default was removed.
    # Set MONGO_URI (or REDIS_URI / DATABASE_URL) explicitly in your
    # environment; there is no built-in database anymore.
    # Accept the common MongoDB env var spellings too, so a deployment that
    # sets MONGODB_URI / MONGODB_URL / MONGO_URL is not silently treated as
    # unconfigured and forced onto the local file DB.
    MONGO_URI = (
        config("MONGO_URI", default=None)
        or config("MONGODB_URI", default=None)
        or config("MONGODB_URL", default=None)
        or config("MONGO_URL", default=None)
    )

    # Hardcoded sudo/dev fallback IDs (A17) are now configurable. Safe default
    # is an empty list, so no upstream IDs are implicitly trusted.
    SUDO_SPECIAL_IDS = config(
        "SUDO_SPECIAL_IDS", default="", cast=_int_list("SUDO_SPECIAL_IDS")
    )
    DEVLIST_IDS = config("DEVLIST_IDS", default="", cast=_int_list("DEVLIST_IDS"))
    # Cloud LLM provider credentials. Other providers are read from the
    # environment directly; these three exist on the class because the agent's
    # one-time config migration copies them into the database by attribute
    # name (APIMIX_API_KEY / APIMIX_BASE_URL / APIMIX_MODEL -> AGENT_CLOUD_*).
    APIMIX_API_KEY = config("APIMIX_API_KEY", default=None)
    APIMIX_BASE_URL = config("APIMIX_BASE_URL", default="https://api.apmix.ai/v1")
    APIMIX_MODEL = config("APIMIX_MODEL", default=None)

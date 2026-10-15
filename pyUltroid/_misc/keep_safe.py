# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Scrub credentials out of the live process before something reads them.

The previous shape was four module-level functions over two mutable module
globals (``__env`` and ``_get_sys``). Two real consequences:

* the globals were written by one function and read by another, so nothing
  declared that they were a pair;
* ``bring_back_dot_env`` read ``__env`` but **nothing ever wrote it** - the
  stash was dead code, so the restore it advertised could never fire.

:class:`SecretScrubber` owns both pieces as instance state, which makes the
stash live and the pairing explicit. The module-level names stay because
``pyUltroid/__main__.py`` imports ``cleanup_cache`` and
``pyUltroid/fns/helper.py`` imports ``KEEP_SAFE``.
"""

import os
from typing import Any, ClassVar, Final

__all__ = ["KEEP_SAFE", "DISCARDED", "SecretScrubber", "cleanup_cache"]

#: keys whose *values* are credentials and must never survive in memory
DISCARDED: Final[tuple[str, ...]] = (
    "API_ID",
    "API_HASH",
    "SESSION",
    "VC_SESSION",
    "REDIS_PASSWORD",
    "REDISPASSWORD",
    "HEROKU_API",
    "BOT_TOKEN",
    "MONGO_URI",
    "DATABASE_URL",
)

ENV_FILE: Final[str] = ".env"
DEFAULT_MIN_INTERVAL: Final[int] = 7200
DEFAULT_MAX_INTERVAL: Final[int] = 28800


class SecretScrubber:
    """Blanks credentials in ``os.environ`` and on ``Var``, keeping a stash.

    The stash is what makes the scrub reversible: :meth:`restore_env` puts
    the ``.env`` text back if it was captured.
    """

    #: the keys this scrubber is responsible for
    keys: ClassVar[tuple[str, ...]] = DISCARDED

    def __init__(self, keys: tuple[str, ...] | None = None) -> None:
        self.keys = tuple(keys) if keys else self.keys
        self._env_text: str | None = None
        self._stash: dict[str, Any] = {}
        self.scrubbed = False

    # -- the .env stash ---------------------------------------------------

    @property
    def env_text(self) -> str | None:
        return self._env_text

    def capture_env(self, path: str = ENV_FILE) -> bool:
        """Read the ``.env`` text into the stash. Returns whether it existed."""
        try:
            with open(path, encoding="utf-8") as handle:
                self._env_text = handle.read()
        except OSError:
            self._env_text = None
            return False
        return True

    def restore_env(self, path: str = ENV_FILE) -> bool:
        """Write the stashed ``.env`` back. A no-op when nothing was captured."""
        if not self._env_text:
            return False
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self._env_text)
        except OSError:
            return False
        return True

    # -- the environment --------------------------------------------------

    def scrub_environ(self) -> list[str]:
        """Blank every environment variable matching a discarded key.

        Matching is substring, not equality, because real deployments carry
        suffixed variants (``BOT_TOKEN2``, ``REDIS_PASSWORD_BACKUP``).
        Returns the variable names that were blanked.
        """
        touched: list[str] = []
        for name in list(os.environ):
            for key in self.keys:
                if key in name:
                    os.environ[name] = ""
                    touched.append(name)
                    break
        return touched

    # -- the config object ------------------------------------------------

    def scrub_config(self, var: Any = None) -> dict[str, Any]:
        """Blank the same keys on ``Var`` after stashing their values.

        The stash is what the old ``_get_sys`` module dict was for; it is
        now per-instance, so two scrubbers cannot collide.
        """
        if var is None:
            try:
                from ..configs import Var
            except Exception:
                return {}
        else:
            Var = var
        if Var is None:
            return {}
        captured: dict[str, Any] = {}
        for key in self.keys:
            if key in getattr(Var, "__dict__", {}):
                captured[key] = getattr(Var, key)
                try:
                    setattr(Var, key, "")
                except Exception:
                    pass
        self._stash.update(captured)
        return captured

    @property
    def stash(self) -> dict[str, Any]:
        """The values blanked by :meth:`scrub_config`."""
        return self._stash

    # -- the whole job ----------------------------------------------------

    def run(self, var: Any = None) -> dict[str, Any]:
        """Capture, scrub the environment, scrub the config. Idempotent."""
        if not self.scrubbed:
            self.capture_env()
            self.scrubbed = True
        self.scrub_environ()
        return self.scrub_config(var)

    def exit(self, code: int = 0) -> None:
        """Restore the ``.env`` and hard-exit without running ``atexit``."""
        self.restore_env()
        print("Exiting System")
        os._exit(code)


class KEEP_SAFE:
    """Source fragments that must never appear in a loaded addon.

    A deny-list, deliberately a class attribute rather than a function: it is
    data, and ``pyUltroid/fns/helper.py`` reads it as ``KEEP_SAFE.All``.
    """

    @property
    def All(self):
        return [
            "_ignore_eval",
            "SESSION",
            "BOT_TOKEN",
            "VC_SESSION",
            "DeleteAccountRequest",
            "HEROKU_API",
            "base64",
            "bash",
            "call_back",
            "get_me(",
            'get_entity("me")',
            "get_entity('me')",
            "exec",
            "phone",
            "REDIS_PASSWORD",
            "load_addons",
            "load_other_plugins",
            "os.system",
            "subprocess",
            "await locals()",
            "aexec",
            ".session.save()",
            ".auth_key.key",
            "INSTA_PASSWORD",
            "INSTA_SET",
            "SUDOS",
            "FULLSUDO",
            "KEEP_SAFE",
            ".flushall",
            "_get_sys",
            ".env",
            "DEVLIST",
        ]


# -- module surface -------------------------------------------------------


def cleanup_cache(what_u_doing_here=None) -> dict[str, Any]:
    """Scrub credentials from the environment and from ``Var``.

    Kept as a function because ``pyUltroid/__main__.py`` calls it on the exit
    path; it now goes through the union rather than a module singleton.
    """
    from .services import misc

    return misc.scrubber.run()


def os_stuff() -> list[str]:
    """Blank the credential-bearing environment variables."""
    from .services import misc

    return misc.scrubber.scrub_environ()

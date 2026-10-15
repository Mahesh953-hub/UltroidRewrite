# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

import os
import sys
import socket
import time
import logging

from .version import __version__, ultroid_version  # noqa: F401


# ─────────────────────────────
# Config / DNS patch
# ─────────────────────────────
class ULTConfig:
    lang = "en"
    thumb = "resources/extras/ultroid.jpg"


# A13b: dnspython is an optional dependency (declared separately by W0-A).
# Only apply the forced-8.8.8.8 DNS patch when dnspython is importable;
# otherwise keep the system resolver. The patch behaviour itself is kept.
try:
    import dns.resolver
except ImportError:
    dns = None


def _resolve_host(hostname):
    resolver = dns.resolver.Resolver()
    resolver.nameservers = ["8.8.8.8", "8.8.4.4"]
    answers = resolver.resolve(hostname, "A")
    return answers[0].address


if dns is not None:
    _original_getaddrinfo = socket.getaddrinfo

    def _custom_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        try:
            ip = _resolve_host(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, proto, "", (ip, port))]
        except Exception:
            return _original_getaddrinfo(host, port, family, type, proto, flags)

    socket.getaddrinfo = _custom_getaddrinfo

# ─────────────────────────────
# The package shell
# ─────────────────────────────
# Importing ``pyUltroid`` boots NOTHING. The runtime is the core union's
# lifecycle (``pyUltroid._core.core.run()``); ``python -m pyUltroid`` starts
# it from ``__main__.py``. These names are the placeholder surface - the
# plugin contract - that ``core.run()`` fills with the real objects:
# a module-level ``from pyUltroid import X`` always works, before and after
# the runtime starts.

# Names that must always exist for submodules that do straight ``from . import
# X``; the runtime rebinds them to the real objects when it starts.
LOGS = logging.getLogger("pyUltroid")
udB = None
ultroid_bot = None
asst = None
_ignore_eval = []
HNDLR = "."
DUAL_HNDLR = "/"
SUDO_HNDLR = "."
# Mode flags: off in library mode (no bootstrap runs to turn them on); the
# bootstrap rebinds them from udB below.
BOT_MODE = None
DUAL_MODE = None
USER_MODE = None
master = None

_universal_lifecycle = None
app = None


def call_back():
    """Gracefully disconnect all active clients on restart or shutdown."""
    from .ultroid import core

    core.stop()

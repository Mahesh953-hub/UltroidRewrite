# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""The user-input bridge: decorators, sudo state, and the Module layer.

Two coexisting surfaces live here.

**The plugin decorators** (``_decorators``, ``_assistant``, ``_supporter``,
``_wrappers``) are the long-standing contract: they bind a plugin function
straight onto a Telethon client the moment it is defined. Plugin files rely
on that and it does not change.

**The Module layer** (``module``, ``listener``, ``methods``, ``registry``)
is the modern surface: a feature becomes a class whose handlers are
methods, discovered from the ``on_`` prefix, with a declared lifecycle. It
is opt-in, independent of :mod:`pyUltroid.universal`, and imported lazily
here so that touching ``pyUltroid._misc`` never forces the Module layer (or
``custom`` / ``fns``) to load.

The ``from .. import *`` below is deliberate and pre-existing: the decorator
modules bind package globals at decoration time, and changing that is a
separate piece of work. Nothing in the Module layer may add another one.
"""

try:
    from ..configs import Var
except Exception:
    Var = None

CMD_HELP = {}
# ----------------------------------------------#


class _SudoManager:
    def __init__(self):
        self.db = None
        self.owner = None
        self._owner_sudos = []

    def _init_db(self):
        if not self.db:
            from .. import udB

            self.db = udB
        return self.db

    def get_sudos(self):
        db = self._init_db()
        SUDOS = db.get_key("SUDOS")
        return SUDOS or []

    def get_specials(self):
        db = self._init_db()
        SPECIALS = db.get_key("SPECIALS")
        return SPECIALS or (list(Var.SUDO_SPECIAL_IDS) if Var else [])

    @property
    def should_allow_sudo(self):
        db = self._init_db()
        return db.get_key("SUDO")

    def owner_and_sudos(self):
        if not self.owner:
            db = self._init_db()
            self.owner = db.get_key("OWNER_ID")
        return [self.owner, *self.get_sudos()]

    @property
    def fullsudos(self):
        db = self._init_db()
        fsudos = db.get("FULLSUDO")
        if not self.owner:
            self.owner = db.get_key("OWNER_ID")
        if not fsudos:
            return [self.owner]
        fsudos = fsudos.split()
        fsudos.append(self.owner)
        return [int(_) for _ in fsudos]

    def is_sudo(self, id_):
        return bool(id_ in self.get_sudos())

    def is_special(self, id_):
        return bool(id_ in self.get_specials())


SUDO_M = _SudoManager()
owner_and_sudos = SUDO_M.owner_and_sudos
sudoers = SUDO_M.get_sudos
is_sudo = SUDO_M.is_sudo
is_special = SUDO_M.is_special
specials = SUDO_M.get_specials

# ------------------------------------------------ #


class HandlerRegistry:
    """Files a decorated handler into the loader's bookkeeping container.

    ``asst_cmd(..., load=LIST)`` and the ``ultroid_cmd`` family need to
    record *what* was registered and *with which options*, and the two
    container shapes differ: a plain list keeps the bare function, a dict
    keyed by file stem keeps ``(function, kwargs)`` pairs. That asymmetry is
    the whole job, so it lives in one class rather than a four-branch
    function.
    """

    __slots__ = ()

    @staticmethod
    def append(load, func, name, arggs):
        """List container: append the bare function."""
        if isinstance(load, list):
            return load.append(func)
        return None

    @staticmethod
    def update(load, func, name, arggs):
        """Dict container: append ``(func, kwargs)`` under *name*, or seed it."""
        if isinstance(load, dict):
            if load.get(name):
                return load[name].append((func, arggs))
            return load.update({name: [(func, arggs)]})
        return None

    @classmethod
    def record(cls, load, func, name, arggs):
        """File into whichever container *load* actually is."""
        if isinstance(load, list):
            return cls.append(load, func, name, arggs)
        if isinstance(load, dict):
            return cls.update(load, func, name, arggs)
        return None


#: the shared registry
REGISTRY = HandlerRegistry()


def append_or_update(load, func, name, arggs):
    return REGISTRY.record(load, func, name, arggs)


# -- the Module layer, imported eagerly -----------------------------------
#
# Bound at import time so ``pyUltroid._misc`` never needs a ``__getattr__``
# hook, while keeping the package's documented import contract: importing
# ``pyUltroid._misc`` never pulls ``custom``, ``strings``, ``fns`` or the
# root in behind it (that is what keeps ``pyUltroid.fns`` loadable in
# isolation - see tests/_fns_probe.py). Only the root-independent Module
# layer and the scrubber are part of the package surface; the plugin
# decorators (``_decorators``, ``_supporter``, ``_assistant``) stay
# reachable by their module paths, which is how plugins already import
# them (``from pyUltroid._misc._decorators import ultroid_cmd``).

from .services import Misc, misc
from .keep_safe import DISCARDED, KEEP_SAFE, SecretScrubber
from .listener import HandlerTag, Listener, handler, is_handler
from .methods import MissingVerb, ModuleMixin
from .module import BoundModule, Module, ModuleError, ModuleExists, ModuleMissing
from .registry import ModuleRegistry, register_module

__all__ = [
    "CMD_HELP",
    "HandlerRegistry",
    "REGISTRY",
    "SUDO_M",
    "append_or_update",
    "is_special",
    "is_sudo",
    "owner_and_sudos",
    "specials",
    "sudoers",
    # module
    "Module",
    "BoundModule",
    "ModuleError",
    "ModuleExists",
    "ModuleMissing",
    # listener
    "HandlerTag",
    "Listener",
    "handler",
    "is_handler",
    # methods
    "MissingVerb",
    "ModuleMixin",
    # registry
    "ModuleRegistry",
    "register_module",
    "Misc",
    "misc",
    # scrubber
    "SecretScrubber",
    "DISCARDED",
    "KEEP_SAFE",
]

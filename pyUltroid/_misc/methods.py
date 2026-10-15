# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""The bound-verb surface a :class:`~pyUltroid._misc.module.Module` gets.

Concept ported from ``re-caligo``'s ``Methods`` mixin (``Debug`` +
``Format`` + ``Telegram``), where a module instance gets
``self.respond()``, ``self.ikm()``, ``self.fmtmsg()``, ``self.fmtsec()``.
That mixin exists so the helper verbs are *instance-bound* - which is part
of why modules are classes at all rather than plain functions.

SarahUB already owns every one of those helpers as free functions (or, as
of the ``custom/`` class layer, as kit instances). So this mixin binds the
existing helpers instead of re-implementing them: **zero duplicated logic**,
only a lookup.

Design notes, and why it is not just a bag of ``def`` wrappers:

* **Resolution is late and dynamic.** Every verb is looked up through
  :meth:`verb` *at call time* by dotted path, so this module has **no
  top-level ``pyUltroid`` import** (handbook rule: no circular imports) and
  a monkeypatched helper is picked up without re-binding.
* **Each verb keeps its own arity**, so ``respond``/``eor`` and ``eod``
  (which forces a ``time``) are distinct entries rather than one signature
  with magic arguments.
* A verb may be **absent** (``fmtexc`` only works inside an ``except``
  block). :meth:`verb` raises a clear error naming the path instead of
  failing later with ``AttributeError: NoneType``.
"""

from __future__ import annotations

import importlib
from typing import Any, Callable, ClassVar, Final, NamedTuple

# Dotted path -> the public method name a module calls.
_VERBS: Final[tuple[tuple[str, str], ...]] = (
    # formatting (custom.FmtKit / the free-function aliases)
    ("custom.fmt", "fmtmsg"),
    ("custom.fmt", "fmtbar"),
    ("custom.fmt", "fmtbyte"),
    ("custom.fmt", "fmtsec"),
    ("custom.fmt", "prog"),
    ("custom.util", "usecs"),
    # buttons (custom.ButtonKit)
    ("custom.tg", "ikm"),
    ("custom.tg", "ids"),
    # debug (custom.DebugKit)
    ("custom.debug", "shell"),
    # framework helpers, resolved lazily on purpose
    ("pyUltroid.fns.helper", "time_formatter"),
)


class MissingVerb(LookupError):
    """A bound verb could not be resolved (missing optional dependency)."""


class _Verb(NamedTuple):
    """One resolved helper: where it lives and what to call it."""

    path: str
    name: str

    def load(self) -> Callable[..., Any]:
        module_name, _, attr = self.path.rpartition(".")
        if not module_name:
            raise MissingVerb(f"malformed verb path: {self.path!r}")
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # optional dep absent, or a cycle in flight
            raise MissingVerb(
                f"verb {self.name!r} needs {self.path!r}, which is unavailable "
                f"({exc.__class__.__name__}: {exc})"
            ) from exc
        target = getattr(module, attr, None)
        if target is None:
            raise MissingVerb(f"verb {self.name!r}: {self.path!r} has no {attr!r}")
        return target


class ModuleMixin:
    """Binds the framework's helper verbs onto a module instance.

    Intentionally *not* a base class of anything here: it is a mixin that
    adds one capability (``self.<verb>(...)``) and knows nothing about
    listener binding, lifecycle or the registry - those live in
    :mod:`~pyUltroid._misc.module` and
    :mod:`~pyUltroid._misc.registry`.
    """

    #: verb name -> dotted path; override in a subclass to add or drop verbs
    VERBS: ClassVar[dict[str, str]] = {name: path for path, name in _VERBS}

    def verb(self, name: str) -> Callable[..., Any]:
        """Resolve one verb by its public name.

        Raises :class:`MissingVerb` - never ``AttributeError`` - when the
        helper is unavailable, so a module fails with an actionable message.
        """
        path = self.VERBS.get(name)
        if path is None:
            raise MissingVerb(f"{type(self).__name__} has no verb {name!r}")
        return _Verb(path, name).load()

    def verbs(self) -> tuple[str, ...]:
        """The verb names this module can call, sorted."""
        return tuple(sorted(self.VERBS))

    # -- explicit conveniences, one line each, all late-bound ------------
    # These exist so the common verbs read well at the call site and show up
    # in ``dir()``; every one of them still routes through ``verb()``.

    def ikm(self, rows: Any) -> Any:
        """Build an inline keyboard from ``rows``."""
        return self.verb("ikm")(rows)

    def fmtmsg(self, head: str, data: Any = None, foot: str = "", msgs: str = "") -> str:
        """Render the framework's standard message block."""
        return self.verb("fmtmsg")(head, data, foot, msgs)

    def fmtsec(self, sec: Any, part: int = 3, human: bool = False) -> str:
        """Render a duration."""
        return self.verb("fmtsec")(sec, part, human)

    def time_formatter(self, milliseconds: float) -> str:
        """Render a millisecond duration the way the help output does."""
        return self.verb("time_formatter")(milliseconds)

    def ids(self, *args: Any, **kwargs: Any) -> Any:
        """Resolve user/chat ids."""
        return self.verb("ids")(*args, **kwargs)

    async def shell(self, cmd: str) -> str:
        """Run a shell command (debug helper)."""
        return await self.verb("shell")(cmd)

    # -- telegram verbs, bound on the module's client --------------------

    async def respond(self, event: Any, text: str = None, **kwargs: Any) -> Any:
        """Edit-or-reply on *event* (the framework's ``eor``)."""
        from pyUltroid.startup.patching import eor

        return await eor(event, text, **kwargs)

    async def eod(self, event: Any, text: str = None, **kwargs: Any) -> Any:
        """Edit-or-delete on *event*; ``time`` defaults to 8 seconds."""
        kwargs.setdefault("time", 8)
        return await self.respond(event, text, **kwargs)

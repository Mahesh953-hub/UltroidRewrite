"""The ``_misc`` -> ``fns`` -> ``fns.admins`` -> ``_misc`` cycle, loaded the
hard way.

The chain used to work only because something else had already imported
``fns``; the star import at the top of ``_misc/__init__.py`` made the whole
thing order-dependent. This loads ``pyUltroid._misc._assistant`` **first**
against framework stubs and asserts the chain still resolves - which is the
only way to know the cycle is really gone rather than merely hidden.
"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MISC = ROOT / "pyUltroid" / "_misc"
FNS = ROOT / "pyUltroid" / "fns"

__all__ = ["load_misc_first", "stub_environment"]


def stub_environment():
    """Install the framework stubs the real ``_misc``/``fns`` files need."""
    def stub(dotted, **attrs):
        if dotted in sys.modules:
            return sys.modules[dotted]
        module = types.ModuleType(dotted)
        module.__path__ = []
        for key, value in attrs.items():
            setattr(module, key, value)
        sys.modules[dotted] = module
        return module

    class NewMessage:
        def __init__(self, **kwargs):
            self.build = dict(kwargs)

    stub("telethon", Button=type("Button", (), {"inline": staticmethod(lambda *a, **k: None)}))
    stub("telethon.events", NewMessage=NewMessage, CallbackQuery=NewMessage,
         InlineQuery=NewMessage, MessageEdited=NewMessage)
    stub("telethon.errors", QueryIdInvalidError=type("Q", (Exception,), {}))
    stub("telethon.tl")
    stub("telethon.tl.types",
         InputWebDocument=object,
         DocumentAttributeAudio=type("DAA", (), {}),
         DocumentAttributeVideo=type("DAV", (), {}))
    stub("telethon.tl.custom", Message=type("Message", (), {}))
    stub("telethon.helpers", _maybe_await=_noop_sync)
    rpc = {name: type(name, (Exception,), {"seconds": 1}) for name in (
        "UserNotParticipantError", "ChatAdminRequiredError", "RPCError",
        "FloodWaitError", "MessageIdInvalidError", "AuthKeyDuplicatedError",
        "AuthBytesInvalidError", "MediaCaptionTooLongError")}
    stub("telethon.errors.rpcerrorlist", **rpc)
    stub("telethon.errors.common",
         AlreadyInConversationError=type("A", (Exception,), {}))
    stub("telethon.utils", get_display_name=lambda *a, **k: "S",
         get_peer_id=lambda *a, **k: 1)
    stub("telethon.tl.functions", messages=types.SimpleNamespace())

    root = stub(
        "pyUltroid",
        LOGS=logging.getLogger("cycle-probe"),
        asst=None,
        udB=types.SimpleNamespace(get_key=lambda k: None, get=lambda k: None),
        ultroid_bot=types.SimpleNamespace(full_name="S", uid=1, me=None),
        HNDLR=".", SUDO_HNDLR="!", DUAL_HNDLR="/", DUAL_MODE=False,
        USER_MODE=True, _ignore_eval=[], _ult_cache={},
    )
    stub("pyUltroid.configs",
         Var=types.SimpleNamespace(DEVLIST_IDS=[], SUDO_SPECIAL_IDS=[]))
    stub("pyUltroid.dB", DEVLIST=[])
    stub("pyUltroid.dB._core", LIST={}, LOADED={}, HELP={})
    stub("pyUltroid.exceptions",
         DependencyMissingError=type("D", (Exception,), {}))
    stub("strings", get_string=lambda k: k)
    stub("pyUltroid.startup")
    stub("pyUltroid.startup.patching",
         eor=lambda event, text=None, **k: _noop())
    fmt = stub("custom.fmt",
               fmtmsg=lambda head, data=None, foot="", msgs="": ("FMT", head, msgs))
    tg = stub("custom.tg", ikm=lambda rows: ("IKM", rows))
    custom = stub("custom")
    custom.fmt, custom.tg = fmt, tg
    custom.fmtmsg, custom.ikm = fmt.fmtmsg, tg.ikm
    return root


async def _noop():
    return None


def _noop_sync(value=None):
    return value


def load_misc_first():
    """Load ``_misc`` -> ``fns.admins`` -> ``_misc._assistant``, in that order.

    Returns ``(package, admins, assistant)``. Raises whatever the real files
    raise - the point is that they do not.
    """
    prefixes = ("pyUltroid", "custom", "telethon", "strings", "decouple")

    def touched() -> set[str]:
        return {
            name
            for name in sys.modules
            if any(
                name == prefix or name.startswith(prefix + ".")
                for prefix in prefixes
            )
        }

    # Snapshot *everything* the stubs will touch. Restoring only pyUltroid
    # leaks a stub telethon module into every later test, which then fails
    # with a confusing "cannot import name TelegramClient".
    saved = {name: sys.modules[name] for name in touched()}
    for name in saved:
        sys.modules.pop(name, None)
    try:
        stub_environment()

        # a fns package whose __getattr__ resolves the union -> .admins, which
        # is the part of the real one this cycle actually reaches
        fns = types.ModuleType("pyUltroid.fns")
        fns.__path__ = [str(FNS)]
        sys.modules["pyUltroid.fns"] = fns

        def _fns_getattr(name):
            if name != "fns":
                raise AttributeError(name)
            module = importlib.import_module("pyUltroid.fns.admins", "pyUltroid.fns")
            return types.SimpleNamespace(
                admin_check=module.AdminKit().admin_check,
                time_formatter=lambda ms: f"{ms}ms",
            )

        fns.__getattr__ = _fns_getattr

        # 1. the _misc package on its own
        spec = importlib.util.spec_from_file_location(
            "pyUltroid._misc", MISC / "__init__.py",
            submodule_search_locations=[str(MISC)],
        )
        package = importlib.util.module_from_spec(spec)
        sys.modules["pyUltroid._misc"] = package
        spec.loader.exec_module(package)

        # 2. the real fns/admins.py, which reaches back into _misc for SUDO_M
        spec = importlib.util.spec_from_file_location(
            "pyUltroid.fns.admins", FNS / "admins.py")
        admins = importlib.util.module_from_spec(spec)
        sys.modules["pyUltroid.fns.admins"] = admins
        spec.loader.exec_module(admins)

        # 3. _assistant, whose `from ..fns import fns` closes the cycle
        spec = importlib.util.spec_from_file_location(
            "pyUltroid._misc._assistant", MISC / "_assistant.py")
        assistant = importlib.util.module_from_spec(spec)
        sys.modules["pyUltroid._misc._assistant"] = assistant
        spec.loader.exec_module(assistant)

        return package, admins, assistant
    finally:
        for name in touched():
            sys.modules.pop(name, None)
        sys.modules.update(saved)

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

import asyncio
import html
import inspect
import re
import sys
from io import BytesIO
from pathlib import Path
from traceback import format_exc
from typing import Final

from telethon import events
from telethon.errors.common import AlreadyInConversationError
from telethon.errors.rpcerrorlist import (
    AuthKeyDuplicatedError,
    BotInlineDisabledError,
    BotMethodInvalidError,
    ChatSendInlineForbiddenError,
    ChatSendMediaForbiddenError,
    ChatSendStickersForbiddenError,
    FloodWaitError,
    MessageDeleteForbiddenError,
    MessageIdInvalidError,
    MessageNotModifiedError,
    UserIsBotError,
)
from telethon.events import MessageEdited, NewMessage
from telethon.utils import get_display_name

from custom import fmtmsg
from pyUltroid.exceptions import DependencyMissingError
from strings import get_string

from .. import (
    DUAL_HNDLR,
    DUAL_MODE,
    HNDLR,
    LOGS,
    SUDO_HNDLR,
    _ignore_eval,
    asst,
    master,
    udB,
    ultroid_bot,
)
from ..dB import DEVLIST
from ..dB._core import LIST, LOADED
from ..fns import fns
from . import SUDO_M, owner_and_sudos, specials
from ._wrappers import eod


#: The FloodWait notice formats a millisecond duration. This used to be an
#: undefined global ``tf``, so every FloodWait that reached this branch
#: raised NameError instead of showing the user why their command paused.
def tf(ms):
    from ..fns import fns

    return fns.time_formatter(ms)


def _devlist():
    from ..dB import DEVLIST

    return DEVLIST

MANAGER = udB.get_key("MANAGER") if udB else None
TAKE_EDITS = udB.get_key("TAKE_EDITS") if udB else None
black_list_chats = udB.get_key("BLACKLIST_CHATS") if udB else None
def allow_sudo():
    return SUDO_M.should_allow_sudo


class PatternCompiler:
    """Turns a plugin's pattern into a compiled Telethon matcher.

    Kept as a class with no state because the rule is stateful in meaning
    but not in data: two handlers with the same ``(pattern, hndlr)`` must
    always produce the same regex, and a plugin can compile against a
    different handler prefix than the global one.
    """

    #: handler values that mean "match with no prefix at all"
    BARE: Final[tuple[str, ...]] = (" ", "NO_HNDLR")

    __slots__ = ()

    def compile(self, data: str, hndlr: str) -> "re.Pattern":
        """Strip the anchors a plugin may have added, then prefix."""
        if data.startswith("^"):
            data = data[1:]
        if data.startswith("."):
            data = data[1:]
        if hndlr in self.BARE:
            # No Hndlr Feature
            return re.compile("^" + data)
        return re.compile("\\" + hndlr + data)


#: the shared compiler; ``compile_pattern`` below is the permanent alias
PATTERNS = PatternCompiler()


def compile_pattern(data, hndlr):
    return PATTERNS.compile(data, hndlr)


def ultroid_cmd(
    pattern=None, manager=False, ultroid_bot=ultroid_bot, asst=asst, **kwargs
):
    owner_only = kwargs.get("owner_only", False)
    groups_only = kwargs.get("groups_only", False)
    admins_only = kwargs.get("admins_only", False)
    fullsudo = kwargs.get("fullsudo", False)
    only_devs = kwargs.get("only_devs", False)
    special = kwargs.get("special", False)
    allow_guest = kwargs.get("allow_guest", False)
    func = kwargs.get("func", lambda e: not e.via_bot_id)
    listen = kwargs.get("listen", None)
    #: When False, an unauthorized incoming command gets a short explicit
    #: denial instead of silently doing nothing. Set ``silent_denials=True``
    #: per command to restore the old silent behavior.
    silent_denials = kwargs.get("silent_denials", True)

    if listen is not None:

        def listen_decor(dec):
            from pyUltroid.startup.dispatcher import register_listen

            return register_listen(
                dec,
                client=ultroid_bot,
                listen=listen,
                match=kwargs.get("listen_func"),
                priority=kwargs.get("listen_priority", 99),
                once=bool(kwargs.get("once", False)),
                name=kwargs.get("listen_name", ""),
            )

        return listen_decor

    def decor(dec):
        async def wrapp(ult):
            if not ult.out:
                if owner_only:
                    if not silent_denials:
                        return await eod(ult, get_string("py_d1"), time=10)
                    return
                from pyUltroid.startup.guestmode import is_guest

                if is_guest(ult.sender_id):
                    if not allow_guest:
                        try:
                            from custom.tg import ikm

                            buttons = ikm([("Help", "guest_help", "primary")])
                            return await eod(
                                ult,
                                "❌ **Access Denied**: You do not have guest permissions to run this command.",
                                buttons=buttons,
                                time=15,
                            )
                        except Exception:
                            return await eod(
                                ult,
                                "❌ **Access Denied**: You do not have guest permissions to run this command.",
                                time=15,
                            )
                elif ult.sender_id not in owner_and_sudos():
                    if not silent_denials:
                        return await eod(ult, get_string("py_d1"), time=10)
                    return
                if ult.sender_id in _ignore_eval:
                    return await eod(
                        ult,
                        get_string("py_d1"),
                    )
                if fullsudo and ult.sender_id not in SUDO_M.fullsudos:
                    return await eod(ult, get_string("py_d2"), time=15)

                # Specials
                if special and ult.sender_id not in specials():
                    return await eod(ult, "Nice Try!")
            chat = ult.chat
            if hasattr(chat, "title"):
                if (
                    "#noub" in chat.title.lower()
                    and not (chat.admin_rights or chat.creator)
                    and not (ult.sender_id in DEVLIST)
                ):
                    return
            if ult.is_private and (groups_only or admins_only):
                return await eod(ult, get_string("py_d3"))
            elif admins_only and not (chat.admin_rights or chat.creator):
                return await eod(ult, get_string("py_d5"))
            if only_devs and not udB.get_key("I_DEV"):
                return await eod(
                    ult,
                    get_string("py_d4").format(HNDLR),
                    time=10,
                )
            # Trace tracking integration
            import time
            import uuid

            from pyUltroid.tracer import TraceRecord, tr
            from pyUltroid.tracer._tracker import _set_trace

            plugin = getattr(dec, "__module__", "unknown")
            if "." in plugin:
                plugin = plugin.split(".")[-1]
            handler = getattr(dec, "__name__", "unknown")
            try:
                user_id = ult.sender_id or 0
                chat_id = ult.chat_id or 0
                is_private = ult.is_private
                text = getattr(ult, "text", "")
                # Rich/HTML parse modes can expose (text, entities) instead of
                # a plain string. Never let that tuple reach tracing or handlers.
                if isinstance(text, tuple):
                    text = text[0] if text else ""
                from .._core.notices import sanitize_error_text

                raw_text = sanitize_error_text(str(text or ""))[:200]
            except Exception:
                user_id = chat_id = 0
                is_private = False
                raw_text = ""

            trace = TraceRecord(
                trace_id=str(uuid.uuid4()),
                plugin=plugin,
                handler=handler,
                pattern=str(pattern) if pattern else None,
                user_id=user_id,
                chat_id=chat_id,
                is_private=is_private,
                started_at=time.time(),
                raw_text=raw_text,
            )
            _set_trace(trace)

            try:
                try:
                    await dec(ult)
                    trace.close(success=True)
                except Exception as e:
                    trace.close(success=False, exc=e)
                    raise
            except FloodWaitError as fwerr:
                try:
                    safe_wait = sanitize_error_text(str(fwerr))
                    await asst.send_message(
                        udB.get_key("LOG_CHANNEL"),
                        f"`FloodWaitError:\n{safe_wait}\n\nSleeping for {tf((fwerr.seconds + 10) * 1000)}`",
                    )
                except Exception:  # noqa: S110 - failure notices must survive without logging credential-bearing exceptions
                    pass
                await asyncio.sleep(fwerr.seconds + 10)
                return
            except ChatSendInlineForbiddenError:
                return await eod(ult, "`Inline Locked In This Chat.`")
            except (ChatSendMediaForbiddenError, ChatSendStickersForbiddenError):
                return await eod(ult, get_string("py_d8"))
            except (BotMethodInvalidError, UserIsBotError):
                return await eod(ult, get_string("py_d6"))
            except AlreadyInConversationError:
                return await eod(ult, get_string("py_d7"))
            except (BotInlineDisabledError, DependencyMissingError) as er:
                from .._core.notices import sanitize_error_text
                return await eod(ult, f"`{sanitize_error_text(er)}`")
            except (MessageIdInvalidError, MessageNotModifiedError, MessageDeleteForbiddenError):
                try:
                    LOGS.error("Command notice operation failed (message unavailable)")
                except Exception:  # noqa: S110 - failure notices must survive without logging credential-bearing exceptions
                    pass
            except AuthKeyDuplicatedError:
                try:
                    LOGS.error("Authentication key duplicated; session requires re-authentication")
                except Exception:  # noqa: S110 - failure notices must survive without logging credential-bearing exceptions
                    pass
                await eod(ult, "Session expired; re-authenticate the user session.")
                sys.exit()
            except events.StopPropagation:
                raise
            except KeyboardInterrupt:
                pass
            except Exception as e:
                from .._core.notices import (
                    as_text,
                    command_error_messages,
                    error_caption,
                    sanitize_error_text,
                )

                safe_trace = sanitize_error_text(format_exc())
                safe_trigger = sanitize_error_text(as_text(getattr(ult, "text", "")))
                safe_exception = sanitize_error_text(f"{type(e).__name__}: {e}")
                try:
                    LOGS.error("Command handler failed:\n%s", safe_trace)
                except Exception:  # noqa: BLE001, S110 - failure notices must survive without logging credential-bearing exceptions
                    pass
                error_log = None
                try:
                    from pyUltroid.imaging import Imaging

                    imaging = Imaging()
                    err_card = imaging.error_card(
                        title=type(e).__name__, message=safe_exception,
                        tb=safe_trace, plugin=f"{plugin}.{handler}",
                        request_id=trace.trace_id,
                    )
                    if err_card.ok:
                        card_buf = err_card.to_bytesio("PNG", "error_card.png")
                        if card_buf:
                            error_log = await asst.send_file(
                                udB.get_key("LOG_CHANNEL"), card_buf,
                                caption=error_caption(safe_exception), parse_mode="html",
                            )
                except Exception:  # noqa: BLE001 - failure notices must survive without logging credential-bearing exceptions
                    try:
                        LOGS.error("Failed to generate/send sanitized error card")
                    except Exception:  # noqa: BLE001, S110 - failure notices must survive without logging credential-bearing exceptions
                        pass

                if not error_log:
                    try:
                        ftext = fmtmsg(
                            head="Crash Report",
                            data={"Status": "Ultroid Client Error", "Trigger": safe_trigger[:200]},
                            msgs=(f"<b>Traceback:</b>\n<pre>{html.escape(safe_trace)}</pre>"
                                  f"\n<b>Error:</b>\n{error_caption(safe_exception)}"),
                            foot=f"Handler: {html.escape(plugin)}.{html.escape(handler)}",
                        )
                        if len(ftext) > 4000:
                            with BytesIO(ftext.encode("utf-8")) as file:
                                file.name = "sanitized-error-log.html"
                                error_log = await asst.send_file(
                                    udB.get_key("LOG_CHANNEL"), file,
                                    caption=error_caption(safe_exception), parse_mode="html",
                                )
                        else:
                            error_log = await asst.send_message(
                                udB.get_key("LOG_CHANNEL"), ftext, parse_mode="html"
                            )
                    except Exception:  # noqa: BLE001 - failure notices must survive without logging credential-bearing exceptions
                        try:
                            LOGS.error("Failed to deliver sanitized command error log")
                        except Exception:  # noqa: BLE001, S110 - failure notices must survive without logging credential-bearing exceptions
                            pass

                link = getattr(error_log, "message_link", None) if error_log else None
                try:
                    messages = command_error_messages(safe_trigger, safe_exception, link)
                    for index, message in enumerate(messages):
                        if index == 0:
                            await ult.eor(message, link_preview=False, parse_mode="html")
                        else:
                            await ult.reply(message, link_preview=False, parse_mode="html")
                except Exception:  # noqa: BLE001 - failure notices must survive without logging credential-bearing exceptions
                    try:
                        LOGS.error("Failed to deliver sanitized command error notice")
                    except Exception:  # noqa: BLE001, S110 - failure notices must survive without logging credential-bearing exceptions
                        pass
            finally:
                _set_trace(None)
                asyncio.ensure_future(tr.live.record(trace))

        cmd = None
        blacklist_chats = False
        chats = None
        if black_list_chats:
            blacklist_chats = True
            chats = list(black_list_chats)
        _add_new = allow_sudo and HNDLR != SUDO_HNDLR

        _all_clients = [
            c for c in getattr(master, "telegram_clients", ()) if c is not None
        ]
        _all_bots = [
            c
            for c in master.clients_by_role("bot")
            if c is not None
        ]
        # A bot client also receives group messages the user client sees, so
        # registering the same command on both makes it execute twice. Brain
        # and other owner commands belong to user clients; bot-prefixed
        # (``/cmd``) surfaces are registered separately below.
        _bot_ids = {id(c) for c in _all_bots}
        _command_clients = [c for c in _all_clients if id(c) not in _bot_ids] or [
            ultroid_bot
        ]

        for _client in _command_clients:
            if _add_new:
                if pattern:
                    cmd = compile_pattern(pattern, SUDO_HNDLR)
                _client.add_event_handler(
                    wrapp,
                    NewMessage(
                        pattern=cmd,
                        incoming=True,
                        forwards=False,
                        func=func,
                        chats=chats,
                        blacklist_chats=blacklist_chats,
                    ),
                )
            if pattern:
                cmd = compile_pattern(pattern, HNDLR)
            _client.add_event_handler(
                wrapp,
                NewMessage(
                    outgoing=True if _add_new else None,
                    pattern=cmd,
                    forwards=False,
                    func=func,
                    chats=chats,
                    blacklist_chats=blacklist_chats,
                ),
            )
            if TAKE_EDITS:

                def func_(x):
                    return not x.via_bot_id and not (x.is_channel and x.chat.broadcast)

                _client.add_event_handler(
                    wrapp,
                    MessageEdited(
                        pattern=cmd,
                        forwards=False,
                        func=func_,
                        chats=chats,
                        blacklist_chats=blacklist_chats,
                    ),
                )
        if manager and MANAGER:
            allow_all = kwargs.get("allow_all", False)
            allow_pm = kwargs.get("allow_pm", False)
            require = kwargs.get("require", None)

            async def manager_cmd(ult):
                if not allow_all and not (
                    await fns.admin_check(ult, require=require)
                ):
                    return
                if not allow_pm and ult.is_private:
                    return
                try:
                    await dec(ult)
                except Exception as er:  # noqa: BLE001 - failure notices must survive without logging credential-bearing exceptions
                    if chat := udB.get_key("MANAGER_LOG"):
                        text = f"**#MANAGER_LOG\n\nChat:** `{get_display_name(ult.chat)}` `{ult.chat_id}`"
                        text += f"\n**Replied :** `{ult.is_reply}`\n**Command :** {ult.text}\n\n**Error :** `{er}`"
                        try:
                            return await asst.send_message(
                                chat, text, link_preview=False
                            )
                        except Exception as er:  # noqa: BLE001 - failure notices must survive without logging credential-bearing exceptions
                            LOGS.exception(er)
                    LOGS.info(f"• MANAGER [{ult.chat_id}]:")
                    LOGS.exception(er)

            if pattern:
                cmd = compile_pattern(pattern, "/")
            for _bot in _all_bots:
                _bot.add_event_handler(
                    manager_cmd,
                    NewMessage(
                        pattern=cmd,
                        forwards=False,
                        incoming=True,
                        func=func,
                        chats=chats,
                        blacklist_chats=blacklist_chats,
                    ),
                )
        if DUAL_MODE and not (manager and DUAL_HNDLR == "/"):
            if pattern:
                cmd = compile_pattern(pattern, DUAL_HNDLR)
            for _bot in _all_bots:
                _bot.add_event_handler(
                    wrapp,
                    NewMessage(
                        pattern=cmd,
                        incoming=True,
                        forwards=False,
                        func=func,
                        chats=chats,
                        blacklist_chats=blacklist_chats,
                    ),
                )
        file = Path(inspect.stack()[1].filename)
        if "addons/" in str(file):
            if LOADED.get(file.stem):
                LOADED[file.stem].append(wrapp)
            else:
                LOADED.update({file.stem: [wrapp]})
        if pattern:
            if LIST.get(file.stem):
                LIST[file.stem].append(pattern)
            else:
                LIST.update({file.stem: [pattern]})
        wrapp.__wrapped__ = dec
        return wrapp

    return decor

# Ultroid - UserBot
# Copyright (C) 2021-2024 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from . import get_help

__doc__ = get_help("help_chatbot")

import asyncio
import aiohttp
import re
from telethon import events, types

# CHATBOT: handlers formerly in _chatactions (chatBot_replies, oracleBot_replies,
# uname_stuff) now live here. Profanity/nsfw were removed in Phase 0.

import os
import json
import random
from datetime import datetime

from pyUltroid.fns import fns
from pyUltroid._core.telethon_compat import get_sender_if_resolvable
from . import LOGS, eod, get_string, udB, ultroid_bot, ultroid_cmd


# ── Moved from _chatactions ────────────────────────────────────────────
async def uname_stuff(user_id: int, username: str, first_name: str):
    """Track username changes (was in _chatactions)."""
    if not (user_id and username and username.strip()):
        return
    uname_db = udB.get_key("USERNAME_DB") or {}
    key = str(user_id)
    old = uname_db.get(key, {})
    if old.get("username") != username:
        uname_db[key] = {"username": username, "first_name": first_name}
        udB.set_key("USERNAME_DB", uname_db)


# ── Stubs for removed services ─────────────────────────────────────────
async def check_echo(chat_id, user_id):
    """Echo check — stub, echo_db was removed. Define locally if needed."""
    return False


detector = None  # ProfanityDetector removed (plugin deleted)
is_profan = lambda chat_id: False  # nsfw_db removed (plugin deleted)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

from pyUltroid.configs import Var

mongouri = udB.get_key("MONGO_URI") or Var.MONGO_URI or ""
if not mongouri:
    LOGS.warning("Oracle replies are disabled until MONGO_URI is configured")

SOL_BASE_URL = "http://confidante-alb-150133398.ap-south-1.elb.amazonaws.com"
SOL_FALLBACK_CONVO_ID = "edd8adb8-c7bb-4c0f-883b-3a413e816ec2"
SOL_MAX_ERRORS = 3

# Regex that catches Sol, SolAI, SolAi, sol, solai — word-boundary aware
_SOL_NAME_RE = re.compile(r"\b(SolAI|SolAi|Sol|sol)\b")

# ---------------------------------------------------------------------------
# Name masking
# ---------------------------------------------------------------------------


def _mask_sol_name(text: str) -> str:
    """Replace any Sol name variant in the reply with the configured alias."""
    alias = udB.get_key("SOL_ALIAS") or "Aria"
    return _SOL_NAME_RE.sub(alias, text)


# ---------------------------------------------------------------------------
# Sol AI helpers
# ---------------------------------------------------------------------------


def _sol_session_key(user_id: int) -> str:
    return f"SOL_SESSION_{user_id}"


def _sol_error_key(user_id: int) -> str:
    return f"SOL_ERRORS_{user_id}"


def _sol_increment_error() -> int:
    """Increment global Sol error count, return new count."""
    count = (udB.get_key("SOL_ERRORS_GLOBAL") or 0) + 1
    udB.set_key("SOL_ERRORS_GLOBAL", count)
    return count


def _sol_clear_errors():
    udB.set_key("SOL_ERRORS_GLOBAL", 0)


def _sol_shutdown():
    """Completely disable Sol — wipes all users, sessions, global mode."""
    LOGS.error("Sol: shutting down after too many consecutive failures.")
    udB.set_key("SOL_USERS", {})
    udB.set_key("SOL_GLOBAL_CHATS", {})
    udB.set_key("SOL_DISABLED", True)


async def _sol_refresh_auth(session: aiohttp.ClientSession) -> str | None:
    """Refresh the Sol JWT using the stored refresh token.
    On success, saves new SOL_TOKEN + SOL_REFRESH_TOKEN to udB and returns new token.
    On failure, returns None.
    """
    refresh_token = udB.get_key("SOL_REFRESH_TOKEN")
    if not refresh_token:
        LOGS.error("Sol: no SOL_REFRESH_TOKEN stored, cannot refresh.")
        return None
    try:
        async with session.post(
            f"{SOL_BASE_URL}/auth/refresh",
            headers={"Content-Type": "application/json"},
            json={"refreshToken": refresh_token},
        ) as resp:
            if resp.status != 200:
                LOGS.error(f"Sol: refresh endpoint returned {resp.status}")
                return None
            data = await resp.json()
            new_token = data.get("jwt")
            new_refresh = data.get("refresh_token")
            if not new_token:
                LOGS.error("Sol: refresh response missing jwt field")
                return None
            udB.set_key("SOL_TOKEN", new_token)
            if new_refresh:
                udB.set_key("SOL_REFRESH_TOKEN", new_refresh)
            LOGS.info("Sol: auth refreshed successfully.")
            return new_token
    except Exception as e:
        LOGS.exception(f"Sol: exception during auth refresh: {e}")
        return None


async def _sol_send_message(
    session: aiohttp.ClientSession,
    headers: dict,
    convo_id: str,
    message: str,
) -> tuple[str | None, bool]:
    """Send a message. Returns (replyText, is_401).
    replyText is None on any failure.
    is_401 signals the caller to attempt a token refresh.
    """
    try:
        async with session.post(
            f"{SOL_BASE_URL}/conversations/message",
            headers=headers,
            json={"conversationId": convo_id, "text": message},
        ) as resp:
            if resp.status == 401:
                return None, True
            data = await resp.json()
            reply = data.get("replyText")
            if not reply:
                raise ValueError("Empty replyText in response")
            return reply, False
    except Exception as e:
        LOGS.exception(f"Sol: send failed on convo {convo_id}: {e}")
        return None, False


async def _sol_try_send(
    session: aiohttp.ClientSession,
    token: str,
    convo_id: str,
    message: str,
    user_id: int,
) -> str | None:
    """Try sending once; if 401, refresh auth and retry once. Returns reply or None."""
    headers = {
        "authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    reply, is_401 = await _sol_send_message(session, headers, convo_id, message)

    if reply:
        return reply

    if is_401:
        LOGS.warning(f"Sol: 401 for user {user_id}, attempting token refresh...")
        new_token = await _sol_refresh_auth(session)
        if not new_token:
            LOGS.error("Sol: token refresh failed — shutting down.")
            _sol_shutdown()
            return None
        # Retry once with fresh token
        headers["authorization"] = f"Bearer {new_token}"
        reply, _ = await _sol_send_message(session, headers, convo_id, message)
        return reply  # None if still failing

    return None


async def sol_ai(user_id: int, message: str) -> str:
    # Bail immediately if Sol has been shut down
    if udB.get_key("SOL_DISABLED"):
        return "⚠️ AI is currently unavailable."

    token = udB.get_key("SOL_TOKEN")
    if not token:
        return "⚠️ SOL_TOKEN is not set in your Ultroid database."

    async with aiohttp.ClientSession() as session:
        # --- Ensure user has a conversation session ---
        convo_id = udB.get_key(_sol_session_key(user_id))
        if not convo_id:
            try:
                async with session.post(
                    f"{SOL_BASE_URL}/conversations",
                    headers={
                        "authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                ) as resp:
                    data = await resp.json()
                    convo_id = data["conversation"]["id"]
                    udB.set_key(_sol_session_key(user_id), convo_id)
                    LOGS.info(f"Sol: new session {convo_id} for user {user_id}")
            except Exception as e:
                LOGS.exception(f"Sol: failed to create session for {user_id}: {e}")
                convo_id = None

        # --- Try user session ---
        if convo_id:
            reply = await _sol_try_send(session, token, convo_id, message, user_id)
            if reply:
                _sol_clear_errors()
                return _mask_sol_name(reply)
            # Session bad — drop it so next call gets a fresh one
            udB.set_key(_sol_session_key(user_id), None)

        # Check again — _sol_try_send may have shut down Sol on 401
        if udB.get_key("SOL_DISABLED"):
            return "⚠️ AI is currently unavailable."

        # --- Fallback to shared conversation ---
        LOGS.warning(f"Sol: falling back to shared convo for user {user_id}")
        # Re-read token in case refresh updated it
        token = udB.get_key("SOL_TOKEN") or token
        reply = await _sol_try_send(
            session, token, SOL_FALLBACK_CONVO_ID, message, user_id
        )
        if reply:
            _sol_clear_errors()
            return _mask_sol_name(reply)

        if udB.get_key("SOL_DISABLED"):
            return "⚠️ AI is currently unavailable."

        # --- Both failed: global error tracking ---
        error_count = _sol_increment_error()
        LOGS.error(f"Sol: global error count is now {error_count}/{SOL_MAX_ERRORS}")

        if error_count >= SOL_MAX_ERRORS:
            _sol_shutdown()
            return "⚠️ AI failed too many times and has been disabled."

        return f"⚠️ AI is unavailable right now. ({error_count}/{SOL_MAX_ERRORS})"


# ---------------------------------------------------------------------------
# ReplyEngine — unified AI reply dispatcher
# ---------------------------------------------------------------------------

_ENGINES = ("sol", "chatbot", "oracle")
_ENGINE_LABELS = {"sol": "Sol", "chatbot": "ChatBot", "oracle": "Oracle"}


class ReplyEngine:
    """Encapsulates all AI reply logic behind a single dispatcher.

    Each engine method returns ``(reply_text, success_bool)``.  When an engine
    is unavailable (raises, returns an error sentinel, or has no credentials)
    ``get_reply`` transparently falls back to a configured default reply
    stored under the ``DEFAULT_REPLIES`` udB key (see ``.setreply``).
    """

    # -- per-chat engine persistence ----------------------------------------

    def _engine_key(self, chat_id):
        return f"CHATBOT_ENGINE_{chat_id}"

    def get_engine(self, chat_id):
        """Return the configured engine for *chat_id* (default ``sol``)."""
        return (udB.get_key(self._engine_key(chat_id)) or "sol").lower()

    def set_engine(self, chat_id, engine):
        udB.set_key(self._engine_key(chat_id), engine)

    # -- dispatcher ---------------------------------------------------------

    async def get_reply(
        self,
        message,
        engine=None,
        chat_id=None,
        user_id=None,
        sender_name="",
        chat_title="",
    ):
        """Dispatcher — pick the engine and delegate.

        If *engine* is not given the chat's configured default is used
        (falling back to ``sol``).  Returns ``(reply_text, success_bool)``.
        On failure a default reply is substituted when available.
        """
        if not engine:
            engine = self.get_engine(chat_id) if chat_id is not None else "sol"
        engine = engine.lower()

        try:
            if engine == "chatbot":
                reply, ok = await self.get_chatbot_reply(message)
            elif engine == "oracle":
                reply, ok = await self.get_oracle_reply(message, user_id)
            else:
                reply, ok = await self.get_sol_reply(user_id, message)
        except Exception as ex:
            LOGS.exception(f"ReplyEngine: {engine} dispatcher failed: {ex}")
            reply, ok = None, False

        if ok and reply:
            return reply, True

        # Engine unavailable — fall back to a configured default reply
        fallback = await self._get_default_reply(engine, sender_name, chat_title)
        if fallback:
            return fallback, False
        return (
            reply
            or (f"⚠️ {_ENGINE_LABELS.get(engine, engine)} engine is unavailable."),
            False,
        )

    # -- individual engines -------------------------------------------------

    async def get_chatbot_reply(self, message):
        """Wrap ``pyUltroid.fns.tools.get_chatbot_reply``."""
        try:
            reply = await fns.get_chatbot_reply(message=message)
            ok = bool(reply) and "Sorry you need" not in reply
            return reply, ok
        except Exception as ex:
            LOGS.exception(f"ReplyEngine: chatbot error: {ex}")
            return None, False

    async def get_oracle_reply(self, message, user_id):
        """Wrap ``pyUltroid.fns.tools.get_oracle_reply``."""
        try:
            reply = await fns.get_oracle_reply(
                query=message,
                user_id=user_id or ultroid_bot.me.id,
                mongo_url=mongouri,
            )
            ok = bool(reply) and "cannot use this without" not in reply.lower()
            return reply, ok
        except Exception as ex:
            LOGS.exception(f"ReplyEngine: oracle error: {ex}")
            return None, False

    async def get_sol_reply(self, user_id, message):
        """Wrap the existing ``sol_ai`` helper."""
        try:
            reply = await sol_ai(user_id=user_id, message=message)
            ok = bool(reply) and "⚠️" not in reply
            return reply, ok
        except Exception as ex:
            LOGS.exception(f"ReplyEngine: sol error: {ex}")
            return None, False

    # -- default replies ----------------------------------------------------

    async def _get_default_reply(self, engine, sender_name="", chat_title=""):
        """Pick a random default reply for *engine* and substitute templates."""
        replies = (udB.get_key("DEFAULT_REPLIES") or {}).get(engine, [])
        if not replies:
            return None
        template = random.choice(replies)
        try:
            return template.format(
                name=sender_name or "friend",
                chat=chat_title or "this chat",
                time=datetime.now().strftime("%H:%M"),
            )
        except Exception:
            return template


# Module-level singleton used by ``_auto_reply`` and the management commands
engine = ReplyEngine()


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


async def _manage_ai_user(event, db_key: str, label: str, type_: str):
    if event.reply_to:
        user_ = (await event.get_reply_message()).sender
    else:
        temp = event.text.split(maxsplit=1)
        try:
            user_ = await event.client.get_entity(await event.client.parse_id(temp[1]))
        except Exception as er:
            LOGS.exception(er)
            user_ = event.chat if event.is_private else None
    if not user_:
        return await eod(event, get_string("chab_1"))

    key = udB.get_key(db_key) or {}
    chat, user = event.chat_id, user_.id

    if type_ == "add":
        if chat in key:
            if user not in key[chat]:
                key[chat].append(user)
        else:
            key[chat] = [user]

        # If adding to SOL_USERS, remove from the other two to avoid conflicts
        if db_key == "SOL_USERS":
            for other_key in ("CHATBOT_USERS", "ORACLE_USERS"):
                other = udB.get_key(other_key) or {}
                if chat in other and user in other[chat]:
                    other[chat].remove(user)
                    if not other[chat]:
                        del other[chat]
                    udB.set_key(other_key, other)

    elif type_ == "remov":
        if chat in key:
            if user in key[chat]:
                key[chat].remove(user)
            if not key[chat]:
                del key[chat]

    udB.set_key(db_key, key)
    await event.eor(f"**{label}:**\n{type_}ed {fns.inline_mention(user_)}")


async def _list_ai_users(event, db_key: str, label: str):
    key = udB.get_key(db_key) or {}
    users = key.get(event.chat_id, [])
    if not users:
        return await event.eor(get_string("chab_2"), time=5)
    msg = f"**{label} Enabled Users In This Chat:**\n\n"
    for i in users:
        try:
            u = await event.client.get_entity(int(i))
            msg += f"• {fns.inline_mention(u)}\n"
        except Exception:
            msg += f"• `{i}`\n"
    await event.eor(msg, link_preview=False)


# ---------------------------------------------------------------------------
# Sol commands
# ---------------------------------------------------------------------------


@ultroid_cmd(pattern="repsol")
async def cmd_repsol(event):
    """Manually query Sol. Usage: .repsol <text> or reply to a message."""
    if event.reply_to:
        message = (await event.get_reply_message()).message
    else:
        try:
            message = event.text.split(" ", 1)[1]
        except IndexError:
            return await eod(event, get_string("tban_1"), time=10)
    await event.eor(await sol_ai(user_id=event.sender_id, message=message))


@ultroid_cmd(pattern="addsol")
async def cmd_addsol(event):
    """Add a user to Sol. Also removes them from ChatBot/Oracle to avoid conflicts."""
    await _manage_ai_user(event, "SOL_USERS", "Sol", "add")


@ultroid_cmd(pattern="remsol")
async def cmd_remsol(event):
    await _manage_ai_user(event, "SOL_USERS", "Sol", "remov")


@ultroid_cmd(pattern="listsol")
async def cmd_listsol(event):
    await _list_ai_users(event, "SOL_USERS", "Sol")


@ultroid_cmd(pattern="solmode")
async def cmd_solmode(event):
    """Toggle global Sol mode for this chat.
    When ON, ALL incoming messages in this chat go to Sol — no per-user list needed.
    Usage: .solmode on / .solmode off
    """
    try:
        arg = event.text.split(" ", 1)[1].strip().lower()
    except IndexError:
        current = (
            "ON"
            if (udB.get_key("SOL_GLOBAL_CHATS") or {}).get(event.chat_id)
            else "OFF"
        )
        return await event.eor(
            f"**Sol Global Mode:** currently `{current}`\nUsage: `.solmode on` or `.solmode off`"
        )

    key = udB.get_key("SOL_GLOBAL_CHATS") or {}
    if arg == "on":
        key[event.chat_id] = True
        udB.set_key("SOL_GLOBAL_CHATS", key)
        await event.eor(
            "**Sol Global Mode:** `ON` — all messages in this chat will go to Sol."
        )
    elif arg == "off":
        key.pop(event.chat_id, None)
        udB.set_key("SOL_GLOBAL_CHATS", key)
        await event.eor("**Sol Global Mode:** `OFF`")
    else:
        await event.eor("Usage: `.solmode on` or `.solmode off`")


@ultroid_cmd(pattern="solalias")
async def cmd_solalias(event):
    """Set the name shown instead of Sol in all AI replies. Usage: .solalias <name>"""
    try:
        alias = event.text.split(" ", 1)[1].strip()
    except IndexError:
        current = udB.get_key("SOL_ALIAS") or "Aria"
        return await event.eor(
            f"**AI Alias:** currently `{current}`\nUsage: `.solalias <name>`"
        )
    udB.set_key("SOL_ALIAS", alias)
    await event.eor(f"**AI Alias:** set to `{alias}`")


@ultroid_cmd(pattern="solreset")
async def cmd_solreset(event):
    """Re-enable Sol after a shutdown and clear error counters."""
    udB.set_key("SOL_DISABLED", False)
    udB.set_key("SOL_ERRORS_GLOBAL", 0)
    await event.eor("**Sol:** re-enabled. Error counter reset.")


# ---------------------------------------------------------------------------
# ChatBot commands (fallback)
# ---------------------------------------------------------------------------


@ultroid_cmd(pattern="repai")
async def cmd_repai(event):
    if event.reply_to:
        message = (await event.get_reply_message()).message
    else:
        try:
            message = event.text.split(" ", 1)[1]
        except IndexError:
            return await eod(event, get_string("tban_1"), time=10)
    await event.eor(await fns.get_chatbot_reply(message=message))


@ultroid_cmd(pattern="addai")
async def cmd_addai(event):
    await _manage_ai_user(event, "CHATBOT_USERS", "ChatBot", "add")


@ultroid_cmd(pattern="remai")
async def cmd_remai(event):
    await _manage_ai_user(event, "CHATBOT_USERS", "ChatBot", "remov")


@ultroid_cmd(pattern="listai")
async def cmd_listai(event):
    await _list_ai_users(event, "CHATBOT_USERS", "ChatBot")


# ---------------------------------------------------------------------------
# Oracle commands (fallback)
# ---------------------------------------------------------------------------


@ultroid_cmd(pattern="repoai")
async def cmd_repoai(event):
    if event.reply_to:
        message = (await event.get_reply_message()).text.strip()
    else:
        try:
            message = event.text.split(" ", 1)[1]
        except IndexError:
            return await eod(event, get_string("tban_1"), time=10)
    await event.eor(
        await fns.get_oracle_reply(
            query=message, user_id=ultroid_bot.me.id, mongo_url=mongouri
        )
    )


@ultroid_cmd(pattern="addoai")
async def cmd_addoai(event):
    await _manage_ai_user(event, "ORACLE_USERS", "Oracle", "add")


@ultroid_cmd(pattern="remoai")
async def cmd_remoai(event):
    await _manage_ai_user(event, "ORACLE_USERS", "Oracle", "remov")


@ultroid_cmd(pattern="listoai")
async def cmd_listoai(event):
    await _list_ai_users(event, "ORACLE_USERS", "Oracle")


# ---------------------------------------------------------------------------
# Engine management commands
# ---------------------------------------------------------------------------


@ultroid_cmd(pattern="chatai( (.*)|$)")
async def cmd_chatai(event):
    """Switch or view the AI engine for this chat.

    Usage: ``.chatai sol|chatbot|oracle``  (or ``.chatai`` to view current)
    """
    chat_id = event.chat_id
    match = (event.pattern_match.group(1) or "").strip()

    if not match:
        current = engine.get_engine(chat_id)
        await event.eor(
            f"**AI Engine for this chat:** `{current}`\n\n"
            "Available engines: `sol`, `chatbot`, `oracle`\n"
            "Usage: `.chatai <engine>` to switch."
        )
        return

    choice = match.split()[0].lower()
    if choice not in _ENGINES:
        await event.eor(
            f"❌ Invalid engine `{choice}`.\n"
            f"Choose from: {', '.join(f'`{e}`' for e in _ENGINES)}"
        )
        return

    engine.set_engine(chat_id, choice)
    await event.eor(f"**AI Engine** set to `{_ENGINE_LABELS[choice]}` for this chat.")


@ultroid_cmd(pattern="chatsettings")
async def cmd_chatsettings(event):
    """Show the current chatbot configuration for this chat."""
    chat_id = event.chat_id
    current_engine = engine.get_engine(chat_id)
    sleep = udB.get_key("CHATBOT_SLEEP") or 1.5
    sol_global = bool((udB.get_key("SOL_GLOBAL_CHATS") or {}).get(chat_id))

    sol_users = len((udB.get_key("SOL_USERS") or {}).get(chat_id, []))
    cb_users = len((udB.get_key("CHATBOT_USERS") or {}).get(chat_id, []))
    or_users = len((udB.get_key("ORACLE_USERS") or {}).get(chat_id, []))

    defaults = udB.get_key("DEFAULT_REPLIES") or {}
    sol_defaults = len(defaults.get("sol", []))
    cb_defaults = len(defaults.get("chatbot", []))
    or_defaults = len(defaults.get("oracle", []))

    text = (
        f"**Chatbot Settings** — `{chat_id}`\n\n"
        f"• **Engine:** `{current_engine}`\n"
        f"• **Sleep delay:** `{sleep}s`\n"
        f"• **Sol global mode:** `{'ON' if sol_global else 'OFF'}`\n\n"
        f"**Enabled users:**\n"
        f"• Sol: `{sol_users}`\n"
        f"• ChatBot: `{cb_users}`\n"
        f"• Oracle: `{or_users}`\n\n"
        f"**Default replies:**\n"
        f"• Sol: `{sol_defaults}`\n"
        f"• ChatBot: `{cb_defaults}`\n"
        f"• Oracle: `{or_defaults}`\n"
    )
    await event.eor(text, link_preview=False)


@ultroid_cmd(pattern="setreply( (.*)|$)")
async def cmd_setreply(event):
    """Add a default reply for an engine.

    Usage: ``.setreply <engine> <reply text>``
    Templates: ``{name}`` ``{chat}`` ``{time}``
    """
    match = (event.pattern_match.group(1) or "").strip()
    if not match:
        await event.eor(
            "Usage: `.setreply <engine> <text>`\n"
            "Engines: `sol`, `chatbot`, `oracle`\n"
            "Templates: `{name}`, `{chat}`, `{time}`"
        )
        return

    parts = match.split(maxsplit=1)
    if len(parts) < 2:
        await event.eor("Usage: `.setreply <engine> <text>`")
        return

    eng, text = parts[0].lower(), parts[1]
    if eng not in _ENGINES:
        await event.eor(
            f"❌ Invalid engine. Choose from: "
            f"{', '.join(f'`{e}`' for e in _ENGINES)}"
        )
        return

    defaults = udB.get_key("DEFAULT_REPLIES") or {}
    defaults.setdefault(eng, []).append(text)
    udB.set_key("DEFAULT_REPLIES", defaults)
    idx = len(defaults[eng])
    await event.eor(f"**Default reply** added for `{_ENGINE_LABELS[eng]}` (#{idx}).")


@ultroid_cmd(pattern="delreply( (.*)|$)")
async def cmd_delreply(event):
    """Remove a default reply by 1-based index.

    Usage: ``.delreply <engine> <index>``
    """
    match = (event.pattern_match.group(1) or "").strip()
    if not match:
        await event.eor("Usage: `.delreply <engine> <index>`")
        return

    parts = match.split()
    if len(parts) != 2:
        await event.eor("Usage: `.delreply <engine> <index>`")
        return

    eng = parts[0].lower()
    try:
        idx = int(parts[1])
    except ValueError:
        await event.eor("Index must be a number.")
        return

    if eng not in _ENGINES:
        await event.eor(
            f"❌ Invalid engine. Choose from: "
            f"{', '.join(f'`{e}`' for e in _ENGINES)}"
        )
        return

    defaults = udB.get_key("DEFAULT_REPLIES") or {}
    replies = defaults.get(eng, [])
    if not replies:
        await event.eor(f"No default replies set for `{_ENGINE_LABELS[eng]}`.")
        return

    if idx < 1 or idx > len(replies):
        await event.eor(f"Index out of range. Valid: `1`–`{len(replies)}`.")
        return

    replies.pop(idx - 1)
    defaults[eng] = replies
    udB.set_key("DEFAULT_REPLIES", defaults)
    await event.eor(f"**Removed** default reply #{idx} for `{_ENGINE_LABELS[eng]}`.")


@ultroid_cmd(pattern="listreplies( (.*)|$)")
async def cmd_listreplies(event):
    """List default replies for an engine.

    Usage: ``.listreplies <engine>``
    """
    match = (event.pattern_match.group(1) or "").strip()
    if not match:
        await event.eor(
            "Usage: `.listreplies <engine>`\n"
            f"Engines: {', '.join(f'`{e}`' for e in _ENGINES)}"
        )
        return

    eng = match.split()[0].lower()
    if eng not in _ENGINES:
        await event.eor(
            f"❌ Invalid engine. Choose from: "
            f"{', '.join(f'`{e}`' for e in _ENGINES)}"
        )
        return

    defaults = udB.get_key("DEFAULT_REPLIES") or {}
    replies = defaults.get(eng, [])
    if not replies:
        await event.eor(f"No default replies set for `{_ENGINE_LABELS[eng]}`.")
        return

    msg = f"**Default replies for {_ENGINE_LABELS[eng]}:**\n\n"
    for i, r in enumerate(replies, 1):
        msg += f"{i}. `{r}`\n"
    await event.eor(msg, link_preview=False)


# ---------------------------------------------------------------------------
# Unified auto-reply handler
# ---------------------------------------------------------------------------


async def _auto_reply(e):
    sender = await get_sender_if_resolvable(e)
    if not isinstance(sender, types.User) or sender.bot:
        return

    # Echo
    if await check_echo(e.chat_id, e.sender_id):
        try:
            await e.respond(e.text or e.message.message)
        except Exception as er:
            LOGS.exception(er)

    if not e.text:
        return

    sender_id = e.sender_id
    chat_id = e.chat_id
    msg = None

    # Determine which engine applies to this sender/chat, then delegate to
    # ReplyEngine.get_reply which handles failures and default-reply fallbacks.
    sol_global = udB.get_key("SOL_GLOBAL_CHATS") or {}
    sol_key = udB.get_key("SOL_USERS") or {}
    cb_key = udB.get_key("CHATBOT_USERS") or {}
    or_key = udB.get_key("ORACLE_USERS") or {}

    if sol_global.get(chat_id):
        # Global Sol mode — entire chat routed to Sol
        msg, _ = await engine.get_reply(
            e.message.message,
            engine="sol",
            chat_id=chat_id,
            user_id=sender_id,
            sender_name=getattr(sender, "first_name", ""),
        )
    elif sol_key.get(chat_id) and sender_id in sol_key[chat_id]:
        msg, _ = await engine.get_reply(
            e.message.message,
            engine="sol",
            chat_id=chat_id,
            user_id=sender_id,
            sender_name=getattr(sender, "first_name", ""),
        )
    elif cb_key.get(chat_id) and sender_id in cb_key[chat_id]:
        msg, _ = await engine.get_reply(
            e.message.message,
            engine="chatbot",
            chat_id=chat_id,
            user_id=sender_id,
            sender_name=getattr(sender, "first_name", ""),
        )
    elif or_key.get(chat_id) and sender_id in or_key[chat_id]:
        msg, _ = await engine.get_reply(
            e.message.message,
            engine="oracle",
            chat_id=chat_id,
            user_id=sender_id,
            sender_name=getattr(sender, "first_name", ""),
        )

    if msg:
        await asyncio.sleep(udB.get_key("CHATBOT_SLEEP") or 1.5)
        await e.reply(msg)

    # In private chats, the sender is the chat user. Avoid get_chat()'s
    # dialog scan, which cannot cast the pinned fork's Community peer.
    if (e.is_group or e.is_private) and sender.username:
        await uname_stuff(e.sender_id, sender.username, sender.first_name or "")

    # Profanity filter
    if detector and is_profan(e.chat_id) and e.text:
        _, flagged = detector(e.text)
        if flagged:
            await e.delete()


# Register the consolidated auto-reply handler
ultroid_bot.add_event_handler(_auto_reply, events.NewMessage(incoming=True))

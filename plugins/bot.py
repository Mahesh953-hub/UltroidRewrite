# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

from . import get_help

__doc__ = get_help("help_bot")

import asyncio
import os
import sys
import time
from platform import python_version as pyver
from random import choice

from telethon import __version__
from telethon.errors.rpcerrorlist import (
    BotMethodInvalidError,
    ChatSendMediaForbiddenError,
)
from telethon.tl.types import (
    InputMessagesFilterVideo,
    RichButtonStyle,
    RichMessage,
    InputRichMessage,
    InputRichMessageHTML,
    InputRichMessageMarkdown,
    InputRichFilePhoto,
)

from pyUltroid.version import __version__ as UltVer

from . import HOSTED_ON, LOGS

try:
    from git import Repo
except ImportError:
    LOGS.error("bot: 'gitpython' module not found!")
    Repo = None

from telethon.utils import resolve_bot_file_id
from pyUltroid.fns import fns
from pyUltroid._core.notices import NoticeEdit, ParseMode
from custom.tg import ikm
from . import (
    ATRA_COL,
    LOGS,
    OWNER_NAME,
    ULTROID_IMAGES,
    Button,
    Telegraph,
    Var,
    asst,
    call_back,
    callback,
    def_logs,
    eor,
    get_string,
    heroku_logs,
    in_pattern,
    inline_pic,
    start_time,
    udB,
    ultroid_cmd,
    ultroid_version,
)



def ULTPIC():
    return inline_pic() or choice(ULTROID_IMAGES)


buttons = ikm(
    [
        [
            (get_string("bot_3"), "url", "https://github.com/TeamUltroid/Ultroid"),
            (get_string("bot_4"), "url", "t.me/UltroidSupportChat"),
        ]
    ]
)

# Will move to strings
alive_txt = """
The Ultroid Userbot

  ◍ Version - {}
  ◍ Py-Ultroid - {}
  ◍ Telethon - {}
"""

in_alive = "{}\n\n🌀 <b>Ultroid Version -><b> <code>{}</code>\n🌀 <b>PyUltroid -></b> <code>{}</code>\n🌀 <b>Python -></b> <code>{}</code>\n🌀 <b>Uptime -></b> <code>{}</code>\n🌀 <b>Branch -></b>[ {} ]\n\n• <b>Join @TeamUltroid</b>"


@callback("alive")
async def alive(event):
    text = alive_txt.format(ultroid_version, UltVer, __version__)
    await event.answer(text, alert=True)


@ultroid_cmd(
    pattern="alive( (.*)|$)",
)
async def lol(ult):
    x = await eor(ult, "⚡")
    match = ult.pattern_match.group(1).strip()
    inline = None
    if match in ["inline", "i"]:
        try:
            res = await ult.client.inline_query(asst.me.username, "alive")
            return await res[0].click(ult.chat_id)
        except BotMethodInvalidError:
            pass
        except Exception as er:
            LOGS.exception(er)
        inline = True
    pic = udB.get_key("ALIVE_PIC")
    if isinstance(pic, list):
        pic = choice(pic)
    uptime = fns.time_formatter((time.time() - start_time) * 1000)
    an = "━〔 ✦ ᴜʟᴛʀᴏɪᴅ-ᴜꜱᴇʀʙᴏᴛ ✦ 〕━"
    header = await fns.advice()
    y = "main"
    xx = "https://github.com/ufoptg/UltroidBackup.git"
    rep = xx.replace(".git", f"/tree/{y}")

    # Build rich alive message using InputRichMessageHTML
    from telethon.tl.types import (
        InputRichMessageHTML,
    )

    an = "━〔 ✦ ᴜʟᴛʀᴏɪᴅ-ᴜꜱᴇʀʙᴏᴛ ✦ 〕━"
    y = "main"
    xx = "https://github.com/ufoptg/UltroidBackup.git"
    rep = xx.replace(".git", f"/tree/{y}")
    uptime = fns.time_formatter((time.time() - start_time) * 1000)

    if _e := udB.get_key("ALIVE_EMOJI"):
        an = an.replace("✦", _e)

    # Build rich HTML message
    html_text = (
        f"<blockquote expandable><b>{an}</b></blockquote>\n\n"
        f"<b>✦ Version</b> - <code>{ultroid_version} [{HOSTED_ON}]</code>\n"
        f"<b>✦ Py-Ultroid</b> - <code>{UltVer}</code>\n"
        f"<b>✦ Python</b> - <code>{pyver()}</code>\n"
        f"<b>✦ Uptime</b> - <code>{uptime}</code>\n"
        f"<b>✦ Telethon</b> - <code>{__version__}</code>\n"
        f"<b>✦ Branch</b> - <code>main</code>\n\n"
        f"{header}\n\n"
        f'<a href="{rep}">📂 {y}</a>'
    )

    # The current Telethon fork exposes URL buttons as KeyboardInlineButton
    # wrapping InlineButtonTypeUrl. RichMessageHTML does not accept a reply
    # markup argument, so the classic button fallback below remains the
    # transport for this alive message.
    rich_msg = InputRichMessageHTML(html=html_text)

    try:
        if pic:
            try:
                await x.edit(
                    rich_msg,
                    file=pic,
                    link_preview=False,
                )
                return await ult.try_delete()
            except ChatSendMediaForbiddenError:
                pass
            except Exception as er:
                LOGS.exception(er)
    except Exception as er:
        LOGS.exception(er)

    # Fallback to text message
    als = (
        f"<b>{an}</b>\n\n"
        f"<b>✦ Version</b> - <code>{ultroid_version} [{HOSTED_ON}]</code>\n"
        f"<b>✦ Py-Ultroid</b> - <code>{UltVer}</code>\n"
        f"<b>✦ Python</b> - <code>{pyver()}</code>\n"
        f"<b>✦ Uptime</b> - <code>{uptime}</code>\n"
        f"<b>✦ Telethon</b> - <code>{__version__}</code>\n"
        f"<b>✦ Branch</b> - <code>main</code>\n\n"
        f"{header}\n\n"
        f'<a href="{rep}">📂 {y}</a>'
    )

    if _e := udB.get_key("ALIVE_EMOJI"):
        als = als.replace("✦", _e)

    await eor(
        x,
        als,
        parse_mode="html",
        link_preview=False,
        buttons=(
            [
                [
                    (
                        "📂 Repo",
                        "url",
                        "https://github.com/TeamUltroid/Ultroid",
                        "primary",
                    ),
                    ("💬 Support", "url", "https://t.me/UltroidSupportChat", "primary"),
                ],
            ]
            if not inline
            else None
        ),
    )


@ultroid_cmd(pattern="ping$", chats=[], type=["official", "assistant"])
async def _(event):
    start = time.time()
    x = await event.eor("⚡")
    end = round((time.time() - start) * 1000)
    uptime = fns.time_formatter((time.time() - start_time) * 1000)

    # Edit the placeholder in place. Passing an InputRichMessageHTML to
    # `Message.edit` used to hand a TL object to the markdown parser, which
    # raises TypeError; the handler then called `eor`, so the user saw both
    # the placeholder and the result as two separate messages.
    text = (
        f"**⚡ Pong !!**\n\n"
        f"  `Latency` : `{end}ms`\n"
        f"  `Uptime` : `{uptime}`"
    )
    notice = NoticeEdit(x, parse_mode=ParseMode.V2)
    if not await notice.try_edit(text):
        # The placeholder could not be updated. Editing the original event is
        # still better than sending a third message.
        await event.eor(text)


@ultroid_cmd(
    pattern="cmds$",
)
async def cmds(event):
    await fns.allcmds(event, Telegraph)


heroku_api = Var.HEROKU_API


@ultroid_cmd(
    pattern="restart$",
    fullsudo=True,
)
async def restartbt(ult):
    from pyUltroid.updater import up as update_engine

    ok = await ult.eor("`Checking for updates…`")
    who = "bot" if ult.client._bot else "user"
    if update_engine.engine.last_state.is_busy():
        return await ok.edit("`Update already in progress — restart skipped.`")
    try:
        diff = await update_engine.engine.check()
    except Exception as er:
        LOGS.exception(er)
        return await ok.edit(f"`Update check failed: {er} — restart skipped.`")
    if diff is None:
        return await ok.edit("`Could not fetch upstream diff — restart skipped.`")
    if diff.has_changes:
        await ok.edit(f"`Applying {diff.commit_count} commit(s)…`")
        try:
            state = await update_engine.engine.apply(
                diff,
                restart_notice=(who, ult.chat_id, ok.id),
                quiet=True,
            )
        except Exception as er:
            LOGS.exception(er)
            return await ok.edit(f"`Update apply crashed: {er} — restart skipped.`")
        if state.status != "done":
            return await ok.edit(f"`{state.summary()} — restart skipped.`")
        if state.needs_restart:
            return await ok.edit(
                f"`{state.summary()}`\n\n`Core files changed — restarting…`"
            )
        await ok.edit(f"`{state.summary()}`\n\n`Restarting…`")
    else:
        await ok.edit("`Already up to date — restarting…`")
    udB.set_key("_RESTART", f"{who}_{ult.chat_id}_{ok.id}")
    if heroku_api:
        return await fns.restart(ok)
    call_back()
    await asyncio.sleep(1)
    os.execl(sys.executable, sys.executable, "-m", "pyUltroid", *sys.argv[1:7])


@ultroid_cmd(
    pattern="shutdown$",
    fullsudo=True,
)
async def shutdownbot(ult):
    await fns.shutdown(ult)


@ultroid_cmd(
    pattern="logs( (.*)|$)",
    chats=[],
)
async def _(event):
    opt = event.pattern_match.group(1).strip()
    file = f"ultroid{sys.argv[-1]}.log" if len(sys.argv) > 1 else "ultroid.log"
    if opt == "heroku":
        await heroku_logs(event)
    elif opt == "carbon":
        event = await event.eor(get_string("com_1"))
        with open(file) as f:
            code = f.read()[-2500:]
        file = await fns.Carbon(
            file_name="ultroid-logs",
            code=code,
            backgroundColor=choice(ATRA_COL),
        )
        if isinstance(file, dict):
            await event.eor(f"`{file}`")
            return
        await event.reply("**Ultroid Logs.**", file=file)
    elif opt == "open":
        with open("ultroid.log") as f:
            file = f.read()[-4000:]
        return await event.eor(f"`{file}`")
    else:
        await def_logs(event, file)
    await event.try_delete()


@in_pattern("alive", owner=True)
async def inline_alive(ult):
    pic = udB.get_key("ALIVE_PIC")
    if isinstance(pic, list):
        pic = choice(pic)
    uptime = fns.time_formatter((time.time() - start_time) * 1000)
    header = udB.get_key("ALIVE_TEXT") or get_string("bot_1")
    y = Repo().active_branch
    xx = Repo().remotes[0].config_reader.get("url")
    rep = xx.replace(".git", f"/tree/{y}")
    kk = f"<a href={rep}>{y}</a>"
    als = in_alive.format(
        header, f"{ultroid_version} [{HOSTED_ON}]", UltVer, pyver(), uptime, kk
    )

    if _e := udB.get_key("ALIVE_EMOJI"):
        als = als.replace("🌀", _e)
    builder = ult.builder
    if pic:
        try:
            if ".jpg" in pic:
                results = [
                    await builder.photo(
                        pic, text=als, parse_mode="html", buttons=buttons
                    )
                ]
            else:
                if _pic := resolve_bot_file_id(pic):
                    pic = _pic
                    buttons.insert(0, *ikm([(get_string("bot_2"), "alive")]))
                results = [
                    await builder.document(
                        pic,
                        title="Inline Alive",
                        description="@TeamUltroid",
                        parse_mode="html",
                        buttons=buttons,
                    )
                ]
            return await ult.answer(results)
        except Exception as er:
            LOGS.exception(er)
    result = [
        await builder.article(
            "Alive", text=als, parse_mode="html", link_preview=False, buttons=buttons
        )
    ]
    await ult.answer(result)


# --------------------------------------------------------------------- #
# Hybrid Update System (HSU) — the shadow-process UpdateEngine is the ONLY
# update path (W1-B). The old GitPython/`bash git pull` flow is gone.

_UPDATE_CONFIRM_BTNS = ikm(
    [
        [
            ("✅ Update Now", "upd_apply", "success"),
            ("↩️ Rollback", "upd_rollback", "danger"),
        ]
    ]
)


def _update_confirm_text(diff) -> str:
    """Changelog + impact summary for the confirmation prompt."""
    text = (
        "**🔼 Ultroid Update Available**\n\n"
        f"**Branch:** `{diff.branch}` • **New commits:** `{diff.commit_count}`\n\n"
        f"{diff.changelog_text(10)}\n\n{diff.impact_summary()}"
    )
    # Telegram message text cap is 4096 chars — keep headroom for entities.
    if len(text) > 3800:
        text = text[:3800] + "\n…"
    return text


async def _post_update_confirm(diff, fallback_event=None):
    """
    Post the changelog + confirm buttons through the assistant bot (inline
    keyboards only deliver callbacks to the bot client). Returns the sent
    message, or None when the assistant is unavailable.
    """
    if not asst:
        return None
    try:
        return await asst.send_message(
            udB.get_key("LOG_CHANNEL")
            or (fallback_event.chat_id if fallback_event else None),
            _update_confirm_text(diff),
            buttons=_UPDATE_CONFIRM_BTNS,
            link_preview=False,
        )
    except Exception as er:
        LOGS.exception(er)
        return None


@ultroid_cmd(pattern="update( (.*)|$)")
async def _(e):
    xx = await e.eor(get_string("upd_1"))
    opt = e.pattern_match.group(1).strip()
    from pyUltroid.updater import up
    upd_git = up.git
    from pyUltroid.updater import up as update_engine

    # ── .update rollback <sha> — explicit rollback via the engine ────────
    if opt.startswith("rollback"):
        from pyUltroid.updater import is_valid_sha

        parts = opt.split(maxsplit=1)
        sha = parts[1].strip() if len(parts) > 1 else ""
        if not sha:
            sha = update_engine.engine.last_state.sha_before
        if not sha:
            return await xx.edit(
                "`No rollback target recorded. Use `.update rollback <sha>`."
            )
        if not is_valid_sha(sha):
            return await xx.edit(
                f"**Invalid SHA** `{sha}` — expected 4-40 hex characters."
            )
        await xx.edit(f"**↩️ Rolling back to** `{sha}`…")
        state = await update_engine.engine.rollback(sha)
        suffix = (
            "\n_Run_ `.restart` _to boot the restored code._"
            if state.status == "rolled_back"
            else ""
        )
        return await xx.edit(f"`{state.summary()}`{suffix}")

    # ── Check ────────────────────────────────────────────────────────────
    diff = await update_engine.engine.check()
    if diff is None:
        return await xx.edit(get_string("upd_2") + "`git fetch/diff failed`")
    if not diff.has_changes:
        return await xx.edit(
            get_string("upd_6").format(diff.branch, upd_git.remote_url(), diff.branch),
            link_preview=False,
        )

    # ── .update now / fast / soft — apply immediately (shadow process) ───
    if opt in ("now", "fast", "soft"):
        await xx.edit(get_string("upd_7"))
        state = await update_engine.engine.apply(
            diff,
            restart_notice=(
                "bot" if e.client._bot else "user",
                e.chat_id,
                xx.id,
            ),
        )
        return await xx.edit(state.summary())

    # ── Default: changelog + confirm buttons (assistant-posted) ──────────
    sent = await _post_update_confirm(diff, fallback_event=e)
    if sent is not None:
        try:
            link = sent.message_link
        except Exception:
            link = None
        if link:
            return await xx.edit(
                f'<strong><a href="{link}">[Update Available — Changelogs]</a></strong>',
                parse_mode="html",
                link_preview=False,
            )
        return await xx.edit("**Update Available** — see the log channel to confirm.")
    # No assistant bot (BOT_TOKEN-less run) — text-only fallback.
    await xx.edit(
        _update_confirm_text(diff) + "\n\n_Run_ `.update now` _to apply._",
        link_preview=False,
    )


@callback("upd_apply", owner=True)
async def _(event):
    from pyUltroid.updater import up as update_engine

    await event.answer("⏳ Applying update…")
    msg_id = getattr(event, "message_id", None) or getattr(
        getattr(event, "message", None), "id", 0
    )
    await event.edit("**⏳ Updating…** _(shadow process running — bot stays up)_")
    # apply() with no diff re-checks upstream first, so a stale prompt can
    # never fast-forward onto a tree newer than what the owner reviewed.
    state = await update_engine.engine.apply(
        restart_notice=("bot", event.chat_id, msg_id),
    )
    text = state.summary()
    if state.status == "done" and state.needs_restart:
        text += "\n\n🔄 **Core files changed — restarting in 3s…**"
    await event.edit(text, buttons=None)


@callback("upd_rollback", owner=True)
async def _(event):
    from pyUltroid.updater import is_valid_sha
    from pyUltroid.updater import up as update_engine

    # sha_before is socket-derived state (it arrives over IPC) — validate
    # before it can reach the engine's shell-level rollback command.
    sha = update_engine.engine.last_state.sha_before
    if not sha:
        return await event.answer(
            "No previous version recorded — use .update rollback <sha>",
            alert=True,
        )
    if not is_valid_sha(sha):
        return await event.answer("Stored rollback target is invalid.", alert=True)
    await event.edit(f"**↩️ Rolling back to `{sha}`…**")
    state = await update_engine.engine.rollback(sha)
    suffix = (
        "\n_Run_ `.restart` _to boot the restored code._"
        if state.status == "rolled_back"
        else ""
    )
    await event.edit(f"`{state.summary()}`{suffix}", buttons=None)


@callback("updtavail", owner=True)
async def updava(event):
    from pyUltroid.updater import up as update_engine

    diff = await update_engine.engine.check()
    if not (diff and diff.has_changes):
        return await event.answer("Already up-to-date.", alert=True)
    await event.edit(
        _update_confirm_text(diff),
        buttons=_UPDATE_CONFIRM_BTNS,
        link_preview=False,
    )

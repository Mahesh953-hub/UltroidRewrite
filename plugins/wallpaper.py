# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}walls`
    List the wallpapers saved on this account.

• `{i}wallsave` `<reply to photo/document>`
    Save the replied media as an account wallpaper.
    If Telegram/this Telethon build rejects wallpaper uploads, the media is
    forwarded back as a plain photo instead so the command still does
    *something* useful.

• `{i}themes`
    List the chat themes available on this account.

• `{i}settheme <emoticon>` `<reply/current chat>`
    Set the current chat's theme to `<emoticon>` (one of the emoticons from
    `{i}themes`). Use `{i}settheme off` to clear it.
"""

import os

from telethon.errors.rpcbaseerrors import RPCError
from telethon.tl import functions, types

from . import HNDLR, LOGS, eod, get_string, ultroid_cmd
from pyUltroid.fns import fns

_WALL_LIST_LIMIT = 25
_THEME_LIST_LIMIT = 40


def _require(namespace, name):
    """``getattr`` probe against the live fork schema for one request type."""
    return getattr(namespace, name, None)


def _friendly_error(exc):
    return f"❌ **Error.**\n`{exc}`"


@ultroid_cmd(pattern="walls$", fullsudo=True)
async def _list_walls(ult):
    ok = await ult.eor(get_string("com_1"))
    req_cls = _require(functions.account, "GetWallPapersRequest")
    if req_cls is None:
        return await eod(
            ok,
            "`account.GetWallPapersRequest isn't available in this "
            "Telethon build (fork layer mismatch?)`",
        )
    try:
        result = await ult.client(req_cls(hash=0))
    except RPCError as exc:
        LOGS.debug("wallpaper: GetWallPapersRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))

    wallpapers = list(getattr(result, "wallpapers", None) or [])
    if not wallpapers:
        return await eod(ok, "`No wallpapers found on this account.`")

    lines = [f"**Account wallpapers** ({len(wallpapers)}):"]
    for wp in wallpapers[:_WALL_LIST_LIMIT]:
        flags = []
        if getattr(wp, "default", False):
            flags.append("default")
        if getattr(wp, "dark", False):
            flags.append("dark")
        if getattr(wp, "pattern", False):
            flags.append("pattern")
        tag = f" ({', '.join(flags)})" if flags else ""
        lines.append(f"`{wp.id}` — `{wp.slug}`{tag}")
    if len(wallpapers) > _WALL_LIST_LIMIT:
        lines.append(f"`… +{len(wallpapers) - _WALL_LIST_LIMIT} more (truncated)`")
    await eod(ok, "\n".join(lines))


@ultroid_cmd(pattern="wallsave$", fullsudo=True)
async def _save_wallpaper(ult):
    if not ult.is_reply:
        return await ult.eor(
            "`Reply to a photo/document to save it as a wallpaper.`", time=6
        )
    reply = await ult.get_reply_message()
    media = getattr(reply, "media", None)
    if media is None:
        return await ult.eor(
            "`Reply to a photo/document to save it as a wallpaper.`", time=6
        )

    ok = await ult.eor(get_string("com_1"))

    upload_cls = _require(functions.account, "UploadWallPaperRequest")
    save_cls = _require(functions.account, "SaveWallPaperRequest")
    settings_cls = getattr(types, "WallPaperSettings", None)
    input_wp_cls = getattr(types, "InputWallPaper", None)
    if upload_cls is None or settings_cls is None:
        return await _fallback_send_photo(
            ult,
            ok,
            reply,
            "`Wallpaper uploading isn't available in this Telethon build — "
            "sending the media back as-is instead.`",
        )

    doc = getattr(reply, "document", None)
    mime_type = getattr(doc, "mime_type", None) if doc else "image/jpeg"

    path = None
    try:
        path = await reply.download_media()
        if not path:
            return await eod(ok, "`Couldn't download the replied media.`")
        uploaded = await ult.client.upload_file(path)
        wallpaper = await ult.client(
            upload_cls(
                file=uploaded,
                mime_type=mime_type or "image/jpeg",
                settings=settings_cls(),
            )
        )
        if save_cls is not None and input_wp_cls is not None:
            try:
                await ult.client(
                    save_cls(
                        wallpaper=input_wp_cls(
                            id=wallpaper.id, access_hash=wallpaper.access_hash
                        ),
                        unsave=False,
                        settings=settings_cls(),
                    )
                )
            except RPCError as exc:
                LOGS.debug("wallpaper: SaveWallPaperRequest failed: %s", exc)
                # Uploaded fine but couldn't pin it to the saved list — still
                # a partial success, so keep going instead of erroring out.
        await eod(
            ok,
            f"✅ **Saved as wallpaper** — id `{wallpaper.id}`, slug `{wallpaper.slug}`.",
        )
    except RPCError as exc:
        LOGS.debug("wallpaper: save failed, falling back to photo: %s", exc)
        await _fallback_send_photo(
            ult,
            ok,
            reply,
            f"`Telegram rejected the wallpaper upload ({exc}) — sending the "
            "media back as-is instead.`",
        )
    except Exception as exc:  # noqa: BLE001 - never leak a traceback to chat
        LOGS.debug("wallpaper: unexpected error: %s", exc)
        await eod(ok, _friendly_error(exc))
    finally:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


async def _fallback_send_photo(ult, ok, reply, note):
    try:
        kind = fns.mediainfo(reply.media)
    except Exception:
        kind = ""
    try:
        await eod(ok, note)
        await ult.client.send_file(
            ult.chat_id,
            reply.media,
            force_document="doc" in kind,
        )
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("wallpaper: fallback send failed: %s", exc)
        await ult.eor(_friendly_error(exc), time=8)


@ultroid_cmd(pattern="themes$", fullsudo=True)
async def _list_themes(ult):
    ok = await ult.eor(get_string("com_1"))
    req_cls = _require(functions.account, "GetChatThemesRequest")
    if req_cls is None:
        return await eod(
            ok,
            "`account.GetChatThemesRequest isn't available in this "
            "Telethon build (fork layer mismatch?)`",
        )
    try:
        result = await ult.client(req_cls(hash=0))
    except RPCError as exc:
        LOGS.debug("wallpaper: GetChatThemesRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))

    themes = list(getattr(result, "themes", None) or [])
    if not themes:
        return await eod(ok, "`No chat themes found on this account.`")

    emoticons = [getattr(t, "emoticon", "?") for t in themes[:_THEME_LIST_LIMIT]]
    lines = [f"**Chat themes** ({len(themes)}):", " ".join(emoticons)]
    if len(themes) > _THEME_LIST_LIMIT:
        lines.append(f"`… +{len(themes) - _THEME_LIST_LIMIT} more (truncated)`")
    lines.append(f"\nUse `{HNDLR}settheme <emoticon>` to apply one to this chat.")
    await eod(ok, "\n".join(lines))


@ultroid_cmd(pattern="settheme( (.*)|$)", fullsudo=True)
async def _set_theme(ult):
    emoticon = ult.pattern_match.group(1).strip()
    if not emoticon:
        return await ult.eor(
            "`Usage: .settheme <emoticon>` (or `off` to clear)", time=6
        )

    ok = await ult.eor(get_string("com_1"))
    req_cls = _require(functions.messages, "SetChatThemeRequest")
    if req_cls is None:
        return await eod(
            ok,
            "`messages.SetChatThemeRequest isn't available in this "
            "Telethon build (fork layer mismatch?)`",
        )
    if emoticon.lower() in {"off", "none", "clear"}:
        theme = types.InputChatThemeEmpty()
        label = "cleared"
    else:
        theme = types.InputChatTheme(emoticon=emoticon)
        label = f"set to `{emoticon}`"
    try:
        input_peer = await ult.client.get_input_entity(ult.chat_id)
        await ult.client(req_cls(peer=input_peer, theme=theme))
    except RPCError as exc:
        LOGS.debug("wallpaper: SetChatThemeRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("wallpaper: SetChatThemeRequest failed: %s", exc)
        return await eod(ok, _friendly_error(exc))
    await eod(ok, f"✅ **Chat theme {label}.**")

# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""
✘ Commands Available -

• `{i}story [caption]` `<reply to media>`
    Post the replied (or attached) photo/video as a Telegram Story.
    Flags:
      `-p <6|12|24|48>`  story period in hours (default 24, free tier).
                         6/12/48 need Telegram Premium — Telegram itself
                         rejects the request server-side otherwise.
      `-priv <everyone|contacts|close_friends|nobody>`  who can view it.
      `-pin`             keep the story pinned on your profile.

• `{i}storydel <id> [id2 id3 ...]`
    Delete one or more of your own stories by id.

• `{i}stealth`
    Activate Telegram's "stealth mode" (hide your recent story views).
    Premium-only, enforced server-side.
"""

import shlex

from pyUltroid._core import stories as storycore

from . import LOGS, eod, get_string, ultroid_cmd

_PRIVACY_CHOICES = {"everyone", "contacts", "close_friends", "nobody"}
_PERIOD_CHOICES = set(storycore.PERIOD_SECONDS)


def _parse_story_args(arg):
    """Parse `.story`'s raw argument string into (caption, period, privacy, pinned)."""
    period = 24
    privacy = None
    pinned = False
    try:
        tokens = shlex.split(arg) if arg else []
    except ValueError as exc:
        raise ValueError(f"Bad quotes: {exc}")
    positional = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-p" and i + 1 < len(tokens):
            raw = tokens[i + 1]
            try:
                val = int(raw)
            except ValueError:
                raise ValueError(f"`-p` must be one of {sorted(_PERIOD_CHOICES)}")
            if val not in _PERIOD_CHOICES:
                raise ValueError(f"`-p` must be one of {sorted(_PERIOD_CHOICES)}")
            period = val
            i += 1
        elif tok == "-priv" and i + 1 < len(tokens):
            val = tokens[i + 1].lower()
            if val not in _PRIVACY_CHOICES:
                raise ValueError(f"`-priv` must be one of {sorted(_PRIVACY_CHOICES)}")
            privacy = val
            i += 1
        elif tok == "-pin":
            pinned = True
        elif tok.startswith("-") and len(tok) > 1:
            raise ValueError(f"Unknown flag: {tok}")
        else:
            positional.append(tok)
        i += 1
    caption = " ".join(positional) if positional else None
    return caption, period, privacy, pinned


def _friendly_story_error(exc):
    """Turn any exception from `_core.stories` (or a raw RPCError) into a
    short, chat-friendly message — never a traceback."""
    if isinstance(exc, storycore.PremiumRequiredError):
        return get_string("story_premium_required").format(exc)
    if isinstance(exc, storycore.StoryFeatureUnavailable):
        return get_string("story_error_unavailable").format(exc)
    if isinstance(exc, storycore.StoryError):
        return get_string("story_error_failed").format(exc)
    return get_string("story_error_generic").format(exc)


@ultroid_cmd(pattern="story( (.*)|$)", fullsudo=True)
async def _send_story(ult):
    raw = ult.pattern_match.group(1).strip()
    try:
        caption, period, privacy, pinned = _parse_story_args(raw)
    except ValueError as exc:
        return await ult.eor(f"`{exc}`", time=8)

    media = None
    if ult.is_reply:
        reply = await ult.get_reply_message()
        media = getattr(reply, "media", None)
    elif ult.media:
        media = ult.media

    if media is None:
        return await ult.eor(
            get_string("story_reply_media_required"),
            time=6,
        )

    ok = await ult.eor(get_string("com_1"))
    try:
        result = await storycore.send_story(
            ult.client,
            "me",
            media,
            caption=caption,
            period=period,
            privacy=privacy,
            pinned=pinned,
        )
    except Exception as exc:  # noqa: BLE001 - never leak a traceback to chat
        LOGS.debug("storytools: send_story failed: %s", exc)
        return await eod(ok, _friendly_story_error(exc))

    story_id = None
    try:
        updates = getattr(result.data, "updates", None) or []
        for upd in updates:
            sid = getattr(upd, "story", None)
            if sid is not None:
                story_id = getattr(sid, "id", None)
                break
    except Exception:
        pass
    suffix = f" (id `{story_id}`)" if story_id else ""
    await eod(ok, get_string("story_posted").format(suffix, period))


@ultroid_cmd(pattern="storydel( (.*)|$)", fullsudo=True)
async def _delete_story(ult):
    raw = ult.pattern_match.group(1).strip()
    if not raw:
        return await ult.eor("`Give the story id(s) to delete.`", time=6)
    ids = []
    for tok in raw.split():
        if not tok.isdigit():
            return await ult.eor(f"`Invalid story id: {tok}`", time=6)
        ids.append(int(tok))

    ok = await ult.eor(get_string("com_1"))
    try:
        await storycore.delete_story(ult.client, "me", ids)
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("storytools: delete_story failed: %s", exc)
        return await eod(ok, _friendly_story_error(exc))
    await eod(ok, get_string("story_deleted").format(len(ids)))


@ultroid_cmd(pattern="stealth$", fullsudo=True)
async def _stealth_mode(ult):
    ok = await ult.eor(get_string("com_1"))
    try:
        await storycore.send_stealth_mode(ult.client)
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("storytools: send_stealth_mode failed: %s", exc)
        return await eod(ok, _friendly_story_error(exc))
    await eod(ok, get_string("story_stealth_on"))

# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""
✘ Commands Available -

• `{i}biz`
   Show the current Telegram Business profile (Premium probe + a summary
   of greeting/away/intro/location/work-hours where retrievable).

• `{i}biz greeting <text>`
   Enable/replace the "away for X days" greeting message shown to new
   chats. Saved as a quick-reply shortcut (`sarahub-biz-greeting`) under
   the hood, exactly like `{i}qr` does.

• `{i}biz greeting off`
   Disable the greeting message.

• `{i}biz away <text>`
   Enable/replace the away message (schedule: always).

• `{i}biz away outside <text>`
   Same, but only shown outside your configured work hours.

• `{i}biz away offline <text>`
   Same, but only shown while you're offline.

• `{i}biz away custom <start_iso> <end_iso> <text>`
   Same, but only active in a fixed window (ISO-8601 timestamps, e.g.
   `2026-09-01T00:00:00+00:00`).

• `{i}biz away off`
   Disable the away message.

• `{i}biz intro <title> | <description>`
   Set the business intro card. Reply to a sticker message to attach it.

• `{i}biz intro off`
   Remove the business intro card.

• `{i}biz location <address>`
   Set the business address shown on your profile.

• `{i}biz location off`
   Clear the business location.

• `{i}biz hours <timezone> <spec>`
   Set your work-hours schedule. `<timezone>` is an IANA tz id (e.g.
   `Europe/London`). `<spec>` is one or more `<days> <start>-<end>`
   groups separated by `;` — `<days>` is a single day (`mon`) or an
   inclusive range (`mon-fri`); `<start>`/`<end>` are `H` or `H:MM` (24h).
   Example: `{i}biz hours Europe/London mon-fri 9-17;sat 10-14`.

• `{i}biz hours off`
   Clear the work-hours schedule.

• `{i}biz links`
   List your business quick-reply chat links.

• `{i}biz linknew <text>` (or reply to a message)
   Create a new `t.me/m/...` business chat link pre-filled with `<text>`.

• `{i}biz linkdel <slug>`
   Delete a business chat link (slug from `{i}biz links`).

Telegram Business is a **Premium-only** feature — every subcommand shows
one clear "Premium required" card instead of a stack trace when this
account isn't Premium.
"""


import datetime

from telethon import utils as tl_utils
from telethon.errors.rpcbaseerrors import RPCError
from telethon.tl import types
from telethon.tl.functions.messages import GetQuickRepliesRequest
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.types import InputQuickReplyShortcut

from pyUltroid._core.business_profile import (
    BusinessFeatureUnavailable,
    BusinessProfileError,
    PremiumRequiredError,
    clear_away_message,
    clear_greeting_message,
    clear_intro,
    clear_location,
    clear_work_hours,
    create_chat_link,
    delete_chat_link,
    get_chat_links,
    is_premium,
    set_away_message,
    set_greeting_message,
    set_intro,
    set_location,
    set_work_hours,
    work_hours,
)
from pyUltroid._core.richmsg import RichBlocks, edit_rich, send_rich

from . import HNDLR, LOGS, eod, get_string, ultroid_cmd
from ._rebrand import ui_cards_enabled

_GREETING_SHORTCUT = "sarahub-biz-greeting"
_AWAY_SHORTCUT = "sarahub-biz-away"

_DAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

_OFF_WORDS = {"off", "clear", "none", "disable"}


# ──────────────────────────────────────────────────────────────────────
# Rich confirmation cards (RichBlocks when ui_cards is enabled; plain
# text otherwise) — see plan item B8. No buttons: userbot messages can't
# carry working inline callbacks, so a decorative button would just spin.
# ──────────────────────────────────────────────────────────────────────


def _text_from_lines(title: str, lines) -> str:
    body = "\n".join(str(l) for l in lines if l)
    return f"**{title}**\n\n{body}" if body else f"**{title}**"


async def _respond(e, ok_msg, title: str, lines, *, style: str = "confirm") -> None:
    """Render *title*/*lines* as a rich confirmation card when
    ``ui_cards_enabled()``, otherwise as plain text.

    ``ok_msg`` is ``None`` for a fresh send (no prior "Processing..."
    message to edit) or the message returned by an earlier
    ``e.eor(get_string("com_1"))`` call.
    """
    if ui_cards_enabled():
        blocks = RichBlocks().header(title)
        body = "\n".join(str(l) for l in lines if l)
        if body:
            blocks.paragraph(body)
        try:
            if ok_msg is None:
                await send_rich(e.client, e.chat_id, blocks, reply_to=e.id)
            else:
                await edit_rich(e.client, e.chat_id, ok_msg, blocks)
            return
        except Exception as exc:  # noqa: BLE001 - defensive, richmsg already retries
            LOGS.debug(
                "business: rich confirmation card failed (%s); falling back.", exc
            )
    text = _text_from_lines(title, lines)
    if ok_msg is None:
        await e.eor(text)
    else:
        await eod(ok_msg, text)


async def _premium_required(e, ok_msg, feature: str) -> None:
    await _respond(
        e,
        ok_msg,
        get_string("biz_title_premium_required"),
        [
            f"`{feature}` requires Telegram Premium on this account.",
            get_string("biz_premium_only_note"),
        ],
        style="cancel",
    )


async def _run_business_action(
    e, ok_msg, title, coro, lines_or_fn, *, style: str = "confirm"
):
    """Await *coro* (a ``pyUltroid._core.business_profile`` wrapper call),
    translating its typed exceptions into the right rich card."""
    try:
        result = await coro
    except PremiumRequiredError:
        return await _premium_required(e, ok_msg, title)
    except BusinessFeatureUnavailable as exc:
        return await _respond(
            e,
            ok_msg,
            get_string("biz_title_feature_unavailable"),
            [f"`{exc}`"],
            style="cancel",
        )
    except BusinessProfileError as exc:
        return await _respond(
            e, ok_msg, get_string("biz_title_error"), [f"`{exc}`"], style="cancel"
        )
    lines = lines_or_fn(result) if callable(lines_or_fn) else lines_or_fn
    return await _respond(e, ok_msg, title, lines, style=style)


def _is_off(text: str) -> bool:
    return text.strip().lower() in _OFF_WORDS


# ──────────────────────────────────────────────────────────────────────
# Quick-reply shortcut helper (greeting/away messages reference one) —
# same technique as `.qr` in plugins/misc.py: there is no dedicated
# "create shortcut" request, so it's created by sending a message to
# Saved Messages tagged with `quick_reply_shortcut=`.
# ──────────────────────────────────────────────────────────────────────


async def _ensure_quick_reply(client, shortcut: str, text: str) -> int:
    await client.send_message(
        "me", text, quick_reply_shortcut=InputQuickReplyShortcut(shortcut=shortcut)
    )
    listing = await client(GetQuickRepliesRequest(hash=0))
    for qr in getattr(listing, "quick_replies", None) or ():
        if qr.shortcut == shortcut:
            return qr.shortcut_id
    raise BusinessProfileError(
        f"Could not resolve quick-reply shortcut '{shortcut}' after saving it."
    )


# ──────────────────────────────────────────────────────────────────────
# Work-hours spec parser — "mon-fri 9-17;sat 10-14" (see module docstring)
# ──────────────────────────────────────────────────────────────────────


def _parse_day_range(token: str):
    token = token.strip().lower()
    a, _, b = token.partition("-")
    b = b or a
    if a not in _DAY_NAMES or b not in _DAY_NAMES:
        raise ValueError(f"Unknown day '{token}' (use mon/tue/wed/thu/fri/sat/sun).")
    ai, bi = _DAY_NAMES.index(a), _DAY_NAMES.index(b)
    if ai <= bi:
        return list(range(ai, bi + 1))
    return list(range(ai, 7)) + list(range(0, bi + 1))


def _parse_clock(token: str) -> int:
    token = token.strip()
    hour_str, _, minute_str = token.partition(":")
    try:
        hour = int(hour_str)
        minute = int(minute_str) if minute_str else 0
    except ValueError as exc:
        raise ValueError(f"Bad time '{token}' (expected 'H' or 'H:MM').") from exc
    if not (0 <= hour <= 24 and 0 <= minute < 60):
        raise ValueError(f"Bad time '{token}' (expected 'H' or 'H:MM').")
    return hour * 60 + minute


def _parse_hours_spec(spec: str):
    """Parse an ergonomic work-hours spec into ``BusinessWeeklyOpen`` windows.

    Grammar: groups separated by ``;``, each group is ``<days> <start>-<end>``
    (see the ``.biz hours`` help entry above).
    """
    windows = []
    for group in spec.split(";"):
        group = group.strip()
        if not group:
            continue
        parts = group.split(None, 1)
        if len(parts) != 2:
            raise ValueError(
                f"Bad hours group '{group}' — expected '<days> <start>-<end>'."
            )
        days_token, time_token = parts
        start_token, sep, end_token = time_token.partition("-")
        if not sep:
            raise ValueError(
                f"Bad time range '{time_token}' — expected '<start>-<end>'."
            )
        start_minute = _parse_clock(start_token)
        end_minute = _parse_clock(end_token)
        if end_minute <= start_minute:
            raise ValueError(f"End time must be after start time in '{time_token}'.")
        for day_index in _parse_day_range(days_token):
            base = day_index * 1440
            windows.append(
                types.BusinessWeeklyOpen(
                    start_minute=base + start_minute,
                    end_minute=base + end_minute,
                )
            )
    if not windows:
        raise ValueError("No work-hours windows parsed — check your spec.")
    return windows


def _slug_from_link(url: str) -> str:
    return (url or "").rstrip("/").rsplit("/", 1)[-1]


# ──────────────────────────────────────────────────────────────────────
# Overview summarizers (`.biz`) — read straight off users.GetFullUser,
# since pyUltroid._core.business_profile intentionally only exposes
# set/clear wrappers, not getters.
# ──────────────────────────────────────────────────────────────────────


def _summarize_greeting(msg) -> str:
    if msg is None:
        return "• Greeting message: `off`"
    return (
        f"• Greeting message: `on` (shortcut #{msg.shortcut_id}, "
        f"after {msg.no_activity_days}d inactivity)"
    )


def _summarize_away(msg) -> str:
    if msg is None:
        return "• Away message: `off`"
    schedule = (
        type(msg.schedule).__name__.replace("BusinessAwayMessageSchedule", "") or "?"
    )
    offline = " · offline-only" if getattr(msg, "offline_only", False) else ""
    return f"• Away message: `on` (shortcut #{msg.shortcut_id}, {schedule}{offline})"


def _summarize_intro(intro) -> str:
    if intro is None:
        return "• Intro card: `off`"
    return f'• Intro card: `on` — "{intro.title}"'


def _summarize_location(loc) -> str:
    if loc is None or (
        not getattr(loc, "address", None) and getattr(loc, "geo_point", None) is None
    ):
        return "• Location: `off`"
    return f"• Location: `on` — {loc.address or '(geo point only)'}"


def _summarize_hours(hours) -> str:
    if hours is None:
        return "• Work hours: `off`"
    windows = getattr(hours, "weekly_open", None) or []
    return f"• Work hours: `on` — `{hours.timezone_id}` ({len(windows)} window(s))"


async def _show_overview(e) -> None:
    try:
        me = await e.client.get_me()
    except Exception as exc:  # noqa: BLE001 - network hiccup, degrade gracefully
        LOGS.debug("business: couldn't fetch self for premium probe: %s", exc)
        me = None
    premium = await is_premium(e.client, me=me)

    lines = [f"Premium: {'✅ yes' if premium else '❌ no — Business requires Premium'}"]
    try:
        full = (await e.client(GetFullUserRequest(id="me"))).full_user
    except Exception as exc:  # noqa: BLE001
        LOGS.debug("business: could not fetch full self info: %s", exc)
        full = None

    if full is not None:
        lines.append(_summarize_greeting(full.business_greeting_message))
        lines.append(_summarize_away(full.business_away_message))
        lines.append(_summarize_intro(full.business_intro))
        lines.append(_summarize_location(full.business_location))
        lines.append(_summarize_hours(full.business_work_hours))
    else:
        lines.append("`Could not fetch current business settings.`")

    lines.append("")
    lines.append(
        f"`{HNDLR}biz greeting|away|intro|location|hours|links <...>` — "
        f"see `{HNDLR}help business`."
    )
    await _respond(e, None, get_string("biz_title_overview"), lines, style="primary")


# ──────────────────────────────────────────────────────────────────────
# Subcommand handlers
# ──────────────────────────────────────────────────────────────────────


async def _cmd_greeting(e, rest: str) -> None:
    rest = rest.strip()
    if not rest:
        return await e.eor(
            f"`Usage: {HNDLR}biz greeting <text>` or `{HNDLR}biz greeting off`.", time=6
        )
    ok_msg = await e.eor(get_string("com_1"))
    if _is_off(rest):
        return await _run_business_action(
            e,
            ok_msg,
            get_string("biz_title_greeting"),
            clear_greeting_message(e.client),
            [get_string("biz_greeting_disabled")],
        )
    try:
        shortcut_id = await _ensure_quick_reply(e.client, _GREETING_SHORTCUT, rest)
    except RPCError as exc:
        return await _respond(
            e,
            ok_msg,
            get_string("biz_title_error"),
            [f"`Could not save greeting text: {exc}`"],
            style="cancel",
        )
    await _run_business_action(
        e,
        ok_msg,
        get_string("biz_title_greeting"),
        set_greeting_message(e.client, shortcut_id=shortcut_id),
        [
            f"Greeting message enabled (quick-reply `{_GREETING_SHORTCUT}`).",
            f"Text: {rest}",
        ],
    )


async def _cmd_away(e, rest: str) -> None:
    rest = rest.strip()
    if not rest:
        return await e.eor(
            f"`Usage: {HNDLR}biz away [outside|offline|custom ...] <text>` or "
            f"`{HNDLR}biz away off`. See `{HNDLR}help business`.",
            time=8,
        )
    if _is_off(rest):
        ok_msg = await e.eor(get_string("com_1"))
        return await _run_business_action(
            e,
            ok_msg,
            get_string("biz_title_away"),
            clear_away_message(e.client),
            [get_string("biz_away_disabled")],
        )

    tokens = rest.split(None, 1)
    modifier = tokens[0].lower() if len(tokens) > 1 else ""
    offline_only = None
    if modifier == "outside":
        schedule = types.BusinessAwayMessageScheduleOutsideWorkHours()
        text = tokens[1]
        desc = "outside working hours"
    elif modifier == "offline":
        schedule = types.BusinessAwayMessageScheduleAlways()
        offline_only = True
        text = tokens[1]
        desc = "always, offline-only"
    elif modifier == "custom":
        sub_tokens = tokens[1].split(None, 2)
        if len(sub_tokens) < 3:
            return await e.eor(
                f"`Usage: {HNDLR}biz away custom <start_iso> <end_iso> <text>`", time=8
            )
        start_iso, end_iso, text = sub_tokens
        try:
            start_dt = datetime.datetime.fromisoformat(start_iso)
            end_dt = datetime.datetime.fromisoformat(end_iso)
        except ValueError:
            return await e.eor(
                "`Dates must be ISO-8601, e.g. 2026-09-01T00:00:00+00:00`", time=8
            )
        schedule = types.BusinessAwayMessageScheduleCustom(
            start_date=start_dt, end_date=end_dt
        )
        desc = f"custom window {start_iso} → {end_iso}"
    else:
        schedule = types.BusinessAwayMessageScheduleAlways()
        text = rest
        desc = "always"

    ok_msg = await e.eor(get_string("com_1"))
    try:
        shortcut_id = await _ensure_quick_reply(e.client, _AWAY_SHORTCUT, text)
    except RPCError as exc:
        return await _respond(
            e,
            ok_msg,
            get_string("biz_title_error"),
            [f"`Could not save away text: {exc}`"],
            style="cancel",
        )
    await _run_business_action(
        e,
        ok_msg,
        get_string("biz_title_away"),
        set_away_message(
            e.client,
            shortcut_id=shortcut_id,
            schedule=schedule,
            offline_only=offline_only,
        ),
        [f"Away message enabled ({desc}).", f"Text: {text}"],
    )


async def _cmd_intro(e, rest: str) -> None:
    rest = rest.strip()
    if not rest:
        return await e.eor(
            f"`Usage: {HNDLR}biz intro <title> | <description>` or "
            f"`{HNDLR}biz intro off`.",
            time=6,
        )
    ok_msg = await e.eor(get_string("com_1"))
    if _is_off(rest):
        return await _run_business_action(
            e,
            ok_msg,
            get_string("biz_title_intro"),
            clear_intro(e.client),
            [get_string("biz_intro_removed")],
        )
    if "|" not in rest:
        return await _respond(
            e,
            ok_msg,
            get_string("biz_title_error"),
            [f"`Usage: {HNDLR}biz intro <title> | <description>`"],
            style="cancel",
        )
    title, _sep, description = rest.partition("|")
    title = title.strip()
    description = description.strip()
    if not title or not description:
        return await _respond(
            e,
            ok_msg,
            get_string("biz_title_error"),
            ["`Both a title and a description are required.`"],
            style="cancel",
        )

    sticker = None
    if e.is_reply:
        reply = await e.get_reply_message()
        reply_sticker = getattr(reply, "sticker", None) if reply else None
        if reply_sticker is not None:
            try:
                sticker = tl_utils.get_input_document(reply_sticker)
            except Exception as exc:  # noqa: BLE001 - sticker attach is best-effort
                LOGS.debug("business: could not use replied sticker: %s", exc)

    await _run_business_action(
        e,
        ok_msg,
        get_string("biz_title_intro"),
        set_intro(e.client, title=title, description=description, sticker=sticker),
        [
            f"Title: {title}",
            f"Description: {description}",
            "Sticker: attached" if sticker is not None else "Sticker: none",
        ],
    )


async def _cmd_location(e, rest: str) -> None:
    rest = rest.strip()
    if not rest:
        return await e.eor(
            f"`Usage: {HNDLR}biz location <address>` or `{HNDLR}biz location off`.",
            time=6,
        )
    ok_msg = await e.eor(get_string("com_1"))
    if _is_off(rest):
        return await _run_business_action(
            e,
            ok_msg,
            get_string("biz_title_location"),
            clear_location(e.client),
            [get_string("biz_location_cleared")],
        )
    await _run_business_action(
        e,
        ok_msg,
        get_string("biz_title_location"),
        set_location(e.client, address=rest),
        [f"Address: {rest}"],
    )


async def _cmd_hours(e, rest: str) -> None:
    rest = rest.strip()
    if not rest:
        return await e.eor(
            f"`Usage: {HNDLR}biz hours <timezone> <spec>` or `{HNDLR}biz hours off`. "
            f"See `{HNDLR}help business`.",
            time=8,
        )
    ok_msg = await e.eor(get_string("com_1"))
    if _is_off(rest):
        return await _run_business_action(
            e,
            ok_msg,
            get_string("biz_title_hours"),
            clear_work_hours(e.client),
            [get_string("biz_hours_cleared")],
        )
    parts = rest.split(None, 1)
    if len(parts) != 2:
        return await _respond(
            e,
            ok_msg,
            get_string("biz_title_error"),
            [
                f"`Usage: {HNDLR}biz hours <timezone> <spec>` — see `{HNDLR}help business`."
            ],
            style="cancel",
        )
    tz_id, spec = parts
    try:
        windows = _parse_hours_spec(spec)
    except ValueError as exc:
        return await _respond(
            e, ok_msg, get_string("biz_title_error"), [f"`{exc}`"], style="cancel"
        )

    schedule = work_hours(tz_id, windows)
    await _run_business_action(
        e,
        ok_msg,
        get_string("biz_title_hours"),
        set_work_hours(e.client, schedule),
        [f"Timezone: `{tz_id}`", f"Windows: {len(windows)}", f"Spec: `{spec}`"],
    )


async def _cmd_links(
    e, rest: str
) -> None:  # noqa: ARG001 - rest unused, kept for dispatch symmetry
    ok_msg = await e.eor(get_string("com_1"))
    try:
        result = await get_chat_links(e.client)
    except PremiumRequiredError:
        return await _premium_required(e, ok_msg, f"{HNDLR}biz links")
    except BusinessFeatureUnavailable as exc:
        return await _respond(
            e,
            ok_msg,
            get_string("biz_title_feature_unavailable"),
            [f"`{exc}`"],
            style="cancel",
        )
    except BusinessProfileError as exc:
        return await _respond(
            e, ok_msg, get_string("biz_title_error"), [f"`{exc}`"], style="cancel"
        )

    links = getattr(result.data, "links", None) or []
    if not links:
        return await _respond(
            e,
            ok_msg,
            get_string("biz_title_links"),
            ["No chat links yet.", f"Create one with `{HNDLR}biz linknew <text>`."],
            style="primary",
        )
    lines = []
    for link in links:
        slug = _slug_from_link(getattr(link, "link", ""))
        title = f" ({link.title})" if getattr(link, "title", None) else ""
        lines.append(
            f"• `{slug}`{title} — {getattr(link, 'views', 0)} view(s)\n  {link.message}"
        )
    await _respond(e, ok_msg, get_string("biz_title_links"), lines, style="primary")


async def _cmd_linknew(e, rest: str) -> None:
    text = rest.strip()
    if not text and e.is_reply:
        reply = await e.get_reply_message()
        text = (reply.raw_text or "").strip() if reply else ""
    if not text:
        return await e.eor(
            f"`Usage: {HNDLR}biz linknew <text>` (or reply to a message).", time=6
        )
    ok_msg = await e.eor(get_string("com_1"))
    await _run_business_action(
        e,
        ok_msg,
        "🔗 Chat Link Created",
        create_chat_link(e.client, text),
        lambda result: [f"Link: {getattr(result.data, 'link', '?')}", f"Text: {text}"],
    )


async def _cmd_linkdel(e, rest: str) -> None:
    slug = rest.strip()
    if not slug:
        return await e.eor(f"`Usage: {HNDLR}biz linkdel <slug>`", time=6)
    ok_msg = await e.eor(get_string("com_1"))
    await _run_business_action(
        e,
        ok_msg,
        "🔗 Chat Link Deleted",
        delete_chat_link(e.client, slug),
        [f"Deleted link `{slug}`."],
    )


_SUBCOMMANDS = {
    "greeting": _cmd_greeting,
    "away": _cmd_away,
    "intro": _cmd_intro,
    "location": _cmd_location,
    "hours": _cmd_hours,
    "links": _cmd_links,
    "linknew": _cmd_linknew,
    "linkdel": _cmd_linkdel,
}


# ──────────────────────────────────────────────────────────────────────
# .biz — command router
# ──────────────────────────────────────────────────────────────────────


@ultroid_cmd(pattern=r"biz(?:\s+([\s\S]*))?$", fullsudo=True)
async def biz_cmd(e):
    raw = (e.pattern_match.group(1) or "").strip()
    if not raw:
        return await _show_overview(e)

    parts = raw.split(None, 1)
    sub = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    handler = _SUBCOMMANDS.get(sub)
    if handler is None:
        return await e.eor(
            f"`Unknown .biz subcommand '{sub}'.` See `{HNDLR}help business`.", time=6
        )
    await handler(e, rest)

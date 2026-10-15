# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.
"""Sticker-set & premium (custom) emoji management.

✘ Commands Available:

• `{i}emojipacks`
    List your installed sticker / custom-emoji packs.

• `{i}emojiview <pack-link-or-shortname>`
    Show the stickers / custom emoji of a set.

• `{i}emojisave [icon_name]`  (reply to a sticker / premium emoji)
    Download the replied document to a file, send it back with a caption, and
    write its metadata(emoji glyph, file_id, document_id, pack,
    author) to a .txt file

• `{i}emojiindex [pack_title]`
    Upload every saved emoji/icon document to a **live** bot custom-emoji set.

✘ Inline frontier (assistant bot, owner only):

• `@{ASST_USERNAME} emoji <query>`
    Premium (custom) emojis **only**. Returns inline articles carrying the
    emoji glyph.
"""

import os
import re

from telethon import Button
from telethon.errors.rpcerrorlist import PackShortNameOccupiedError
from telethon.utils import get_input_document, pack_bot_file_id

from pyUltroid._core._cbs import cb, cb_pattern
from pyUltroid._core.events import InlineResultBuilder

from custom.icons import DEFAULT_ICON_UNICODE, ICONS
import custom.icons as _icons_mod
from custom.tg import ikm

from . import (
    HNDLR,
    LOGS,
    OWNER_ID,
    asst,
    callback,
    eod,
    eor,
    functions,
    in_pattern,
    types,
    udB,
    ultroid_cmd,
)

_EMOJI_SAVED_KEY = "EMOJI_SAVED"
_EMOJI_DOC_IDS_KEY = "EMOJI_DOC_IDS"

_PACK_CACHE = {}
_PACK_SEQ = 0

_STICKERSET_LINK_RE = re.compile(
    r"(?:https?://)?(?:t(?:elegram)?\.me)/add(?:emoji|stickers)/([A-Za-z0-9_]+)",
    re.I,
)
_SHORTNAME_RE = re.compile(r"^[A-Za-z0-9_]+$")
_PACK_PAGE_SIZE = 20


#  Helpers
def _utf16_len(text: str) -> int:
    """UTF-16 code-unit length (entity offsets/lengths are UTF-16 based)."""
    return len(text.encode("utf-16-le")) // 2


def _parse_shortname(raw):
    """Pull a sticker/emoji-set shortname out of a link or bare shortname."""
    raw = (raw or "").strip()
    if not raw:
        return None
    m = _STICKERSET_LINK_RE.search(raw)
    if m:
        return m.group(1)
    raw = raw.lstrip("@")
    return raw if _SHORTNAME_RE.match(raw) else None


def _set_input_from_short(short):
    return types.InputStickerSetShortName(short_name=short)


async def _fetch_set(client, stickerset_input):
    """``messages.GetStickerSetRequest`` by an InputStickerSet (ID/ShortName)."""
    return await client(
        functions.messages.GetStickerSetRequest(stickerset=stickerset_input, hash=0)
    )


def _doc_alt(doc):
    """Best-effort fallback glyph for a sticker/custom-emoji document."""
    for attr in getattr(doc, "attributes", None) or ():
        if isinstance(
            attr, (types.DocumentAttributeCustomEmoji, types.DocumentAttributeSticker)
        ):
            if getattr(attr, "alt", None):
                return attr.alt
    return None


def _doc_ext(doc):
    """File extension hinted by the document mime type."""
    mime = (getattr(doc, "mime_type", "") or "").lower()
    if "webp" in mime:
        return ".webp"
    if "tgs" in mime or "lottie" in mime:
        return ".tgs"
    if "webm" in mime:
        return ".webm"
    if "png" in mime:
        return ".png"
    if "jpeg" in mime or "jpg" in mime:
        return ".jpg"
    return ""


def _meta_text(m):
    """Plain key/value metadata for the saved .txt file."""
    return (
        f"emoji: {m.get('emoji')}\n"
        f"name: {m.get('name')}\n"
        f"document_id: {m.get('document_id')}\n"
        f"file_id: {m.get('file_id')}\n"
        f"pack: {m.get('pack')}\n"
        f"author: {m.get('author')}\n"
        f"is_custom_emoji: {m.get('is_custom_emoji')}\n"
        f"file: {m.get('file')}\n"
    )


def _meta_caption(m, txt_path):
    """Caption (also the metadata view) attached to the saved media file."""
    kind = "custom emoji" if m.get("is_custom_emoji") else "sticker"
    return (
        f"{m.get('emoji')} saved {kind}\n"
        f"name: {m.get('name')}\n"
        f"document_id: {m.get('document_id')}\n"
        f"file_id: {m.get('file_id')}\n"
        f"pack: {m.get('pack')}\n"
        f"author: {m.get('author')}\n"
        f"metadata file: {txt_path}"
    )


def _render_pack_page(idx, page):
    info = _PACK_CACHE.get(idx)
    if not info:
        return "This pack view expired — run `.emojipacks` / `.emojiview` again.", None
    docs = info.get("docs") or []
    total = len(docs)
    pages = max(1, (total + _PACK_PAGE_SIZE - 1) // _PACK_PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * _PACK_PAGE_SIZE
    kind = "custom-emoji pack" if info.get("is_emoji") else "sticker pack"
    lines = [
        f"{info['title']} ({info['short']}) — {kind}, {total} items",
        f"page {page + 1}/{pages}",
        "",
    ]
    for i, doc in enumerate(docs[start : start + _PACK_PAGE_SIZE], start=start + 1):
        alt = _doc_alt(doc) or "❓"
        lines.append(f"  {i:>3}. {alt}  doc: {doc.id}")
    text = "\n".join(lines)
    rows = []
    nav = []
    if page > 0:
        nav.append(("« Prev", cb("assistant", "emoji", "pg", str(idx), str(page - 1))))
    if page < pages - 1:
        nav.append(("Next »", cb("assistant", "emoji", "pg", str(idx), str(page + 1))))
    if nav:
        rows.append(nav)
    return text, rows


async def _send_markup(event, text, rows=None):
    buttons = ikm(rows) if rows else None
    if getattr(event.client, "_bot", False):
        return await event.reply(text, buttons=buttons, link_preview=False)
    return await asst.send_message(
        event.chat_id, text, buttons=buttons, link_preview=False
    )


def _emoji_entity_parsemode(glyph, doc_id):
    """Callable parse_mode: wrap the leading *glyph* in a MessageEntityCustomEmoji.

    Selecting the article sends the real custom emoji for Premium viewers; the
    plain *glyph* remains the fallback for everyone else. No entity is emitted
    when *doc_id* is falsy (plain text then).
    """
    glen = _utf16_len(glyph)
    did = int(doc_id) if doc_id else None

    def _parse(text):
        if did is not None:
            return text, [types.MessageEntityCustomEmoji(0, glen, document_id=did)]
        return text, []

    return _parse


def _writeback_icon_doc_ids(new_ids):
    path = os.path.abspath(_icons_mod.__file__)
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(r"(ICON_DOCUMENT_IDS\s*:\s*dict\s*=\s*\{)(.*?)(\})", src, re.S)
    if not m:
        return False, "ICON_DOCUMENT_IDS block not found"
    head, body, tail = m.group(1), m.group(2), m.group(3)
    existing = {}
    comment_lines = []
    for line in body.splitlines():
        em = re.match(r'\s*"([^"]+)"\s*:\s*(\d+)\s*,?\s*$', line)
        if em:
            existing[em.group(1)] = int(em.group(2))
        elif line.strip().startswith("#"):
            comment_lines.append(line)
    added = 0
    for name, did in new_ids.items():
        if name in existing:
            continue  # add-only: never overwrite/remove
        existing[name] = int(did)
        added += 1
    if not added:
        return True, 0
    entry_lines = [f'    "{n}": {v},' for n, v in existing.items()]
    new_body = "\n" + "\n".join(comment_lines + entry_lines) + "\n"
    new_src = src[: m.start()] + head + new_body + tail + src[m.end() :]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new_src)

    try:
        _icons_mod.ICON_DOCUMENT_IDS.update(new_ids)
    except Exception:
        pass
    return True, added


async def _upload_emoji_set(items, short_name, title):
    """Attempt to upload saved emoji/icon files into a bot custom-emoji set.

    Returns ``{name: doc_id}``. Raises on any network/session failure so the
    caller can report "no bot session / unreachable" — this is a *real* call,
    never stubbed. Requires a connected assistant bot (the owner must have
    started it) and the source files to still exist on disk.
    """
    from telethon.tl.functions.messages import UploadMediaRequest
    from telethon.tl.functions.stickers import (
        AddStickerToSetRequest,
        CreateStickerSetRequest,
    )

    stickers = []
    used = []
    for it in items:
        path = it.get("file")
        if not path or not os.path.exists(path):
            continue
        upl = await asst.upload_file(path)
        doc = await asst(UploadMediaRequest(types.InputPeerSelf(), upl))
        stickers.append(
            types.InputStickerSetItem(
                document=get_input_document(doc),
                emoji=it.get("emoji") or "✨",
            )
        )
        used.append(it)
    if not stickers:
        return {}
    try:
        res = await asst(
            CreateStickerSetRequest(
                user_id=OWNER_ID,
                title=title,
                short_name=short_name,
                stickers=stickers,
                emojis=True,
            )
        )
        docs = getattr(res, "documents", None) or []
        return {
            (it.get("name") or it.get("emoji")): doc.id for it, doc in zip(used, docs)
        }
    except PackShortNameOccupiedError:
        # Set already exists: add stickers one-by-one, then read back doc ids.
        for st in stickers:
            try:
                await asst(
                    AddStickerToSetRequest(
                        stickerset=_set_input_from_short(short_name), sticker=st
                    )
                )
            except Exception as er:
                LOGS.debug(f"emojiindex: add-to-set failed for one item: {er}")
        full = await _fetch_set(asst, _set_input_from_short(short_name))
        docs = full.documents or []
        tail = docs[-len(used) :] if docs else []
        return {
            (it.get("name") or it.get("emoji")): doc.id for it, doc in zip(used, tail)
        }


async def _send_index_to_log(new_ids):
    """Post the icon/doc-id index link to LOG_CHANNEL (best-effort)."""
    log = udB.get_key("LOG_CHANNEL")
    if not log:
        return "LOG_CHANNEL not set; index not sent."
    lines = ["**Icon index**", ""]
    for n, d in new_ids.items():
        lines.append(f"{DEFAULT_ICON_UNICODE.get(n, '')} `{n}` → `{d}`")
    text = "\n".join(lines)
    link = None
    try:
        from pyUltroid.fns import fns

        body = "<br>".join(
            f"{DEFAULT_ICON_UNICODE.get(n, '')} {n} -&gt; {d}"
            for n, d in new_ids.items()
        )
        link = await fns.make_html_telegraph("Icon Index", body)
    except Exception as er:
        LOGS.debug(f"emojiindex: telegraph index failed: {er}")
    try:
        await asst.send_message(
            int(log), text + (f"\n\nIndex: {link}" if link else ""), link_preview=False
        )
    except Exception as er:
        LOGS.debug(f"emojiindex: LOG_CHANNEL send failed: {er}")
        return f"could not send to LOG_CHANNEL: {er}"
    return link or "sent as message"


#  User commands
@ultroid_cmd(pattern="emojipacks$")
async def emoji_packs_cmd(event):
    """List installed sticker/emoji packs with buttons to view each."""
    xx = await event.eor("`Fetching your sticker/emoji packs…`")
    try:
        res = await event.client(
            functions.messages.GetMyStickersRequest(offset_id=0, limit=100)
        )
    except Exception as er:
        return await xx.eor(f"`Couldn't fetch your packs: {er}`")
    sets = getattr(res, "sets", None) or []
    if not sets:
        return await xx.eor("You have no installed sticker/emoji packs.")
    rows = []
    global _PACK_SEQ
    for covered in sets:
        st = getattr(covered, "set", None)
        if st is None:
            continue
        short = getattr(st, "short_name", None)
        if not short:
            continue
        is_em = bool(getattr(st, "emojis", False))
        title = getattr(st, "title", short)
        idx = _PACK_SEQ
        _PACK_SEQ += 1
        _PACK_CACHE[idx] = {
            "short": short,
            "title": title,
            "is_emoji": is_em,
            "docs": None,
        }
        glyph = "🧩" if is_em else "🎨"
        rows.append(
            [(f"{glyph} {title[:24]}", cb("assistant", "emoji", "pg", str(idx), "0"))]
        )
    text = f"Your installed packs — {len(rows)}\nTap a pack to view its items."
    await xx.delete()
    await _send_markup(event, text, rows)


@ultroid_cmd(pattern="emojiview( (.*)|$)")
async def emoji_view_cmd(event):
    """Show a pack's stickers/emoji, paged, with prev/next buttons."""
    raw = (event.pattern_match.group(1) or "").strip()
    if not raw:
        return await eod(
            event, f"**Usage:** `{HNDLR}emojiview <pack-link-or-shortname>`"
        )
    short = _parse_shortname(raw)
    if not short:
        return await eod(
            event,
            "Couldn't read a pack shortname. Try a `t.me/addemoji/Name` or "
            "`t.me/addstickers/Name` link, or the bare shortname.",
        )
    xx = await event.eor("`Fetching pack…`")
    try:
        full = await _fetch_set(event.client, _set_input_from_short(short))
    except Exception as er:
        return await xx.eor(f'`Couldn\'t fetch pack "{short}": {er}`')
    st = full.set
    docs = full.documents or []
    if not docs:
        return await xx.eor(
            f"`\"{getattr(st, 'title', short)}\" ({short}) has no items.`"
        )
    global _PACK_SEQ
    idx = _PACK_SEQ
    _PACK_SEQ += 1
    _PACK_CACHE[idx] = {
        "short": short,
        "title": getattr(st, "title", short),
        "is_emoji": bool(getattr(st, "emojis", False)),
        "docs": docs,
    }
    text, rows = _render_pack_page(idx, 0)
    await xx.delete()
    await _send_markup(event, text, rows)


@ultroid_cmd(pattern="emojisave( (.*)|$)")
async def emoji_save_cmd(event):
    """Save a sticker/premium emoji to a file + caption + metadata .txt + udB."""
    reply = await event.get_reply_message()
    if not (reply and reply.media):
        return await eod(
            event,
            f"**Usage:** reply to a sticker or premium emoji with "
            f"`{HNDLR}emojisave [icon_name]`",
        )
    doc = getattr(reply, "document", None) or getattr(reply, "sticker", None)
    if doc is None:
        return await eod(event, "That message has no sticker/emoji document to save.")
    raw = (event.pattern_match.group(1) or "").strip()
    icon_name = raw if (raw and raw in ICONS) else (raw or None)

    alt = None
    stickerset = None
    is_custom = False
    for attr in getattr(doc, "attributes", None) or ():
        if isinstance(attr, types.DocumentAttributeCustomEmoji):
            is_custom = True
            alt = attr.alt
            stickerset = attr.stickerset
        elif isinstance(attr, types.DocumentAttributeSticker):
            alt = alt or attr.alt
            stickerset = stickerset or attr.stickerset
    glyph = alt or "❓"
    doc_id = getattr(doc, "id", None)
    try:
        file_id = pack_bot_file_id(doc)
    except Exception:
        file_id = str(doc_id) if doc_id is not None else ""

    # pack shortname + title (best-effort; needs the network).
    pack_short = None
    pack_title = None
    if stickerset is not None:
        if isinstance(stickerset, types.InputStickerSetShortName):
            pack_short = stickerset.short_name
        try:
            full = await _fetch_set(event.client, stickerset)
            st = full.set
            pack_short = pack_short or getattr(st, "short_name", None)
            pack_title = getattr(st, "title", None)
        except Exception as er:
            LOGS.debug(f"emojisave: couldn't resolve pack: {er}")

    author = "—"
    try:
        sender = await reply.get_sender()
        if sender is not None:
            author = (
                getattr(sender, "first_name", None)
                or getattr(sender, "username", None)
                or str(getattr(sender, "id", ""))
            )
    except Exception:
        pass

    name = icon_name or (raw or glyph)
    xx = await event.eor("`Saving…`")
    ext = _doc_ext(doc)
    fname = f"emoji_{doc_id}{ext}" if doc_id is not None else f"emoji_save{ext}"
    try:
        path = await event.client.download_media(reply, file=fname)
    except Exception as er:
        return await xx.eor(f"`Download failed: {er}`")
    path = path or fname

    meta = {
        "name": name,
        "emoji": glyph,
        "document_id": doc_id,
        "file_id": file_id,
        "pack": pack_short or pack_title or "—",
        "author": author,
        "is_custom_emoji": is_custom,
        "file": path,
        "pack_title": pack_title,
    }

    txt_path = f"{path}.txt"
    try:
        with open(txt_path, "w", encoding="utf-8") as fh:
            fh.write(_meta_text(meta))
    except Exception as er:
        LOGS.debug(f"emojisave: metadata .txt write failed: {er}")

    saved = udB.get_key(_EMOJI_SAVED_KEY) or []
    saved.append(meta)
    udB.set_key(_EMOJI_SAVED_KEY, saved)

    caption = _meta_caption(meta, txt_path)
    try:
        await event.client.send_file(
            event.chat_id, path, caption=caption, reply_to=reply.id
        )
        if os.path.exists(txt_path):
            await event.client.send_file(
                event.chat_id, txt_path, caption="📋 metadata", reply_to=reply.id
            )
    except Exception as er:
        LOGS.exception("emojisave: send failed")
        return await xx.eor(f"`Saved to disk but send failed: {er}`")

    await xx.eor(
        f"Saved {glyph} (`{name}`) — doc id `{doc_id}`.\n"
        f"Metadata written to `{txt_path}` and sent as caption."
    )


@ultroid_cmd(pattern="emojiindex( (.*)|$)")
async def emoji_index_cmd(event):
    """Upload saved emoji/icon docs via a live bot → store ids → write back."""
    raw = (event.pattern_match.group(1) or "").strip()
    saved = udB.get_key(_EMOJI_SAVED_KEY) or []
    if not saved:
        return await eod(
            event,
            "No saved emoji/icon documents. Use `.emojisave` (reply to a "
            "sticker/premium emoji) first.",
        )
    if asst is None:
        return await eod(
            event,
            "`No assistant bot session — .emojiindex needs a live bot to upload "
            "emoji documents.`",
        )
    xx = await event.eor(
        "`Uploading saved emoji/icon documents to a bot custom-emoji set…`"
    )
    title = raw or "Icon Index"
    try:
        bot_user = asst.me.username
    except Exception:
        bot_user = "Ultroid"
    short_name = f"emoji_idx_by_{bot_user}"
    try:
        new_ids = await _upload_emoji_set(saved, short_name, title)
    except Exception as er:
        LOGS.exception("emojiindex: upload failed")
        return await xx.eor(
            f"`Upload failed — no live bot session / Telegram unreachable: {er}`\n"
            "No doc ids were written."
        )
    if not new_ids:
        return await xx.eor(
            "Upload produced no new document ids (no saved files on disk?)."
        )

    # 1) persist to udB (add-only: keep any existing mapping).
    existing = udB.get_key(_EMOJI_DOC_IDS_KEY) or {}
    for n, d in new_ids.items():
        existing.setdefault(n, d)
    udB.set_key(_EMOJI_DOC_IDS_KEY, existing)

    # 2) write back ADD-ONLY into custom/icons.ICON_DOCUMENT_IDS (icon names only).
    icon_ids = {n: d for n, d in new_ids.items() if n in ICONS}
    wb_ok, wb_info = False, "n/a"
    if icon_ids:
        try:
            wb_ok, wb_info = _writeback_icon_doc_ids(icon_ids)
        except Exception as er:
            LOGS.exception("emojiindex: write-back failed")
            wb_info = f"write-back error: {er}"

    # 3) post the index link to LOG_CHANNEL.
    log_info = await _send_index_to_log(new_ids)

    lines = [f"**Emoji/icon index built** — {len(new_ids)} document id(s):", ""]
    for n, d in new_ids.items():
        lines.append(f"{DEFAULT_ICON_UNICODE.get(n, '')} `{n}` → `{d}`")
    lines.append("")
    lines.append(f"udB `{_EMOJI_DOC_IDS_KEY}`: updated.")
    if icon_ids:
        lines.append(
            f"custom/icons.py ICON_DOCUMENT_IDS: {'+' + str(wb_info) if wb_ok else wb_info} "
            "(add-only, existing rows preserved)."
        )
    else:
        lines.append(
            "custom/icons.py: no saved names matched ICONS keys (nothing written back)."
        )
    lines.append(f"LOG_CHANNEL: {log_info}")
    await xx.eor("\n".join(lines), link_preview=False)


#  Callback: pack-view pagination (assistant bot)
@callback(cb_pattern("assistant", "emoji", "pg"), owner=True)
async def _pack_page_cb(event):
    data = event.data_match.group(1).decode("utf-8")  # "<idx>:<page>"
    idx_s, _, page_s = data.partition(":")
    try:
        idx = int(idx_s)
        page = int(page_s) if page_s.isdigit() else 0
    except ValueError:
        return await event.answer("Bad pagination data.", alert=True)
    info = _PACK_CACHE.get(idx)
    if not info:
        return await event.answer(
            "This pack view expired — run .emojipacks / .emojiview again.", alert=True
        )
    if info.get("docs") is None:
        await event.answer("Loading pack…")
        try:
            full = await _fetch_set(asst, _set_input_from_short(info["short"]))
        except Exception as er:
            return await event.answer(f"Couldn't fetch pack: {er}", alert=True)
        info["docs"] = full.documents or []
    text, rows = _render_pack_page(idx, page)
    await event.edit(text, buttons=rows, link_preview=False)


@in_pattern("emoji(.*)", owner=True)
async def emoji_inline(event):
    """Premium-emoji inline frontier: articles + copy/search-again buttons."""
    query = (event.pattern_match.group(1) or "").strip().lower()
    builder = InlineResultBuilder(event)
    saved = udB.get_key(_EMOJI_SAVED_KEY) or []
    premium = [it for it in saved if it.get("is_custom_emoji")]

    again_q = f"emoji {query}".strip() or "emoji"
    again_btn = [Button.switch_inline("🔍 Search again", query=again_q, same_peer=True)]

    if not premium:
        art = await builder.article(
            title="Premium emoji frontier",
            description="Use .emojisave first",
            text=(
                "No saved premium emojis yet.\n"
                "Use `.emojisave` (reply to a premium/custom emoji) to populate "
                "this frontier."
            ),
            buttons=[again_btn],
            link_preview=False,
        )
        return await event.answer([art] if art is not None else [])

    doc_ids = udB.get_key(_EMOJI_DOC_IDS_KEY) or {}
    matches = premium
    if query:
        matches = [
            it
            for it in premium
            if query in str(it.get("name", "")).lower()
            or query in str(it.get("emoji", "")).lower()
            or query in str(it.get("pack", "")).lower()
            or query in str(it.get("document_id", "")).lower()
        ]
    if not matches:
        art = await builder.article(
            title="No matches",
            description="Try another query",
            text=f"No saved premium emojis match `{query}`.",
            buttons=[again_btn],
            link_preview=False,
        )
        return await event.answer([art] if art is not None else [])

    results = []
    for it in matches[:50]:
        glyph = it.get("emoji") or "❓"
        name = it.get("name") or glyph
        doc_id = doc_ids.get(name) or it.get("document_id")
        pack = it.get("pack") or "—"
        author = it.get("author") or "—"
        body = f"{glyph}\nDoc id: {doc_id}\nPack: {pack}\nAuthor: {author}"
        btns = [
            [types.KeyboardButtonCopy(text="📋 Copy doc id", copy_text=str(doc_id))],
            again_btn,
        ]
        art = await builder.article(
            title=f"{glyph} {name}",
            description=f"doc: {doc_id} • {pack} • {author}",
            text=body,
            parse_mode=_emoji_entity_parsemode(glyph, doc_id),
            buttons=btns,
            link_preview=False,
        )
        if art is not None:
            results.append(art)
    await event.answer(results)

# Native rich messages (TL layer 230)

`richmsg.py` builds and sends Telegram `RichMessage` payloads using the pinned
Telethon fork's TL layer 230 schema. This is a strict native path: there is no
layer-availability probing, `has(kind)` compatibility API, account/Premium-based
engine selection, or fallback to ordinary message entities. Missing or invalid
native payloads fail explicitly.

## Build a message

`RichBlocks` provides structured content while `RichMarkup` preserves
caller-supplied HTML or Markdown. Supported block helpers include headers,
paragraphs, lists, code, tables, expandable sections, and media.

```python
from pyUltroid._core.richmsg import (
    RichBlocks,
    rich_message_html,
    send_rich,
)

blocks = (
    RichBlocks()
    .header("Status", level=2)
    .paragraph("Everything is healthy.")
    .table([["Key", "Value"], ["Mode", "Rich"]])
    .expandable("Details", "Additional information")
)

payload = rich_message_html(blocks)
await send_rich(client, chat, blocks)
```

`rich_message_html`, `rich_message_markdown`, `rich_message_blocks`, and
`rich_message` create the corresponding native `InputRichMessage*` payloads.
For media, use `RichFile` or an existing `InputRichFilePhoto` /
`InputRichFileDocument` and reference its ID in the markup (`<img src="id">` or
`<tg-document src="id">`). File references must match the supplied file vector.
`RichFile` sources are uploaded through `messages.uploadMedia` before sending.

## Sending and editing

`send_rich` and `edit_rich` issue raw layer-230 `messages.SendMessageRequest`,
`messages.EditMessageRequest`, or `messages.EditInlineBotMessageRequest`
requests with a native `rich_message` field. They do not retry through
`client.send_message` or convert the payload to message entities. A readable
plain-text `message` field accompanies the rich payload as required by the
request schema; pass `plain_text=` to override the generated value.

Both the assistant bot client (`asst`) and the userbot client route
`FmtMessage` values from `custom.fmt.fmtmsg` through this sender. The rich path
does not inspect account Premium status or sender type.

## Inline results

`InlineResultBuilder.article()` and `article_rich()` both produce
`InputBotInlineMessageRichMessage` results. Text articles do not fall back to
Telethon's plain `InlineBuilder.article()`; invalid conversion or schema
construction raises. Photo and document results remain media results and use
their native media constructors.

## Compatibility contract

This project requires the Telethon fork pinned in the project dependencies,
which exposes TL layer 230. Do not add Layer 227/228 runtime probes,
`available`/`has(kind)` wrappers, ordinary-message entity fallbacks, or
account-based rich-message selection. Keep inline text builders and assistant
send/edit paths on the same native RichMessage engine.

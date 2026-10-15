# Rich-message engines

`richmsg.py` keeps one format-neutral `RichBlocks` structure and exports it
through two deliberately different engines. This preserves the original design:
a block set always has an ordinary Telegram representation and does not depend
on Telegram certifying a `RichMessage`.

## Selection axes

The builder and the `rich_message`, `send_rich`, and `edit_rich` entry points
accept four controls:

| Argument | Values | Meaning |
| --- | --- | --- |
| `engine` | `auto`, `legacy`, `rich` | Select policy-based, ordinary-message, or native rich output. |
| `format` | `html`, `markdown` | Select the markup branch for either engine. |
| `is_premium` | `True`, `False`, `None` | Explicit Premium status of the sending account. `None` means unknown. |
| `is_bot` | `True`, `False`, `None` | Explicit sender type. `None` means unknown. |

`auto` chooses the native Rich engine when `is_premium=True` or `is_bot=True`.
Bots always qualify, regardless of Premium status. A normal non-Premium user, or
an account whose status is unknown, uses Legacy. Explicit `engine="rich"` is an
expert override and builds native output directly.

Existing `RichBlocks()` and `send_rich(client, peer, blocks)` calls continue to
work. For compatibility, an old send call with none of the four controls still
uses `rich_sender()` to route a fresh user-account send through the configured
assistant bot. New code should pass `is_bot` and `is_premium` explicitly so
ownership is not implicit. Edits are never rerouted.

## Legacy engine

Legacy is the always-available fallback. It renders blocks into Markdown or HTML
and parses that markup with `telethon.extensions.markdownv3`, producing normal
message text plus Telegram entities. It does not instantiate `RichMessage` and
does not need server certification.

Markdown is the primary representation because mdv3 has broad Markdown support;
HTML uses mdv3's `mode="html"` branch. Lists, code, expandable blockquotes,
tables converted to preformatted grids, and media captions retain useful normal
message representations.

Telegram's normal message entities have no H1/H2/H3 concept. Legacy headings
therefore become bold lines. This is an inherent fallback limitation, not a
heading emulation bug.

```python
blocks = RichBlocks(
    engine="legacy",
    format="markdown",
    is_premium=False,
    is_bot=False,
).header("Status").paragraph("Everything is healthy.")

await send_rich(client, chat, blocks)
```

## Rich engine

Rich builds Layer 228 native input objects and sends them in the raw request's
`rich_message` field:

- HTML: `InputRichMessageHTML`
- Markdown: `InputRichMessageMarkdown`
- bot inline content: `InputBotInlineMessageRichMessage`

The module directly imports these APIs from the pinned
`Mahesh953-hub/Telethon` fork. There are no stock-Telethon/Layer-227 capability
probes, `RichMessageUnavailable` exceptions, or exception-driven native-to-plain
fallbacks. A normal Legacy text/entity representation still accompanies a Rich
send so older viewers retain meaningful text.

```python
blocks = RichBlocks(
    engine="auto",
    format="markdown",
    is_premium=True,
    is_bot=False,
).header("Premium card").table([["Key", "Value"], ["Mode", "Rich"]])

await send_rich(client, chat, blocks)
```

For a bot Markdown payload:

```python
payload = bot_inline_rich_message(
    blocks,
    format="markdown",
    is_bot=True,
    is_premium=False,
    reply_markup=markup,
)
```

## Migration and extension notes

- Keep `RichBlocks` format-neutral. Add a block once, then implement its Legacy,
  Rich HTML, and Rich Markdown representations together.
- Do not reintroduce runtime feature detection for stock Telethon. This project
  requires the Layer 228 fork.
- Do not fake unsupported entities in Legacy mode. Preserve content plainly and
  document the loss.
- Keep both engines built from the same normalized block list so generator input
  is consumed once and fallback content cannot drift from rich content.
- Uploaded media must already be an `InputPhoto` or `InputDocument`; Legacy can
  retain only its caption.
- A future mdv3 rewrite may make `RichMessage` itself the parser's primary
  output. Until then, the explicit Rich HTML/Markdown exporters are the native
  boundary and mdv3 remains the ordinary-message backbone.

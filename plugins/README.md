# SarahUB Plugins Folder

This folder contains first-party plugins loaded automatically by the modernized loader on boot.

---

## 1. Modern Plugin Template (`howtocuddleme.py`)

For a complete, working example showcasing all modernized features in a single file, please refer to [howtocuddleme.py](file:///root/Rewrite/SarahUB/plugins/howtocuddleme.py).

This template file demonstrates:
- **Custom Client Attributes**: No relative imports required! Use `client.asst`, `client.ultroid_bot`, and `client.udB` directly.
- **Colorized Buttons**: Construct ReplyInlineMarkups with custom styles ('danger', 'success', 'primary') and icons using `ikm()`.
- **Guest Gating**: Control access scopes using `allow_guest=True` vs `allow_guest=False`.
- **Inline `listen()`**: Ask questions and capture incoming chat events/messages asynchronously with automatic timeouts.
- **PluginPart Registries**: Keep your handlers clean and isolated under a specific scope to prevent collisions.

---

## 2. Basic Command Structure

Write clean event handlers using the `@ultroid_cmd` decorator. The modern loader will automatically intercept, wrap, and register your commands under a dedicated `PluginPart` scope.

```python
from telethon import events
from pyUltroid._misc._decorators import ultroid_cmd

@ultroid_cmd(pattern="my_ping$")
async def ping(event):
    await event.reply("Pong!")
```

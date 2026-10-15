---
title: SarahUB
colorFrom: blue
colorTo: red
sdk: docker
pinned: false
---

# SarahUB

A modernized, high-performance **Telegram userbot** built on a pinned
[Mahesh953-hub/Telethon](https://github.com/Mahesh953-hub/Telethon) fork.
It is a heavily reworked continuation of the Ultroid lineage — with an
isolated plugin architecture, a token-gated web setup server, native rich
message support, styled inline keyboards, voice-chat music, business mode, and
an opt-in AI agent.

SarahUB does **not** rely on any patched Telegram app. It uses the official
Telegram client protocol through Telethon, so you can license your account the
same way you would with any API-based userbot.

---

## What is SarahUB?

SarahUB is a single process that runs several cooperating clients against one
database:

| Client | Role |
|--------|------|
| `ultroid_bot` | Your **user account** — the primary self-bot that runs commands, plugins, and VC music. |
| `asst` | Your **assistant bot** (a `@BotFather` token) — owns managed bot features, business mode, guest mode, inline keyboards, and rich-message delivery. |

Commands get dispatched across every connected client through a single
dispatcher fan-out. Inline keyboards and native rich messages only render from
a **bot** account, so SarahUB automatically routes those sends through `asst`
when the callee is the user client — this is handled for you in the rich-message
layer (`rich_sender`), not something plugin authors have to remember.

### Key features

- **Isolated plugin architecture** — multi-handler dynamic event tracking under
  `PluginPart` scopes, eliminating command collapse and collisions.
- **Client attribute injection** — key references (`asst`, `ultroid_bot`, `udB`,
  `HNDLR`, `DUAL_HNDLR`, `SUDO_HNDLR`) are bound onto client instances, removing
  the need for relative imports.
- **Inline `listen()` helper** — lightweight inline event capture for user
  responses and inline button callback clicks.
- **Business mode** — integrated business secretary connection support with
  automatic `InvokeWithBusinessConnectionRequest` context wrapping.
- **Guest mode gating** — access-control restrictions for guest sessions with
  timeout routines.
- **Rich messages & styled buttons** — native `RichMessages` (tables,
  blockquotes, headers, lists, code, media), custom-emoji buttons, and
  styled buttons (`primary`/`success`/`danger`) with icons. See
  [Rich formats](#rich-formats).
- **Web setup server** — token-gated setup UI for API credentials, phone/QR
  login, live SSE logs, and atomic `.env` writes. See [Server](#server).
- **Multiclient support** — one primary account plus optional secondary user
  sessions and additional bot tokens, all sharing one database.
- **AI agent & mini router** — a full agentic loop with tool capability
  policies, a provider registry, and a lightweight intent classifier
  (`MiniAI`).

---

## Requirements

- Python **3.10 or newer** (the project runs on 3.10–3.13; the Docker image
  uses a Miniforge **Python 3.11** environment on a CUDA base).
- A Telegram **API ID / API hash** from <https://my.telegram.org/apps>.
- The client library installed from the pinned fork (see `requirements.txt`
  and `pyproject.toml`).
- Optional: `uv` for fast environment management, and a `@BotFather` token for
  bot features.

---

## Ways to deploy

SarahUB supports several deployment styles. They share the same `.env`
configuration (see [Configuration](#configuration)).

### 1. Web setup server (recommended for first run)

When no session is configured yet, `python3 -m pyUltroid` starts the setup
server automatically. Open the printed URL, enter your `API_ID` / `API_HASH`,
and log in via **phone OTP** or **QR code**. The server writes a valid `.env`
(including your `SESSION` string) atomically and then restarts into the bot
automatically.

```bash
git clone https://github.com/Mahesh953-hub/SarahUB && cd SarahUB
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m pyUltroid
```

### 2. Docker (GPU / CUDA)

The image is based on `nvidia/cuda:12.5.1-cudnn-devel-ubuntu22.04`, installs a
Miniforge **Python 3.11** environment along with Node.js (for the setup server
frontend) and FFmpeg (pulled from the `mwader/static-ffmpeg` image), and clones
the repository so `start.sh` can keep it updated via `git pull`:

```bash
docker build -t sarahub .
docker run --rm -it \
  --env-file .env \
  -p 7860:7860 \
  sarahub
```

For a **private** fork, pass a token at build time (used only for the clone):

```bash
docker build --build-arg GIT_TOKEN=<PAT> -t sarahub .
```

The container runs `./start.sh` as a non-root `sarah` user and exposes port
`7860`. All tunables (`TZ`, `APP_USER`, `PYTHON_VERSION`, `GIT_TOKEN`,
`EXPOSE_PORT`, …) are declared as `ARG`s in [`Dockerfile`](./Dockerfile);
[`.dockerignore`](./.dockerignore) keeps secrets and runtime state out of the
build context.

The Hugging Face Spaces frontmatter (`sdk: docker`) means this same image works
as a Space — the setup token printed at startup is the access control for the
public UI.

### 3. Source with `uv` (development)

`uv` is the recommended workflow for local development and for keeping the
lockfile in sync:

```bash
uv venv --python 3.12
uv pip install -e .

# Or install all extras at once (vc, web, ai, plugins, local, dev)
uv pip install -e ".[vc,web,ai,plugins,local,dev]"

uv lock
uv sync
```

### 4. Source with plain `pip`

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 -m pyUltroid
```

### 5. Termux (Android)

`resources/startup/termux.sh` bootstraps a Termux install, and
`resources/startup/_termux.py` is the helper it uses. `resources/startup/locals.py`
is the standalone local-deploy helper for a quick interactive install on Linux.

---

## Configuration

Copy `.env.sample` to `.env` and fill in real values. Never commit `.env`.
Key variables:

| Variable | Purpose |
|----------|---------|
| `API_ID` / `API_HASH` | Mandatory Telegram application credentials. |
| `SESSION` | Primary user account session string (generated by the setup server if absent). |
| `BOT_TOKEN` | Primary assistant bot token (from `@BotFather`) — required for business/guest/managed-bot features. |
| `LOG_CHANNEL` | Log channel/group ID (`0` to disable). |
| `SESSION2`, `SESSION3` | Optional additional user accounts. |
| `BOT_TOKEN2`, `BOT_TOKEN3` | Optional additional bot accounts. |
| `DATABASE_URL` / `REDIS_URI` / `MONGO_URI` | Optional `SQL`/`Redis`/`Mongo` backends (defaults to the bundled `udB` store). |
| `BOT_MODE` | Set truthy to disable the user account and run as a bot-only deployment. |

All clients share the same `udB` database and dispatcher fan-out.

---

## Server

The web setup server (`pyUltroid/webserver`, exposed via `server.py` and the
bootstrap hook at the top of `pyUltroid`) replaces the old status-only stub:

- **Token-gated**: a one-time `SETUP_TOKEN` is generated per process (or set
  via env) and printed once; every `/api/*` route requires it (`X-Setup-Token`
  header or `?token=` query param), compared in constant time.
- **Login**: phone-OTP and QR login through the pinned Telethon fork, fully
  programmatic (never the console-prompting `client.start()`).
- **Credentials**: `.env` writes are atomic (temp file → `fsync` → `chmod 0600`
  → `os.replace`); secrets are written server-side and never returned in a
  response body.
- **Logs**: live SSE stream at `/api/logs`, with every line passed through a
  `SecretRedactor` before it reaches clients. No OpenAPI/docs endpoints.
- **Rate limiting**: phone-OTP and QR endpoints are per-IP rate-limited; login
  sessions lazy-expire after 10 minutes.
- **Binding / keep-alive**: `SETUP_BIND` (default `0.0.0.0`) and `PORT`
  (default `7860`); an optional Hugging Face keep-alive pinger is configured
  via `SPACE_URL`, `HF_TOKEN`, `KEEP_ALIVE`, `KEEP_ALIVE_MIN_SECONDS`,
  `KEEP_ALIVE_MAX_SECONDS`.

When both `python3 -m pyUltroid` and `server.py` run (the `start.sh` layout),
exactly one bot starts — the bootstrap waiter uses a PID marker, and the loser
retries the bind. See `pyUltroid/webserver/README.md` for the full
loop/thread and security model.

---

## Rich formats

SarahUB builds richer Telegram output through three cooperating layers.

### Native rich messages — `pyUltroid._core.richmsg`

A `RichBlocks` builder over the fork's native `RichMessage` schema
(`InputRichMessageHTML` / `InputRichMessageMarkdown`), with `send_rich` /
`edit_rich` helpers and a guaranteed plain-text fallback when the peer/client
can't render native rich messages (Telegram Premium server rendering and the
viewer's client capability are outside the library's control):

- **Blocks**: tables, expandable blockquotes, headers, lists, code, and media.
- **Fallback**: every block set is also rendered to plain text + entities, so
  the message degrades gracefully instead of failing.
- **Routing**: `rich_sender` re-targets fresh sends through the assistant bot
  when the caller is a user account, so native rich messages and inline
  keyboards actually render and fire callbacks.

### Styled buttons — `custom.tg.ikm` / `custom.rich.button`

`ikm` builds inline keyboards from tuples and is the canonical button builder
used across the codebase:

```python
from custom.tg import ikm

# Colour-styled buttons; style in {"default","primary","success","danger"}
buttons = ikm([("Confirm", "yes", "success"), ("Cancel", "no", "danger")])

# URL, switch-inline, and WebView buttons
ikm([("Support", "url", "https://t.me/UltroidSupportChat")])
ikm([("Open app", "web_app", "https://example.com/app")])

# auto-prefix matching emoji icons ("◀️ Back", …)
ikm([("Back", "back")], icon=True)
```

`custom.rich` adds named presets (`STYLE_PRESETS`), `resolve_style`, the
one-button `button` builder, and `rich_ikm`. Pre-built Telethon `Button`
objects pass through untouched, so URL/switch-inline buttons can be mixed with
tuple specs freely.

> Button styles and custom-emoji icons only **render** when the sending account
> has Telegram Premium (and the emoji is from a pack the account may use);
> delivery rules are enforced server-side.

### Text & premium emoji — `custom.rich`

- `parse_rich` / `unparse_rich` — MarkdownV3 parsing with native
  `||spoiler||` support (plus `markdown`, `markdownv2`, and `html`).
- `premium_emoji` / `with_emojis` / `emoji_entity` — build raw
  `MessageEntityCustomEmoji` entities with Telegram-correct UTF-16 offsets.

### Mini Apps / WebView buttons

Plugins can open platform Mini Apps or generic web pages:

```python
from custom.tg import ikm, web_app_button

ikm([("Open App", "web_app", "https://example.com/app")])
web_app_button("Open App", "https://example.com/app", simple=True)
```

Incoming `web_app_data` service messages are captured by `plugins/webtools.py`.

---

## AI agent

The agent layer (`pyUltroid/agent`, commands `.agent` / `.ai` / `.ask` in
`plugins/brain.py`) provides:

- **Provider registry**: OpenAI (including GPT-5/Codex Responses), Anthropic,
  Gemini, Groq, xAI/Grok, apmix.ai, Meta/Llama, Ollama, Cloudflare, and custom
  OpenAI-compatible endpoints.
- **`AIClient` SDK**: a provider-neutral client with `verify()`, `chat()`,
  `search()`, `stream()`, and `agent()` methods.
- **MiniAI SDK**: a lightweight classifier that routes intents, accepts explicit
  tool-call continuations, and checks a cached response layer.
- **Tool executor**: every tool declares required capability tokens
  (`TOOL_REQUIRED_CAPS`); the default grant excludes destructive operations
  (e.g. `update_check(apply=True)`, on-demand installs, plugin load/unload,
  process kill, filesystem access).
- **Shell timeout clamping**: agent shell commands are clamped to `[1, 120]`s.

Configure the matching provider keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
`GEMINI_API_KEY` (or `GOOGLE_API_KEY`), `GROQ_API_KEY`, `XAI_API_KEY`,
`META_LLAMA_API_KEY`, or `APIMIX_API_KEY` (apmix.ai; the base URL defaults to
`https://api.apmix.ai/v1` and can be overridden with `APIMIX_BASE_URL`).
Custom compatible services use `COMPAT_BASE_URL` with an
optional `COMPAT_API_KEY` and `COMPAT_MODEL`. Leave cloud keys unset to route to
a local Ollama instance. Use `.aiproviders` to inspect providers and
`.aiverify [provider]` for a read-only credential check.

---

## Business bot prerequisite

Business mode requires the `asst` bot to be connected as a **Business bot** to
a business account through Telegram's business settings. This is a one-time
manual step; until it's done, business commands report `BOT_ACCESS_RESTRICTED`.
There is no code-level bypass.

---

## Verification & testing

```bash
python -m pytest tests/ -q
python smoke_test.py
```

CI runs the same two steps on a Python version matrix (3.10–3.13; 3.14 is an
experimental / continue-on-error job). The test suite is credential-free.

---

## Project structure & docs

- `pyUltroid/` — core runtime (startup, dispatcher, database, `_core` helpers).
- `plugins/` — user-facing commands; `assistant/` — bot/inline/callback handlers.
- `custom/` — `tg` (buttons/keyboards), `rich` (rich messages/emoji), `fmt`.
- `vc/` — voice-chat music stack (`py-tgcalls`).
- `docs/` — architecture, setup, migration, and release-hygiene notes.
- `pyUltroid/webserver/README.md` — web-setup server internals.
- `tests/README.md` — how the credential-free harness works.
- `CHANGELOG.md` — release history and version rationale.
- `.env.sample` — copy to `.env` and fill in your own values.

## Changelog

See [CHANGELOG.md](./CHANGELOG.md).

## License

AGPL-3.0-or-later.

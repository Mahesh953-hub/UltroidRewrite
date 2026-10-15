# pyUltroid/webserver — SarahUB web setup server

Replaces the old `/status`-only `server.py` stub with a token-gated setup
server: API keys form, phone-OTP and QR login (pinned Telethon fork),
atomic `.env` writes, live SSE logs, and a completion → restart handshake.

## Entry points

| Entry | Mode | Completion behaviour |
|---|---|---|
| `python server.py` (foreground process in `start.sh`) | standalone | spawns `python -m pyUltroid` as a child, keeps serving `/status` + keep-alive |
| `python -m pyUltroid` with missing config (the runtime's `gate_web_setup` step) | embedded | main thread blocks on the completion event, then `os.execl(python -m pyUltroid)` |

If both run at once (`start.sh`): whoever binds the port first owns setup.
The loser behaviour is role-aware — an embedded bootstrap parks in a
PID-marker wait loop and boots once the config appears; `server.py`
retries the bind (it must stay alive as the container's foreground
process), and consults the marker before spawning so **exactly one bot**
starts.

## Event-loop / thread model

* Exactly **one asyncio loop** owns HTTP *and* the Telethon login clients:
  the uvicorn server loop. Standalone mode: main thread via `uvicorn.run`.
  Embedded mode: a daemon thread via `uvicorn.Server.run()`
  (`runner.ThreadedServer`); uvicorn installs signal handlers only on the
  main thread, so this is safe.
* The bot's loop (`startup/BaseClient`'s module-level loop under a uvloop
  policy) is never touched during setup: embedded mode re-execs before the
  bot bootstrap begins; standalone mode runs the bot in a child process.
* Telethon login is fully programmatic (`connect` →
  `send_code_request`/`sign_in`/`qr_login`/`QRLogin.wait`) — never
  `client.start()`, whose console prompts die on the A14 input()-kill shim
  (`startup/_extra._ask_input`).
* Logs: `logging.Handler.emit` runs in the *emitting* thread and only does
  `loop.call_soon_threadsafe(queue.put_nowait, line)` per subscriber — the
  single thread-safe cross-loop primitive. No locks across loop calls, no
  blocking on the server loop.
* The HF keep-alive pinger stays a plain daemon `threading.Thread` using
  blocking `requests` — deliberately off the asyncio loop.

SSE was chosen over WebSocket: zero new dependencies, proxy/health-check
friendly, and the stream is strictly server→client.

## Security model

* One-time setup token generated per process (or `SETUP_TOKEN` env),
  printed to the terminal once, required on every `/api/*` route via
  `X-Setup-Token` header or `?token=` query param (EventSource/`<img>`
  cannot set headers). Constant-time comparison. Enforced by a pure-ASGI
  middleware (runs before body parsing; streams SSE untouched) plus
  per-route checks as defense in depth.
* No OpenAPI docs (`docs_url`/`redoc_url`/`openapi_url` all disabled).
* `.env` writes are atomic (temp file → fsync → chmod 0600 →
  `os.replace`); secrets are written server-side and never returned in any
  response body. `SESSION` lands in `.env` directly from the login route.
  Values containing spaces/quotes/backslashes round-trip exactly
  (single-pair unquoting + sequential escape resolution).
* Every log line passes through the `SecretRedactor` (API hash, bot token,
  session string, 2FA password, phone number, setup token) before reaching
  SSE clients. Login-error log lines redact the exception text and never
  attach `exc_info`.
* Phone/OTP **and** QR-password endpoints are rate-limited per client IP;
  login sessions lazy-expire after 10 minutes (Telegram's code/QR tokens
  expire anyway) and are swept whenever a new login starts.
* Login client lifecycle: every Telethon client is disconnected exactly
  once — on success, on terminal failure (expired/restarted code, dead QR
  wait), or on TTL expiry/abandonment (scheduled back onto the client's
  home loop). Retryable errors (wrong code, wrong 2FA password) keep the
  session. After `SessionPasswordNeededError` the consumed OTP is never
  resubmitted — the 2FA step is password-only (real Telethon attempts a
  presented code before any password fallback, so a consumed code would
  fail the whole call).
* `LOG_CHANNEL=0` sentinel when the user skips it: verified crash-safe on
  the boot path — `invite_asst_to_log` try/excepts it, and `autopilot()`
  treats the unresolvable entity as "not found" and auto-creates a real
  log channel (or falls back to Saved Messages) before `ready()`/
  `customize()` consume it. Trade-off of skipping LOG_CHANNEL: a fresh
  auto-created log group per restart (env re-sync overwrites the healed
  value) — the UI therefore recommends providing one.
* Bind address is `SETUP_BIND` (default `0.0.0.0` so HF Spaces / VPS web
  views work — the token is the access control). Set
  `SETUP_BIND=127.0.0.1` for local-only setups. Port: `PORT` (default
  7860, the HF Space convention).

## Env configuration

Server: `SETUP_BIND`, `PORT`, `SETUP_TOKEN`, `SETUP_NO_SPAWN`,
`SETUP_WAIT_TIMEOUT`, `PYULTROID_ENV_FILE` (tests).

Keep-alive: `SPACE_URL`, `HF_TOKEN`, `KEEP_ALIVE`,
`KEEP_ALIVE_MIN_SECONDS`, `KEEP_ALIVE_MAX_SECONDS` — see `keepalive.py`.

## QR rendering

`GET /api/login/qr/{id}/image` serves a PNG only when the optional
`qrcode` package is importable (it lives in the pyproject `[plugins]`
extra, deliberately not a hard dep). Otherwise the UI falls back to the
`tg://login?token=…` URL as copyable text. QR acceptance is inherently
partial in a sandbox — a human phone-scan is not automatable; tests assert
the URL, expiry, and recreate cycle instead.

# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""Mini App settings dashboard.

Four classes, each answering to a different collaborator:

* :class:`MemoryStore` is the fallback key/value store used when no live
  ``udB`` exists. It used to be deleted from this module while two live
  references to it stayed behind, which turned the documented "degrade
  gracefully outside ``python -m pyUltroid``" path into a ``NameError``.
* :class:`MiniAppStore` owns *storage semantics*: the persisted token, the
  validated UI preferences, and the toggle rows read out of the manifest
  core.registry.
* :class:`MiniAppServer` is the process-wide supervisor that starts at most
  one uvicorn thread - the lock and the cached server are instance state
  now, not module globals.
* :class:`MiniAppApplication` builds the FastAPI app and its three routes.
"""

from __future__ import annotations

import logging
import threading
from types import TracebackType
from typing import Any, Final

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .runner import PortProbe, ThreadedServer
from .security import AUTHORITY, TokenAuthority

LOGS: logging.Logger = logging.getLogger("pyUltroid.webserver.miniapp")

MINIAPP_TOKEN_KEY: Final[str] = "MINIAPP_TOKEN"
MINIAPP_URL_KEY: Final[str] = "MINIAPP_URL"
TOKEN_HEADER: Final[str] = "X-Miniapp-Token"
TOKEN_QUERY_PARAM: Final[str] = "token"

THEMES: Final[tuple[str, ...]] = ("default", "dark")
BUTTON_STYLES: Final[tuple[str, ...]] = ("classic", "styled")

DEFAULT_THEME: Final[str] = "default"
DEFAULT_BUTTON_STYLE: Final[str] = "classic"
DEFAULT_CARDS: Final[bool] = True

DEFAULT_MINIAPP_PORT: Final[int] = 7861
DEFAULT_BIND: Final[str] = "0.0.0.0"


class MemoryStore:
    """In-memory ``get_key`` / ``set_key`` / ``del_key`` stand-in for ``udB``.

    Only used when the bot is not running in-process (local development,
    tests, plugin surfaces). Explicit about being ephemeral: nothing here
    survives a restart.
    """

    __slots__ = ("_data", "_lock")

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get_key(self, key: str) -> Any:
        with self._lock:
            return self._data.get(key)

    def set_key(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value

    def del_key(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __repr__(self) -> str:
        return f"<MemoryStore keys={len(self._data)}>"


class MiniAppStore:
    """Storage semantics for the dashboard: token, UI prefs, toggle rows.

    A Mini App token is re-read from the database on *every* request, which
    is what makes rotation take effect immediately instead of at the next
    restart.
    """

    __slots__ = ("db", "registry", "live_db", "_warned")

    def __init__(self, db: Any = None, registry: Any = None) -> None:
        self.db, self.live_db = self._resolve_db(db)
        self.registry = self._resolve_registry(registry)
        self._warned = False

    # -- collaborators

    @staticmethod
    def _resolve_db(explicit: Any = None) -> tuple[Any, bool]:
        if explicit is not None:
            return explicit, True
        try:
            from pyUltroid import udB as live_db  # noqa: PLC0415

            if live_db is not None:
                return live_db, True
        except Exception:
            pass
        return MemoryStore(), False

    @staticmethod
    def _resolve_registry(explicit: Any = None) -> Any:
        if explicit is not None:
            return explicit
        from pyUltroid.ultroid import core

        return core.manifest

    def warn_if_ephemeral(self) -> None:
        if self.live_db or self._warned:
            return
        self._warned = True
        LOGS.warning(
            "miniapp: pyUltroid.udB unavailable  -  using an in-memory "
            "fallback store (expected outside `python -m pyUltroid`)."
        )

    # -- token

    def ensure_token(self) -> str:
        """The persisted token, minting and storing one on first use."""
        token = self.db.get_key(MINIAPP_TOKEN_KEY)
        if not token:
            token = AUTHORITY.mint(track=False)
            self.db.set_key(MINIAPP_TOKEN_KEY, token)
        return token

    def rotate_token(self) -> str:
        """Replace the token. Every previously issued link stops working."""
        token = AUTHORITY.mint(track=False)
        self.db.set_key(MINIAPP_TOKEN_KEY, token)
        return token

    def adopt_token(self, token: str) -> str:
        """Force a caller-supplied token to be the persisted one."""
        self.db.set_key(MINIAPP_TOKEN_KEY, token)
        return token

    # -- preferences

    def ui_state(self) -> dict[str, Any]:
        """Validated theme / cards / buttons, falling back to the defaults."""
        theme = self.db.get_key("UI_THEME")
        if theme not in THEMES:
            theme = DEFAULT_THEME
        cards = self.db.get_key("UI_CARDS")
        if not isinstance(cards, bool):
            cards = DEFAULT_CARDS
        buttons = self.db.get_key("UI_BUTTONS")
        if buttons not in BUTTON_STYLES:
            buttons = DEFAULT_BUTTON_STYLE
        return {"theme": theme, "cards": cards, "buttons": buttons}

    def toggle_rows(self) -> list[dict[str, Any]]:
        """The manifest's toggle entries, one bad row skipped, not fatal."""
        toggles: list[dict[str, Any]] = []
        entries = getattr(self.registry, "_entries", None)
        if not isinstance(entries, dict):
            return toggles
        for tkey, entry in entries.items():
            try:
                if entry.get("type") != "toggle":
                    continue
                toggles.append(
                    {
                        "key": tkey,
                        "label": entry.get("label", tkey),
                        "plugin": entry.get("plugin", ""),
                        "icon": entry.get("icon", ""),
                        "description": entry.get("description", ""),
                        "enabled": bool(self.registry.get_state(tkey)),
                    }
                )
            except Exception:  # never let one bad entry break the dashboard
                LOGS.debug("miniapp: skipping malformed manifest entry %r", tkey)
        toggles.sort(key=lambda row: row["key"])
        return toggles

    def snapshot(self) -> dict[str, Any]:
        """Everything the dashboard renders, in one object."""
        return {
            "ui": self.ui_state(),
            "themes": list(THEMES),
            "button_styles": list(BUTTON_STYLES),
            "pmf_toggles": self.toggle_rows(),
        }

    # -- writes

    def cycle_theme(self) -> str:
        return self._cycle("UI_THEME", THEMES, DEFAULT_THEME)

    def cycle_buttons(self) -> str:
        return self._cycle("UI_BUTTONS", BUTTON_STYLES, DEFAULT_BUTTON_STYLE)

    def flip_cards(self) -> bool:
        current = self.db.get_key("UI_CARDS")
        nxt = not (current if isinstance(current, bool) else DEFAULT_CARDS)
        self.db.set_key("UI_CARDS", nxt)
        return nxt

    async def flip_manifest_toggle(self, key: str) -> None:
        """Delegates to the manifest registry; raises on a bad key."""
        await self.registry.flip_toggle(key)

    def _cycle(self, key: str, options: tuple[str, ...], default: str) -> str:
        current = self.db.get_key(key)
        if current not in options:
            current = default
        nxt = options[(options.index(current) + 1) % len(options)]
        self.db.set_key(key, nxt)
        return nxt

    def __repr__(self) -> str:
        kind = "live" if self.live_db else "memory"
        return f"<MiniAppStore {kind} db={self.db!r}>"


class MiniAppServer:
    """Supervises at most one in-process Mini App server.

    The lock, the cached handle and the port decision all live on the
    instance, so tests get an isolated supervisor simply by constructing
    their own instead of resetting module globals.
    """

    __slots__ = ("_lock", "_server")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._server: ThreadedServer | None = None

    @property
    def server(self) -> ThreadedServer | None:
        return self._server

    def running(self) -> bool:
        return self._server is not None

    def ensure(
        self,
        app: FastAPI,
        host: str = DEFAULT_BIND,
        port: int = DEFAULT_MINIAPP_PORT,
    ) -> ThreadedServer | None:
        """Idempotently start *app*. Best-effort: never raises."""
        with self._lock:
            if self._server is not None:
                return self._server
            if not PortProbe().bindable(host, port):
                LOGS.warning(
                    "miniapp: %s:%s already in use  -  not starting a second Mini "
                    "App server.",
                    host,
                    port,
                )
                return None
            self._server = ThreadedServer(app, host, port).start()
            return self._server

    def stop(self, timeout: float | None = None) -> bool:
        with self._lock:
            server, self._server = self._server, None
        return server.stop(timeout) if server is not None else False

    def __enter__(self) -> "MiniAppServer":
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.stop()

    def __repr__(self) -> str:
        server = self._server
        target = f"{server.host}:{server.port}" if server is not None else "none"
        return f"<MiniAppServer {target}>"


class TogglePayload(BaseModel):
    kind: str
    key: str | None = None


class MiniAppState:
    """The handle the FastAPI app exposes as ``app.state.miniapp``."""

    __slots__ = ("store", "authority")

    def __init__(self, store: MiniAppStore, authority: TokenAuthority) -> None:
        self.store = store
        self.authority = authority

    @property
    def db(self) -> Any:
        return self.store.db

    @property
    def registry(self) -> Any:
        return self.store.registry

    @property
    def live_udb(self) -> bool:
        return self.store.live_db

    def __repr__(self) -> str:
        return f"<MiniAppState {self.store!r}>"


class MiniAppApplication:
    """The Mini App FastAPI app: one page, one state route, one toggle route."""

    __slots__ = ("store", "authority", "page")

    def __init__(
        self,
        *,
        token: str | None = None,
        udb: Any = None,
        registry: Any = None,
        authority: TokenAuthority | None = None,
        page: str | None = None,
    ) -> None:
        self.authority = authority if authority is not None else AUTHORITY
        self.store = MiniAppStore(udb, registry)
        self.store.warn_if_ephemeral()
        if token:
            self.store.adopt_token(token)
        else:
            self.store.ensure_token()
        self.page = page if page is not None else _PAGE_HTML

    # -- collaborators

    @property
    def state(self) -> MiniAppState:
        return MiniAppState(self.store, self.authority)

    def authorized(self, request: Request) -> bool:
        """Re-resolve the expected token each time, so rotation is instant."""
        provided = request.headers.get(TOKEN_HEADER) or request.query_params.get(
            TOKEN_QUERY_PARAM
        )
        return self.authority.verify(provided, self.store.ensure_token())

    # -- build

    def build(self) -> FastAPI:
        app = FastAPI(
            title="Ultroid Mini App",
            version="1.0.0",
            docs_url=None,
            redoc_url=None,
            openapi_url=None,
        )
        app.state.miniapp = self.state

        @app.middleware("http")
        async def _security_headers(request: Request, call_next: Any) -> Any:
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "no-referrer"
            return response

        @app.get("/settings")
        def settings_page(request: Request):
            if not self.authorized(request):
                return _unauthorized()
            return HTMLResponse(self.page)

        @app.get("/settings/api/state")
        def get_state(request: Request):
            if not self.authorized(request):
                return _unauthorized()
            return self.store.snapshot()

        @app.post("/settings/api/toggle")
        async def post_toggle(payload: TogglePayload, request: Request):
            if not self.authorized(request):
                return _unauthorized()
            failure = await self._apply(payload)
            if failure is not None:
                return failure
            return self.store.snapshot()

        return app

    async def _apply(self, payload: TogglePayload) -> JSONResponse | None:
        """Perform the requested toggle; ``None`` means it succeeded."""
        kind = payload.kind
        if kind == "ui_theme":
            self.store.cycle_theme()
        elif kind == "ui_cards":
            self.store.flip_cards()
        elif kind == "ui_buttons":
            self.store.cycle_buttons()
        elif kind == "pmf":
            if not payload.key:
                return JSONResponse(
                    {"detail": "key is required for kind=pmf"}, status_code=422
                )
            try:
                await self.store.flip_manifest_toggle(payload.key)
            except Exception:
                LOGS.exception("miniapp: flip_toggle failed for %r", payload.key)
                return JSONResponse({"detail": "toggle failed"}, status_code=500)
        else:
            return JSONResponse({"detail": f"unknown kind {kind!r}"}, status_code=422)
        return None

    def __repr__(self) -> str:
        return f"<MiniAppApplication {self.store!r}>"


# -- the process-wide supervisor ----------------------------------------

SUPERVISOR: Final[MiniAppServer] = MiniAppServer()


# -- module surface -------------------------------------------------------


def _unauthorized() -> JSONResponse:
    return JSONResponse({"detail": "invalid or missing miniapp token"}, status_code=401)


def _fallback_kv() -> MemoryStore:
    """A fresh ephemeral store (the historical module-global factory)."""
    return MemoryStore()


def _extract_token(request: Request) -> str | None:
    return request.headers.get(TOKEN_HEADER) or request.query_params.get(
        TOKEN_QUERY_PARAM
    )


def _ui_state(udb) -> dict:
    return MiniAppStore(udb).ui_state()


def _pmf_state(registry) -> list[dict]:
    # An explicit throwaway store: the db is never read on this path, and
    # leaving it implicit would reach for ``pyUltroid.udB`` for nothing.
    return MiniAppStore(MemoryStore(), registry).toggle_rows()


def snapshot(udb, registry) -> dict:
    store = MiniAppStore(udb, registry)
    return store.snapshot()


def ensure_token(udb) -> str:
    """Return the persisted Mini App token, generating one on first use."""
    return MiniAppStore(udb).ensure_token()


def rotate_token(udb) -> str:
    return MiniAppStore(udb).rotate_token()


def create_miniapp(*, token: str | None = None, udb=None, registry=None) -> FastAPI:
    """Build the Mini App (the historical entry point)."""
    return MiniAppApplication(token=token, udb=udb, registry=registry).build()


def ensure_running(
    app: FastAPI, host: str = DEFAULT_BIND, port: int = DEFAULT_MINIAPP_PORT
) -> ThreadedServer | None:
    """Idempotently start *app* on the process-wide supervisor.

    Returns the running server, or ``None`` when the port is already taken
    by something else (best-effort - never raises).
    """
    return SUPERVISOR.ensure(app, host, port)


# -- the SPA itself (single self-contained HTML file) -------------------

_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<meta name="referrer" content="no-referrer">
<meta http-equiv="Cache-Control" content="no-store">
<title>Ultroid Settings</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
  :root {
    --bg: #f6f6fb;
    --text: #17181a;
    --hint: #8e8e93;
    --link: #5b5bf5;
    --button: #5b5bf5;
    --button-text: #ffffff;
    --secondary-bg: #ffffff;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    padding: 14px;
    line-height: 1.4;
  }
  h1 { font-size: 18px; font-weight: 650; margin: 4px 0 14px; letter-spacing: -0.01em; }
  .card {
    background: var(--secondary-bg);
    border: 1px solid rgba(120,120,128,0.14);
    border-radius: 14px;
    box-shadow: 0 1px 2px rgba(20,20,40,0.04), 0 6px 16px -10px rgba(20,20,40,0.12);
    padding: 6px 14px;
    margin-bottom: 12px;
  }
  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 10px 0;
    border-bottom: 1px solid rgba(120,120,128,0.16);
  }
  .row:last-child { border-bottom: none; }
  .row .label { font-size: 15px; font-weight: 550; }
  .row .sub { font-size: 12px; color: var(--hint); margin-top: 2px; }
  button.pill {
    border: 1px solid transparent;
    border-radius: 999px;
    padding: 7px 16px;
    font: inherit;
    font-size: 13px;
    font-weight: 600;
    background: var(--button);
    color: var(--button-text);
    cursor: pointer;
    transition: opacity 0.15s ease, transform 0.05s ease;
  }
  button.pill:active { transform: translateY(1px); opacity: 0.9; }
  button.pill.off {
    background: rgba(120,120,128,0.16);
    color: var(--text);
    border-color: rgba(120,120,128,0.22);
  }
  #status { font-size: 12px; color: var(--hint); margin-top: 10px; text-align: center; }
  #error {
    color: #cc2929;
    background: rgba(204,41,41,0.08);
    border: 1px solid rgba(204,41,41,0.2);
    border-radius: 10px;
    font-size: 13px;
    padding: 8px 12px;
    margin: 0 0 10px;
    display: none;
  }
</style>
</head>
<body>
  <h1>&#9881; SarahUB Settings</h1>
  <div id="error"></div>
  <div class="card">
    <div class="row">
      <div>
        <div class="label">Theme</div>
        <div class="sub">Overall UI color scheme</div>
      </div>
      <button class="pill" id="btn-theme" data-kind="ui_theme">&hellip;</button>
    </div>
    <div class="row">
      <div>
        <div class="label">Rich Cards</div>
        <div class="sub">PIL-rendered cards vs plain rich messages</div>
      </div>
      <button class="pill" id="btn-cards" data-kind="ui_cards">&hellip;</button>
    </div>
    <div class="row">
      <div>
        <div class="label">Buttons</div>
        <div class="sub">Classic vs styled inline buttons</div>
      </div>
      <button class="pill" id="btn-buttons" data-kind="ui_buttons">&hellip;</button>
    </div>
  </div>
  <div class="card" id="pmf-card" style="display:none;">
    <div class="label" style="margin-bottom: 6px;">Plugin Toggles</div>
    <div id="pmf-list"></div>
  </div>
  <div id="status">loading&hellip;</div>

<script>
(function () {
  "use strict";
  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    try {
      tg.ready();
      tg.expand();
      var tp = tg.themeParams || {};
      var map = {
        bg_color: "--bg", text_color: "--text", hint_color: "--hint",
        link_color: "--link", button_color: "--button",
        button_text_color: "--button-text", secondary_bg_color: "--secondary-bg"
      };
      Object.keys(map).forEach(function (k) {
        if (tp[k]) document.documentElement.style.setProperty(map[k], tp[k]);
      });
      tg.onEvent("themeChanged", function () {
        var tp2 = tg.themeParams || {};
        Object.keys(map).forEach(function (k) {
          if (tp2[k]) document.documentElement.style.setProperty(map[k], tp2[k]);
        });
      });
    } catch (e) { /* best-effort only */ }
  }

  var params = new URLSearchParams(window.location.search);
  var token = params.get("token") || "";
  // Read the token into memory (used for every subsequent /settings/api/*
  // call via the X-Miniapp-Token header), then immediately scrub it from
  // the visible address bar. history.replaceState does not re-request the
  // page, so `token` above stays intact for the rest of this session.
  if (token && window.history && window.history.replaceState) {
    try {
      window.history.replaceState(null, "", window.location.pathname);
    } catch (e) { /* best-effort only */ }
  }
  var statusEl = document.getElementById("status");
  var errorEl = document.getElementById("error");

  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.style.display = "block";
  }

  function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({ "X-Miniapp-Token": token }, opts.headers || {});
    return fetch(path, opts).then(function (res) {
      if (res.status === 401) throw new Error("Unauthorized  -  reopen from the bot.");
      if (!res.ok) throw new Error("Request failed (" + res.status + ")");
      return res.json();
    });
  }

  function pill(el, on, onText, offText) {
    el.textContent = on ? onText : offText;
    el.classList.toggle("off", !on);
  }

  function render(state) {
    pill(document.getElementById("btn-theme"), state.ui.theme === "dark", "dark", "default");
    pill(document.getElementById("btn-cards"), state.ui.cards, "on", "off");
    pill(document.getElementById("btn-buttons"), state.ui.buttons === "styled", "styled", "classic");

    var list = document.getElementById("pmf-list");
    var card = document.getElementById("pmf-card");
    list.innerHTML = "";
    if (state.pmf_toggles && state.pmf_toggles.length) {
      card.style.display = "block";
      state.pmf_toggles.forEach(function (t) {
        var row = document.createElement("div");
        row.className = "row";
        var left = document.createElement("div");
        left.innerHTML = '<div class="label">' + (t.icon ? t.icon + " " : "") +
          escapeHtml(t.label) + '</div><div class="sub">' + escapeHtml(t.plugin) + '</div>';
        var btn = document.createElement("button");
        btn.className = "pill" + (t.enabled ? "" : " off");
        btn.textContent = t.enabled ? "on" : "off";
        btn.onclick = function () { toggle("pmf", t.key); };
        row.appendChild(left);
        row.appendChild(btn);
        list.appendChild(row);
      });
    } else {
      card.style.display = "none";
    }
    statusEl.textContent = "synced";
  }

  function escapeHtml(s) {
    var d = document.createElement("div");
    d.textContent = s || "";
    return d.innerHTML;
  }

  function toggle(kind, key) {
    statusEl.textContent = "saving\u2026";
    var body = { kind: kind };
    if (key) body.key = key;
    api("/settings/api/toggle", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(render)
      .catch(function (err) { showError(err.message); statusEl.textContent = "error"; });
  }

  ["btn-theme", "btn-cards", "btn-buttons"].forEach(function (id) {
    document.getElementById(id).addEventListener("click", function () {
      toggle(this.getAttribute("data-kind"), null);
    });
  });

  api("/settings/api/state").then(render).catch(function (err) {
    showError(err.message);
    statusEl.textContent = "error";
  });
})();
</script>
</body>
</html>
"""

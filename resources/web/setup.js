/* SarahUB setup UI (W2-A). Vanilla JS, no external assets (offline-safe).
 * Token comes from the terminal-printed URL (?token=…) and is cached in
 * sessionStorage; API calls send it as X-Setup-Token, EventSource/<img>
 * use the ?token= query param.
 *
 * All paths are resolved relative to a base derived once from the page's
 * own URL (never hard-coded absolute paths like "/api/..."). This keeps
 * the app subpath-proof behind a reverse proxy / platform path prefix
 * (e.g. Hugging Face Spaces), where absolute "/..." URLs would resolve
 * against the proxy root instead of the app's mount point. */
(() => {
  "use strict";

  const BASE = new URL(".", location.href);
  const apiUrl = (path) => new URL(path, BASE).href;

  const params = new URLSearchParams(location.search);
  let token = params.get("token") || sessionStorage.getItem("setup_token") || "";
  if (token) sessionStorage.setItem("setup_token", token);

  const $ = (id) => document.getElementById(id);
  const show = (el) => el.classList.remove("hidden");
  const hide = (el) => el.classList.add("hidden");
  const say = (el, msg) => { el.textContent = msg; };

  let loginId = null;        // in-progress phone login
  let lastCode = null;       // phone code (re-sent with 2FA password)
  let qrLoginId = null;      // in-progress QR login
  let qrSource = null;       // EventSource for QR wait
  let passwordFor = null;    // "phone" | "qr" — which flow needs 2FA

  if (!token) show($("token-banner"));

  // ---- step indicator -----------------------------------------------------
  const STEP_ORDER = ["keys", "login", "complete"];
  function setActiveStep(name) {
    const idx = STEP_ORDER.indexOf(name);
    if (idx < 0) return;
    STEP_ORDER.forEach((step, i) => {
      const li = document.querySelector(`.step[data-step="${step}"]`);
      if (!li) return;
      li.classList.toggle("is-done", i < idx);
      li.classList.toggle("is-current", i === idx);
      const num = li.querySelector(".step-num");
      if (num) num.textContent = i < idx ? "\u2713" : String(i + 1);
    });
  }

  async function api(path, options = {}) {
    const headers = Object.assign(
      { "X-Setup-Token": token, "Content-Type": "application/json" },
      options.headers || {}
    );
    const resp = await fetch(apiUrl(path), Object.assign({}, options, { headers }));
    let body = null;
    try { body = await resp.json(); } catch (e) { /* non-JSON */ }
    return { status: resp.status, body };
  }

  function detail(body, fallback) {
    return (body && body.detail) ? body.detail : fallback;
  }

  // ---- state ------------------------------------------------------------
  async function refreshState() {
    const { status, body } = await api("api/state");
    if (status === 401) {
      say($("status-line"), "unauthorized — check the ?token= in your URL");
      show($("token-banner"));
      return;
    }
    if (!body) { say($("status-line"), "server unreachable"); return; }
    say($("status-line"),
        body.setup_required ? "setup required" : "configured ✔");
    if (body.has_api_keys) { show($("step-login")); setActiveStep("login"); }
    if (body.has_session) { show($("step-complete")); setActiveStep("complete"); }
  }

  // ---- step 1: keys ------------------------------------------------------
  $("keys-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const form = new FormData(ev.target);
    const payload = {
      api_id: parseInt(form.get("api_id"), 10),
      api_hash: String(form.get("api_hash") || "").trim(),
    };
    const botToken = String(form.get("bot_token") || "").trim();
    const logChannel = String(form.get("log_channel") || "").trim();
    if (botToken) payload.bot_token = botToken;
    if (logChannel) payload.log_channel = parseInt(logChannel, 10);
    say($("keys-result"), "saving…");
    const { status, body } = await api("api/setup/keys", {
      method: "POST", body: JSON.stringify(payload),
    });
    if (status === 200) {
      say($("keys-result"), "saved ✔ — now log in below");
      show($("step-login"));
      setActiveStep("login");
    } else {
      say($("keys-result"), "failed: " + detail(body, `HTTP ${status}`));
    }
  });

  // ---- step 2: tabs -------------------------------------------------------
  $("tab-phone").addEventListener("click", () => {
    $("tab-phone").classList.add("active");
    $("tab-qr").classList.remove("active");
    show($("pane-phone")); hide($("pane-qr"));
  });
  $("tab-qr").addEventListener("click", () => {
    $("tab-qr").classList.add("active");
    $("tab-phone").classList.remove("active");
    show($("pane-qr")); hide($("pane-phone"));
  });

  function onLoggedIn() {
    say($("login-result"), "logged in ✔ — session saved on the server");
    show($("step-complete"));
    setActiveStep("complete");
  }

  // ---- step 2a: phone ------------------------------------------------------
  $("phone-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const phone = String(new FormData(ev.target).get("phone") || "").trim();
    say($("login-result"), "sending code…");
    const { status, body } = await api("api/login/phone", {
      method: "POST", body: JSON.stringify({ phone }),
    });
    if (status === 200) {
      loginId = body.login_id;
      say($("login-result"), `code sent (${body.code_type}) — check Telegram`);
      show($("otp-form"));
    } else {
      say($("login-result"), "failed: " + detail(body, `HTTP ${status}`));
    }
  });

  $("otp-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const code = String(new FormData(ev.target).get("code") || "").trim();
    lastCode = code;
    say($("login-result"), "signing in…");
    const { status, body } = await api("api/login/otp", {
      method: "POST", body: JSON.stringify({ login_id: loginId, code }),
    });
    if (status === 200) { onLoggedIn(); return; }
    if (status === 428) {
      passwordFor = "phone";
      say($("login-result"), "two-factor password required");
      show($("password-form"));
      return;
    }
    say($("login-result"), "failed: " + detail(body, `HTTP ${status}`));
  });

  $("password-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const password = String(new FormData(ev.target).get("password") || "");
    const path = passwordFor === "qr"
      ? `api/login/qr/${qrLoginId}/password`
      : "api/login/otp";
    const payload = passwordFor === "qr"
      ? { password }
      : { login_id: loginId, code: lastCode || "", password };
    const { status, body } = await api(path, {
      method: "POST", body: JSON.stringify(payload),
    });
    if (status === 200) { onLoggedIn(); hide($("password-form")); return; }
    say($("login-result"), "failed: " + detail(body, `HTTP ${status}`));
  });

  // ---- step 2b: QR -----------------------------------------------------------
  function renderQr(url, expires) {
    show($("qr-box"));
    $("qr-url").textContent = url;
    $("qr-expires").textContent = expires ? `expires ${expires}` : "";
    const img = $("qr-img");
    img.onerror = () => hide(img);   // qrcode package absent → URL text stays
    img.onload = () => show(img);
    img.src = apiUrl(`api/login/qr/${qrLoginId}/image`)
      + `?token=${encodeURIComponent(token)}&_=${Date.now()}`;
  }

  function startQrWait() {
    if (qrSource) qrSource.close();
    qrSource = new EventSource(
      apiUrl(`api/login/qr/${qrLoginId}/wait`) + `?token=${encodeURIComponent(token)}`);
    qrSource.addEventListener("expired", (ev) => {
      const data = JSON.parse(ev.data);
      renderQr(data.url, data.expires);   // auto-recreated token
      say($("login-result"), "QR expired — showing a fresh one");
    });
    qrSource.addEventListener("needs_password", () => {
      qrSource.close();
      passwordFor = "qr";
      say($("login-result"), "two-factor password required");
      show($("password-form"));
    });
    qrSource.addEventListener("success", () => {
      qrSource.close();
      onLoggedIn();
    });
    qrSource.addEventListener("error", (ev) => {
      if (ev.data) {
        say($("login-result"), "QR login failed: " + JSON.parse(ev.data).detail);
        qrSource.close();
      }
    });
  }

  $("qr-start").addEventListener("click", async () => {
    say($("login-result"), "generating QR…");
    const { status, body } = await api("api/login/qr", { method: "POST" });
    if (status !== 200) {
      say($("login-result"), "failed: " + detail(body, `HTTP ${status}`));
      return;
    }
    qrLoginId = body.login_id;
    renderQr(body.url, body.expires);
    startQrWait();
    say($("login-result"), "scan the QR with your phone");
  });

  // ---- step 3: complete -------------------------------------------------------
  $("complete-btn").addEventListener("click", async () => {
    say($("complete-result"), "finishing…");
    const { status, body } = await api("api/setup/complete", { method: "POST" });
    if (status === 200) {
      say($("complete-result"),
          `done ✔ — bot is starting (${body.mode}). This page may stop responding during restart.`);
    } else {
      say($("complete-result"), "failed: " + detail(body, `HTTP ${status}`));
    }
  });

  // ---- live logs ----------------------------------------------------------------
  function startLogs() {
    if (!token) return;
    const logs = $("logs");
    const src = new EventSource(apiUrl("api/logs") + `?token=${encodeURIComponent(token)}`);
    src.addEventListener("log", (ev) => {
      logs.textContent += ev.data + "\n";
      logs.scrollTop = logs.scrollHeight;
    });
    src.onerror = () => { /* EventSource auto-reconnects */ };
  }

  refreshState();
  startLogs();
})();

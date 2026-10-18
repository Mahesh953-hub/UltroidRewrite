# Ultroid - UserBot
# Copyright (C) 2021-2024 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""/agent - a full agent-configuration menu tree for the assistant bot.

This module owns the assistant:agent:* callback-data namespace (see
assistant/_cbs.py). One @asst_cmd(pattern="agent") entry point opens a
primary-blue dashboard; from there six coloured-button sections branch out,
each driven by @callback handlers over the colon callback model:

* menu:<key>        - screen navigation (one MENU_PATTERN handler)
* conv:<field>:<id> - conversation-style value entry (CONV_PATTERN)
* act:<name>[:<id>] - real side-effects / fetches (ACT_PATTERN)

Every button is built with cb(...) through the _menu/_conv/_act
wrappers below, and every wrapper call is paired with one of those three
cb_pattern(...) handlers - no dead buttons, no dead handlers (verified by
tests/test_assistant_cbs_symmetry.py, which resolves these wrappers).

Menu tree (each level owns the next; back pops one level)
---------
home
    llama             llama:<ollama|llamacpp|lmstudio|tgi|vllm>
                        base/model/ctx/temp/sys + fetch-models (real GET)
    cloud             cloud:<openai|gemini|anthropic|openrouter|groq>
                        key(model/masked)/model/sys/base + validate-key (real GET)
    claude            key(masked)/default-model/fallback + fetch-modelspaste URL (real GET)
    keys              masked credentials readout + clear (llama/cloud/claude/all)
    modules           importability probe of openai/anthropic/httpx/aiohttp/requests
    act:syscheck      Python/platform/memory + endpoint reachability (real GET)

Persisted udB keys (namespace AGENT_*)
------------------------------------------
Local LLM : AGENT_LLAMA_<PROV>_BASE|MODEL|CTX|TEMP|SYS
Cloud AI  : AGENT_CLOUD_<PROV>_KEY|MODEL|SYS|BASE
Claude    : AGENT_CLAUDE_KEY|MODEL|FALLBACK

Real network calls (vs. key-gated)
----------------------------------
* Llama "Fetch Models"        - REAL GET, no key: Ollama {base}/api/tags;
  OpenAI-compatible servers (llama.cpp/LM Studio/TGI/vLLM) {base}/v1/models.
* Cloud "Validate Key"        - REAL authenticated GET per provider
  (OpenAI/Anthropic/OpenRouter/Groq/Gemini). Requires a stored key.
* Claude "Fetch Models  URL" - REAL GET api.anthropic.com/v1/models
  (x-api-key). Requires a stored key; result pasted via get_paste +
  make_html_telegraph.
* "Check System" reachability - REAL GET (short timeout) against each
  configured provider host.

All network calls go through async_searcher (the repo's standard async GET
helper, also used by callbackstuffs.py) wrapped in asyncio.wait_for with
a timeout and try/except, so the event loop is never blocked and the module
imports cleanly even when an optional client library is absent (the modules
probe uses importlib.util.find_spec, never an import).
"""

import asyncio
import html
import platform
from datetime import datetime, timezone

from custom.tg import ikm
from pyUltroid._core.richmsg import RichMarkup, send_rich
from pyUltroid.agent import agent
from pyUltroid.fns import fns

from . import *
from ._cbs import cb, cb_pattern
from .rich_pages import body_page, edit_callback_page, rich_page

# --------------------------------------------------------------------#
# Callback-data wrappers. _menu/_conv/_act are the emitter side;
# MENU/CONV/ACT_PATTERN are the three handler prefixes that consume them. The
# static symmetry audit (tests/test_assistant_cbs_symmetry.py) resolves these
# one-line wrappers, so a single pattern handler covers every key under it.


def _menu(*parts):
    return cb("assistant", "agent", "menu", *parts)


def _conv(*parts):
    return cb("assistant", "agent", "conv", *parts)


def _act(*parts):
    return cb("assistant", "agent", "act", *parts)


MENU_PATTERN = cb_pattern("assistant", "agent", "menu")
CONV_PATTERN = cb_pattern("assistant", "agent", "conv")
ACT_PATTERN = cb_pattern("assistant", "agent", "act")

# --------------------------------------------------------------------#
# Provider registries + persistence helpers.

#: Local LLM servers. api selects the model-list endpoint shape:
#: ollama -> {base}/api/tags; openai -> {base}/v1/models.
LLAMA_PROVIDERS = {
    "ollama": {
        "label": "Ollama",
        "default_base": "http://localhost:11434",
        "api": "ollama",
    },
    "llamacpp": {
        "label": "llama.cpp",
        "default_base": "http://localhost:8080",
        "api": "openai",
    },
    "lmstudio": {
        "label": "LM Studio",
        "default_base": "http://localhost:1234/v1",
        "api": "openai",
    },
    "tgi": {
        "label": "TGI / WebUI",
        "default_base": "http://localhost:5000/v1",
        "api": "openai",
    },
    "vllm": {
        "label": "vLLM",
        "default_base": "http://localhost:8000/v1",
        "api": "openai",
    },
}

#: Cloud LLM providers (provider id -> display label).
CLOUD_PROVIDERS = {
    "openai": "OpenAI-compatible",
    "gemini": "Google Gemini",
    "anthropic": "Anthropic",
    "apimix": "apmix.ai",
    "openrouter": "OpenRouter",
    "groq": "Groq",
    "xai": "xAI (Grok)",
    "meta_llama": "Meta Llama API",
}

#: Host roots used only for the (key-gated) reachability probe in "check system".
CLOUD_HOSTS = {
    "openai": "https://api.openai.com",
    "gemini": "https://generativelanguage.googleapis.com",
    "anthropic": "https://api.anthropic.com",
    "apimix": "https://api.apmix.ai",
    "openrouter": "https://openrouter.ai",
    "groq": "https://api.groq.com",
    "xai": "https://api.x.ai",
    "meta_llama": "https://api.llama.com",
}

#: Optional AI client libraries probed by the "modules" screen.
MODULES = [
    ("openai", "openai", 'pip install "openai>=1.40"'),
    ("anthropic", "anthropic", "pip install anthropic"),
    ("httpx", "httpx", "pip install httpx"),
    ("aiohttp", "aiohttp", "pip install aiohttp"),
    ("requests", "requests", "pip install requests"),
]

#: Conversation-field metadata: field -> (label, prompt, validator).
#: validator is None | "int" | "float".
_CONV_FIELDS = {
    "llama_base": (
        "Base URL",
        "Send the base URL of the local server (e.g. http://localhost:11434). Use /cancel to abort.",
        None,
    ),
    "llama_model": (
        "Model",
        "Send the model name to use (e.g. llama3, qwen2.5:7b). Use /cancel to abort.",
        None,
    ),
    "llama_ctx": (
        "Context Window",
        "Send the context window size in tokens (e.g. 4096). Use /cancel to abort.",
        "int",
    ),
    "llama_temp": (
        "Temperature",
        "Send the sampling temperature, 0.0 2.0 (e.g. 0.7). Use /cancel to abort.",
        "float",
    ),
    "llama_sys": (
        "System Prompt",
        "Send the system prompt text. Use /cancel to abort.",
        None,
    ),
    "cloud_key": (
        "API Key",
        "Send the API key for this cloud provider. Use /cancel to abort.",
        None,
    ),
    "cloud_model": (
        "Model",
        "Send the model id (e.g. gpt-4o-mini, gemini-1.5-pro). Use /cancel to abort.",
        None,
    ),
    "cloud_sys": (
        "System Prompt",
        "Send the system prompt text. Use /cancel to abort.",
        None,
    ),
    "cloud_base": (
        "Base URL",
        "Send the OpenAI-compatible base URL (e.g. https://api.openai.com/v1). Use /cancel to abort.",
        None,
    ),
    "claude_key": (
        "Anthropic API Key",
        "Send your Anthropic API key (sk-ant-...). Use /cancel to abort.",
        None,
    ),
    "claude_model": (
        "Default Model",
        "Send the default Claude model id (e.g. claude-3-5-sonnet-20241022). Use /cancel to abort.",
        None,
    ),
    "claude_fallback": (
        "Fallback Model",
        "Send the fallback Claude model id. Use /cancel to abort.",
        None,
    ),
}

#: Fields whose value may be echoed back in the convo confirmation (non-secret,
#: short). Keys and system prompts are never echoed.
_ECHO_FIELDS = {
    "llama_base",
    "llama_model",
    "llama_ctx",
    "llama_temp",
    "cloud_model",
    "cloud_base",
    "claude_model",
    "claude_fallback",
}


def _agent_var(field, scope=""):
    """Build a udB key from a convo field (llama_base) + optional
    provider scope (ollama) -> AGENT_LLAMA_OLLAMA_BASE."""
    section, _, suffix = field.partition("_")
    base = f"AGENT_{section.upper()}"
    if scope:
        base = f"{base}_{scope.upper()}"
    return f"{base}_{suffix.upper()}"


def _has_value(value):
    """True when a setting is explicitly present, including a stored False/0."""
    return value is not None and not (isinstance(value, str) and not value.strip())


def _state_style(value):
    """Use green only for a configured value; leave unset controls unstyled."""
    return "success" if _has_value(value) else None


def _back_btn(*current):
    """Back button for the screen at menu path current.

    Destination is the parent path - same segments minus the last one
    (llama/<prov> -> llama -> home).
    """
    parent = current[:-1] or ("home",)
    return ("Back", _menu(*parent), None)


def _conv_back(field, scope):
    """Destination token for a convo flow's " Back" button - re-opens the
    provider screen the field belongs to."""
    section = field.split("_", 1)[0]
    if section == "llama":
        return _menu("llama", scope)
    if section == "cloud":
        return _menu("cloud", scope)
    return _menu("claude")


def _mask(value):
    """Mask a secret for display - never echoes the raw value back."""
    if not value:
        return "not set"
    s = str(value)
    if len(s) <= 8:
        return "****"
    return f"{s[:4]}****{s[-4:]}"


def _settings_page(title, description, rows, details=None):
    """Build a native RichMessage settings table with consistent page chrome."""
    page = rich_page(
        title,
        rows,
        description=description,
        columns=("Setting", "Current value"),
        total=len(rows),
    )
    if not details:
        return page
    detail_title, detail_text = details
    expandable = (
        f"</table><blockquote expandable><b>{html.escape(str(detail_title))}</b>\n"
        f"{html.escape(str(detail_text))}</blockquote><hr>"
    )
    return RichMarkup(page.text.replace("</table><hr>", expandable), "html")


# --------------------------------------------------------------------#
# Network helper. async_searcher is the repo's standard async GET (used by
# callbackstuffs.py too); asyncio.wait_for enforces the timeout so a
# hung host can never block the event loop. Returns (ok, status, body):
# ok=True  -> (status, parsed-json-or-None) for any HTTP status (host reached)
# ok=False -> (None, "timed out" | "<ExceptionClass>") on timeout / conn error.


async def _http_get(url, headers=None, timeout=10):
    async def _eval(resp):
        status = resp.status
        try:
            body = await resp.json()
        except Exception:
            body = None
        return status, body

    try:
        status, body = await asyncio.wait_for(
            fns.async_searcher(url, headers=headers, evaluate=_eval),
            timeout=timeout,
        )
        return True, status, body
    except TimeoutError:
        return False, None, "timed out"
    except Exception as e:
        return False, None, f"{e.__class__.__name__}"


def _validate_spec(prov, key, base):
    """Return (url, headers) for a real lightweight authenticated "whoami"
    request, or (None, None) when no safe validation endpoint is known."""
    if prov == "openai":
        b = (base or "https://api.openai.com/v1").rstrip("/")
        return f"{b}/models", {"Authorization": f"Bearer {key}"}
    if prov == "gemini":
        return (
            f"https://generativelanguage.googleapis.com/v1beta/models?key={key}",
            {},
        )
    if prov == "anthropic":
        return (
            "https://api.anthropic.com/v1/models",
            {"x-api-key": key, "anthropic-version": "2023-06-01"},
        )
    if prov == "openrouter":
        return "https://openrouter.ai/api/v1/key", {"Authorization": f"Bearer {key}"}
    if prov == "groq":
        return "https://api.groq.com/openai/v1/models", {
            "Authorization": f"Bearer {key}"
        }
    if prov == "xai":
        return "https://api.x.ai/v1/models", {"Authorization": f"Bearer {key}"}
    if prov == "meta_llama":
        return "https://api.llama.com/v1/models", {"Authorization": f"Bearer {key}"}
    return None, None


def _validate_hint(prov, body):
    """A non-sensitive hint from a successful validation (model count / key
    label) - never includes the key itself."""
    try:
        if prov == "openrouter" and isinstance(body, dict):
            d = body.get("data", {}) or {}
            parts = []
            if d.get("label"):
                parts.append(f"label: {d['label']}")
            if d.get("usage") is not None:
                parts.append(f"usage: {d['usage']}")
            return f" ({', '.join(parts)})" if parts else ""
        if isinstance(body, dict):
            data = body.get("data") or body.get("models") or []
            if isinstance(data, list) and data:
                return f" ({len(data)} models listed)"
    except Exception:
        pass
    return ""


def _probe_modules():
    """Real importability probe - uses find_spec (no import side effects),
    so the module imports fine even when a library is missing."""
    import importlib.util

    return {name: importlib.util.find_spec(name) is not None for name, _, _ in MODULES}


def _mem_info():
    """Best-effort memory readout: psutil if available, else /proc/meminfo,
    else a clear 'unavailable' note."""
    try:
        import psutil

        vm = psutil.virtual_memory()
        return (
            f"{vm.percent}% used - {vm.available // (1024**2)} MB free / "
            f"{vm.total // (1024**2)} MB total"
        )
    except Exception:
        pass
    try:
        with open("/proc/meminfo") as f:
            d = {}
            for line in f:
                k, _, v = line.partition(":")
                d[k.strip()] = v.strip().split()[0]
        total = int(d["MemTotal"]) // 1024
        avail = int(d["MemAvailable"]) // 1024
        pct = (100 * (total - avail) / total) if total else 0
        return f"{pct:.0f}% used - {avail} MB free / {total} MB total"
    except Exception:
        return "unavailable (install psutil for details)"


def _collect_endpoints():
    """Endpoints to probe in "check system": user-configured local bases + the
    hosts of cloud/Claude providers that actually have a stored key."""
    eps = []
    for pid, info in LLAMA_PROVIDERS.items():
        base = udB.get_key(_agent_var("llama_base", pid))
        if base:
            eps.append((f"Llama - {info['label']}", base.rstrip("/")))
    for pid in CLOUD_PROVIDERS:
        if udB.get_key(_agent_var("cloud_key", pid)):
            if pid == "openai":
                b = (
                    udB.get_key(_agent_var("cloud_base", pid))
                    or "https://api.openai.com/v1"
                ).rstrip("/")
                host = b.rsplit("/v1", 1)[0] if "/v1" in b else b
                eps.append((f"Cloud - {CLOUD_PROVIDERS[pid]}", host))
            else:
                eps.append((f"Cloud - {CLOUD_PROVIDERS[pid]}", CLOUD_HOSTS[pid]))
    if udB.get_key("AGENT_CLAUDE_KEY"):
        eps.append(("Claude - Anthropic", "https://api.anthropic.com"))
    if not eps:
        eps.append(("Ollama (default)", LLAMA_PROVIDERS["ollama"]["default_base"]))
    return eps


# --------------------------------------------------------------------#
# Menu renderers - each returns native RichMarkup plus callback buttons.


def _home():
    primary = agent().config_default_provider()
    primary_label = (
        CLOUD_PROVIDERS.get(primary)
        or (LLAMA_PROVIDERS.get(primary) or {}).get("label")
        or primary
    )
    text = _settings_page(
        "Agent Config",
        "Configure local and cloud models, manage keys, inspect installed modules, and run a system health check.",
        [
            ("Version", f"v{ultroid_version}"),
            ("Primary provider", primary_label if primary else "Automatic routing"),
            ("Native RichMessage engine", "Available for this assistant bot"),
        ],
    )
    buttons = ikm(
        [
            [
                ("Llama / Local", _menu("llama"), "primary"),
                ("Cloud AI", _menu("cloud"), "primary"),
            ],
            [
                ("Claude (Anthropic)", _menu("claude"), "primary"),
                ("Keys", _menu("keys"), "primary"),
            ],
            [
                ("Modules", _menu("modules"), "primary"),
                ("Check System", _act("syscheck"), "primary"),
            ],
            [("Close", _act("close"), None)],
        ],
        icon=False,
    )
    return text, buttons


def _llama_menu():
    rows = []
    items = list(LLAMA_PROVIDERS.items())
    while items:
        a = items.pop(0)
        row = [(a[1]["label"], _menu("llama", a[0]), "primary")]
        if items:
            b = items.pop(0)
            row.append((b[1]["label"], _menu("llama", b[0]), "primary"))
        rows.append(row)
    rows.append([_back_btn("llama")])
    text = _settings_page(
        "Llama / Local",
        "Choose a local inference server. Each server keeps its own URL, model, context, temperature, and prompt.",
        [
            (
                info["label"],
                (
                    f"{udB.get_key(_agent_var('llama_base', pid)) or info['default_base']} · "
                    f"{udB.get_key(_agent_var('llama_model', pid)) or 'model not set'}"
                ),
            )
            for pid, info in LLAMA_PROVIDERS.items()
        ],
    )
    return text, ikm(rows, icon=False)


def _llama_prov(prov):
    info = LLAMA_PROVIDERS.get(prov)
    if not info:
        return body_page("Llama", "<p>Unknown provider.</p>"), ikm(
            [[_back_btn("llama", prov)]], icon=False
        )
    stored_base = udB.get_key(_agent_var("llama_base", prov))
    base = stored_base or info["default_base"]
    model = udB.get_key(_agent_var("llama_model", prov))
    context = udB.get_key(_agent_var("llama_ctx", prov))
    temperature = udB.get_key(_agent_var("llama_temp", prov))
    system_prompt = udB.get_key(_agent_var("llama_sys", prov))
    text = _settings_page(
        f"Llama · {info['label']}",
        "Settings for this local inference endpoint. Fetch Models checks the live server without changing configuration.",
        [
            ("Base URL", base),
            ("Model", model or "Not set"),
            ("Context window", context if _has_value(context) else "Not set"),
            ("Temperature", temperature if _has_value(temperature) else "Not set"),
            ("API protocol", info["api"]),
        ],
        details=("System prompt", system_prompt or "Not set"),
    )
    buttons = ikm(
        [
            [
                ("Base URL", _conv("llama_base", prov), _state_style(stored_base)),
                ("Model", _conv("llama_model", prov), _state_style(model)),
            ],
            [
                ("Context", _conv("llama_ctx", prov), _state_style(context)),
                ("Temperature", _conv("llama_temp", prov), _state_style(temperature)),
            ],
            [("System Prompt", _conv("llama_sys", prov), _state_style(system_prompt))],
            [("Fetch Models", _act("llama_fetch", prov), None)],
            [_back_btn("llama", prov)],
        ],
        icon=False,
    )
    return text, buttons


def _llama_prov_buttons(prov):
    return ikm([[_back_btn("llama", prov)]], icon=False)


def _cloud_menu():
    rows = []
    items = list(CLOUD_PROVIDERS.items())
    while items:
        a = items.pop(0)
        row = [(a[1], _menu("cloud", a[0]), "primary")]
        if items:
            b = items.pop(0)
            row.append((b[1], _menu("cloud", b[0]), "primary"))
        rows.append(row)
    rows.append([_back_btn("cloud")])
    text = _settings_page(
        "Cloud AI",
        "Store keys securely, choose a model and prompt, validate credentials, and select a primary provider.",
        [
            (
                label,
                "configured · "
                + str(udB.get_key(_agent_var("cloud_model", pid)) or "model not set")
                if udB.get_key(_agent_var("cloud_key", pid))
                else "not configured",
            )
            for pid, label in CLOUD_PROVIDERS.items()
        ],
    )
    return text, ikm(rows, icon=False)


def _provider_pin_label(provider: str) -> str:
    """Label for a provider's pin button, including its current state."""
    if not udB.get_key("AGENT_CLOUD_%s_KEY" % provider.upper()):
        return "Not configured"
    if agent().config_default_provider() == provider:
        return "Primary for .ai/.ask"
    return "Set as primary"


def _cloud_prov(prov):
    label = CLOUD_PROVIDERS.get(prov, prov)
    key = udB.get_key(_agent_var("cloud_key", prov))
    stored_base = udB.get_key(_agent_var("cloud_base", prov))
    base = stored_base
    if not base and prov == "openai":
        base = "https://api.openai.com/v1"
    model = udB.get_key(_agent_var("cloud_model", prov))
    system_prompt = udB.get_key(_agent_var("cloud_sys", prov))
    data = [
        ("API key", _mask(key)),
        ("Model", model or "Not set"),
        (
            "Primary for .ai/.ask",
            "Yes" if agent().config_default_provider() == prov else "No",
        ),
    ]
    if prov == "openai":
        data.insert(2, ("Base URL", base or "Not set"))
    text = _settings_page(
        f"Cloud · {label}",
        "Credentials are masked. Validate Key performs a read-only authenticated provider check.",
        data,
        details=("System prompt", system_prompt or "Not set"),
    )
    rows = [
        [
            ("API Key", _conv("cloud_key", prov), _state_style(key)),
            ("Model", _conv("cloud_model", prov), _state_style(model)),
        ],
        [("System Prompt", _conv("cloud_sys", prov), _state_style(system_prompt))],
    ]
    if prov == "openai":
        rows.insert(
            1,
            [("Base URL", _conv("cloud_base", prov), _state_style(stored_base))],
        )
    if _has_value(key):
        rows.append([("Validate Key", _act("cloud_validate", prov), "success")])
        pinned = agent().config_default_provider() == prov
        rows.append(
            [
                (
                    _provider_pin_label(prov),
                    _act("cloud_primary", prov),
                    "success" if pinned else None,
                )
            ]
        )
    rows.append([_back_btn("cloud", prov)])
    buttons = ikm(rows, icon=False)
    return text, buttons


def _cloud_prov_buttons(prov):
    return ikm([[_back_btn("cloud", prov)]], icon=False)


def _claude_menu():
    key = udB.get_key("AGENT_CLAUDE_KEY")
    model = udB.get_key("AGENT_CLAUDE_MODEL")
    fallback = udB.get_key("AGENT_CLAUDE_FALLBACK")
    text = _settings_page(
        "Claude · Anthropic",
        "Configure model selection and fallback. Fetch Models checks Anthropic's live catalog and returns shareable specs.",
        [
            ("API key", _mask(key)),
            ("Default model", model or "Not set"),
            ("Fallback model", fallback or "Not set"),
        ],
    )
    buttons = ikm(
        [
            [
                ("API Key", _conv("claude_key"), _state_style(key)),
                ("Default Model", _conv("claude_model"), _state_style(model)),
            ],
            [("Fallback Model", _conv("claude_fallback"), _state_style(fallback))],
            [("Fetch Models", _act("claude_fetch"), None)],
            [_back_btn("claude")],
        ],
        icon=False,
    )
    return text, buttons


def _claude_menu_buttons():
    return ikm([[_back_btn("claude")]], icon=False)


def _keys_menu():
    rows = []
    for pid, label in CLOUD_PROVIDERS.items():
        k = udB.get_key(_agent_var("cloud_key", pid))
        rows.append((f"Cloud · {label}", _mask(k) if k else "Not set"))
    ck = udB.get_key("AGENT_CLAUDE_KEY")
    rows.append(("Claude · Anthropic", _mask(ck) if ck else "Not set"))
    configured = [
        LLAMA_PROVIDERS[p]["label"]
        for p in LLAMA_PROVIDERS
        if udB.get_key(_agent_var("llama_base", p))
    ]
    rows.append(("Local LLM endpoints", ", ".join(configured) if configured else "None configured"))
    text = _settings_page(
        "Credentials",
        "Keys are shown masked and never echoed in full. Use the buttons to clear a provider section.",
        rows,
    )
    buttons = ikm(
        [
            [
                ("Clear Llama", _act("keys_clear", "llama"), "danger"),
                ("Clear Cloud", _act("keys_clear", "cloud"), "danger"),
            ],
            [
                ("Clear Claude", _act("keys_clear", "claude"), "danger"),
                ("Clear All", _act("keys_clear", "all"), "danger"),
            ],
            [_back_btn("keys")],
        ],
        icon=False,
    )
    return text, buttons


def _modules_menu():
    probe = _probe_modules()
    rows = []
    for name, label, install in MODULES:
        rows.append(
            (label, "Installed" if probe[name] else f"Missing · {install}")
        )
    text = _settings_page(
        "AI Modules",
        "Importability of optional AI client libraries. Missing modules affect only the features that use them.",
        rows,
    )
    buttons = ikm([[_back_btn("modules")]], icon=False)
    return text, buttons


def _render_menu(key):
    """Dispatch a menu:<path...> callback to its screen renderer.

    key is the path below assistant:agent:menu: - every level owns
    the next (llama -> llama:<prov>), so back is always the parent
    path (see :func:_back_btn).
    """
    levels = key.split(":") if key else []
    if levels == ["home"]:
        return _home()
    if levels == ["llama"]:
        return _llama_menu()
    if len(levels) == 2 and levels[0] == "llama":
        return _llama_prov(levels[1])
    if levels == ["cloud"]:
        return _cloud_menu()
    if len(levels) == 2 and levels[0] == "cloud":
        return _cloud_prov(levels[1])
    if levels == ["claude"]:
        return _claude_menu()
    if levels == ["keys"]:
        return _keys_menu()
    if levels == ["modules"]:
        return _modules_menu()
    return None, None


# --------------------------------------------------------------------#
# Action bodies - real network fetches / probes. Each returns (text, buttons).


async def _llama_fetch(prov):
    info = LLAMA_PROVIDERS.get(prov)
    if not info:
        return (
            f"<b>Llama</b>\n\nUnknown provider.",
            ikm([[_back_btn("llama", prov)]], icon=False),
        )
    base = (udB.get_key(_agent_var("llama_base", prov)) or info["default_base"]).rstrip(
        "/"
    )
    if info["api"] == "ollama":
        url = f"{base}/api/tags"
    elif base.endswith("/v1"):
        # OpenAI-compatible servers whose configured base already carries the
        # /v1 prefix (LM Studio, TGI and vLLM defaults do): appending another
        # one doubled the prefix and every fetch 404'd.
        url = f"{base}/models"
    else:
        url = f"{base}/v1/models"
    ok, status, body = await _http_get(url, timeout=10)
    head = f"Llama - {info['label']} - Models"
    if not ok:
        return (
            f"<b>{head}</b>\n\nUnreachable - needs a running provider at "
            f"<code>{html.escape(url)}</code>.\n\nReason: <code>{html.escape(str(body))}</code>",
            _llama_prov_buttons(prov),
        )
    if status != 200:
        return (
            f"<b>{head}</b>\n\nServer responded HTTP {status} at "
            f"<code>{html.escape(url)}</code>. Is the right provider running there?",
            _llama_prov_buttons(prov),
        )
    if info["api"] == "ollama":
        models = (body or {}).get("models", []) or []
        lines = []
        for m in models[:30]:
            det = m.get("details", {}) or {}
            lines.append(
                f"- <b>{html.escape(str(m.get('name', '?')))}</b> - "
                f"{html.escape(str(det.get('parameter_size', '?')))} - "
                f"{html.escape(str(det.get('quantization_level', '?')))}"
            )
    else:
        models = (body or {}).get("data", []) or []
        lines = [f"- <b>{html.escape(str(m.get('id', '?')))}</b>" for m in models[:30]]
    count = len(models)
    if not lines:
        msg = "No models returned by the server."
    else:
        msg = (
            f"Fetched {count} model(s) from <code>{html.escape(url)}</code>:\n\n"
            + "\n".join(lines)
        )
        if count > 30:
            msg += f"\n... and {count - 30} more"
    return f"<b>{head}</b>\n\n{msg}", _llama_prov_buttons(prov)


async def _cloud_validate(prov):
    label = CLOUD_PROVIDERS.get(prov, prov)
    back = _cloud_prov_buttons(prov)
    head = f"Cloud - {label} - Validate"
    key = udB.get_key(_agent_var("cloud_key", prov))
    if not key:
        return (
            f"<b>{head}</b>\n\nNo API key set for this provider. Set one first.",
            back,
        )
    base = udB.get_key(_agent_var("cloud_base", prov)) if prov == "openai" else None
    url, headers = _validate_spec(prov, key, base)
    if url is None:
        return (
            f"<b>{head}</b>\n\nNo safe validation endpoint is known for this "
            f"provider. Validate by sending a real completion request with your key.",
            back,
        )
    ok, status, body = await _http_get(url, headers=headers, timeout=12)
    if not ok:
        return (
            f"<b>{head}</b>\n\nCould not reach provider.\nReason: "
            f"<code>{html.escape(str(status or body))}</code>",
            back,
        )
    if status == 200:
        return (
            f"<b>{head}</b>\n\nKey is valid. Provider authenticated the request."
            + html.escape(_validate_hint(prov, body)),
            back,
        )
    if status in (401, 403):
        return (
            f"<b>{head}</b>\n\nProvider rejected the key (HTTP {status}). "
            f"Check the key and try again.",
            back,
        )
    return f"<b>{head}</b>\n\nUnexpected response (HTTP {status}).", back


async def _claude_fetch():
    back = _claude_menu_buttons()
    head = f"Claude - Models"
    key = udB.get_key("AGENT_CLAUDE_KEY")
    if not key:
        return (
            f"<b>{head}</b>\n\nNo Anthropic API key set. Set one first "
            f"(Claude API Key).",
            back,
        )
    ok, status, body = await _http_get(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        timeout=12,
    )
    if not ok:
        return (
            f"<b>{head}</b>\n\nCould not reach Anthropic.\nReason: "
            f"<code>{html.escape(str(status or body))}</code>",
            back,
        )
    if status != 200:
        msg = f"Anthropic rejected the request (HTTP {status})."
        if status in (401, 403):
            msg += " Check your API key."
        return f"<b>{head}</b>\n\n{msg}", back
    data = (body or {}).get("data", []) or []
    if not data:
        return f"<b>{head}</b>\n\nNo models returned.", back

    # Plaintext specs for the paste.
    specs = [
        f"Anthropic Models - fetched {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
    ]
    for m in data:
        specs.append(f"- {m.get('id', '?')}")
        if m.get("display_name"):
            specs.append(f"    display_name: {m['display_name']}")
        if m.get("created_at"):
            specs.append(f"    created_at: {m['created_at']}")
        if m.get("type"):
            specs.append(f"    type: {m['type']}")
    specs_text = "\n".join(specs)

    paste_url = None
    try:
        okp, pkey = await fns.get_paste(specs_text, extension="txt")
        if okp and pkey:
            paste_url = f"https://spaceb.in/{pkey}"
    except Exception:
        paste_url = None

    tele_url = None
    try:
        tele_url = await fns.make_html_telegraph(
            "Anthropic Models", f"<pre>{html.escape(specs_text)}</pre>"
        )
    except Exception:
        tele_url = None

    summary = "\n".join(f"- {html.escape(str(m.get('id', '?')))}" for m in data[:20])
    if len(data) > 20:
        summary += f"\n... and {len(data) - 20} more"
    body_html = f"Fetched {len(data)} model(s):\n\n{summary}"
    if not paste_url and not tele_url:
        body_html += "\n\n( Paste generation failed - full list shown above. )"

    rows = []
    url_row = []
    if paste_url:
        url_row.append(("Specs Paste", "url", paste_url, "primary"))
    if tele_url:
        url_row.append(("Telegraph", "url", tele_url, "primary"))
    if url_row:
        rows.append(url_row)
    rows.append([_back_btn("claude")])
    return f"<b>{head}</b>\n\n{body_html}", ikm(rows, icon=False)


async def _keys_clear(scope):
    cleared = []

    def _del(var):
        if udB.get_key(var):
            udB.del_key(var)
            cleared.append(var)

    if scope in ("llama", "all"):
        for pid in LLAMA_PROVIDERS:
            for f in ("BASE", "MODEL", "CTX", "TEMP", "SYS"):
                _del(f"AGENT_LLAMA_{pid.upper()}_{f}")
    if scope in ("cloud", "all"):
        for pid in CLOUD_PROVIDERS:
            for f in ("KEY", "MODEL", "SYS", "BASE"):
                _del(f"AGENT_CLOUD_{pid.upper()}_{f}")
    if scope in ("claude", "all"):
        for f in ("KEY", "MODEL", "FALLBACK"):
            _del(f"AGENT_CLAUDE_{f}")
    # A pin that outlives its key is a trap: .ai would keep forcing a
    # provider that can no longer authenticate. Drop the pin when the
    # provider it points at just lost its credentials.
    try:
        pinned = agent().config_default_provider()
        if pinned and not agent().config_ready(pinned):
            agent().config_use_default(None)
    except Exception:
        pass
    msg = (
        f"Cleared {len(cleared)} stored credential(s)."
        if cleared
        else "Nothing to clear - no credentials stored for this section."
    )
    return (
        f"<b>Credentials - Cleared</b>\n\n{msg}",
        ikm([[_back_btn("keys")]], icon=False),
    )


async def _syscheck():
    py = platform.python_version()
    plat = platform.platform()
    mem = _mem_info()
    endpoints = _collect_endpoints()

    async def _one(label, url):
        ok, _status, body = await _http_get(url, timeout=6)
        if ok:
            return label, url, "reachable"
        return label, url, f"{html.escape(str(body))}"

    reach = (
        await asyncio.gather(*[_one(label, url) for label, url in endpoints])
        if endpoints
        else []
    )

    lines = [
        f"Python: <code>{html.escape(py)}</code>",
        f"Platform: <code>{html.escape(plat)}</code>",
        f"Memory: <code>{html.escape(mem)}</code>",
        "",
        "<b>Endpoint reachability:</b>",
    ]
    if reach:
        for label, url, state in reach:
            lines.append(
                f"- {html.escape(label)} - {state} (<code>{html.escape(url)}</code>)"
            )
    else:
        lines.append("- No provider endpoints configured yet.")
    return (
        f"<b>System Check</b>\n\n" + "\n".join(lines),
        ikm([[_back_btn("home")]], icon=False),
    )


# --------------------------------------------------------------------#
# Handlers. CallbackQuery.Event has no .buttons and event.client is the
# assistant bot (event.client._bot self-references it); all state is read
# from udB, so no get_message() is needed and routing stays on the bot.


@asst_cmd(pattern="agent", owner=True)
async def _agent_cmd(event):
    text, buttons = _home()
    await send_rich(
        event.client,
        event.chat_id,
        text,
        buttons=buttons,
        reply_to=event.id,
        link_preview=False,
    )


@callback(MENU_PATTERN, owner=True)
async def _menu_handler(event):
    key = event.data_match.group(1).decode("utf-8")
    text, buttons = _render_menu(key)
    if text is None:
        # A dead path (stale button after a reload of the module tree) must
        # not just swallow the click; tell the owner and land on home.
        text, buttons = _home()
        try:
            await event.answer(
                "That screen no longer exists; reopened home.", alert=True
            )
        except Exception:
            pass
        await edit_callback_page(event, text, buttons)
        return
    await event.answer()
    await edit_callback_page(event, text, buttons)


@callback(CONV_PATTERN, owner=True)
async def _conv_handler(event):
    key = event.data_match.group(1).decode("utf-8")
    field, _, scope = key.partition(":")
    meta = _CONV_FIELDS.get(field)
    if not meta:
        return
    label, prompt, validator = meta
    var = _agent_var(field, scope)
    back = _conv_back(field, scope)
    try:
        await event.answer("Enter a value in the chat.")
        await event.delete()
    except Exception:
        pass
    async with event.client.conversation(event.sender_id, timeout=120) as conv:
        await conv.send_message(prompt)
        try:
            response = await conv.get_response()
        except TimeoutError:
            return await conv.send_message(
                "Timed out. Please try again.", buttons=get_back_button(back)
            )
        value = response.message
        value_text = str(value).strip()
        cancel_command = (
            value_text.split(maxsplit=1)[0].split("@", 1)[0] if value_text else ""
        )
        if cancel_command.casefold() == "/cancel":
            return await conv.send_message("Cancelled.", buttons=get_back_button(back))
        if validator:
            try:
                value = float(value) if validator == "float" else int(value)
                if validator == "float" and not 0 <= value <= 2:
                    raise ValueError("out of range")
            except (ValueError, TypeError):
                return await conv.send_message(
                    f"Invalid value for {label}.",
                    buttons=get_back_button(back),
                )
        await setit(event, var, value)
        if field in _ECHO_FIELDS:
            shown = f"{value}"
        elif field.endswith("_key"):
            shown = "   - (masked)"
        else:
            shown = "saved"
        await conv.send_message(
            f"{label} set to {shown}.", buttons=get_back_button(back)
        )


@callback(ACT_PATTERN, owner=True)
async def _act_handler(event):
    key = event.data_match.group(1).decode("utf-8")
    action, _, rest = key.partition(":")
    if action == "close":
        try:
            return await event.delete()
        except Exception:
            return await event.answer("Could not close.", alert=True)
    await event.answer("Working...")
    if action == "syscheck":
        text, buttons = await _syscheck()
    elif action == "llama_fetch":
        text, buttons = await _llama_fetch(rest)
    elif action == "cloud_validate":
        text, buttons = await _cloud_validate(rest)
    elif action == "cloud_primary":
        try:
            if agent().config_default_provider() == rest:
                agent().config_use_default(None)
                text, buttons = _cloud_prov(rest)
            else:
                agent().config_use_default(rest, require_configured=True)
                text, buttons = _cloud_prov(rest)
        except (KeyError, ValueError) as exc:
            text = body_page(
                "Primary provider",
                f"<p>Could not set {html.escape(CLOUD_PROVIDERS.get(rest, rest))} as primary: "
                f"{html.escape(str(exc))}</p>",
            )
            buttons = _cloud_prov_buttons(rest)
    elif action == "claude_fetch":
        text, buttons = await _claude_fetch()
    elif action == "keys_clear":
        text, buttons = await _keys_clear(rest)
    else:
        return
    if isinstance(text, str):
        text = body_page("Agent result", text)
    await edit_callback_page(event, text, buttons)

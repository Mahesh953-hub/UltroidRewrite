# Tests

Credential-free regression suite for the SarahUB modernization. Everything
here must run without real Telegram credentials, a database, or network
access.

## Running the suite

```bash
# from the repo root, with the project venv (or any env with requirements.txt installed)
python -m pytest tests/ -q

# smoke test (imports pyUltroid with mocked clients, exercises core flows)
python smoke_test.py
```

CI runs both on Python 3.12 — see `.github/workflows/ci.yml`.

## Layout

| File | Covers |
|---|---|
| `test_core_architecture.py` | Private-core (`pyUltroid/_core`) architecture: loader registry, client wrapper, cards, events — dependency-light, namespace-isolated. |
| `test_universal_runtime.py` | Universal runtime layer (`pyUltroid/universal`): modes, storage, managed bots, inline, discovery, capability detection. |
| `test_updater_engine.py` | W1-B HSU updater (`pyUltroid/updater`): `classify_file`, `GitManager` fetch/pull/rollback against a temp git repo, `UpdateEngine` check/apply/rollback with faked shadow+shell — includes the bare-`apply()` busy-guard regression. |
| `test_plugin_load.py` | Plugin-load non-regression harness over `plugins/`, `assistant/`, `assistant/manager/`, `vc/` (details below). |
| `conftest.py` | Shared helpers: repo-root `sys.path` bootstrap, `isolated_sys_modules` namespace-isolation context manager. |

## Plugin-load harness (`test_plugin_load.py`)

Runs the real `AsyncPluginLoader` over every plugin tree with mocked
Telethon clients (same technique as `smoke_test.py`: dummy `StringSession`,
mocked `start`/`connect`/`get_me`). Because importing `pyUltroid` mutates
process-global state, the harness executes in a **subprocess** — the test
file doubles as the worker and reports one `PLUGLOAD_JSON:` line:

```bash
# debug the harness standalone (prints one JSON line at the end)
python tests/test_plugin_load.py --worker
```

### Baseline-count philosophy

The suite asserts **loaded-count floors**, not exact counts:

| Tree | Floor | Files | Notes |
|---|---|---|---|
| `plugins/` | ≥ 71 | 88 | 71 load with only `requirements.txt`; 73 with the vc/plugin extras below. |
| `assistant/` | ≥ 4 | 8 | 4 load with only `requirements.txt`. |
| `assistant/manager/` | == 6/6 | 6 | No optional deps — must stay exact. |
| `vc/` | ≥ 13 | 14 | Floor applies only once the full vc stack is importable; see below. |

Rules:

1. **Floors never drop.** A higher count is always fine; raise a floor in
   the same commit that makes more plugins load.
2. **Skip-tolerant.** A plugin failing on a missing *optional* dependency
   (loader error `ModuleNotFoundError: ...` or `Missing plugin
   dependencies: ...`) counts as skipped-not-regressed, not as a failure —
   the suite passes in a bare `requirements.txt` env and in a fully
   provisioned one.
3. **Real errors always fail.** Any load error that is neither a dependency
   skip nor an allow-listed known issue fails
   `test_no_unexplained_load_errors`.

### vc/ floor gating

The vc floor is live only when the whole vc import closure is importable
(`mutagen`, `psutil`, `yt_dlp`, `youtubesearchpython`, `pytgcalls`,
`ntgcalls`, `httpx`, `aiohttp`, `aiofiles`, `pydantic`, `Crypto`).
Otherwise the vc test skips and every vc module must classify as a
dependency skip. W0-A declares the missing half of this list; until that
lands, CI (which installs `requirements.txt` only) exercises the skip path.

Wave-0 venv note: W0-D additively installed `mutagen`, `psutil`, `yt-dlp`
and `youtube-search-python` (PyPI name; import name `youtubesearchpython`)
into the shared venv so the vc floor is exercised locally. These are the
packages W0-A adds to the manifests — never upgrade/remove existing venv
packages.

### Known issues allow-list

`KNOWN_ISSUES` in `test_plugin_load.py` maps a plugin id to the error
substring of a **pre-existing code bug** (not a missing dep). Remove the
entry and raise the floor when the owning wave lands the fix.

| Plugin | Bug | Owner |
|---|---|---|
| `vc._pytgcalls` | `from ._youtube import YouTubeData` but `_youtube.py` only defines `class YouTube` — real import bug, invisible until mutagen (A7) was installed. W1-A's acceptance is `vc/` 14/14 load; then raise the vc floor 13 → 14. | W1-A |

Related, not allow-listed (skip-classified): `plugins.imaging` fails its
`__requires__` gate with `Missing plugin dependencies: pillow>=10.0`
although Pillow is installed — loader depspec bug A5, owned by W0-B
(`pillow` is the distribution; import name is `PIL`). When W0-B merges,
`plugins/` loads 72+ in a bare env.

## Env-assumption history

`test_universal_runtime.py::test_deep_link_and_capability_fallback_are_dependency_free`
originally asserted `detect_runtime_capabilities() == RuntimeCapabilities()`
(all-`False`). That is only true when **Telethon is not installed** — the
exploration ran it in a bare env, where it passed. In the full venv the
  Telethon fork (1.45.0, layer 229) is importable, detection finds the managed-bot
surfaces, and the assertion failed (the old 30-pass/1-fail baseline).

The test is now environment-aware in both directions:

- **Bare env** (no Telethon): keeps the original all-`False` fallback
  assertion — detection must degrade gracefully instead of raising.
- **Full env** (Telethon importable): asserts detection agrees with a
  direct `capabilities(types, functions)` symbol probe, and that the
  topology fallback for a primary-user client without an assistant still
  collapses every capability to `False`.

Deterministic in both environments; no skips needed.

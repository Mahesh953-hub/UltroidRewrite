"""Single owner of AI provider configuration.

Full narrative: see docs/HANDBOOK.md (module narratives appendix)."""

from __future__ import annotations

from typing import Any, ClassVar, Dict, List, Optional

try:  # the legacy environment source; absent until configs imports
    from pyUltroid.configs import Var
except Exception:  # pragma: no cover - depends on install order
    Var = None


__all__ = (
    "AgentConfig",
    "Var",
    "MIGRATED_KEY",
    "PROVIDERS",
)

#: Set once the legacy environment has been copied into the database.
MIGRATED_KEY = "AGENT_ENV_MIGRATED"

#: Which provider a bare ``.ai`` command uses. Unset means the registry's own
#: cost-tier routing decides.
DEFAULT_PROVIDER_KEY = "AGENT_PROVIDER"

#: Field suffixes every cloud provider understands.
_CLOUD_FIELDS = ("KEY", "MODEL", "BASE", "SYS")

#: Local-LLM (llama-family) servers use BASE rather than KEY, plus tuning.
_LLAMA_FIELDS = ("BASE", "MODEL", "CTX", "TEMP", "SYS")


#: provider id -> how its configuration is addressed.
#:
#: ``keys`` maps a field to the database keys that hold it, most specific
#: first. The first key that exists wins, which lets the dedicated Claude menu
#: (``AGENT_CLAUDE_*``) take precedence over the generic cloud namespace while
#: both remain settable.
#:
#: ``legacy`` maps a field to (environment attribute, database key) pairs whose
#: values :func:`migrate_env_config` copies into the first key of the field
#: when it is still unset.
PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {
        "keys": {
            "KEY": ("AGENT_CLOUD_OPENAI_KEY",),
            "MODEL": ("AGENT_CLOUD_OPENAI_MODEL",),
            "BASE": ("AGENT_CLOUD_OPENAI_BASE",),
            "SYS": ("AGENT_CLOUD_OPENAI_SYS",),
        },
        "legacy": {
            "KEY": (("OPENAI_API_KEY", "OPENAI_API_KEY"),),
            "MODEL": (("OPENAI_MODEL", "OPENAI_MODEL"),),
            "BASE": (("OPENAI_BASE_URL", "OPENAI_BASE_URL"),),
        },
        "label": "OpenAI",
        "needs_key": True,
    },
    "gemini": {
        "keys": {
            "KEY": ("AGENT_CLOUD_GEMINI_KEY",),
            "MODEL": ("AGENT_CLOUD_GEMINI_MODEL",),
            "BASE": ("AGENT_CLOUD_GEMINI_BASE",),
            "SYS": ("AGENT_CLOUD_GEMINI_SYS",),
        },
        "legacy": {
            "KEY": (("GEMINI_API_KEY", None), ("GOOGLE_API_KEY", None)),
            "MODEL": (("GEMINI_MODEL", None),),
        },
        "label": "Google Gemini",
        "needs_key": True,
    },
    "anthropic": {
        "keys": {
            "KEY": ("AGENT_CLAUDE_KEY", "AGENT_CLOUD_ANTHROPIC_KEY"),
            "MODEL": ("AGENT_CLAUDE_MODEL", "AGENT_CLOUD_ANTHROPIC_MODEL"),
            "BASE": ("AGENT_CLOUD_ANTHROPIC_BASE",),
            "SYS": ("AGENT_CLAUDE_SYS", "AGENT_CLOUD_ANTHROPIC_SYS"),
            "FALLBACK": ("AGENT_CLAUDE_FALLBACK",),
        },
        "legacy": {
            "KEY": (("ANTHROPIC_API_KEY", None),),
            "MODEL": (("ANTHROPIC_MODEL", None),),
        },
        "label": "Anthropic",
        "needs_key": True,
    },
    "groq": {
        "keys": {
            "KEY": ("AGENT_CLOUD_GROQ_KEY",),
            "MODEL": ("AGENT_CLOUD_GROQ_MODEL",),
            "BASE": ("AGENT_CLOUD_GROQ_BASE",),
            "SYS": ("AGENT_CLOUD_GROQ_SYS",),
        },
        "legacy": {
            "KEY": (("GROQ_API_KEY", None),),
            "MODEL": (("GROQ_MODEL", None),),
        },
        "label": "Groq",
        "needs_key": True,
    },
    "openrouter": {
        "keys": {
            "KEY": ("AGENT_CLOUD_OPENROUTER_KEY",),
            "MODEL": ("AGENT_CLOUD_OPENROUTER_MODEL",),
            "BASE": ("AGENT_CLOUD_OPENROUTER_BASE",),
            "SYS": ("AGENT_CLOUD_OPENROUTER_SYS",),
        },
        "legacy": {},
        "label": "OpenRouter",
        "needs_key": True,
        "registry_name": "openrouter",
    },
    "ollama": {
        "keys": {
            "BASE": ("AGENT_LLAMA_OLLAMA_BASE",),
            "MODEL": ("AGENT_LLAMA_OLLAMA_MODEL",),
            "CTX": ("AGENT_LLAMA_OLLAMA_CTX",),
            "TEMP": ("AGENT_LLAMA_OLLAMA_TEMP",),
            "SYS": ("AGENT_LLAMA_OLLAMA_SYS",),
        },
        "legacy": {
            "BASE": (("OLLAMA_URL", None),),
            "MODEL": (("OLLAMA_MODEL", "OLLAMA_MODEL"),),
        },
        "label": "Ollama",
        "needs_key": False,
    },
    "openai_compat": {
        "keys": {
            "KEY": ("AGENT_CLOUD_COMPAT_KEY",),
            "MODEL": ("AGENT_CLOUD_COMPAT_MODEL",),
            "BASE": ("AGENT_CLOUD_COMPAT_BASE",),
            "SYS": ("AGENT_CLOUD_COMPAT_SYS",),
        },
        "legacy": {
            "KEY": (("COMPAT_API_KEY", "COMPAT_API_KEY"),),
            "MODEL": (("COMPAT_MODEL", "COMPAT_MODEL"),),
            "BASE": (("COMPAT_BASE_URL", "COMPAT_BASE_URL"),),
        },
        "label": "OpenAI-compatible",
        "needs_key": True,
        "registry_name": "openai_compat",
    },
    "xai": {
        "keys": {
            "KEY": ("AGENT_CLOUD_XAI_KEY",),
            "MODEL": ("AGENT_CLOUD_XAI_MODEL",),
            "BASE": ("AGENT_CLOUD_XAI_BASE",),
            "SYS": ("AGENT_CLOUD_XAI_SYS",),
        },
        "legacy": {
            "KEY": (("XAI_API_KEY", None),),
            "MODEL": (("XAI_MODEL", None),),
            "BASE": (("XAI_BASE_URL", None),),
        },
        "label": "xAI",
        "needs_key": True,
    },
    "apimix": {
        "keys": {
            "KEY": ("AGENT_CLOUD_APIMIX_KEY",),
            "MODEL": ("AGENT_CLOUD_APIMIX_MODEL",),
            "BASE": ("AGENT_CLOUD_APIMIX_BASE",),
            "SYS": ("AGENT_CLOUD_APIMIX_SYS",),
        },
        "legacy": {
            "KEY": (("APIMIX_API_KEY", None),),
            "MODEL": (("APIMIX_MODEL", None),),
            "BASE": (("APIMIX_BASE_URL", None),),
        },
        "label": "apmix",
        "needs_key": True,
    },
    "meta_llama": {
        "keys": {
            "KEY": ("AGENT_CLOUD_META_KEY",),
            "MODEL": ("AGENT_CLOUD_META_MODEL",),
            "BASE": ("AGENT_CLOUD_META_BASE",),
            "SYS": ("AGENT_CLOUD_META_SYS",),
        },
        "legacy": {
            "KEY": (("META_LLAMA_API_KEY", None),),
            "MODEL": (("META_LLAMA_MODEL", None),),
            "BASE": (("META_LLAMA_BASE_URL", None),),
        },
        "label": "Meta Llama",
        "needs_key": True,
    },
}


def _provider_spec(provider: str) -> dict[str, Any]:
    name = str(provider).lower()
    if name == "compat":
        name = "openai_compat"
    spec = PROVIDERS.get(name)
    if spec is None:
        raise ValueError(
            f"Unknown AI provider {provider!r}; expected one of: "
            + ", ".join(sorted(PROVIDERS))
        )
    return spec


class AgentConfig:
    """The AI provider configuration store (``AGENT_*`` udB keys).

    Same shape as the dB layer's ``FeatureDB`` classes: one instance per
    feature (``agent_config``), with an injectable database for tests and
    the module-level functions kept as backward-compatible aliases.
    """

    CONFIG_KEY: ClassVar[str] = MIGRATED_KEY


    def _read(self, key: str) -> str | None:
        try:
            from pyUltroid import udB
        except Exception:
            return None
        if udB is None:
            return None
        try:
            return udB.get_key(key)
        except Exception:
            return None

    def _write(self, key: str, value: str) -> bool:
        try:
            from pyUltroid import udB
        except Exception:
            return False
        if udB is None:
            return False
        try:
            udB.set_key(key, value)
            return True
        except Exception:
            return False

    def config_field(
        self, provider: str, field: str, default: str | None = None
    ) -> str | None:
        """The configured value for *provider*'s *field*, or *default*.

        Only the database is read. The legacy environment reached the database
        through :meth:`migrate_env_config`, which runs once at startup.
        """
        spec = _provider_spec(provider)
        for key in spec["keys"].get(field, ()):  # first key holding a value wins
            value = self._read(key)
            if value:
                return value
        return default

    def config_set(self, provider: str, field: str, value: str | None) -> bool:
        """Write *field* for *provider* to its first database key."""
        spec = _provider_spec(provider)
        keys = spec["keys"].get(field)
        if not keys:
            return False
        if value is None or value == "":
            return self.config_clear(provider, field)
        return self._write(keys[0], str(value))

    def config_clear(self, provider: str, field: str) -> bool:
        spec = _provider_spec(provider)
        from pyUltroid import udB

        if udB is None:
            return False
        cleared = False
        for key in spec["keys"].get(field, ()):
            try:
                udB.del_key(key)
                cleared = True
            except Exception:
                pass
        return cleared

    def config_source(self, provider: str, field: str) -> str | None:
        """Which database key holds *field*, for showing provenance in the UI."""
        spec = _provider_spec(provider)
        for key in spec["keys"].get(field, ()):
            if self._read(key):
                return key
        return None

    def _legacy_value(self, env_attr: str, db_key: str | None):
        if Var is not None:
            value = getattr(Var, env_attr, None)
            if value:
                return value
        if db_key:
            value = self._read(db_key)
            if value:
                return value
        return None

    def config_migrate(self, force: bool = False) -> list[str]:
        """Copy legacy environment and database values into the unified keys.

        Runs once; the ``AGENT_ENV_MIGRATED`` flag guards it. Values already
        set through the menu are never overwritten, so the migration is
        additive for existing deployments. Returns the keys that were written.

        After this has run, the database is the only configuration source: an
        environment variable edited later has no effect. Deployments that
        change configuration through the environment should set the
        corresponding ``AGENT_*`` key instead, or force a re-run at their own
        risk.
        """
        if not force and self._read(MIGRATED_KEY):
            return []
        written: list[str] = []
        failed = 0
        for provider, spec in PROVIDERS.items():
            for field, entries in spec.get("legacy", {}).items():
                keys = spec["keys"].get(field, ())
                if not keys:
                    continue
                if any(self._read(key) for key in keys):
                    # Already configured through the menu, under any of the
                    # field's keys. Writing keys[0] anyway would let a legacy
                    # environment value take precedence over the menu's choice.
                    continue
                for env_attr, db_key in entries:
                    value = self._legacy_value(env_attr, db_key)
                    if value:
                        if self._write(keys[0], str(value)):
                            written.append(keys[0])
                        else:
                            failed += 1
                        break
        try:
            from pyUltroid import udB
        except Exception:
            udB = None
        if udB is not None and failed == 0:
            # A failed write must not set the flag: the next boot re-runs and
            # retries exactly the writes that failed, because the ones that
            # succeeded now hold a value and are skipped.
            self._write(MIGRATED_KEY, "1")
        return written

    def config_ready(self, provider: str) -> bool:
        """Whether *provider* has what it needs to run.

        Local servers need only a base URL; cloud providers need a key.
        """
        spec = _provider_spec(provider)
        if spec["needs_key"]:
            return bool(self.config_field(provider, "KEY"))
        return bool(self.config_field(provider, "BASE"))

    def config_status(self) -> dict[str, dict[str, Any]]:
        """Effective configuration per provider, for ``.aiconf`` and the menu.

        Every value is the one a request would actually use, so this is the
        place to look when a command does not behave as the menu suggests.
        """
        status: dict[str, dict[str, Any]] = {}
        for provider, spec in PROVIDERS.items():
            fields = {}
            for field in spec["keys"]:
                fields[field] = self.config_field(provider, field)
            status[provider] = {
                "label": spec["label"],
                "configured": self.config_ready(provider),
                "fields": fields,
                "sources": {
                    field: self.config_source(provider, field) for field in spec["keys"]
                },
            }
        return status

    def config_default_provider(self) -> str | None:
        """Resolve a provider pin, or configured apimix as the default."""
        value = self._read(DEFAULT_PROVIDER_KEY)
        if value:
            value = str(value).lower()
            if value == "compat":
                value = "openai_compat"
            if value in PROVIDERS:
                return value
        # A configured apimix account is the preferred route for unpinned
        # .ai/.ask requests; a saved explicit provider choice still wins.
        return "apimix" if self.config_ready("apimix") else None

    def config_use_default(
        self, provider: str | None, *, require_configured: bool = False
    ) -> bool:
        """Pin the provider for bare AI commands, or clear it with ``None``.

        ``require_configured`` prevents the UI from recording a pin that can
        never run. Automatic routing remains the correct choice for
        unconfigured setups.
        """
        if provider is None:
            from pyUltroid import udB

            if udB is None:
                return False
            try:
                udB.del_key(DEFAULT_PROVIDER_KEY)
                return True
            except Exception:
                return False
        spec = _provider_spec(provider)  # canonicalizes ``compat``
        if require_configured and not self.config_ready(provider):
            raise ValueError(
                f"Provider {provider!r} is not configured; configure it first or "
                "keep automatic routing."
            )
        return self._write(
            DEFAULT_PROVIDER_KEY, str(spec.get("registry_name", provider)).lower()
        )

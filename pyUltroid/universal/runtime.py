"""Explicit runtime state shared by universal dispatcher plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .tl import RuntimeCapabilities


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    app_version: str = ""
    device_model: str = "Ultroid"


@dataclass(slots=True)
class RuntimeContext:
    client: Any
    assistant: Any
    user: Any
    app: Any
    db: Any
    dispatcher: Any
    settings: RuntimeSettings
    capabilities: RuntimeCapabilities = field(default_factory=RuntimeCapabilities)

    def is_authorized(self, user_id: int | None) -> bool:
        """Return whether an action came from the owner or configured sudo."""
        if not user_id or self.db is None:
            return False
        owner = self.db.get_key("OWNER_ID")
        sudos = self.db.get_key("SUDOS") or []
        allowed = set()
        for value in (owner, *sudos):
            try:
                allowed.add(int(value))
            except (TypeError, ValueError):
                continue
        return user_id in allowed

"""Small, typed persistence helpers backed by Ultroid's existing udB."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


class UDBStore:
    def __init__(self, db: Any, key: str, default: Any) -> None:
        self.db = db
        self.key = key
        self.default = default

    def get(self) -> Any:
        if self.db is None:
            return deepcopy(self.default)
        value = self.db.get_key(self.key)
        if not isinstance(value, type(self.default)):
            return deepcopy(self.default)
        return deepcopy(value)

    def set(self, value: Any) -> None:
        if self.db is not None:
            self.db.set_key(self.key, value)

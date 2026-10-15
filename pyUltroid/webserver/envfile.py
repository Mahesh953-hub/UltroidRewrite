# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

"""The ``.env`` document: one path-bound object, not a bag of free functions.

:mod:`pyUltroid.webserver.envfile` is the dependency leaf of the package -
stdlib only, no sibling imports - so that everything else may import it
without dragging in fastapi, telethon or uvicorn.

The module-level functions below are thin wrappers over a lazily created
default :class:`EnvFile`; they exist because ``tests/test_webserver.py`` and
``server.py`` import them directly.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Final, Mapping

REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]

SETUP_KEYS: Final[tuple[str, ...]] = (
    "API_ID",
    "API_HASH",
    "BOT_TOKEN",
    "SESSION",
    "LOG_CHANNEL",
)

ENV_FILE_VAR: Final[str] = "PYULTROID_ENV_FILE"

REQUIRED_KEYS: Final[tuple[str, ...]] = ("API_ID", "API_HASH", "SESSION")


class EnvFile:
    """A single ``.env`` document bound to one path.

    Parsing, quoting and the atomic replace are *private* to the instance:
    the class is the only thing that knows how a line is shaped. Callers get
    exactly two verbs - :meth:`read` and :meth:`write` - plus :meth:`export`.
    """

    __slots__ = ("path",)

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path: Path = Path(path) if path is not None else self.default_path()

    # -- location

    @staticmethod
    def default_path() -> Path:
        """``$PYULTROID_ENV_FILE`` when set, else ``<repo>/.env``."""
        override = os.environ.get(ENV_FILE_VAR)
        return Path(override) if override else REPO_ROOT / ".env"

    # -- reading

    def read(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        return self.parse(self.path.read_text(encoding="utf-8"))

    @staticmethod
    def parse(text: str) -> dict[str, str]:
        """Parse dotenv text.

        Deliberately minimal and deliberately unchanged: no inline-comment
        stripping and no ``export`` prefix handling, because the writer never
        produces either.
        """
        data: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = _unquote(value.strip())
        return data

    def merged_with_environ(self) -> dict[str, str]:
        """File contents overlaid by ``os.environ`` - the real env wins."""
        merged = self.read()
        merged.update(os.environ)
        return merged

    def missing(self) -> tuple[str, ...]:
        """Required keys absent (or non-numeric, for ``API_ID``)."""
        merged = self.merged_with_environ()
        absent: list[str] = []
        for key in REQUIRED_KEYS:
            value = merged.get(key)
            usable = _is_int(value) if key == "API_ID" else bool(value)
            if not usable:
                absent.append(key)
        return tuple(absent)

    def complete(self) -> bool:
        """True when every required credential is present and usable."""
        return not self.missing()

    # -- writing

    def write(
        self, updates: Mapping[str, object], *, apply_to_environ: bool = True
    ) -> Path:
        """Merge *updates* in place, preserving comments and key order."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        content = self._render(updates)
        self._replace(content)
        if apply_to_environ:
            for key, value in updates.items():
                os.environ[str(key)] = str(value)
        return self.path

    def export(self) -> int:
        """Seed ``os.environ`` from the file without overwriting real values."""
        count = 0
        for key, value in self.read().items():
            if key not in os.environ:
                os.environ[key] = value
                count += 1
        return count

    # -- internals

    def _render(self, updates: Mapping[str, object]) -> str:
        existing = (
            self.path.read_text(encoding="utf-8").splitlines()
            if self.path.exists()
            else []
        )
        remaining = {str(k): str(v) for k, v in updates.items()}
        out: list[str] = []
        for line in existing:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in remaining:
                    out.append(f"{key}={_format_value(remaining.pop(key))}")
                    continue
            out.append(line)
        out.extend(f"{key}={_format_value(value)}" for key, value in remaining.items())
        return "\n".join(out) + "\n"

    def _replace(self, content: str) -> None:
        """Same-directory temp file, fsync, chmod 0600, then atomic rename."""
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=self.path.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def __repr__(self) -> str:
        return f"<EnvFile {self.path}>"


# -- module surface -------------------------------------------------------


def default_env_path() -> Path:
    return EnvFile.default_path()


def parse_env_text(text: str) -> dict:
    return EnvFile.parse(text)


def read_env(path=None) -> dict:
    return EnvFile(path).read()


def write_env_atomic(updates: dict, path=None, *, apply_to_environ=True) -> Path:
    return EnvFile(path).write(updates, apply_to_environ=apply_to_environ)


def _unquote(value: str) -> str:
    """Strip a matching outer quote pair; resolve escapes inside double quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        if value[0] == "'":
            return inner
        out: list[str] = []
        i = 0
        while i < len(inner):
            if inner[i] == "\\" and i + 1 < len(inner):
                out.append(inner[i + 1])
                i += 2
            else:
                out.append(inner[i])
                i += 1
        return "".join(out)
    return value


def _format_value(value) -> str:
    text = str(value)
    if not text:
        return '""'
    if any(ch.isspace() for ch in text) or "#" in text:
        escaped = text.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _is_int(value: object) -> bool:
    try:
        return bool(int(value)) if value else False
    except (TypeError, ValueError):
        return False
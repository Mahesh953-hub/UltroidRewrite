import os
from glob import glob
from typing import Any

from yaml import safe_load

from pyUltroid import ULTConfig, udB


class StringStore:
    """The language-string service: loads YAML files, resolves keys, caches.

    Owns ``languages`` and every lookup so the resolution policy (missing
    key -> English -> translated + cached) lives in one typed place.
    """

    __slots__ = ("languages", "path")

    def __init__(self, languages: dict | None = None, path: str = "strings/strings/{}.yml") -> None:
        self.languages: dict[str, dict] = languages if languages is not None else {}
        self.path = path

    def load(self, file: str) -> None:
        """Load one ``.yml`` language file into ``languages[code]``."""
        if not file.endswith(".yml"):
            return
        if not os.path.exists(file):
            file = self.path.format("en")
        code = file.split("/")[-1].split("\\")[-1][:-4]
        self.languages[code] = safe_load(open(file, encoding="UTF-8"))

    def get(self, key: str, *, default: bool = True, lang: str | None = None) -> Any:
        """Resolve *key* for *lang*, falling back to English then caching."""
        lang = lang or ULTConfig.lang or "en"
        if lang in self.languages and key in self.languages[lang]:
            return self.languages[lang][key]
        if "en" not in self.languages or key not in self.languages["en"]:
            if not default:
                return None
            return f"Warning: could not load any string with the key `{key}`"
        return self.languages["en"][key]

    def help_of(self, key: str) -> str | None:
        """The ``help_<key>`` document, prefixed with the command banner."""
        doc = self.get(f"help_{key}", default=False)
        if doc:
            return self.get("cmda") + doc
        return None

    def all_languages(self) -> dict[str, dict]:
        """Load every shipped language file and return their metadata."""
        for file in glob("strings/strings/*yml"):
            self.load(file)
        return {
            code: {
                "name": self.languages[code]["name"],
                "natively": self.languages[code]["natively"],
                "authors": self.languages[code]["authors"],
            }
            for code in self.languages
        }


#: The process-wide string store.
store = StringStore()

ULTConfig.lang = (udB.get_key("language") if udB else None) or os.getenv(
    "LANGUAGE", "en"
)

store.load(store.path.format(ULTConfig.lang))


# -- module surface (plugins consume these by name) --------------------------
def get_string(key: str, _res: bool = True) -> Any:
    return store.get(key, default=_res)


def get_help(key: str) -> str | None:
    return store.help_of(key)


def get_languages() -> dict[str, dict]:
    return store.all_languages()


def load(file: str) -> None:
    store.load(file)

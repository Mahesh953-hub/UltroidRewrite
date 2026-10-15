"""Base for the per-feature database classes.

Every feature declares the udB key it owns and implements its own verbs. The
database is imported once here rather than resolved through a per-instance
wrapper: there is nothing to inject, because a feature is stateless and every
instance of it reads the same live database.
"""

from .. import udB


class FeatureDB:
    """The key a feature owns, and the one read it is built on."""

    key: str = ""

    def get_stuff(self):
        return udB.get_key(self.key) or {}


class KeyManager:
    """A typed accessor over one udB key, used by features that need casting."""

    def __init__(self, key, cast=None) -> None:
        self._key = key
        self._cast = cast

    def get(self):
        data = udB.get_key(self._key)
        if self._cast and not isinstance(data, self._cast):
            return [data] if self._cast == list else self._cast(data)
        return data or (self._cast() if callable(self._cast) else self._cast)

    def get_child(self, key):
        return self.get()[key]

    def count(self):
        return len(self.get())

    def add(self, item):
        content = self.get()
        if content is None and callable(type(item)):
            content = type(item)()
        if isinstance(content, dict) and isinstance(item, dict):
            content.update(item)
        elif isinstance(content, list) and item not in content:
            content.append(item)
        else:
            return
        udB.set_key(self._key, content)

    def remove(self, item):
        content = self.get()
        if isinstance(content, list) and item in content:
            content.remove(item)
        elif isinstance(content, dict) and content.get(item):
            del content[item]
        else:
            return
        udB.set_key(self._key, content)

    def contains(self, item):
        return item in self.get()

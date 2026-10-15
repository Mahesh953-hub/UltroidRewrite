# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class SnipsDB(FeatureDB):
    """Snip (saved command) storage (udB key ``SNIP``)."""

    key = "SNIP"

    def get_all(self):
        return udB.get_key(self.key) or {}

    def add(self, word, msg, media, button):
        ok = self.get_all()
        ok.update({word: {"msg": msg, "media": media, "button": button}})
        udB.set_key(self.key, ok)

    def remove(self, word):
        ok = self.get_all()
        if ok.get(word):
            ok.pop(word)
            udB.set_key(self.key, ok)

    def get(self, word):
        ok = self.get_all()
        if ok.get(word):
            return ok[word]
        return False

    def list(self):
        return "".join(f"👉 ${z}\n" for z in self.get_all())

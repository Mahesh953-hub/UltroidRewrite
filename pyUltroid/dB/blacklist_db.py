# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class BlacklistDB(FeatureDB):
    """Per-chat blacklisted words (udB key ``BLACKLIST_DB``)."""

    key = "BLACKLIST_DB"

    def get_stuff(self):
        return udB.get_key(self.key) or {}

    def add(self, chat, word):
        ok = self.get_stuff()
        if ok.get(chat):
            for z in word.split():
                if z not in ok[chat]:
                    ok[chat].append(z)
        else:
            ok.update({chat: [word]})
        return udB.set_key(self.key, ok)

    def remove(self, chat, word):
        ok = self.get_stuff()
        if ok.get(chat) and word in ok[chat]:
            ok[chat].remove(word)
            return udB.set_key(self.key, ok)

    def list(self, chat):
        ok = self.get_stuff()
        if ok.get(chat):
            txt = "".join(f"👉`{z}`\n" for z in ok[chat])
            if txt:
                return txt

    def get(self, chat):
        ok = self.get_stuff()
        if ok.get(chat):
            return ok[chat]

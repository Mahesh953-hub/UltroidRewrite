# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class FilterDB(FeatureDB):
    """Per-chat filter storage (udB key ``FILTERS``)."""

    key = "FILTERS"

    def get_stuff(self):
        return udB.get_key(self.key) or {}

    def add(self, chat, word, msg, media, button):
        ok = self.get_stuff()
        if ok.get(chat):
            ok[chat].update({word: {"msg": msg, "media": media, "button": button}})
        else:
            ok.update({chat: {word: {"msg": msg, "media": media, "button": button}}})
        udB.set_key(self.key, ok)

    def remove(self, chat, word):
        ok = self.get_stuff()
        if ok.get(chat) and ok[chat].get(word):
            ok[chat].pop(word)
            udB.set_key(self.key, ok)

    def remove_all(self, chat):
        ok = self.get_stuff()
        if ok.get(chat):
            ok.pop(chat)
            udB.set_key(self.key, ok)

    def get(self, chat):
        ok = self.get_stuff()
        if ok.get(chat):
            return ok[chat]

    def list(self, chat):
        ok = self.get_stuff()
        if ok.get(chat):
            return "".join(f"👉 `{z}`\n" for z in ok[chat])

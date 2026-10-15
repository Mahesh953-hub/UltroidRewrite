# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class NotesDB(FeatureDB):
    """Per-chat note storage (udB key ``NOTE``); chat ids are ints."""

    key = "NOTE"

    def get_stuff(self):
        return udB.get_key(self.key) or {}

    def add(self, chat, word, msg, media, button):
        ok = self.get_stuff()
        if ok.get(int(chat)):
            ok[int(chat)].update({word: {"msg": msg, "media": media, "button": button}})
        else:
            ok.update({int(chat): {word: {"msg": msg, "media": media, "button": button}}})
        udB.set_key(self.key, ok)

    def remove(self, chat, word):
        ok = self.get_stuff()
        if ok.get(int(chat)) and ok[int(chat)].get(word):
            ok[int(chat)].pop(word)
            return udB.set_key(self.key, ok)

    def remove_all(self, chat):
        ok = self.get_stuff()
        if ok.get(int(chat)):
            ok.pop(int(chat))
            return udB.set_key(self.key, ok)

    def get(self, chat, word):
        ok = self.get_stuff()
        if ok.get(int(chat)) and ok[int(chat)].get(word):
            return ok[int(chat)][word]

    def list(self, chat):
        ok = self.get_stuff()
        if ok.get(int(chat)):
            return "".join(f"👉 #{z}\n" for z in ok[chat])

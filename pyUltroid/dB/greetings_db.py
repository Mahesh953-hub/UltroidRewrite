# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class GreetingsDB(FeatureDB):
    """Welcome/goodbye/thanks settings (udB keys ``WELCOME``, ``GOODBYE``,
    ``THANK_MEMBERS``)."""

    def _stuff(self, key):
        return udB.get_key(key) or {}

    def _set(self, key, value):
        return udB.set_key(key, value)

    def add_welcome(self, chat, msg, media, button):
        ok = self._stuff("WELCOME")
        ok.update({chat: {"welcome": msg, "media": media, "button": button}})
        return self._set("WELCOME", ok)

    def get_welcome(self, chat):
        return self._stuff("WELCOME").get(chat)

    def delete_welcome(self, chat):
        ok = self._stuff("WELCOME")
        if ok.get(chat):
            ok.pop(chat)
            return self._set("WELCOME", ok)

    def add_goodbye(self, chat, msg, media, button):
        ok = self._stuff("GOODBYE")
        ok.update({chat: {"goodbye": msg, "media": media, "button": button}})
        return self._set("GOODBYE", ok)

    def get_goodbye(self, chat):
        return self._stuff("GOODBYE").get(chat)

    def delete_goodbye(self, chat):
        ok = self._stuff("GOODBYE")
        if ok.get(chat):
            ok.pop(chat)
            return self._set("GOODBYE", ok)

    def add_thanks(self, chat):
        x = self._stuff("THANK_MEMBERS")
        x.update({chat: True})
        return self._set("THANK_MEMBERS", x)

    def remove_thanks(self, chat):
        x = self._stuff("THANK_MEMBERS")
        if x.get(chat):
            x.pop(chat)
            return self._set("THANK_MEMBERS", x)

    def must_thank(self, chat):
        return self._stuff("THANK_MEMBERS").get(chat)

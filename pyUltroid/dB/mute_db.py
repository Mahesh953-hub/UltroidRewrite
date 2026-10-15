# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class MuteDB(FeatureDB):
    """Per-chat muted-user storage (udB key ``MUTE``)."""

    key = "MUTE"

    def get_muted(self):
        return udB.get_key(self.key) or {}

    def mute(self, chat, id):
        ok = self.get_muted()
        if ok.get(chat):
            if id not in ok[chat]:
                ok[chat].append(id)
        else:
            ok.update({chat: [id]})
        return udB.set_key(self.key, ok)

    def unmute(self, chat, id):
        ok = self.get_muted()
        if ok.get(chat) and id in ok[chat]:
            ok[chat].remove(id)
        return udB.set_key(self.key, ok)

    def is_muted(self, chat, id):
        ok = self.get_muted()
        return bool(ok.get(chat) and id in ok[chat])

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class ForceSubDB(FeatureDB):
    """Forced-subscribe chats (udB key ``FORCESUB``)."""

    key = "FORCESUB"

    def get_chats(self):
        return udB.get_key(self.key) or {}

    def add(self, chat_id, chattojoin):
        omk = self.get_chats()
        omk.update({chat_id: chattojoin})
        return udB.set_key(self.key, omk)

    def get_setting(self, chat_id):
        omk = self.get_chats()
        if chat_id in omk.keys():
            return omk[chat_id]

    def remove(self, chat_id):
        omk = self.get_chats()
        if chat_id in omk.keys():
            try:
                del omk[chat_id]
                return udB.set_key(self.key, omk)
            except KeyError:
                return False

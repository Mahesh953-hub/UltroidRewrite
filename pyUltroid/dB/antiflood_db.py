# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class AntifloodDB(FeatureDB):
    """Per-chat flood limits (udB key ``ANTIFLOOD``)."""

    key = "ANTIFLOOD"

    def get_flood(self):
        return udB.get_key(self.key) or {}

    def set_flood(self, chat_id, limit):
        omk = self.get_flood()
        omk.update({chat_id: limit})
        return udB.set_key(self.key, omk)

    def get_flood_limit(self, chat_id):
        omk = self.get_flood()
        if chat_id in omk.keys():
            return omk[chat_id]

    def rem_flood(self, chat_id):
        omk = self.get_flood()
        if chat_id in omk.keys():
            del omk[chat_id]
            return udB.set_key(self.key, omk)

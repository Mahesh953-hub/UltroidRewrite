# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class WarnDB(FeatureDB):
    """Per-chat user warnings (udB key ``WARNS``)."""

    key = "WARNS"

    def get_stuff(self):
        return udB.get_key(self.key) or {}

    def add(self, chat, user, count, reason):
        x = self.get_stuff()
        try:
            x[chat].update({user: [count, reason]})
        except Exception:
            x.update({chat: {user: [count, reason]}})
        return udB.set_key(self.key, x)

    def warns(self, chat, user):
        x = self.get_stuff()
        try:
            count, reason = x[chat][user][0], x[chat][user][1]
            return count, reason
        except Exception:
            return 0, None

    def reset(self, chat, user):
        x = self.get_stuff()
        try:
            x[chat].pop(user)
            return udB.set_key(self.key, x)
        except Exception:
            return

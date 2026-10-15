# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from datetime import datetime as dt

from .base import FeatureDB, udB

class AfkDB(FeatureDB):
    """The AFK marker (udB key ``AFK_DB``)."""

    key = "AFK_DB"

    def get_stuff(self):
        return udB.get_key(self.key) or []

    def add(self, msg, media_type, media):
        time = dt.now().strftime("%b %d %Y %I:%M:%S%p")
        udB.set_key(self.key, [msg, media_type, media, time])
        return

    def is_afk(self):
        afk = self.get_stuff()
        if afk:
            start_time = dt.strptime(afk[3], "%b %d %Y %I:%M:%S%p")
            afk_since = str(dt.now().replace(microsecond=0) - start_time)
            return afk[0], afk[1], afk[2], afk_since
        return False

    def delete(self):
        return udB.del_key(self.key)

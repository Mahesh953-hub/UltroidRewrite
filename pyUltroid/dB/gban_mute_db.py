# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class GbanMuteDB(FeatureDB):
    """Global bans and mutes (udB keys ``GBAN`` and ``GMUTE``)."""

    key = "GBAN"


    def list_gbanned(self):
        return udB.get_key("GBAN") or {}

    def gban(self, user, reason):
        ok = self.list_gbanned()
        ok.update({int(user): reason or "No Reason. "})
        return udB.set_key("GBAN", ok)

    def ungban(self, user):
        ok = self.list_gbanned()
        if ok.get(int(user)):
            del ok[int(user)]
            return udB.set_key("GBAN", ok)

    def is_gbanned(self, user):
        ok = self.list_gbanned()
        if ok.get(int(user)):
            return ok[int(user)]

    def list_gmuted(self):
        return udB.get_key("GMUTE") or []

    def gmute(self, user):
        ok = self.list_gmuted()
        ok.append(int(user))
        return udB.set_key("GMUTE", ok)

    def ungmute(self, user):
        ok = self.list_gmuted()
        if user in ok:
            ok.remove(int(user))
            return udB.set_key("GMUTE", ok)

    def is_gmuted(self, user):
        return int(user) in self.list_gmuted()

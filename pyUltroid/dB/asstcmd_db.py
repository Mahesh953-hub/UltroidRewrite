# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class AsstCmdDB(FeatureDB):
    """Assistant-bot command storage (udB key ``ASST_CMDS``)."""

    key = "ASST_CMDS"

    def get_stuff(self):
        return udB.get_key(self.key) or {}

    def add(self, cmd, msg, media, button):
        ok = self.get_stuff()
        ok.update({cmd: {"msg": msg, "media": media, "button": button}})
        return udB.set_key(self.key, ok)

    def remove(self, cmd):
        ok = self.get_stuff()
        if ok.get(cmd):
            ok.pop(cmd)
            return udB.set_key(self.key, ok)

    def reply(self, cmd):
        ok = self.get_stuff()
        if ok.get(cmd):
            okk = ok[cmd]
            return okk["msg"], okk["media"], okk["button"] if ok.get("button") else None
        return

    def list(self):
        return self.get_stuff().keys()

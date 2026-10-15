# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class EchoDB(FeatureDB):
    """Per-chat echo targets (udB key ``ECHO``); ids are ints."""

    key = "ECHO"

    def get_stuff(self):
        return udB.get_key(self.key) or {}

    def add(self, chat, user):
        x = self.get_stuff()
        if k := x.get(int(chat)):
            if user not in k:
                k.append(int(user))
            x.update({int(chat): k})
        else:
            x.update({int(chat): [int(user)]})
        return udB.set_key(self.key, x)

    def remove(self, chat, user):
        x = self.get_stuff()
        if k := x.get(int(chat)):
            if user in k:
                k.remove(int(user))
            x.update({int(chat): k})
        return udB.set_key(self.key, x)

    def check(self, chat, user):
        x = self.get_stuff()
        if (k := x.get(int(chat))) and int(user) in k:
            return True

    def list(self, chat):
        return self.get_stuff().get(int(chat))

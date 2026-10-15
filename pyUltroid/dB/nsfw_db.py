# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class NsfwDB(FeatureDB):
    key = "NSFW"
    """NSFW/profanity chat actions (udB keys ``NSFW`` and ``PROFANITY``)."""


    def get_stuff(self, key="NSFW"):
        return udB.get_key(key) or {}

    def nsfw_chat(self, chat, action):
        x = self.get_stuff()
        x.update({chat: action})
        return udB.set_key("NSFW", x)

    def rem_nsfw(self, chat):
        x = self.get_stuff()
        if x.get(chat):
            x.pop(chat)
            return udB.set_key("NSFW", x)

    def is_nsfw(self, chat):
        x = self.get_stuff()
        if x.get(chat):
            return x[chat]

    def profan_chat(self, chat, action):
        x = self.get_stuff("PROFANITY")
        x.update({chat: action})
        return udB.set_key("PROFANITY", x)

    def rem_profan(self, chat):
        x = self.get_stuff("PROFANITY")
        if x.get(chat):
            x.pop(chat)
            return udB.set_key("PROFANITY", x)

    def is_profan(self, chat):
        x = self.get_stuff("PROFANITY")
        if x.get(chat):
            return x[chat]

# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class BotChatDB(FeatureDB):
    """Bot-chat bookkeeping (udB key ``BOTCHAT``)."""

    key = "BOTCHAT"

    def get_stuff(self):
        return udB.get_key(self.key) or {}

    def add(self, msg_id, user_id):
        ok = self.get_stuff()
        ok.update({msg_id: user_id})
        return udB.set_key(self.key, ok)

    def get_who(self, msg_id):
        ok = self.get_stuff()
        if ok.get(msg_id):
            return ok[msg_id]

    def tag_add(self, msg, chat, user):
        ok = self.get_stuff()
        if not ok.get("TAG"):
            ok.update({"TAG": {msg: [chat, user]}})
        else:
            ok["TAG"].update({msg: [chat, user]})
        return udB.set_key(self.key, ok)

    def who_tag(self, msg):
        ok = self.get_stuff()
        if ok.get("TAG") and ok["TAG"].get(msg):
            return ok["TAG"][msg]
        return False, False

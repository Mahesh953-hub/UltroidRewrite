from .base import FeatureDB, udB

class BlacklistChatDB(FeatureDB):
    """Globally blacklisted chats (udB key ``BLACKLIST_CHATS``)."""

    key = "BLACKLIST_CHATS"

    def _chats(self):
        return udB.get_key(self.key) or []

    def add(self, chat_id):
        chat = self._chats()
        if chat_id not in chat:
            chat.append(chat_id)
            return udB.set_key(self.key, chat)

    def remove(self, chat_id):
        chat = self._chats()
        if chat_id in chat:
            chat.remove(chat_id)
            return udB.set_key(self.key, chat)

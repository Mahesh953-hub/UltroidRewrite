# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class FileStoreDB(FeatureDB):
    """Stored file-message hashes (udB key ``FILE_STORE``)."""

    key = "FILE_STORE"

    def get_stored(self):
        return udB.get_key(self.key) or {}

    def store(self, hash, msg_id):
        all = self.get_stored()
        all.update({hash: msg_id})
        return udB.set_key(self.key, all)

    def list_all(self):
        return list(self.get_stored().keys())

    def get(self, hash):
        all = self.get_stored()
        if all.get(hash):
            return all[hash]

    def delete(self, hash):
        all = self.get_stored()
        all.pop(hash)
        return udB.set_key(self.key, all)

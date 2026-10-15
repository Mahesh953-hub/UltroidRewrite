# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

from .base import FeatureDB, udB

class VcSudosDB(FeatureDB):
    """VC sudo users (udB key ``VC_SUDOS``)."""

    key = "VC_SUDOS"

    def get_vcsudos(self):
        return udB.get_key(self.key) or []

    def is_vcsudo(self, id):
        return id in self.get_vcsudos()

    def add(self, id):
        sudos = self.get_vcsudos()
        sudos.append(id)
        return udB.set_key(self.key, sudos)

    def delete(self, id):
        if self.is_vcsudo(id):
            sudos = self.get_vcsudos()
            sudos.remove(id)
            return udB.set_key(self.key, sudos)

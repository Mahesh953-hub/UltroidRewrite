# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

import time

from .base import FeatureDB, udB

class GuestDB(FeatureDB):
    """Guest-session store (udB key ``GUEST_SESSIONS``)."""

    key = "GUEST_SESSIONS"

    def get_guests(self):
        sessions = udB.get_key(self.key)
        if not isinstance(sessions, dict):
            return {}
        # Convert keys to integers since JSON serialization might turn them
        # into strings
        return {int(k): v for k, v in sessions.items()}

    def add_guest(self, user_id: int, expiry_seconds: int = None, scope: str = "all"):
        sessions = self.get_guests()
        now = time.time()
        expiry = (now + expiry_seconds) if expiry_seconds is not None else None
        sessions[user_id] = {"scope": scope, "start": now, "expiry": expiry}
        udB.set_key(self.key, sessions)

    def remove_guest(self, user_id: int) -> bool:
        sessions = self.get_guests()
        if user_id in sessions:
            del sessions[user_id]
            udB.set_key(self.key, sessions)
            return True
        return False

    def is_guest(self, user_id: int) -> bool:
        sessions = self.get_guests()
        if user_id in sessions:
            sess = sessions[user_id]
            expiry = sess.get("expiry")
            if expiry is not None and time.time() > expiry:
                self.remove_guest(user_id)
                return False
            return True
        return False

    def clean_expired(self) -> list:
        sessions = self.get_guests()
        expired = []
        now = time.time()
        for user_id in list(sessions.keys()):
            sess = sessions[user_id]
            expiry = sess.get("expiry")
            if expiry is not None and now > expiry:
                del sessions[user_id]
                expired.append(user_id)
        if expired:
            udB.set_key(self.key, sessions)
        return expired

#: the process-wide guest store.
guest_db = GuestDB()

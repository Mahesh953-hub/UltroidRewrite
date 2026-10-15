#  Copyright (c) 2025 AshokShau
#  Licensed under the GNU AGPL v3.0: https://www.gnu.org/licenses/agpl-3.0.html
#  Part of the TgMusicBot project. All rights reserved where applicable.

from collections import deque
from typing import Any, Optional

from ._dataclass import CachedTrack


class ChatCacher:
    def __init__(self):
        self.chat_cache: dict[int, dict[str, Any]] = {}

    def add_song(self, chat_id: int, song: CachedTrack) -> CachedTrack:
        if chat_id not in self.chat_cache:
            self.chat_cache[chat_id] = {"is_active": True, "queue": deque()}
        self.chat_cache[chat_id]["queue"].append(song)
        return song

    def get_next_song(self, chat_id: int) -> CachedTrack | None:
        queue = self.chat_cache.get(chat_id, {}).get("queue", deque())
        return queue[1] if len(queue) > 1 else None

    def get_current_song(self, chat_id: int) -> CachedTrack | None:
        queue = self.chat_cache.get(chat_id, {}).get("queue", deque())
        return queue[0] if queue else None

    def remove_current_song(self, chat_id: int) -> CachedTrack | None:
        queue = self.chat_cache.get(chat_id, {}).get("queue", deque())
        return queue.popleft() if queue else None

    def is_active(self, chat_id: int) -> bool:
        return self.chat_cache.get(chat_id, {}).get("is_active", False)

    def set_active(self, chat_id: int, active: bool):
        if chat_id not in self.chat_cache:
            self.chat_cache[chat_id] = {"is_active": active, "queue": deque()}
        else:
            self.chat_cache[chat_id]["is_active"] = active
            if "queue" not in self.chat_cache[chat_id]:
                self.chat_cache[chat_id]["queue"] = deque()

    def clear_chat(self, chat_id: int):
        self.chat_cache.pop(chat_id, None)

    def clear_all(self):
        self.chat_cache.clear()

    def count(self, chat_id: int) -> int:
        return len(self.chat_cache.get(chat_id, {}).get("queue", deque()))

    def set_loop_count(self, chat_id: int, loop: int) -> bool:
        # Was `if queue := ...`, which is falsy for an EMPTY deque - so
        # clearing the loop count on an idle chat silently did nothing.
        state = self.chat_cache.get(chat_id)
        if state is None:
            return False
        state["is_active"] = bool(state.get("is_active")) or loop > 0
        state["loop"] = loop
        return True

    def get_loop_count(self, chat_id: int) -> int:
        return int(self.chat_cache.get(chat_id, {}).get("loop", 0) or 0)

    def remove_track(self, chat_id: int, queue_index: int) -> bool:
        queue = self.chat_cache.get(chat_id, {}).get("queue", deque())
        if len(queue) > queue_index:
            queue_list = list(queue)
            queue_list.pop(queue_index)
            self.chat_cache[chat_id]["queue"] = deque(queue_list)
            return True
        return False

    def get_queue(self, chat_id: int) -> list[CachedTrack]:
        return list(self.chat_cache.get(chat_id, {}).get("queue", deque()))

    def get_active_chats(self) -> list[int]:
        return [
            chat_id for chat_id, data in self.chat_cache.items() if data["is_active"]
        ]


chat_cache = ChatCacher()

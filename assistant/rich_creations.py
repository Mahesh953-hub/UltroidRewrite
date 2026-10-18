"""Board-only assistant command and reusable rich creation exports."""

from pyUltroid._core.assistant_creations import (
    CREATIONS,
    chessboard_html,
    creation_payload,
)
from pyUltroid._core.richmsg import rich_message, send_rich
from pyUltroid._misc._assistant import asst_cmd

__all__ = ("CREATIONS", "chess", "chessboard_html", "creation_payload")


@asst_cmd(pattern="chess", owner=True)
async def chess(event):
    """Render only the complete starting chess position."""
    return await send_rich(
        event.client, event.chat_id, rich_message(chessboard_html(), format="html")
    )

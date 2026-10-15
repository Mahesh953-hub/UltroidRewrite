# Pilot command demonstrating native colorized button markups
import re
from . import ultroid_cmd, asst, callback, LOGS
from custom.tg import ikm


@ultroid_cmd(pattern="buttonpilot$", allow_guest=True)
async def button_pilot_cmd(event):
    """
    Command: .buttonpilot
    Sends an inline keyboard utilizing custom backgrounds (danger, success, primary) and icons.
    """
    LOGS.info("buttonpilot command triggered.")
    markup = ikm(
        [
            [("Blue Button", "pilot_blue", "primary", 1)],
            [("Red Button", "pilot_red", "danger", 2)],
            [("Green Button", "pilot_green", "success", 3)],
        ]
    )

    try:
        # Use assistant (bot client) to send the inline buttons to the chat
        await asst.send_message(
            event.chat_id,
            "🎨 **Select a custom color button:**",
            buttons=markup,
        )
        await event.delete()
    except Exception as e:
        LOGS.error(f"Failed to send button pilot message: {e}")
        await event.edit(f"❌ Failed to send pilot buttons: `{e}`")


@callback(data=re.compile(b"pilot_(.*)"))
async def pilot_button_callback(event):
    """
    Callback handler for pilot button queries.
    Updates the message to reflect which color button was clicked.
    """
    color_clicked = event.data_match.group(1).decode("utf-8")
    LOGS.info(f"Pilot callback triggered: color={color_clicked}")

    color_names = {
        "blue": "BLUE (style: primary, icon: 1) 🔵",
        "red": "RED (style: danger, icon: 2) 🔴",
        "green": "GREEN (style: success, icon: 3) 🟢",
    }

    selected = color_names.get(color_clicked, color_clicked.upper())
    await event.edit(f"🎉 You successfully clicked the **{selected}** button!")

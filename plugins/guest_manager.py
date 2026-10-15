# Guest session management plugin for SarahUB
import time
from . import ultroid_cmd
from pyUltroid.startup.guestmode import add_guest, remove_guest, get_guests


@ultroid_cmd(pattern=r"guest (add|remove|list)(?:\s+(.*))?$")
async def guest_manager_cmd(event):
    """
    Command: .guest add <user_id> [expiry_seconds]
             .guest remove <user_id>
             .guest list
    """
    action = event.pattern_match.group(1).lower()
    args_str = event.pattern_match.group(2) or ""
    args = args_str.strip().split()

    if action == "list":
        from custom.fmt import fmtmsg, fmtsec

        guests = get_guests()
        if not guests:
            return await event.eor("📝 **Active Guest Sessions**: None")

        guest_data = {}
        for uid, details in guests.items():
            expiry = details.get("expiry")
            if expiry:
                rem = expiry - time.time()
                expiry_str = (
                    f"expires in {fmtsec(rem, part=1, human=True)}"
                    if rem > 0
                    else "expired"
                )
            else:
                expiry_str = "never expires"

            elapsed = time.time() - details["start"]
            guest_data[str(uid)] = (
                f"scope: {details.get('scope', 'all')} | added: {fmtsec(elapsed, part=1, human=True)} ago | status: {expiry_str}"
            )

        msg = fmtmsg(head="Active Guest Sessions", data=guest_data)
        return await event.eor(msg, parse_mode="html")

    # For add or remove, we need a user_id
    user_id = None
    expiry_seconds = None

    # Check if there is a reply
    reply = await event.get_reply_message()
    if reply:
        user_id = reply.sender_id
        # In case of reply, first arg if present is expiry_seconds (for add)
        if args:
            try:
                expiry_seconds = int(args[0])
            except ValueError:
                pass
    else:
        # No reply, resolve from args
        if not args:
            return await event.eor(
                "❌ **Error**: Please specify a User ID or reply to a user's message."
            )
        try:
            user_id = int(args[0])
        except ValueError:
            return await event.eor(
                f"❌ **Error**: Invalid User ID `{args[0]}`. Must be an integer."
            )
        if len(args) > 1:
            try:
                expiry_seconds = int(args[1])
            except ValueError:
                pass

    if action == "add":
        add_guest(user_id, expiry_seconds=expiry_seconds)
        expiry_text = f"{expiry_seconds} seconds" if expiry_seconds else "indefinite"
        await event.eor(
            f"✅ **Guest Added**: User `{user_id}` is now registered as guest (duration: {expiry_text})."
        )

    elif action == "remove":
        removed = remove_guest(user_id)
        if removed:
            await event.eor(
                f"✅ **Guest Removed**: Revoked access for user `{user_id}`."
            )
        else:
            await event.eor(
                f"❌ **Guest Not Found**: User `{user_id}` has no active guest session."
            )

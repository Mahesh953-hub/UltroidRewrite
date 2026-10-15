"""standalone logging wrapper"""

import logging

logging.basicConfig(
    format="%(asctime)s,%(msecs)03d [ %(levelname).1s ] %(name)s: %(message)s",
    datefmt="%b %-d | %-I:%M %p | %-S",
    level=logging.INFO,
)
for lib in ("telethon", "httpx"):
    logging.getLogger(lib).setLevel(logging.ERROR)

LOGS = logging.getLogger("pyUltLogs")

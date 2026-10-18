# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""Group-manager plugin namespace.

The manager modules intentionally import this namespace with ``from . import
*``. Re-export the assistant runtime names exactly as before, and make that
existing public surface explicit for loader and static-audit tooling.
"""

from pyUltroid._misc._decorators import ultroid_cmd

from .. import *

__all__ = tuple(name for name in globals() if not name.startswith("_"))

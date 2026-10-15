# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.


# -- The fns class layer: one class per module, imported eagerly so an
# import of ``pyUltroid.fns`` never needs a ``__getattr__`` hook. Optional
# third-party dependencies (googleapiclient, numpy) are capability-gated:
# the class is ``None`` when the dep is absent and the methods that need it
# raise a clear error at call time.
from __future__ import annotations

# https://github.com/bisoncorps/search-engine-parser/blob/ede1355a1f63398d9217b8e502fbd6c52b53bf09/search_engine_parser/core/utils.py#L11

some_random_headers = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.7; rv:11.0) Gecko/20100101 Firefox/11.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/72.0.3626.121 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:22.0) Gecko/2010 101 Firefox/22.0",
    "Mozilla/5.0 (Windows NT 6.1; rv:11.0) Gecko/20100101 Firefox/11.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_7_4) AppleWebKit/536.5 (KHTML, like Gecko) "
    "Chrome/19.0.1084.46 Safari/536.5",
    "Mozilla/5.0 (Windows; Windows NT 6.1) AppleWebKit/536.5 (KHTML, like Gecko) "
    "Chrome/19.0.1084.46 Safari/536.5",
    "Mozilla/5.0 (X11; Linux x86_64; rv:58.0) Gecko/20100101 Firefox/58.0",
]

try:
    from ._fns import Fns, fns
except ImportError:
    Fns = fns = None
from .admins import AdminKit
from .helper import Helper
from .info import InfoKit
try:
    from .misc import MiscKit
except ImportError:
    MiscKit = None

try:
    from .executor import Terminal
except ImportError:
    Terminal = None

try:
    from .gDrive import GDriveManager
except ImportError:
    GDriveManager = None

try:
    from .tools import ToolKit
except ImportError:
    ToolKit = None

__all__ = [
    "AdminKit",
    "Terminal",
    "GDriveManager",
    "Helper",
    "InfoKit",
    "MiscKit",
    "ToolKit",
    "Fns",
    "fns",
]

"""The fns union. Split out so the lazy-export path stays lazy."""

from __future__ import annotations

from .admins import AdminKit
from .helper import Helper
from .info import InfoKit
from .misc import MiscKit
from .tools import ToolKit

__all__ = ["Fns", "fns"]


class Fns(Helper, ToolKit, MiscKit, AdminKit, InfoKit):
    """Every helper in this folder, behind one object.

    A flat union, unlike agent/, tracer/, extender/ and updater/. Those four
    each had colliding method names across their subsystems, so attributes were
    the only way to keep every verb reachable. The five kits here were checked
    for collisions and have none: Helper, ToolKit, MiscKit, AdminKit and
    InfoKit between them define 80 distinct methods and no name twice. So one
    flat class inherits all five and every method is reachable with no
    shadowing at all.

    No __init__ here on purpose: Helper is first in the MRO, so its
    constructor - which owns the shared aiohttp session - is the one that runs.
    """

    @property
    def kits(self) -> tuple:
        """The kits this union inherits, in MRO order."""
        return (Helper, ToolKit, MiscKit, AdminKit, InfoKit)


#: The process-wide helper surface.
fns = Fns()

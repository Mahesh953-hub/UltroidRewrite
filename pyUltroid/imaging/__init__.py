# Ultroid - UserBot Rewrite
# pyUltroid/imaging/__init__.py
#
# Public API for the Hybrid Image Processing system.
#
#   from pyUltroid.imaging import Imaging
#   imaging = Imaging()
#   imaging.load(src).grayscale()
#
# There are no module singletons. Build an :class:`Imaging` instance and keep
# it: it inherits load/convert/resize from :class:`ImagePipeline`, the effects
# from :class:`ImageProcessor`, the generators from :class:`ImageRenderer` and
# the traceback card from :class:`ErrorCardRenderer`.

from ._errorcard import CARD_WIDTH, ErrorCardRenderer, palette
from ._pipeline import ImageInput, ImagePipeline, ImageResult
from ._processor import ImageProcessor
from ._renderer import PALETTE, ImageRenderer

__all__ = [
    # the union class - build one and use it everywhere
    "Imaging",
    # core type
    "ImageResult",
    "ImageInput",
    # classes (for typing)
    "ImagePipeline",
    "ImageProcessor",
    "ImageRenderer",
    "ErrorCardRenderer",
    # palettes
    "PALETTE",
    "palette",
    "CARD_WIDTH",
]


class Imaging(ImagePipeline, ImageProcessor, ImageRenderer, ErrorCardRenderer):
    """Every imaging capability in the package, as one object.

    Inherits load/convert/resize from :class:`ImagePipeline`, the effects from
    :class:`ImageProcessor`, the image generators from :class:`ImageRenderer`
    and the traceback card from :class:`ErrorCardRenderer`. Each parent keeps
    its own state; the cooperative ``__init__`` chain wires them in order.
    """

    def __init__(self) -> None:
        super().__init__()
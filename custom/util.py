import collections

from .debug import aexec, shell  # noqa: F401


class UtilKit:
    """Small patching and time-formatting utilities.

    One instance (``util_kit``); the module-level functions below are
    backward-compatible aliases.
    """

    def patch(self, cls: type) -> collections.abc.Callable:
        def wrap(func: collections.abc.Callable) -> collections.abc.Callable:
            if hasattr(func, "_patch"):
                func._patch.append(cls)
            else:
                func._patch = [cls]

            return func

        return wrap

    def usecs(self, s: float) -> str:
        if not s:
            return "0 s"

        if s >= 1:
            return f"{s:.2f} s"

        if s >= 0.001:
            return f"{s * 1000:.2f} ms"

        return f"{s * 1000000:.2f} µs"


util_kit = UtilKit()


def patch(cls: type) -> collections.abc.Callable:
    return util_kit.patch(cls)


def usecs(s: float) -> str:
    return util_kit.usecs(s)

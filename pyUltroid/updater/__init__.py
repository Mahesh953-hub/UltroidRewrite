# Public API for the Hybrid Update System.
#
#   from pyUltroid.updater import updater
#   from pyUltroid.updater import git, DiffResult, UpdateState

from ._git import (
    CommitRecord,
    DiffResult,
    FileKind,
    GitManager,
    classify_file,
    is_valid_sha
)
from ._engine import (
    UpdateEngine,
    UpdateState
)


class Updates:
    """Update orchestration and git plumbing behind one object.

    Attributes because ``rollback`` means two different things: GitManager
    moves HEAD, UpdateEngine restores a snapshot it had taken. Flattened, the
    engine would silently call git's version and believe it had undone its own
    work.

    up.engine   check / apply / status / rollback
    up.git      branches, diffs, fetch, pull
    """

    __slots__ = ("engine", "git")

    def __init__(self, engine: UpdateEngine = None, git: GitManager = None) -> None:
        # The engine is handed this same GitManager, so the two never hold
        # separate views of the working tree.
        self.git = git if git is not None else GitManager()
        self.engine = engine if engine is not None else UpdateEngine(git=self.git)

    async def check(self, *a, **kw):
        return await self.engine.check(*a, **kw)

    async def apply(self, *a, **kw):
        return await self.engine.apply(*a, **kw)

    def components(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__}


#: The process-wide updater.
up = Updates()

__all__ = [

    "Updates",
    "up",
        # Data types
    "DiffResult",
    "UpdateState",
    "CommitRecord",
    "FileKind",
    # Utilities
    "classify_file",
    "is_valid_sha",
]

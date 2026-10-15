# Ultroid - UserBot Rewrite
# pyUltroid/tracer/__init__.py
#
# Public API for the Hybrid Code Searcher + Trace Tracker.
#
# Import surface:
#
#   from pyUltroid.tracer import tracer, searcher, plugin_index
#   from pyUltroid.tracer import trace_handler, get_current_trace, TraceRecord

from ._indexer import (
    FunctionRecord,
    HandlerRecord,
    ImportRecord,
    PluginRecord,
    PluginIndex,
    plugin_index,
)
from ._tracker import (
    TraceRecord,
    TraceTracker,
    trace_handler,
    get_current_trace
)
from ._searcher import (
    CodeSearcher,
    SearchResult
)


class Tracer:
    """Index, search and runtime traces behind one object.

    Attributes, not inheritance: PluginIndex and CodeSearcher both define
    find_handler, find_function, find_import, find_db_key, all_plugins,
    handler_map and conflicts, and they answer different questions - the index
    knows every registered handler, the searcher only what a query matched.
    TraceTracker.stats is the runtime's counters, PluginIndex.stats the
    index's. Nine names would be shadowed by MRO otherwise.

    tr.index.<verb>   what the codebase contains
    tr.search.<verb>  what matches a query
    tr.live.<verb>    what just ran
    """

    __slots__ = ("index", "search", "live")

    def __init__(
        self,
        index: PluginIndex = None,
        search: CodeSearcher = None,
        live: TraceTracker = None,
    ) -> None:
        # One index, shared: a search that does not see what was just indexed
        # would report a plugin as missing. Defaults to the process-wide
        # ``plugin_index`` singleton that ``startup/loader.py`` warms.
        self.index = index if index is not None else plugin_index
        self.search = (
            search if search is not None else CodeSearcher(index=self.index)
        )
        self.live = live if live is not None else TraceTracker()

    def stats(self) -> dict:
        """Index size and live counters in one snapshot."""
        return {"index": self.index.stats(), "live": self.live.stats()}

    def components(self) -> dict:
        return {name: getattr(self, name) for name in self.__slots__}


#: The process-wide tracer.
tr = Tracer()

__all__ = [

    "Tracer",
    "tr",
    "plugin_index",
        # Decorator
    "trace_handler",  # wrap a handler for automatic tracing
    "get_current_trace",  # get active TraceRecord in current async task
    # Data types
    "TraceRecord",
    "SearchResult",
    "HandlerRecord",
    "FunctionRecord",
    "ImportRecord",
    "PluginRecord",
]

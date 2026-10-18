"""agent engine lifecycle, started/stopped WITH the bot."""

from __future__ import annotations

from typing import Optional

from pyUltroid.startup.logger import LOGS


class AgentLifecycle:
    id = "ultroid.agent"

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.started = False
        self._context = None

    # ── universal plugin contract
    def lifecycle_register(self, context) -> None:
        self._context = context

    async def lifecycle_start(self, context=None) -> None:
        if self.started:
            return
        self.started = True
        try:
            from ._providers import ProviderRegistry

            configured = [k for k, v in ProviderRegistry().provider_health().items() if v]
            LOGS.info(
                f"[AGENT:LIFECYCLE] engine started  -  providers: "
                f"{', '.join(configured) if configured else 'none'}"
            )
        except Exception:
            pass

        try:
            from ._providers import ProviderRegistry

            ollama = ProviderRegistry().provider_by_name("ollama")
            if ollama is None:
                return
            running = await ollama._is_running()
            LOGS.info(
                f"[AGENT:LIFECYCLE] local Ollama provider: "
                f"{'reachable' if running else 'not running (cloud escalation only)'}"
            )
        except Exception:
            pass

    async def lifecycle_stop(self, context=None) -> None:
        if not self.started:
            return
        self.started = False

        try:
            from ._providers import AIResponse

            shutdown = AIResponse(
                content="",
                provider="lifecycle",
                model="shutdown",
                error="Agent engine stopped before this request completed.",
            )
            # In the union this object *is* the MiniAI, so its in-flight map
            # is right here. Constructing a fresh MiniAI would hand back an
            # empty one and wake nobody, which is the bug that a module
            # singleton was silently papering over.
            mini = getattr(self, "_in_flight", None)
            in_flight = mini if mini is not None else {}
            results = getattr(self, "_in_flight_results", None)
            if results is None:
                results = {}
            stamped = set()
            for key, evt in list(in_flight.items()):
                results[key] = shutdown
                stamped.add(key)
                evt.set()
            in_flight.clear()
            if stamped:
                _schedule_result_sweep(self, stamped, shutdown)
        except Exception as er:
            LOGS.warning(f"[AGENT:LIFECYCLE]: Exception: {er}")
        LOGS.info("[AGENT:LIFECYCLE] engine stopped")


# ── registration
_SHUTDOWN_RESULT_SWEEP_DELAY = 5.0


async def _sweep_stamped_results(agent, stamped, sentinel, delay: float) -> None:
    """Drop shutdown-stamped cache-key results after *delay*  -  but only the
    entries that still hold OUR sentinel (a real response that raced in for
    the same key is never deleted)."""
    import asyncio

    await asyncio.sleep(delay)
    results = getattr(agent, "_in_flight_results", None) or {}
    for key in stamped:
        try:
            if results.get(key) is sentinel:
                results.pop(key, None)
        except Exception as exc:
            LOGS.warning(f"[AGENT:LIFECYCLE]: Exception: {exc}")


def _schedule_result_sweep(agent, stamped, sentinel) -> None:
    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    try:
        asyncio.ensure_future(
            _sweep_stamped_results(
                agent,
                stamped,
                sentinel,
                _SHUTDOWN_RESULT_SWEEP_DELAY,
            )
        )
    except Exception:
        pass


_registered = False


def register_lifecycle() -> bool:
    """
    Attach the engine lifecycle to the installed universal plugin lifecycle.
    Idempotent  -  a plugins/brain.py hot-reload never double-registers (the
    registry flag lives in this module, which plugin unload does not purge).
    Returns True when attached (or already attached).
    """
    global _registered
    if _registered:
        return True
    try:
        from pyUltroid import asst, ultroid_bot

        lifecycle = getattr(asst or ultroid_bot, "universal_plugins", None)
        if lifecycle is None:
            return False
        # The process agent, not a fresh AgentLifecycle: in the union that
        # object also carries the in-flight requests stop() must wake, and a
        # separate instance would hold none of them.
        from pyUltroid.agent import agent as _agent

        lifecycle.register(_agent())
        _registered = True
        LOGS.info("[AGENT:LIFECYCLE] registered with the universal plugin lifecycle")
        return True
    except Exception as exc:
        LOGS.debug(f"[AGENT:LIFECYCLE] registration skipped: {exc}")
        return False


def lifecycle_registered() -> bool:
    return _registered

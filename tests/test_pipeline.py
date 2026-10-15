"""Tests for the update orchestrator and the Telethon internals facade.

These drive a real, unconnected ``TelegramClient`` through its real
``_dispatch_update`` path with synthetic layer-229 updates, so routing,
claiming, pass-through and one-shot retirement are all verified against
Telethon itself rather than a mock.
"""

from __future__ import annotations

import asyncio

import pytest
from telethon import TelegramClient, events
from telethon.tl import types

from pyUltroid._core import internals
from pyUltroid._core.pipeline import (
    Route,
    UpdatePipeline,
    _update_types_of,
    pipeline_for,
    uninstall_pipeline,
)

API_ID = 12345
API_HASH = "0123456789abcdef0123456789abcdef"


def make_client() -> TelegramClient:
    client = TelegramClient(None, API_ID, API_HASH)
    # Avoid a network get_me() during dispatch.
    client._mb_entity_cache.self_id = 4242
    return client


def inline_query(query: str, query_id: int = 1) -> types.UpdateBotInlineQuery:
    update = types.UpdateBotInlineQuery(
        query_id=query_id, user_id=7, query=query, offset=""
    )
    update._entities = {}
    return update


def callback_query(
    data: bytes | None = None, query_id: int = 1
) -> types.UpdateBotCallbackQuery:
    update = types.UpdateBotCallbackQuery(
        query_id=query_id,
        user_id=7,
        peer=types.PeerUser(7),
        msg_id=11,
        chat_instance=1,
        data=data,
    )
    update._entities = {}
    return update


# ── builder → update-type index ───────────────────────────────────────────
def test_update_types_are_read_from_builder_bytecode():
    assert _update_types_of(events.InlineQuery()) == (types.UpdateBotInlineQuery,)
    assert set(_update_types_of(events.CallbackQuery())) == {
        types.UpdateBotCallbackQuery,
        types.UpdateInlineBotCallbackQuery,
    }


def test_raw_builder_types_are_honoured():
    builder = events.Raw(types=types.UpdateBotCallbackQuery)
    assert _update_types_of(builder) == (types.UpdateBotCallbackQuery,)


# ── route indexing ────────────────────────────────────────────────────────
def test_candidates_are_priority_ordered_and_subclass_aware():
    client = make_client()
    pipe = UpdatePipeline(client)

    low = pipe.route(events.InlineQuery, lambda e: None, priority=1, route_id="low")
    high = pipe.route(events.InlineQuery, lambda e: None, priority=9, route_id="high")

    order = [r.id for r in pipe.candidates(inline_query("x"))]
    assert order == ["high", "low"]

    # A wildcard route is always a candidate, after the typed ones.
    wild = pipe.route(events.Raw(), lambda e: None, priority=0, route_id="wild")
    assert [r.id for r in pipe.candidates(inline_query("x"))] == ["high", "low", "wild"]
    assert wild in pipe.routes and low in pipe.routes


def test_duplicate_route_id_replaces_instead_of_stacking():
    client = make_client()
    pipe = UpdatePipeline(client)
    pipe.route(events.InlineQuery, lambda e: None, route_id="dup")
    pipe.route(events.InlineQuery, lambda e: None, route_id="dup")
    assert len(pipe) == 1


def test_remove_by_id_handler_and_owner():
    client = make_client()
    pipe = UpdatePipeline(client)

    async def handler(event):
        return None

    pipe.route(events.InlineQuery, handler, owner="plug", route_id="r")
    assert pipe.remove("r") == 1
    assert len(pipe) == 0

    pipe.route(events.InlineQuery, handler, owner="plug", route_id="r2")
    assert pipe.remove(handler) == 1

    kept = pipe.route(events.InlineQuery, handler, owner="others", route_id="r3")
    pipe.route(events.InlineQuery, handler, owner="plug", route_id="r4")
    assert pipe.remove_owner("plug") == 1
    assert [r.id for r in pipe.routes] == ["r3"]
    assert kept.id == "r3"


# ── dispatch: claim, pass-through, one-shot ───────────────────────────────
@pytest.mark.asyncio
async def test_claim_short_circuits_telethon_handlers():
    client = make_client()
    pipe = UpdatePipeline(client).install()
    seen: list[str] = []

    @client.on(events.InlineQuery)
    async def telethon_handler(event):  # pragma: no cover - must not run
        seen.append("telethon")

    async def claimer(event):
        seen.append("pipeline")
        raise events.StopPropagation

    pipe.route(events.InlineQuery, claimer, priority=10, exclusive=True)
    await client._dispatch_update(inline_query("ping"))

    assert seen == ["pipeline"]
    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_unclaimed_update_falls_through_to_telethon():
    client = make_client()
    pipe = UpdatePipeline(client).install()
    seen: list[str] = []

    @client.on(events.InlineQuery)
    async def telethon_handler(event):
        seen.append(f"telethon:{event.text}")

    async def observer(event):
        seen.append("pipeline")

    pipe.route(events.InlineQuery, observer, priority=10)  # non-exclusive
    await client._dispatch_update(inline_query("hello"))

    assert seen == ["pipeline", "telethon:hello"]
    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_once_route_retires_itself():
    client = make_client()
    pipe = UpdatePipeline(client).install()
    hits = []

    pipe.route(events.InlineQuery, lambda e: hits.append(e.text), once=True)
    await client._dispatch_update(inline_query("a"))
    await client._dispatch_update(inline_query("b"))

    assert hits == ["a"]
    assert len(pipe) == 0
    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_route_match_rejection_leaves_route_alive():
    client = make_client()
    pipe = UpdatePipeline(client).install()
    hits = []

    pipe.route(
        events.InlineQuery,
        lambda e: hits.append(e.text),
        match=lambda e: e.text == "wanted",
    )
    await client._dispatch_update(inline_query("nope"))
    await client._dispatch_update(inline_query("wanted"))

    assert hits == ["wanted"]
    pipe.uninstall()
    client.disconnect()


# ── register-and-forget ───────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_wait_for_captures_without_client_on():
    client = make_client()
    pipe = UpdatePipeline(client).install()

    async def feed():
        await asyncio.sleep(0)
        await client._dispatch_update(inline_query("pong", query_id=9))

    feeder = asyncio.ensure_future(feed())
    event = await pipe.wait_for(
        events.InlineQuery, match=lambda e: e.text == "pong", timeout=2
    )
    await feeder

    assert isinstance(event, events.InlineQuery.Event)
    assert event.text == "pong"
    assert len(pipe) == 0  # the waiter retired itself
    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_wait_for_is_not_consumed_by_a_rejection():
    client = make_client()
    pipe = UpdatePipeline(client).install()

    async def feed():
        await asyncio.sleep(0)
        await client._dispatch_update(inline_query("first", query_id=1))
        await client._dispatch_update(inline_query("second", query_id=2))

    feeder = asyncio.ensure_future(feed())
    event = await pipe.wait_for(
        events.InlineQuery, match=lambda e: e.text == "second", timeout=2
    )
    await feeder

    assert event.text == "second"
    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_wait_for_timeout_and_cleanup():
    client = make_client()
    pipe = UpdatePipeline(client).install()

    with pytest.raises(asyncio.TimeoutError):
        await pipe.wait_for(events.InlineQuery, timeout=0.05)

    assert len(pipe) == 0
    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_instant_runs_handler_inside_dispatch_and_returns_result():
    client = make_client()
    pipe = UpdatePipeline(client).install()
    order: list[str] = []

    async def handler(event):
        order.append(f"answer:{event.data.decode()}")
        return event.data.decode()

    waiter = pipe.instant(events.CallbackQuery, handler, timeout=2)

    async def feed():
        await asyncio.sleep(0)
        order.append("dispatch")
        await client._dispatch_update(callback_query(b"click"))

    feeder = asyncio.ensure_future(feed())
    result = await waiter
    await feeder

    # The handler ran between "feed started" and "feed returned".
    assert result == "click"
    assert order == ["dispatch", "answer:click"]
    assert len(pipe) == 0
    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_instant_propagates_handler_errors_to_the_awaiter():
    client = make_client()

    async def handler(event):
        raise RuntimeError("nope")

    pipe = UpdatePipeline(client).install()
    waiter = pipe.instant(events.InlineQuery, handler, timeout=2)

    async def feed():
        await asyncio.sleep(0)
        await client._dispatch_update(inline_query("boom"))

    feeder = asyncio.ensure_future(feed())
    with pytest.raises(RuntimeError, match="nope"):
        await waiter
    await feeder
    assert len(pipe) == 0
    pipe.uninstall()
    client.disconnect()


# ── telemetry / introspection ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_stats_track_hits_and_matches():
    client = make_client()
    pipe = UpdatePipeline(client).install()
    route = pipe.route(events.InlineQuery, lambda e: None, exclusive=True)

    await client._dispatch_update(inline_query("a"))
    await client._dispatch_update(inline_query("b"))

    recorded = route.describe()
    assert recorded["hits"] == 2
    assert recorded["matches"] == 2
    assert recorded["errors"] == 0
    assert recorded["updates"] == ["UpdateBotInlineQuery"]
    pipe.uninstall()
    client.disconnect()


def test_pipeline_registry_is_per_client():
    first, second = make_client(), make_client()
    assert pipeline_for(first) is pipeline_for(first)
    assert pipeline_for(first) is not pipeline_for(second)
    first.disconnect()
    second.disconnect()


def test_install_uninstall_restores_original_dispatch():
    client = make_client()
    original = client._dispatch_update
    pipe = UpdatePipeline(client).install()
    assert client._dispatch_update is not original

    pipe.uninstall()
    assert client._dispatch_update == original
    client.disconnect()


# ── internals facade ──────────────────────────────────────────────────────
def test_internals_insert_and_find_handlers():
    client = make_client()

    async def first(event):
        return None

    async def second(event):
        return None

    client.add_event_handler(second)
    internals.insert_handler(client, events.Raw(), first)

    slots = internals.handler_slots(client)
    assert slots[0].callback is first
    assert internals.find_handlers(client, callback=second)[0].callback is second
    assert internals.count_handlers(client, events.Raw) == 2
    client.disconnect()


@pytest.mark.asyncio
async def test_internals_defers_removal_during_dispatch():
    """A handler that removes itself must not corrupt the in-flight loop."""
    client = make_client()
    order: list[str] = []

    async def second(event):
        order.append("second")

    async def self_removing(event):
        order.append("self")
        internals.remove_handler(client, self_removing)

    client.add_event_handler(self_removing, events.Raw())
    client.add_event_handler(second, events.Raw())

    pipeline = UpdatePipeline(client).install()
    await client._dispatch_update(inline_query("x"))

    # Both ran: the removal was deferred to the end of the pass.
    assert order == ["self", "second"]
    assert internals.find_handlers(client, callback=self_removing) == []
    assert internals.dispatch_depth(client) == 0
    pipeline.uninstall()
    client.disconnect()


def test_internals_owner_tagging_is_total():
    client = make_client()

    async def owned(event):
        return None

    async def orphan(event):
        return None

    internals.tag_owner(owned, "plugin.alpha")
    client.add_event_handler(owned)
    client.add_event_handler(orphan)

    assert internals.owner_of(owned) == "plugin.alpha"
    assert internals.owner_of(orphan) is None

    grouped = internals.handlers_by_owner(client)
    assert len(grouped["plugin.alpha"]) == 1
    assert len(grouped[""]) == 1
    client.disconnect()


def test_internals_describe_reports_wiring():
    client = make_client()
    client.add_event_handler(lambda e: None, events.Raw())
    report = internals.describe(client)

    assert report["handlers"] == 1
    assert report["by_builder"] == {"Raw": 1}
    assert report["dispatch_depth"] == 0
    client.disconnect()


def test_internals_raises_on_non_client():
    with pytest.raises(internals.InternalsError):
        internals.event_builders(object())


@pytest.mark.asyncio
async def test_feed_update_sets_entities_for_synthetic_updates():
    client = make_client()
    seen = []

    @client.on(events.InlineQuery)
    async def handler(event):
        seen.append(event.text)

    update = types.UpdateBotInlineQuery(query_id=3, user_id=7, query="fed", offset="")
    await internals.feed_update(client, update)

    assert update._entities == {}
    assert seen == ["fed"]
    client.disconnect()


# ── dynamic patch registry integration ────────────────────────────────────
def test_patch_registry_installs_and_retires_routes():
    from pyUltroid._core.dynamic import PatchRegistry, patch_owner

    client = make_client()
    pipe = pipeline_for(client).install()
    registry = PatchRegistry(client=client)

    class Target:
        pass

    def original(self, event):  # pragma: no cover - replaced by the patch
        return "original"

    async def patched(self, event):
        return "patched"

    async def route_handler(event):
        return None

    registry.register(
        "demo",
        Target,
        patched,
        attr_name="handler",
        routes=[(events.InlineQuery, route_handler, {"priority": 5})],
    )
    assert registry.routes_of("demo") == 0

    assert registry.apply("demo") is True
    assert registry.routes_of("demo") == 1
    installed = [r for r in pipe.routes if r.owner == patch_owner("demo")]
    assert installed[0].priority == 5

    assert registry.unapply("demo") is True
    assert registry.routes_of("demo") == 0
    assert len(pipe) == 0

    pipe.uninstall()
    client.disconnect()


def test_patch_route_install_failure_does_not_break_apply():
    from pyUltroid._core.dynamic import PatchRegistry

    client = make_client()
    pipe = pipeline_for(client).install()
    registry = PatchRegistry(client=client)

    class Target:
        pass

    def patched(self, event):  # pragma: no cover - applied, never called
        return "patched"

    registry.register(
        "broken",
        Target,
        patched,
        attr_name="handler",
        routes=[("not-a-builder", object())],
    )
    assert registry.apply("broken") is True
    assert Target.handler is patched
    assert registry.routes_of("broken") == 0
    assert any(not r.success for r in registry.audit_records())
    pipe.uninstall()
    uninstall_pipeline(client)
    client.disconnect()


# ── review regressions ────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_builder_filter_sees_a_client_bound_event():
    """Telethon binds the event before filtering; so must the pipeline."""
    client = make_client()
    pipe = UpdatePipeline(client).install()

    seen = []

    def func(event):
        # A predicate that reads the event's client, like any real `func=`.
        seen.append(event.client is client)
        return True

    pipe.route(events.InlineQuery(func=func), lambda e: None, route_id="client-func")
    await client._dispatch_update(inline_query("bound"))

    assert seen == [True]
    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_returning_stop_claims_the_update():
    client = make_client()
    pipe = UpdatePipeline(client).install()
    ran = []

    @client.on(events.InlineQuery)
    async def telethon_handler(event):  # pragma: no cover - must not run
        ran.append("telethon")

    async def handler(event):
        return events.StopPropagation

    pipe.route(events.InlineQuery, handler, route_id="stopper")
    await client._dispatch_update(inline_query("stop"))

    assert ran == []
    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_decorator_routes_with_the_same_handler_name_coexist():
    client = make_client()
    pipe = UpdatePipeline(client).install()

    first_calls, second_calls = [], []

    pipe.on(events.InlineQuery)(lambda e: first_calls.append(e.text))

    def handler(event):  # pragma: no cover - the pipeline calls it, not us
        second_calls.append(event.text)

    # Two plugins both exporting `handler` must both stay registered.
    pipe.route(events.InlineQuery, handler)
    assert len(pipe) == 2
    assert len({route.id for route in pipe.routes}) == 2

    await client._dispatch_update(inline_query("both"))

    assert first_calls == ["both"] and second_calls == ["both"]
    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_a_once_route_runs_at_most_once():
    client = make_client()
    pipe = UpdatePipeline(client).install()
    calls = []

    async def handler(event):
        calls.append(event.text)

    pipe.route(events.InlineQuery, handler, once=True, route_id="one-shot")

    await client._dispatch_update(inline_query("first", query_id=1))
    await client._dispatch_update(inline_query("second", query_id=2))

    assert calls == ["first"]
    assert len(pipe) == 0
    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_wait_for_retires_its_route_before_claiming():
    client = make_client()
    pipe = UpdatePipeline(client).install()
    assert len(pipe) == 0

    waiter = asyncio.ensure_future(pipe.wait_for(events.InlineQuery))
    await asyncio.sleep(0)
    assert len(pipe) == 1

    await client._dispatch_update(inline_query("ping"))
    event = await waiter
    assert event.text == "ping"
    assert len(pipe) == 0

    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_bind_client_installs_routes_for_already_applied_patches():
    from pyUltroid._core.dynamic import PatchRegistry

    client = make_client()
    pipe = UpdatePipeline(client).install()
    registry = PatchRegistry()  # no client yet

    class Target:
        pass

    def patched(self, event):  # pragma: no cover - applied, never called
        return "patched"

    registry.register(
        "late",
        Target,
        patched,
        attr_name="handler",
        routes=[(events.InlineQuery, patched)],
    )
    assert registry.apply("late") is True
    assert registry.routes_of("late") == 0

    registry.bind_client(client)
    assert registry.routes_of("late") == 1

    assert registry.unapply("late") is True
    assert registry.routes_of("late") == 0

    pipe.uninstall()
    client.disconnect()


@pytest.mark.asyncio
async def test_uninstall_only_undoes_its_own_patch():
    client = make_client()
    telethon_dispatch = client._dispatch_update
    telethon_event = client._dispatch_event

    first = UpdatePipeline(client).install()
    second = UpdatePipeline(client).install()

    # The first pipeline must not tear out the second one's patch.
    first.uninstall()
    assert getattr(client._dispatch_update, "__self__", None) is second
    assert first.installed is True

    second.uninstall()
    assert getattr(client._dispatch_update, "__self__", None) is first

    # Now that it is on top again, the first can unwind cleanly.
    first.uninstall()
    assert client._dispatch_update == telethon_dispatch
    assert client._dispatch_event == telethon_event
    uninstall_pipeline(client)
    client.disconnect()


@pytest.mark.asyncio
async def test_install_wraps_the_album_dispatch_path():
    from pyUltroid._core import internals

    client = make_client()
    telethon_event = client._dispatch_event
    pipe = UpdatePipeline(client).install()

    assert client._dispatch_event != telethon_event
    # The album path shares the deferred-removal pass, so a removal made from
    # inside it is deferred rather than applied mid-iteration.
    with internals.dispatch_pass(client, None):
        internals.remove_handler(client, lambda e: None)
        assert internals.dispatch_depth(client) == 1

    await client._dispatch_event(inline_query("album"))
    assert internals.dispatch_depth(client) == 0

    pipe.uninstall()
    assert client._dispatch_event == telethon_event
    uninstall_pipeline(client)
    client.disconnect()


def test_patching_bind_client_reaches_the_registry():
    """``startup.patching.bind_client`` is the production wiring entry point."""
    from pyUltroid._core.dynamic import PatchRegistry
    from pyUltroid.startup import patching

    seen = []
    original = PatchRegistry.bind_client

    def spy(self, client):
        seen.append(client)
        return original(self, client)

    client = make_client()
    PatchRegistry.bind_client = spy
    try:
        patching.bind_client(client)
    finally:
        PatchRegistry.bind_client = original
        client.disconnect()
    assert seen == [client]
    assert patching.get_patch_registry()._client is client

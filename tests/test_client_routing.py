"""Tests for MasterClient's declarative routing rules."""

from __future__ import annotations

import pytest

from pyUltroid._core.client import (
    MasterClient,
    RoutingRule,
    UnknownRoleError,
    bind_origin,
)


class FakeClient:
    """A stand-in real client: identity is what routing cares about."""

    def __init__(self, name: str, **attrs):
        self.name = name
        for key, value in attrs.items():
            setattr(self, key, value)

    def __repr__(self) -> str:
        return f"<FakeClient {self.name}>"


def make_master(**kwargs) -> MasterClient:
    return MasterClient(**kwargs)


def test_default_rule_order():
    master = make_master()
    assert [rule.name for rule in master.routing_rules] == [
        "origin",
        "business",
        "user",
        "bot",
    ]


def test_user_wins_over_bot_without_context():
    bot, user = FakeClient("bot"), FakeClient("user")
    master = make_master(bot=bot, user=user)
    assert master.route() is user
    assert master.explain().name == "user"


def test_bot_only_mode_routes_to_bot():
    bot = FakeClient("bot")
    master = make_master(bot=bot)
    assert master.route() is bot
    assert master.explain().name == "bot"


def test_origin_overrides_business_and_user():
    bot, user = FakeClient("bot"), FakeClient("user")
    master = make_master(bot=bot, user=user)
    with bind_origin(user):
        assert master.route() is user
        assert master.explain().name == "origin"


def test_unknown_origin_is_ignored():
    bot, user = FakeClient("bot"), FakeClient("user")
    master = make_master(bot=bot, user=user)
    with bind_origin(FakeClient("stranger")):
        assert master.route() is user


def test_no_clients_raises():
    master = make_master()
    assert master.explain() is None
    with pytest.raises(RuntimeError):
        master.route()


def test_add_and_remove_rules():
    bot, user = FakeClient("bot"), FakeClient("user")
    master = make_master(bot=bot, user=user)

    master.add_rule(
        RoutingRule(
            name="always-bot",
            matches=lambda m: True,
            resolve=lambda m: m.bot,
            priority=9000,
        )
    )
    assert master.route() is bot
    assert master.remove_rule("always-bot") is True
    assert master.route() is user
    assert master.remove_rule("always-bot") is False


def test_duplicate_rule_name_rejected_unless_replacing():
    master = make_master(user=FakeClient("user"))
    rule = RoutingRule("custom", lambda m: False, lambda m: None, priority=1)
    master.add_rule(rule)
    with pytest.raises(ValueError):
        master.add_rule(RoutingRule("custom", lambda m: False, lambda m: None))
    replaced = master.add_rule(
        RoutingRule("custom", lambda m: False, lambda m: None, priority=2),
        replace=True,
    )
    assert replaced.priority == 2
    assert [r.name for r in master.routing_rules].count("custom") == 1


def test_rule_priority_ordering_is_stable():
    master = make_master(user=FakeClient("user"))
    master.add_rule(RoutingRule("low", lambda m: False, lambda m: None, priority=-5))
    master.add_rule(RoutingRule("high", lambda m: False, lambda m: None, priority=5000))
    names = [r.name for r in master.routing_rules]
    assert names[0] == "high"
    assert names.index("origin") < names.index("low")


def test_route_for_sends_bot_updates_to_the_bot():
    bot, user = FakeClient("bot"), FakeClient("user")
    master = make_master(bot=bot, user=user)

    update = type("UpdateBotInlineQuery", (), {})()
    event = type("Event", (), {"original_update": update})()
    assert master.route_for(event) is bot


def test_route_for_honours_the_event_source_client():
    bot, user, second = FakeClient("bot"), FakeClient("user"), FakeClient("second")
    master = make_master(bot=bot, user=user)
    master.add_client("user", second)

    event = type("Event", (), {"client": second})()
    assert master.route_for(event) is second


def test_route_for_falls_back_to_route():
    bot, user = FakeClient("bot"), FakeClient("user")
    master = make_master(bot=bot, user=user)
    event = type("Event", (), {})()
    assert master.route_for(event) is user


def test_unknown_role_is_reported_clearly():
    master = make_master()
    with pytest.raises(UnknownRoleError):
        master.add_client("nonsense", FakeClient("x"))


def test_clients_are_deduplicated_by_identity():
    shared = FakeClient("shared")
    master = make_master(user=shared)
    master.add_client("user", shared)
    assert len(master.clients_by_role("user")) == 1
    assert master.clients == (shared,)


def test_master_client_survives_a_pickle_round_trip():
    """The built-in rules must not hold local functions or lambdas.

    ``MasterClient`` stores its rules, so a closure or lambda in a default rule
    makes every instance unpicklable — a contract
    ``tests/test_multiclient.py`` already asserts.
    """
    import pickle

    master = make_master(user=FakeClient("user"), bot=FakeClient("bot"))
    loaded = pickle.loads(pickle.dumps(master))
    assert [rule.name for rule in loaded.routing_rules] == [
        "origin",
        "business",
        "user",
        "bot",
    ]
    assert all(
        rule.matches.__module__ == "pyUltroid._core.client"
        and rule.resolve.__module__ == "pyUltroid._core.client"
        for rule in loaded.routing_rules
    )
    assert loaded.route().name == "user"


def test_default_rules_keep_working_after_a_pickle_round_trip():
    import pickle

    bot, user = FakeClient("bot"), FakeClient("user")
    loaded = pickle.loads(pickle.dumps(make_master(bot=bot, user=user)))
    assert loaded.route() is loaded.user
    with bind_origin(loaded.user):
        assert loaded.route() is loaded.user

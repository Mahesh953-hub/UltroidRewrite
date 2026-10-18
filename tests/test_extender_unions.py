"""The tracer, extender and updater unions: no singletons, no shadowing."""

from __future__ import annotations

import pytest


def test_singletons_are_shared_with_the_unions():
    """The union convention: classes build the unions, and the singletons
    the runtime genuinely needs (the HRE gate the loader calls per plugin,
    the installer it patches in tests, the manifest registry the loader
    reads, the plugin index startup warms) are ONE object each, defaulting
    INTO the unions so every surface reads the same truth.
    """
    from pyUltroid import extender, tracer, updater

    assert extender.hre is extender.ex.resources
    assert extender.installer is extender.ex.packages
    assert extender.manifest_registry is extender.ex.manifests
    assert extender.hre._installer is extender.installer
    assert tracer.plugin_index is tracer.tr.index
    assert updater.up.engine is not None and updater.up.git is not None


def test_unions_expose_every_component():
    from pyUltroid.tracer import tr
    from pyUltroid.extender import ex
    from pyUltroid.updater import up

    assert set(tr.components()) == {"index", "search", "live"}
    assert set(ex.components()) == {"manifests", "resources", "packages", "parse"}
    assert set(up.components()) == {"engine", "git"}


def test_legacy_updater_imports_keep_the_full_shared_apis():
    from pyUltroid.updater import git, up, updater

    assert updater is up.engine
    assert git is up.git
    assert updater._git is git
    assert all(
        callable(getattr(updater, name))
        for name in ("check", "apply", "status", "rollback")
    )
    assert all(
        callable(getattr(git, name))
        for name in ("current_sha", "current_branch", "fetch_diff", "rollback")
    )


def test_collaborators_are_shared_not_duplicated():
    """Two objects over the same state would drift."""
    from pyUltroid.tracer import tr
    from pyUltroid.extender import ex
    from pyUltroid.updater import up

    assert tr.search._index is tr.index
    assert ex.resources._manifests is ex.manifests
    assert ex.resources._installer is ex.packages
    assert ex.packages._manifests is ex.manifests
    assert up.engine._git is up.git


def test_the_colliding_names_are_reachable_under_both_owners():
    """find_handler on the index and on the searcher stay distinct."""
    from pyUltroid.tracer import tr

    assert callable(tr.index.find_handler)
    assert callable(tr.search.find_handler)
    assert tr.index.find_handler is not tr.search.find_handler


def test_extender_install_dep_exists_on_the_router_not_the_performer():
    from pyUltroid.extender import ex

    assert callable(ex.resources.install_dep)
    assert callable(ex.packages.install_dep)


def test_updater_rollback_is_on_both_and_distinct():
    from pyUltroid.updater import up

    assert callable(up.engine.rollback)
    assert callable(up.git.rollback)
    assert up.engine.rollback is not up.git.rollback

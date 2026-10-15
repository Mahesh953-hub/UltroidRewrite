# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://www.github.com/TeamUltroid/Ultroid/blob/main/LICENSE/>.

"""Structured callback-data model ("cbs") for ``assistant/``.

Every inline button assistant/-side code sends carries callback data (or, for
``@in_pattern`` results, an inline-query string) that some ``@callback`` /
``@in_pattern`` handler must consume. Before this module, that data was a
grab-bag of ad-hoc prefixes (``cbs_``, ``abs_``, ``aki_``, ``hlp_``, bare
atoms like ``"setter"``...) with no single place documenting which handler
owns which prefix — a classic "button times out, nobody knows why" trap: a
button emits data with no registered handler, or a handler's pattern has no
button that ever emits it.

This module gives every assistant/ callback datum one of three shapes:

1. ``help:<plugin>:<data>``      — the group-manager help menu
   (``assistant/manager/_help.py``); paged button indexation.
2. ``assistant:<name>:<tail>``   — everything else living in ``assistant/``
   and ``assistant/manager/`` (menus, convo flows, games, ytdl helpers,
   standalone alert-and-return buttons — all of it). ``<name>`` is the
   owning module's short name (``cbstuffs``, ``games``, ``lang``, ``start``,
   ...); ``<tail>`` is that module's own sub-format (colon-joined further,
   free-form, whatever the handler needs).
3. **Standalones** — call-and-cut buttons that only ``event.answer(...)`` an
   alert or delete the message, with no further navigation/state. They are
   *not* a fourth colon-prefix: they use the same ``assistant:<name>:<tail>``
   shape as namespace 2.

Symmetry rule
--------------
Every callback data a button *emits* must have a matching ``@callback``
pattern that *consumes* it, and vice versa. Emitters use :func:`cb` /
:func:`cb_str`; handlers use :func:`cb_pattern` / :func:`cb_exact_pattern`.
``tests/test_assistant_cbs_symmetry.py`` statically scans every literal
call site of these four functions and fails on any one-sided
``namespace:name`` prefix.

Cross-boundary tokens
----------------------
A handful of callback datas cross the ``assistant/`` <-> ``plugins/`` (or
``pyUltroid`` core) boundary — e.g. the group-manager help menu's "back to
plugin help" button is emitted by ``plugins/_help.py`` but consumed by
``assistant/manager/_help.py``. These tokens are **fully migrated** to the
colon model: both sides build/handle the same literal ``cb(...)`` segments,
with the primitives imported from the dependency-free leaf
``pyUltroid._core._cbs`` (re-exported below) — so no side introduces an
import-order cycle. The symmetry audit
(``tests/test_assistant_cbs_symmetry.py``) scans the participating files
across both layers and pins the migrated prefixes so a silent
re-literalization can't slip through.

Back-navigation
----------------
Every "« Back" button targets a real, already-registered destination token
(e.g. ``assistant:cbstuffs:menu:otvars`` has its own ``@callback`` handler).
Reusing that destination's own callback data as the back button's data is
not a workaround — a button click is a *real* Telegram callback-query round
trip, dispatched by Telethon exactly as if the destination button itself had
been clicked. No in-process redispatch engine is needed, and the symmetry
rule is satisfied automatically (the "emitter" is just every
``get_back_button()`` call site, the "handler" is the destination's own
handler).
"""

from pyUltroid._core._cbs import (  # noqa: F401  (re-export)
    SEP,
    _clean,
    cb,
    cb_pattern,
    cb_exact_pattern,
    cb_str,
)

__all__ = [
    "SEP",
    "_clean",
    "cb",
    "cb_str",
    "cb_pattern",
    "cb_exact_pattern",
]

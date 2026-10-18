"""Instant-inline round trip, parse-mode normalisation and registry hygiene.

The three-request inline flow is driven end to end against recording fakes: the
user client's ``GetInlineBotResults`` is turned into a real
``UpdateBotInlineQuery`` and pushed through the assistant's pipeline, the
assistant's ``SetInlineBotResults`` is captured, and the user client's
``SendInlineBotResult`` is inspected for ``hide_via`` and ``reply_to``.
"""

import asyncio
import gc
import unittest
import weakref
from types import MethodType, SimpleNamespace

from telethon import events
from telethon.errors.rpcerrorlist import BotInlineDisabledError
from telethon.tl import types
from telethon.tl.custom.inlineresults import InlineResults as TLInlineResults

from custom.entities import normalize_parsed, plain_text
from pyUltroid._core import instant as instant_mod
from pyUltroid._core import pipeline as pipeline_mod
from pyUltroid._core.inlineresults import InlineResults
from pyUltroid._core.instant import (
    MAX_RESULTS,
    InlineUnavailable,
    InstantInline,
    InstantInlineError,
    InstantQuery,
    InstantResult,
    active_sessions,
    session_for_token,
)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeEntityCache:
    """Stands in for Telethon's ``_mb_entity_cache``; only ``self_id`` is read."""

    def __init__(self, self_id):
        self.self_id = self_id


class FakeBot:
    """The assistant: owns a pipeline and answers inline queries."""

    def __init__(self, user_id=1000, username="sarah_bot"):
        self._self_id = user_id
        self.me = types.User(id=user_id, bot=True, username=username)
        self.answers = []
        self.parse_mode = None
        self._event_builders = []
        self._conversations = {}
        self._mb_entity_cache = FakeEntityCache(user_id)
        self._event_handler_tasks = set()
        self.dispatched = []

    async def get_me(self, input_peer=False):
        return self.me

    def build_reply_markup(self, buttons, inline_only=False):
        return "MARKUP" if buttons else None

    async def _parse_message_text(self, message, parse_mode):
        return message or "", []

    async def __call__(self, request):
        self.answers.append(request)
        return True

    async def _dispatch_update(self, update):
        """Telethon's own pass — the pipeline wraps this and falls through to it."""
        self.dispatched.append(update)
        return None


class FakeUser:
    """The user client: issues the query and the send."""

    def __init__(self, bot, user_id=7, fail_with=None):
        self._bot = bot
        self._self_id = user_id
        self.me = types.User(id=user_id, bot=False, first_name="Ada")
        self.sent = []
        self.queries = []
        self.fail_with = fail_with

    async def get_me(self, input_peer=False):
        return self.me

    async def get_input_entity(self, peer):
        return peer

    async def inline_query(
        self, bot, query, *, entity=None, offset=None, geo_point=None
    ):
        """Drive the assistant's pipeline for real, then wrap its answer."""
        self.queries.append(SimpleNamespace(query=query, offset=offset, entity=entity))
        if self.fail_with is not None:
            raise self.fail_with
        update = types.UpdateBotInlineQuery(
            query_id=555,
            user_id=self._self_id,
            query=query,
            offset=offset or "",
        )
        update._entities = {}
        await self._bot._dispatch_update(update)
        if not self._bot.answers:
            raise AssertionError("the assistant never answered")
        answered = self._bot.answers[-1]
        results = types.messages.BotResults(
            query_id=555,
            results=[_to_bot_inline_result(r) for r in answered.results],
            cache_time=answered.cache_time,
            users=[],
            gallery=answered.gallery,
            next_offset=answered.next_offset,
        )
        return TLInlineResults(self, results, entity=entity)

    async def __call__(self, request):
        self.sent.append(request)
        return True

    def _get_response_message(self, request, result, entity):
        return request


def _to_bot_inline_result(built):
    """Mirror an ``InputBotInlineResult`` back as the ``BotInlineResult`` Telegram returns."""
    return types.BotInlineResult(
        id=built.id,
        type=built.type,
        send_message=types.BotInlineMessageText(message=""),
        title=getattr(built, "title", None),
        description=getattr(built, "description", None),
        url=getattr(built, "url", None),
    )


class InstantBase(unittest.TestCase):
    """Isolate the module-level session registry between tests.

    ``run()`` closes its loop, so a watchdog armed inside it never fires. The
    real runtime keeps one loop alive for the process, which is why the
    watchdog test below uses a single loop; here the registry is cleared
    explicitly so one test cannot observe another's session.
    """

    def setUp(self):
        instant_mod._SESSIONS.clear()

    def tearDown(self):
        for session in tuple(instant_mod._SESSIONS.values()):
            session.close()
        instant_mod._SESSIONS.clear()


def make_session(**kwargs):
    bot = FakeBot()
    user = FakeUser(bot)
    results = kwargs.pop("results", [InstantResult(title="One", text="first")])
    session = InstantInline(results, bot=bot, user=user, **kwargs)
    return session, bot, user


class InstantResultTests(InstantBase):
    def test_an_unknown_kind_is_rejected_at_construction(self):
        with self.assertRaises(InstantInlineError):
            InstantResult(title="x", kind="hologram")

    def test_a_media_kind_without_a_file_is_rejected(self):
        with self.assertRaises(InstantInlineError):
            InstantResult(title="x", kind="photo")

    def test_a_game_without_a_short_name_is_rejected(self):
        with self.assertRaises(InstantInlineError):
            InstantResult(title="x", kind="game")

    def test_as_text_normalises_a_parse_mode_tuple(self):
        spec = InstantResult(title="fallback", text=("hello", [1, 2]))
        self.assertEqual(spec.as_text(), ("hello", [1, 2]))

    def test_as_text_falls_back_to_the_title(self):
        self.assertEqual(
            InstantResult(title="only a title").as_text(), ("only a title", [])
        )


class InstantResultBuildTests(InstantBase):
    """Every advertised kind must build against the real ``InlineResults``.

    The geo kinds take no caption arguments, ``game`` needs ``game=True`` and
    ``photo`` has no ``mime_type``; a single shared keyword blob raised
    ``TypeError``/``ValueError`` on each of them.
    """

    def build(self, spec):
        """Build against the fork's real ``InlineBuilder``, not a stub."""
        return run(spec.build(InlineResults(FakeBot())))

    def test_a_location_builds_without_caption_arguments(self):
        spec = InstantResult(
            title="here",
            kind="location",
            extra={"latitude": 52.5, "longitude": 13.4},
        )
        result = self.build(spec)
        self.assertEqual(result.type, "geo")
        self.assertEqual(result.send_message.geo_point.lat, 52.5)

    def test_a_venue_builds_without_caption_arguments(self):
        spec = InstantResult(
            title="the pub",
            kind="venue",
            extra={
                "latitude": 52.5,
                "longitude": 13.4,
                "address": "1 Main Street",
            },
        )
        result = self.build(spec)
        self.assertEqual(result.type, "venue")
        self.assertEqual(result.send_message.address, "1 Main Street")

    def test_a_contact_builds_without_caption_arguments(self):
        spec = InstantResult(
            title="",
            kind="contact",
            extra={"phone_number": "+100", "first_name": "Ada"},
        )
        result = self.build(spec)
        self.assertEqual(result.type, "contact")
        self.assertEqual(result.send_message.phone_number, "+100")

    def test_a_game_result_actually_asks_for_a_game_message(self):
        spec = InstantResult(title="chess", kind="game", extra={"short_name": "chess"})
        result = self.build(spec)
        self.assertEqual(result.short_name, "chess")
        self.assertIsInstance(result.send_message, types.InputBotInlineMessageGame)

    def test_a_photo_honours_include_media_false(self):
        photo = types.InputPhoto(id=1, access_hash=2, file_reference=b"")
        spec = InstantResult(
            title="pic",
            text="caption",
            kind="photo",
            file=photo,
            include_media=False,
        )
        result = self.build(spec)
        self.assertIsInstance(result.send_message, types.InputBotInlineMessageText)

    def test_extra_still_overrides_the_field_defaults(self):
        """``extra`` supplied the whole argument set before the per-kind split."""
        spec = InstantResult(extra={"title": "From extra", "text": "Body"})
        result = self.build(spec)
        self.assertEqual(result.title, "From extra")
        self.assertEqual(
            result.send_message.rich_message.html,
            "Body",
        )

    def test_extra_can_override_the_parse_mode(self):
        spec = InstantResult(title="t", text="*raw*", extra={"parse_mode": None})
        result = self.build(spec)
        self.assertEqual(result.send_message.rich_message.html, "*raw*")

    def test_extra_can_override_a_media_caption(self):
        photo = types.InputPhoto(id=1, access_hash=2, file_reference=b"")
        spec = InstantResult(
            title="pic",
            text="original",
            kind="photo",
            file=photo,
            extra={"text": "overridden"},
        )
        result = self.build(spec)
        self.assertEqual(result.send_message.message, "overridden")

    def test_a_photo_includes_its_media_by_default(self):
        photo = types.InputPhoto(id=1, access_hash=2, file_reference=b"")
        spec = InstantResult(title="pic", text="caption", kind="photo", file=photo)
        result = self.build(spec)
        self.assertIsInstance(result.send_message, types.InputBotInlineMessageMediaAuto)


class InlineResultsDelegationTests(InstantBase):
    """``InlineResults`` builds its messages through the fork, not a copy.

    The local helpers duplicated ``InlineBuilder._message`` and swallowed parse
    failures, so a plugin with broken markup sent visible markup characters and
    its author never learned why.
    """

    def test_a_parse_failure_is_reported_rather_than_sent_raw(self):
        """``cached_photo`` is one of the two paths that go through the wrapper."""
        bot = FakeBot()

        async def refuse(message, parse_mode):
            raise ValueError("Failed to parse message")

        bot._parse_message_text = refuse
        photo = types.InputPhoto(id=1, access_hash=2, file_reference=b"")
        with self.assertRaises(ValueError):
            run(InlineResults(bot).cached_photo(photo, text="**broken", id="fixed"))

    def test_a_cached_document_parse_failure_is_reported_too(self):
        bot = FakeBot()

        async def refuse(message, parse_mode):
            raise ValueError("Failed to parse message")

        bot._parse_message_text = refuse
        doc = types.InputDocument(id=1, access_hash=2, file_reference=b"")
        with self.assertRaises(ValueError):
            run(InlineResults(bot).cached_document(doc, text="**broken", id="fixed"))

    def test_a_caption_less_cached_result_still_builds(self):
        """The fork rejects ``text=None``; a caption-less cached photo is valid."""
        bot = FakeBot()
        photo = types.InputPhoto(id=1, access_hash=2, file_reference=b"")
        result = run(InlineResults(bot).cached_photo(photo))
        self.assertEqual(result.send_message.message, "")

    def test_buttons_reach_the_clients_markup_builder(self):
        bot = FakeBot()
        result = run(
            InlineResults(bot).article(title="t", text="x", buttons=["b"], id="fixed")
        )
        self.assertEqual(result.send_message.reply_markup, "MARKUP")

    def test_buttons_without_a_client_are_an_error_not_a_silent_drop(self):
        """Dropping buttons the caller asked for is worse than failing loudly."""
        results = InlineResults(SimpleNamespace(builder=SimpleNamespace()))
        self.assertIsNone(results._client)
        with self.assertRaises(ValueError):
            results._markup(["b"])

    def test_buttons_on_a_cached_result_without_a_client_are_an_error(self):
        """The delegated path has to enforce the same contract as ``_markup``.

        The fork builds the markup from its own client, so a client-less builder
        raised ``AttributeError`` on ``None`` here instead of the documented
        error.
        """
        from pyUltroid._core.inlineresults import InlineResultError

        results = InlineResults(SimpleNamespace(builder=SimpleNamespace()))
        self.assertIsNone(results._client)
        photo = types.InputPhoto(id=1, access_hash=2, file_reference=b"")
        with self.assertRaises(InlineResultError):
            run(results.cached_photo(photo, text="hi", buttons=["b"], id="fixed"))

    def test_named_document_builders_are_available_without_layer_probes(self):
        for name in ("gif", "video", "audio", "voice", "sticker", "file"):
            self.assertTrue(callable(getattr(InlineResults, name, None)), name)
        self.assertFalse(hasattr(InlineResults, "has"))

    def test_a_file_result_is_built_as_a_document(self):
        recorded = {}

        class _Recorder:
            _client = None

            async def document(self, file, **kwargs):
                recorded.update(kwargs, file=file)
                return "built"

        results = InlineResults(SimpleNamespace(builder=_Recorder()))
        self.assertEqual(run(results.file("f.bin", text="caption")), "built")
        self.assertEqual(recorded["type"], "file")
        self.assertIsNone(recorded["mime_type"])
        self.assertEqual(recorded["text"], "caption")

    def test_a_geo_result_still_carries_its_markup(self):
        bot = FakeBot()
        result = run(
            InlineResults(bot).location(
                52.5, 13.4, title="here", buttons=["b"], id="fixed"
            )
        )
        self.assertEqual(result.send_message.reply_markup, "MARKUP")


class NormalizeParsedTests(InstantBase):
    def test_a_bare_string_becomes_a_pair(self):
        self.assertEqual(normalize_parsed("hi"), ("hi", []))

    def test_a_custom_markdown_pair_passes_through(self):
        self.assertEqual(normalize_parsed(("hi", [7])), ("hi", [7]))

    def test_a_none_entities_pair_becomes_an_empty_list(self):
        self.assertEqual(normalize_parsed(("hi", None)), ("hi", []))

    def test_none_uses_the_fallback(self):
        self.assertEqual(normalize_parsed(None, fallback="empty"), ("empty", []))

    def test_a_message_like_object_is_unwrapped(self):
        message = types.Message(id=1, peer_id=None, message="body", entities=[3])
        self.assertEqual(normalize_parsed(message), ("body", [3]))

    def test_plain_text_never_returns_a_tuple(self):
        self.assertIsInstance(plain_text(("hi", [1])), str)
        self.assertEqual(plain_text(("hi", [1])), "hi")


class InstantInlineRoundTripTests(InstantBase):
    def test_show_drives_all_three_requests_and_keeps_via(self):
        session, bot, user = make_session(
            results=[InstantResult(title="One", text="first", buttons=["b"])]
        )
        event = FakeEvent(user, chat_id=-100, message_id=42)
        sent = run(session.show(event))

        self.assertEqual(len(bot.answers), 1, "the assistant answered exactly once")
        self.assertEqual(len(user.sent), 1, "the user sent exactly one result")
        self.assertIs(sent, user.sent[0])
        self.assertFalse(sent.hide_via, "hide_via must stay false so 'via @bot' shows")
        self.assertEqual(sent.reply_to.reply_to_msg_id, 42)
        self.assertEqual(sent.query_id, 555)

    def test_via_false_hides_the_attribution(self):
        session, _bot, user = make_session()
        run(session.show(FakeEvent(user, chat_id=-100, message_id=9), via=False))
        self.assertTrue(user.sent[0].hide_via)

    def test_reply_false_sends_without_a_reply(self):
        session, _bot, user = make_session()
        run(session.show(FakeEvent(user, chat_id=-100, message_id=9), reply=False))
        self.assertIsNone(user.sent[0].reply_to)

    def test_the_provider_is_called_only_when_telegram_asks(self):
        calls = []

        def provider(query):
            calls.append(query)
            return [InstantResult(title=f"for {query.text}", text=query.text)]

        session, bot, user = make_session(results=provider)
        self.assertEqual(calls, [], "nothing is computed before the query arrives")
        run(session.show(FakeEvent(user, chat_id=1, message_id=2), text="funny horse"))
        self.assertEqual(len(calls), 1)
        self.assertIsInstance(calls[0], InstantQuery)
        self.assertEqual(
            calls[0].text, "funny horse", "the token is stripped from the query"
        )
        self.assertEqual(bot.answers[0].results[0].title, "for funny horse")

    def test_an_async_provider_is_awaited(self):
        async def provider(query):
            await asyncio.sleep(0)
            return [InstantResult(title="async", text="async")]

        session, bot, user = make_session(results=provider)
        run(session.show(FakeEvent(user, chat_id=1, message_id=2)))
        self.assertEqual(bot.answers[0].results[0].title, "async")

    def test_a_bare_string_result_is_promoted_to_an_article(self):
        session, bot, user = make_session(results=["just text"])
        run(session.show(FakeEvent(user, chat_id=1, message_id=2)))
        self.assertEqual(bot.answers[0].results[0].title, "just text")

    def test_pagination_sets_next_offset_and_windows_the_results(self):
        specs = [InstantResult(title=f"r{i}", text=f"r{i}") for i in range(5)]
        session, bot, user = make_session(results=specs, per_page=2)
        run(session.query())
        answer = bot.answers[0]
        self.assertEqual(len(answer.results), 2)
        self.assertEqual(answer.next_offset, "1")
        session.close()

    def test_the_last_page_has_no_next_offset(self):
        specs = [InstantResult(title=f"r{i}", text=f"r{i}") for i in range(3)]
        session, bot, user = make_session(results=specs, per_page=2)
        run(session.query(offset="1"))
        self.assertIsNone(bot.answers[0].next_offset)
        self.assertEqual(len(bot.answers[0].results), 1)
        session.close()

    def test_the_page_travels_in_telegrams_offset_field_not_the_query_text(self):
        """Telegram echoes ``next_offset`` back in ``offset``, never in the text."""
        specs = [InstantResult(title=f"r{i}", text=f"r{i}") for i in range(5)]
        session, bot, user = make_session(results=specs, per_page=2)
        run(session.query("horse", offset="1"))
        request = user.queries[0]
        self.assertEqual(request.offset, "1")
        self.assertEqual(
            request.query,
            f"{session.token} horse",
            "the page must not be encoded into the query text",
        )
        self.assertEqual([r.title for r in bot.answers[0].results], ["r2", "r3"])
        session.close()

    def test_query_text_that_looks_like_a_page_marker_is_still_user_text(self):
        seen = []

        def provider(query):
            seen.append((query.text, query.page))
            return [InstantResult(title="ok", text="ok")]

        session, _bot, _user = make_session(results=provider)
        run(session.query("p3 funny horse"))
        self.assertEqual(seen, [("p3 funny horse", 0)])
        session.close()

    def test_a_non_numeric_offset_is_read_as_the_first_page(self):
        specs = [InstantResult(title=f"r{i}", text=f"r{i}") for i in range(3)]
        session, bot, _user = make_session(results=specs, per_page=2)
        run(session.query(offset="not-a-page"))
        self.assertEqual([r.title for r in bot.answers[0].results], ["r0", "r1"])
        session.close()

    def test_an_index_beyond_the_built_results_is_rejected(self):
        session, _bot, user = make_session()
        with self.assertRaises(InstantInlineError):
            run(session.show(FakeEvent(user, chat_id=1, message_id=2), index=3))
        session.close()


class InstantInlineRoutingTests(InstantBase):
    def test_the_route_is_retired_after_a_successful_show(self):
        session, bot, user = make_session()
        run(session.show(FakeEvent(user, chat_id=1, message_id=2)))
        self.assertFalse(session.open)
        self.assertEqual(len(pipeline_mod.pipeline_for(bot)), 0)
        self.assertIsNone(session_for_token(session.token))

    def test_the_route_is_retired_when_the_send_fails(self):
        bot = FakeBot()
        user = FakeUser(bot, fail_with=BotInlineDisabledError(None))
        session = InstantInline(
            [InstantResult(title="x", text="x")], bot=bot, user=user
        )
        with self.assertRaises(InlineUnavailable):
            run(session.show(FakeEvent(user, chat_id=1, message_id=2)))
        self.assertFalse(session.open)
        self.assertEqual(len(pipeline_mod.pipeline_for(bot)), 0)

    def test_the_context_manager_retires_the_route_on_an_exception(self):
        session, bot, _user = make_session()

        async def boom():
            async with session:
                self.assertTrue(session.open)
                raise ZeroDivisionError

        with self.assertRaises(ZeroDivisionError):
            run(boom())
        self.assertFalse(session.open)
        self.assertEqual(len(pipeline_mod.pipeline_for(bot)), 0)

    def test_register_is_idempotent(self):
        session, bot, _user = make_session()

        async def twice():
            await session.register()
            first = session._route
            await session.register()
            return first is session._route

        self.assertTrue(run(twice()))
        self.assertEqual(len(pipeline_mod.pipeline_for(bot)), 1)
        session.close()

    def test_close_is_idempotent_and_reports_only_the_first_removal(self):
        session, _bot, _user = make_session()
        run(session.register())
        self.assertTrue(session.close())
        self.assertFalse(session.close())

    def test_a_token_from_another_user_is_ignored(self):
        session, bot, user = make_session()
        run(session.register())
        update = types.UpdateBotInlineQuery(
            query_id=1, user_id=999999, query=session._compose("x"), offset=""
        )
        update._entities = {}
        run(bot._dispatch_update(update))
        self.assertEqual(bot.answers, [], "a stranger's query is not answered")
        session.close()

    def test_a_query_without_the_token_is_ignored(self):
        session, bot, _user = make_session()
        run(session.register())
        update = types.UpdateBotInlineQuery(
            query_id=1, user_id=7, query="somebody else's query", offset=""
        )
        update._entities = {}
        run(bot._dispatch_update(update))
        self.assertEqual(bot.answers, [])
        session.close()

    def test_two_sessions_on_one_bot_keep_their_tokens_apart(self):
        bot = FakeBot()
        user = FakeUser(bot)
        a = InstantInline([InstantResult(title="A", text="A")], bot=bot, user=user)
        b = InstantInline([InstantResult(title="B", text="B")], bot=bot, user=user)
        self.assertNotEqual(a.token, b.token)
        run(a.register())
        run(b.register())
        self.assertEqual(len(pipeline_mod.pipeline_for(bot)), 2)
        self.assertEqual(len(active_sessions()), 2)
        run(a.query())
        self.assertEqual(bot.answers[-1].results[0].title, "A")
        run(b.query())
        self.assertEqual(bot.answers[-1].results[0].title, "B")
        a.close()
        b.close()
        self.assertEqual(len(pipeline_mod.pipeline_for(bot)), 0)

    def test_the_watchdog_retires_a_route_the_caller_abandoned(self):
        session, bot, _user = make_session(timeout=0.01)

        async def abandon():
            await session.register()
            self.assertTrue(session.open)
            await asyncio.sleep(0.05)

        run(abandon())
        self.assertFalse(session.open, "the watchdog closed the abandoned session")
        self.assertEqual(len(pipeline_mod.pipeline_for(bot)), 0)
        self.assertIsNone(session_for_token(session.token))


class InstantInlineFallbackTests(InstantBase):
    def test_inline_unavailable_fails_closed_without_user_message(self):
        bot = FakeBot()
        user = FakeUser(bot, fail_with=BotInlineDisabledError(None))
        session = InstantInline(
            [InstantResult(title="T", text="body", buttons=["b"])], bot=bot, user=user
        )
        event = FakeEvent(user, chat_id=1, message_id=2)
        with self.assertRaises(InlineUnavailable):
            run(session.show(event))
        self.assertEqual(len(user.queries), 1, "inline query was attempted first")
        self.assertEqual(user.sent, [], "no direct user message was sent")
        self.assertEqual(event.eor_calls, [], "the trigger was not edited as the user")
        self.assertFalse(session.open, "the one-shot route was retired")

    def test_inline_disabled_fails_closed_by_default(self):
        bot = FakeBot()
        user = FakeUser(bot, fail_with=BotInlineDisabledError(None))
        session = InstantInline(
            [InstantResult(title="T", text="b")], bot=bot, user=user
        )
        with self.assertRaises(InlineUnavailable):
            run(session.show(FakeEvent(user, chat_id=1, message_id=2)))
        self.assertEqual(user.sent, [])

    def test_a_missing_bot_is_reported_as_inline_unavailable(self):
        session = InstantInline(
            [InstantResult(title="T", text="b")], bot=None, user=None
        )
        with self.assertRaises(InlineUnavailable):
            session._resolve_clients(None)

    def test_a_non_bot_answering_client_is_refused(self):
        bot = FakeBot()
        bot.me = types.User(id=1, bot=False, first_name="Not a bot")
        user = FakeUser(bot)
        session = InstantInline(
            [InstantResult(title="T", text="b")], bot=bot, user=user
        )
        with self.assertRaises(InlineUnavailable):
            run(session.register())


class InstantInlineValidationTests(InstantBase):
    def test_no_results_is_rejected(self):
        with self.assertRaises(InstantInlineError):
            InstantInline(None)

    def test_per_page_above_telegrams_ceiling_is_rejected(self):
        with self.assertRaises(InstantInlineError):
            InstantInline(["x"], per_page=MAX_RESULTS + 1)

    def test_a_non_positive_timeout_is_rejected(self):
        with self.assertRaises(InstantInlineError):
            InstantInline(["x"], timeout=0)

    def test_the_token_is_unguessable_and_unique(self):
        tokens = {InstantInline(["x"]).token for _ in range(50)}
        self.assertEqual(len(tokens), 50)
        self.assertTrue(all(len(t) > 10 for t in tokens))


class PipelineRegistryLeakTests(InstantBase):
    def test_a_collected_client_drops_out_of_the_pipeline_registry(self):
        class Client:
            pass

        client = Client()
        pipeline = pipeline_mod.pipeline_for(client)
        self.assertIn(pipeline, pipeline_mod.registered_pipelines())
        marker = weakref.ref(client)
        del client
        gc.collect()
        self.assertIsNone(marker(), "the client must be collectable")
        self.assertNotIn(
            pipeline,
            pipeline_mod.registered_pipelines(),
            "the registry entry must go with the client",
        )

    def test_a_pipeline_reports_none_once_its_client_is_gone(self):
        class Client:
            pass

        client = Client()
        pipeline = pipeline_mod.pipeline_for(client)
        del client
        gc.collect()
        self.assertIsNone(pipeline.client)

    def test_an_installed_client_is_still_collectable(self):
        """The saved dispatch methods must not pin the client.

        This is the production shape: every route goes through ``install()``, and
        ``install()`` saves the methods it displaces. Saving them as bound
        methods would make the registry reach the client again.
        """
        bot = FakeBot()
        pipeline = pipeline_mod.pipeline_for(bot).install()
        self.assertTrue(pipeline.installed)
        marker = weakref.ref(bot)
        del bot
        gc.collect()
        self.assertIsNone(marker(), "an installed client must still be collectable")
        self.assertNotIn(pipeline, pipeline_mod.registered_pipelines())
        self.assertIsNone(pipeline.client)

    def test_uninstall_restores_dispatch_and_forgets_the_saved_methods(self):
        bot = FakeBot()
        original = bot._dispatch_update
        pipeline = pipeline_mod.pipeline_for(bot).install()
        self.assertIsNot(bot._dispatch_update, original)
        pipeline.uninstall()
        self.assertFalse(pipeline.installed)
        self.assertEqual(
            bot._dispatch_update.__func__,
            original.__func__,
            "the client's own dispatch must be back in place",
        )
        self.assertFalse(pipeline.installed)

    def test_an_unclaimed_update_survives_an_uninstall_mid_pass(self):
        """A pass that started before the uninstall still falls through.

        A route may uninstall the pipeline while it is awaiting, and so may any
        other task. The update is Telegram's either way, so the pass keeps the
        fall-through it started with instead of finding ``None`` there.
        """
        bot = FakeBot()
        pipeline = pipeline_mod.pipeline_for(bot).install()

        ran = []

        async def slow_handler(event):
            await asyncio.sleep(0)
            pipeline.uninstall()  # the pass is now mid-flight with no _original
            ran.append(event)

        pipeline.route(
            events.InlineQuery,
            slow_handler,
            match=lambda event: True,
            exclusive=False,
        )
        update = types.UpdateBotInlineQuery(
            query_id=1, user_id=7, query="anything", offset=""
        )
        update._entities = {}
        run(bot._dispatch_update(update))
        self.assertEqual(len(ran), 1, "the non-claiming route must have run")
        self.assertIn(update, bot.dispatched)

    def test_a_stacked_teardown_mid_pass_still_reaches_telethon(self):
        """Tearing down a whole stack mid-pass must not strand the update.

        The upper pipeline's fall-through is the lower pipeline's wrapper, so the
        lower one must still be able to fall through after its own uninstall.
        """
        bot = FakeBot()
        lower = pipeline_mod.UpdatePipeline(bot).install()
        upper = pipeline_mod.UpdatePipeline(bot).install()

        async def teardown(event):
            await asyncio.sleep(0)
            upper.uninstall()
            lower.uninstall()

        upper.route(
            events.InlineQuery, teardown, match=lambda event: True, exclusive=False
        )
        update = types.UpdateBotInlineQuery(
            query_id=1, user_id=7, query="anything", offset=""
        )
        update._entities = {}
        run(bot._dispatch_update(update))
        self.assertIn(update, bot.dispatched)

    def test_a_torn_down_pipeline_still_does_not_retain_its_client(self):
        """Keeping the saved callables must not undo the retention fix."""
        bot = FakeBot()
        pipeline = pipeline_mod.pipeline_for(bot).install().uninstall()
        self.assertIsNotNone(pipeline._original)
        marker = weakref.ref(bot)
        del bot
        gc.collect()
        self.assertIsNone(marker())

    def test_an_existing_dispatch_override_is_put_back_not_deleted(self):
        """A client that already overrode dispatch keeps its override."""
        bot = FakeBot()
        calls = []

        async def custom(client, update):
            calls.append(update)

        bot._dispatch_update = MethodType(custom, bot)
        pipeline = pipeline_mod.pipeline_for(bot).install()
        pipeline.uninstall()
        self.assertEqual(bot._dispatch_update.__func__, custom)
        run(bot._dispatch_update("update"))
        self.assertEqual(calls, ["update"])

    def test_a_client_is_collectable_after_a_direct_uninstall(self):
        bot = FakeBot()
        pipeline = pipeline_mod.pipeline_for(bot).install().uninstall()
        marker = weakref.ref(bot)
        del bot
        gc.collect()
        self.assertIsNone(marker())
        self.assertIsNone(pipeline.client)


class FakeEvent:
    """A trigger message used by inline-result round-trip tests."""

    def __init__(self, client, chat_id, message_id):
        self.client = client
        self.chat_id = chat_id
        self.id = message_id
        self.sender_id = getattr(client, "_self_id", None)
        self.out = True
        self.eor_calls = []

    async def eor(self, text, **kwargs):
        self.eor_calls.append((text, kwargs))
        return self


if __name__ == "__main__":
    unittest.main()

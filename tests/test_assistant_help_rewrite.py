from __future__ import annotations

import ast
import importlib.util
from collections import OrderedDict
from types import SimpleNamespace

from conftest import ROOT


def _command_help():
    path = ROOT / "pyUltroid/fns/command_help.py"
    spec = importlib.util.spec_from_file_location("command_help_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_command_syntax_hides_regex_and_keeps_alternatives_readable():
    command_help = _command_help()
    assert command_help.command_syntax("help( (.*)|$)", ".") == ".help [arguments]"
    assert command_help.command_syntax("(?:whisper|whspr)(?: |$)(.*)", ".") == (
        ".whisper / .whspr [arguments]"
    )
    assert command_help.command_syntax("(auto|stop)name$", ".") == (
        ".autoname / .stopname"
    )
    assert command_help.command_syntax("vcmute$", ".") == ".vcmute"
    assert command_help.command_matches("(?:whisper|whspr)(?: |$)(.*)", "whspr")
    assert not command_help.command_matches("vcmute$", "vcunmute")


def test_callback_wrapper_no_longer_injects_settings_callback_heading():
    source = (ROOT / "pyUltroid/_misc/_assistant.py").read_text(encoding="utf-8")
    assert '"Settings Callback"' not in source


def test_start_update_claim_is_idempotent_and_bounded():
    path = ROOT / "assistant/start.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_claim_start_event"
    )
    module = ast.fix_missing_locations(ast.Module(body=[helper], type_ignores=[]))
    namespace = {
        "OrderedDict": OrderedDict,
        "_START_EVENTS": OrderedDict(),
        "_START_EVENT_LIMIT": 3,
    }
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - isolated helper
    claim = namespace["_claim_start_event"]

    event = SimpleNamespace(
        chat_id=10,
        sender_id=20,
        message=SimpleNamespace(id=30),
    )
    assert claim(event)
    assert not claim(event)
    assert claim(
        SimpleNamespace(chat_id=10, sender_id=20, message=SimpleNamespace(id=31))
    )
    assert claim(
        SimpleNamespace(chat_id=10, sender_id=20, message=SimpleNamespace(id=32))
    )
    assert claim(
        SimpleNamespace(chat_id=10, sender_id=20, message=SimpleNamespace(id=33))
    )
    assert len(namespace["_START_EVENTS"]) == 3


def test_settings_buttons_use_plain_consistent_captions():
    source = (ROOT / "assistant/callbackstuffs.py").read_text(encoding="utf-8")
    for inconsistent in (
        "Oғғ",
        "Inғo",
        "PMPermit Type",
        "PMLOGGER",
        "Chat Bot  On",
        "Addons  On",
        "Sudo Mode  On",
    ):
        assert inconsistent not in source


def _load_functions(path, names, namespace):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    module = ast.fix_missing_locations(ast.Module(body=selected, type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - isolated helpers
    return namespace


def test_settings_summary_removes_live_inventory_and_secret_values():
    values = {
        "DEEP_AI": "must-not-appear",
        "PMSETTING": True,
        "VC_SESSION": "private-session-string",
    }
    db = SimpleNamespace(get_key=values.get)
    def render_page(*args, **kwargs):
        return args, kwargs

    helpers = _load_functions(
        ROOT / "assistant/start.py",
        {"_settings_sections", "_settings_page"},
        {"html": __import__("html"), "udB": db, "rich_page": render_page},
    )
    page = helpers["_settings_page"]()
    rendered = repr(page)

    assert page[0][0] == "Assistant Settings"
    assert "Current state" not in rendered
    assert "DEEP_AI" not in rendered and "PMSETTING" not in rendered
    assert "VC_SESSION" not in rendered
    assert "must-not-appear" not in rendered
    assert "private-session-string" not in rendered


def test_language_settings_edit_inline_callback_id_directly():
    import asyncio

    calls = []

    async def edit_rich(*args, **kwargs):
        calls.append((args, kwargs))
        return "edited"

    class InputBotInlineMessageID64:
        pass

    client = object()
    inline_id = InputBotInlineMessageID64()
    event = SimpleNamespace(
        client=client,
        chat_id=12345,
        query=SimpleNamespace(msg_id=inline_id),
    )
    callback_target = _load_functions(
        ROOT / "assistant/rich_pages.py",
        {"callback_edit_target"},
        {"Any": object},
    )["callback_edit_target"]
    helper = _load_functions(
        ROOT / "assistant/localization.py",
        {"_edit_language"},
        {"edit_rich": edit_rich, "callback_edit_target": callback_target},
    )["_edit_language"]

    assert asyncio.run(helper(event, "content", "buttons")) == "edited"
    args, kwargs = calls[0]
    assert args == (client, inline_id, None, "content")
    assert kwargs["buttons"] == "buttons"


def test_force_subscribe_accepts_native_and_legacy_values_without_character_splitting():
    path = ROOT / "assistant/pmbot.py"
    parser = _load_functions(path, {"_force_sub_chats"}, {"ast": ast})[
        "_force_sub_chats"
    ]
    assert parser([-100123, -100456]) == (-100123, -100456)
    assert parser("[-100123, -100456]") == (-100123, -100456)
    assert parser("-100123") == (-100123,)
    assert parser("@example_channel") == ("@example_channel",)
    assert parser(None) == ()
    settings = (ROOT / "assistant/callbackstuffs.py").read_text(encoding="utf-8")
    assert 'udB.set_key("PMBOT_FSUB", str(Ll))' in settings


def test_consumed_assistant_dm_replies_stop_telethon_dispatch():
    bridge = (ROOT / "assistant/agent_bridge.py").read_text(encoding="utf-8")
    sessions = (ROOT / "assistant/ai_dm.py").read_text(encoding="utf-8")
    assert "raise events.StopPropagation" in bridge
    assert "raise events.StopPropagation" in sessions


def test_owner_info_setting_is_read_dynamically_and_false_disables_the_button():
    path = ROOT / "assistant/start.py"

    class FakeDB:
        value = None

        def get_key(self, key):
            if key == "BOT_INFO_START":
                return self.value
            if key == "PMBOT":
                return False
            raise AssertionError(f"Unexpected configuration lookup: {key}")

    db = FakeDB()
    namespace = {
        "udB": db,
        "OWNER_NAME": "Owner",
        "OWNER_ID": 123,
        "ultroid_version": "1.2.3",
    }
    namespace = _load_functions(
        path, {"_default_owner_info", "_owner_info_state"}, namespace
    )
    state = namespace["_owner_info_state"]
    assert state() == (namespace["_default_owner_info"](), False, True)
    db.value = "Custom {mention}"
    assert state() == ("Custom {mention}", True, True)
    db.value = "False"
    assert state() == ("", True, False)


def test_paste_callback_builds_keyboard_with_ikm_call_not_subscript():
    path = ROOT / "assistant/callbackstuffs.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    broken = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "ikm"
    ]
    assert not broken, f"ikm must be called as a builder, not indexed: {broken}"


def test_deployment_guide_falls_back_to_text_when_photo_delivery_fails():
    import asyncio

    path = ROOT / "plugins/_ultroid.py"
    helper = _load_functions(
        path,
        {"_send_fallback_guide"},
        {"DEPLOYMENT_FALLBACK": "readable setup help"},
    )["_send_fallback_guide"]

    class FakeClient:
        def __init__(self):
            self.calls = []

        async def send_file(self, *args, **kwargs):
            self.calls.append(("file", args, kwargs))
            raise OSError("photo unavailable")

        async def send_message(self, *args, **kwargs):
            self.calls.append(("message", args, kwargs))
            return "sent"

    client = FakeClient()
    result = asyncio.run(helper(client, 42))
    assert result == "sent"
    assert [call[0] for call in client.calls] == ["file", "message"]
    assert client.calls[-1][1] == (42, "readable setup help")


def test_pm_bot_profile_link_matches_html_parse_mode():
    source = (ROOT / "assistant/pmbot.py").read_text(encoding="utf-8")
    assert "fns.inline_mention(k, html=True)" in source
    assert "event.text.startswith" not in source


def test_ai_command_prefix_is_anchored_and_consumed_once():
    import re

    path = ROOT / "assistant/ai_dm.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "_AI_PREFIX"
            for target in node.targets
        )
    )
    module = ast.fix_missing_locations(ast.Module(body=[assignment], type_ignores=[]))
    namespace = {"re": re}
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - isolated regex
    prefix = namespace["_AI_PREFIX"]
    assert prefix.sub("", "/ai@helperbot summarize this", count=1) == "summarize this"
    assert prefix.sub("", "please mention /ai in the report", count=1) == (
        "please mention /ai in the report"
    )


def test_ai_session_list_uses_a_structured_list_without_markdown_indentation():
    import asyncio
    import time

    path = ROOT / "assistant/ai_dm.py"

    class Blocks:
        def __init__(self, **_kwargs):
            self.items = None

        def header(self, *_args, **_kwargs):
            return self

        def list(self, items):
            self.items = list(items)
            return self

    sent = {}

    async def send_rich(_client, _chat_id, blocks, **kwargs):
        sent["items"] = blocks.items
        sent["kwargs"] = kwargs
        return "sent"

    helper = _load_functions(
        path,
        {"_send_session_list"},
        {
            "RichBlocks": Blocks,
            "send_rich": send_rich,
            "time": time,
            "_LOGS": SimpleNamespace(exception=lambda *_: None),
        },
    )["_send_session_list"]
    event = SimpleNamespace(client=object(), chat_id=44)
    result = asyncio.run(
        helper(
            event,
            [
                {
                    "title": "A < B",
                    "created": 0,
                    "anchor": 12,
                }
            ],
        )
    )
    assert result == "sent"
    assert sent["items"] == ["A < B — 1970-01-01 00:00 (reply to message 12)"]
    assert not sent["items"][0].startswith("  ")
    assert "**" not in sent["items"][0]
    assert sent["kwargs"]["reply_to"] is event


def test_akinator_provider_text_is_escaped_while_intentional_bold_survives():
    from custom.fmt import fmtmsg, rawhtml
    import html

    path = ROOT / "assistant/games.py"
    result = _load_functions(
        path,
        {"_akinator_result"},
        {"fmtmsg": fmtmsg, "rawhtml": rawhtml, "html": html},
    )["_akinator_result"]("A & B <script>", "literal <strong> and &amp;")
    assert "<b>A &amp; B &lt;script&gt;</b>" in result
    assert "literal &lt;strong&gt; and &amp;amp;" in result
    assert "<script>" not in result
    assert "<strong>" not in result


def test_deploy_notice_state_is_not_saved_when_delivery_fails():
    path = ROOT / "pyUltroid/startup/funcs.py"

    class FakeDB:
        def __init__(self):
            self.values = {}

        def set_key(self, key, value):
            self.values[key] = value

    helper = _load_functions(path, {"_record_deploy_notice_sent"}, {})[
        "_record_deploy_notice_sent"
    ]
    db = FakeDB()
    assert helper(db, None, initial_deploy=True) is False
    assert db.values == {}

    assert helper(db, SimpleNamespace(id=21, media=None), initial_deploy=True)
    assert db.values == {"LAST_UPDATE_LOG_SPAM": 21, "INIT_DEPLOY": "Done"}


def test_admin_verification_marks_missing_or_expired_tokens_as_stale():
    path = ROOT / "assistant/manager/admins.py"
    helper = _load_functions(path, {"_verification_pending"}, {})[
        "_verification_pending"
    ]
    assert helper("token", {"token": object()})
    assert not helper("token", {})
    assert not helper("token", None)


def test_locale_callback_resolves_only_supported_language_codes():
    path = ROOT / "assistant/localization.py"
    helper = _load_functions(path, {"_language_key"}, {})["_language_key"]
    supported = {"en": {}, "hi": {}}
    assert helper("hi", supported) == "hi"
    assert helper("HI", supported) == "hi"
    assert helper("xx", supported) is None


def test_timezone_input_accepts_trimmed_iana_names_and_rejects_unknown_names():
    import pytz

    helper = _load_functions(
        ROOT / "assistant/start.py",
        {"_valid_timezone"},
        {
            "tz": pytz.timezone,
            "UnknownTimeZoneError": pytz.UnknownTimeZoneError,
        },
    )["_valid_timezone"]
    assert helper("  Asia/Kolkata  ") == "Asia/Kolkata"
    assert helper("UTC") == "UTC"
    assert helper("") is None
    assert helper("not/a-real-timezone") is None


def test_message_formatter_passes_native_rich_payloads_without_calling_strip():
    path = ROOT / "pyUltroid/_misc/_assistant.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    formatter = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "MessageFormatter"
    )
    method = next(
        node
        for node in formatter.body
        if isinstance(node, ast.FunctionDef) and node.name == "format"
    )
    module = ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[]))
    namespace = {"Any": object}
    exec(compile(module, str(path), "exec"), namespace)  # noqa: S102 - isolated method

    class Formatter:
        format = namespace["format"]

    native = SimpleNamespace(__rich_format__="html", text="rich payload")
    assert Formatter().format(native) is native


def test_callback_settings_plain_values_are_clean_and_secrets_are_not_shown():
    path = ROOT / "assistant/callbackstuffs.py"

    class FakeDB:
        def get_key(self, key):
            return "super-secret-token" if key == "VC_SESSION" else None

    namespace = _load_functions(
        path,
        {"_mask_val", "_plain"},
        {"udB": FakeDB()},
    )
    assert namespace["_mask_val"]("VC_SESSION") == "Set (hidden)"
    assert "super-secret-token" not in namespace["_mask_val"]("VC_SESSION")
    assert namespace["_plain"]("**Status** and `code`") == "Status and code"


def test_user_client_help_fallback_has_no_assistant_bot_callback_data():
    path = ROOT / "plugins/_help.py"

    def fake_ikm(rows, **_kwargs):
        return rows

    namespace = _load_functions(
        path,
        {"_fallback_help_buttons"},
        {
            "asst": SimpleNamespace(me=SimpleNamespace(username="helper_bot")),
            "ikm": fake_ikm,
        },
    )
    buttons = namespace["_fallback_help_buttons"]()
    assert buttons == [
        [("Open assistant", "url", "https://t.me/helper_bot?start=start", "primary")]
    ]
    assert all(button[1] == "url" for row in buttons for button in row)


def test_assistant_manager_package_exports_runtime_names_for_siblings():
    source = (ROOT / "assistant/manager/__init__.py").read_text(encoding="utf-8")
    assert "from .. import *" in source
    assert (
        '__all__ = tuple(name for name in globals() if not name.startswith("_"))'
        in source
    )


def test_onboarding_callback_has_missing_user_guard_and_native_message_path():
    source = (ROOT / "assistant/manager/_on_adds.py").read_text(encoding="utf-8")
    assert 'user is None or not getattr(user, "is_self", False)' in source
    assert "send_rich(" in source
    assert "except Exception" not in source


def test_afk_command_detection_honors_configured_prefix_and_bot_alias():
    path = ROOT / "assistant/manager/afk.py"
    helper = _load_functions(
        path,
        {"_is_afk_command"},
        {"HNDLR": "."},
    )["_is_afk_command"]
    assert helper(".afk away")
    assert helper("/afk@helper_bot reason")
    assert not helper(".afker")
    assert not helper("ordinary message")


def test_start_text_fallback_escapes_literal_html_and_owner_info_uses_rich_body():
    import html

    path = ROOT / "assistant/start.py"

    def no_parser(value, *, parse_mode):
        assert parse_mode == "mdv3"
        return None

    helper = _load_functions(
        path,
        {"_html_body"},
        {"coerce_to_rich_markup": no_parser, "html": html},
    )["_html_body"]
    assert helper("<strong>literal</strong> &amp;") == (
        "&lt;strong&gt;literal&lt;/strong&gt; &amp;amp;"
    )
    source = path.read_text(encoding="utf-8")
    assert "_rich_menu(\"Owner Information\", msg)" in source
    assert "await edit_callback_page(" in source
    assert "await send_rich(" in source


def test_manager_help_search_section_lists_both_search_commands():
    path = ROOT / "assistant/manager/_help.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    assignments = {
        node.targets[0].id: ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id in {"HELP_SECTIONS", "CATEGORY_LABELS"}
    }

    assert assignments["CATEGORY_LABELS"]["Search"] == "Search"
    assert assignments["HELP_SECTIONS"]["Search"] == [
        ("/search <query>", "Search the web for a query."),
        ("/deepresearch <query>", "Run a deeper, multi-step research search."),
    ]


def test_manager_help_page_footer_shows_counts_and_navigation_hint():
    import html

    path = ROOT / "assistant/manager/_help.py"
    seen = []

    def rich_page(_title, _rows, **kwargs):
        seen.append(kwargs)
        return SimpleNamespace(
            text=(
                f"<p><i>page {kwargs['page']} of {kwargs['pages']} "
                f"• total {kwargs['total']}</i></p>"
            )
        )

    namespace = {
        "CATEGORY_LABELS": {"Search": "Search"},
        "HELP_PAGE_SIZE": 1,
        "HELP_SECTIONS": {
            "Search": [
                ("/search <query>", "Search the web."),
                ("/deepresearch <query>", "Research in depth."),
            ]
        },
        "HELP_HOME_DATA": "home",
        "cb": lambda *parts: ":".join(map(str, parts)),
        "html": html,
        "ikm": lambda rows, **_kwargs: rows,
        "rich_page": rich_page,
    }
    helper = _load_functions(path, {"_help_page"}, namespace)["_help_page"]

    first_page, _buttons = helper("Search", 0)
    assert first_page.text.endswith("<p><i>page 1 of 2 • total 2</i></p>")
    last_page, _buttons = helper("Search", 1)
    assert last_page.text.endswith("<p><i>page 2 of 2 • total 2</i></p>")
    assert seen[0]["total"] == 2 and seen[1]["total"] == 2

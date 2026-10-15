from .debug import DebugKit, debug_kit, aexec, shell
from .fmt import FmtKit, fmt_kit, fmtbar, fmtbyte, fmtexc, fmtmsg, fmtsec, prog
from .util import UtilKit, util_kit, patch, usecs
from .tg import (
    DEFAULT_ICON_UNICODE,
    TG_ANDROID_ICON_DOCUMENT_IDS,
    ButtonKit,
    button_kit,
    ids,
    ikm,
    register_button_type,
    request_peer_button,
    rkm,
)

__all__ = [
    "DEFAULT_ICON_UNICODE",
    "TG_ANDROID_ICON_DOCUMENT_IDS",
    "ButtonKit",
    "button_kit",
    "DebugKit",
    "debug_kit",
    "FmtKit",
    "fmt_kit",
    "IconKit",
    "icon_kit",
    "RichKit",
    "rich_kit",
    "UtilKit",
    "util_kit",
    "aexec",
    "fmtbar",
    "fmtbyte",
    "fmtexc",
    "fmtmsg",
    "fmtsec",
    "ids",
    "ikm",
    "patch",
    "prog",
    "register_button_type",
    "request_peer_button",
    "rkm",
    "shell",
    "usecs",
]

# The rich layer needs the optional markdown-v3 extras; the class layer is
# capability-gated (None when absent) rather than deferred behind a hook.
try:
    from .rich import (  # noqa: F401
        RICH_MD,
        STYLE_PRESETS,
        RichKit,
        button,
        emoji_entity,
        parse_rich,
        premium_emoji,
        resolve_style,
        rich_ikm,
        rich_kit,
        unparse_rich,
        with_emojis,
    )
except Exception:
    RichKit = rich_kit = None
else:
    __all__ += [
        "RICH_MD",
        "STYLE_PRESETS",
        "button",
        "emoji_entity",
        "parse_rich",
        "premium_emoji",
        "resolve_style",
        "rich_ikm",
        "unparse_rich",
        "with_emojis",
    ]

try:
    from .icons import IconKit, icon_kit  # noqa: F401
except Exception:
    IconKit = icon_kit = None

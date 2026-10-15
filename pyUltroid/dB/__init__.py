from .. import udB

if udB is None:
    # The runtime has not started: importing the per-feature database layer
    # would construct singletons against a database that does not exist.
    # This is the honest gate - not how the interpreter was invoked.
    from ..exceptions import RunningAsFunctionLibError

    raise RunningAsFunctionLibError(
        "The pyUltroid runtime has not started, so the database layer is"
        " unavailable. Start it with `python -m pyUltroid` (the runtime"
        " binds udB before anything imports this package)."
    )

from ..configs import Var

DEVLIST = list(Var.DEVLIST_IDS)

ULTROID_IMAGES = [
    f"https://graph.org/file/{_}.jpg"
    for _ in [
        "ec250c66268b62ee4ade6",
        "3c25230ae30d246194eba",
        "b01715a61b9e876c0d45d",
        "4ceaf720a96a24527ecff",
        "a96223b574f29f3f0d184",
        "6e081d339a01cc6190393",
    ]
]

stickers = [
    "CAADAQADeAIAAm_BZBQh8owdViocCAI",
    "CAADAQADegIAAm_BZBQ6j8GpKtnrSgI",
    "CAADAQADfAIAAm_BZBQpqC84n9JNXgI",
    "CAADAQADfgIAAm_BZBSxLmTyuHvlzgI",
    "CAADAQADgAIAAm_BZBQ3TZaueMkS-gI",
    "CAADAQADggIAAm_BZBTPcbJMorVVsQI",
    "CAADAQADhAIAAm_BZBR3lnMZRdsYxAI",
    "CAADAQADhgIAAm_BZBQGQRx4iaM4pQI",
    "CAADAQADiAIAAm_BZBRRF-cjJi_QywI",
    "CAADAQADigIAAm_BZBQQJwfzkqLM0wI",
    "CAADAQADjAIAAm_BZBQSl5GSAT0viwI",
    "CAADAQADjgIAAm_BZBQ2xU688gfHhQI",
    "CAADAQADkAIAAm_BZBRGuPNgVvkoHQI",
    "CAADAQADpgIAAm_BZBQAAZr0SJ5EKtQC",
    "CAADAQADkgIAAm_BZBTvuxuayqvjhgI",
    "CAADAQADlAIAAm_BZBSMZdWN2Yew1AI",
    "CAADAQADlQIAAm_BZBRXyadiwWGNkwI",
    "CAADAQADmAIAAm_BZBQDoB15A1jS1AI",
    "CAADAQADmgIAAm_BZBTnOLQ8_d72vgI",
    "CAADAQADmwIAAm_BZBTve1kgdG0Y5gI",
    "CAADAQADnAIAAm_BZBQUMyFiylJSqQI",
    "CAADAQADnQIAAm_BZBSMAe2V4pwhNgI",
    "CAADAQADngIAAm_BZBQ06D92QL_vywI",
    "CAADAQADnwIAAm_BZBRw7UAbr6vtEgI",
    "CAADAQADoAIAAm_BZBRkv9DnGPXh_wI",
    "CAADAQADoQIAAm_BZBQwI2NgQdyKlwI",
    "CAADAQADogIAAm_BZBRPHJF3XChVLgI",
    "CAADAQADowIAAm_BZBThpas7rZD6DAI",
    "CAADAQADpAIAAm_BZBQcC2DpZcCw1wI",
    "CAADAQADpQIAAm_BZBQKruTcEU4ntwI",
]

# ── Class exports (the per-feature DB layer, one class per feature) ──
# Each module owns one class + one singleton; the module-level functions are
# backward-compatible aliases for the plugin surface.

from .afk_db import AfkDB
from .antiflood_db import AntifloodDB
from .asstcmd_db import AsstCmdDB
from .base import FeatureDB, KeyManager
from .blacklist_chat_db import BlacklistChatDB
from .blacklist_db import BlacklistDB
from .botchat_db import BotChatDB
from .echo_db import EchoDB
from .filestore_db import FileStoreDB
from .filter_db import FilterDB
from .forcesub_db import ForceSubDB
from .gban_mute_db import GbanMuteDB
from .guest_db import GuestDB, guest_db
from .greetings_db import GreetingsDB
from .mute_db import MuteDB
from .notes_db import NotesDB
from .nsfw_db import NsfwDB
from .snips_db import SnipsDB
from .vc_sudos import VcSudosDB
from .warn_db import WarnDB

# ── the database layer, as one object ───────────────────────────────────────
# Each feature is an independent namespace with its own verbs - ``add`` means
# something different to filters, warnings and notes. One flat class inheriting
# all nineteen would carry nineteen ``add`` methods and MRO would pick one, so
# they stay attributes: every verb remains reachable and nothing is shadowed.


class Database:
    """Every feature store, as one object.

    ``db.filter.add(...)`` reads better than importing ``filter_db`` and
    wondering whether you hold the class or a singleton.
    """

    __slots__ = (
        "afk",
        "antiflood",
        "asstcmd",
        "blacklist_chat",
        "blacklist",
        "botchat",
        "echo",
        "filestore",
        "filter",
        "forcesub",
        "gban_mute",
        "guest",
        "greetings",
        "mute",
        "notes",
        "nsfw",
        "snips",
        "vc_sudos",
        "warn",
    )

    def __init__(self) -> None:
        self.afk = AfkDB()
        self.antiflood = AntifloodDB()
        self.asstcmd = AsstCmdDB()
        self.blacklist_chat = BlacklistChatDB()
        self.blacklist = BlacklistDB()
        self.botchat = BotChatDB()
        self.echo = EchoDB()
        self.filestore = FileStoreDB()
        self.filter = FilterDB()
        self.forcesub = ForceSubDB()
        self.gban_mute = GbanMuteDB()
        self.guest = GuestDB()
        self.greetings = GreetingsDB()
        self.mute = MuteDB()
        self.notes = NotesDB()
        self.nsfw = NsfwDB()
        self.snips = SnipsDB()
        self.vc_sudos = VcSudosDB()
        self.warn = WarnDB()

    def features(self) -> dict:
        """Feature name -> store, for diagnostics."""
        return {name: getattr(self, name) for name in self.__slots__}

    def keys(self) -> dict:
        """Feature name -> the udB key it owns."""
        return {name: store.key for name, store in self.features().items()}


#: The process-wide database layer.
db = Database()

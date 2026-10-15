# Ultroid - UserBot
# Copyright (C) 2021-2023 TeamUltroid
#
# This file is a part of < https://github.com/TeamUltroid/Ultroid/ >
# PLease read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.

import ast
import os

try:
    from redis import Redis
except ImportError:
    Redis = None

try:
    from pymongo import MongoClient
except ImportError:
    MongoClient = None

try:
    import psycopg2
except ImportError:
    psycopg2 = None

from . import *

try:
    from ..configs import Var
except Exception:
    Var = None

try:
    from localdb import Database

    if not (hasattr(Database, "get") and hasattr(Database, "set")):
        Database = None
except Exception:
    Database = None


# --------------------------------------------------------------------------------------------- #


class _BaseDatabase:
    def __init__(self, *args, **kwargs):
        self._cache = {}

    def get_key(self, key):
        try:
            from pyUltroid.tracer import get_current_trace

            trace = get_current_trace()
            if trace:
                trace.db_reads.append(str(key))
        except Exception:
            pass
        if key in self._cache:
            return self._cache[key]
        value = self._get_data(key)
        self._cache.update({key: value})
        return value

    def re_cache(self):
        self._cache.clear()
        for key in self.keys():
            self._cache.update({key: self.get_key(key)})

    def ping(self):
        return 1

    @property
    def usage(self):
        return 0

    def keys(self):
        return []

    def del_key(self, key):
        try:
            from pyUltroid.tracer import get_current_trace

            trace = get_current_trace()
            if trace:
                trace.db_writes.append(str(key))
        except Exception:
            pass
        if key in self._cache:
            del self._cache[key]
        self.delete(key)
        return True

    def _get_data(self, key=None, data=None):
        if key:
            data = self.get(str(key))
        if data and isinstance(data, str):
            try:
                data = ast.literal_eval(data)
            except Exception:
                pass
        return data

    def set_key(self, key, value, cache_only=False):
        try:
            from pyUltroid.tracer import get_current_trace

            trace = get_current_trace()
            if trace:
                trace.db_writes.append(str(key))
        except Exception:
            pass
        value = self._get_data(data=value)
        self._cache[key] = value
        if cache_only:
            return
        return self.set(str(key), str(value))

    def rename(self, key1, key2):
        _ = self.get_key(key1)
        if _:
            self.del_key(key1)
            self.set_key(key2, _)
            return 0
        return 1


class MongoDB(_BaseDatabase):
    def __init__(self, key, dbname="UltroidDB"):
        self.dB = MongoClient(key, serverSelectionTimeoutMS=5000)
        self.db = self.dB[dbname]
        super().__init__()

    def __repr__(self):
        return f"<Ultroid.MonGoDB\n -total_keys: {len(self.keys())}\n>"

    @property
    def name(self):
        return "Mongo"

    @property
    def usage(self):
        return self.db.command("dbstats")["dataSize"]

    def ping(self):
        if self.dB.server_info():
            return True

    def keys(self):
        return self.db.list_collection_names()

    def set(self, key, value):
        if key in self.keys():
            self.db[key].replace_one({"_id": key}, {"value": str(value)})
        else:
            self.db[key].insert_one({"_id": key, "value": str(value)})
        return True

    def delete(self, key):
        self.db.drop_collection(key)

    def get(self, key):
        if x := self.db[key].find_one({"_id": key}):
            return x["value"]

    def flushall(self):
        self.dB.drop_database("UltroidDB")
        self._cache.clear()
        return True


# --------------------------------------------------------------------------------------------- #

# Thanks to "Akash Pattnaik" / @BLUE-DEVIL1134
# for SQL Implementation in Ultroid.
#
# Please use https://elephantsql.com/ !


class SqlDB(_BaseDatabase):
    def __init__(self, url):
        self._url = url
        self._connection = None
        self._cursor = None
        try:
            self._connection = psycopg2.connect(dsn=url)
            self._connection.autocommit = True
            self._cursor = self._connection.cursor()
            self._cursor.execute(
                "CREATE TABLE IF NOT EXISTS Ultroid (ultroidCli varchar(70))"
            )
        except Exception as error:
            LOGS.exception(error)
            LOGS.info("Invalid SQL Database")
            if self._connection:
                self._connection.close()
            raise RuntimeError("Invalid SQL Database") from error
        super().__init__()

    @property
    def name(self):
        return "SQL"

    @property
    def usage(self):
        self._cursor.execute(
            "SELECT pg_size_pretty(pg_relation_size('Ultroid')) AS size"
        )
        data = self._cursor.fetchall()
        return int(data[0][0].split()[0])

    def keys(self):
        self._cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name  = 'ultroid'"
        )  # case sensitive
        data = self._cursor.fetchall()
        return [_[0] for _ in data]

    def get(self, variable):
        try:
            self._cursor.execute(f"SELECT {variable} FROM Ultroid")
        except psycopg2.errors.UndefinedColumn:
            return None
        data = self._cursor.fetchall()
        if not data:
            return None
        if len(data) >= 1:
            for i in data:
                if i[0]:
                    return i[0]

    def set(self, key, value):
        try:
            self._cursor.execute(f"ALTER TABLE Ultroid DROP COLUMN IF EXISTS {key}")
        except (psycopg2.errors.UndefinedColumn, psycopg2.errors.SyntaxError):
            pass
        except Exception as er:
            LOGS.exception(er)
        self._cache.update({key: value})
        self._cursor.execute(f"ALTER TABLE Ultroid ADD {key} TEXT")
        self._cursor.execute(f"INSERT INTO Ultroid ({key}) values (%s)", (str(value),))
        return True

    def delete(self, key):
        try:
            self._cursor.execute(f"ALTER TABLE Ultroid DROP COLUMN {key}")
        except psycopg2.errors.UndefinedColumn:
            return False
        return True

    def flushall(self):
        self._cache.clear()
        self._cursor.execute("DROP TABLE Ultroid")
        self._cursor.execute(
            "CREATE TABLE IF NOT EXISTS Ultroid (ultroidCli varchar(70))"
        )
        return True


# --------------------------------------------------------------------------------------------- #


class RedisDB(_BaseDatabase):
    def __init__(
        self,
        host,
        port,
        password,
        platform="",
        logger=LOGS,
        *args,
        **kwargs,
    ):
        if host and ":" in host:
            spli_ = host.split(":")
            host = spli_[0]
            port = int(spli_[-1])
            if host.startswith("http"):
                logger.error("Your REDIS_URI should not start with http !")
                raise RuntimeError("Your REDIS_URI should not start with http!")
        elif not host or not port:
            logger.error("Port Number not found")
            raise RuntimeError("Redis host or port not found")
        kwargs["host"] = host
        kwargs["password"] = password
        kwargs["port"] = port

        if platform.lower() == "qovery" and not host:
            var, hash_, host, password = "", "", "", ""
            for vars_ in os.environ:
                if vars_.startswith("QOVERY_REDIS_") and vars_.endswith("_HOST"):
                    var = vars_
            if var:
                hash_ = var.split("_", maxsplit=2)[1].split("_")[0]
            if hash_:
                kwargs["host"] = os.environ.get(f"QOVERY_REDIS_{hash_}_HOST")
                kwargs["port"] = os.environ.get(f"QOVERY_REDIS_{hash_}_PORT")
                kwargs["password"] = os.environ.get(f"QOVERY_REDIS_{hash_}_PASSWORD")
        self.db = Redis(**kwargs)
        self.set = self.db.set
        self.get = self.db.get
        self.keys = self.db.keys
        self.delete = self.db.delete
        super().__init__()

    @property
    def name(self):
        return "Redis"

    @property
    def usage(self):
        return sum(self.db.memory_usage(x) for x in self.keys())


# --------------------------------------------------------------------------------------------- #


class _JSONDatabase:
    """Built-in dict-on-disk store used when the external ``localdb``
    package is unavailable. Same surface as ``localdb.Database``:
    ``get``/``set``/``delete``/``keys``. Lazy: the file is only created on
    the first write."""

    def __init__(self, name="ultroid"):
        import json
        import os

        self._json = json
        self._path = f"{name}.localdb.json"
        self._data = {}
        if os.path.exists(self._path):
            try:
                with open(self._path, encoding="utf-8") as fh:
                    self._data = json.load(fh) or {}
            except Exception as exc:
                LOGS.warning("Could not read %s (%s); starting empty.", self._path, exc)

    def _flush(self):
        tmp = f"{self._path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            self._json.dump(self._data, fh, ensure_ascii=False)
        import os

        os.replace(tmp, self._path)

    def get(self, key):
        return self._data.get(str(key))

    def set(self, key, value):
        self._data[str(key)] = value
        self._flush()
        return True

    def delete(self, key):
        self._data.pop(str(key), None)
        self._flush()
        return True

    def keys(self):
        return list(self._data.keys())


class LocalDB(_BaseDatabase):
    def __init__(self):
        backend = Database if Database is not None else _JSONDatabase
        self.db = backend("ultroid")
        self.get = self.db.get
        self.set = self.db.set
        self.delete = self.db.delete
        super().__init__()

    @property
    def name(self):
        return "LocalDB"

    def keys(self):
        db_keys = getattr(self.db, "keys", None)
        if callable(db_keys):
            try:
                merged = set(db_keys()) | set(self._cache.keys())
                return list(merged)
            except Exception:
                pass
        return self._cache.keys()

    def __repr__(self):
        return f"<Ultroid.LocalDB\n -total_keys: {len(self.keys())}\n>"


def _backend_usable(name, configured, driver, package):
    """True when *configured* and its client *driver* is importable.

    Configured-but-uninstalled logs an error and falls through to the next
    backend instead of aborting startup."""
    if not configured:
        return False
    if driver is None:
        LOGS.error(
            "%s is configured but the '%s' package is not installed; "
            "falling back to the next available database backend.",
            name,
            package,
        )
        return False
    return True


def UltroidDB():
    """Pick the database backend from what's configured in ``Var``:
    Redis > Mongo > SQL > LocalDB (always available, built-in JSON store)."""
    from .. import HOSTED_ON

    def _flag(value):
        return "SET" if value else "unset"

    LOGS.info(
        "database drivers: redis=%s pymongo=%s psycopg2=%s",
        "importable" if Redis else "missing",
        "importable" if MongoClient else "missing",
        "importable" if psycopg2 else "missing",
    )
    LOGS.info(
        "database config: REDIS_URI=%s MONGO_URI=%s DATABASE_URL=%s",
        _flag(Var and (Var.REDIS_URI or Var.REDISHOST)),
        _flag(Var and Var.MONGO_URI),
        _flag(Var and Var.DATABASE_URL),
    )

    try:
        if _backend_usable(
            "Redis", Var and (Var.REDIS_URI or Var.REDISHOST), Redis, "redis"
        ):
            return RedisDB(
                host=Var.REDIS_URI or Var.REDISHOST,
                password=Var.REDIS_PASSWORD or Var.REDISPASSWORD,
                port=Var.REDISPORT,
                platform=HOSTED_ON,
                decode_responses=True,
                socket_timeout=5,
                retry_on_timeout=True,
            )
        if _backend_usable("MongoDB", Var and Var.MONGO_URI, MongoClient, "pymongo"):
            return MongoDB(Var.MONGO_URI)
        if _backend_usable(
            "SQL", Var and Var.DATABASE_URL, psycopg2, "psycopg2-binary"
        ):
            return SqlDB(Var.DATABASE_URL)
        LOGS.info("Using local file as database.")
        return LocalDB()
    except Exception as err:
        LOGS.exception(err)
        raise

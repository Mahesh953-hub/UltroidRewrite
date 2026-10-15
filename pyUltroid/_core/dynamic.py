# Ultroid - UserBot
# Copyright (C) 2021-2025 TeamUltroid
#
# This file is part of < https://github.com/TeamUltroid/Ultroid/ >
# Please read the GNU Affero General Public License in
# <https://github.com/TeamUltroid/pyUltroid/blob/main/LICENSE>.
"""Dynamic runtime facilities for Ultroid"""

from __future__ import annotations

import dataclasses
import time
from collections import deque
from dataclasses import dataclass, field, make_dataclass
from typing import Any, Optional
from collections.abc import Callable, Iterable


# Extension contracts (lightweight documentation / lookup registry)
@dataclass(frozen=True)
class ExtensionContract:
    """A documented, discoverable dynamic function contract.

    The optional *func* may be ``None`` when the contract lives in another
    owner and we only want to publish its signature and semantics.
    """

    name: str
    func: Callable[..., Any] | None
    description: str
    category: str
    signature: tuple[str, ...] = field(default_factory=tuple)

    def __repr__(self) -> str:  # pragma: no cover - nicer debugging output
        return f"ExtensionContract({self.name!r}, category={self.category!r})"


_EXTENSION_CONTRACTS: dict[str, ExtensionContract] = {}


def register_contract(
    name: str,
    func: Callable[..., Any] | None = None,
    *,
    description: str = "",
    category: str = "dynamic",
    signature: Iterable[str] | None = None,
) -> ExtensionContract:
    """Register a discoverable contract for a dynamic helper.

    Args:
        name: Short contract name (e.g. ``"eor"``).
        func: The callable, if available locally; ``None`` for cross-owner
            contracts that are documented here but implemented elsewhere.
        description: Human-readable semantics and return shape.
        category: Grouping key for filtering.
        signature: Optional parameter names (positional/keyword).

    Returns:
        The registered :class:`ExtensionContract`.
    """

    contract = ExtensionContract(
        name=name,
        func=func,
        description=description,
        category=category,
        signature=tuple(signature) if signature else (),
    )
    _EXTENSION_CONTRACTS[name] = contract
    return contract


def get_contract(name: str) -> ExtensionContract | None:
    """Return a registered extension contract by name, or ``None``."""
    return _EXTENSION_CONTRACTS.get(name)


def unregister_contract(name: str) -> bool:
    """Forget a registered contract; ``True`` when one was removed.

    Without this a plugin that registers a contract and is then unloaded leaves
    its callable  -  and the module that defines it  -  reachable forever.
    """
    return _EXTENSION_CONTRACTS.pop(name, None) is not None


def clear_contracts(category: str | None = None) -> int:
    """Forget every contract, or every contract in ``category``.

    Returns the number of contracts removed.
    """
    doomed = [
        name
        for name, contract in _EXTENSION_CONTRACTS.items()
        if category is None or contract.category == category
    ]
    for name in doomed:
        del _EXTENSION_CONTRACTS[name]
    return len(doomed)


def list_contracts(category: str | None = None) -> list[ExtensionContract]:
    """Return all registered contracts, optionally filtered by category."""
    contracts = list(_EXTENSION_CONTRACTS.values())
    if category is not None:
        contracts = [c for c in contracts if c.category == category]
    return contracts


# Patch registry with audit log and persisted enable/disable state
@dataclass(frozen=True)
class AuditRecord:
    """A single patch-registry event.

    *action* is one of ``register``, ``apply``, ``unapply``, ``enable``,
    ``disable``.
    """

    timestamp: float
    name: str
    action: str
    target: str
    success: bool
    message: str


@dataclass
class PatchRecord:
    """Internal metadata for one registered patch."""

    name: str
    target: type
    func: Callable[..., Any]
    attr_name: str
    description: str
    category: str
    enabled_default: bool
    original_attr: Any = None
    applied: bool = False
    #: Update routes this patch contributes, installed on apply and retired on
    #: unapply. Each entry is a ``Route`` or a ``(updates, handler, options)``
    #: tuple; ``options`` is passed to ``UpdatePipeline.route``.
    routes: list = field(default_factory=list)


def patch_owner(name: str) -> str:
    """The pipeline owner label a patch's routes are registered under."""
    return f"patch:{name}"


class PatchRegistry:
    """Named, auditable, rollback-capable patch registry.

    Args:
        db: A database object with ``get_key(name)`` / ``set_key(name, value)`` /
            ``del_key(name)`` (typically ``pyUltroid.udB``).  May be ``None`` for
            unit tests or pre-bootstrap imports; persistence is skipped when it
            is absent.
        state_key: udB key used to store enable/disable flags.
        audit_capacity: Maximum number of audit records kept in memory.
    """

    def __init__(
        self,
        db: Any | None = None,
        state_key: str = "DYNAMIC_PATCH_STATE",
        *,
        audit_capacity: int = 200,
        client: Any | None = None,
    ) -> None:
        self._db = db
        self._state_key = state_key
        self._patches: dict[str, PatchRecord] = {}
        self._audit: deque[AuditRecord] = deque(maxlen=audit_capacity)
        self._overrides: dict[str, bool] = {}
        self._client = client

    def bind_database(self, db: Any | None) -> None:
        """Attach (or re-attach) a udB instance to the registry."""
        self._db = db

    def bind_client(self, client: Any | None) -> None:
        """Attach the client whose update pipeline patch routes install on.

        Patches that were already applied before a client existed get their
        routes installed here; otherwise a route registered early would stay
        absent from the pipeline forever.
        """
        self._client = client
        if client is None:
            return
        for rec in self._patches.values():
            if rec.applied and rec.routes and not self.routes_of(rec.name):
                self._install_routes(rec)

    def _load_state(self) -> dict[str, bool]:
        """Load persisted enable/disable state.  Safe to call before udB exists.

        In-memory overrides take precedence over persisted state so that
        enable/disable work even when no database has been bound yet.
        """
        if self._db is None:
            return dict(self._overrides)
        raw = self._db.get_key(self._state_key)
        state = (
            {str(k): bool(v) for k, v in raw.items()} if isinstance(raw, dict) else {}
        )
        # In-memory overrides are authoritative for this process.
        state.update(self._overrides)
        return state

    def _persist(self, name: str, enabled: bool) -> None:
        """Write a single patch flag back to udB (and cache it in memory)."""
        self._overrides[name] = enabled
        if self._db is None:
            return
        state = self._load_state()
        state[name] = enabled
        self._db.set_key(self._state_key, state)

    def _record(
        self, name: str, action: str, target: str, success: bool, message: str
    ) -> None:
        self._audit.append(
            AuditRecord(
                timestamp=time.time(),
                name=name,
                action=action,
                target=target,
                success=success,
                message=message,
            )
        )

    def _pipeline(self) -> Any | None:
        """The bound client's pipeline, or ``None`` when no client is bound."""
        if self._client is None:
            return None
        from .pipeline import pipeline_for

        return pipeline_for(self._client)

    def routes_of(self, name: str) -> int:
        """How many update routes patch ``name`` currently has installed."""
        pipeline = self._pipeline()
        if pipeline is None:
            return 0
        owner = patch_owner(name)
        return sum(1 for route in pipeline.routes if route.owner == owner)

    def _install_routes(self, rec: PatchRecord) -> int:
        """Install ``rec.routes`` on the bound pipeline; returns how many.

        A route that cannot be installed is audited and skipped  -  a cosmetic
        route must never make an otherwise-good patch fail to apply.
        """
        pipeline = self._pipeline()
        if not rec.routes or pipeline is None:
            return 0
        from .pipeline import Route

        owner = patch_owner(rec.name)
        installed = 0
        for spec in rec.routes:
            try:
                if isinstance(spec, Route):
                    spec.owner = owner
                    pipeline.add(spec)
                else:
                    updates, handler, *rest = spec
                    pipeline.route(
                        updates, handler, owner=owner, **(rest[0] if rest else {})
                    )
            except Exception as exc:  # noqa: BLE001
                self._record(
                    rec.name,
                    "apply",
                    _type_name(rec.target),
                    False,
                    f"Route install failed: {exc}",
                )
                continue
            installed += 1
        return installed

    def _retire_routes(self, rec: PatchRecord) -> int:
        """Remove every route owned by ``rec``; returns how many were removed."""
        pipeline = self._pipeline()
        if pipeline is None:
            return 0
        return pipeline.remove_owner(patch_owner(rec.name))

    def register(
        self,
        name: str,
        target: type,
        func: Callable[..., Any],
        *,
        attr_name: str | None = None,
        description: str = "",
        category: str = "default",
        enabled: bool = True,
        allow_override: bool = False,
        routes: Iterable[Any] | None = None,
    ) -> PatchRecord:
        """Register a patch without applying it.

        Args:
            name: Unique patch name.
            target: Class whose attribute will be replaced.
            func: Callable to attach.
            attr_name: Attribute name on *target*.  Defaults to ``func.__name__``
                with a single leading underscore stripped (legacy behaviour).
            description: Optional explanation.
            category: Grouping/category key.
            enabled: Default enabled state.  Can be overridden by persisted udB
                state after the database is bound.
            allow_override: If ``False`` (default), re-registering the same name
                raises :class:`ValueError`.
            routes: Update routes this patch contributes. Each entry is a
                :class:`pyUltroid._core.pipeline.Route` or a
                ``(updates, handler, options)`` tuple. They are installed on
                the bound client's pipeline by :meth:`apply` and removed by
                :meth:`unapply`, both under the ``patch:<name>`` owner label.

        Returns:
            The :class:`PatchRecord` describing the patch.
        """

        if name in self._patches and not allow_override:
            raise ValueError(f"Patch {name!r} is already registered")
        rec = PatchRecord(
            name=name,
            target=target,
            func=func,
            attr_name=attr_name or func.__name__.removeprefix("_"),
            description=description,
            category=category,
            enabled_default=enabled,
            routes=list(routes or ()),
        )
        self._patches[name] = rec
        self._record(
            name,
            "register",
            _type_name(target),
            True,
            f"Registered patch {name!r} -> {rec.attr_name}",
        )
        return rec

    def is_registered(self, name: str) -> bool:
        """Return whether *name* is a known patch."""
        return name in self._patches

    def is_enabled(self, name: str) -> bool:
        """Return whether *name* is currently enabled.

        Persisted state overrides the default set at registration time.
        """
        rec = self._patches.get(name)
        if rec is None:
            raise KeyError(name)
        state = self._load_state()
        if name in state:
            return state[name]
        return rec.enabled_default

    def enable(self, name: str) -> bool:
        """Enable a patch and persist the flag."""
        rec = self._patches.get(name)
        if rec is None:
            raise KeyError(name)
        self._persist(name, True)
        self._record(name, "enable", _type_name(rec.target), True, "Enabled")
        return True

    def disable(self, name: str) -> bool:
        """Disable a patch and persist the flag."""
        rec = self._patches.get(name)
        if rec is None:
            raise KeyError(name)
        self._persist(name, False)
        self._record(name, "disable", _type_name(rec.target), True, "Disabled")
        return True

    def apply(self, name: str, *, force: bool = False) -> bool:
        """Apply a registered patch to its target class.

        Args:
            name: Patch name.
            force: Apply even if the patch is disabled.

        Returns:
            ``True`` if the patch is applied after this call (including the
            idempotent already-applied case), ``False`` if it was skipped
            because it is disabled.

        Raises:
            KeyError: Unknown patch name.
            Exception: Any exception raised while setting the attribute.
        """
        rec = self._patches.get(name)
        if rec is None:
            raise KeyError(name)

        if not force and not self.is_enabled(name):
            self._record(
                name,
                "apply",
                _type_name(rec.target),
                False,
                "Skipped (disabled)",
            )
            return False

        if rec.applied:
            detail = "Already applied (idempotent)"
            # An earlier apply may have run before a client was bound, or a
            # route may have failed to install. Retry rather than reporting an
            # incomplete patch as complete.
            if rec.routes and self._client is not None and not self.routes_of(name):
                installed = self._install_routes(rec)
                if installed:
                    detail += f" + {installed} route(s)"
            self._record(name, "apply", _type_name(rec.target), True, detail)
            return True

        attr_name = rec.attr_name
        try:
            original = getattr(rec.target, attr_name, None)
            setattr(rec.target, attr_name, rec.func)
            rec.original_attr = original
            rec.applied = True
            installed = self._install_routes(rec)
            detail = f"Set {attr_name!r}"
            if installed:
                detail += f" + {installed} route(s)"
            self._record(name, "apply", _type_name(rec.target), True, detail)
            return True
        except Exception as exc:  # noqa: BLE001
            self._record(
                name,
                "apply",
                _type_name(rec.target),
                False,
                f"Failed: {exc}",
            )
            raise

    def unapply(self, name: str) -> bool:
        """Rollback a patch, restoring the original target attribute.

        Returns:
            ``True`` if the patch was active and is now rolled back,
            ``False`` if it was not currently applied.

        Raises:
            KeyError: Unknown patch name.
            Exception: Any exception raised while restoring the attribute.
        """
        rec = self._patches.get(name)
        if rec is None:
            raise KeyError(name)
        if not rec.applied:
            self._record(
                name,
                "unapply",
                _type_name(rec.target),
                False,
                "Not currently applied",
            )
            return False

        attr_name = rec.attr_name
        try:
            if rec.original_attr is None:
                # The attribute did not exist before the patch; delete it.
                try:
                    delattr(rec.target, attr_name)
                except AttributeError:
                    pass
            else:
                setattr(rec.target, attr_name, rec.original_attr)
            rec.original_attr = None
            rec.applied = False
            retired = self._retire_routes(rec)
            detail = f"Restored {attr_name!r}"
            if retired:
                detail += f", removed {retired} route(s)"
            self._record(name, "unapply", _type_name(rec.target), True, detail)
            return True
        except Exception as exc:  # noqa: BLE001
            self._record(
                name,
                "unapply",
                _type_name(rec.target),
                False,
                f"Failed: {exc}",
            )
            raise

    def apply_all(self, *, force: bool = False) -> dict[str, bool]:
        """Apply all registered patches.  Returns a map of name -> applied."""
        return {name: self.apply(name, force=force) for name in self._patches}

    def unapply_all(self) -> dict[str, bool]:
        """Unapply all currently applied patches."""
        return {name: self.unapply(name) for name in self._patches}

    def list_patches(self, category: str | None = None) -> list[PatchRecord]:
        """Return a snapshot of registered patch records."""
        records = list(self._patches.values())
        if category is not None:
            records = [r for r in records if r.category == category]
        return records

    def is_applied(self, name: str) -> bool:
        """Return whether *name* is currently applied."""
        rec = self._patches.get(name)
        if rec is None:
            raise KeyError(name)
        return rec.applied

    def applied_patches(self) -> list[str]:
        """Return names of currently applied patches."""
        return [name for name, rec in self._patches.items() if rec.applied]

    def audit_records(self) -> list[AuditRecord]:
        """Return a list of all audit events recorded in this registry."""
        return list(self._audit)

    def reset(self) -> None:
        """Unapply all patches and clear the registry.  Intended for tests."""
        for name in list(self._patches):
            self.unapply(name)
        self._patches.clear()
        self._audit.clear()
        self._overrides.clear()


# Dynamic dataclass schemas (frozen / unfrozen round trips)
_SCHEMA_REGISTRY: dict[str, dict[bool, type]] = {}

#: Insertion-ordered eviction bound for :data:`_SCHEMA_REGISTRY`.
_SCHEMA_REGISTRY_LIMIT = 256


def _LOGS():
    """Deferred logger lookup: ``dynamic`` is imported before startup logging."""
    from ..startup.logger import LOGS

    return LOGS


def forget_schema(name: str) -> bool:
    """Drop a generated schema pair; ``True`` when one was removed."""
    return _SCHEMA_REGISTRY.pop(name, None) is not None


def make_schema(
    name: str,
    fields: list[Any],
    *,
    frozen: bool = False,
    bases: tuple[type, ...] = (),
    namespace: dict[str, Any] | None = None,
) -> type:
    """Create a dataclass schema at runtime.

    Thin wrapper around :func:`dataclasses.make_dataclass` that keeps a registry
    of frozen and mutable variants so :func:`freeze` and :func:`unfreeze` can
    find the counterpart class.

    Args:
        name: Class name.
        fields: Field specification accepted by ``make_dataclass``.
        frozen: Build a frozen (read-only) class.
        bases: Base classes.
        namespace: Extra class namespace entries.

    Returns:
        The newly created dataclass type.
    """

    ns = dict(namespace) if namespace else {}
    ns.setdefault("_schema_name", name)
    ns.setdefault("_schema_frozen", frozen)
    cls = make_dataclass(name, fields, bases=bases, namespace=ns, frozen=frozen)
    if len(_SCHEMA_REGISTRY) >= _SCHEMA_REGISTRY_LIMIT and name not in _SCHEMA_REGISTRY:
        # Bounded: the registry exists only so freeze/unfreeze can find a
        # counterpart class. A caller that generates schemas in a loop would
        # otherwise retain every class object it ever built.
        oldest = next(iter(_SCHEMA_REGISTRY))
        del _SCHEMA_REGISTRY[oldest]
        _LOGS().debug("dynamic schema registry full; evicted %r", oldest)
    _SCHEMA_REGISTRY.setdefault(name, {})[frozen] = cls
    counterpart = _SCHEMA_REGISTRY[name].get(not frozen)
    if counterpart is not None:
        cls._schema_counterpart = counterpart
        counterpart._schema_counterpart = cls
    return cls


def _type_name(obj: Any) -> str:
    """Return a concise type/class name for audit logs."""
    if isinstance(obj, type):
        return f"{obj.__module__}.{obj.__qualname__}"
    cls = type(obj)
    return f"{cls.__module__}.{cls.__qualname__}"


def _schema_fields_spec(cls: type) -> list[tuple[str, Any, Any]]:
    """Return a ``make_dataclass`` field spec from an existing dataclass."""
    spec: list[tuple[str, Any, Any]] = []
    for f in dataclasses.fields(cls):
        field_obj = dataclasses.field(
            default=(
                f.default
                if f.default is not dataclasses.MISSING
                else dataclasses.MISSING
            ),
            default_factory=(
                f.default_factory
                if f.default_factory is not dataclasses.MISSING
                else dataclasses.MISSING
            ),
            repr=f.repr,
            hash=f.hash,
            compare=f.compare,
            metadata=f.metadata,
        )
        spec.append((f.name, f.type, field_obj))
    return spec


def _counterpart_class(cls: type) -> type:
    """Return the frozen or mutable counterpart of a schema class."""
    if not dataclasses.is_dataclass(cls):
        raise TypeError(f"{cls!r} is not a dataclass")
    counterpart = getattr(cls, "_schema_counterpart", None)
    if counterpart is not None:
        return counterpart

    name = getattr(cls, "_schema_name", cls.__name__)
    frozen = cls.__dataclass_params__.frozen
    if name in _SCHEMA_REGISTRY and not frozen in _SCHEMA_REGISTRY[name]:
        return _SCHEMA_REGISTRY[name][not frozen]

    # Build the counterpart lazily and link the two classes.
    fields = _schema_fields_spec(cls)
    counterpart = make_schema(name, fields, frozen=not frozen)
    cls._schema_counterpart = counterpart
    counterpart._schema_counterpart = cls
    return counterpart


def _convert_to_mutability(value: Any, *, frozen: bool) -> Any:
    """Recursively convert dataclass instances to *frozen* or mutable form."""
    if dataclasses.is_dataclass(value):
        cls = type(value)
        if cls.__dataclass_params__.frozen == frozen:
            return value
        target_cls = _counterpart_class(cls)
        return target_cls(
            **{
                f.name: _convert_to_mutability(getattr(value, f.name), frozen=frozen)
                for f in dataclasses.fields(target_cls)
            }
        )
    if isinstance(value, dict):
        return {k: _convert_to_mutability(v, frozen=frozen) for k, v in value.items()}
    if isinstance(value, list):
        return [_convert_to_mutability(item, frozen=frozen) for item in value]
    return value


def freeze(instance: Any, *, deep: bool = True) -> Any:
    """Return a frozen copy of a mutable dataclass instance.

    If the instance is already frozen it is returned unchanged.  When *deep*
    is ``True``, nested mutable dataclass instances are frozen recursively.
    """
    if not dataclasses.is_dataclass(instance):
        raise TypeError(
            f"freeze() expected a dataclass instance, got {type(instance)!r}"
        )
    if instance.__dataclass_params__.frozen:
        return instance
    if not deep:
        frozen_cls = _counterpart_class(type(instance))
        return frozen_cls(**dataclasses.asdict(instance))
    return _convert_to_mutability(instance, frozen=True)


def unfreeze(instance: Any, *, deep: bool = True) -> Any:
    """Return a mutable copy of a frozen dataclass instance.

    If the instance is already mutable it is returned unchanged.  When *deep*
    is ``True``, nested frozen dataclass instances are unfrozen recursively.
    """
    if not dataclasses.is_dataclass(instance):
        raise TypeError(
            f"unfreeze() expected a dataclass instance, got {type(instance)!r}"
        )
    if not instance.__dataclass_params__.frozen:
        return instance
    if not deep:
        mutable_cls = _counterpart_class(type(instance))
        return mutable_cls(**dataclasses.asdict(instance))
    return _convert_to_mutability(instance, frozen=False)


def schema_to_dict(instance: Any) -> dict[str, Any]:
    """Serialize a dataclass instance to a plain dict."""
    if not dataclasses.is_dataclass(instance):
        raise TypeError(
            f"schema_to_dict() expected a dataclass instance, got {type(instance)!r}"
        )
    return dataclasses.asdict(instance)


def schema_from_dict(schema: type, data: dict[str, Any]) -> Any:
    """Deserialize a plain dict into a dataclass instance."""
    if not dataclasses.is_dataclass(schema):
        raise TypeError(f"schema_from_dict() expected a dataclass type, got {schema!r}")
    return _schema_from_dict(schema, data)


def _schema_from_dict(schema: type, data: dict[str, Any]) -> Any:
    """Internal recursive dict -> dataclass constructor."""
    kwargs: dict[str, Any] = {}
    for f in dataclasses.fields(schema):
        val = data.get(f.name)
        field_type = f.type
        if dataclasses.is_dataclass(val):
            # Already a dataclass instance; convert to the declared field type
            # if the mutability differs.
            if (
                dataclasses.is_dataclass(field_type)
                and val.__dataclass_params__.frozen
                != field_type.__dataclass_params__.frozen
            ):
                val = (
                    unfreeze(val)
                    if not field_type.__dataclass_params__.frozen
                    else freeze(val)
                )
        elif dataclasses.is_dataclass(field_type) and isinstance(val, dict):
            val = _schema_from_dict(field_type, val)
        elif isinstance(val, list):
            val = [_convert_list_item(field_type, item) for item in val]
        kwargs[f.name] = val
    return schema(**kwargs)


def _convert_list_item(field_type: Any, item: Any) -> Any:
    """Handle list items that are dicts mapping to a dataclass field type."""
    if isinstance(field_type, str):
        return item
    origin = getattr(field_type, "__origin__", None)
    if origin is list:
        args = getattr(field_type, "__args__", ())
        if args and dataclasses.is_dataclass(args[0]) and isinstance(item, dict):
            return _schema_from_dict(args[0], item)
    if dataclasses.is_dataclass(field_type) and isinstance(item, dict):
        return _schema_from_dict(field_type, item)
    return item


# Built-in extension contracts
register_contract(
    "eor",
    None,
    category="telegram",
    description=(
        "Edit-or-reply helper patched onto telethon.tl.custom.Message. "
        "Signature: eor(event, text=None, time=None, link_preview=False, "
        "edit_time=None, **args). Returns the sent/edited message. "
        "Implemented in pyUltroid.startup.patching."
    ),
    signature=("event", "text", "time", "link_preview", "edit_time"),
)

register_contract(
    "eod",
    None,
    category="telegram",
    description=(
        "Edit-or-delete helper that wraps eor and defaults time=8 so the "
        "message self-deletes. Signature: eod(event, text=None, **kwargs). "
        "Implemented in pyUltroid._misc._wrappers."
    ),
    signature=("event", "text"),
)

register_contract(
    "ikm",
    None,
    category="telegram",
    description=(
        "Inline keyboard maker. Accepts a list-of-lists button spec and "
        "returns a list-of-lists of Telethon Button objects for the buttons= "
        "argument of send/edit helpers. Implemented in custom.tg."
    ),
    signature=("data",),
)

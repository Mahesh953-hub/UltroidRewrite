"""Callback-data primitives  -  a dependency-free leaf shared by every layer."""

from __future__ import annotations

import re

SEP = ":"


def _validate_part(part):
    """Validate one callback-data segment; raise on anything that would
    silently corrupt the colon-joined token.

    The legacy ``_clean`` *dropped* ``None``/empty parts, which produced a
    token with a missing segment whose handler never matched  -  a button that
    silently times out. It also accepted a part containing ``SEP``, which
    splits into extra segments on the handler side and desynchronises the
    two halves of a cross-boundary token. Both are now hard errors.
    """
    if part is None or part == "":
        raise ValueError(
            "cb() segment must be non-empty; got None or empty string  -  "
            "refusing to build a callback token with a missing segment"
        )
    text = str(part)
    if SEP in text:
        raise ValueError(
            f"cb() segment {text!r} contains the separator {SEP!r}; "
            "callback data is colon-joined and a ':' inside a segment "
            "corrupts the token on the handler side"
        )
    return text


def _clean(parts) -> list:
    return [_validate_part(p) for p in parts]


def cb(*parts) -> bytes:
    """Build callback data: ``cb("assistant", "ofox", "cheetah")`` -> ``b"assistant:ofox:cheetah"``.

    Every part is validated (non-empty, no embedded ``SEP``) and stringified,
    so ints/ids are fine as-is. Raises ``ValueError`` on a bad segment instead
    of silently emitting a token no handler can match.
    """
    cleaned = _clean(parts)
    if not cleaned:
        raise ValueError(
            "cb() called with no segments; refusing to build an empty token"
        )
    return SEP.join(cleaned).encode()


def cb_str(*parts) -> str:
    return cb(*parts).decode()


def cb_pattern(*prefix_parts, capture: bool = True):
    prefix = re.escape(SEP.join(_clean(prefix_parts)).encode()) + re.escape(
        SEP.encode()
    )
    return re.compile(prefix + (b"(.*)" if capture else b"$"))


def cb_exact_pattern(*prefix_parts):
    return SEP.join(_clean(prefix_parts)).encode()

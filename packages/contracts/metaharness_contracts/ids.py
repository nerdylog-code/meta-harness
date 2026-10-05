"""Typed identifiers.

Contract rule (WP-003 decision 1): an id is an **opaque string with a type
prefix**. Nothing may parse meaning out of the opaque part — the prefix is the
only contract, and the generation algorithm (UUID, ULID, counter, hash…) is an
implementation detail that must never leak into an interface.

    >>> is_valid_id("agt_01J8Z", IdKind.AGENT)
    True
    >>> is_valid_id("mis_01J8Z", IdKind.AGENT)
    False
"""

from __future__ import annotations

import secrets
import time
from enum import Enum


class IdKind(str, Enum):
    AGENT = "agent"
    MISSION = "mission"
    TASK = "task"
    RUN = "run"
    SESSION = "session"
    ARTIFACT = "artifact"
    PLUGIN = "plugin"
    RUNTIME = "runtime"
    EVENT = "event"
    APPROVAL = "approval"


ID_PREFIXES: dict[IdKind, str] = {
    IdKind.AGENT: "agt",
    IdKind.MISSION: "mis",
    IdKind.TASK: "tsk",
    IdKind.RUN: "run",
    IdKind.SESSION: "ses",
    IdKind.ARTIFACT: "art",
    IdKind.PLUGIN: "plg",
    IdKind.RUNTIME: "rt",
    IdKind.EVENT: "evt",
    IdKind.APPROVAL: "apr",
}

PREFIXES: frozenset[str] = frozenset(ID_PREFIXES.values())


class InvalidId(ValueError):
    """Raised when a value is not a well-formed id of the expected kind."""


def prefix_for(kind: IdKind | str) -> str:
    kind = IdKind(kind) if not isinstance(kind, IdKind) else kind
    return ID_PREFIXES[kind]


def new_id(kind: IdKind | str, *, salt: str | None = None) -> str:
    """Generate an id.

    The body is time-prefixed (sortable) plus random, but callers must treat it
    as opaque: this function is the *only* place allowed to know how it is made.
    """
    body = salt or f"{int(time.time() * 1000):013d}{secrets.token_hex(6)}"
    return f"{prefix_for(kind)}_{body}"


def is_valid_id(value: object, kind: IdKind | str | None = None) -> bool:
    if not isinstance(value, str):
        return False
    prefix, separator, body = value.partition("_")
    if not separator or not body:
        return False
    if prefix not in PREFIXES:
        return False
    if kind is not None and prefix != prefix_for(kind):
        return False
    return True


def opaque_part(value: str) -> str:
    """The body of an id. Exposed for logs/diagnostics only — never for logic."""
    return value.partition("_")[2]


def validate_id(value: object, kind: IdKind | str, *, field: str = "id") -> str:
    if not is_valid_id(value, kind):
        expected = prefix_for(kind)
        raise InvalidId(f"{field} must be a {expected}_-prefixed id, got {value!r}")
    return str(value)

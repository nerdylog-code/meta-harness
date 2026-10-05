"""Wire-safe serialization helpers.

WP-003 decisions 11 and 12: persisted/API contracts are JSON-safe and
versionable, no Python-only type may leak on the wire, and unknown fields in
*external* envelopes are preserved rather than causing a crash.
"""

from __future__ import annotations

import datetime as _dt
import enum
import json
import math
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from pydantic import BaseModel

JSON_SCALARS = (str, int, float, bool, type(None))
REJECTED_TYPES = (bytes, bytearray, complex, Decimal, set, frozenset, Path, _dt.datetime, _dt.date)


class NotJsonSafe(TypeError):
    """A value cannot be represented in JSON without losing meaning."""


def json_safe(value: Any, *, path: str = "$") -> Any:
    """Convert to plain JSON types, refusing anything lossy.

    Floats must be finite: NaN/Infinity are not valid JSON and silently become
    null in many encoders. Refusing is better than shipping a number that
    changes meaning depending on who parses it.
    """
    if isinstance(value, BaseModel):
        return json_safe(value.model_dump(mode="json"), path=path)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): json_safe(item, path=f"{path}.{key}") for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item, path=f"{path}[{index}]") for index, item in enumerate(value)]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise NotJsonSafe(f"{path}: {value!r} is not a finite JSON number")
        return value
    if isinstance(value, REJECTED_TYPES):
        raise NotJsonSafe(f"{path}: {type(value).__name__} has no lossless JSON form")
    raise NotJsonSafe(f"{path}: unsupported type {type(value).__name__}")


def dumps(value: Any, *, sort_keys: bool = True) -> str:
    """Canonical JSON: sorted keys, no NaN, no Python reprs."""
    return json.dumps(json_safe(value), sort_keys=sort_keys, ensure_ascii=False, allow_nan=False)


def round_trip(model: BaseModel) -> BaseModel:
    """Serialize and re-parse a model, asserting the result is equivalent."""
    payload = dumps(model)
    return type(model).model_validate_json(payload)


def external_envelope(cls: type[BaseModel], payload: Mapping[str, Any]) -> BaseModel:
    """Parse an envelope that may carry fields we do not know about.

    Used at integration boundaries (a runtime that added metadata in a newer
    version): unknown keys are preserved, not fatal, and not interpreted.
    """
    return cls.model_validate(dict(payload))


def diff_keys(left: BaseModel, right: BaseModel) -> tuple[set[str], set[str]]:
    """Field names present on one side but not the other. Used by parity tests."""
    return set(left.model_fields), set(right.model_fields)


def sorted_strings(values: Iterable[str]) -> list[str]:
    return sorted(str(value) for value in values)

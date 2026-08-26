"""Credential / secret redaction.

Run on every event and every tool argument before persistence. Conservative
patterns only — we never redact too much; we accept some false negatives.
"""

from __future__ import annotations

import re
from typing import Any

# Conservative: api keys, bearer tokens, JWT, generic KEY/TOKEN assignments.
_PATTERNS = [
    (re.compile(r"(?i)(sk-[A-Za-z0-9_\-]{16,})"), "[REDACTED:openai-key]"),
    (re.compile(r"(?i)(sk-ant-[A-Za-z0-9_\-]{16,})"), "[REDACTED:anthropic-key]"),
    (re.compile(r"(?i)(ghp_[A-Za-z0-9]{20,})"), "[REDACTED:github-pat]"),
    (re.compile(r"(?i)(xai-[A-Za-z0-9]{20,})"), "[REDACTED:xai-key]"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"), "Bearer [REDACTED:bearer]"),
    (re.compile(r"(?i)(?:api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?([A-Za-z0-9._\-]{16,})"), r"\1=[REDACTED:secret]"),
    (re.compile(r"eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "[REDACTED:jwt]"),
]

_MAX_DEPTH = 6
_MAX_ITEMS = 32
_MAX_STRING = 8192


def _trunc(s: str) -> str:
    return s if len(s) <= _MAX_STRING else s[:_MAX_STRING] + "...[truncated]"


def redact_string(s: str) -> str:
    # Truncate first — regex on huge strings is the silent perf cliff.
    truncated = _trunc(s)
    out = truncated
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


def redact(value: Any, depth: int = 0) -> Any:
    if depth > _MAX_DEPTH:
        return "[depth-limit]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= _MAX_ITEMS:
                out["..."] = "truncated"
                break
            out[str(k)] = redact(v, depth + 1)
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for i, item in enumerate(value):
            if i >= _MAX_ITEMS:
                out.append("...")
                break
            out.append(redact(item, depth + 1))
        return out
    return str(value)
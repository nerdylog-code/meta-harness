"""Selective egress: the only connected path a sandbox may use (ADR-0020, S2B)."""

from __future__ import annotations

from .broker import (
    ALLOWED_PORT,
    Decision,
    EgressBroker,
    EgressRecord,
    EgressRefused,
    HostAllowlist,
    enforcement_level,
    forbidden_reason,
    resolve_hostname,
    sni_from_client_hello,
)

__all__ = [
    "ALLOWED_PORT",
    "Decision",
    "EgressBroker",
    "EgressRecord",
    "EgressRefused",
    "HostAllowlist",
    "enforcement_level",
    "forbidden_reason",
    "resolve_hostname",
    "sni_from_client_hello",
]

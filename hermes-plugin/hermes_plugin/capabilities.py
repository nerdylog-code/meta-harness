"""Internal capability registry + resolver.

Consumers request a capability id (e.g. ``validation.test``). Providers
register their fulfilment at bootstrap. The resolver picks the best
provider per consumer request.

This is intentionally separate from Hermes's host capability gate
(``plugin_capabilities.CAPABILITY_REGISTRY``) — that one is about host API
surface override permissions; this one is about *what the harness itself
can do at runtime*.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable

_CAPABILITIES: dict[str, list["Provider"]] = {}
_LOCK = threading.Lock()


@dataclass
class Provider:
    capability: str
    name: str                # human-friendly id, e.g. "local-test-runner"
    description: str = ""
    trust: str = "system"    # system | trusted-installed | user-authored | model-generated | experimental
    available: bool = True
    handler: Callable | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def register(capability: str, name: str, *, description: str = "",
             trust: str = "system", available: bool = True,
             handler: Callable | None = None,
             metadata: dict | None = None) -> Provider:
    """Register a provider for one capability. Idempotent on (cap, name)."""
    p = Provider(
        capability=capability, name=name, description=description,
        trust=trust, available=available, handler=handler,
        metadata=dict(metadata or {}),
    )
    with _LOCK:
        providers = _CAPABILITIES.setdefault(capability, [])
        for i, existing in enumerate(providers):
            if existing.name == name:
                providers[i] = p
                return p
        providers.append(p)
    return p


def unregister(capability: str, name: str) -> None:
    with _LOCK:
        providers = _CAPABILITIES.get(capability, [])
        _CAPABILITIES[capability] = [p for p in providers if p.name != name]


def resolve(capability: str) -> Provider | None:
    """Return the best available provider for a capability.

    Priority: trust (system > trusted-installed > user-authored > experimental)
    then declared availability. The resolver is hot-swappable; tests can call
    ``unregister`` and re-register to force a different binding.
    """
    with _LOCK:
        providers = list(_CAPABILITIES.get(capability, []))
    if not providers:
        return None
    order = {"system": 0, "trusted-installed": 1, "user-authored": 2,
             "model-generated": 3, "experimental": 4, "quarantined": 5}
    available = [p for p in providers if p.available]
    if not available:
        return None
    available.sort(key=lambda p: order.get(p.trust, 99))
    return available[0]


def list_capabilities() -> list[dict]:
    with _LOCK:
        out = []
        for cap, providers in _CAPABILITIES.items():
            out.append({
                "capability": cap,
                "providers": [
                    {"name": p.name, "trust": p.trust, "available": p.available,
                     "description": p.description, "metadata": p.metadata}
                    for p in providers
                ],
            })
    return out


def reset() -> None:
    """Test helper — clears all registrations."""
    with _LOCK:
        _CAPABILITIES.clear()
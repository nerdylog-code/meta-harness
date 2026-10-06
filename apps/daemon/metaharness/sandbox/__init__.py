"""Execution boundary (M3): the mechanism that can refuse an action, and the record of what it did.

`provider.py` holds the mechanisms (container, mount namespace, none). `environment.py` turns one of
them into what a session actually needs: the argv to spawn, the effective policy, and the checks
that justify the claim. See ADR-0019.
"""

from __future__ import annotations

from .environment import BUDGET_MODES, EnvironmentPlan, ExecutionEnvironment
from .provider import (
    Availability,
    ContainerSandboxProvider,
    LocalSandboxProvider,
    NamespaceSandboxProvider,
    SandboxError,
    SandboxPlan,
    SandboxProvider,
    SandboxSpec,
    describe_providers,
    make_staged_home,
    resolve_runtime_paths,
    select_provider,
)

__all__ = [
    "BUDGET_MODES",
    "Availability",
    "ContainerSandboxProvider",
    "EnvironmentPlan",
    "ExecutionEnvironment",
    "LocalSandboxProvider",
    "NamespaceSandboxProvider",
    "SandboxError",
    "SandboxPlan",
    "SandboxProvider",
    "SandboxSpec",
    "describe_providers",
    "make_staged_home",
    "resolve_runtime_paths",
    "select_provider",
]

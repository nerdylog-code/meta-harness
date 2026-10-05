"""Process supervision package — one interface, two OS implementations.

    from metaharness.process import supervisor
    sup = supervisor()

The factory is the only place that looks at the OS; callers see one contract
(BOOK §10.3).
"""

from __future__ import annotations

import os

from .base import (
    DEFAULT_GRACE_S,
    DEFAULT_WALL_TIMEOUT_S,
    KillReport,
    ProcessError,
    ProcessHandle,
    ProcessOutcome,
    ProcessSupervisor,
    StreamChunk,
)

__all__ = [
    "DEFAULT_GRACE_S",
    "DEFAULT_WALL_TIMEOUT_S",
    "KillReport",
    "ProcessError",
    "ProcessHandle",
    "ProcessOutcome",
    "ProcessSupervisor",
    "StreamChunk",
    "supervisor",
]


def supervisor(**kwargs) -> ProcessSupervisor:
    """Return the supervisor for the current OS."""
    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        from .windows import WindowsProcessSupervisor

        return WindowsProcessSupervisor(**kwargs)
    from .posix import PosixProcessSupervisor

    return PosixProcessSupervisor(**kwargs)

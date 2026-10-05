"""Liveness probes the boot reconciler asks about the *outside* world.

Keeping this behind a Protocol is what lets the reconciler be tested deterministically
and, more importantly, keeps the decision "is this process really alive?" out of the
store. The store records; the reconciler asks and then writes a new event.
"""

from __future__ import annotations

from typing import Iterable, Protocol, runtime_checkable


@runtime_checkable
class ProcessProbe(Protocol):
    """Answers one question: is this pid a live process right now?"""

    def alive(self, pid: int | None) -> bool: ...


class PidProbe:
    """The real probe: psutil, which is already a dependency of the supervisor."""

    def alive(self, pid: int | None) -> bool:
        if not pid:
            return False
        try:
            import psutil
        except ImportError:  # pragma: no cover - psutil is a declared dependency
            return False
        try:
            process = psutil.Process(int(pid))
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except Exception:
            return False


class StaticProbe:
    """A probe with a fixed answer set -- for tests and for dry runs.

    It exists in the package rather than in the tests because `python -m
    metaharness.reconcile --dry-run` is a legitimate operation: it answers "what would
    boot reconciliation mark?" without touching a process table.
    """

    def __init__(self, alive_pids: Iterable[int] = (), *, default: bool = False) -> None:
        self.alive_pids = {int(pid) for pid in alive_pids}
        self.default = default

    def alive(self, pid: int | None) -> bool:
        if pid is None:
            return False
        return int(pid) in self.alive_pids if not self.default else True


class AllDeadProbe(StaticProbe):
    """Every pid is gone: the state a machine is in after a reboot."""

    def __init__(self) -> None:
        super().__init__(())

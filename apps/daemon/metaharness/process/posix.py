"""POSIX (Linux/macOS) supervisor: new session + process-group signals."""

from __future__ import annotations

import os
import signal
from typing import Any, Sequence

from .base import ProcessHandle, ProcessSupervisor

_SIGNALS = {"int": signal.SIGINT, "term": signal.SIGTERM, "kill": signal.SIGKILL}


class PosixProcessSupervisor(ProcessSupervisor):
    platform = "posix"

    def _spawn_kwargs(self) -> dict[str, Any]:
        # start_new_session=True gives the child its own session and process
        # group, which is what makes a group signal (and therefore a reliable
        # tree kill) possible at all.
        return {"start_new_session": True}

    def _signal_group(self, handle: ProcessHandle, sig: str, known: Sequence[int] = ()) -> bool:
        number = _SIGNALS[sig]
        sent = False

        # 1) The whole group at once, while the session is still ours.
        try:
            os.killpg(os.getpgid(handle.pid), number)
            sent = True
        except (ProcessLookupError, PermissionError, OSError):
            pass

        # 2) Per-pid sweep over the *snapshot taken before signalling* plus
        #    anything still reachable now. This is what catches a descendant
        #    that ran setsid() (a group signal can never reach it) and anything
        #    reparented by the death of its parent in step 1. Children first:
        #    killing a parent before its child is how orphans are created.
        targets = sorted({*known, *self.descendants(handle.pid)}, reverse=True)
        for pid in targets:
            try:
                os.kill(pid, number)
                sent = True
            except (ProcessLookupError, PermissionError):
                continue

        if not sent:
            try:
                os.kill(handle.pid, number)
                sent = True
            except (ProcessLookupError, PermissionError):
                pass
        return sent

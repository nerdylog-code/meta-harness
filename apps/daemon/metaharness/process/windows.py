"""Windows supervisor: new process group + psutil tree discovery.

Windows has no POSIX process groups or ``killpg``. A bare ``process.kill()``
terminates only the direct child and leaves grandchildren running — which is
exactly the orphan bug the BOOK §79 gate exists to catch. The tree is therefore
discovered with psutil and killed deepest-first, and the verification in
``ProcessSupervisor.terminate/kill_tree`` re-walks it afterwards.
"""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Any, Sequence

import psutil

from .base import ProcessHandle, ProcessSupervisor


def _depth(process: psutil.Process) -> int:
    """Depth below the root, so descendants are killed before their parents."""
    depth = 0
    current = process
    for _ in range(64):
        try:
            parent = current.parent()
        except psutil.Error:
            break
        if parent is None:
            break
        depth += 1
        current = parent
    return -depth  # negative key => deepest first when sorted ascending


class WindowsProcessSupervisor(ProcessSupervisor):
    platform = "windows"

    def _spawn_kwargs(self) -> dict[str, Any]:
        # A new process group is what makes CTRL_BREAK_EVENT addressable and
        # keeps the child out of our console's group.
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}

    def _tree(self, pid: int, known: Sequence[int] = ()) -> list[psutil.Process]:
        """Descendants deepest-first, then the root itself.

        ``known`` is the pre-signal snapshot: on Windows a killed process
        vanishes immediately and its children are reparented, so anything the
        live walk found earlier must be included or it becomes an orphan that
        nobody is looking for.
        """
        found: dict[int, psutil.Process] = {}
        for candidate in known:
            try:
                found[candidate] = psutil.Process(candidate)
            except psutil.Error:
                continue
        try:
            parent = psutil.Process(pid)
        except psutil.Error:
            return list(found.values())
        for child in parent.children(recursive=True):
            try:
                found[child.pid] = child
            except psutil.Error:
                continue
        ordered = sorted(found.items(), key=lambda item: _depth(item[1])) if found else []
        processes = [process for _, process in ordered]
        if not any(process.pid == pid for process in processes):
            processes.append(parent)
        return processes

    def _signal_group(self, handle: ProcessHandle, sig: str, known: Sequence[int] = ()) -> bool:
        sent = False
        if sig == "int":
            # Graceful interrupt exists only for console groups created with
            # CREATE_NEW_PROCESS_GROUP; if it is unavailable we fall through to
            # terminate, and the caller's escalation still applies.
            try:
                os.kill(handle.pid, signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                return True
            except (AttributeError, OSError):
                pass
        for process in self._tree(handle.pid, known):
            try:
                if sig == "term":
                    process.terminate()
                else:
                    process.kill()
                sent = True
            except psutil.Error:
                continue
        return sent

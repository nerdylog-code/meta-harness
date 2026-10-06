#!/usr/bin/env python3
"""A1/A3 smoke for the built desktop shell: launch it, check the daemon came up, kill it, and
prove nothing survived.

This needs a display and a built binary, so it is a manual step, not a CI step:

    cd apps/desktop && uv run python smoke_shell.py

It launches the raw binary rather than the AppImage on purpose: an AppImage needs FUSE, and a
FUSE failure would tell us nothing about the shell. A7 (the bundles exist) is a separate check.

Evidence it produces:
  * the shell process starts and stays up;
  * the daemon answers /health on the shell's port -- started by the shell, or attached to if one
    was already listening (both are correct outcomes, and the shell says which);
  * the shell's log records the lifecycle;
  * after the window goes away, the daemon process is gone and the port is closed (A3).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BINARY = REPO / "apps" / "desktop" / "src-tauri" / "target" / "release" / "metaharness-desktop"
LOG = Path(os.environ.get("METAHARNESS_DATA_DIR") or Path.home() / ".local" / "share" / "MetaHarness") / "logs" / "desktop-shell.log"
PORT = int(os.environ.get("MH_PORT", "8765"))
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{(' -> ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)


def health(port: int) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=1.0) as response:
            data = json.loads(response.read())
        return data if isinstance(data, dict) and "status" in data else None
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None


def status_lines(offset: int = 0) -> list[dict]:
    """The shell writes every status line to its log as well as to stdout, so this script can
    follow a fallback port without reading the shell's pipe."""
    if not LOG.exists():
        return []
    found = []
    for line in LOG.read_text()[offset:].splitlines():
        if line.startswith("[status] "):
            try:
                found.append(json.loads(line[len("[status] ") :]))
            except json.JSONDecodeError:
                continue
    return found


def port_from_log(offset: int) -> int | None:
    for payload in reversed(status_lines(offset)):
        if payload.get("status") in {"started", "attached"} and payload.get("port"):
            return int(payload["port"])
    return None


def daemon_pids() -> list[int]:
    import psutil

    found = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(process.info.get("cmdline") or [])
        except Exception:
            continue
        if "-m metaharness" in cmdline:
            found.append(process.info["pid"])
    return found


def main() -> int:
    if not BINARY.is_file():
        print(f"no binary at {BINARY}; run `pnpm tauri build` in apps/desktop first")
        return 2

    pre_existing = health(PORT)
    pre_daemons = set(daemon_pids())
    print(f"binary: {BINARY.name} ({BINARY.stat().st_size // 1024} KiB)")
    print(f"port {PORT} before launch: {'served' if pre_existing else 'free'}")

    log_offset = LOG.stat().st_size if LOG.exists() else 0
    process = subprocess.Popen(
        [str(BINARY)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    print(f"launched shell pid={process.pid}")

    # The shell may pick a fallback port when the configured one is taken by something that is
    # not our daemon, so learn the real port from the shell's own log instead of assuming.
    actual_port = PORT
    started: dict | None = None
    deadline = time.time() + 45
    while time.time() < deadline:
        if process.poll() is not None:
            break
        actual_port = port_from_log(log_offset) or actual_port
        started = health(actual_port)
        if started:
            break
        time.sleep(0.5)

    check("the shell process stayed up", process.poll() is None, f"rc={process.returncode}")
    check("the daemon answers /health on the shell's port", bool(started), json.dumps(started or {})[:120] if started else "no answer in 45s")
    if started:
        check(
            "the daemon reports the canonical store",
            "store" in started and started["store"].get("schema_version", 0) >= 4,
            f"schema v{started['store'].get('schema_version')}",
        )

    spawned = set(daemon_pids()) - pre_daemons
    if pre_existing:
        check("an already-listening daemon was attached to, not duplicated", not spawned, f"new daemons: {sorted(spawned)}")
    else:
        check("the shell spawned exactly one daemon", len(spawned) == 1, f"new: {sorted(spawned)}")
        if actual_port != PORT:
            check("a taken port produced a clear fallback, not a dead end", True, f"configured {PORT}, using {actual_port}")

    if LOG.exists():
        tail = LOG.read_text()[log_offset:]
        print("  shell log tail:")
        for line in tail.strip().splitlines()[-6:]:
            print(f"    {line}")
        check("the shell's log records the lifecycle", "spawned:" in tail or "attached" in tail)

    print("closing the window (SIGTERM to the shell)…")
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)

    deadline = time.time() + 20
    while time.time() < deadline:
        if not health(actual_port) and not (set(daemon_pids()) - pre_daemons):
            break
        time.sleep(0.5)

    remaining = set(daemon_pids()) - pre_daemons
    check("no orphan daemon survived the shell (A3)", not remaining, f"survivors: {sorted(remaining)}")
    check("the port is free again", health(actual_port) is None)

    print()
    if FAILURES:
        print(f"FAILED: {FAILURES}")
        return 1
    print("A1/A3 smoke: the shell starts the daemon, serves the UI and leaves nothing behind")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""The desktop shell's only piece of logic, and it lives in Python on purpose.

WP-007 says two things that decide this file's shape: the Tauri shell must not accrete business
logic, and the daemon's lifecycle must go through WP-005's supervisor rather than growing a
second process implementation in Rust. So the Rust side is a window and nothing else: it spawns
this script, reads one JSON line from its stdout, and navigates the webview. Everything that can
be wrong -- is a daemon already listening, start one, wait until it is healthy, kill the tree when
the window closes -- is here, where the supervisor and the test suite already live.

Protocol: one JSON object per line on stdout, never anything else.

    {"status": "attached", ...}   a MetaHarness daemon was already listening; nothing was spawned
    {"status": "started",  ...}   this process spawned the daemon and it answered /health
    {"status": "failed",   ...}   the daemon did not come up; carries the reason and the log path
    {"status": "stopped",  ...}   the child tree was killed; orphan_check says whether it was clean

stdin is the shell's handle on this process: EOF means "the window closed", and that is the
signal to kill the tree. The daemon's own output is captured by the supervisor (``drain=True``),
so it can never interleave with this JSON channel and corrupt it.

The shell is optional by design (BOOK §4): this script is never needed by
``python scripts/dev.py``, and nothing here imports Tauri.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
DAEMON_SRC = REPO_ROOT / "apps" / "daemon"
if str(DAEMON_SRC) not in sys.path:
    sys.path.insert(0, str(DAEMON_SRC))

from metaharness.process import KillReport, ProcessHandle, supervisor as make_supervisor  # noqa: E402

DEFAULT_PORT = 8765
DEFAULT_HOST = "127.0.0.1"
DEFAULT_TIMEOUT_S = 30.0
PROBE_TIMEOUT_S = 0.8


def default_log_path() -> Path:
    """Where the shell's log lives. Same app identity the daemon uses for its data root."""
    from platformdirs import user_data_dir

    base = Path(os.environ.get("METAHARNESS_DATA_DIR") or user_data_dir("MetaHarness"))
    return base / "logs" / "desktop-shell.log"


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def health(port: int, host: str = DEFAULT_HOST, timeout: float = PROBE_TIMEOUT_S) -> dict[str, Any] | None:
    """The daemon's own answer, or None. Shape-checked, so a random server on the port is not
    mistaken for our daemon (A2 depends on this distinction being real)."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=timeout) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    if isinstance(data, dict) and "status" in data and "store" in data:
        return data
    return None


def port_is_occupied(port: int, host: str = DEFAULT_HOST) -> bool:
    """Is *anything* listening, ours or not?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((host, port)) == 0


def pick_free_port(host: str = DEFAULT_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def emit_status(log: Any, payload: dict[str, Any]) -> None:
    """The shell reads stdout; the log keeps the same record so a human (or a smoke test) can
    reconstruct what happened without the pipe."""
    line = json.dumps(payload, ensure_ascii=False)
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
    try:
        log.write("[status] " + line + "\n")
        log.flush()
    except Exception:  # pragma: no cover - log already closed
        pass


def child_env(args: argparse.Namespace) -> dict[str, str]:
    """The environment the daemon is given. Deliberately small and secret-free: the shell has
    no credential to hand over, and inventing one would be theatre (BOOK §65 lands with daemon
    auth, a later work package)."""
    env = dict(os.environ)
    env["METAHARNESS_HOST"] = args.host
    env["METAHARNESS_PORT"] = str(args.port)
    env["METAHARNESS_DESKTOP"] = "1"
    if args.data_dir:
        env["METAHARNESS_DATA_DIR"] = args.data_dir
    return env


def _stop_when_stdin_closes(stop: asyncio.Future[None], loop: asyncio.AbstractEventLoop) -> None:
    def waiter() -> None:
        try:
            sys.stdin.read()
        except Exception:  # pragma: no cover - closed handle
            pass
        loop.call_soon_threadsafe(lambda: None if stop.done() else stop.set_result(None))

    threading.Thread(target=waiter, name="desktop-stdin", daemon=True).start()


async def wait_for_exit() -> None:
    """Return when the shell closes our stdin (window closed) or signals us."""
    loop = asyncio.get_running_loop()
    stop: asyncio.Future[None] = loop.create_future()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: None if stop.done() else stop.set_result(None))
        except (NotImplementedError, RuntimeError, ValueError):  # pragma: no cover - Windows
            pass
    _stop_when_stdin_closes(stop, loop)
    await stop


async def shutdown(sup: Any, handle: ProcessHandle | None) -> KillReport | None:
    if handle is None:
        return None
    try:
        await sup.close_stdin(handle)
    except Exception:  # pragma: no cover - already gone
        pass
    return await sup.kill_tree(handle)


async def run(args: argparse.Namespace) -> int:
    existing = health(args.port, args.host)
    if args.probe_only:
        if existing:
            emit({"status": "attached", "port": args.port, "probe_only": True})
            return 0
        emit({"status": "free", "port": args.port, "probe_only": True})
        return 3

    if args.print_child_env:
        emit({"status": "child_env", "env": {k: v for k, v in child_env(args).items() if k.startswith("METAHARNESS_")}})
        return 0

    log_path = Path(args.log) if args.log else default_log_path()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")

    if existing:
        # A2: a daemon is already there. Report it and attach -- never spawn a second one on a
        # port that is already serving, and never hang waiting for a port we will not get.
        payload = {
            "status": "attached",
            "port": args.port,
            "port_source": "configured",
            "url": f"http://{args.host}:{args.port}",
            "detail": "a MetaHarness daemon is already listening; this window attached to it",
            "schema_version": (existing.get("store") or {}).get("schema_version"),
        }
        log.write(f"attached to an existing daemon on {args.port}\n")
        emit_status(log, payload)
        await wait_for_exit()
        emit_status(log, {"status": "stopped", "spawned": False, "orphan_check": True, "survivors": []})
        log.close()
        return 0

    # WP-007 constraint 5: something else on the configured port must not be a dead end. If the
    # listener is not our daemon, start on a fresh port and say so -- clearly, in the status line
    # the shell renders and in the log.
    port_source = "configured"
    if port_is_occupied(args.port, args.host):
        fallback = pick_free_port(args.host)
        log.write(
            f"port {args.port} is held by a process that is not this daemon; using {fallback} instead\n"
        )
        args.port = fallback
        port_source = "fallback"

    log.write(f"\n=== shell launch {time.strftime('%Y-%m-%dT%H:%M:%S')} port={args.port} source={port_source} ===\n")
    log.flush()

    sup = make_supervisor()
    argv = [args.python, "-m", "metaharness"]
    handle = await sup.spawn(argv, cwd=str(REPO_ROOT), env=child_env(args), capture=True, drain=True)
    log.write(f"spawned: {' '.join(argv)} pid={handle.pid} cwd={REPO_ROOT}\n")
    log.flush()

    def daemon_output() -> str:
        """Whatever the daemon said before it died. Without this the log would record a code and
        no reason, which is exactly the blank-window failure A4 forbids."""
        parts = []
        for which in ("stderr", "stdout"):
            try:
                captured = sup.capture(handle, which).strip()
            except Exception:  # pragma: no cover - stream already gone
                captured = ""
            if captured:
                log.write(f"--- daemon {which} ---\n{captured}\n")
                parts.append(captured)
        return "\n".join(parts)

    deadline = time.monotonic() + args.timeout
    healthy: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        process = handle.process
        if process is not None and process.returncode is not None:
            detail = daemon_output()
            tail = detail.strip().splitlines()[-1][:240] if detail.strip() else ""
            log.close()
            emit_status(
                log,
                {
                    "status": "failed",
                    "port": args.port,
                    "port_source": port_source,
                    "pid": handle.pid,
                    "reason": f"the daemon exited with code {process.returncode} before answering /health"
                    + (f" -- {tail}" if tail else ""),
                    "log": str(log_path),
                },
            )
            return 1
        healthy = health(args.port, args.host)
        if healthy:
            break
        await asyncio.sleep(0.25)

    if not healthy:
        log.write(f"no /health within {args.timeout}s\n")
        daemon_output()
        report = await shutdown(sup, handle)
        log.write(f"killed tree: survivors={report.survivors if report else '?'}\n")
        log.close()
        emit_status(
            log,
            {
                "status": "failed",
                "port": args.port,
                "port_source": port_source,
                "pid": handle.pid,
                "reason": f"the daemon did not answer /health within {args.timeout}s",
                "log": str(log_path),
                "orphan_check": bool(report.orphan_check) if report else None,
            },
        )
        return 1

    log.write("healthy\n")
    log.flush()
    emit_status(
        log,
        {
            "status": "started",
            "port": args.port,
            "port_source": port_source,
            "url": f"http://{args.host}:{args.port}",
            "pid": handle.pid,
            "log": str(log_path),
            "schema_version": (healthy.get("store") or {}).get("schema_version"),
        },
    )

    await wait_for_exit()
    report = await shutdown(sup, handle)
    log.write(f"stopped: orphan_check={report.orphan_check if report else None}\n")
    emit_status(
        log,
        {
            "status": "stopped",
            "spawned": True,
            "pid": handle.pid,
            "orphan_check": bool(report.orphan_check) if report else None,
            "survivors": list(report.survivors) if report else [],
        },
    )
    log.close()
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Meta-Harness desktop shell host (WP-007)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("METAHARNESS_PORT", DEFAULT_PORT)))
    parser.add_argument("--host", default=os.environ.get("METAHARNESS_HOST", DEFAULT_HOST))
    parser.add_argument("--python", default=sys.executable, help="interpreter that runs the daemon")
    parser.add_argument("--data-dir", default=os.environ.get("METAHARNESS_DATA_DIR"))
    parser.add_argument("--log", default=None)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_S)
    parser.add_argument("--probe-only", action="store_true", help="report whether the port is served, then exit")
    parser.add_argument("--print-child-env", action="store_true", help="print the env the daemon would get")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        emit({"status": "failed", "reason": f"refusing to bind {args.host}: the desktop shell is loopback-only"})
        return 2
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

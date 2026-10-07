#!/usr/bin/env python3
"""The desktop shell's only piece of logic, and it lives in Python on purpose.

WP-007 says two things that decide this file's shape: the Tauri shell must not accrete business
logic, and the daemon's lifecycle must go through WP-005's supervisor rather than growing a
second process implementation in Rust. So the Rust side is a window and nothing else: it spawns
this script, reads one JSON line from its stdout, and navigates the webview. Everything that can
be wrong -- is a daemon already listening, start one, wait until it is healthy, kill the tree when
the window closes -- is here, where the supervisor and the test suite already live.

Protocol: one JSON object per line on stdout, never anything else.

    {"status": "attached", ...}       probe-only confirmation that a daemon is listening
    {"status": "attach_refused", ...} normal launch refused an unsafe authenticated attach
    {"status": "started", ...}         this process spawned the daemon and checked for fresh bootstrap
    {"status": "failed", ...}          the daemon did not come up; carries the reason and log path
    {"status": "stopped", ...}         the child tree was killed; orphan_check says whether clean
    {"status": "free", ...}            probe-only confirmation that the port is free
    {"status": "child_env", ...}       diagnostic output of the child environment

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
import re
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
BOOTSTRAP_URL_RE = re.compile(r"(https?://[^\s\"']*/auth/bootstrap\?capability=)[^&\s\"']+", re.IGNORECASE)
_BOOTSTRAP_CAPABILITY: str | None = None


def redact(value: Any) -> Any:
    """Recursively redact bootstrap capabilities before anything reaches stdout or disk."""
    if isinstance(value, str):
        clean = BOOTSTRAP_URL_RE.sub(r"\1REDACTED", value)
        if _BOOTSTRAP_CAPABILITY:
            clean = clean.replace(_BOOTSTRAP_CAPABILITY, "REDACTED")
        return clean
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def default_log_path() -> Path:
    """Where the shell's log lives. Same app identity the daemon uses for its data root."""
    from platformdirs import user_data_dir

    base = Path(os.environ.get("METAHARNESS_DATA_DIR") or user_data_dir("MetaHarness"))
    return base / "logs" / "desktop-shell.log"


def emit(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(redact(payload), ensure_ascii=False) + "\n")
    sys.stdout.flush()


def health(port: int, host: str = DEFAULT_HOST, timeout: float = PROBE_TIMEOUT_S) -> dict[str, Any] | None:
    """Return the minimal, identity-checked public health response, or None."""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=timeout) as response:
            data = json.loads(response.read())
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    if (
        isinstance(data, dict)
        and data.get("status") == "ok"
        and data.get("service") == "meta-harness"
        and isinstance(data.get("version"), str)
        and bool(data["version"])
        and data.get("auth_required") is True
    ):
        return data
    return None


def bootstrap_file(args: argparse.Namespace) -> Path:
    """Resolve the daemon's private bootstrap file without reading any credential elsewhere."""
    from metaharness.paths import data_root

    root = data_root(args.data_dir).resolve()
    return root / "bootstrap.url"


def read_bootstrap_url(path: Path, host: str, port: int) -> str | None:
    """Read a fresh bootstrap URL once; never include its secret in host output or logs."""
    global _BOOTSTRAP_CAPABILITY
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    expected_prefix = f"http://{host}:{port}/auth/bootstrap?capability="
    capability = value[len(expected_prefix):] if value.startswith(expected_prefix) else ""
    if not capability or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in capability):
        return None
    _BOOTSTRAP_CAPABILITY = capability
    return value


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
    line = json.dumps(redact(payload), ensure_ascii=False)
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
        # A running daemon's one-use bootstrap belongs to its launcher; this host cannot
        # safely pair with it, even if its public liveness endpoint is healthy.
        reason = (
            "A MetaHarness daemon is already running and requires authentication. "
            "This host will not attach without a safe pairing mechanism; stop that daemon "
            "and relaunch Meta-Harness from this window."
        )
        emit_status(log, {
            "status": "attach_refused",
            "port": args.port,
            "port_source": "configured",
            "reason": reason,
            "log": str(log_path),
        })
        log.close()
        return 1

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
                captured = redact(captured)
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

    bootstrap_path = bootstrap_file(args)
    bootstrap_url = read_bootstrap_url(bootstrap_path, args.host, args.port)
    if bootstrap_url is None:
        log.write("bootstrap unavailable: daemon has no fresh bootstrap file\n")
    else:
        log.write("fresh bootstrap available for the desktop WebView (capability redacted)\n")
    log.flush()
    emit_status(
        log,
        {
            "status": "started",
            "port": args.port,
            "port_source": port_source,
            "url": f"http://{args.host}:{args.port}",
            "bootstrap_file": str(bootstrap_path) if bootstrap_url is not None else None,
            "bootstrap_available": bootstrap_url is not None,
            "pid": handle.pid,
            "log": str(log_path),
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

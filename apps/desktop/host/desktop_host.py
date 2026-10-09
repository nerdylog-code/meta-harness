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
import secrets
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
BOOTSTRAP_CAPABILITY_RE = re.compile(r"(\"capability\"\s*:\s*\")[A-Za-z0-9_-]+(\")", re.IGNORECASE)


#: Keys whose value is a credential by construction. Deliberately a tiny explicit set: redacting every
#: field whose name merely contains "token" or "secret" would hide legitimate diagnostic metadata and
#: make auditing harder, which is the opposite of what this is for.
SENSITIVE_KEYS = frozenset({"capability"})

#: Live secrets, in memory only. The key-aware rule cannot help when the capability appears inside an
#: unrelated string -- an error message, a captured daemon log line -- so the host also replaces the
#: exact live value wherever ordinary output would carry it. Never written, never printed, never
#: shared with the private pipe's consumer. There is one bootstrap capability today; this is not a vault.
_LIVE_SECRETS: set[str] = set()
_LIVE_SECRETS_LOCK = threading.Lock()

#: Below this length a value is not a capability, and replacing it could damage ordinary text.
MIN_REGISTERED_SECRET_CHARS = 16


def register_secret(secret: str) -> None:
    """Remember a live credential so that ordinary output cannot emit it. Memory only."""
    if not isinstance(secret, str) or len(secret) < MIN_REGISTERED_SECRET_CHARS:
        return
    with _LIVE_SECRETS_LOCK:
        _LIVE_SECRETS.add(secret)


def unregister_secret(secret: str) -> None:
    with _LIVE_SECRETS_LOCK:
        _LIVE_SECRETS.discard(secret)


def clear_secrets() -> None:
    with _LIVE_SECRETS_LOCK:
        _LIVE_SECRETS.clear()


def _redact_string(value: str) -> str:
    value = BOOTSTRAP_URL_RE.sub(r"\1REDACTED", value)
    value = BOOTSTRAP_CAPABILITY_RE.sub(r"\1REDACTED\2", value)
    with _LIVE_SECRETS_LOCK:
        live = tuple(_LIVE_SECRETS)
    for secret in live:
        value = value.replace(secret, "REDACTED")
    return value


def redact(value: Any) -> Any:
    """Redact credentials before anything reaches ordinary stdout or a log.

    The key matters as much as the value. Recursing into ``{"capability": SECRET}`` turns the
    credential into a bare string that no pattern can recognise, so a sensitive key is replaced outright
    instead of being handed down to the string rule. Everything else recurses, and the live-secret pass
    catches the capability when it turns up inside unrelated text.
    """
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, dict):
        out: dict[Any, Any] = {}
        for key, item in value.items():
            if isinstance(key, str) and key.strip().lower() in SENSITIVE_KEYS:
                out[key] = "REDACTED"
            else:
                out[key] = redact(item)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact(item) for item in value)
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


def port_is_occupied(port: int, host: str = DEFAULT_HOST) -> bool:
    """Is *anything* listening, ours or not?"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.4)
        return probe.connect_ex((host, port)) == 0


def pick_free_port(host: str = DEFAULT_HOST) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def emit_private(payload: dict[str, Any]) -> None:
    """Write a private parent-pipe message.

    This is the ONE intentional exception to redaction, and it must NOT route through ``redact()``: the
    bootstrap URL is exactly what the shell needs, and redacting it here would break the handover this
    whole channel exists for. It goes to the parent pipe and nowhere else -- not stdout, not the log,
    not a file. If someone later "fixes" this by redacting the payload, they are removing the only path
    the credential is allowed to travel.
    """
    sys.stderr.write("\x1e" + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stderr.flush()


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
    """The daemon's launch environment; the one-use credential is sent separately over stdin."""
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
    report = await sup.kill_tree(handle)
    # The credential is no longer live once the daemon is gone, so the registry is emptied here rather
    # than left for the process to die with.
    clear_secrets()
    return report


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
    env = child_env(args)
    env["METAHARNESS_READ_BOOTSTRAP_STDIN"] = "1"
    handle = await sup.spawn(argv, cwd=str(REPO_ROOT), env=env, capture=True, drain=True)
    capability = secrets.token_urlsafe(32)
    # Registered before anything derived from it is built or logged, and kept for the life of the
    # process: ordinary diagnostic paths may run much later, and the secret must stay unloggable until
    # shutdown clears it.
    register_secret(capability)
    bootstrap_record = json.dumps({"type": "bootstrap", "capability": capability}, separators=(",", ":")).encode("utf-8") + b"\n"
    await sup.write_stdin(handle, bootstrap_record)
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

    bootstrap_url = f"http://{args.host}:{args.port}/auth/bootstrap?capability={capability}"
    log.write("desktop authentication capability handed over privately\n")
    log.flush()
    emit_status(
        log,
        {
            "status": "started",
            "port": args.port,
            "port_source": port_source,
            "url": f"http://{args.host}:{args.port}",
            "bootstrap_available": True,
            "pid": handle.pid,
            "log": str(log_path),
        },
    )
    emit_private({"type": "bootstrap", "url": bootstrap_url})

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

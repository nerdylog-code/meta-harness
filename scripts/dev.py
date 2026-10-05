#!/usr/bin/env python3
"""Canonical developer entry point (BOOK §67 / Appendix C).

    python scripts/dev.py [--host 127.0.0.1] [--port 8765] [--no-browser]

Works identically from PowerShell and a POSIX shell: no Bash, no shell=True,
no `&&` chains. Never hardcodes a path.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_DIR = REPO_ROOT / "apps" / "daemon"
if str(DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(DAEMON_DIR))

REQUIRED = ("fastapi", "uvicorn", "pydantic", "platformdirs")
DEFAULT_PORT = 8765


def missing_dependencies() -> list[str]:
    missing = []
    for name in REQUIRED:
        try:
            __import__(name)
        except Exception:
            missing.append(name)
    return missing


def port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def pick_port(host: str, preferred: int, tries: int = 12) -> int:
    """Return a free port. ``preferred == 0`` means "any ephemeral port"."""
    if preferred == 0:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, 0))
            return int(probe.getsockname()[1])
    for offset in range(tries):
        candidate = preferred + offset
        if port_available(host, candidate):
            return candidate
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def open_when_ready(url: str, host: str, port: int, timeout_s: float = 20.0) -> None:
    """Open the browser only once the socket actually answers."""

    def worker() -> None:
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if not port_available(host, port):  # something is listening
                try:
                    webbrowser.open(url)
                except Exception:
                    pass
                return
            time.sleep(0.25)

    threading.Thread(target=worker, daemon=True).start()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Meta-Harness daemon (development).")
    parser.add_argument("--host", default=os.environ.get("METAHARNESS_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("METAHARNESS_PORT", DEFAULT_PORT)))
    parser.add_argument("--data-dir", default=os.environ.get("METAHARNESS_DATA_DIR") or None)
    parser.add_argument("--no-browser", action="store_true", help="do not open a browser")
    parser.add_argument("--no-web", action="store_true", help="serve the API only, ignore any web bundle")
    parser.add_argument("--reload", action="store_true", help="uvicorn autoreload (development)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    missing = missing_dependencies()
    if missing:
        print(f"[dev] missing dependencies: {', '.join(missing)}", file=sys.stderr)
        print("[dev] run: uv sync   (or: python -m pip install -e .)", file=sys.stderr)
        return 2

    from metaharness.app import Settings, create_app  # noqa: PLC0415 - after the dep check
    import uvicorn  # noqa: PLC0415

    port = pick_port(args.host, args.port)
    settings = Settings(
        host=args.host,
        port=port,
        data_dir=args.data_dir,
        serve_web=not args.no_web,
    )
    app = create_app(settings)
    url = f"http://{args.host}:{port}"

    print(f"[dev] meta-harness v2 skeleton")
    print(f"[dev] data root : {settings.resolved_data_dir()}")
    print(f"[dev] web bundle: {settings.resolved_web_root() or '(none — API only)'}")
    print(f"[dev] bound     : {url}")
    print(f"[dev] health    : {url}/health")
    print(f"[dev] events ws : ws://{args.host}:{port}/v1/events/ws")

    if not args.no_browser:
        open_when_ready(url, args.host, port)

    if args.reload:
        uvicorn.run(
            "metaharness.app:create_app",
            factory=True,
            host=args.host,
            port=port,
            reload=True,
            reload_dirs=[str(DAEMON_DIR)],
        )
    else:
        uvicorn.run(app, host=args.host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

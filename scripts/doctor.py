#!/usr/bin/env python3
"""Diagnose the local installation (BOOK Appendix C).

    python scripts/doctor.py

Exit codes: 0 = all checks pass, 2 = warnings only, 1 = a check failed.
Prints the resolved data root so "where does this thing write?" is never a
guess — that ambiguity is what produced v1's D1 defect.
"""

from __future__ import annotations

import importlib
import os
import shutil
import socket
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DAEMON_DIR = REPO_ROOT / "apps" / "daemon"
if str(DAEMON_DIR) not in sys.path:
    sys.path.insert(0, str(DAEMON_DIR))

CHECKS: list[tuple[str, str]] = []
REQUIRED_MODULES = ("fastapi", "uvicorn", "pydantic", "platformdirs", "httpx")
OPTIONAL_MODULES = ("websockets",)
WEB_BUNDLE_CANDIDATES = ("apps/web/dist", "apps/web/build")


def record(name: str, ok: bool, *, warn_only: bool = False) -> None:
    CHECKS.append((("WARN" if warn_only else "OK") if ok else ("WARN" if warn_only else "FAIL"), name))


def main() -> int:
    print("Meta-Harness v2 — doctor")
    print(f"  repo root : {REPO_ROOT}")
    print(f"  python    : {sys.version.split()[0]} ({sys.executable})")
    print()

    # --- interpreter ---------------------------------------------------------
    py_ok = sys.version_info >= (3, 12)
    print(f"[{'OK' if py_ok else 'FAIL'}] Python >= 3.12 ({sys.version.split()[0]})")
    CHECKS.append(("OK" if py_ok else "FAIL", "python version"))

    # --- dependencies --------------------------------------------------------
    for module in REQUIRED_MODULES:
        ok = _import_ok(module)
        print(f"[{'OK' if ok else 'FAIL'}] dependency: {module}")
        CHECKS.append(("OK" if ok else "FAIL", f"dependency {module}"))
    for module in OPTIONAL_MODULES:
        ok = _import_ok(module)
        print(f"[{'OK' if ok else 'WARN'}] dependency (optional): {module}")
        CHECKS.append(("OK" if ok else "WARN", f"optional dependency {module}"))

    # --- layout --------------------------------------------------------------
    try:
        from metaharness import paths  # noqa: PLC0415

        root = paths.ensure_layout()
        writable = os.access(root, os.W_OK)
        print(f"[{'OK' if writable else 'FAIL'}] data root writable: {root}")
        CHECKS.append(("OK" if writable else "FAIL", "data root writable"))
        missing = [name for name in paths.SUBDIRS if not (root / name).is_dir()]
        print(f"[{'OK' if not missing else 'FAIL'}] layout complete ({', '.join(paths.SUBDIRS)})")
        CHECKS.append(("OK" if not missing else "FAIL", "layout complete"))
        print(f"       source checkout detected: {paths.find_repo_root() or 'no (installed mode)'}")
    except Exception as exc:  # pragma: no cover - surfaced, not swallowed
        print(f"[FAIL] layout check raised: {exc}")
        CHECKS.append(("FAIL", "layout check"))

    # --- port ----------------------------------------------------------------
    host = os.environ.get("METAHARNESS_HOST", "127.0.0.1")
    port = int(os.environ.get("METAHARNESS_PORT", 8765))
    free = _port_free(host, port)
    print(f"[{'OK' if free else 'WARN'}] port {host}:{port} {'free' if free else 'in use (dev.py will pick another)'}")
    CHECKS.append(("OK" if free else "WARN", "port availability"))

    # --- toolchain -----------------------------------------------------------
    for tool, warn in (("git", False), ("uv", True), ("node", True), ("pnpm", True)):
        found = shutil.which(tool)
        level = "OK" if found else ("WARN" if warn else "FAIL")
        print(f"[{level}] toolchain: {tool}{'' if found else ' (missing)'}")
        CHECKS.append((level, f"toolchain {tool}"))

    # --- web bundle ----------------------------------------------------------
    bundle = next((REPO_ROOT / rel for rel in WEB_BUNDLE_CANDIDATES if (REPO_ROOT / rel / "index.html").is_file()), None)
    print(f"[{'OK' if bundle else 'WARN'}] web bundle: {bundle or 'not built (API-only mode)'}")
    CHECKS.append(("OK" if bundle else "WARN", "web bundle"))

    # --- v1 regression asset -------------------------------------------------
    v1_tests = (REPO_ROOT / "tests" / "run_all.py").is_file()
    print(f"[{'OK' if v1_tests else 'FAIL'}] v1 regression suite present (tests/run_all.py)")
    CHECKS.append(("OK" if v1_tests else "FAIL", "v1 suite present"))

    fails = sum(1 for level, _ in CHECKS if level == "FAIL")
    warns = sum(1 for level, _ in CHECKS if level == "WARN")
    print()
    print(f"result: {'FAIL' if fails else ('WARN' if warns else 'PASS')} ({fails} fail, {warns} warn)")
    return 1 if fails else (2 if warns else 0)


def _import_ok(module: str) -> bool:
    try:
        importlib.import_module(module)
        return True
    except Exception:
        return False


def _port_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


if __name__ == "__main__":
    raise SystemExit(main())

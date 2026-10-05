#!/usr/bin/env python3
"""Diagnose the local installation (BOOK Appendix C).

    python scripts/doctor.py

Exit codes: 0 = all checks pass, 2 = warnings only, 1 = a check failed.
Prints the resolved data root so "where does this thing write?" is never a
guess — that ambiguity is what produced v1's D1 defect.
"""

from __future__ import annotations

import argparse
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Diagnose the local Meta-Harness installation.")
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="always exit 0 and only print the verdict (for environments that are "
        "expected to be partial, such as a CI runner without pnpm or a built web bundle)",
    )
    args = parser.parse_args(argv)

    print("Meta-Harness v2 - doctor")
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

    # --- canonical store (WP-004) --------------------------------------------
    # Prints the resolved data root and the schema version, which is what the WP-004
    # acceptance command asks for. Opening a store that does not exist yet would create
    # it, so a diagnostic must not: an absent store is reported as "not created yet".
    try:
        if str(DAEMON_DIR) not in sys.path:
            sys.path.insert(0, str(DAEMON_DIR))
        from metaharness.store import Store, default_db_path
        from metaharness.store.migrations import MIGRATIONS_DIR, discover

        db_path = default_db_path()
        expected = len(discover(MIGRATIONS_DIR))
        if db_path.is_file():
            store = Store(db_path, emit_open_event=False)
            try:
                report = store.verify()
                version_ok = store.schema_version == expected
                level = "OK" if (report.ok and version_ok) else "FAIL"
                print(
                    f"[{level}] store: schema v{store.schema_version}/{expected}, "
                    f"{report.events} events, journal {report.journal_mode}"
                )
                print(f"       store path: {db_path}")
                for problem in report.problems[:5]:
                    print(f"       problem: {problem}")
                CHECKS.append((level, "canonical store"))
            finally:
                store.close()
        else:
            print(f"[OK] store: not created yet; {expected} migration(s) ready")
            print(f"       store path (created on first run): {db_path}")
            CHECKS.append(("OK", "canonical store"))
    except Exception as exc:
        print(f"[WARN] canonical store check unavailable: {exc}")
        CHECKS.append(("WARN", "canonical store"))

    # --- v1 regression asset -------------------------------------------------
    v1_tests = (REPO_ROOT / "tests" / "run_all.py").is_file()
    print(f"[{'OK' if v1_tests else 'FAIL'}] v1 regression suite present (tests/run_all.py)")
    CHECKS.append(("OK" if v1_tests else "FAIL", "v1 suite present"))

    fails = sum(1 for level, _ in CHECKS if level == "FAIL")
    warns = sum(1 for level, _ in CHECKS if level == "WARN")
    verdict = "FAIL" if fails else ("WARN" if warns else "PASS")
    print()
    print(f"result: {verdict} ({fails} fail, {warns} warn)")
    if args.report_only:
        print("(report-only: exit code forced to 0)")
        return 0
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

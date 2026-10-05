#!/usr/bin/env python3
"""Run every test suite (BOOK Appendix C).

    python scripts/test.py                 # v1 regression + v2 unit + v2 integration
    python scripts/test.py --suite v1
    python scripts/test.py --suite unit
    python scripts/test.py --list

Uses stdlib ``unittest`` discovery, so the v2 suites need no extra dependency.
Exit code 0 only when every selected suite passes.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS = REPO_ROOT / "tests"

SUITES: dict[str, list[str]] = {
    "v1": ["run_all.py", "tests/run_all.py"],  # v1's own runner (regression asset)
    "unit": ["tests/unit", "-"],
    "integration": ["tests/integration", "-"],
    "contracts": ["tests/contracts", "-"],
}


def run_v1() -> int:
    script = TESTS / "run_all.py"
    if not script.is_file():
        print("[test] v1 suite missing (tests/run_all.py)")
        return 1
    print("[test] v1 regression suite -> tests/run_all.py")
    return subprocess.run([sys.executable, str(script)], cwd=str(REPO_ROOT)).returncode


def run_discovery(path: Path) -> int:
    if not path.is_dir():
        print(f"[test] skipping {path.name}: directory not present")
        return 0
    print(f"[test] discovery -> {path.relative_to(REPO_ROOT)}")
    cmd = [
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        str(path),
        "-t",
        str(path),
        "-p",
        "test_*.py",
        "-v",
    ]
    return subprocess.run(cmd, cwd=str(REPO_ROOT)).returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Meta-Harness test suites.")
    parser.add_argument("--suite", action="append", choices=sorted(SUITES), default=None)
    parser.add_argument("--list", action="store_true", help="list suites and exit")
    args = parser.parse_args(argv)

    if args.list:
        for name in sorted(SUITES):
            print(name)
        return 0

    selected = args.suite or ["v1", "unit", "contracts", "integration"]
    results: dict[str, int] = {}
    for name in selected:
        if name == "v1":
            results[name] = run_v1()
        else:
            results[name] = run_discovery(TESTS / name)

    print()
    for name in selected:
        print(f"[test] {name:<12} {'PASS' if results[name] == 0 else 'FAIL'} (exit {results[name]})")
    failed = [name for name, code in results.items() if code != 0]
    if failed:
        print(f"[test] failing suites: {', '.join(failed)}")
        return 1
    print("[test] all selected suites passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

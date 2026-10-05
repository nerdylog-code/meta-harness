#!/usr/bin/env python3
"""Build distributable artifacts (BOOK Appendix C, phase 27 preview).

    python scripts/package.py            # sdist + wheel of the daemon
    python scripts/package.py --web      # also build apps/web when a toolchain exists

Nothing is published, uploaded or released by this script — it only writes into
``dist/`` locally. Publishing is an explicit, separately authorised action.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIST = REPO_ROOT / "dist"


def run(cmd: list[str], cwd: Path) -> int:
    print(f"[package] {' '.join(cmd)}  (cwd={cwd})")
    return subprocess.run(cmd, cwd=str(cwd)).returncode


def build_python() -> int:
    uv = shutil.which("uv")
    if uv:
        return run([uv, "build", "--out-dir", str(DIST)], REPO_ROOT)
    print("[package] uv not found; falling back to python -m build")
    return run([sys.executable, "-m", "build", "--outdir", str(DIST)], REPO_ROOT)


def build_web() -> int:
    web = REPO_ROOT / "apps" / "web"
    if not (web / "package.json").is_file():
        print("[package] no apps/web/package.json — skipping web build")
        return 0
    pnpm = shutil.which("pnpm")
    npm = shutil.which("npm")
    if pnpm:
        code = run([pnpm, "install", "--frozen-lockfile"], web)
        return code or run([pnpm, "build"], web)
    if npm:
        print("[package] pnpm missing; using npm (the lockfile is pnpm's — install may differ)")
        code = run([npm, "install"], web)
        return code or run([npm, "run", "build"], web)
    print("[package] neither pnpm nor npm found — cannot build the web app")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build Meta-Harness artifacts.")
    parser.add_argument("--web", action="store_true", help="also build apps/web")
    parser.add_argument("--skip-python", action="store_true")
    args = parser.parse_args(argv)

    code = 0
    if not args.skip_python:
        code = build_python()
    if code == 0 and args.web:
        code = build_web()
    if code == 0 and DIST.is_dir():
        produced = sorted(p.name for p in DIST.iterdir())
        print(f"[package] dist/: {', '.join(produced) if produced else '(empty)'}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

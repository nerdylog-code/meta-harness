#!/usr/bin/env python3
"""Render-test the desktop plugin under React 18 + jsdom.

This catches runtime errors that 'check_plugin_load.py' (which only loads
the module) misses: undefined imports used inside JSX, missing React keys,
broken state machines.

Requires: node + a local node_modules with jsdom, react, react-dom.
Install with: pip install -r requirements.txt   (we use a sister dir).

Usage:
    python scripts/check_plugin_render.py [plugin.js path]
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = Path(sys.argv[1]) if len(sys.argv) >= 2 else ROOT / "desktop-plugin" / "plugin.js"
SCRIPT = Path(__file__).resolve().parent / "probe_render.mjs"
# Sister directory holds node_modules so this script doesn't pollute the
# repo (also: .gitignore excludes /scripts/node_modules).
NM_DIR = Path(__file__).resolve().parent / "node_modules"


def ensure_node_modules() -> None:
    """Install jsdom+react if not already present."""
    if (NM_DIR / "jsdom" / "package.json").exists() \
            and (NM_DIR / "react" / "package.json").exists() \
            and (NM_DIR / "react-dom" / "package.json").exists():
        return  # already installed
    print("Installing jsdom, react, react-dom into scripts/node_modules...")
    NM_DIR.mkdir(parents=True, exist_ok=True)
    pkg = NM_DIR / "package.json"
    if not pkg.exists():
        pkg.write_text('{"name":"mh-probe","private":true,"version":"0.0.0"}', encoding="utf-8")
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm:
        print("FAIL: npm not on PATH; install nodejs from https://nodejs.org")
        sys.exit(2)
    try:
        subprocess.run(
            [npm, "install", "--silent", "--no-audit", "--no-fund",
             "jsdom@22", "react@18", "react-dom@18"],
            cwd=str(NM_DIR), check=True, timeout=180,
        )
    except subprocess.TimeoutExpired:
        print("FAIL: npm install timed out (180s)")
        sys.exit(3)


def main() -> int:
    if not PLUGIN.exists():
        print(f"FAIL: {PLUGIN} not found")
        return 1
    rc = ensure_node_modules()
    if rc is not None:
        return rc
    try:
        rc = subprocess.run(
            ["node", str(SCRIPT), str(PLUGIN)],
            cwd=str(NM_DIR), timeout=60,
        ).returncode
    except subprocess.TimeoutExpired:
        print("FAIL: render test timed out (60s)")
        return 4
    return rc


if __name__ == "__main__":
    sys.exit(main())
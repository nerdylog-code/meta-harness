#!/usr/bin/env python3
"""Deliberately misbehaving child used by the ProcessSupervisor tests.

Flags (all optional):

  --children N        spawn N grandchildren that just sleep
  --stdout TEXT       write TEXT to stdout (repeat --stdout N times for volume)
  --stderr TEXT       write TEXT to stderr
  --exit-code N       exit with this code
  --ignore-sigterm    ignore SIGTERM so escalation to SIGKILL is exercised
  --echo-stdin        echo each stdin line back to stdout, then exit
  --sleep S           sleep S seconds (default 30)

It is a test fixture, not a product component: it exists so the supervisor's
"zero orphans on cancel" gate can be measured against a real process tree.
"""

from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time


def spawn_grandchildren(count: int) -> list[subprocess.Popen]:
    children = []
    for _ in range(count):
        # Start the grandchild in its own session so it does NOT inherit our
        # death by group signal for free -- the supervisor must still find it.
        children.append(
            subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(600)"],
                start_new_session=(os.name != "nt"),
            )
        )
    return children


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--stdout", action="append", default=[])
    parser.add_argument("--stdout-bytes", type=int, default=0, help="print this many bytes to stdout (deadlock test)")
    parser.add_argument("--stderr", action="append", default=[])
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--ignore-sigterm", action="store_true")
    parser.add_argument("--echo-stdin", action="store_true")
    parser.add_argument("--sleep", type=float, default=30.0)
    parser.add_argument("--print-tree", action="store_true")
    args = parser.parse_args(argv)

    if args.ignore_sigterm:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)

    grandchildren = spawn_grandchildren(args.children)
    if args.print_tree:
        print(f"pid={os.getpid()} grandchildren={[c.pid for c in grandchildren]}", flush=True)

    if args.stdout_bytes:
        block = b"x" * 8192 + b"\n"
        written = 0
        buffer = sys.stdout.buffer
        while written < args.stdout_bytes:
            buffer.write(block)
            written += len(block)
        buffer.flush()

    for line in args.stdout:
        print(line, flush=True)
    for line in args.stderr:
        print(line, file=sys.stderr, flush=True)

    if args.echo_stdin:
        for raw in sys.stdin:
            print(f"echo:{raw.strip()}", flush=True)
        return args.exit_code

    try:
        time.sleep(args.sleep)
    except KeyboardInterrupt:  # a child that dies politely on SIGINT
        return 130
    return args.exit_code


if __name__ == "__main__":
    raise SystemExit(main())

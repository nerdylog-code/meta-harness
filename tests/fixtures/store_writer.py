"""A store writer that can be killed mid-write (WP-004 A7).

Its whole purpose is to be interrupted at an arbitrary moment: it appends events in a
loop, flushes a line per event so the parent can tell how far it got, and never exits on
its own. The parent kills it with a hard kill, then reopens the store and asks whether the
database is intact and whether any partially-written event survived.

Usage::

    python store_writer.py <db-path> [--count N] [--delay S]
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "daemon"))

import metaharness_contracts as c  # noqa: E402
from metaharness.store import Store  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db", help="path to the sqlite file")
    parser.add_argument("--count", type=int, default=10_000, help="events to append (default: forever)")
    parser.add_argument("--delay", type=float, default=0.01, help="seconds between appends")
    parser.add_argument("--data-root", default=None)
    args = parser.parse_args()

    store = Store(args.db, data_root=args.data_root, emit_open_event=False)
    run_id = c.new_id(c.IdKind.RUN)
    store.emit("run.created", {"writer": "store_writer"}, run_id=run_id)
    # Mirror what an adapter does: the run is genuinely 'running' with a live pid, so a
    # crash leaves something for boot reconciliation to find (WP-004 A9).
    store.emit("run.started", {"pid": os.getpid(), "writer": "store_writer"}, run_id=run_id)

    for index in range(args.count):
        result = store.emit("heartbeat.lease", {"index": index}, run_id=run_id)
        # Flush per line: the parent needs to know which seq was durably acknowledged.
        print(f"seq={result.seq}", flush=True)
        time.sleep(args.delay)

    print("done", flush=True)
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

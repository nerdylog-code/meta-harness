"""Connection and transaction discipline for the canonical store.

Three choices here are deliberate and load-bearing (BOOK 8/9, ADR-0003):

* **WAL.** Readers never block the single writer, which is what lets a late WebSocket
  subscriber read the log while an adapter is appending to it.
* **``synchronous=NORMAL``.** Under WAL this keeps commits consistent across a crash
  while allowing the *tail* of the log to be lost on a power cut. The honest contract
  is therefore "the database is never corrupt and never contains a partial event",
  *not* "nothing is ever lost" -- and WP-004 A7 asserts exactly that, with a hard kill.
  ``FULL`` would give more and cost an fsync per append; the trade is documented in
  docs/architecture/STORAGE.md rather than assumed.
* **``BEGIN IMMEDIATE`` for every write.** The lock is taken when the transaction opens,
  not when the first statement runs, so two writers can never interleave their reads of
  ``MAX(seq)``. That is what makes ``seq`` gapless.

Connections are not shared across threads: each ``Store`` owns one connection and
serialises access to it with a re-entrant lock, which is honest for a local daemon with
one writer.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import StoreError

WAL_MODE = "wal"
SYNCHRONOUS = "NORMAL"
BUSY_TIMEOUT_MS = 5000

#: A store handle is a single-writer object; the lock makes that explicit instead of
#: leaving it to the caller's discipline.
_WRITE_LOCK = threading.RLock()


def connect(path: str | Path, *, create: bool = True) -> sqlite3.Connection:
    """Open (and configure) a SQLite connection to ``path``."""
    path = Path(path)
    if not create and not path.exists():
        raise StoreError(f"store does not exist: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        conn = sqlite3.connect(
            str(path),
            isolation_level=None,  # we manage transactions explicitly
            timeout=BUSY_TIMEOUT_MS / 1000,
        )
    except sqlite3.Error as exc:  # pragma: no cover - environment dependent
        raise StoreError(f"cannot open store at {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA journal_mode={WAL_MODE}")
    conn.execute(f"PRAGMA synchronous={SYNCHRONOUS}")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block inside one ``BEGIN IMMEDIATE`` transaction.

    Commits on clean exit, rolls back on any exception, and re-raises so the caller can
    translate. Nested use joins the outer transaction instead of opening a second one
    (SQLite has no real nesting), which keeps the "one transaction per append" rule
    intact when a projection needs to write more than once.
    """
    with _WRITE_LOCK:
        if conn.in_transaction:
            yield conn
            return
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def journal_mode(conn: sqlite3.Connection) -> str:
    row = conn.execute("PRAGMA journal_mode").fetchone()
    return str(row[0]) if row else "unknown"


def integrity_check(conn: sqlite3.Connection) -> list[str]:
    """SQLite's own consistency verdict, plus a foreign-key sweep."""
    problems = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
    if problems == ["ok"]:
        problems = []
    problems.extend(str(row[0]) for row in conn.execute("PRAGMA foreign_key_check").fetchall())
    return problems

"""Numbered, ordered, immutable migrations.

The rules come from BOOK 4/84 and WP-004:

1. Files are ``NNNN_name.sql``, contiguous from 1. A gap or a duplicate is a defect,
   not a convenience -- it is how a renumbered or deleted migration gets noticed.
2. Each file is applied inside **one** transaction, together with its bookkeeping row.
   A failing statement rolls the whole migration back: there is no such thing as a
   half-applied migration (constraint 7).
3. Each file's ``sha256`` is recorded when applied and re-checked on every later open.
   Editing an applied migration is refused before anything executes (A5).
4. A correction is a **new** migration.

``executescript`` is deliberately not used: it commits any pending transaction before
running, which would silently destroy rule 2. Statements are split and executed one at
a time inside our own transaction instead -- and the splitter understands `BEGIN ... END`
trigger bodies, string literals and comments, because a naive split on ``;`` would cut a
trigger in half.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from .db import transaction
from .errors import MigrationError, MigrationHistoryError, SchemaUnknownError, TamperedMigrationError

#: apps/daemon/metaharness/migrations -- beside this package, not inside it, so the .sql
#: files are readable as data rather than shipped as modules.
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

MIGRATION_PATTERN = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

BOOTSTRAP_SQL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    name        TEXT    NOT NULL,
    sha256      TEXT    NOT NULL,
    applied_at  REAL    NOT NULL,
    duration_ms INTEGER NOT NULL
);
"""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    sha256: str
    sql: str

    @property
    def label(self) -> str:
        return f"{self.version:04d}_{self.name}"


@dataclass(frozen=True)
class MigrationStep:
    version: int
    name: str
    applied: bool
    duration_ms: int


def split_statements(sql: str) -> list[str]:
    """Split a SQL script into executable statements.

    Handles the three things that make a naive ``sql.split(';')`` wrong for our
    migrations: single-quoted string literals (which may contain ``;``), ``--`` line
    comments, and ``CREATE TRIGGER ... BEGIN ... END;`` bodies (whose inner ``;``
    separators are not statement boundaries).
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    in_comment = False
    trigger_depth = 0
    index = 0
    length = len(sql)

    while index < length:
        char = sql[index]
        pair = sql[index : index + 2]

        if in_comment:
            if char == "\n":
                in_comment = False
                current.append(char)
            index += 1
            continue

        if in_string:
            current.append(char)
            if char == "'":
                if pair == "''":  # escaped quote inside a literal
                    current.append("'")
                    index += 2
                    continue
                in_string = False
            index += 1
            continue

        if pair == "--":
            in_comment = True
            index += 2
            continue

        if char == "'":
            in_string = True
            current.append(char)
            index += 1
            continue

        if char == ";":
            if trigger_depth > 0:
                current.append(char)
            else:
                statement = "".join(current).strip()
                if statement:
                    statements.append(statement)
                current = []
            index += 1
            continue

        # Track trigger bodies: BEGIN raises the depth, END lowers it. Only the outer
        # statement is a unit of execution, so inner semicolons must not split.
        if char.isalpha():
            word_match = re.match(r"[A-Za-z_]+", sql[index:])
            assert word_match is not None
            word = word_match.group(0).upper()
            if word == "BEGIN":
                trigger_depth += 1
            elif word == "END":
                trigger_depth = max(0, trigger_depth - 1)
            current.append(sql[index : index + len(word_match.group(0))])
            index += len(word_match.group(0))
            continue

        current.append(char)
        index += 1

    tail = "".join(current).strip()
    if tail:
        statements.append(tail)
    return statements


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def discover(directory: Path | None = None) -> list[Migration]:
    """Read the migration directory, validating numbering and naming."""
    directory = Path(directory or MIGRATIONS_DIR)
    if not directory.is_dir():
        raise MigrationError(f"migrations directory is missing: {directory}")

    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = MIGRATION_PATTERN.match(path.name)
        if not match:
            raise MigrationHistoryError(
                f"migration file {path.name!r} does not match NNNN_name.sql; "
                "renaming a migration is forbidden once a store has applied it"
            )
        version = int(match.group(1))
        sql = path.read_text(encoding="utf-8")
        found.append(Migration(version, match.group(2), path, _sha256(sql), sql))

    versions = [migration.version for migration in found]
    if len(set(versions)) != len(versions):
        raise MigrationHistoryError(f"duplicate migration versions: {sorted(versions)}")
    expected = list(range(1, len(found) + 1))
    if versions != expected:
        raise MigrationHistoryError(
            f"migration versions must be contiguous from 1; found {versions}, expected {expected}"
        )
    return found


def applied_versions(conn: sqlite3.Connection) -> dict[int, sqlite3.Row]:
    """``{version: row}`` for everything already applied (``{}`` on a fresh store)."""
    if not _table_exists(conn, "schema_migrations"):
        return {}
    return {row["version"]: row for row in conn.execute("SELECT * FROM schema_migrations")}


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def schema_version(conn: sqlite3.Connection) -> int:
    versions = applied_versions(conn)
    return max(versions) if versions else 0


def ensure_bootstrap(conn: sqlite3.Connection) -> None:
    conn.execute(BOOTSTRAP_SQL)


def plan(conn: sqlite3.Connection, migrations: list[Migration]) -> list[Migration]:
    """Pending migrations, after refusing a tampered or lost history.

    Raises before executing anything, so a mismatch can only ever abort -- never
    half-apply (A5).
    """
    recorded = applied_versions(conn)
    by_version = {migration.version: migration for migration in migrations}

    for version, row in sorted(recorded.items()):
        migration = by_version.get(version)
        if migration is None:
            raise MigrationHistoryError(
                f"store recorded migration {version:04d} ({row['name']}) but that file is gone; "
                "migration history must not be deleted or renamed"
            )
        if migration.sha256 != row["sha256"]:
            raise TamperedMigrationError(
                f"migration {migration.label} changed after it was applied "
                f"(recorded {row['sha256'][:12]}, on disk {migration.sha256[:12]}); "
                "migrations are immutable -- write a new one instead"
            )

    highest_recorded = max(recorded) if recorded else 0
    if highest_recorded > len(migrations):
        raise SchemaUnknownError(
            f"store is at schema version {highest_recorded} but only {len(migrations)} "
            "migration(s) are available: this store was written by a newer build"
        )
    return [migration for migration in migrations if migration.version not in recorded]


def apply_all(
    conn: sqlite3.Connection, directory: Path | None = None
) -> list[MigrationStep]:
    """Apply every pending migration, one transaction each."""
    migrations = discover(directory)
    ensure_bootstrap(conn)
    steps: list[MigrationStep] = []

    for migration in plan(conn, migrations):
        started = time.perf_counter()
        try:
            with transaction(conn):
                for number, statement in enumerate(split_statements(migration.sql), start=1):
                    try:
                        conn.execute(statement)
                    except sqlite3.Error as exc:
                        raise MigrationError(
                            f"migration {migration.label} failed at statement {number}: {exc}\n"
                            f"statement: {statement[:300]}"
                        ) from exc
                conn.execute(
                    "INSERT INTO schema_migrations (version, name, sha256, applied_at, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        migration.version,
                        migration.name,
                        migration.sha256,
                        time.time(),
                        int((time.perf_counter() - started) * 1000),
                    ),
                )
        except MigrationError:
            raise
        except Exception as exc:
            raise MigrationError(f"migration {migration.label} aborted: {exc}") from exc
        steps.append(
            MigrationStep(
                version=migration.version,
                name=migration.name,
                applied=True,
                duration_ms=int((time.perf_counter() - started) * 1000),
            )
        )
    return steps

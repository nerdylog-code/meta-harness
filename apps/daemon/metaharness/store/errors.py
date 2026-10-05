"""Failures the store raises, and what each one means for the caller.

Every one of these is a startup-visible failure by design (BOOK 82): the store is
the system of record, so "keep going and hope" is never the right response to a
storage problem.
"""

from __future__ import annotations


class StoreError(RuntimeError):
    """Base class for every failure raised by the store itself."""


class MigrationError(StoreError):
    """A migration could not be applied.

    The caller must abort startup rather than continue on a half-migrated schema
    (WP-004 constraint 7). The message carries the version, the file and the failing
    statement so the operator does not have to guess.
    """


class TamperedMigrationError(MigrationError):
    """An already-applied migration file no longer hashes to what was recorded.

    Migrations are immutable after merge (BOOK 84). This is raised *before* anything
    is executed, so a tampered history can never be half-applied.
    """


class MigrationHistoryError(MigrationError):
    """The recorded history does not match the files on disk.

    For example: a store that recorded version 3 while the directory offers only 1 and
    2, or duplicated/gapped version numbers in the directory itself.
    """


class SchemaUnknownError(StoreError):
    """The database file is not a Meta-Harness store (or is from a newer schema)."""


class AppendOnlyViolation(StoreError):
    """Something tried to update or delete a canonical event.

    The schema raises this from a trigger, so it reaches us as an ``sqlite3`` error and
    is translated here. It is a programming defect, never a user error.
    """


class ProjectionError(StoreError):
    """A projection refused an event.

    Raised inside the append transaction, which rolls the whole thing back: the store
    never keeps an event whose projection could not be applied (WP-004 A2).
    """

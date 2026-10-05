-- 0001 - the canonical event log.
--
-- Immutable after merge (BOOK 84): a mistake is a new migration, never an edit to
-- this file. The migration runner records this file's sha256 and refuses to open a
-- store whose applied history no longer matches it.
--
-- Append-only is a property of the schema, not a convention: the two triggers below
-- abort any UPDATE or DELETE on `events`. The Architect's rule for WP-004 is that
-- the store never erases or rewrites a canonical event -- a correction is a *new*
-- event -- so violating it must be impossible rather than discouraged.
--
-- `seq` is the primary key and is assigned by the store as MAX(seq)+1 inside the
-- same BEGIN IMMEDIATE transaction that inserts the row, which makes it monotonic
-- and gapless per store instance (WP-004 constraint 3). It is deliberately not
-- AUTOINCREMENT: we want the gap-free guarantee, not a rowid counter that survives
-- rollbacks.
--
-- The envelope columns are exactly the 14 frozen keys of CanonicalEvent; `namespace`
-- is a derived column so that per-namespace queries stay cheap without parsing JSON.

CREATE TABLE IF NOT EXISTS events (
    seq            INTEGER PRIMARY KEY,
    id             TEXT    NOT NULL UNIQUE,
    ts             REAL    NOT NULL,
    kind           TEXT    NOT NULL,
    namespace      TEXT    NOT NULL,
    mission_id     TEXT,
    task_id        TEXT,
    run_id         TEXT,
    agent_id       TEXT,
    session_id     TEXT,
    runtime_id     TEXT,
    correlation_id TEXT,
    causation_id   TEXT,
    payload        TEXT    NOT NULL,
    provenance     TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS events_kind_seq ON events (kind, seq);
CREATE INDEX IF NOT EXISTS events_namespace_seq ON events (namespace, seq);
CREATE INDEX IF NOT EXISTS events_run_seq ON events (run_id, seq);
CREATE INDEX IF NOT EXISTS events_session_seq ON events (session_id, seq);
CREATE INDEX IF NOT EXISTS events_correlation_seq ON events (correlation_id, seq);

CREATE TRIGGER IF NOT EXISTS events_append_only_update
BEFORE UPDATE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only: a correction is a new event, never an edit (WP-004)');
END;

CREATE TRIGGER IF NOT EXISTS events_append_only_delete
BEFORE DELETE ON events
BEGIN
    SELECT RAISE(ABORT, 'events are append-only: the log is never rewritten (WP-004)');
END;

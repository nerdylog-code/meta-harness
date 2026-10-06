-- 0006 - runtime migrations as a lifecycle.
--
-- A migration is an operation with stages, not a single atomic act: it builds and verifies a
-- capsule, checks the destination, creates a session there, attaches the capsule, archives the
-- source, and completes. The Architect's rule is that a partial migration must never be hidden, so
-- each stage is an event and this table is their folded state.
--
-- `state` is the furthest stage reached. `failed_stage` says where it stopped. A row in state
-- `failed` is a fact about an attempt, not an error to be cleaned up: the log is append-only and
-- the recovery is another attempt, never a rewrite.
--
-- The id is a `run_` id used as the envelope's `correlation_id`: a migration is an operation, and
-- the envelope already has a field for grouping one operation's events. No new id kind was invented
-- for it (the contract stays frozen).

CREATE TABLE IF NOT EXISTS migrations (
    id           TEXT    PRIMARY KEY,
    agent_id     TEXT,
    mission_id   TEXT,
    from_runtime TEXT,
    to_runtime   TEXT,
    from_session TEXT,
    to_session   TEXT,
    capsule_id   TEXT,
    digest       TEXT,
    state        TEXT    NOT NULL,
    failed_stage TEXT,
    reason       TEXT,
    created_ts   REAL    NOT NULL,
    updated_ts   REAL    NOT NULL,
    last_seq     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS migrations_agent_idx ON migrations (agent_id);
CREATE INDEX IF NOT EXISTS migrations_state_idx ON migrations (state);
CREATE INDEX IF NOT EXISTS migrations_capsule_idx ON migrations (capsule_id);

-- 0003 - the run lifecycle projection.
--
-- `runs` is the first projection, and it exists because WP-004's acceptance test A9
-- needs a real lifecycle to reconcile: a run left in `running` by a dead process must
-- boot back into an honest state. The table is a *projection* of `run.*` events -- not
-- an independent truth -- and `replay` rebuilds it byte-for-byte (A3).
--
-- States are the vocabulary of the BOOK 13 `run.*` namespace plus the corrective
-- states the boot reconciler produces:
--
--   created -> running -> done | failed | interrupted | orphaned
--
-- `last_seq` records which event last moved the row, which is what lets a reviewer see
-- the projection's position in the log without replaying anything.
--
-- mission_id/task_id/agent_id/session_id are opaque id strings, never foreign keys:
-- the missions/agents/sessions tables arrive with their own features (WP-004 risk row),
-- and an FK here would make this package create speculative tables it does not test.

CREATE TABLE IF NOT EXISTS runs (
    id            TEXT    PRIMARY KEY,
    mission_id    TEXT,
    task_id       TEXT,
    agent_id      TEXT,
    session_id    TEXT,
    runtime_id    TEXT,
    state         TEXT    NOT NULL,
    reason        TEXT,
    pid           INTEGER,
    created_ts    REAL    NOT NULL,
    updated_ts    REAL    NOT NULL,
    last_seq      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS runs_state_idx ON runs (state);
CREATE INDEX IF NOT EXISTS runs_session_idx ON runs (session_id);
CREATE INDEX IF NOT EXISTS runs_task_idx ON runs (task_id);

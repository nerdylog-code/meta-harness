-- 0005 - context capsules.
--
-- A capsule is the object that crosses a runtime boundary (M2). It is stored as an artifact --
-- content-addressed, so its bytes are verifiable -- and this table is the index that points at
-- it. The row is written by `apply_event` inside the same transaction as `capsule.created`, like
-- every other projection: replay rebuilds it, and there is no second source of truth.
--
-- `verified` and `verification` are recorded rather than assumed. A capsule that failed
-- verification is still a row (the log is append-only and an attempt is a fact), but the row says
-- so, and the migration refuses to travel on one.

CREATE TABLE IF NOT EXISTS capsules (
    id           TEXT    PRIMARY KEY,   -- the artifact id holding the capsule JSON
    agent_id     TEXT,
    mission_id   TEXT,
    session_id   TEXT,
    runtime_id   TEXT,                  -- the runtime the capsule was built FROM
    phase        TEXT    NOT NULL,
    objective    TEXT    NOT NULL,
    sha256       TEXT    NOT NULL,      -- digest of the exact bytes that were stored
    size         INTEGER NOT NULL,
    verified     INTEGER NOT NULL DEFAULT 0,
    verification TEXT,                  -- JSON: every check and its outcome
    created_ts   REAL    NOT NULL,
    last_seq     INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS capsules_agent_idx ON capsules (agent_id);
CREATE INDEX IF NOT EXISTS capsules_session_idx ON capsules (session_id);
CREATE INDEX IF NOT EXISTS capsules_mission_idx ON capsules (mission_id);

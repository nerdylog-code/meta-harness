-- 0004 - missions, agents, agent versions and sessions.
--
-- These are projections of the event log, exactly like `runs`: every row is written by
-- `apply_event` inside the same transaction as the event that caused it, and replay rebuilds
-- them byte-for-byte. There is no second source of truth for an agent (the Architect's rule),
-- so there is deliberately no "update" path anywhere but through an event.
--
-- An agent's identity and its configuration are separate on purpose (ADR-0002): `agents` holds
-- the identity, `agent_versions` holds each configuration it has had. Moving Nova from one
-- runtime to another creates a version, never a new agent.
--
-- `sessions` records which runtime and model actually served a session, not which one was
-- requested -- the difference is the whole point of keeping them.

CREATE TABLE IF NOT EXISTS missions (
    id         TEXT    PRIMARY KEY,
    title      TEXT    NOT NULL,
    objective  TEXT    NOT NULL DEFAULT '',
    owner      TEXT,
    status     TEXT    NOT NULL DEFAULT 'active',
    created_ts REAL    NOT NULL,
    updated_ts REAL    NOT NULL,
    last_seq   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agents (
    id           TEXT    PRIMARY KEY,
    display_name TEXT    NOT NULL,
    role         TEXT    NOT NULL DEFAULT 'builder',
    created_ts   REAL    NOT NULL,
    updated_ts   REAL    NOT NULL,
    last_seq     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_versions (
    id                TEXT    PRIMARY KEY,
    agent_id          TEXT    NOT NULL,
    version           INTEGER NOT NULL,
    runtime_preferred TEXT,
    model_primary     TEXT,
    created_ts        REAL    NOT NULL,
    last_seq          INTEGER NOT NULL,
    UNIQUE (agent_id, version)
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT    PRIMARY KEY,
    agent_id   TEXT,
    mission_id TEXT,
    task_id    TEXT,
    run_id     TEXT,
    runtime_id TEXT,
    provider   TEXT,
    model      TEXT,
    state      TEXT    NOT NULL,
    reason     TEXT,
    created_ts REAL    NOT NULL,
    updated_ts REAL    NOT NULL,
    last_seq   INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS sessions_agent_idx ON sessions (agent_id);
CREATE INDEX IF NOT EXISTS sessions_mission_idx ON sessions (mission_id);
CREATE INDEX IF NOT EXISTS agent_versions_agent_idx ON agent_versions (agent_id);

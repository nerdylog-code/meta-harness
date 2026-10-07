-- 0007 - tasks and their dependencies: the work graph.
--
-- Projections of the event log, like everything else here: written only by `apply_event` inside
-- the append transaction, rebuilt byte-for-byte by replay. There is deliberately no UPDATE path
-- outside an event, which is what makes "restart and the graph is identical" a property of the
-- log rather than a hope.
--
-- `task_dependencies` is a separate table rather than a JSON column because the graph is queried
-- as a graph: readiness is a join, and a cycle check has to be able to see the edges. Each edge
-- carries the seq that created it, so a rebuild is deterministic even when an edge is removed and
-- added again.
--
-- A task is not a session and a run is not an agent: `run_id` records which run executed the task,
-- and `owner_agent` records who was assigned. Neither defines the other.

CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT    PRIMARY KEY,
    mission_id    TEXT    NOT NULL,
    title         TEXT    NOT NULL,
    description   TEXT    NOT NULL DEFAULT '',
    state         TEXT    NOT NULL,
    owner_agent   TEXT,
    run_id        TEXT,
    workspace_scope TEXT,
    acceptance    TEXT    NOT NULL DEFAULT '{}',
    proof         TEXT    NOT NULL DEFAULT '[]',
    artifacts     TEXT    NOT NULL DEFAULT '[]',
    created_ts    REAL    NOT NULL,
    updated_ts    REAL    NOT NULL,
    completed_ts  REAL,
    last_seq      INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_mission ON tasks (mission_id, state);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks (run_id);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id       TEXT    NOT NULL,
    depends_on    TEXT    NOT NULL,
    created_seq   INTEGER NOT NULL,
    PRIMARY KEY (task_id, depends_on)
);

CREATE INDEX IF NOT EXISTS idx_task_deps_reverse ON task_dependencies (depends_on);

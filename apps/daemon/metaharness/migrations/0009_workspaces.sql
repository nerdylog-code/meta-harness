-- 0009 - workspace allocations and writer leases.
--
-- A worktree is concurrency and write isolation, NOT a security boundary (BOOK §26): nothing here
-- grants or implies host filesystem isolation. That dimension is the sandbox's, and it is measured
-- in `session.policy`, not inferred from the existence of a worktree.
--
-- Identity is `(task_id, repository_common_dir)`. No new public id prefix is introduced: the
-- namespace is frozen, and a lease is identified by its allocation plus a monotonically increasing
-- generation. Two clones of the same URL are different repositories; two worktrees of one clone
-- share a common dir, which is what `--git-common-dir` answers.
--
-- The lease row is a projection of `workspace.lease.*` events, so "who may write right now" is a
-- fact in the log rather than a lock in a process. `expires_at` is stored rather than derived,
-- because a bounded TTL is what keeps a lease from becoming an immortal lock.

CREATE TABLE IF NOT EXISTS workspaces (
    task_id             TEXT    NOT NULL,
    repository_root     TEXT    NOT NULL,
    repository_common   TEXT    NOT NULL,
    mission_id          TEXT,
    base_ref            TEXT    NOT NULL,
    base_commit         TEXT    NOT NULL,
    branch              TEXT    NOT NULL,
    locator             TEXT    NOT NULL,
    host_path           TEXT    NOT NULL,
    state               TEXT    NOT NULL,
    dirty               INTEGER,
    created_ts          REAL    NOT NULL,
    updated_ts          REAL    NOT NULL,
    last_seq            INTEGER NOT NULL,
    PRIMARY KEY (task_id, repository_common)
);

CREATE INDEX IF NOT EXISTS idx_workspaces_state ON workspaces (state);
CREATE INDEX IF NOT EXISTS idx_workspaces_path ON workspaces (host_path);

CREATE TABLE IF NOT EXISTS workspace_leases (
    task_id             TEXT    NOT NULL,
    repository_common   TEXT    NOT NULL,
    run_id              TEXT    NOT NULL,
    generation          INTEGER NOT NULL,
    acquired_ts         REAL    NOT NULL,
    heartbeat_ts        REAL    NOT NULL,
    expires_at          REAL    NOT NULL,
    state               TEXT    NOT NULL,
    last_seq            INTEGER NOT NULL,
    PRIMARY KEY (task_id, repository_common)
);

CREATE INDEX IF NOT EXISTS idx_leases_state ON workspace_leases (state, expires_at);

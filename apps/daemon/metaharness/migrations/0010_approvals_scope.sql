-- 0010 - the scope of an approval: which mission and task it belongs to.
--
-- An approval is about work, and "which work" must be answerable without parsing an action payload.
-- The canonical envelope already carries `mission_id` and `task_id`, so this records what the log
-- already says rather than deriving anything. Rows that predate the column keep NULL, which is the
-- honest answer: an approval whose scope was never recorded has no scope, and the board says so
-- instead of guessing one.

ALTER TABLE approvals ADD COLUMN mission_id TEXT;
ALTER TABLE approvals ADD COLUMN task_id TEXT;

CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals (task_id, state);

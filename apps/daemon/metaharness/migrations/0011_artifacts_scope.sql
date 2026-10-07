-- 0011 - the scope of an artifact: which mission and task produced it.
--
-- The task row already records the artifacts it was completed with, and that remains the canonical
-- list for "what this task delivered". This column answers a different question the board needs
-- while a task is still running: which artifacts exist *for* this task right now, without parsing
-- an origin blob or counting a neighbour's work. The canonical envelope already carries the scope,
-- so this records what the log says. Rows that predate the column keep NULL.

ALTER TABLE artifacts ADD COLUMN mission_id TEXT;
ALTER TABLE artifacts ADD COLUMN task_id TEXT;

CREATE INDEX IF NOT EXISTS idx_artifacts_task ON artifacts (task_id);

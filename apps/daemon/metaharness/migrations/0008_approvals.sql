-- 0008 - approvals: an authorisation bound to one exact action.
--
-- A projection of the event log, like everything else: written only by `apply_event` inside the
-- append transaction, rebuilt by replay. The row is a *record* of what was asked and what was
-- decided -- the decision itself is the event, so an approval cannot exist only in a UI modal.
--
-- The binding is `action_payload_hash`, computed over the action type and the exact payload
-- (canonical JSON, sorted keys). A different payload is a different hash, which is what makes
-- "this approval does not cover that action" a local, mechanical answer instead of a judgement.
--
-- `consumed_ts` is here rather than in the frozen `ApprovalRequest` because consumption is a fact
-- about the log (the action ran), not a property of the authorisation the human granted.

CREATE TABLE IF NOT EXISTS approvals (
    id                  TEXT    PRIMARY KEY,
    action_type         TEXT    NOT NULL,
    action_payload      TEXT    NOT NULL DEFAULT '{}',
    action_payload_hash TEXT    NOT NULL,
    risk_level          TEXT    NOT NULL,
    human_summary       TEXT    NOT NULL,
    reversibility       TEXT    NOT NULL DEFAULT 'reversible',
    requested_by        TEXT    NOT NULL,
    requested_ts        REAL    NOT NULL,
    expires_at          REAL,
    state               TEXT    NOT NULL DEFAULT 'pending',
    granted_by          TEXT,
    granted_ts          REAL,
    decided_reason      TEXT,
    consumed_ts         REAL,
    last_seq            INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_approvals_state ON approvals (state, requested_ts);
CREATE INDEX IF NOT EXISTS idx_approvals_hash ON approvals (action_payload_hash);

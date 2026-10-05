-- 0002 - the artifact index.
--
-- Large payloads never live in the database (BOOK 9, WP-004 constraint 5): the bytes
-- live on the filesystem under the platformdirs artifacts/ directory, content-addressed
-- by sha256, and this table records only the index. There is deliberately **no blob
-- column**, and `tests/unit/store/test_artifacts.py` asserts that by inspecting the
-- declared column types -- a 5 MB payload must not grow the database file.
--
-- `origin` records where the artifact came from (mission/run/task/agent/tool) so that
-- provenance survives even when the payload is opaque, and `seq` ties the artifact to
-- the event that registered it, which is what makes replay reproducible.

CREATE TABLE IF NOT EXISTS artifacts (
    id         TEXT    PRIMARY KEY,
    path       TEXT    NOT NULL,
    sha256     TEXT    NOT NULL,
    mime       TEXT    NOT NULL DEFAULT 'application/octet-stream',
    size       INTEGER NOT NULL,
    metadata   TEXT    NOT NULL DEFAULT '{}',
    origin     TEXT    NOT NULL DEFAULT '{}',
    created_ts REAL    NOT NULL,
    seq        INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS artifacts_sha256_idx ON artifacts (sha256);
CREATE INDEX IF NOT EXISTS artifacts_seq_idx ON artifacts (seq);

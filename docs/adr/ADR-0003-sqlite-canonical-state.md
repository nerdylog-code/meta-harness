# ADR-0003 — SQLite is the canonical source of state and events

**Status:** accepted (2026-10-05) · **Decision owner:** project owner + Architect (Book §Appendix A #3)
**Supersedes:** v1 `docs/DECISIONS.md` ADR-0003 (*"SQLite + JSONL hybrid"*, 2026-08-24)
**Format:** PROJECT_BOOK §91.

---

## DECISION

Where the v2 control plane keeps its authoritative state and its canonical event log, and what role JSONL plays.

## CHOICE

**SQLite (WAL) is the single canonical store** for state and events. Appending an event and updating the projections it affects happen in **one transaction**.

**JSONL is a derived artifact only**: export, debug trace, backup-friendly form, benchmark/replay package. It is produced *from* SQLite and is never read as authority. Deleting every `.jsonl` file must change nothing about the system's state.

Large payloads stay out of the database: they live on the filesystem, and SQLite stores `id, path, sha256, mime, size, metadata, origin`.

## ALTERNATIVES

1. **JSONL as the source of truth, SQLite as an index** (v1's shape, `docs/ARCHITECTURE.md`: *"events.jsonl — append-only, full payload, replay source of truth"*).
   Rejected: there is no atomic transaction spanning a file append and a SQLite write. The two truths can always diverge — a crash between them, a partially written line, a hand-edited file — and every reader then has to guess which one wins. PROJECT_BOOK §8 names this reason explicitly.
2. **Dual write, both authoritative.** Same defect, more code.
3. **One file per event, no database.** Rejected: no transactional projections, no cheap range/kind queries, no single-file backup or migration story.
4. **A server-grade database (Postgres et al.).** Rejected for v2: local-first, no service dependency, single-user daemon. SQLite in WAL mode is the right weight.

## WHY

- Projections and their events cannot diverge if they commit together (BOOK §9).
- Replay is a property of one ordered log, not of reconciling two stores (BOOK §78 gate).
- Query and index behaviour is explicit SQL, which the Book prefers over an ORM in the kernel.
- Artifact externalization is what keeps the database small enough that WAL and file-level backup remain sane.

## REVERSIBILITY

**Medium.** The decision itself is one-way for the kernel — WP-004 builds on it — but the lossless JSONL exporter makes the *data* portable, so leaving SQLite later is a migration, not a rewrite of history. Cost of reversal: the storage package plus whatever grew on top of its transaction discipline.

## EVIDENCE

| Claim | Evidence |
|---|---|
| v1 maintains two stores and calls JSONL authoritative | `hermes-plugin/hermes_plugin/store.py` (475 lines) writes `events.jsonl` **and** `event_index`; `docs/ARCHITECTURE.md` §"Event plane"; v1 ADR-0003 |
| v1's own rule forbids the useful half of SQLite | v1 ADR-0003: *"Storing raw LLM tokens in SQLite is forbidden; the SQLite row references the JSONL offset for replay"* — i.e. reading a payload requires a second lookup into a file that the transaction did not cover |
| The Book rejects dual truth | PROJECT_BOOK §8: *"SQLite passa a ser o source of truth… Não manteremos dois sources of truth porque não existe transação realmente atômica entre SQLite e append de arquivo."* |
| v1 never exercised either store at runtime | `~/.hermes/meta-harness` does not exist on the recon host; the plugin is `not enabled` (see `V1_INVENTORY.md` §3) |
| The Book already enumerates the target schema | PROJECT_BOOK §9 (22 tables) + §12 (artifact fields incl. SHA-256) |

## RISKS

| Risk | Mitigation |
|---|---|
| One file to lose | WAL + periodic derived export (the JSONL path doubles as the backup artifact) + artifact hashing |
| Unbounded growth from big tool outputs | Artifact externalization is mandatory, not optional (BOOK §21) |
| Windows file locking (`-wal`/`-shm` held after shutdown) | WP-004 A10 runs the restart/crash tests on Windows CI |
| Migration mistakes becoming permanent | Migrations are ordered and immutable after merge (BOOK §84); a fix is a new migration |
| Silent divergence creeping back in through a "quick" JSONL read | WP-004 asserts no component reads JSONL as authority; the exporter is one-way |

## HOW TO VALIDATE

1. **WP-004 A2** — an event whose projection update fails rolls back entirely (proves single-transaction).
2. **WP-004 A3** — replay from an empty store reproduces the live projections byte-identically, on a generated log of ≥10 000 events.
3. **WP-004 A4** — restart preserves state with no manual step.
4. **WP-004 A8** — deleting the JSONL export changes nothing; re-importing it reproduces the same state.
5. **Review check** — `grep` for any read of a `.jsonl` path outside the export/importer module is a defect.

# ADR-0018 — The Context Capsule is the transfer object; the text is derived from it

**Status:** accepted (M2). **Related:** ADR-0002 (identity ≠ configuration), ADR-0006
(`RuntimeAdapter` v2), `packages/contracts/metaharness_contracts/capsule.py`, BOOK §21/§22.

## Context

A runtime migration needs to carry operational state from one runtime to another. The obvious
implementation is to hand the new runtime a text prompt — "here is what was going on, continue" —
because every runtime accepts text and no runtime accepts our types.

That is also the failure mode. If the prompt *is* the transfer, then:

* the state that travelled is whatever someone remembered to put in a sentence, and nothing can
  check it;
* the link between the old session and the new one lives in prose, so lineage becomes archaeology;
* a runtime that reformats or truncates the prompt silently corrupts the handoff;
* and "the capsule was transferred" becomes an unfalsifiable claim in the UI.

The Architect named this risk directly when approving M2: *do not let `resume_instruction`
accidentally become the official mechanism of migration.*

## Decision

**The `ContextCapsule` is the transfer object.** It is built from the event log, verified, stored
as a content-addressed artifact, and attached to the destination session by an explicit, typed
event:

```
context.capsule.attached {
    capsule_id, source_session_id, destination_session_id,
    agent_id, mission_id, digest, sha256, attachment: "structured"
}
```

`resume_instruction` — the capsule's own field — is **derived**: it is what the destination runtime
is handed as text, and it is recorded as a `message.submitted` event with
`injected: "context_capsule"` and the `capsule_id` it came from. The text is a rendering of the
capsule, never the capsule's substitute.

The verification happens **at transfer time**, not only when the capsule was written: the digest is
recomputed over the bytes on disk and compared with the stored `sha256`, every referenced artifact
is resolved against the store, and a capsule belonging to another agent is refused. A migration
that cannot verify its capsule stops with a 409 before anything moves.

## Consequences

* **The link is queryable.** `GET /v1/migrations/{id}` returns the attempt and its events, so
  "which capsule carried this agent, from where to where, with what digest" is an answer, not a
  reconstruction.
* **Migration is a lifecycle, not an act.** `migration.requested → capsule_verified →
  destination_created → capsule_attached → source_archived → completed`, or `migration.failed`
  with the stage it reached and the state it had. A partial migration is a recorded fact with a
  named recovery, never a silent rollback.
* **The destination is checked before the source is archived.** An unavailable destination leaves
  the old session exactly as it was, which is a property that can only hold if nothing is destroyed
  before its replacement exists and already holds the capsule.
* **A runtime that only accepts text is still a first-class destination** — it receives a
  rendering, and the canonical object stays ours. This is what makes "move Nova to another
  runtime" an operation with evidence instead of a plausible sentence.
* The capsule stays bounded (64 KB, no transcript fields), so "transfer the state" can never quietly
  become "copy the conversation".

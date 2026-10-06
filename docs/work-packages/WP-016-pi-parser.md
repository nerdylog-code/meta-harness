# WP-016 — Pi record parser

**Owner:** flash-class builder · **Wave:** W5 · **Depends on:** WP-015 · **Blocks:** WP-017

---

## Objective

Turn Pi's records into our canonical events and usage samples. Pure functions, no process, no
I/O: the parser is the one place that knows what Pi's vocabulary *means*.

## Allowed files

```
apps/daemon/metaharness/runtimes/pi/parse.py          (new)
tests/unit/runtimes/pi/test_parse.py
tests/fixtures/pi_records/*.ndjson                    (captured and hand-written samples)
```

## Forbidden files

```
packages/contracts/**   (the envelope and UsageSample are frozen; a needed field goes back to the Architect)
apps/daemon/metaharness/runtimes/pi/transport.py      (WP-015 owns it)
```

## Required reading

`docs/protocols/PI_RPC.md` §5–§6 · `docs/architecture/EVENTS.md` · the frozen `UsageSample`
(`packages/contracts/metaharness_contracts/usage.py`).

## Architecture constraints

1. **Every record becomes at most one canonical event**, with a namespaced `kind` from BOOK §13.
   Unknown record types are preserved as `runtime.pi.unknown` with the raw payload — never dropped
   (a silently dropped frame is a hole in the audit trail).
2. **Usage provenance is `provider_reported`**, always, because that is what it is. Our own
   measurements are `measured` and must never be blended into Pi's numbers.
3. **`cacheRead`/`cacheWrite`/`reasoning` are separate metrics**, not folded into input/output.
4. **`agent_end` ≠ settled.** The parser exposes both; only `agent_settled` means "Pi will not
   continue automatically".
5. Text deltas are *not* persisted as events one by one. They are streamed to the UI and folded
   into a single `message.completed` event carrying the final text (BOOK §21: bulky payloads do
   not become context).

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | Captured real stream | the recorded probe (`session`→`agent_settled`) parses into events with no unknown leftovers except what is genuinely unmodelled |
| A2 | Usage fidelity | `input/output/cacheRead/cacheWrite/reasoning/totalTokens` and `cost.*` map to the right `UsageSample` fields, provenance `provider_reported` |
| A3 | Thinking vs text | `thinking_*` and `text_*` are distinguishable and neither is labelled as the other |
| A4 | Unknown record | an invented record type yields `runtime.pi.unknown` with the payload intact |
| A5 | Tool events | a synthetic tool call parses to `tool.started`/`tool.completed` with the tool name and a bounded result preview |
| A6 | No text-delta spam | 500 deltas produce one `message.completed`, not 500 events |

## Expected events

```
message.started / message.completed   { role, chars, thinking: bool }
runtime.pi.thinking                   { summary }        (folded, not per-delta)
tool.started / tool.completed         { tool, args_preview, result_preview, duration_ms }
usage.sampled                         { ...UsageSample fields... }
runtime.pi.settled                    { turn, stop_reason }
runtime.pi.unknown                    { record_type, payload }
```

## Risks

| Risk | Mitigation |
|---|---|
| Treating `usage.cost` as our own estimate | provenance is asserted in A2 |
| Persisting every delta and exploding the log | A6 |

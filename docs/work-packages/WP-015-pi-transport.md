# WP-015 — Pi transport

**Owner:** strong builder · **Wave:** W5 · **Depends on:** WP-005 (ProcessSupervisor), WP-004 (store) · **Blocks:** WP-016, WP-017

---

## Objective

A transport that speaks Pi's RPC framing correctly and nothing more: spawn, write commands,
read records, correlate responses, and never lose or merge a record.

## Allowed files

```
apps/daemon/metaharness/runtimes/pi/transport.py      (new)
tests/unit/runtimes/pi/test_transport.py
tests/fixtures/pi_fake_rpc.py                         (a scripted NDJSON peer, no model)
```

## Forbidden files

```
packages/contracts/**            (frozen)
apps/web/**
docs/protocols/PI_RPC.md         (it is the spec; a correction goes through the Architect)
```

## Required reading

`docs/protocols/PI_RPC.md` §2–§4 · Pi's own `docs/rpc.md` (framing, backpressure, correlation)
· `docs/architecture/PROCESS_SUPERVISION.md`.

## Architecture constraints

1. **Framing is hand-written, not `readline`.** Split only on `LF`; a `U+2028`/`U+2029` inside a
   JSON string must not be treated as a boundary. This is a stated hazard in Pi's docs and a
   test below pins it.
2. **stdout is protocol only.** Anything non-JSON on stdout is a protocol violation: report it
   as a transport error with the offending line, never skip it silently.
3. **stderr is diagnostics**: captured, bounded, attached to the run as an artifact preview.
4. **Correlation by `id`, never by order.** Two commands may be outstanding.
5. **Cancellation goes through `ProcessSupervisor`** and must end with `orphan_check == True`.
6. No `pi --mode text` anywhere: ANSI scraping is forbidden (ADR-0014).

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | Command/response round trip | a scripted peer answers `get_state`; the transport returns its `data` |
| A2 | Correlation under concurrency | two commands outstanding, responses returned out of order, each caller gets its own |
| A3 | Framing edge cases | a record containing `U+2028`, an escaped `\n`, CRLF line endings and a split-across-reads record all parse as one record each |
| A4 | Non-JSON stdout | a stray log line on stdout raises a transport error naming the line, not a silent skip |
| A5 | Backpressure | 10 000 records are drained without stalling the peer and without unbounded memory |
| A6 | Cancel | `cancel()` leaves zero orphans (`orphan_check`) on Linux **and** Windows |
| A7 | Process death | a peer that exits mid-command surfaces a typed error and no hanging future |

## Expected events

```
runtime.pi.spawned      { pid, argv_count, cwd }
runtime.pi.record       { kind: response|event, command? }
runtime.pi.protocol_error { line_preview, reason }
runtime.pi.closed       { reason, orphans: bool }
```

## Windows requirements

Same suite; killing the peer uses the Windows path of `ProcessSupervisor` (no SIGTERM
emulation). A6 runs in CI on both OSes.

## Risks

| Risk | Mitigation |
|---|---|
| A "helpful" line reader that hides the `U+2028` bug | A3 tests it explicitly |
| Treating a `prompt` response as completion | Out of scope here, but the transport returns `disposition` untouched so WP-017 cannot confuse them |

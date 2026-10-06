# WP-018 — Pi conformance

**Owner:** flash-class builder · **Wave:** W5 · **Depends on:** WP-017 · **Blocks:** the M1 gate

---

## Objective

Prove Pi behaves like a runtime rather than like a special case: the same suite every future
adapter must pass, run against Pi on both OSes, with the unsupported parts named instead of
skipped.

## Allowed files

```
tests/conformance/test_pi.py                          (new)
tests/conformance/test_runtime_contract.py            (new: the suite itself, parameterised by adapter)
apps/daemon/metaharness/runtimes/registry.py          (register the fake for the suite)
```

## Forbidden files

```
packages/contracts/**      (the suite lives in tests/; the contract does not grow to fit one runtime)
```

## Required reading

BOOK §69 (runtime conformance) · ADR-0006 · `packages/contracts/metaharness_contracts/conformance.py`
· `docs/protocols/PI_RPC.md`.

## Architecture constraints

1. **The suite is written once and parameterised by adapter.** If a test needs `if runtime ==
   "pi"`, the contract is wrong and the Architect is told.
2. **Unsupported capability returns `UnsupportedCapability`**; the suite asserts that, and never
   counts an unsupported capability as a pass *or* a fail.
3. The suite runs against (a) the `FakeRuntimeAdapter` from the contracts, and (b) the Pi adapter
   with the scripted peer; the real Pi run is opt-in (WP-017 A3).
4. Both OSes in CI, always.

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | probe | returns `RuntimeInfo` with a runtime id and a version |
| A2 | capabilities | every advertised capability is either exercised or explicitly unsupported |
| A3 | session lifecycle | create → send → stream → settled → close leaves no process behind |
| A4 | events | the event stream is ordered, typed and non-empty |
| A5 | cancel | mid-run cancel settles or kills, and reports honestly which happened |
| A6 | usage | tokens are reported; cost is either provider-reported or explicitly unsupported |
| A7 | steering | `steer` is accepted while a run is active, or reported unsupported |
| A8 | compaction | `compact` reports what it did, or is reported unsupported |
| A9 | parity | the same suite passes for the fake adapter, so the suite itself is not Pi-shaped |
| A10 | both OSes | A1–A9 on Linux and Windows CI |

## Expected events

None of its own; the suite asserts on events the adapter emits.

## Risks

| Risk | Mitigation |
|---|---|
| A suite that only passes because it is lenient | A9 runs it against a second, unrelated adapter |
| Opt-in real run mistaken for a gate | the M1 checklist names the real run separately from this suite |

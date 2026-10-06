# WP-020 — Runtime events UI

**Owner:** flash-class builder · **Wave:** W5 · **Depends on:** WP-019 · **Feeds:** the M1 gate

---

## Objective

Make the runtime visible while it runs: tool calls, usage, context pressure and the settled
state, each labelled with where the number came from.

## Allowed files

```
apps/web/src/routes/agent.tsx              (extend the chat surface)
apps/web/src/components/UsagePanel.tsx     (new)
apps/web/src/components/ToolCallList.tsx   (new)
apps/web/src/api.ts                        (usage types)
tests/unit/web/**                          (a DOM-level check of the honest labels)
```

## Forbidden files

```
packages/contracts/**      (UsageSample is frozen)
apps/daemon/metaharness/store/**
```

## Required reading

BOOK §17 (universal usage sample) · §21 (tool output virtualization) · §49 · §82 (display
failures) · `docs/protocols/PI_RPC.md` §6.

## Architecture constraints

1. **Provenance is rendered, not hidden.** A provider-reported cost and a measured duration must
   be distinguishable in the UI. `unknown` is never drawn as `0`.
2. **Tool results are previews.** A large tool output is an artifact reference plus a bounded
   preview; the UI must not fetch and render megabytes into the conversation.
3. **Context pressure is shown only when it is measured.** No gauge with a hardcoded 30 %.
4. **A failed run shows its reason**, taken from the corrective event, not from a client guess.

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | usage panel | input/output/cache/reasoning and cost render separately, each with its provenance label |
| A2 | unknown ≠ zero | a metric with provenance `unknown` renders as unknown |
| A3 | tool calls | a tool call renders name, duration and a bounded preview, with a link to the artifact |
| A4 | settled state | the surface distinguishes "streaming" from "settled" from "cancelled" |
| A5 | no invented gauge | with no usage samples, the panel says so instead of drawing zeroes |

## Expected events

None of its own; it renders what WP-016/017 emit.

## Risks

| Risk | Mitigation |
|---|---|
| A pretty dashboard that lies about unmeasured things | A2 and A5 |
| Rendering a 5 MB tool result inline | A3 asserts the preview bound |

# Topologies

A topology is a small declarative graph that the executor knows how to walk.
Topologies live as YAML in `topologies/` (built-ins) or in
`<hermes-home>/meta-harness/topologies/` (user-extendable).

## Built-ins

| Id | Kind | Nodes | Use case |
|---|---|---|---|
| `solo` | solo | agent | trivial one-shot work |
| `architect-builder` | sequence | architect, builder | medium tasks |
| `fusion` | parallel | alpha, beta, synthesis | independent perspectives |
| `gate-build` | gate | builder, validator | high-assurance |
| `visual-build-review` | sequence | observer, builder, reviewer | UI work |

## Schema

```yaml
name: <id>                  # required, also used as id if `id:` omitted
version: "1"
kind: solo | sequence | parallel | gate

nodes:
  - id: <node_id>           # required
    type: agent | process | gate
    role: <role_id>         # builder, architect, etc.
    engine: hermes | pi     # default inherits the run's engine hint
    model: <model_id>       # optional
    capabilities:           # optional, used for capability lookup
      - validation.test
    config:                 # arbitrary per-node config
      ...

edges:
  - from: <node_id>
    to: <node_id>

failure:                    # optional failure routing
  <node_id>:
    retry:
      target: <node_id>
      max: 3

limits:                     # execution caps
  retries: 3
```

## Solo

```yaml
name: solo
kind: solo
nodes:
  - id: agent
    role: builder
    engine: hermes
```

## Sequence

`architect -> builder`. The executor runs the topo order via Kahn's
algorithm. Prior results are appended to each subsequent node's task as a
compact `[prior]` summary, capped at 4 KiB.

## Parallel

All nodes are started concurrently. Results are merged into the run
context. The runner assumes **single-writer** semantics — concurrent
writers must live in isolated worktrees (see ADR-0009).

## Gate

Two nodes: `builder` + `validator`. The executor runs the builder, then the
validator. If the validator's worker emits `worker.completed`, the gate
passes; otherwise its error is fed back as feedback on the next attempt.
Retries are capped by `limits.retries` (default 3).

## Adding a topology

Drop a YAML file in `<hermes-home>/meta-harness/topologies/`. The next time
the harness boots (or when `POST /character-packs/reload` triggers a
topology rescan), it will be picked up.

```bash
# Restart is not required for new topologies if you trigger a rescan via:
hermes --tools harness_topology_list
# (which already uses the live loader)
```

## Routing

Routing between topologies is a heuristic; the model is free to override
via `harness_run(topology="...")`. Heuristics:

- Single-line edits, single test fix → `solo`
- Code edits with tests → `solo` with validator prompt
- Architecture-heavy task → `architect-builder`
- Visual UI task → `visual-build-review`
- High-risk implementation → `gate-build`
- Explicit comparison → `fusion`

The harness does not invoke an LLM router. Routing is cheap and predictable.

## Limits

The executor enforces:

- retries per topology (`limits.retries`)
- parallel worker cap (`runtime._MAX_PARALLEL = 3`)

These are upper bounds, not targets.
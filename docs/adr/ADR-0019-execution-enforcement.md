# ADR-0019 — Execution Enforcement Model

**Status:** accepted · **Date:** 2026-10-06 · **Milestone:** M3

## Context

M2 ended with a finding that was more useful than the feature it shipped. An agent was asked to work
in its workspace; it walked out of it, found the Meta-Harness repository on the host, and started
working there. Nothing lied: the session had a `workspace` field, and the runtime was launched with
that working directory. A working directory is not a boundary. It is where a well-behaved process
starts.

So M3 has one job: **the control plane must be able to refuse, not only to ask.** An agent may stay
autonomous and intelligent; it may not leave the space, time and budget the control plane granted.

Three things must not be conflated, because conflating them is how a product ends up claiming
protection it does not have:

* what was **requested**,
* what is **effective**,
* and the **evidence** that the two agree.

## Decision

### 1. Three separate records

`RequestedPolicy` is what the caller asked for: workspace, sandbox preference, network, budgets.

`EffectivePolicy` is what the environment actually arranged: which provider, which filesystem mode,
which budgets with which enforcement mechanism.

`EnforcementEvidence` is the measured result: per dimension a level, plus the checks that were run
and their outcomes. Evidence is produced **before** the session starts, by executing probes inside
the prepared environment — not by inspecting the plan. A boundary asserted after the fact is not one.

`weak`, `moderate`, `strong` are the levels, and they are per dimension, not per session. A session
can be `filesystem: strong` and `isolation: weak` at the same time, and it usually is: bubblewrap
mounts are strong containment, an open network is not. One aggregate badge would have to lie about
one of them.

### 2. Budgets say how they are enforced

`max_wall_time`, `max_tool_calls`, `max_tokens`, `max_cost`, `max_child_processes`. Each budget
records `requested`, `observed`, `enforcement_mode`, `limit_reached`, `action`, `provenance`.

The rule that gives this its value: **a limit may not be labelled harder than it is.**

* `wall_time` is **strong** — the supervisor owns the process, so the limit is a kill, and the tree
  is checked for survivors afterwards.
* `tool_calls` and `child_processes` are **moderate** — the count is exact (it is counted from real
  events), but stopping means cancelling a runtime that may already be mid-operation. The record
  says `best effort`, not `hard`.
* `tokens` and `cost` are **weak** — the provider reports usage after the turn, so the limit can only
  be recorded and acted on afterwards. Calling that enforcement would be a lie.

The contract refuses to construct a record where a soft limit claims to be hard, and requires an
action when a limit is reached.

### 3. SandboxProvider, not Docker in the adapter

```
RuntimeAdapter → ExecutionEnvironment → SandboxProvider → ProcessSupervisor
```

The adapter asks for an environment and receives a wrapped command line. It never sees a container
runtime, and a provider never sees a session. Providers, strongest first: `container` (docker/podman),
`namespace` (bubblewrap), `none`.

A provider that cannot be used says **why** (`the daemon is not reachable at /var/run/docker.sock`),
and `auto` degrades to the next one. A missing sandbox never blocks the product: the session runs,
and the record says `weak` with the reason. **A worktree or a scratch directory is never called a
sandbox** — it is a working directory, which is the thing M2 proved is not a boundary.

### 4. What the strong provider actually does

bubblewrap with `--unshare-all`, `--share-net` only when the policy allows it, `/proc` and `/dev`
fresh, `/tmp` an empty private tmpfs, system paths read-only, the workspace the only writable host
path, and a **synthetic HOME** that is a per-session staged copy — never the real one.

The runtime's installation is mounted read-only, and it is *derived*, not hardcoded: the launcher is
read, and the trees it names are bound. That is how `hermes` was found to need
`~/.hermes/tools/python-3.14…` (its interpreter), `~/.hermes/hermes-agent` (its code), and the
committed dependency environment its `installs/<key>/facts.json` names. A home-level data root
(`~/.hermes` itself) is never mounted wholesale.

The runtime's data root is the staged copy, with the install state mirrored into it: the record is
staged with its paths rewritten to the session's own root, and the real environment tree is mounted
read-only at the path the rewritten record names. The environment directory itself is a writable
tmpfs holding read-only binds of its contents, because the runtime writes a lease into it and
mounting it read-only failed with `EROFS` on that first write — while copying 830 MB per session was
the alternative.

### 5. Credentials

A sandboxed runtime must still authenticate, and the honest way to arrange that is to let the runtime
name what it needs. Two mechanisms, both derived from the runtime's own records:

* its own credentials file (`~/.hermes/.env`, staged 0600 into the session's copy), and
* environment-sourced credentials, taken from the pool entries that say `env:NAME` — exactly those
  variables, never the daemon's environment.

Only the **names** are recorded, in `session.policy` as `credential_env`. The values never enter the
event log. Known limitation: a credential passed with `--setenv` is visible in the sandbox's argv to
the same user's processes; on a single-user desktop that is the same trust domain, and the mitigation
(a secrets file the runtime reads) is available for runtimes that support one.

## Consequences

* A session without a policy is still possible and still recorded as `weak`. Absence of a request is
  not evidence of protection.
* `tests/integration/sandbox/` asserts the boundary itself: the repository absent, the real HOME
  files unreadable, the workspace writable, and — for the M2 finding specifically — that with no
  sandbox the repository **is** visible, so the before and after are both facts.
* Where no strong provider exists (Windows CI, a machine without bubblewrap), those tests **skip with
  that exact reason**. `strong sandbox real not verified here` is an acceptable outcome; a simulated
  pass is not.
* The desktop shell keeps its guarantees: the renderer gets no capability and no secret, and the
  session's policy is visible in the Agent Inspector with badges that can say `WEAK`, `SOFT LIMIT`
  and `NOT ENFORCED`.

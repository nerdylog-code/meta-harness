# META-HARNESS V2 — PROJECT BOOK
## Agent Operating Studio — Architecture, Production Plan, Operating Manual & Portfolio Roadmap

**Status:** Master Project Book  
**Codename:** Meta-Harness v2  
**Commercial name:** TBD after working vertical slice  
**Target platforms:** Windows + Linux  
**Primary repository:** `nerdylog-code/meta-harness`  
**Development philosophy:** Local-first, daemon-first, web-first, desktop-capable, plugin-driven, measurable, reversible  
**Intended readers:** Human owner, architect agents, builder agents, reviewers, integrators, future maintainers

---

# 0. HOW TO USE THIS BOOK

This document is the high-level source of truth for the project.

It exists to prevent the project from becoming a collection of disconnected AI experiments.

Every coding agent working on this project should read this document before making architectural decisions.

When this document conflicts with current code:

1. **Code is the operational truth.**
2. **ADRs explain intentional architectural deviations.**
3. **This book defines product direction and architectural intent.**
4. If code and this book diverge unintentionally, create an issue or ADR before changing shared contracts.

Agents must not reinterpret product principles casually.

A builder may implement a defined module.
A reviewer may challenge implementation quality.
An integrator may merge compatible work.
Only the architect role should approve changes to:

- domain contracts;
- security invariants;
- event semantics;
- plugin trust model;
- context architecture;
- permission model;
- canonical runtime adapter interfaces;
- persistent storage semantics;
- cross-platform process model;
- benchmark methodology.

---

# 1. PRODUCT THESIS

Meta-Harness v2 is an **Agent Operating Studio**.

It is a local-first control plane, visual orchestration environment, observability layer, agent factory, runtime abstraction layer, context engine and plugin platform for heterogeneous AI agents.

The product does not try to replace Pi, Hermes, OpenClaw, OMP, LangGraph or future agent runtimes.

Instead, it coordinates them.

The core idea is:

> **An agent has an identity independent of the runtime, model and session used to execute it.**

An agent can therefore preserve:

- name;
- role;
- skills;
- memory;
- preferences;
- context policy;
- permissions;
- workspace access;
- evaluation history;
- heartbeat behavior;
- voice;
- visual identity;
- mission history;

while changing:

- runtime;
- model;
- provider;
- session;
- workspace isolation mechanism;
- tool implementation.

The system should allow the user to create, operate, observe, connect, disconnect, evaluate and evolve AI workers from one visual control plane.

---

# 2. THE PRODUCT IN ONE SENTENCE

> A visual operating system for building, running, observing and evolving AI workers across multiple agent runtimes.

---

# 3. WHAT THE PRODUCT IS NOT

Meta-Harness is not:

- a fork of OpenClaw;
- a fork of Hermes;
- a fork of Pi;
- a fork of OMP;
- just another chat interface;
- an IDE replacement;
- a Jira/Linear replacement;
- a generic workflow canvas with AI branding;
- a swarm launcher that blindly spawns many agents;
- a prompt manager;
- a single-provider product;
- a system where chat history is the database;
- a system where every available tool is inserted into every model call;
- a system where an agent can silently rewrite its trusted production configuration;
- a system where "the model said it finished" counts as proof.

---

# 4. CORE INSPIRATIONS

The product is original, but it intentionally learns principles from strong systems.

## 4.1 OpenClaw

Study and adapt:

- conversation-first New Web UI philosophy;
- Control UI / Gateway separation;
- WebSocket control plane;
- heartbeat;
- lightweight isolated heartbeat context;
- channels;
- WhatsApp;
- voice;
- Ask-system pattern;
- jobs/workboard concepts;
- managed worktrees;
- operator/client architecture.

Do not copy:

- branding;
- exact UI;
- internal data model;
- product-specific assumptions.

## 4.2 Hermes Agent

Study and adapt:

- memory;
- skills;
- self-improvement;
- context compression;
- stable prompt caching strategy;
- model-independent runtime access;
- ACP / JSON-RPC / HTTP-SSE integration;
- multi-agent patterns;
- mixture-of-agents concepts.

Do not make Hermes the host of the new kernel.

Hermes becomes a runtime plugin.

## 4.3 Pi

Study and adapt:

- small harness surface;
- structured RPC;
- extension model;
- customizable compaction;
- embeddability;
- clean runtime adapter boundary.

Pi is an excellent first runtime for the walking skeleton.

## 4.4 OMP / Oh My Pi

Study and adapt:

- tool ergonomics;
- efficient editing protocols;
- tool result discipline;
- agent hub;
- subagents;
- advisor/reviewer models;
- context-aware rules;
- cross-platform native behavior;
- observability;
- structured results.

## 4.5 DeepSeek Harness

Study and adapt:

- deep plugin composition;
- configurable tool/context surface;
- strong separation between mechanism and policy;
- ability to minimize what the model sees.

Do not make the security floor replaceable.

## 4.6 Orca

Study and adapt:

- durable Goal/Mission concept;
- persistent roles;
- deterministic orchestration;
- human approval gates;
- separation between sessions and persistent project state;
- local-first daemon.

## 4.7 Disler experiments

Study and adapt principles from:

- fusion-harness;
- self-compact-pi-agent;
- pi-agent-observability;
- software factories;
- agent sandboxes;
- infinite loops;
- multi-agent observability;
- best-of-N;
- single-writer;
- typed work phases;
- code-owned orchestration.

The important principle is:

> **Code controls sequence, retries and acceptance. Agents do the work that requires judgment.**

---

# 5. NON-NEGOTIABLE PRINCIPLES

1. Agent identity is independent from runtime.
2. Session is disposable.
3. Mission is durable.
4. The daemon is the system of record.
5. The UI is a projection, never the source of truth.
6. Deterministic code controls lifecycle whenever possible.
7. Models are used where judgment is required.
8. Every important state transition produces an event.
9. Context is budgeted.
10. Tools use progressive disclosure.
11. Large tool results become artifacts.
12. Secrets do not enter renderer state.
13. Secrets do not enter model context by accident.
14. Parallel readers are allowed.
15. Parallel writers require isolation.
16. Shared worktree mutation requires a writer lease.
17. Every autonomous run has resource limits.
18. Self-improvement is versioned and reversible.
19. Generated plugins/skills begin untrusted.
20. No efficiency claim without reproducible measurement.
21. Windows and Linux are first-class from the beginning.
22. No core feature may require Bash.
23. No core feature may require WSL.
24. No feature is complete without cancellation behavior.
25. No agent is published without permissions and evals.

---

# 6. TARGET USER EXPERIENCE

The application should feel like one place where AI workers are alive and operational.

Example:

```text
┌─────────────────────────────────────────────────────────────────┐
│ Agent Operating Studio                daemon ●    $1.42    82k │
├───────────────┬─────────────────────────────────┬───────────────┤
│ MISSIONS      │                                 │ ASK / INSPECT │
│               │                                 │               │
│ ● Harness V2  │      Active Workspace           │ "Why did      │
│ ○ Job Hunter  │                                 │ Nova stop?"   │
│               │ Chat / Canvas / Workboard       │               │
│ AGENTS        │ Files / Memory / Sessions       │               │
│               │ Metrics / Timeline / Artifacts  │               │
│ 🟢 Nova / Pi  │                                 │               │
│ 🟡 Atlas/Herm │                                 │               │
│ 🔵 Echo/Open  │                                 │               │
├───────────────┴─────────────────────────────────┴───────────────┤
│ Nova ████  Atlas ██  Echo █████ │ tokens │ cost │ heartbeat    │
└─────────────────────────────────────────────────────────────────┘
```

The central surface can switch between:

- Chat;
- Canvas;
- Workboard;
- Agents;
- Sessions;
- Timeline;
- Files;
- Artifacts;
- Memory;
- RAG;
- Metrics;
- Evaluations.

---

# 7. DOMAIN VOCABULARY

## Mission

A durable operational objective.

A Mission may contain multiple repositories, agents, tasks, sessions, decisions and workflows.

## Agent

A persistent AI worker identity.

## Agent Version

A versioned configuration of an Agent.

Example:

```text
nova:v1
nova:v2
nova:v3
```

## Runtime

The executable harness that runs a Session.

Examples:

- Pi;
- Hermes;
- OpenClaw;
- OMP.

## Session

A disposable interaction/execution context owned by a Runtime.

## Run

A concrete attempt to perform a Task or workflow.

## Task

A bounded unit of work.

## Workspace

A filesystem/repository boundary where work occurs.

## Artifact

A durable output too large or important to live only in model context.

## Skill

Reusable procedural knowledge.

## Memory

Durable semantic knowledge.

## Context Capsule

Structured operational state used to continue work after compaction or runtime migration.

## Plugin

A versioned capability extension.

## Capability

A behavior offered by a plugin/runtime/provider.

## Approval

Explicit authorization for a risky action.

## Event

Canonical record of something that happened.

## Work Graph

Canonical graph representing tasks, agents, dependencies, data flow, permissions and workflows.

---

# 8. HIGH-LEVEL ARCHITECTURE

```text
                           CHANNELS
               Web / WhatsApp / Voice / CLI
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│                      CONTROL PLANE                           │
│                                                              │
│ Mission Registry          Agent Registry                     │
│ Task Graph                Policies                           │
│ Scheduler                 Heartbeats                         │
│ Approvals                 Usage                              │
│ Context Engine            Memory                             │
│ Artifact Store            Event Store                        │
│ Plugin Kernel             Secrets Broker                     │
└───────────────────────┬──────────────────────────────────────┘
                        │
          ┌─────────────┼───────────────┐
          ▼             ▼               ▼
        Pi           Hermes         OpenClaw
          ▼             ▼               ▼
        OMP        LangGraph       Future runtimes
```

---

# 9. OFFICIAL TECHNOLOGY STACK

## 9.1 Backend

Python 3.12+

Core dependencies:

- FastAPI;
- Pydantic v2;
- asyncio;
- AnyIO;
- httpx;
- websockets;
- aiosqlite;
- platformdirs;
- psutil;
- keyring;
- structlog;
- uv.

Avoid a heavyweight ORM in the kernel.

Use explicit SQL and ordered migrations.

## 9.2 Web UI

- React;
- TypeScript;
- Vite;
- TanStack Query;
- TanStack Router;
- React Flow / XYFlow;
- Radix primitives;
- Tailwind.

## 9.3 Desktop

Tauri v2.

The desktop shell launches the daemon and loads the same web application.

## 9.4 Storage

SQLite WAL for canonical state.

Filesystem for large artifacts.

JSONL for export/debug/replay packages, not as a competing source of truth.

---

# 10. CROSS-PLATFORM CONTRACT

Windows and Linux must work without requiring separate product architecture.

## 10.1 Paths

Use `platformdirs`.

Never hardcode:

```text
~/.metaharness
/home/user
C:\Users\...
```

## 10.2 Process execution

Never depend on shell strings for core execution.

Bad:

```python
subprocess.run("cd foo && command", shell=True)
```

Preferred:

```python
exec(["command", "arg"], cwd=workspace)
```

## 10.3 ProcessSupervisor

Canonical interface:

```text
spawn
write_stdin
read_stdout
read_stderr
interrupt
terminate
kill_tree
health
```

Implement OS-specific behavior behind one interface.

### Linux

- process groups;
- SIGINT/SIGTERM;
- SIGKILL fallback.

### Windows

- CREATE_NEW_PROCESS_GROUP;
- psutil child discovery;
- CTRL_BREAK where applicable;
- terminate tree;
- kill fallback.

Acceptance requirement:

> Cancelling a runtime session leaves no orphaned child processes.

---

# 11. REPOSITORY LAYOUT

```text
meta-harness/
├── apps/
│   ├── daemon/
│   ├── web/
│   └── desktop/
├── packages/
│   ├── contracts/
│   ├── plugin-sdk/
│   ├── ui-kit/
│   └── benchmark-spec/
├── plugins/
│   ├── runtimes/
│   │   ├── pi/
│   │   ├── hermes/
│   │   ├── openclaw/
│   │   └── omp/
│   ├── context/
│   ├── memory/
│   ├── rag/
│   ├── sandbox/
│   ├── channels/
│   ├── voice/
│   └── renderers/
├── topologies/
├── roles/
├── character-packs/
├── evals/
├── benchmarks/
├── docs/
├── scripts/
└── tests/
```

---

# 12. PERSISTENCE MODEL

SQLite tables should eventually include:

```text
events
missions
agents
agent_versions
runtimes
sessions
runs
tasks
task_edges
workspaces
workspace_leases
artifacts
approvals
plugins
plugin_versions
usage_samples
context_capsules
memory_items
skills
rag_collections
rag_documents
heartbeat_jobs
eval_runs
benchmark_runs
```

Large artifacts live in filesystem storage.

Every artifact record stores:

- id;
- path;
- SHA-256;
- MIME type;
- size;
- metadata;
- originating mission/run/task/agent.

---

# 13. EVENT MODEL

Every operationally meaningful action must emit a canonical event.

```json
{
  "id": "evt_01J...",
  "seq": 12345,
  "ts": "2026-10-05T20:00:00Z",
  "kind": "tool.completed",
  "mission_id": "mis_...",
  "task_id": "tsk_...",
  "run_id": "run_...",
  "agent_id": "agt_nova",
  "session_id": "ses_...",
  "runtime_id": "pi",
  "correlation_id": "corr_...",
  "causation_id": "evt_...",
  "payload": {},
  "provenance": {}
}
```

Namespaces:

```text
system.*
runtime.*
mission.*
agent.*
task.*
run.*
session.*
message.*
tool.*
approval.*
workspace.*
artifact.*
context.*
memory.*
rag.*
heartbeat.*
usage.*
plugin.*
channel.*
voice.*
eval.*
benchmark.*
```

---

# 14. AGENT MODEL

```yaml
id: nova
display_name: Nova
role: builder

runtime_policy:
  preferred: pi
  fallback:
    - omp
    - hermes

model_policy:
  primary: deepseek-v4.1-flash
  escalation: reasoning-premium

skills:
  - typescript
  - tests

memory_policy:
  mode: project

context_policy:
  engine: capsule-v1

workspace_policy:
  mode: isolated-worktree

heartbeat_policy:
  mode: standard

permissions:
  filesystem: scoped
  git_push: approval
  network: allowlist

character:
  pack: default
  id: builder

voice:
  provider: null
```

---

# 15. RUNTIME ADAPTER CONTRACT

```python
class RuntimeAdapter(Protocol):
    async def probe(self) -> RuntimeInfo: ...
    async def capabilities(self) -> CapabilitySet: ...
    async def models(self) -> list[ModelInfo]: ...
    async def create_session(self, spec: SessionSpec) -> RuntimeSession: ...
    async def send(self, session_id: str, message: Message) -> None: ...
    async def steer(self, session_id: str, instruction: str) -> None: ...
    async def follow_up(self, session_id: str, message: Message) -> None: ...
    async def events(self, session_id: str) -> AsyncIterator[RuntimeEvent]: ...
    async def interrupt(self, session_id: str) -> None: ...
    async def cancel(self, session_id: str) -> None: ...
    async def usage(self, session_id: str) -> UsageSnapshot: ...
    async def artifacts(self, session_id: str) -> list[ArtifactRef]: ...
    async def close(self, session_id: str) -> None: ...
```

Capabilities:

```text
streaming
steering
follow_up
tool_events
usage
approvals
compaction
model_switch
subagents
session_resume
voice
worktrees
```

Unsupported capabilities return explicit `UnsupportedCapability`.

---

# 16. RUNTIME IMPLEMENTATION ORDER

1. Pi
2. Hermes
3. OpenClaw
4. OMP

## Pi

Use structured RPC.

## Hermes

Use supported external JSON-RPC / HTTP-SSE surfaces.
Do not depend on the old host shim.

## OpenClaw

Use the Gateway public/operator protocol.

## OMP

Use structured RPC/client surfaces and progressively add advanced features.

---

# 17. PLUGIN KERNEL

Plugin types:

```text
RuntimeAdapter
ToolProvider
ContextEngine
MemoryProvider
RagProvider
SandboxProvider
WorkspaceProvider
ChannelProvider
VoiceProvider
WorkflowProvider
RendererProvider
MetricsExporter
SkillProvider
ModelProvider
```

Manifest:

```text
id
version
kind
entrypoint
capabilities.provides
capabilities.requires
permissions
events.consumes
events.produces
config_schema
health_check
model_visible_surfaces
unload_semantics
```

Trust:

```text
core
builtin
signed
trusted-user
user
generated
experimental
quarantined
```

---

# 18. KERNEL INVARIANTS

Not replaceable by plugins:

- identity;
- event ordering;
- audit trail;
- permission floor;
- secret isolation;
- redaction;
- resource budgets;
- plugin trust;
- approval enforcement.

---

# 19. CONTEXT ENGINE

Context is budgeted:

```text
Context Budget
├── Stable System
├── Agent Identity
├── Active Policies
├── Skills
├── Mission State
├── Recent Conversation
├── Memory
├── RAG
├── Tool Schemas
└── Output Reserve
```

When pressure rises:

1. reduce retrieval;
2. prune tool results;
3. remove unused schemas;
4. compact older state;
5. preserve active operational state.

Never truncate randomly.

---

# 20. PROGRESSIVE TOOL DISCLOSURE

Initial surface should be small:

```text
capabilities.search
artifact.read
workspace.inspect
task.inspect
task.report
```

Tools appear when needed.

This reduces structural prompt overhead.

---

# 21. TOOL OUTPUT VIRTUALIZATION

Large outputs become artifacts.

Model sees summary + preview + Artifact ID.

Full data remains retrievable by range.

Mandatory for large logs, diffs, datasets and retrieved documents.

---

# 22. CONTEXT CAPSULE

Pressure phases:

```text
NORMAL
NOTICE
PREPARE
FORCED
COMPACTING
RESUMED
```

Capsule schema includes:

```text
objective
current_phase
completed
next_actions
invariants
decisions
open_questions
blockers
active_files
artifacts
tests
memory_candidates
workspace_state
resume_instruction
self_assessment
```

The agent writes its own operational handoff while it still understands the task.

---

# 23. CAPSULE VERIFICATION

```text
Agent Capsule
 ↓
Schema validation
 ↓
ID validation
 ↓
File/artifact validation
 ↓
Open-task reconciliation
 ↓
Decision reconciliation
 ↓
Optional reviewer
 ↓
Accept / repair
 ↓
Compact
```

Raw history stays archived.

---

# 24. CONTEXT LAYERS

```text
L0 Hot Context
L1 Context Capsule
L2 Semantic Ledger
L3 Memory
L4 Skills
L5 RAG
L6 Raw Archive
```

---

# 25. HEARTBEAT SYSTEM

## Runtime Healthbeat

Deterministic.

## Task Lease Heartbeat

Deterministic.

## Agent Attention Heartbeat

May invoke cheap model, but receives isolated light context only.

No full transcript by default.

---

# 26. WORKSPACE ISOLATION

```text
L0 Direct
L1 Scoped Workspace
L2 Git Worktree
L3 Container / VM
```

Linux supports Docker/Podman.

Windows supports Docker Desktop/WSL2-backed containers when present.

Containers are optional.

Worktree remains default coding isolation.

---

# 27. WORKSPACE POLICY

```yaml
include:
  - src/auth/**
  - packages/contracts/**

write:
  - src/auth/**

readonly:
  - packages/contracts/**

deny:
  - infra/**
  - .env
  - secrets/**

network:
  mode: allowlist
```

Enforcement is labeled:

```text
weak
moderate
strong
```

---

# 28. SINGLE-WRITER RULE

One checkout, one writer.

Parallel agents receive isolated worktrees.

Shared mutation requires a WriterLease.

---

# 29. MISSION WORK GRAPH

Node types:

```text
task
agent
human
gate
runtime
tool
workspace
rag
artifact
workflow
```

Edge types:

```text
dependency
control
message
data
artifact
permission
workspace
review
```

---

# 30. WORKBOARD

Kanban projection:

```text
BACKLOG
READY
RUNNING
WAITING
REVIEW
BLOCKED
DONE
```

Card metadata:

- agent;
- runtime;
- model;
- heartbeat;
- context;
- tokens;
- cost;
- workspace;
- proof;
- retry count.

---

# 31. CANVAS

Visual projection/editor of the Work Graph.

Live activity represents real events.

Removing an edge changes future routing or permissions at a safe boundary.

Canvas is not an independent source of truth.

---

# 32. ORCHESTRATION TOPOLOGIES

```text
solo
sequence
parallel
gate
retry
planner-builder
architect-builder
builder-reviewer
fan-out
best-of-n
debate
fusion
swarm
factory-dag
human-approval
conditional-router
```

Topologies are data-driven.

---

# 33. MODEL FUSION

Patterns:

```text
Opinion
Debate
Fusion
Architect/Builders
```

Source agents should often be read-only.

One designated writer/integrator owns mutation.

---

# 34. SWARM

Swarm is bounded fan-out.

Per-child:

```text
task
scope
budget
workspace
tools
deadline
output schema
parent
```

Global caps:

```text
max_depth
max_children
max_active
max_tokens
max_cost
max_wall_time
```

No unbounded recursion.

---

# 35. AGENT FACTORY

Input:

> Create an API security reviewer.

Output:

`AgentBlueprint`

Required:

```text
identity
purpose
responsibilities
non_responsibilities
role_prompt
runtime_policy
model_policy
escalation_policy
tools
skills
context_policy
memory_policy
rag_policy
workspace_policy
network_policy
secret_policy
approval_policy
heartbeat_policy
voice_policy
channel_policy
budgets
eval_suite
failure_policy
```

No publication without permissions, workspace policy, model policy and evals.

---

# 36. GRILLME

GrillMe evolves into a workflow:

```text
Intent
→ Interview
→ Risk analysis
→ Tool needs
→ Workspace needs
→ Runtime/model recommendation
→ Prompt
→ Skills
→ Evals
→ Blueprint
→ Benchmark
→ Publish
```

---

# 37. SELF-IMPROVEMENT

```text
Experience
→ Candidate
→ Evidence
→ Proposed version
→ Regression eval
→ Benchmark
→ Staging
→ Risk gate
→ Promotion
→ Monitoring
→ Rollback
```

No silent live prompt mutation.

---

# 38. ASK META-HARNESS

Custodian/system agent.

Initial tools:

```text
system.search
system.inspect
system.explain
system.propose
```

It retrieves state progressively instead of receiving everything at once.

---

# 39. ACTION PROPOSALS

Risky actions become typed proposals.

Example:

```text
Move Nova: Pi → Hermes

Impact:
Current session closes.
New session starts.
Context Capsule migrates.

Risk:
R1

Reversible:
Yes
```

---

# 40. RISK LEVELS

```text
R0 Read-only
R1 Reversible local
R2 Workspace mutation
R3 External side effect
R4 Critical/destructive/financial/credential/production
```

R4 approval is mandatory and cannot be disabled by plugins.

---

# 41. SECRETS BROKER

Providers:

```text
os-keychain
encrypted-local-vault
environment-readonly
```

Windows: Credential Manager.

Linux: Secret Service where available.

Never expose provider keys to renderer.

---

# 42. CHANNELS

Plugin contract for:

```text
Web
WhatsApp
Telegram
Discord
Slack
CLI
Webhook
```

Inbound messages normalize to `ChannelMessage`.

---

# 43. WHATSAPP STRATEGY

Stage 1:

Use OpenClaw as a channel bridge.

Stage 2:

Optional native WhatsApp plugin.

Channel implementation must not change the kernel.

---

# 44. VOICE

```text
STT
Realtime Voice
Full Agent
TTS
Channel
```

Simple conversational turns stay fast.

Deep/tool/memory questions consult the full agent.

---

# 45. RAG LAB

```text
Source
→ Parser
→ Chunker
→ Embedding
→ Index
→ Retriever
→ Reranker
→ Agent
```

Initial local-first solution:

- SQLite FTS5;
- lightweight embedding store.

Future providers are plugins.

---

# 46. RAG EVAL

Measure:

```text
retrieval recall
groundedness
citation correctness
context tokens
latency
cost
```

---

# 47. LANGGRAPH

LangGraph is a WorkflowProvider.

It does not become the core engine.

---

# 48. TRAINING LAB

Manages:

```text
datasets
trajectories
preference pairs
graders
evals
exports
training jobs
model registry
```

Training providers remain plugins.

---

# 49. UNIVERSAL USAGE SAMPLE

```text
input_tokens
output_tokens
reasoning_tokens
cache_read_tokens
cache_write_tokens
system_tokens
tool_schema_tokens
retrieval_tokens
tool_result_tokens
context_tokens
context_limit
compaction_input
compaction_output
provider_cost
estimated_cost
ttft_ms
duration_ms
tool_duration_ms
retries
provider
model
runtime
agent
session
run
task
```

Provenance:

```text
provider_reported
runtime_reported
measured
estimated
unknown
```

---

# 50. OBSERVABILITY

Views:

```text
Single Agent
Swimlane
Mission Timeline
Race
Cost
Context
```

---

# 51. HARNESSBENCH

Controlled comparison:

```text
same task
same repository snapshot
same provider/model
same prompt
same tool policy where possible
same acceptance test
multiple trials
```

Targets:

```text
Pi
Hermes
OpenClaw
OMP
Meta-Harness
```

Primary metrics:

```text
tokens / accepted result
cost / accepted result
wall time / accepted result
human interventions / accepted result
```

---

# 52. CONTEXT RETENTION BENCHMARK

Measure after compaction:

```text
identifier_retention
decision_retention
open_task_retention
constraint_retention
resume_success
```

---

# 53. UI INFORMATION ARCHITECTURE

```text
NEW MISSION

MISSIONS
AGENTS
LABS
SYSTEM
```

Mission surfaces:

```text
Chat
Canvas
Workboard
Agents
Sessions
Timeline
Files
Artifacts
Memory
Metrics
```

Right panel:

```text
Ask
Selection
Activity
Approvals
```

---

# 54. AGENT DETAIL

Display:

```text
Status
Runtime
Model
Mission
Task
Workspace
Context %
Cache %
Tokens
Cost
Heartbeat
```

Actions:

```text
Chat
Steer
Pause
Move Runtime
Inspect Context
Open Workspace
```

---

# 55. CHARACTER / OFFICE

Existing character packs become `RendererProvider`s.

Possible renderers:

```text
Professional
Pixel Office
Minimal Network
JRPG-inspired original
Cyber
```

Assets only. No executable pack code.

---

# 56. CANVAS LIVE ACTIVITY

Real event animation:

```text
Agent → Tool
Agent → Agent
RAG → Agent
Agent → Artifact
Validator → Task
```

Can display token amount, latency, failure and blocked state.

---

# 57. API

HTTP JSON for commands/queries.

WebSocket for events.

Conceptual endpoints:

```text
/v1/missions
/v1/agents
/v1/tasks
/v1/sessions
/v1/runs
/v1/artifacts
/v1/plugins
/v1/runtimes
/v1/usage
/v1/context
/v1/approvals
/v1/benchmarks
/v1/events/ws
```

---

# 58. LOCAL AUTH

Default daemon bind:

```text
127.0.0.1
```

Per-launch secret.

Remote access requires explicit future configuration.

---

# 59. PACKAGING

Development canonical commands should converge toward:

```text
python scripts/dev.py
python scripts/test.py
python scripts/doctor.py
python scripts/package.py
```

No mandatory Bash.

Production:

- packaged Python daemon;
- Tauri desktop shell.

Windows:
- MSI/NSIS.

Linux:
- AppImage/deb.

---

# 60. CI

Required:

```text
ubuntu-latest
windows-latest
```

Jobs:

```text
python lint
python tests
typescript typecheck
web tests
contract tests
runtime mock tests
package smoke
```

No merge with Windows red.

---

# 61. RUNTIME CONFORMANCE

Every adapter:

```text
probe
create session
send
stream
cancel
close
tool event if supported
usage if supported
steering if supported
compaction if supported
approval if supported
```

---

# 62. PLUGIN CONFORMANCE

```text
install
load
health
disable
reload
config migration
crash isolation
unload
rollback
```

---

# 63. THREAT MODEL

Must explicitly address:

```text
prompt injection
malicious RAG
malicious skill
malicious plugin
tool-result injection
secret exfiltration
workspace escape
symlink escape
shell injection
runaway swarm
runaway cost
stale approval
agent spoofing
event spoofing
artifact tampering
runtime impersonation
```

---

# 64. DEVELOPMENT ROLES

## Architect

Owns:

- contracts;
- ADRs;
- security boundaries;
- context architecture;
- shared interfaces;
- architecture conflict resolution.

## Builder

Implements scoped work.

## Specialist

Handles targeted technical areas.

## Reviewer

Independent quality/architecture check.

## Integrator

Merges compatible work; escalates contract conflicts.

---

# 65. MODEL ALLOCATION

Use strongest reasoning for:

```text
domain redesign
security
Context Capsule architecture
permissions
plugin trust
event semantics
migration strategy
benchmark methodology
```

Use cheap/fast models for:

```text
components
CRUD
CSS
fixtures
simple migrations
adapter mapping
mechanical tests
routine docs
```

---

# 66. PARALLEL DEVELOPMENT

Recommended 3–5 builders per wave.

Flow:

```text
Architect spec
→ Builders
→ Automated gates
→ Reviewers
→ Integrator
→ Architect only for contract conflicts
```

---

# 67. WORK PACKAGE FORMAT

Every WP must contain:

```text
ID
Title
Objective
Dependencies
Allowed files
Forbidden files
Required reading
Architecture constraints
Acceptance tests
Expected output
Expected events
Windows requirements
Linux requirements
Known risks
```

Worker report:

```text
STATUS
FILES CHANGED
DECISIONS
TESTS RUN
RESULTS
KNOWN LIMITATIONS
ARCHITECTURE DEVIATIONS
FOLLOW-UPS
```

---

# 68. MASTER ROADMAP

## PHASE 0 — Freeze V1

Tasks:

- tag `v0.1-hermes-hosted`;
- branch `v2/control-plane`;
- inventory;
- keep/adapt/replace matrix;
- clean accidental caches;
- verify tests.

Expected result:
Safe migration from existing MVP.

## PHASE 1 — Cross-platform Skeleton

Build daemon/web/desktop/contracts.

Gate:
Windows + Linux CI green.

Expected:
Real application shell.

## PHASE 2 — Contracts + Event Store

Build core entities, migrations and projections.

Gate:
restart + replay correctness.

Expected:
Runtime-neutral backbone.

## PHASE 3 — ProcessSupervisor

Build safe process lifecycle.

Gate:
zero orphan children on cancel.

Expected:
Reusable runtime foundation.

## PHASE 4 — Pi Walking Skeleton

UI → daemon → Pi → events → storage → UI.

Gate:
real task completes.

Expected:
First usable vertical slice.

## PHASE 5 — Context Engine V1

Budget, artifacts, Capsule, verifier, compaction.

Gate:
task survives two compactions.

Expected:
Engineered long-running context.

## PHASE 6 — Mission + Task Graph

Gate:
plan → build → test → review workflow completes.

Expected:
Real orchestration.

## PHASE 7 — Worktrees + Workspace Policy

Gate:
parallel writers isolated.

Expected:
Safe multi-agent coding.

## PHASE 8 — Control UI V1

Gate:
normal usage without terminal.

Expected:
Product-quality interaction.

## PHASE 9 — Workboard

Gate:
board reflects canonical task state.

Expected:
Operational visibility.

## PHASE 10 — Canvas

Gate:
visual Architect → Builder → Reviewer flow executes.

Expected:
Visual orchestration.

## PHASE 11 — Hermes Adapter

Gate:
runtime conformance.

Expected:
Agent can migrate Pi ↔ Hermes.

## PHASE 12 — OpenClaw Adapter

Gate:
discover/session/send/stream/cancel/approval.

Expected:
OpenClaw managed from Meta-Harness.

## PHASE 13 — OMP Adapter

Gate:
runtime conformance.

Expected:
High-performance coding runtime available.

## PHASE 14 — Multi-runtime Missions

Gate:
Pi/Hermes/OpenClaw/OMP coexist in same Mission.

Expected:
Central thesis proven.

## PHASE 15 — Heartbeat

Gate:
agent heartbeat uses light isolated context.

Expected:
Persistent awareness at controlled cost.

## PHASE 16 — Ask Meta-Harness

Gate:
explains work/failures/cost/blockers/context.

Expected:
Conversational control plane.

## PHASE 17 — Fusion

Gate:
2–5 models with usage lineage.

Expected:
Combined model strengths.

## PHASE 18 — Swarm / Factory

Gate:
hard budgets enforced.

Expected:
Safe scale-out.

## PHASE 19 — Agent Factory + GrillMe

Gate:
description → blueprint → eval → publish.

Expected:
Agent manufacturing system.

## PHASE 20 — Self-improvement

Gate:
bad candidate fails regression and cannot promote.

Expected:
Controlled learning.

## PHASE 21 — Channels

Gate:
WhatsApp round-trip to correct Agent identity.

Expected:
Daily communication integration.

## PHASE 22 — Voice

Gate:
simple query local/realtime; deep query consults full Agent.

Expected:
Natural low-latency interaction.

## PHASE 23 — RAG Lab

Gate:
Task access restricted to permitted collection.

Expected:
Measurable knowledge layer.

## PHASE 24 — Workflow Lab / LangGraph

Gate:
LangGraph workflow runs without kernel changes.

Expected:
Workflow ecosystem extensibility.

## PHASE 25 — HarnessBench

Gate:
reproducible comparison report.

Expected:
Evidence-driven efficiency claims.

## PHASE 26 — Training Lab

Gate:
provider-independent core.

Expected:
Future model improvement experimentation.

## PHASE 27 — Desktop Packaging

Gate:
clean Windows/Ubuntu install without Python/Node.

Expected:
Distributable software.

## PHASE 28 — Security Hardening

Gate:
security suite green.

Expected:
Credible beyond demos.

## PHASE 29 — Portfolio Productization

Gate:
README, diagrams, benchmark, website, video, case study.

Expected:
Portfolio flagship project.

---

# 69. PORTFOLIO MVP

Must include:

```text
Windows + Linux
daemon
Web UI
Tauri desktop
Agents
Missions
Tasks
Sessions
Pi
Hermes
OpenClaw
OMP
Workboard
Canvas
worktrees
Context Capsules
heartbeats
Ask Meta-Harness
usage/cost
Fusion
basic Agent Factory
one WhatsApp path
one Voice path
small HarnessBench
```

---

# 70. V1 PRODUCT

Adds:

```text
full plugin SDK
self-improvement
sandbox providers
RAG Lab
Workflow Lab
LangGraph
Eval Lab
Training Lab
multi-channel
voice providers
remote daemon
plugin registry
advanced HarnessBench
```

---

# 71. POSTPONED

Do not start early:

```text
Kubernetes
distributed cluster
cloud SaaS
custom vector DB
own LLM inference
full IDE
custom source control
native mobile
plugin marketplace
recursive self-rewrite
```

---

# 72. FIRST ENGINEERING WAVE

## WP-001 — Freeze V1
## WP-002 — V2 Repository Skeleton
## WP-003 — Contracts Foundation
## WP-004 — Event Store V2
## WP-005 — Cross-platform ProcessSupervisor
## WP-006 — Web Skeleton
## WP-007 — Tauri Skeleton

Do not begin Pi adapter until WP-003, WP-004 and WP-005 are green.

---

# 73. SECOND WAVE

```text
WP-008 Mission persistence
WP-009 Agent registry
WP-010 Session registry
WP-011 UsageSample
WP-012 Artifact store
WP-013 Approval model
WP-014 WebSocket projections
```

---

# 74. THIRD WAVE

```text
WP-015 Pi transport
WP-016 Pi parser
WP-017 Pi RuntimeAdapter
WP-018 Pi conformance
WP-019 Agent Chat UI
WP-020 Runtime Events UI
```

---

# 75. MILESTONE M1 — LIVING AGENT

User story:

1. Launch on Windows/Linux.
2. Create Nova.
3. Runtime = Pi.
4. Pick model.
5. Start Session.
6. Send message.
7. Watch streaming.
8. Watch tool.
9. Watch usage.
10. Cancel.
11. Close.
12. Reopen.
13. Nova persists.
14. Mission persists.

---

# 76. MILESTONE M2 — RUNTIME MIGRATION

```text
Nova / Pi
→ Context Capsule
→ Nova / Hermes
```

Agent identity remains unchanged.

This is the strongest proof of architecture.

---

# 77. MILESTONE M3 — MULTI-RUNTIME TEAM

```text
Nova / Pi
Atlas / Hermes
Echo / OpenClaw
Iris / OMP
```

One Mission.

One Work Graph.

One Canvas.

One observability plane.

---

# 78. EXPECTED FUTURE OUTCOMES

## Technical

Runtime-neutral control plane.

## Product

One coherent environment for heterogeneous agents.

## Efficiency

Reduced waste through:

- progressive tools;
- artifact virtualization;
- caching-friendly structure;
- Context Capsules;
- light heartbeat;
- retrieval.

## Reliability

Improved through:

- deterministic orchestration;
- acceptance gates;
- retries;
- task state;
- isolation;
- bounded autonomy.

## Safety

Versioned, evaluated, reversible improvement.

## Portfolio

Demonstrates:

- Python;
- APIs;
- WebSockets;
- desktop packaging;
- React/TypeScript;
- agent engineering;
- automation;
- observability;
- security;
- RAG;
- evaluation;
- cross-platform engineering;
- product architecture.

---

# 79. SUCCESS METRICS

```text
Mission success rate
First-pass task success
Tokens per accepted task
Cost per accepted task
Median task time
Human interventions per mission
Context retention
Runtime failure rate
Tool failure rate
Retry rate
Heartbeat cost
Runtime migration success
Worktree collision rate
```

---

# 80. CODE VS MODEL POLICY

Use code for deterministic operations.

Use models for semantic judgment.

Examples of code:

```text
tests
schema validation
hashes
leases
task state
budgets
```

Examples of model judgment:

```text
planning
synthesis
semantic review
conflict resolution
context summarization
```

---

# 81. ACCEPTANCE GATES

Every phase has a measurable gate.

A response saying "done" is never the gate.

---

# 82. FAILURE PHILOSOPHY

Display explicit failures.

Never hide:

```text
runtime unavailable
unsupported capability
budget exceeded
approval required
context degraded
weak isolation
estimated usage
partial tool failure
```

No fake green.

---

# 83. RECOVERY

On startup:

```text
reconcile active sessions
check real processes
release stale leases
mark orphans
preserve artifacts
offer mission recovery
```

Never blindly resume dangerous side effects.

---

# 84. MIGRATIONS

Migrations are ordered and tested.

Never casually rewrite migration history.

---

# 85. SECURITY CHECKLIST

Before V1:

```text
secret isolation
path normalization
symlink escape tests
safe process args
plugin permissions
RAG injection defenses
approval binding
network policies
cost caps
swarm limits
event provenance
artifact hashing
```

---

# 86. DOCUMENTATION

Required:

```text
PROJECT_BOOK.md
PROJECT_STATE.md
docs/architecture/ARCHITECTURE.md
docs/architecture/DOMAIN.md
docs/architecture/EVENTS.md
docs/architecture/PLUGINS.md
docs/architecture/CONTEXT.md
docs/architecture/SECURITY.md
docs/architecture/RUNTIMES.md
docs/architecture/CROSS_PLATFORM.md
docs/architecture/BENCHMARKING.md
docs/adr/
```

---

# 87. AGENT STARTUP INSTRUCTION

> Read PROJECT_BOOK.md, PROJECT_STATE.md, relevant ADRs and your Work Package before coding. Do not redesign shared contracts unless explicitly authorized. If implementation requires violating an invariant, stop and report the conflict instead of silently changing architecture.

---

# 88. BUILDER RULES

Builders must:

1. stay in scope;
2. run specified tests;
3. preserve Windows/Linux;
4. emit required events;
5. implement cancellation;
6. report limitations;
7. avoid unnecessary dependencies;
8. avoid architecture changes;
9. avoid premature abstractions;
10. return structured reports.

---

# 89. REVIEWER RULES

Check:

```text
requirements
architecture
Windows/Linux
cancellation
errors
race conditions
security
tests
token/context impact
dependency inflation
observability
```

Severity:

```text
P0 critical
P1 blocking
P2 important
P3 improvement
```

---

# 90. INTEGRATOR RULES

Integrator:

- merges compatible work;
- runs gates;
- does not rewrite for taste;
- escalates contract conflicts;
- preserves history.

---

# 91. ARCHITECT DECISION FORMAT

```text
DECISION
CHOICE
ALTERNATIVES
WHY
REVERSIBILITY
EVIDENCE
RISKS
HOW TO VALIDATE
```

---

# 92. BENCHMARK DISCIPLINE

Never call one harness more efficient from anecdotal runs.

Use controlled, repeatable trials.

---

# 93. COMMERCIAL NAMING

Keep `Meta-Harness v2` as technical codename until M1.

Naming criteria:

- original;
- pronounceable;
- non-generic;
- package/domain viable;
- represents agency/coordination/control;
- not tied to one runtime.

---

# 94. PORTFOLIO STORY

Not:

> I made another AI coding agent.

Instead:

> I built a local-first control plane for heterogeneous AI agents that separates persistent identity from runtime execution and adds visual orchestration, work isolation, context engineering, observability, model fusion, agent generation and reproducible efficiency measurement.

---

# 95. HERO DEMO

Show:

1. Windows/Linux launch.
2. Mission opens.
3. Four named Agents.
4. Four runtimes.
5. Architect creates DAG.
6. Builders receive worktrees.
7. Canvas animates.
8. Workboard updates.
9. One agent reaches pressure.
10. Capsule generated.
11. Context compacts.
12. Agent continues.
13. Reviewer blocks.
14. Builder fixes.
15. Usage compared.
16. Ask explains.
17. WhatsApp interacts.
18. Voice interacts.
19. Mission completes with proof.

---

# 96. LONG-TERM OPTIONS

Only after local product works:

```text
remote workers
collaboration
shared missions
enterprise policies
plugin registry
encrypted remote control
standard runtime protocol
hosted benchmarks
agent marketplace
mission templates
organization Agent Factory
governance dashboards
```

---

# 97. FINAL BUILD ORDER

```text
1 Freeze
2 Skeleton
3 Contracts
4 Event store
5 Process supervisor
6 Pi slice
7 Context engine
8 Mission/Task Graph
9 Worktrees
10 Control UI
11 Workboard
12 Canvas
13 Hermes
14 OpenClaw
15 OMP
16 Multi-runtime
17 Heartbeats
18 Ask
19 Fusion
20 Swarm
21 Agent Factory
22 Self-improvement
23 Channels
24 Voice
25 RAG
26 LangGraph
27 HarnessBench
28 Training Lab
29 Packaging
30 Security
31 Portfolio
```

---

# 98. ANTI-FAILURE RULE

Never build the whole vision at once.

Priority:

```text
working
↓
observable
↓
reliable
↓
efficient
↓
extensible
↓
beautiful
↓
broad
```

Always maintain a working vertical slice.

---

# 99. FIRST PROMPT TO THE IMPLEMENTATION ORCHESTRATOR

```text
Read PROJECT_BOOK.md completely.

Then inspect the current repository and PROJECT_STATE.md.

Do not code yet.

Produce:
1. V1 inventory.
2. Keep/adapt/replace matrix.
3. Proposed tag and branch operations.
4. WP-001 through WP-007 as executable Work Packages.
5. Dependency DAG.
6. Exact acceptance commands for Windows and Linux.
7. Conflicts between current code and PROJECT_BOOK.md.

Do not redesign the project.
Do not begin feature implementation before WP-001 is complete.
```

---

# 100. FINAL ARCHITECTURAL STATEMENT

The project succeeds if Meta-Harness becomes the durable operating layer above AI runtimes rather than another runtime competing with them.

The user should be able to:

- create an AI worker;
- give it identity;
- give it knowledge;
- give it skills;
- choose its runtime;
- choose its model;
- choose its tools;
- limit its workspace;
- limit its budget;
- connect it to other agents;
- disconnect capabilities visually;
- observe every meaningful action;
- move it between runtimes;
- preserve operational state;
- evaluate its quality;
- improve it safely;
- communicate through chat, channels and voice;
- compare efficiency;
- orchestrate teams and factories;
- keep the system understandable.

The central concept is not a chatbot.

It is not a model.

It is not one harness.

It is the **operating layer for AI workers**.

---

# APPENDIX A — INITIAL ADRS

```text
ADR-0001 Meta-Harness becomes independent control plane
ADR-0002 Agent identity is runtime-independent
ADR-0003 SQLite is canonical state/event source
ADR-0004 Web-first UI + Tauri shell
ADR-0005 Python daemon
ADR-0006 RuntimeAdapter v2
ADR-0007 Progressive tool disclosure
ADR-0008 Tool output virtualization
ADR-0009 Context Capsule architecture
ADR-0010 Single-writer invariant
ADR-0011 Plugin safety floor
ADR-0012 Work Graph is canonical
ADR-0013 Windows + Linux first-class
ADR-0014 Structured protocol over ANSI scraping
ADR-0015 Self-improvement requires staging/eval
```

---

# APPENDIX B — INITIAL CONTRACTS

```text
AgentId
MissionId
TaskId
RunId
SessionId
ArtifactId
PluginId
RuntimeId

AgentSpec
AgentVersion
MissionSpec
TaskSpec
WorkspacePolicy
SessionSpec
RuntimeInfo
CapabilitySet
RuntimeEvent
CanonicalEvent
UsageSample
ArtifactRef
ContextCapsule
ApprovalRequest
ActionProposal
PluginManifest
```

---

# APPENDIX C — TARGET COMMAND SURFACE

```text
python scripts/dev.py
python scripts/test.py
python scripts/doctor.py
python scripts/package.py
```

These must work from PowerShell and POSIX environments.

---

# APPENDIX D — DEFINITION OF DONE

A feature is done only when it has:

- implementation;
- tests;
- integration path;
- Windows behavior;
- Linux behavior;
- error behavior;
- cancellation behavior;
- events;
- observability;
- docs;
- resource accounting when applicable.

Model features also require:

- token measurement;
- cost measurement;
- retry measurement;
- evaluation.

---

# APPENDIX E — ESCALATION TRIGGERS

Escalate to Architect when:

- shared contract must change;
- migration is destructive;
- plugin needs new permissions;
- Windows requires different domain semantics;
- runtime lacks required capability;
- Capsule loses required state;
- isolation cannot be enforced;
- renderer needs raw credentials;
- swarm exceeds budget assumptions;
- model output cannot be validated;
- benchmark comparability breaks.

---

# APPENDIX F — PORTFOLIO DELIVERABLES

Repository:

```text
README.md
PROJECT_BOOK.md
PROJECT_STATE.md
docs/architecture/
docs/adr/
docs/security/
benchmarks/results/
demo/
images/
```

Public:

- architecture overview;
- runtime comparison;
- Context Engine article;
- Agent Factory demo;
- Canvas demo;
- HarnessBench report;
- Windows/Linux install demo;
- product demo video;
- technical walkthrough;
- case study.

---

# APPENDIX G — FUTURE RESEARCH BACKLOG

```text
standardized Agent Runtime Protocol
remote workers
encrypted peer-to-peer control
strong Windows sandboxing
local embedding optimization
retrieval cache
semantic diffs
learned routing
evaluator ensembles
model portfolio optimization
adaptive context budgets
automatic skill discovery
safe policy evolution
benchmark generation
long-horizon Missions
multi-user collaboration
organization policies
reproducible sandbox images
```

---

# APPENDIX H — ARCHITECT GRILL QUESTIONS

Before approving a new subsystem, ask:

1. Does deterministic code solve this better than a model?
2. Does this require persistent state or only transient UI state?
3. Is the daemon still the source of truth?
4. Does this increase model-visible context?
5. Can the capability use progressive disclosure?
6. Can large output become an Artifact?
7. What happens on cancellation?
8. What happens after a crash?
9. What happens on Windows?
10. What happens on Linux?
11. Does this create a second source of truth?
12. Does it add vendor lock-in?
13. Does it create a security boundary or merely a policy boundary?
14. Can it be rolled back?
15. How is it measured?
16. What is the acceptance gate?
17. What happens if a runtime does not support it?
18. Does this require a new plugin type?
19. Is this needed before M1/M2/M3?
20. Are we building product value or infrastructure for hypothetical future value?

---

# APPENDIX I — PRODUCT MILESTONE CHECKLIST

## M1 — Living Agent

- [ ] Windows launch
- [ ] Linux launch
- [ ] Agent create
- [ ] Pi session
- [ ] streaming
- [ ] tools
- [ ] usage
- [ ] cancellation
- [ ] persistence
- [ ] restart recovery

## M2 — Runtime Migration

- [ ] Capsule
- [ ] Pi close
- [ ] Hermes create
- [ ] identity preserved
- [ ] task continuation
- [ ] event lineage preserved
- [ ] usage separated by runtime

## M3 — Multi-runtime Team

- [ ] Pi
- [ ] Hermes
- [ ] OpenClaw
- [ ] OMP
- [ ] one Mission
- [ ] Workboard
- [ ] Canvas
- [ ] shared metrics
- [ ] isolated workspaces
- [ ] approvals

---

# APPENDIX J — PORTFOLIO RESULTS WE WANT TO BE ABLE TO PROVE

At the end of the project we want evidence-backed statements such as:

> Meta-Harness can run named persistent agents across four heterogeneous runtimes.

> An agent can migrate between runtimes while preserving mission identity and structured operational context.

> Parallel coding agents operate in isolated worktrees with zero shared-tree write collisions.

> Context compaction preserves required task IDs, decisions and unresolved work at a measured retention score.

> Heartbeat uses bounded lightweight context rather than replaying the full session.

> HarnessBench can compare runtime configurations under controlled tasks using accepted-result metrics.

> Self-improvement candidates cannot reach trusted production configuration without regression evaluation and promotion.

> The same product runs on Windows and Linux from one architecture.

These claims must be demonstrated by tests, benchmark artifacts or recorded demos — never just documentation.

---

**END OF PROJECT BOOK**

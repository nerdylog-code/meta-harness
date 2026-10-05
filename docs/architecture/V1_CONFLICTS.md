# V1 ↔ PROJECT_BOOK conflicts

**Purpose:** list every place where the v1 repository (`nerdylog-code/meta-harness @ 3ef4a5c`) and the META-HARNESS V2 PROJECT BOOK disagree, or where the Book is silent on something the code has already decided.

**Rule applied:** code is the operational truth, the Book defines intent, and a conflict that touches a shared contract is **not** decided by a builder — it goes to the Architect (BOOK §0, §Appendix E).

Each conflict carries: what v1 says, what the Book says, the consequence, and a recommendation marked **[ADR]** (needs a decision record), **[WP]** (absorbed by a work package), or **[DOC]** (documentation only).

---

## C1 — Source of truth for events and state  **[ADR]**

| | |
|---|---|
| **v1** | `docs/DECISIONS.md` ADR-0003: *"SQLite + JSONL hybrid"*, with the explicit rule **"Storing raw LLM tokens in SQLite is forbidden; the SQLite row references the JSONL offset for replay."** `docs/ARCHITECTURE.md` calls `events.jsonl` the *"replay source of truth"* |
| **Book** | §8: *"SQLite passa a ser o source of truth. JSONL passa a ser: export, debug trace, backup-friendly format, benchmark artifact."* The Book explicitly rejects dual truth: *"Não manteremos dois sources of truth porque não existe transação realmente atômica entre SQLite e append de arquivo."* |
| **Consequence** | The Book reverses v1's central storage decision. `store.py` (475 lines) writes both, and its transaction discipline assumes JSONL is authoritative. v2's event store is a rewrite, and the v1 ADR must be explicitly superseded — not silently ignored |
| **Recommendation** | **[ADR]** Adopt the Book's position (single canonical log in SQLite, JSONL as an export artifact). Write `ADR-0003-v2` that supersedes `v1 ADR-0003` and names the JSONL exporter as the compatibility path. Keep v1's *mechanics* (WAL, explicit SQL, no ORM, `event_index` shape) |

---

## C2 — Runtime interface  **[ADR, then WP-003]**

| | |
|---|---|
| **v1** | `engines/base.py`: `Engine` with `available()`, `default_model()`, `start(spec)`, `send(worker_id, msg)`, `stream(worker_id)`, `cancel()`, `close()`; `WorkerHandle`; `EVENT_SCHEMA = "meta-harness.event/v1"` |
| **Book** | §12 RuntimeAdapter v2: `probe`, `capabilities`, `models`, `create_session(SessionSpec)`, `send`, `steer`, `follow_up`, `events`, `interrupt`, `cancel`, `usage`, `artifacts`, `close` — plus a declared **capability/feature-flag set** (`streaming`, `steering`, `tool_events`, `usage`, `compaction`, …) and an explicit `UnsupportedCapability` return instead of a silent no-op |
| **Consequence** | The interfaces are incompatible at the signature level. `engines/pi.py` (245 lines) maps Pi's NDJSON onto the v1 schema; that mapping is reusable, the protocol class is not. Nothing in v1 negotiates capabilities, so the future UI could assume features a runtime lacks — the exact failure the Book's feature flags are designed to prevent |
| **Recommendation** | **[ADR]** Accept RuntimeAdapter v2 as canonical. **[WP-003]** ships it in `packages/contracts`. V1's `EVENT_SCHEMA` becomes a set of canonical event names, not a protocol |

---

## C3 — Hermes adapter transport  **[ADR]**

| | |
|---|---|
| **v1** | `engines/hermes.py` reaches the host through a late-bound in-process `_HOST_REQUEST` shim, and falls back to a documented `"stub mode"`. `available()` is False whenever that shim is absent — which `PROJECT_STATE.md` admits is *every current build* |
| **Book** | §14: *"Não usar mais `_HOST_REQUEST`. Preferência: TUI Gateway JSON-RPC. Fallback: HTTP + SSE."* |
| **Consequence** | Agreement on the negative, divergence on the positive. The Book's preferred transport (TUI gateway JSON-RPC) is **not yet verified anywhere in this repo or its docs** |
| **External evidence (outside this repo)** | `~/projetos/harness-console/FASE0-EVIDENCIA.md` (2026-10-05, measured on this host) reports a *third* option: `hermes acp` speaks ACP over stdio, answered `PONG`/`end_turn` through the `acpx` client, and — decisively — **the Hermes venv already ships `agent-client-protocol` 0.9.0 with the client side** (`spawn_agent_process`), which drove two consecutive turns in one session without the `refusal` that `acpx`'s one-process-per-invocation model triggers |
| **Recommendation** | **[ADR]** Compare three transports before writing an adapter: TUI gateway JSON-RPC, HTTP+SSE, ACP-over-stdio. ACP has measured evidence on this host today and is the only one of the three that is *also* spoken by Pi, OpenClaw and OpenCode (common denominator). Do not start WP-011 (Hermes adapter) on an unverified assumption |

---

## C4 — Pi transport: one protocol, two implementations  **[ADR]**

| | |
|---|---|
| **v1** | `engines/pi.py`: `pi --mode rpc`, JSON-RPC over stdio, NDJSON, never scrape ANSI output |
| **Book** | §13: identical — `pi --mode rpc`, NDJSON, chosen as the first adapter because it is *"structured, simples, baixo acoplamento, já existe adapter anterior, ideal para walking skeleton"* |
| **Divergence** | None on paper. But the Phase-0 evidence above records that `pi-acp` (0.0.34, requires pi ≥ 0.81.0; host has 0.99.2) **also** works and speaks ACP |
| **Consequence** | Two viable Pi transports. Choosing ACP for Pi buys one client implementation for all runtimes at the cost of a Node/npx wrapper; choosing `--mode rpc` keeps an in-process, dependency-light path but makes Pi a special case |
| **Recommendation** | **[ADR]** Pick deliberately. If C3 lands on ACP, the adapter-per-runtime cost collapses and Pi should follow; if not, `--mode rpc` stays the walking-skeleton path exactly as the Book plans |

---

## C5 — Repository shape and the fate of the Hermes plugin doors  **[ADR]**

| | |
|---|---|
| **v1** | Two doors into Hermes: the Python plugin (`~/.hermes/plugins/meta-harness/`, patched into `plugins.enabled`) and the desktop plugin (`~/.hermes/desktop-plugins/meta-harness/plugin.js`), plus a third (broken) host-dashboard door |
| **Book** | §6/§11: a monorepo — `apps/{daemon,web,desktop}`, `packages/{contracts,plugin-sdk,ui-kit,benchmark-spec}`, `plugins/runtimes/*`, `topologies/`, `roles/`, `character-packs/`, `evals/`, `benchmarks/`, `docs/…`, `scripts/`, `tests/…`. The Book never says what happens to the host plugin doors |
| **Consequence** | A real migration decision with a human cost: the only thing a user can *use* today is the host plugin. `install.sh` also patches the user's `config.yaml` — a side effect that must not survive into v2 unchanged |
| **Recommendation** | **[ADR]** Freeze the Hermes-hosted form at `v0.1-hermes-hosted` (keep it installable and documented as the legacy MVP) and build v2 as a standalone daemon + web app. Retire the dashboard door (D3) and stop patching `config.yaml` from an installer |

---

## C6 — Cross-platform: Bash, and a single-OS CI matrix  **[WP-001, WP-002]**

| | |
|---|---|
| **v1** | The documented install path is `bash scripts/install.sh`; Windows is served by a parallel `.bat` family (`doctor.bat`, `install.bat`, `uninstall.bat`). CI is `ubuntu-latest` only |
| **Book** | §5/§10 *"Nenhuma feature core poderá assumir Bash"*; §67/Appendix C: the canonical surface is `python scripts/dev.py \| test.py \| doctor.py \| package.py`, *"must work from PowerShell and POSIX environments"*; §68: CI matrix `ubuntu-latest` + `windows-latest`, *"Nenhum merge se Windows estiver vermelho"* |
| **Consequence** | v1's dual-script maintenance **is** the tax the Book is engineered to remove, and it is measurable: two script families, zero Windows CI. The Book's rule is currently unenforceable because no Windows job exists |
| **Recommendation** | **[WP-002]** replace both script families with Python entry points. **[WP-001]** does not need to fix it, but must record it. Add the `windows-latest` job in the first WP that creates an app (WP-002), not at the end |

---

## C7 — ADR numbering collision  **[DOC, but blocking for §Appendix A]**

| | |
|---|---|
| **v1** | `docs/DECISIONS.md` already uses `ADR-0001` … `ADR-0015` for a completely different set (ADR-0001 = *"Hermes-first integration, no fork"*, ADR-0003 = *"SQLite + JSONL hybrid"*, ADR-0010 = *"Renderer never sees raw provider keys"*) |
| **Book** | §Appendix A proposes `ADR-0001` … `ADR-0015` for the v2 set (ADR-0001 = *"Meta-Harness becomes independent control plane"*, ADR-0003 = *"SQLite is canonical state/event source"*, ADR-0015 = *"Self-improvement requires staging/eval"*) |
| **Consequence** | Same numbers, different decisions, in the same repository. Any future reference to "ADR-0003" is ambiguous and will be resolved wrongly by an agent |
| **Recommendation** | **[DOC]** Move the historical set to `docs/adr/v1/ADR-V1-0001.md … ADR-V1-0015.md` during WP-001 and start the v2 series clean at `docs/adr/ADR-0001-*.md`. **Blocking:** Appendix A cannot be created before this rename |

---

## C8 — "Working" claims that do not survive installation  **[WP-001]**

| | |
|---|---|
| **v1** | `PROJECT_STATE.md` lists as working: *"five built-in topologies, four role prompts, one default character pack with eight original SVG sprites"*, *"doctor returns PASS with zero warnings"* |
| **Measured** | After install, the plugin tree contains **no** `topologies/`, **no** `character-packs/`, **no** `roles/`; `~/.hermes/meta-harness` (the data dir) does not exist; the plugin is `not enabled`. `roles/` is read by no code path at all (D5). The built-in content claims are true *inside a checkout* and false *in an installation* |
| **Book** | §82: *"Display explicit failures. Never hide: … No fake green."* |
| **Consequence** | A reader of `PROJECT_STATE.md` concludes the MVP runs. It does not, in any installed layout |
| **Recommendation** | **[WP-001]** add a one-paragraph *"Installation reality"* correction to `PROJECT_STATE.md` (or move the state file to a v2 form) that separates *verified in checkout* from *verified installed*. Do not rewrite v1's history |

---

## C9 — Stale claims in README  **[WP-001]**

| | |
|---|---|
| **v1 README** | States *"Hosted CI — **Not configured in this repository**"* and describes an install/doctor run in an isolated Hermes home as the strongest evidence |
| **Measured** | `.github/workflows/ci.yml` exists, ran twice, latest **success** (15 s, 2026-09-29). CI **is** configured and green |
| **Consequence** | The public README understates the project. Minor, but it is a truthfulness defect in a portfolio repository |
| **Recommendation** | **[WP-001]** correct the verification table; carry the corrected wording into the v2 README |

---

## C10 — License  **[ADR]**

| | |
|---|---|
| **v1** | `LICENSE` = **Apache-2.0**; GitHub reports it as `"other"` (unrecognised variant), so no licence badge appears |
| **Book** | Silent on licensing. §105/Appendix F require a public, portfolio-grade repository |
| **Consequence** | Publishing v2 under a different licence than v1 creates a split history for the same product name. `gh repo view` already shows the repo as "Other" rather than Apache-2.0, which costs a visible signal on the portfolio page |
| **Recommendation** | **[ADR]** Decide once, before v2 goes public: keep Apache-2.0 (patent grant, matches a platform ambition) and fix the file so GitHub detects it, or switch explicitly. Note the v1 tree contains no third-party assets (the character art is original, per ADR-0007) |

---

## C11 — UI constraint disappears in v2  **[DOC / WP-006]**

| | |
|---|---|
| **v1** | The desktop plugin is restricted by the Hermes Desktop loader to `@hermes/plugin-sdk` and `react*` imports — `docs/RECONNAISSANCE.md` records *"Anything else is rejected as 'unsupported imports' before evaluation"*, ADR-0011 then fixes the renderer at Canvas2D + SVG, no Pixi.js. The UI is 4 tabs in one 438-line file |
| **Book** | §6/§59–§63: the app is its own React/TS/Vite application with TanStack Router/Query, React Flow, Radix, Tailwind, served by the daemon; the desktop is only a Tauri shell |
| **Consequence** | The pickiest constraint in v1 disappears, and with it the justification for ADR-0011 and for the single-file, no-build plugin. v2 can use any renderer — which also means v2 can no longer borrow v1's UI |
| **Recommendation** | **[DOC]** Mark ADR-0011 (and ADR-0002, the standalone-door decision) as **superseded** rather than wrong. **[WP-006]** starts the web app fresh |

---

## C12 — A parallel effort already exists, with measurements the Book does not have  **[ADR — highest priority]**

| | |
|---|---|
| **What it is** | `~/projetos/harness-console/` — `PLANO.md` (execution plan, same thesis in a smaller scope: "harness próprio, plugin-first, que orquestra vários runtimes … numa única UI no estilo do Control UI do OpenClaw") plus `FASE0-EVIDENCIA.md` (a Phase-0 report executed 2026-10-05 on this host) and `probes/acp_client_probe.py` |
| **Overlap** | Both projects want: one shell over multiple runtimes, per-agent identity, an "Ask" that retrieves instead of dumping, per-harness token/cost metrics, and a sanitized public repo |
| **Divergence** | `harness-console` chooses ACP-over-stdio as the *only* common bus, a Python-native ACP client inside the plugin backend, the Console (not each harness) as the owner of conversation state, and a 3-column UI inside Hermes Desktop. The Book chooses a Python daemon + own web UI + Tauri, with a bespoke `RuntimeAdapter` contract per runtime and a much larger scope (29 phases) |
| **Measured facts the Book does not contain** | ① ACP works across `hermes acp`, `pi-acp` and `openclaw acp` through one client. ② `hermes acp` keeps sessions in process memory, so named-session resume always returns `refusal` — which is exactly why "Console owns the state" was adopted. ③ The Hermes venv already contains `agent-client-protocol` 0.9.0 **with a client** — no new dependency is needed to speak ACP from Python. ④ `Usage` (input / cached_read / output / thought / total) arrives per turn in the protocol — v2's `UsageSample` can be *measured* rather than estimated. ⑤ `dsh`'s ACP is automation-only (no resume/stream/tools/MCP) and cannot back a live UI. ⑥ OpenClaw is a pacman-owned package: `openclaw update` fails by design. ⑦ Every `session/new` must carry an explicit tool restriction, or a test turn can silently write memory |
| **Consequence** | The Book was written as if the transport layer were an open question it could settle by preference. It is not: on this host the primary facts are already measured, and two of them (② and ⑤) invalidate assumptions the Book makes about session semantics and about `dsh` as a runtime. Building WP-003/WP-004/WP-005 and *then* discovering this would cost exactly the wasted wave the Book's own §98 (anti-failure rule) is meant to prevent |
| **Recommendation** | **[ADR, before WP-003]** Decide the relationship: (a) **Book-as-trunk** — meta-harness v2 is the product; `harness-console` becomes its Phase-0 evidence and a source of adapters; (b) **Console-as-trunk** — the smaller scope ships first and meta-harness v2 becomes its long-term roadmap; (c) **one trunk, staged** — build v2's skeleton from the Book but admit ACP as a transport candidate (i.e. resolve C3/C4 *from the FASE0 evidence* instead of re-testing), and carry the Console's measured invariants (`Usage` per turn, state ownership, explicit tool restriction) into v2's contracts. Recommendation: **(c)**, because it is the only option that keeps both the Book's scope ambition and the measurements already paid for |
| **Resolved** | **2026-10-05 — project owner decision: (a) as the trunk, with (c)'s transport posture.** meta-harness v2 is the product; `~/projetos/harness-console` becomes Phase-0 evidence and a source of adapters; and C3/C4 are to be settled *from the FASE0 measurements* rather than re-tested. Carried into v2's contracts: per-turn `Usage` from the protocol, control-plane ownership of conversation state, and an explicit tool restriction on every `session/new` |

---

## Summary table

| ID | Conflict | Type | Needs decision before |
|---|---|---|---|
| C12 | Parallel `harness-console` effort with measurements the Book lacks | ADR | **resolved 2026-10-05**: trunk = meta-harness v2 |
| C1 | JSONL-canonical vs SQLite-canonical | ADR | WP-004 |
| C2 | `Engine` protocol vs RuntimeAdapter v2 | ADR + contracts | WP-003 |
| C3 | Hermes transport (`_HOST_REQUEST` dead; Book prefers TUI gateway; ACP measured) | ADR | Hermes adapter WP |
| C4 | Pi transport (`--mode rpc` vs ACP) | ADR | Pi adapter WP |
| C5 | Monorepo target; fate of the host plugin doors | ADR | WP-002 |
| C6 | Bash-first scripts; no Windows CI | WP-001/002 | WP-002 |
| C7 | ADR numbering collision | DOC | Appendix A / WP-001 |
| C8 | "Working" claims that fail on install | WP-001 | WP-001 |
| C9 | Stale README (CI status) | WP-001 | WP-001 |
| C10 | Apache-2.0 vs undecided | ADR | public release |
| C11 | Superseded UI constraints (ADR-0002/0011) | DOC | WP-006 |

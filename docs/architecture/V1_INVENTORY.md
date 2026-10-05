# V1 Inventory — `meta-harness` (Hermes-hosted MVP)

**Recon date:** 2026-10-05
**Recon host:** Arch Linux, user `nerdylog`, Hermes Agent `v0.21.5+7363.gc535369` (2026.9.24)
**Method:** fresh clone of the public repo + read-only inspection of the installed plugin.
Nothing in this document is an estimate: every claim carries the command that produced it.

```
$ git clone https://github.com/nerdylog-code/meta-harness.git
$ git rev-parse HEAD
3ef4a5c6f72ef63595e51c749a97a4ea56bc3196
```

---

## 1. Repository facts

| Item | Value | Evidence |
|---|---|---|
| URL | `https://github.com/nerdylog-code/meta-harness` | `gh repo view` |
| Visibility | **public** | `gh repo view … --json visibility` |
| Default branch | `master` (only branch) | `git branch -a` |
| HEAD | `3ef4a5c` — *ci: instala pyyaml antes dos testes (falha real do run 36610646178)* | `git log --oneline -20` |
| Commits | 8 | `git log --oneline \| wc -l` |
| **Tags** | **none** | `git tag -l` (empty) |
| License | Apache-2.0 (`LICENSE`, detected by GitHub as "Other") | `head -3 LICENSE` |
| Size | 194 KB (`diskUsage`), 3 764 lines of Python | `gh repo view`, `wc -l` |
| Last push | 2026-09-29T18:16:15Z | `gh repo view --json pushedAt` |
| CI | `.github/workflows/ci.yml` — `ubuntu-latest` **only**, Python 3.12, `pip install pyyaml`, `python tests/run_all.py` | file + `gh run list` |
| CI runs | 1 failure (7 s) → 1 success (15 s). Latest = **success** | `gh run list -R nerdylog-code/meta-harness` |
| Tracked bytecode | **16 `.pyc` files under `__pycache__/`** committed despite `.gitignore` | `git ls-files \| grep -cE 'pycache\|\.pyc'` → 16 |
| Installer copy set | `hermes-plugin/**` + `desktop-plugin/plugin.js` only | `scripts/install.sh` |

The last commit message is a CI-fix commit, so the repository has a working CI loop with a known red→green history.

---

## 2. Tree and code volume

```
meta-harness/
├── character-packs/default/       # character-pack.json + 8 original SVG sprites (4 × idle/walk)
├── desktop-plugin/plugin.js       # 438 lines, single file, no build step
├── docs/                          # 10 markdown documents (ARCHITECTURE, DECISIONS, RECONNAISSANCE, …)
├── .github/workflows/ci.yml       # ubuntu-latest only
├── hermes-plugin/
│   ├── plugin.yaml                # name/version/kind: standalone/manifest_version: 2
│   ├── __init__.py                # 265 lines — register(ctx): hooks + 8 model-facing tools
│   ├── dashboard/                 # manifest.json + plugin_api.py (34 lines)
│   └── hermes_plugin/             # the runtime package
│       ├── runtime.py             # 331  bootstrap, hook adapters, tool handlers
│       ├── api.py                 # 370  FastAPI router + WebSocket
│       ├── store.py               # 475  SQLite (WAL) + JSONL event store
│       ├── topology.py            # 425  declarative topology executor
│       ├── characters.py          # 283  character pack registry + animation mapping
│       ├── plugin_lab.py          # 266  versioned model-authored plugin candidates
│       ├── capabilities.py        #  97  internal capability registry + resolver
│       ├── paths.py               #  68  HERMES_HOME / data_dir resolution
│       ├── redaction.py           #  63  credential scrubbing at the event boundary
│       └── engines/               #  30 base + 45 registry + 224 hermes + 245 pi
├── roles/                         # architect.md, builder.md, reviewer.md, validator.md
├── scripts/                       # install/doctor/uninstall (.sh + .bat), check_plugin_load.py,
│                                  # check_plugin_render.py, probe_render.mjs, package.json
├── tests/                         # run_all.py (25) + test_core.py (257, 21 tests)
├── topologies/                    # solo, gate-build, architect-builder, fusion, visual-build-review
├── PROJECT_STATE.md               # "Phase 14 — MVP delivered"
└── README.md
```

Largest Python modules (real `wc -l`, not estimates):

| Lines | File |
|---:|---|
| 475 | `hermes-plugin/hermes_plugin/store.py` |
| 425 | `hermes-plugin/hermes_plugin/topology.py` |
| 370 | `hermes-plugin/hermes_plugin/api.py` |
| 331 | `hermes-plugin/hermes_plugin/runtime.py` |
| 283 | `hermes-plugin/hermes_plugin/characters.py` |
| 266 | `hermes-plugin/hermes_plugin/plugin_lab.py` |
| 265 | `hermes-plugin/__init__.py` |
| 245 | `hermes-plugin/hermes_plugin/engines/pi.py` |
| 224 | `hermes-plugin/hermes_plugin/engines/hermes.py` |
| 257 | `tests/test_core.py` |
| **3 764** | **total (all `.py`, incl. tests and scripts)** |

---

## 3. Component inventory

Each row states what the component *actually does today*, not what its docstring promises.

| Component | What it is | Real state | Evidence |
|---|---|---|---|
| `hermes-plugin/plugin.yaml` | Host plugin manifest: `kind: standalone`, `manifest_version: 2`, 6 `provides_hooks`, 4 config keys | Valid; declares hooks the loader accepts | `hermes plugins list` |
| `hermes-plugin/__init__.py` | `register(ctx)` entry. Registers 6 hooks (`pre/post_tool_call`, `pre/post_api_request`, `on_session_start/end`) and **8 model-facing tools** (`harness_inspect`, `harness_run`, `harness_status`, `harness_cancel`, `harness_capabilities`, `harness_plugins`, `harness_topology_list`, `harness_artifact_get`, `harness_plugin_lab`). Every registration is wrapped in `try/except` so a missing host API cannot crash the loader | Real and defensive | file |
| `hermes_plugin/runtime.py` | Bootstrap: opens SQLite, registers engines, capabilities, character packs, topologies, validation capabilities; implements the 8 tool handlers + 6 hook adapters | Real | `runtime.py:37-100` |
| `hermes_plugin/store.py` | SQLite (WAL) + append-only `events.jsonl`. Tables: `runs`, `agents`, `artifacts`, `plugin_state`, `topology_state`, `character_assignments`, `event_index` (+4 indexes) | Real, transactional, thread-locked | `grep -n "CREATE TABLE" store.py` |
| `hermes_plugin/capabilities.py` | Internal capability registry + trust-priority resolver (`system > trusted-installed > user-authored > model-generated > experimental > quarantined`) | Real, hot-swappable, unit-tested | `test_resolve_picks_higher_trust` |
| `hermes_plugin/topology.py` | YAML topologies as data; executors for `solo`, `sequence`, `parallel`, `gate`; generic DAG fallback; cycle-safe topological order; recursive cancel | Real, unit-tested | `TestTopology` |
| `hermes_plugin/engines/base.py` | Bespoke `Engine` protocol: `start/send/stream/cancel/close` + `WorkerHandle` + `EVENT_SCHEMA = "meta-harness.event/v1"` | Real, but *not* the v2 RuntimeAdapter | file |
| `hermes_plugin/engines/hermes.py` | Hermes-native engine via host gateway RPC | **Dead on current hosts.** `available()` returns False unless a module-level `_HOST_REQUEST` shim exists in-process — it never does. Contains an explicit `"stub mode"` path | `hermes.py:30-42,77-78,113`; `test_hermes_unavailable_when_no_host` asserts False |
| `hermes_plugin/engines/pi.py` | Pi engine over `pi --mode rpc` (JSON-RPC/NDJSON stdio), PATH + `PI_EXECUTABLE` resolution, synthetic failure event when unavailable | Real design; host has `pi 0.99.2` on PATH (`~/.local/share/mise/shims/pi`) | `pi --version` |
| `hermes_plugin/characters.py` | Declarative character packs (manifest + sprites) + state→animation map with `working_file`→`idle` fallback | Registry real, **content never loads in an installed layout** (D1) | see §7 D1 |
| `hermes_plugin/plugin_lab.py` | Immutable versioned model-authored plugin candidates (`generated/<id>/vNNNN`) + validate / experimental activate / rollback | Real, unit-tested; promotion into the trusted namespace deliberately out of scope | `TestPluginLab` |
| `hermes_plugin/redaction.py` | Credential scrubbing (OpenAI keys, bearer tokens, nested dicts, giant-string bounds) before persistence | Real, unit-tested | `TestRedaction*` |
| `hermes_plugin/paths.py` | `hermes_home()` (hermes_constants → `HERMES_HOME` → `~/.hermes`) and `data_dir/events/store/artifacts/character-packs/generated/topologies/roles` | Real. In v2 this is the natural `platformdirs` seam | file |
| `hermes_plugin/api.py` | 29 routes under `/api/plugins/meta-harness/`: status, capabilities, plugins, runs (CRUD/cancel/pause/resume), run events + **WebSocket** `/runs/{id}/events/ws`, topologies, character packs + assets, character assignments, artifacts, plugin-lab | Real router, mounted by the host | `grep -n "@router"` |
| `hermes-plugin/dashboard/` | Host-dashboard door: `manifest.json` (`tab.path: /meta-harness`, `after:kanban`) + `plugin_api.py` | **Broken**: manifest declares `"entry": "plugin.js"` and `"css": "style.css"`; neither file exists in the repo (D3) | `cat dashboard/manifest.json` |
| `desktop-plugin/plugin.js` | Hermes Desktop plugin, 438 lines, no build: 4 tabs (overview/agents/topologies/characters), sidebar nav, `/meta-harness` route, status-bar chip, 2 palette commands | Loads (shim probe: PASS, 5 contributions) | `scripts/check_plugin_load.py` |
| `topologies/*.yaml` (5) | `solo`, `gate-build`, `architect-builder`, `fusion`, `visual-build-review` — declarative nodes/edges/limits | Real, but never installed (D2) | `ls topologies/` |
| `character-packs/default/` | 1 pack, 4 characters × idle+walk = **8 original SVGs** + manifest | Real assets, never installed (D2) | `ls -R character-packs/` |
| `roles/*.md` (4) | architect / builder / reviewer / validator role prompts | **Read by no code path.** Only `paths.roles_dir()` mentions a roles directory; nothing loads it | `grep -rn "roles" hermes_plugin/*.py` |
| `tests/` | 21 unit tests, dependency-free runner | **21/21 pass** (see §4) | `python tests/run_all.py` |
| `scripts/check_plugin_load.py` | Simulates the desktop plugin loader and asserts the contribution set | PASS: `contributions=5` | ran it |
| `scripts/check_plugin_render.py` + `probe_render.mjs` | jsdom + React render probe (installs `scripts/node_modules` on demand) | Not re-run in this recon (npm install); README claims a clean render | README |
| `scripts/install.sh\|.bat`, `doctor.sh\|.bat`, `uninstall.sh\|.bat` | Idempotent installer (backs up `config.yaml`, patches `plugins.entries`), doctor (PASS/FAIL/WARN), uninstaller | Real; **the only documented install path is Bash** on POSIX (D6) | file |
| `docs/*.md` (10) | ARCHITECTURE, DECISIONS (15 ADRs), RECONNAISSANCE, PLUGINS, TOPOLOGIES, CHARACTER_PACKS, HERMES_INTEGRATION, PI_INTEGRATION, SECURITY, DEVELOPMENT | Substantive, honest about limits | `wc -c docs/*.md` |

### Installed state on this host

```
$ hermes plugins list          # filtered
meta-harness   status=not enabled   version=0.1.0   source=user
$ ls ~/.hermes/meta-harness
ls: cannot access '…/meta-harness': No such file or directory      # data dir never created
$ ls ~/.hermes/character-packs
ls: cannot access '…/character-packs': No such file or directory   # seed target absent
$ find ~/.hermes/plugins/meta-harness -maxdepth 2   # no topologies/ , no character-packs/ , no roles/
```

Conclusion: **v1 was installed but never enabled and never bootstrapped.** Nothing in this inventory about v1 runtime behaviour is an observation of it running; only the test suite and the loader probe executed.

---

## 4. Verification evidence log

Every command below was run on 2026-10-05 against HEAD `3ef4a5c` in a fresh clone.

| # | Command | Result | Reading |
|---|---|---|---|
| V1 | `python3 tests/run_all.py` (system Python 3.14.7) | **fails**: `ModuleNotFoundError: No module named 'yaml'` | The suite needs PyYAML; only CI installs it |
| V2 | `~/.hermes/hermes-agent/venv/bin/python3 tests/run_all.py` (Python 3.13.15, PyYAML 6.0.3) | **21 tests, OK, 0.094 s** | The suite is agent-independent and green |
| V3 | `python3 scripts/check_plugin_load.py` | `PASS id=meta-harness contributions=5` (nav, route, chip, 2 palette) | Desktop plugin contract holds under a loader shim |
| V4 | `gh run list -R nerdylog-code/meta-harness` | latest `success` (15 s), previous `failure` (7 s) | CI works; history contains a real red→green fix |
| V5 | `git tag -l`, `git branch -a` | empty; `master` only | No baseline tag exists → WP-001 is genuinely pending |
| V6 | `git ls-files \| grep -c pycache` | 16 | Committed bytecode, as PROJECT_BOOK §76 predicts |
| V7 | `git grep` for builtin-asset resolution | `parents[2]` in `characters.py:181` and `topology.py:132`; `parents[3]` in `runtime.py:80` | Path arithmetic is layout-dependent and wrong after install (D1) |
| V8 | `grep -rn "roles" hermes_plugin/*.py` | only `paths.roles_dir()` | `roles/` is dead weight today (D5) |

**Boundary of this evidence:** no observation of a live run, a live session, a Hermes Desktop render, or a Windows execution. The desktop-plugin render probe (`check_plugin_render.py`) was **not** re-run here.

---

## 5. Keep / Adapt / Replace matrix

Verdicts are recommendations for the v2 control plane; every `ADAPT`/`REPLACE` that touches a shared contract needs Architect approval (PROJECT_BOOK §0/§Appendix E).

| V1 artifact | LOC/lines | v2 destination (BOOK §) | Verdict | Rationale |
|---|---|---|---|---|
| `store.py` (SQLite+JSONL, 7 tables) | 475 | §8 Event Plane, §9 Storage (22 tables) | **REPLACE** (reuse patterns) | Schema is run/agent-centric; v2 needs mission/task/run/approval/capsule/usage/plugin-version tables and a canonical event log. Keep: WAL, explicit SQL, no ORM, thread discipline, `event_index` idea |
| `events.jsonl` as *source of truth* (ADR-0003) | — | §8 (SQLite is canonical) | **REPLACE** (contract change) | Direct contradiction with the Book. Requires an explicit superseding ADR (see CONFLICTS C1) |
| `capabilities.py` (registry + trust sort) | 97 | §10/§17 Plugin Kernel + trust ladder | **ADAPT** | Concept survives; trust ladder must be widened to the 9-level v2 ladder and bound to the plugin manifest |
| `topology.py` (YAML-as-data, 4 executors) | 425 | §33 Orchestration Topologies, §29 Work Graph | **ADAPT** | YAML-as-data is exactly right; the executor becomes a `WorkflowProvider` over a real task graph instead of a run-scoped DAG |
| `engines/base.py` `Engine` protocol | 30 | §12 RuntimeAdapter v2 | **REPLACE** | Signature mismatch (`start/send/stream` vs `probe/capabilities/create_session/steer/usage/artifacts`) and no capability negotiation |
| `engines/hermes.py` (`_HOST_REQUEST` shim) | 224 | §14 Hermes adapter | **REPLACE** | The shim does not exist on any current host build; the engine is provably unavailable. Book already rejects `_HOST_REQUEST` |
| `engines/pi.py` (`pi --mode rpc`) | 245 | §13 Pi adapter | **ADAPT** | Transport matches the Book exactly; interface must be re-cut to RuntimeAdapter v2 |
| `characters.py` + `character-packs/default` (8 SVGs) | 283 + assets | §62 Agent Office → `RendererProvider` | **ADAPT / KEEP assets** | Declarative-assets-only rule already satisfies "no pack executes code". Needs to become a plugin type, and the resolution bug fixed |
| `plugin_lab.py` (versioned candidates + rollback) | 266 | §40 Self-Improvement, §10 versioning | **ADAPT** | Immutable version dirs + pointer + rollback is the right primitive; must plug into candidate→eval→staging→promotion instead of standing alone |
| `redaction.py` | 63 | §11 kernel invariants (redaction) | **KEEP** | Kernel-level concern, self-contained, tested |
| `paths.py` | 68 | §5/§10.1 Cross-platform paths | **ADAPT** | Same resolution *philosophy*, but `platformdirs` replaces `HERMES_HOME` arithmetic; also fixes D1 |
| `api.py` (29 host-mounted routes + WS) | 370 | §64 API UI↔daemon (`/v1/…`, `/v1/events/ws`) | **REPLACE** (reuse shapes) | Routes must hang off the daemon, not the Hermes host. `runs/{id}/events/ws` and the run/agent/artifact payload shapes are directly reusable |
| `hermes-plugin/__init__.py` + `plugin.yaml` + 8 tools | 265 + manifest | §6 (not a fork; a control plane) | **KEEP frozen** | Works, and is the only thing giving the project a usable artifact today. Freeze at `v0.1-hermes-hosted`; do not extend, do not delete |
| `desktop-plugin/plugin.js` | 438 | §59–§61 UI, §6 apps/web | **REPLACE** | It is bound to the Hermes Desktop import allowlist (`@hermes/plugin-sdk` + `react*`) and to the host's routes. v2 owns its own web app, so the constraint disappears. Keep the "one file, no build" lesson for *probe* surfaces only |
| `dashboard/manifest.json` + `plugin_api.py` | 34 | — | **DELETE** | Points at files that do not exist (D3); superseded by the daemon API |
| `topologies/*.yaml` (5) | — | §33 | **KEEP as seed data** | Real content, no code; migrate verbatim into the v2 topology store |
| `roles/*.md` (4) | — | §73 roles as data | **KEEP as data** | Currently loaded by nothing; becomes role prompts in AgentBlueprint generation |
| `tests/test_core.py` (21 tests) | 257 | v2 `tests/unit` | **KEEP** | It is the only executable regression asset in the repo |
| `scripts/check_plugin_load.py` + `check_plugin_render.py` | 168 + 75 | §69 Runtime conformance, §70 Plugin conformance | **ADAPT** | These are the embryo of the conformance suites: same idea, wider scope, Python not JS shims |
| `scripts/install.sh\|.bat`, `doctor.sh\|.bat` | — | §67/Appendix C (`scripts/*.py`) | **REPLACE** | Book forbids mandatory Bash; v1 already had to maintain two families of scripts. Rewrite once in Python |
| `.github/workflows/ci.yml` | 12 | §68 CI matrix | **ADAPT** | Add `windows-latest` (+ later npm/TS jobs); keep the "install PyYAML then run tests" step |
| `docs/*.md` + ADRs 0001–0015 | 58 KB | §86 doc set | **KEEP as historical** | Renumber to avoid collision with the Book's Appendix A (see CONFLICTS C7) |

---

## 6. What does not exist at all

Nothing below has any implementation in v1; each is a v2 build item, not an adaptation:

`Mission` · `Task` + `TaskGraph`/`TaskEdge` · `Workspace`/`WriterLease` · `Approval` model · `UsageSample` (universal token/cost sample) · `ContextCapsule` + verifier + compaction · context budget engine · progressive tool disclosure · tool-output virtualization (`Artifact` as a first-class, hashed object) · memory provider · skills registry/versioning · RAG (FTS5, chunker, reranker, evals) · heartbeat (runtime / lease / attention) · Workboard · Canvas · Event-sourcing projections + replay · plugin SDK + manifest/type system + trust gating · secrets broker · channels · voice · `ProcessSupervisor` (cross-platform, orphan-free) · Tauri desktop shell · benchmarks (`HarnessBench`) · eval lab · LangGraph workflow provider · threat model · Windows CI.

Storage comparison, v1 → v2 (BOOK §9): 7 tables → 22 named tables.

---

## 7. Defects found in v1

These are real bugs, each with the evidence above. They matter because they explain why v1 looks complete on paper and is inert in practice — and two of them would otherwise be inherited by v2.

| ID | Defect | Evidence | Proposed disposition |
|---|---|---|---|
| **D1** | **Built-in content never resolves after install.** `characters.py:181` and `topology.py:132` compute `Path(__file__).resolve().parents[2]` (correct in the repo layout, but after `install.sh` copies `hermes-plugin/*` into `~/.hermes/plugins/meta-harness/`, `parents[2]` is `<HERMES_HOME>/plugins`). `runtime.py:80` uses `parents[3]`, which is wrong in *both* layouts | V7 + the installed tree has no `topologies/`/`character-packs/` | Fix in v2 by construction: assets live in a data dir resolved by `platformdirs`, never by `__file__` arithmetic. Do not "fix" v1 |
| **D2** | **`install.sh` copies only `hermes-plugin/**` and `plugin.js`.** `topologies/`, `character-packs/`, `roles/` are never installed | `scripts/install.sh` (copy commands) | Document in WP-001; solved in v2 by a package data manifest |
| **D3** | **Broken host-dashboard door.** `dashboard/manifest.json` declares `"entry": "plugin.js"`, `"css": "style.css"`; neither exists anywhere in the repo | `cat hermes-plugin/dashboard/manifest.json` + full `find` | Delete in WP-001 (it is dead surface) or ship the files; deleting is cheaper and v2 has its own UI |
| **D4** | **16 `.pyc` files tracked in git** despite `.gitignore` | `git ls-files \| grep -c` → 16 | WP-001: `git rm -r --cached` on `__pycache__` |
| **D5** | **`roles/` is dead weight.** No code path reads it; only `paths.roles_dir()` names a directory | V8 | Keep the four prompts as data for AgentBlueprint role generation; stop advertising them as a runtime feature |
| **D6** | **Bash-first install.** The documented POSIX path is `bash scripts/install.sh`; Windows needs a parallel `.bat` family | README + `scripts/` | Book §67/Appendix C: one `python scripts/*.py` surface. This is a Book requirement, not a v1 bug per se — but it is the concrete cost the Book is trying to remove |
| **D7** | **`engine.hermes` is permanently unavailable on current hosts**; the only usable engine is Pi, and v1's own `PROJECT_STATE.md` admits it | `hermes.py:30-42,77`; `test_hermes_unavailable_when_no_host` | v2 replaces it with the Hermes adapter of §14. Do not attempt to resurrect `_HOST_REQUEST` |
| **D8** | **README is stale.** It states "Hosted CI — Not configured in this repository" while `ci.yml` exists and is green; it also claims a local install/doctor verification that the repo's own `PROJECT_STATE.md` frames differently | README "Verification status" table vs `gh run list` | WP-001: correct the README or supersede it with the v2 README |
| **D9** | **Tests silently depend on PyYAML.** The documented command `python tests/run_all.py` fails on a stock interpreter (3.14.7 here) with `ModuleNotFoundError: yaml` | V1 vs V2 | WP-001: state the dependency in the test README and make `scripts/test.py` install/verify it (WP-002) |

---

## 8. Honest summary of v1

What v1 really is: a **coherent, tested, well-documented single-machine prototype** that wires a Hermes plugin to a real event store, a capability resolver, a declarative topology executor, a plugin lab, and a declarative character-pack system — with one deliberately dead engine (Hermes-native) and one engine that depends on an external binary (Pi).

What it is not: a system that has ever run end-to-end as installed, a control plane, or anything with missions, tasks, workspaces, approvals, capsules, usage accounting, or cross-platform processes.

Therefore the Book's PHASE 0 verdict holds — with one correction: v1 is **not** an empty skeleton (it has 21 passing tests, real schemas, real routes, 58 KB of honest documentation and a green CI loop). The accurate statement is: *v1 is a functional-looking MVP whose integration layer never actually ran, because its content and its Hermes engine never resolved in an installed layout.*

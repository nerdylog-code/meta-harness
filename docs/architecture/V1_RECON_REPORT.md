# Reconnaissance report — implementation orchestrator, first pass

**Date:** 2026-10-05 · **Host:** Arch Linux, `nerdylog`, Hermes Agent `v0.21.5+7363.gc535369`
**Baseline inspected:** `nerdylog-code/meta-harness @ 3ef4a5c6f72ef63595e51c749a97a4ea56bc3196` (public, `master`, 8 commits, **0 tags**)
**Scope of this pass:** exactly the BOOK §99 brief — *read the Book, inspect the repository and `PROJECT_STATE.md`, do not code, produce seven artifacts.*
**Status:** complete for §99; **no code was written, no branch was created, no tag was made, nothing was pushed.**

---

## 1. Deliverables produced

| §99 item | Where it lives |
|---|---|
| 1. V1 inventory | `docs/architecture/V1_INVENTORY.md` (component-by-component, with an evidence log and defects D1–D9) |
| 2. Keep / adapt / replace matrix | `docs/architecture/V1_INVENTORY.md` §5 (19 rows) |
| 3. Proposed tag and branch operations | §4 of this report |
| 4. WP-001 … WP-007 as executable work packages | `docs/work-packages/WP-00{1..7}-*.md` |
| 5. Dependency DAG (+ waves, model allocation) | `docs/architecture/WP_DAG.md` |
| 6. Exact acceptance commands, Windows and Linux | per-WP "Exact acceptance commands" sections (PowerShell + POSIX) |
| 7. Conflicts between current code and the Book | `docs/architecture/V1_CONFLICTS.md` (C1–C12) + `docs/adr/ADR-0001-control-plane.md` |

All of the above are **uncommitted** in the working copy at `~/projetos/meta-harness` (a fresh clone of the public repo). `git status` is the proof of what has and has not been done.

---

## 2. What the repository actually is (one paragraph)

A coherent, tested, single-machine prototype: 3 764 lines of Python, 21 passing unit tests (verified: `Ran 21 tests in 0.094s — OK`, run 2026-10-05 with the Hermes venv interpreter, Python 3.13.15 + PyYAML 6.0.3), a green CI run (`gh run list` → latest `success`, 15 s), a loader probe that passes (`contributions=5`), a real SQLite+JSONL store, a capability resolver, a declarative topology executor, a plugin lab, declarative character packs, 5 topologies, 8 original SVG sprites, and 58 KB of unusually honest documentation — **plus one engine that can never resolve, a built-in-content path bug that makes every topology and character pack invisible after install, and no live run ever observed.** v1 is not an empty skeleton and it is not a working product. The accurate label is *functional-looking MVP whose integration layer never actually ran.*

---

## 3. The seven §99 answers, condensed

**1. V1 inventory** — 30 components classified, each with the command that produced the claim. Installed state matters: `hermes plugins list` reports `meta-harness … not enabled`, `~/.hermes/meta-harness` does not exist, and the installed plugin tree contains no `topologies/`, `character-packs/` or `roles/`.

**2. Keep / adapt / replace** — 19 rows. Highlights:

- **Keep**: `redaction.py`, the 21 tests, the 5 topology YAMLs, the character-pack assets, `docs/*` as history, v1's frozen plugin form itself.
- **Adapt**: `capabilities.py` (trust ladder widens to 9 levels), `topology.py` (becomes a workflow provider over a real task graph), `engines/pi.py` (transport matches the Book; interface re-cut), `characters.py` (becomes a `RendererProvider`), `plugin_lab.py` (becomes the self-improvement candidate pipeline), `paths.py` (`platformdirs`), the conformance probes, the CI workflow.
- **Replace**: `store.py` (22-table v2 schema), the `Engine` protocol → `RuntimeAdapter` v2, `engines/hermes.py` (dead), `api.py` (moves to the daemon), `desktop-plugin/plugin.js` (host-bound), the two Bash script families.
- **Delete**: the host-dashboard door (points at files that do not exist).

**3. Proposed tag and branch operations** — see §4.

**4. WP-001 … WP-007** — written to the Book's §67 template (objective, dependencies, allowed/forbidden files, required reading, architecture constraints, acceptance tests, expected output, expected events, Windows/Linux requirements, exact commands, risks).

**5. DAG** — strictly serial at the front (`WP-001 → WP-002`), then a 3-wide wave (`WP-003 ∥ WP-005 ∥ WP-006`), then (`WP-004 ∥ WP-007`). Maximum concurrency 3, inside the Book's 3–5 envelope. The Pi adapter stays blocked behind WP-003/004/005 as the Book requires.

**6. Acceptance commands** — every WP carries POWERSHELL and POSIX command blocks that run the real thing (`python scripts/test.py --suite …`, `pnpm build`, `pnpm tauri build`), not prose.

**7. Conflicts** — C1–C12, summarised in §6 below.

---

## 4. Proposed tag and branch operations — **proposed only, not executed**

The Book asks for `tag v0.1-hermes-hosted` and `branch v2/control-plane` (BOOK §76). Neither exists (`git tag -l` is empty; `git branch -a` shows only `master`).

**Resolved 2026-10-05: the owner chose the raw form.** The tag was created on `3ef4a5c` byte-for-byte, with no cleanup commit, and no local change was mixed into it — see §4.b. The cleaned-commit variant below is kept for the record.

**The alternative that was offered: tag the *cleaned* freeze commit, not bare `3ef4a5c`.**

```
# on master (v1 line)
git rm -r --cached tests/__pycache__ hermes-plugin/**/__pycache__     # D4: 16 tracked .pyc
git rm -r --cached hermes-plugin/dashboard/manifest.json hermes-plugin/dashboard/plugin_api.py   # D3: dead door
#  + README verification-table correction (D8), PROJECT_STATE "installation reality" note (C8),
#  + docs/DECISIONS.md → docs/adr/v1/ADR-V1-0001..0015.md (C7), + the recon docs in this report
git commit -m "freeze: v0.1 baseline — index hygiene, dead door removal, honest docs"
git tag -a v0.1-hermes-hosted -m "v0.1 — Hermes-hosted MVP (frozen baseline)"
git checkout -b v2/control-plane
git push origin master v0.1-hermes-hosted v2/control-plane      # REQUIRES EXPLICIT AUTHORIZATION — not run
```

Rationale for tagging the cleaned commit: all four changes are non-functional (index hygiene, deletion of a surface that resolves to nothing, documentation truth, ADR renumbering), and a baseline that ships 16 `.pyc` files and a false CI claim is a worse artifact to freeze than one that does not. The alternative — tagging `3ef4a5c` untouched — is defensible if you would rather preserve v1 byte-for-byte; say so and I will use that form instead. The tag is **annotated**, so the freeze carries a message and a date.

Nothing above was executed at the time of writing. The tag and branch were created later the same day in the **raw** form — see §4.b. The remote has still not been touched.

### 4.b Executed form (2026-10-05)

The owner chose the **raw** form. Because the working copy held uncommitted deliverables, they were preserved first and kept out of the tag:

```
HEAD at freeze time ............. 3ef4a5c6f72ef63595e51c749a97a4ea56bc3196
changes to tracked files ........ none (git diff HEAD empty)
uncommitted at freeze time ...... 12 new untracked files (the §99 deliverables, 1 629 lines)
preservation #1 ................. git stash push -u -m "WP-001 preservation: secao-99 recon
                                  deliverables (docs/architecture, docs/adr, docs/work-packages)
                                  @ 3ef4a5c"  — entry kept (apply, not pop)
preservation #2 ................. tar.gz, sha256 4bf8df7d2417e92a24f4b0accc219128ad9befca4d70b0e0b0981de0813c811e
preservation #3 ................. patch,  sha256 0ef63f8b080256803b50b261d230f78c97a5309b7583d43f34949e074601ef62
                                  (git apply --check: OK)
worktree at tag time ............ clean (git status --porcelain -uall empty)
tag ............................. v0.1-hermes-hosted (annotated) -> 3ef4a5c
§99 docs inside the tag ......... 0 files under docs/architecture and docs/work-packages
§99 docs mixed into the tag ..... no
branch .......................... v2/control-plane, created **from the tag**
restored on the branch .......... 12 files, sha256-identical to the tar.gz (12/12, 0 divergences)
remote .......................... untouched (no push, no remote branch, no release)
```

The tag therefore contains the v1 MVP and nothing else, and the deliverables live only on `v2/control-plane`.

The tar/patch hashes above describe the **freeze-time snapshot (set A)**. The deliverables were amended later the same day, when the owner's two answers were recorded (ADR-0001 status + decision log, `V1_CONFLICTS` C12 resolution). A post-amendment snapshot (set B) supersedes set A on disk; set A is kept as the record of what existed at the moment of the freeze.

---

## 5. The blocking decisions (a human or the Architect must answer these)

| # | Decision | Why it blocks |
|---|---|---|
| **D-A** | **Which project is the trunk: this Book's control plane, or the parallel `~/projetos/harness-console` effort?** | `harness-console` already carries measured Phase-0 evidence (ACP works across Hermes/Pi/OpenClaw; `hermes acp` sessions are in-process so named resume always refuses; the Hermes venv already ships an ACP client; per-turn `Usage` arrives in the protocol; `dsh` is automation-only). The Book was written without these measurements and two of them contradict its assumptions. Deciding this after WP-004 would waste a wave — exactly what the Book's §98 anti-failure rule exists to prevent. See CONFLICTS C12. **RESOLVED 2026-10-05:** trunk = meta-harness v2 (the Book); `harness-console` becomes Phase-0 evidence and a source of adapters, and C3/C4 are settled from its measurements instead of being re-tested |
| **D-B** | **Event source of truth: SQLite (Book §8) or JSONL (v1 ADR-0003)?** | Reverses v1's central decision; `store.py` is a 475-line consequence. Blocks WP-004 |
| **D-C** | **Hermes transport: TUI gateway JSON-RPC, HTTP+SSE, or ACP-over-stdio?** | v1's `_HOST_REQUEST` is provably dead. ACP is the only candidate with measurements on this host *today*. Blocks the Hermes adapter |
| **D-D** | **Pi transport: `pi --mode rpc` (Book §13, v1) or ACP (measured)?** | Shapes the first adapter and therefore the walking skeleton |
| **D-E** | **What happens to the Hermes plugin doors?** | Freeze them (recommended), maintain them in parallel, or retire them. The installer currently patches the user's `config.yaml` |
| **D-F** | **Licence** | v1 is Apache-2.0 and GitHub shows it as "Other"; a public portfolio needs one deliberate answer |

Recommendations for each are in CONFLICTS C1–C12; **D-A is the one I would settle first.**

---

## 6. Conflict summary

| ID | Conflict | Impact |
|---|---|---|
| C1 | JSONL-canonical (v1 ADR-0003) vs SQLite-canonical (Book §8) | Rewrites the storage kernel |
| C2 | v1 `Engine` protocol vs `RuntimeAdapter` v2 (Book §12) | Rewrites every adapter's interface |
| C3 | Hermes: dead `_HOST_REQUEST` vs Book's TUI gateway vs measured ACP | Blocks the Hermes adapter |
| C4 | Pi: `--mode rpc` vs ACP | Blocks the first adapter |
| C5 | Monorepo target; fate of the host plugin doors; installer patching `config.yaml` | Blocks WP-002 |
| C6 | Bash-first scripts + ubuntu-only CI vs "no core feature may require Bash" + Windows CI | Blocks WP-002's gate |
| C7 | ADR numbering collision (v1 0001–0015 vs Book Appendix A 0001–0015) | Blocks Appendix A; fixed in WP-001 |
| C8 | "5 topologies / 4 roles / 1 pack" are true in the checkout and false in any install | Truthfulness; WP-001 |
| C9 | README says CI is unconfigured; CI is green | Truthfulness; WP-001 |
| C10 | Apache-2.0 vs undecided | Public release |
| C11 | v1's UI constraints (import allowlist, no Pixi) vanish in v2 | Marks ADR-0002/0011 superseded |
| C12 | Parallel `harness-console` effort with measurements the Book lacks | **Highest priority** |

---

## 7. Honest limitations of this pass

1. **Nothing was executed against a live v1 run.** The plugin is disabled on this host and its data directory has never existed. All v1 behaviour claims come from reading code and running the unit tests — not from observing the system work. This is stated at the top of `V1_INVENTORY.md` §4 as a boundary.
2. **The desktop render probe was not re-run** (`scripts/check_plugin_render.py` installs an npm sandbox on demand). Only the loader probe was executed.
3. **No Windows machine was available.** Every Windows command in the WPs is written from the platform contract (PowerShell, `py -3.12`, `platformdirs`, `CREATE_NEW_PROCESS_GROUP`) and is **unverified until WP-002's CI matrix runs it**. That is precisely why the Book puts a Windows job in CI rather than in a checklist.
4. **The Book's own scope was assumed, not negotiated.** This report treats the 29-phase roadmap and the 22-table schema as given. It does not argue for a smaller product — though C12 is, in effect, that argument arriving from the other direction.
5. **Documentation in this pass is in English** because the repository and the Book are, and the deliverables live in the repository.

---

## 8. Status of the next actions

1. ~~Answer **D-A** (trunk)~~ — **done 2026-10-05**: meta-harness v2 is the trunk; `harness-console` is Phase-0 evidence and a source of adapters.
2. ~~Execute the freeze (§4)~~ — **done 2026-10-05** in the raw form: tag `v0.1-hermes-hosted` on `3ef4a5c`, branch `v2/control-plane`, no local change mixed into the tag, no remote mutation. Details in §4.b.
3. **Next: record decisions D-B, D-C and D-D as ADRs** — `D-B` (SQLite vs JSONL as the source of truth), `D-C` (Hermes transport: TUI gateway JSON-RPC vs HTTP+SSE vs ACP-over-stdio), `D-D` (Pi transport: `--mode rpc` vs ACP). WP-003 depends on all three, and C12's resolution makes the FASE0 measurements the evidence base for D-C/D-D instead of a fresh test round.
4. **Then WP-002**, whose gate is the first artefact this project has never had: a daemon that answers `/health` on **Windows and Linux** with no Bash in the path.

WP-001's remaining cleanup items (D4 tracked bytecode, D3 dead dashboard door, C7 ADR renumbering, C8/C9 documentation truth) were **declined for the tag** and are now optional; the natural home for them is the first commit on `v2/control-plane`, not a v1 cleanup commit.

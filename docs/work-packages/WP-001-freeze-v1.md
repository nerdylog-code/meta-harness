# WP-001 — Freeze V1

**Owner:** cheap builder · **Wave:** W1 · **Depends on:** nothing · **Blocks:** everything

---

## Objective

Preserve the Hermes-hosted MVP as a permanent, installable, honestly-documented baseline, and leave a clean branch point for the v2 control plane. This package changes **no runtime behaviour**. Its only code-adjacent actions are index hygiene and the removal of provably dead files.

## Dependencies

None. This is the first package and everything else waits for its gate.

## Allowed files

```
.gitignore                                     (only if a pattern is genuinely missing)
README.md                                      (correct the stale verification table)
PROJECT_STATE.md                               (add an "Installation reality" correction)
LICENSE                                        (unchanged unless ADR C10 decides otherwise)
docs/DECISIONS.md  →  docs/adr/v1/ADR-V1-*.md  (move + renumber, content unchanged)
docs/architecture/V1_INVENTORY.md              (already produced — verify, do not rewrite)
docs/architecture/V1_CONFLICTS.md              (already produced — verify, do not rewrite)
docs/adr/ADR-0001-control-plane.md             (already produced — verify, do not rewrite)
hermes-plugin/dashboard/manifest.json          (delete — see D3)
hermes-plugin/dashboard/plugin_api.py          (delete — see D3)
*__pycache__/*.pyc                             (remove from the git index)
git refs                                       (local tag + local branch only)
```

## Forbidden files

```
hermes-plugin/hermes_plugin/**        (runtime code — frozen, do not touch)
hermes-plugin/__init__.py             (entry point — frozen)
hermes-plugin/plugin.yaml             (manifest — frozen)
desktop-plugin/plugin.js              (UI — frozen)
topologies/**  roles/**  character-packs/**   (content — frozen)
tests/**  scripts/install.*  scripts/doctor.*  scripts/uninstall.*   (frozen)
.github/workflows/ci.yml              (WP-002 widens it; this package does not)
```

Fixing D1/D2 (the built-in-content path bugs) is **explicitly out of scope** — v1 is frozen as it is, and v2 solves the same class of problem by construction. If the builder believes a fix is required for the tag to be useful, that is an **architecture deviation**: stop and escalate.

## Required reading

`docs/architecture/V1_INVENTORY.md` (facts, evidence, defects D1–D9) · `docs/architecture/V1_CONFLICTS.md` (C7, C8, C9) · `PROJECT_BOOK.md` §76 (PHASE 0) · §112 (documentation rule) · v1 `docs/DECISIONS.md`.

## Architecture constraints

1. The tag must reference a commit whose `python tests/run_all.py` is green (BOOK §76 gate).
2. `master` keeps the v1 line; v2 work happens on `v2/control-plane`. No force-push, no history rewrite, no remote mutation without explicit authorization.
3. Deleting the dashboard door is **removal of a surface that resolves to nothing** (`"entry": "plugin.js"`, `"css": "style.css"`, neither present). It is not a feature removal.
4. `PROJECT_STATE.md` stays a checkpoint, never a diary (BOOK §112). The correction is one short section, not a rewrite.
5. v1 ADRs move **verbatim** — different numbers, identical text, one added header line: `> Historical (v1 series). Superseded in the v2 line by docs/adr/ADR-*.md` (resolves C7).

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | Baseline tag exists and is annotated | `git tag --list` shows `v0.1-hermes-hosted`; `git cat-file -t` says `tag` |
| A2 | Tagged commit is green | `python tests/run_all.py` on the tag → `Ran 21 tests … OK`, exit 0 |
| A3 | No bytecode in the index | `git ls-files \| grep -cE 'pycache\|\.pyc'` → `0` |
| A4 | Dead door removed | no `dashboard/manifest.json` and no `dashboard/plugin_api.py` in the tree; `grep -rn "plugin_api\|style.css" hermes-plugin/` → no live reference |
| A5 | ADR collision resolved | `docs/adr/v1/` contains `ADR-V1-0001.md` … `ADR-V1-0015.md`; no v2-numbered file outside `docs/adr/ADR-0001-control-plane.md` |
| A6 | Honest docs | `README.md` no longer claims CI is unconfigured; `PROJECT_STATE.md` separates *verified in checkout* from *verified installed* |
| A7 | Branch exists and is clean | `git status -sb` on `v2/control-plane` → clean working tree; branch created **from the tag**, not from an uncommitted tree |
| A8 | CI still green on `master` | the push (when authorized) shows the `ci` workflow `success` |

## Expected output

Annotated tag `v0.1-hermes-hosted`, branch `v2/control-plane`, the recon documents committed on both lines, and a `docs/architecture/V1_INVENTORY.md` + `V1_CONFLICTS.md` + `ADR-0001-control-plane.md` triple that a reviewer can check line by line.

## Expected events

None. This package writes no runtime events and adds no event kinds.

## Windows requirements

- Every command above must run from **PowerShell** (`Get-ChildItem`, `git`, `python`). No Git Bash, no WSL.
- Deleting `__pycache__` from the index must not delete files on disk on Windows (a locked `.pyc` can fail a naive `Remove-Item`): use `git rm -r --cached` first, delete afterwards.
- `python scripts\doctor.sh` is **not** an accepted verification path on Windows — that is exactly why D6 exists. Note it, do not fix it here.

## Linux requirements

- Same commands from a POSIX shell. `python3` may be 3.14+ without PyYAML (measured on this host): the gate command must run under an interpreter that has PyYAML, and the package must state which one (see D9).

## Exact acceptance commands

POSIX (Linux/macOS):

```bash
git tag -a v0.1-hermes-hosted -m "v0.1 — Hermes-hosted MVP (frozen baseline)"
git checkout -b v2/control-plane v0.1-hermes-hosted
git ls-files | grep -cE 'pycache|\.pyc'                       # expect 0
~/.hermes/hermes-agent/venv/bin/python3 tests/run_all.py       # expect: Ran 21 tests, OK
git tag -l && git cat-file -t v0.1-hermes-hosted               # tag / tag
git status -sb                                                 # clean
# optional, real pre-existing install must remain valid:
bash scripts/doctor.sh "$HOME/.hermes"                         # expect PASS lines, unchanged behaviour
```

Windows (PowerShell, no WSL):

```powershell
git tag -a v0.1-hermes-hosted -m "v0.1 - Hermes-hosted MVP (frozen baseline)"
git checkout -b v2/control-plane v0.1-hermes-hosted
(git ls-files | Select-String -Pattern 'pycache|\.pyc').Count      # expect 0
py -3.12 -m pip install --quiet pyyaml
py -3.12 tests\run_all.py                                          # expect: Ran 21 tests, OK
git tag -l; git cat-file -t v0.1-hermes-hosted                     # v0.1-hermes-hosted / tag
git status -sb                                                     # clean
```

## Known risks

| Risk | Mitigation |
|---|---|
| Tagging a commit that still contains the dead door or tracked `.pyc` produces a permanently dirty baseline | Do the purge **before** tagging, and verify with A3/A4 on the tagged tree |
| A builder "helpfully" fixes D1/D2 in the frozen code | Explicitly forbidden above; deviation ⇒ stop and escalate |
| Moving `docs/DECISIONS.md` breaks inbound links from `README.md`/`PROJECT_STATE.md` | Update those two references in the same commit; ADR content itself is untouched |
| The local tag is mistaken for a released artifact | The report must state that nothing was pushed and the tag is local until authorized |

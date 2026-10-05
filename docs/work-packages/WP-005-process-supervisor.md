# WP-005 — Cross-platform ProcessSupervisor

**Owner:** Luna-class builder · **Wave:** W3 · **Depends on:** WP-002 · **Runs in parallel with:** WP-003 and WP-006 · **Blocks:** every runtime adapter

---

## Objective

Build the one component every runtime adapter depends on: a `ProcessSupervisor` that spawns, streams, interrupts, terminates and **kills a child process tree without leaving orphans** — identically on Windows and Linux. The Book puts this before any adapter (BOOK §79) because a runtime that cannot be reliably cancelled poisons everything above it.

## Dependencies

WP-002 green (monorepo, `scripts/*.py`, two-OS CI). Deliberately **independent of WP-003**: this is processes, not data.

## Allowed files

```
apps/daemon/metaharness/process/**           (new — the deliverable)
apps/daemon/metaharness/process/supervisor.py        (interface + factory)
apps/daemon/metaharness/process/posix.py             (PosixProcessSupervisor)
apps/daemon/metaharness/process/windows.py           (WindowsProcessSupervisor)
tests/unit/process/**  tests/integration/process/**
tests/fixtures/child_tree.py                          (a helper that spawns a grandchild)
docs/architecture/CROSS_PLATFORM.md                   (extend WP-002's version with the process model)
docs/adr/ADR-0008-process-supervisor.md
```

## Forbidden files

```
packages/contracts/**      (only if a needed type is missing: escalate, do not add)
apps/web/**  apps/desktop/**
hermes-plugin/**  desktop-plugin/**  plugins/runtimes/**   (adapters are a later wave)
```

## Required reading

`PROJECT_BOOK.md` §5 (cross-platform is a kernel requirement), §10.2/§10.3 (no shell strings; the supervisor contract), §79 (PHASE 3 + gate: *"zero orphan process"*), §82 (honest failure), §113 (definition of done: cancel behaviour is mandatory) · `docs/architecture/V1_INVENTORY.md` D6 (the Bash tax v1 paid) · `docs/architecture/V1_CONFLICTS.md` C6.

## Architecture constraints

1. **No `shell=True`.** `exec(argv, cwd=…)` only. There is no code path where a caller's string is interpreted by a shell (BOOK §10.2).
2. **One interface, two implementations.** `spawn / write_stdin / read_stdout / read_stderr / interrupt / terminate / kill_tree / health`. The OS difference is confined to `posix.py` / `windows.py`; callers never branch on platform.
3. **Linux:** new process group (`start_new_session=True`), `SIGINT` → `SIGTERM` → `SIGKILL` escalation with a bounded grace period, and the whole group signalled.
4. **Windows:** `CREATE_NEW_PROCESS_GROUP`, `psutil` recursive child discovery, `CTRL_BREAK_EVENT` where applicable, then `terminate()` → `kill()` over the discovered tree. A bare `proc.kill()` is insufficient and is a defect.
5. **Cancellation is verified, not assumed.** `kill_tree()` must re-walk the tree after the grace period and assert emptiness; returning without proof of zero descendants is a failure (BOOK §79 gate).
6. **Streams are drained concurrently** and never block the child: a process that fills a pipe buffer must not deadlock the supervisor.
7. **Output is attributed and bounded.** stdout/stderr are captured with per-stream size caps and overflow handled by truncation-with-marker, not by dropping the child (this is the seam tool-output virtualization will use later, BOOK §21).
8. **Exit is reported honestly:** exit code, signal (POSIX) or return code (Windows), duration, and whether the termination was requested. `None` is never reported as `0`.
9. **No orphan on daemon death:** if the daemon dies, children must not outlive it silently — this is what `kill_tree` plus the startup reconciliation of WP-004 (BOOK §83) jointly guarantee.
10. **The supervisor is kernel code, not a plugin** (BOOK §11/§18): resource budgets and process lifecycle are not delegable.

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | Spawn + capture | a child writes to stdout and stderr; both are captured with correct attribution |
| A2 | Stdin | a child reading stdin receives what the caller writes, in order |
| A3 | Exit reporting | a child exiting `3` reports `3`; a child killed by signal reports the signal, not `0` |
| A4 | **Zero orphans (grandchild)** | a child that spawns a grandchild is cancelled → after cancellation, **0** surviving descendants, verified by walking the process tree independently (e.g. `psutil` / `pgrep -P`) |
| A5 | Graceful-then-hard | a child ignoring `SIGTERM` is escalated to `SIGKILL` after the grace period, and the escalation is logged |
| A6 | No deadlock | a child emitting 50 MB to stdout finishes; the supervisor does not stall |
| A7 | Concurrent cancel | cancelling one child leaves sibling children running and untouched |
| A8 | Double cancel | cancelling twice is idempotent, with a clear second result, never an exception storm |
| A9 | Both OSes | A1–A8 pass on `ubuntu-latest` **and** `windows-latest` in CI (A4 and A5 are the two that differ) |
| A10 | Timeout | a max-wall-time budget terminates the tree and reports the reason as a budget outcome (BOOK §113) |

## Expected output

`ProcessSupervisor` with two OS implementations, a child-tree fixture, a CI job that proves A4/A5 on both platforms, `docs/architecture/CROSS_PLATFORM.md`'s process section, and ADR-0008.

## Expected events

```
system.process.spawned     { pid, argv_hash, cwd, engine_hint }
system.process.stdout      { pid, bytes, truncated }
system.process.stderr      { pid, bytes, truncated }
system.process.exited      { pid, returncode, signal, duration_ms, requested }
system.process.kill_tree   { root_pid, killed_pids, escalated, orphan_check }
```

`kill_tree` must include the `orphan_check` result — the Book's gate is *proof* of zero orphans, so the proof belongs in the event.

## Windows requirements

- Never `shell=True`; `.bat`/`.cmd` targets are invoked via `cmd /c` explicitly and only where a caller asks for it.
- A4 must use `psutil` recursive discovery, because Windows has no process groups in the POSIX sense; the test asserts the discovery actually ran.
- Killing a `python.exe` tree from a different session may require `CREATE_NEW_PROCESS_GROUP`; the test must run in a real subprocess, not in-process threads.
- Path arguments are never quoted-and-glued into a string; argv is passed as a list.

## Linux requirements

- Process group signalling (`os.killpg`) with `start_new_session=True`; A4 verifies no escaped descendant (a grandchild that calls `setsid()` must be reported as an escape, honestly, rather than claimed as killed).
- `SIGKILL` escalation path exercised in A5 within the CI time budget.

## Exact acceptance commands

POSIX:

```bash
git checkout v2/control-plane
uv sync
python scripts/test.py --suite process
# explicit orphan proof, independent of the test harness:
python -c "
import subprocess, time, os, metaharness_process as p
sup = p.supervisor()
h = sup.spawn(['python3','tests/fixtures/child_tree.py'], cwd='.')
time.sleep(1.0); sup.kill_tree(h)
print('descendants after cancel:', subprocess.run(['pgrep','-P',str(os.getpid())],capture_output=True,text=True).stdout.strip() or 'none')"
```

Windows (PowerShell):

```powershell
git checkout v2/control-plane
uv sync
python scripts\test.py --suite process
python -c "import metaharness_process as p, psutil; sup=p.supervisor(); h=sup.spawn(['py','tests/fixtures/child_tree.py']); import time; time.sleep(1.0); sup.kill_tree(h); print('children after cancel:', len(psutil.Process(h.pid).children(recursive=True)))"
```

CI cross-check:

```bash
gh run list -R nerdylog-code/meta-harness --workflow ci --limit 5   # both OSes green, process suite included
```

## Known risks

| Risk | Mitigation |
|---|---|
| The classic orphan bug: killing the parent while the grandchild survives | A4 is the gate, and it verifies independently of the supervisor's own bookkeeping |
| Windows tests passing locally because the child was launched in the same console | Force a real subprocess + `CREATE_NEW_PROCESS_GROUP`; run in CI |
| Timeout-based tests becoming flaky on slow CI runners | Bound with generous grace periods and assert *outcomes*, not exact timings |
| The supervisor quietly becoming a shell (`shell=True` "just for one command") | Constraint 1 is absolute; a caller needing shell semantics must compose it explicitly and visibly |
| Escalation logged but not verified | A5 asserts the escalation happened, not just that the process died |

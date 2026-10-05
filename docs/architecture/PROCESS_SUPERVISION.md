# Process supervision

Every runtime adapter depends on this component, which is why BOOK §79 puts it
before any adapter: a runtime that cannot be reliably cancelled poisons
everything above it. One interface, two OS implementations, callers never branch
on platform.

```
apps/daemon/metaharness/process/
  base.py      ProcessSupervisor — interface, tree logic, streams, verification
  posix.py     new session + process-group signals
  windows.py   CREATE_NEW_PROCESS_GROUP + psutil tree discovery
  __init__.py  supervisor() — the only place that looks at the OS
```

## Contract

| Method | Behaviour |
|---|---|
| `spawn(argv, cwd=…, env=…)` | `exec` an argv list. **A command string is refused** (`ProcessError`) — passing one is a shell habit and the shell is where the injection surface lives |
| `write_stdin` / `close_stdin` | ordered writes; a closed pipe raises a clear error instead of losing data silently |
| `stream(handle, "stdout"\|"stderr")` | async iterator over bounded, attributed chunks |
| `capture` / `preview` | the whole capture (bounded) and its last 4000 characters |
| `wait(handle, timeout_s=…)` | outcome with `returncode`, `signal`, `duration_ms`, `requested` and `reason` |
| `interrupt` | `SIGINT` to the group / `CTRL_BREAK_EVENT` |
| `terminate(handle, grace_s=…)` | `SIGTERM` → wait → escalate to `SIGKILL`, then verify |
| `kill_tree` | hard kill of the whole tree, then verify |
| `health` | pid, liveness, returncode, descendant count |
| `descendants` | live tree walk (psutil, works on both OSes) |

`on_event` is an optional callback that receives `system.process.*` events, so
the kernel can be observed without importing the app or the event bus.

## The invariant, and how it is proven

> Cancelling leaves **zero** orphan processes.

The gate is `tests/integration/test_process_supervisor.py::test_a4_*`. It spawns
a child that spawns two grandchildren **in their own session** (`setsid`), then
kills the tree and checks the OS — not the supervisor's own bookkeeping — for
survivors. `KillReport.orphan_check` is only true when nothing survived, and the
same flag is part of the emitted `system.process.kill_tree` payload, so the proof
travels with the event instead of living only in a test.

### The orphan bug this design exists to prevent (found, then fixed, then measured)

The first implementation walked the tree **after** signalling. The root died
instantly, its children were reparented, `psutil.Process(root).children()` then
returned `[]`, and the grandchildren survived while the supervisor reported
success. The test caught it immediately; the fix is a **pre-signal snapshot**:

1. enumerate and remember the live tree **before** any signal (`_snapshot`);
2. signal the group, then sweep every remembered pid individually — this is what
   reaches a descendant that called `setsid()` and a process reparented by the
   death of its parent in step 2;
3. verify against the union of *currently reachable* descendants and *every pid
   ever observed* for that root (`_known`), so a reparented grandchild cannot
   hide behind a dead parent.

Measured effect on this host: the pre-fix implementation leaked **8 orphaned
grandchildren** across two runs (all created 17:57–18:00, all reparented to the
session leader). After the fix, two consecutive full runs (18:01 and 18:02)
leaked **zero**. `KillReport.escaped` additionally lists processes that had
already left the session, so a group signal's blind spot is reported rather than
assumed away.

### Second bug, found by the Windows CI matrix (not by Linux)

The first CI run of the matrix failed the whole supervisor suite on Windows with
eight errors and one signature: `psutil.NoSuchProcess` raised out of
`kill_tree`. Cause: the live tree walk called
`psutil.Process(pid).children(recursive=True)`, and on Windows a process is
terminated and reaped **immediately**, so the walk frequently runs against a tree
that is disappearing underneath it. Linux was forgiving enough that 13/13 passed
locally for hours.

Fix: every walk is guarded. A tree that is already gone is not an error — it is
the desired end state — so `descendants()` returns an empty list instead of
raising, in both `base.py` and `windows.py`.

The same run also exposed a **test** that encoded a POSIX behaviour as a
universal rule: A5 asserted `escalated == True` after `terminate()`. On Windows
there is no catchable graceful termination for a plain child, so `terminate()` is
a single hard step and there is nothing to escalate *from*. A5 now asserts the
universal invariant (tree gone, zero survivors) on both platforms, asserts
escalation only where the platform can send a catchable signal, and a new A5b
covers the platform-neutral path (`interrupt()` then `terminate()`).

This is the concrete reason the Book puts Windows in CI rather than in a
checklist: Linux green was not evidence.

## Platform differences
| Concern | Linux/macOS | Windows |
|---|---|---|
| Isolation for signalling | `start_new_session=True` → own session and process group | `CREATE_NEW_PROCESS_GROUP` |
| Group signal | `os.killpg(os.getpgid(pid), sig)` | no equivalent; per-process |
| Graceful interrupt | `SIGINT` to the group | `CTRL_BREAK_EVENT` (falls back to terminate when unavailable) |
| Tree discovery | `psutil` recursive children | `psutil` recursive children |
| Hard kill order | children first, then the root | **deepest-first** by parent depth |
| Escape detection | `os.getsid(pid) != os.getsid(root)` | n/a (no sessions); the psutil walk is the guarantee |

## Streams

- One drain task per pipe, started at spawn, so a child that fills a pipe buffer
  cannot deadlock the supervisor (test A6 pushes 4 MB through stdout).
- Capture is bounded (`max_stream_bytes`, default 8 MB); beyond it the stream is
  truncated **with a marker** rather than dropped silently.
- A slow consumer never blocks the plane: chunks go to a bounded queue.

## Budgets

`wall_timeout_s` (default 900 s) is enforced by `wait()`: on expiry the tree is
killed and the outcome reports `reason="wall_timeout…"` with `requested=True`.
A budget stop is a first-class result, not an exception (BOOK §12/§113).

## Honest limits

- The **Windows path has not executed yet.** The matrix runs it on the first
  push, which is not authorised; until then Windows support here is a written
  contract with tests that target it, not a measured fact.
- `interrupt` on Windows without an attached console falls back to terminate.
- Zombie reaping depends on the event loop; `kill_tree` verifies with `psutil`
  (`STATUS_ZOMBIE` is not counted as alive) but a supervisor process that crashes
  mid-kill is not covered — WP-004's startup reconciliation (BOOK §83) is the
  answer for that case, and it does not exist yet.

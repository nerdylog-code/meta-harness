# Cross-platform contract (Windows + Linux)

Windows and Linux are first-class from the beginning (BOOK §5/§68). This file
records the rules the skeleton already enforces and the ones later work packages
must not break.

## 1. Paths

| Rule | Why |
|---|---|
| User data comes from `platformdirs` | `%LOCALAPPDATA%\MetaHarness\…` on Windows, `~/.local/share/MetaHarness/…` on Linux, `~/Library/Application Support/…` on macOS — one API, no branching |
| `METAHARNESS_DATA_DIR` overrides it | tests and portable installs must never touch the real user directory |
| **No `Path(__file__).parents[N]` for content** | v1's D1 defect: right in a checkout, wrong in every install. Assets live in the data root; a source checkout is *discovered* (`find_repo_root`), never assumed |
| No hardcoded separators or home directories | `Path` does the joining; `os.environ`/`platformdirs` do home resolution |
| Paths are normalized when stored | an `ArtifactRef.path` is normalized at construction, with the original preserved in metadata (WP-003) |

Resolved data root is printed by `python scripts/doctor.py` and returned by
`GET /health` (`data_root`) — where a run writes is never a guess.

## 2. Processes

| Rule | Why |
|---|---|
| `exec(["argv", …], cwd=…)` — never `shell=True`, never a command string | one injection surface removed, identical argv semantics on both OSes |
| `ProcessSupervisor` behind one interface (`spawn/write_stdin/read_stdout/read_stderr/interrupt/terminate/kill_tree/health`) | callers never branch on platform (WP-005) |
| Linux: process group + `SIGINT`→`SIGTERM`→`SIGKILL` escalation on the whole group | children cannot escape by ignoring a signal |
| Windows: `CREATE_NEW_PROCESS_GROUP` + `psutil` recursive discovery + `terminate()`→`kill()` over the tree | Windows has no POSIX process groups; a bare `proc.kill()` leaves grandchildren alive |
| **Cancellation is verified, not assumed** — the tree is re-walked after the grace period | the BOOK §79 gate is "zero orphan process"; the proof belongs in the event payload |
| Streams are drained concurrently with bounded buffers | a child filling a pipe buffer must not deadlock the supervisor |

## 3. Ports, binding and signals

- Default bind is `127.0.0.1`; the app **refuses non-loopback peers** on HTTP and
  WebSocket scopes (pure-ASGI guard). Remote access is a separate, explicit
  future feature (BOOK §66).
- `scripts/dev.py` reuses the preferred port or picks a free one; it prints the
  bound URL, so scripts and humans never parse a log to find the port.
- Shutdown is graceful in both directions: `system.daemon.stopping` is emitted
  before exit, which is also what makes a clean stop distinguishable from a
  crash in the event plane.

## 4. Toolchain and commands

`python scripts/dev.py | test.py | doctor.py | package.py` must work from
**PowerShell** and from a POSIX shell. Consequences:

- no Bash requirement anywhere in the core path (v1 shipped two families of
  `.sh`/`.bat` scripts and still had Linux-only CI — that is the exact cost this
  rule removes);
- no `make`/`just` requirement (they may exist as conveniences);
- scripts locate their own interpreter and repository root; they never assume
  `python3` (Windows has `py`/`python`) and never assume a CWD.

## 5. CI

`.github/workflows/ci.yml` runs a matrix over **`ubuntu-latest` and
`windows-latest`**: dependencies, unit suite, integration suite (real server +
websockets) and the frozen v1 regression suite. Additionally, a web job runs
only when `apps/web/package.json` exists, so the skeleton's CI stays green
before the UI lands.

Merge rule, from BOOK §68: **no merge with Windows red.** A Linux-only green
run is not evidence of a working cross-platform skeleton.

## 6. Known cross-platform gaps (honest list)

| Gap | Status |
|---|---|
| Windows CI has not yet executed the current tree (the workflow exists; the first push will tell) | pending authorisation to push |
| No Windows-specific sandbox/isolation provider | later phases; worktree isolation is the default and needs no sandbox |
| Tauri bundle (MSI/NSIS) | WP-007 |
| Packaged daemon without a Python requirement | PHASE 27 |
| File locking around SQLite WAL on Windows | WP-004 runs its restart/crash tests on Windows CI |

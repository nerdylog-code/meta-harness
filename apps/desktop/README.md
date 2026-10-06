# Meta-Harness desktop shell (WP-007)

A Tauri v2 shell that starts the daemon on loopback and loads the same web UI the browser uses.
The shell is **optional by design**: `python scripts/dev.py` and a browser keep working without
it (PROJECT_BOOK §4/§6), and a test fails if anything in `apps/web/src` so much as mentions it.

```
Meta-Harness (Tauri window, Rust)      ← a window, a pipe, a shutdown. Nothing else.
        │ spawns and holds a pipe to
apps/desktop/host/desktop_host.py      ← all the logic: attach or spawn, wait for /health,
        │                                 kill the tree on stdin EOF, report orphan_check
        ▼
python -m metaharness                  ← the daemon, unmodified
```

Why the logic is Python and not Rust: WP-007 forbids the shell from accreting business logic and
requires the daemon's lifecycle to go through WP-005's `ProcessSupervisor`. Keeping the lifecycle
in a Python host satisfies both, and it means A2/A3/A5/A6 are testable with no Rust toolchain —
so they run in the CI matrix on both OSes. See `docs/adr/ADR-0009-tauri-shell.md`.

## Build and run

```bash
cd apps/desktop
pnpm install
pnpm tauri dev        # dev window
pnpm tauri build      # AppImage + deb (Linux) / nsis + msi (Windows)
```

## Verify

```bash
uv run python scripts/test.py --suite desktop     # A2/A3/A5/A6 — no Rust, no display
uv run python apps/desktop/smoke_shell.py         # A1/A3 — opens a window briefly, then closes it
```

`smoke_shell.py` launches the raw binary rather than the AppImage on purpose: an AppImage needs
FUSE, and a FUSE failure would tell us nothing about the shell.

## Files

| Path | What it is |
| --- | --- |
| `host/desktop_host.py` | the shell's logic: attach/spawn/health/shutdown, one JSON status line per state |
| `src-tauri/src/main.rs` | the window: spawns the host, reads the status line, navigates or shows the failure |
| `src-tauri/tauri.conf.json` | window + bundle config; `withGlobalTauri: false` |
| `src-tauri/capabilities/default.json` | `core:default` only — no shell, no fs, no http for the renderer |
| `ui/index.html` | the boot page, replaced by the daemon's UI or by the failure reason |
| `smoke_shell.py` | A1/A3 evidence against a built binary |

Requirements a user still needs, and the known gaps (no desktop events yet, no per-launch token,
Windows bundle unverified here) are recorded in `docs/architecture/PACKAGING.md` rather than
implied to be solved.

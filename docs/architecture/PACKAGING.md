# Packaging the desktop shell (WP-007)

**What this is:** the Tauri v2 shell that starts the daemon on loopback and loads the same web UI
the browser uses. **What it is not:** a second implementation of anything. The shell has no
domain logic, no HTTP client, no process-tree code, and it holds no secret.

## What a user needs installed at this stage

Honest answer, because the alternative is a lie that fails on a clean machine:

| Requirement | Why | Status |
| --- | --- | --- |
| Python 3.12+ with the project's dependencies (`uv sync`) | the daemon **and** the shell's host are Python | **required today** |
| A WebKitGTK runtime (Linux) / WebView2 (Windows) | the window | required, provided by the OS or installed |
| Node/pnpm | only to *build* the web bundle, not to run it | not required at runtime once `apps/web/dist` exists |
| Rust/cargo | only to *build* the shell | not required at runtime |

Packaging the daemon itself (no Python required) is PHASE 27 (BOOK §103) and is **not** done here.
A8 records exactly this rather than claiming a self-contained bundle.

## Build

```bash
cd apps/desktop
pnpm install          # @tauri-apps/cli, pinned exactly
pnpm tauri build      # bundles under src-tauri/target/release/bundle/
```

Linux produced (verified, this checkout):

```
src-tauri/target/release/bundle/appimage/Meta-Harness_0.2.0_amd64.AppImage   97.08 MiB
src-tauri/target/release/bundle/deb/Meta-Harness_0.2.0_amd64.deb              1.33 MiB
```

Windows targets `nsis` and `msi` (same command on a Windows host). **A Windows bundle was not
produced here** — no Windows machine was involved. The Windows job in CI is opt-in and does not
build the shell yet; the honest statement is *not verified on Windows*, not "works on Windows".

## How the shell works, and why it is shaped this way

```
Meta-Harness (Tauri window, Rust, ~200 lines)
   │  spawns, and holds a pipe to
   ▼
apps/desktop/host/desktop_host.py   ← all the logic lives here, in Python
   │  attaches to an existing daemon, or spawns one through WP-005's ProcessSupervisor,
   │  waits for /health, kills the tree on stdin EOF
   ▼
python -m metaharness               ← the daemon, unmodified
```

Three decisions worth recording:

1. **The logic is Python, not Rust.** WP-007 says the shell must not accrete business logic and
   must reuse the supervisor instead of growing a second process implementation. Both are
   satisfied by keeping the lifecycle in a Python host and letting Rust own a window and a pipe.
   It also means A2/A3/A5/A6 are testable without a Rust toolchain — they run in the CI matrix.
2. **The window loads the daemon's URL, not a copy of the UI.** Loading `apps/web/dist` from the
   app's own origin would make every `fetch` a cross-origin request to the daemon, and the daemon
   sends no CORS headers (it never needed to). Navigating to `http://127.0.0.1:<port>` keeps the
   UI on the origin that served it.
3. **A taken port is not a dead end.** If the configured port is held by something that is not our
   daemon, the host starts on a fresh port and says so (`port_source: "fallback"`), which the
   window renders. If a *Meta-Harness* daemon is already there, the shell attaches to it instead
   of starting a second one.

## Shutdown

Closing the window closes the host's stdin. That is the whole protocol: the host then calls the
supervisor's `kill_tree`, verifies with `orphan_check`, and reports `survivors`. Verified by
`smoke_shell.py`: after the window goes away, the daemon process is gone and the port is closed.

## Verification

```bash
# A2/A3/A5/A6 — no Rust, no display, runs in CI on both OSes
uv run python scripts/test.py --suite desktop

# A1/A3 — needs a display and a built binary (opens a window briefly, then closes it)
uv run python apps/desktop/smoke_shell.py
```

`smoke_shell.py` launches the raw binary rather than the AppImage on purpose: an AppImage needs
FUSE, and a FUSE failure would tell us nothing about the shell.

## Known gaps, recorded rather than hidden

* **`system.desktop.started` / `system.desktop.daemon_failed` are not emitted.** WP-007's expected
  events need a daemon-side endpoint or a daemon-side desktop awareness, and WP-007 explicitly
  forbids touching `apps/daemon/**`. Today the shell's own log
  (`<data root>/logs/desktop-shell.log`, one JSON status line per state change) is the record.
  The events land when the daemon gains that surface, in a later package.
* **No per-launch auth token.** BOOK §65 describes the desktop receiving a secret over a pipe and
  proxying through Rust. The daemon has no token yet (the enforced property is loopback-only), and
  the shell must not invent one: `child_env()` passes four `METAHARNESS_*` variables and nothing
  else, and a test asserts it. A6 is therefore "the renderer is granted nothing and receives no
  secret" — true and checked — not "the token is proxied".
* **Windows bundle and Windows smoke are not verified here.** The host's Windows path goes through
  the same supervisor the Windows CI already exercises (WP-005), but that is an argument, not
  evidence. Marked unverified.

# WP-007 — Tauri skeleton

**Owner:** Luna-class builder · **Wave:** W4 · **Depends on:** WP-006 (build output + interface)

---

## Objective

Make the product installable as a desktop application on Windows and Linux: a Tauri v2 shell that starts the daemon and loads the same web UI. The desktop is a **shell and nothing else** — no business logic, no second source of truth (BOOK §4/§6).

## Dependencies

WP-006 green: the web app builds, and the daemon speaks HTTP + WS on `127.0.0.1` with a per-launch token.

## Allowed files

```
apps/desktop/**                     (new — the deliverable)
apps/desktop/src-tauri/**           (Rust shell, tauri.conf.json, capabilities, icons)
apps/desktop/src-tauri/tauri.*.conf.json
scripts/dev.py  scripts/package.py  (extend: desktop target)
docs/architecture/PACKAGING.md      (new)
docs/adr/ADR-0009-tauri-shell.md
```

## Forbidden files

```
apps/daemon/**                      (the shell consumes the daemon; it does not modify it)
apps/web/**                         (WP-006 owns it; the shell only loads its output)
packages/contracts/**
hermes-plugin/**  desktop-plugin/**
```

## Required reading

`PROJECT_BOOK.md` §4/§6 (Tauri is a shell; the app must keep working without it), §65 (local auth: the desktop receives the secret over a pipe/env, the browser receives a bootstrap cookie), §66 (remote mode is explicitly later), §67 (packaging: MSI/NSIS on Windows, AppImage/deb on Linux), §103 (PHASE 27 gate: clean install with no Python/Node required — note that this package is the *shell*; the packaged daemon is a later phase) · `docs/architecture/CROSS_PLATFORM.md` · `docs/architecture/UI.md`.

## Architecture constraints

1. **The shell must be optional.** `python scripts/dev.py` and the browser path keep working with no Tauri installed — "a aplicação continuará funcionando sem Tauri" (BOOK §4). A test asserts this.
2. **The daemon is started by the shell, then owned by it**: the shell keeps the child handle and kills the tree on exit (reuse WP-005's supervisor — no new process code).
3. **The secret never reaches the renderer** (BOOK §65; v1 ADR-0010's rule survives): it is passed to the daemon over a pipe/env and used by Rust-side proxying, never injected into the web context as a readable value.
4. **No remote bind.** `127.0.0.1` only; remote access is a later, explicit configuration (BOOK §66).
5. **Single-instance behaviour**: a second launch must not spawn a second daemon on a conflicting port; it focuses the existing window or starts on a fresh port with a clear message.
6. **Clean shutdown**: closing the window stops the daemon and leaves **no orphan process** (WP-005's gate applies through the shell too).
7. **Failure is visible**: if the daemon fails to start, the window shows the reason and the log path — never a blank window.
8. **Icons/branding**: use project-owned assets only. No third-party game art (v1 ADR-0007's rule survives).

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | Launch | the shell starts the daemon and shows the UI with live health + events |
| A2 | Port collision | with a daemon already on the configured port, the shell reports it clearly instead of hanging or double-starting |
| A3 | Shutdown | closing the window stops the child tree; an independent check finds 0 orphans |
| A4 | Crash handling | killing the daemon externally surfaces an explicit error in the window (with the log path), not a blank page |
| A5 | Headless independence | with Tauri absent, `python scripts/dev.py` + browser still work; asserted by a test that does not import Tauri |
| A6 | Secret handling | a network/state inspection finds no daemon secret readable from the web context |
| A7 | Bundles | `tauri build` produces an **MSI/NSIS** on Windows and an **AppImage or deb** on Linux |
| A8 | Clean-machine install | on a machine without Node or Rust, the produced bundle runs and shows the UI (Python packaging of the daemon is the later PHASE 27 item; this test records exactly which runtime is still required, honestly) |

## Expected output

`apps/desktop` with a working Tauri v2 shell, per-OS bundles built in CI on both platforms, `docs/architecture/PACKAGING.md` describing what a user must have installed at this stage, and ADR-0009.

## Expected events

```
system.desktop.started    { shell_version, daemon_port, bundled }
system.desktop.daemon_failed { reason, log_path }
system.daemon.stopping    { reason: "desktop_exit" }
```

## Windows requirements

- `tauri build` produces MSI/NSIS; the bundle is smoke-tested in CI.
- Process shutdown uses the WP-005 Windows implementation (`CREATE_NEW_PROCESS_GROUP` + `psutil` tree discovery) so A3 is real, not nominal.
- WebView2 availability is reported explicitly when missing (local-first means "works offline", not "fails silently").
- The daemon's data root resolves via `platformdirs` under `%LOCALAPPDATA%`.

## Linux requirements

- AppImage and/or deb produced; the bundle is launched in CI (xvfb) at least to the point of showing the window and connecting to the daemon.
- No dependency on a desktop environment beyond a WebKitGTK runtime; the missing-runtime case is a clear message.

## Exact acceptance commands

POSIX:

```bash
git checkout v2/control-plane
cd apps/desktop
pnpm install
pnpm tauri build                       # expect AppImage/deb artifacts under src-tauri/target/release/bundle/
./src-tauri/target/release/bundle/appimage/*.AppImage &
python scripts/test.py --suite desktop  # A2/A3/A5/A6 assertions
cd ../..
python scripts/dev.py --no-browser &    # A5: works without Tauri
```

Windows (PowerShell):

```powershell
git checkout v2/control-plane
cd apps\desktop
pnpm install
pnpm tauri build                       # expect MSI/NSIS under src-tauri\target\release\bundle\
python ..\..\scripts\test.py --suite desktop
cd ..\..
Start-Process -NoNewWindow python -ArgumentList "scripts\dev.py","--no-browser"
```

CI cross-check (bundles on both platforms):

```bash
gh run list -R nerdylog-code/meta-harness --limit 5
gh release view --repo nerdylog-code/meta-harness    # only after explicit authorization; nothing is published by this WP
```

## Known risks

| Risk | Mitigation |
|---|---|
| Rust/Tauri toolchain making Windows CI slow or red for unrelated reasons | Keep the desktop job opt-in in early waves; the Book's Windows-red rule applies to the *default* matrix, and this package must still not merge with a red desktop job once the job exists |
| The shell accreting business logic ("just this one API call in Rust") | Forbidden: everything goes through the daemon over HTTP/WS |
| Publishing artifacts without authorization | Packaging builds locally/in CI; **no release, no upload** without explicit human approval |
| A8 over-claiming "no Python/Node needed" before the daemon is packaged | Record the real requirement now; the Book's clean-install gate belongs to PHASE 27 |
| Orphaned daemon after a shell crash | WP-005's supervisor + WP-004's startup reconciliation cover it; A3/A4 assert it |

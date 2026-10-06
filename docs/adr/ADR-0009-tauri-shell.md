# ADR-0009 — The desktop shell is a window, and its logic is Python

**Status:** accepted (WP-007)
**Supersedes:** nothing. **Related:** ADR-0001 (independent control plane), BOOK §4/§6 (Tauri is a
shell; the application keeps working without it), §65 (local auth), §79 (no orphans), §103
(clean-install gate belongs to PHASE 27).

## Context

The product must be installable as a desktop application on Windows and Linux. Two failure modes
were predictable from the v1 experience and from the work packages already delivered:

1. **The shell becomes a second product.** Every "just this one API call in Rust" moves domain
   behaviour out of the daemon, and the desktop quietly becomes the place where the real
   implementation lives — with the browser path falling behind.
2. **The shell grows a second process implementation.** WP-005 already solved cross-platform
   spawn/kill/orphan-verification, and it was paid for with a real bug (a tree that dies while
   being walked, on Windows). Re-solving that in Rust would mean paying for it again, in a second
   language, with a second test suite.

## Decision

The Tauri shell owns a window, a pipe, and a shutdown. All of the lifecycle logic lives in
`apps/desktop/host/desktop_host.py`, in Python, reusing `metaharness.process.ProcessSupervisor`:

* the shell spawns the host and reads **one JSON status line** from it
  (`attached` / `started` / `failed` / `stopped`);
* the host decides whether to attach to an existing daemon or start one, waits for `/health`,
  and on stdin EOF kills the tree and reports `orphan_check` + `survivors`;
* the window is navigated to the daemon's own URL, so the UI keeps the origin that served it;
* the renderer is granted no capability (`capabilities/default.json` has `core:default` only) and
  no secret is passed to it.

The Rust side is forbidden from: holding domain types, speaking HTTP to the daemon, implementing
process trees, or receiving credentials.

## Consequences

* A2/A3/A5/A6 are testable without a Rust toolchain and therefore run in the CI matrix on both
  OSes; only A1/A4/A7 need a built shell.
* The shell is optional in a way that is asserted, not asserted-about: a test fails if anything in
  `apps/web/src` mentions the shell, and `scripts/dev.py` keeps working untouched.
* The status-line protocol is the shell's contract with the host. Adding a state means adding a
  line, and the shell's failure path (reason + log path, never a blank window) is part of it.
* The desktop carries a Python requirement until the daemon is packaged (PHASE 27). That is
  recorded in `PACKAGING.md` instead of being papered over.
* Two of WP-007's expected events (`system.desktop.started`, `system.desktop.daemon_failed`) are
  **not** emitted, because emitting them requires a daemon-side surface and WP-007 forbids
  modifying `apps/daemon/**`. They are listed as a known gap, not quietly dropped.

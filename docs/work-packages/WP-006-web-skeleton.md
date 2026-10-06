# WP-006 — Web skeleton

**Owner:** Flash-class builder · **Wave:** W3 · **Depends on:** WP-002 · **Runs in parallel with:** WP-003 and WP-005 · **Feeds:** WP-007

**Status: done.** What was built, and the deviations that were decided rather than assumed:

- **Router:** TanStack Router with **code-based** routes (`src/router.tsx`). File-based routing
  would add a codegen plugin and a second source of truth for four destinations; the Book's
  full information architecture is worth revisiting this for, and that is recorded here.
- **Query:** TanStack Query with one shared `QueryClient`; the daemon stays the system of
  record and nothing domain-shaped is cached client-side.
- **One websocket.** The stream lives in a context provider (`src/stream.tsx`) because two
  components need it and two sockets would make the daemon's subscriber count a lie.
  Connection state is three-valued — `connecting` / `live` / `degraded` — with capped
  exponential backoff, and the inspector always says whether it is showing the live socket or
  the durable backlog. A dead socket is never rendered as "no events".
- **Shells:** missions, agents and runtime pages derive what they can from the log and state
  plainly what does not exist yet. No invented rows, no always-zero gauges, no "create agent"
  button the daemon could not honour.
- **Deferred on purpose:** Tailwind and Radix (BOOK §9.2). They belong with the design system
  work (PHASE 8); the skeleton uses plain CSS tokens as the seam, so introducing them later
  replaces one file instead of every component.
- **SPA fallback added to the daemon.** Client-side routing needs it: `StaticFiles(html=True)`
  answers 404 for a deep link. The daemon now serves the shell for non-API paths, keeps real
  files winning, refuses traversal with 403, and still answers 404 JSON for `/v1/*`.
- **Supply chain:** `@tanstack/react-router` is pinned to an exact version (`1.170.41`)
  because 1.169.5 and 1.169.8 are flagged as malicious; a caret range could resolve to one.

**Verified in a real browser**, not by inspection: built bundle served by a live daemon,
rendered in headless Chromium — the shell, sidebar, store panel, reconciliation panel and
event table all present, the connection badge reading `live` (the socket really connected),
5 event rows on the overview and 6 in the inspector with their real kinds, an explicit empty
state on `/agents`, deep links returning the shell (200), `/v1/nope` returning JSON 404, and
`curl --path-as-is` traversal attempts (`../`, `%2e%2e`, mixed) all refused with 403 and no
file content.

---

## Objective

Turn `apps/web` from WP-002's placeholder into the real application shell: the Vite/React/TypeScript app that talks to the daemon over HTTP + WebSocket and renders live events. This package builds the **frame** (routing, data layer, layout, health/live-events view) — not the product screens, which belong to the later Control UI phase (BOOK §84).

## Dependencies

WP-002 green (daemon serving `/health`, `/version`, `/events/ws`; workspace manifests in place).

## Allowed files

```
apps/web/**                        (the deliverable)
packages/ui-kit/**                 (only primitives actually used in this package)
docs/architecture/UI.md            (new — information architecture as data, BOOK §59/§53)
tests/e2e/web/**                   (build + smoke)
```

## Forbidden files

```
apps/daemon/**            (consume the API; do not extend it here)
apps/desktop/**  packages/contracts/**
hermes-plugin/**  desktop-plugin/**
```

Do **not** port v1's `desktop-plugin/plugin.js`. It is bound to the Hermes Desktop import allowlist (CONFLICTS C11) and cannot run outside that host.

## Required reading

`PROJECT_BOOK.md` §4 (web UI stack), §53/§59 (information architecture), §60–§61 (Mission/Agent screens), §63 (live activity is observability, not decoration), §64 (API + event stream), §65 (local auth/bootstrap token), §82 (no fake green) · `docs/architecture/V1_INVENTORY.md` §5 (`desktop-plugin/plugin.js` row) · `docs/architecture/V1_CONFLICTS.md` C11.

## Architecture constraints

1. **The daemon is the source of truth; the UI is a projection** (BOOK §5.5). No domain state is invented client-side. TanStack Query owns server state; any local store holds *view* state only.
2. **Real-time via WebSocket, with a polling fallback** — v1 proved this pattern works (`api.py` has `/runs/{id}/events/ws`), and the Book keeps the same shape at `/v1/events/ws`.
3. **Every event carries provenance** (`provider_reported` / `measured` / `estimated` / `unknown`) and the UI must **display** it (BOOK §17/§82). An estimated cost must never look like a measured one.
4. **Explicit failure states are first-class UI states**: `runtime unavailable`, `unsupported capability`, `budget exceeded`, `approval required`, `context degraded`, `weak isolation`, `partial tool failure` (BOOK §82). "No fake green" is an acceptance criterion, not styling advice.
5. **Auth bootstrap**: the page acquires a per-launch token during bootstrap and never receives provider secrets (BOOK §65; v1 ADR-0010's rule survives).
6. **Accessibility and keyboard-first navigation** are baseline: the shell must be navigable without a mouse, with visible focus, since the Book's target is daily use (BOOK §84).
7. **No card-grid dashboard reflex.** The frame must express hierarchy and density (roster, active work, inspector), not a wall of equally-weighted cards — this is the explicit design direction of the product's shell, and a generic tile grid is a rejection, not a style preference.
8. **Vite + TypeScript + TanStack Router/Query + Tailwind + Radix primitives**, per BOOK §4. No additional state framework in this package.
9. **No build step added to the daemon's critical path**: `python scripts/dev.py` must still work when the web bundle is absent (degraded, with a clear message).

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | Build | `pnpm build` in `apps/web` succeeds with zero TypeScript errors |
| A2 | Dev loop | `python scripts/dev.py` serves the app against a live daemon |
| A3 | Health | the shell shows daemon status, version, git sha, resolved data root |
| A4 | Live events | a daemon-side event appears in the UI without a page reload, over the WS; disconnecting the WS degrades to polling with a visible indicator |
| A5 | Provenance rendering | a fixture event marked `estimated` is visibly distinguishable from a `provider_reported` one |
| A6 | Failure states | a fixture `runtime unavailable` and a fixture `unsupported capability` render as explicit errors, never as an empty state or a spinner that never resolves |
| A7 | Keyboard | full navigation of the shell without a mouse; focus visible at every step |
| A8 | No secrets | a network-trace assertion shows no provider key or vault value in any response the UI consumes |
| A9 | Both OSes | A1–A3 pass on Windows and Linux (Node/pnpm path differences only) |

## Expected output

The app shell (routing, layout regions, query client, WS client with fallback, token bootstrap), a live-events view, explicit error surfaces, `docs/architecture/UI.md` describing the information architecture as data, and a build that the Tauri shell (WP-007) can bundle.

## Expected events

Consumes `system.*` (health, daemon lifecycle) and renders any canonical event generically. Emits **client-side UI intent only** — e.g. a `ui.action.requested` record — never domain events, which only the daemon may author.

## Windows requirements

- `pnpm build` and `python scripts\dev.py` from PowerShell; no Bash-dependent npm scripts (no `NODE_OPTIONS` shell tricks, no `&&`-chained POSIX scripts).
- Path handling for the built bundle uses Node's `path`, never string concatenation.
- The dev server binds `127.0.0.1` only.

## Linux requirements

- Same commands from POSIX shells; the bundle output path is identical on both OSes (asserted, so WP-007 can rely on it).

## Exact acceptance commands

POSIX:

```bash
git checkout v2/control-plane
uv sync
cd apps/web && pnpm install && pnpm build && cd ../..
python scripts/dev.py &                       # daemon + web
# browser-side check (headless):
python scripts/test.py --suite web-e2e
curl -s http://127.0.0.1:<port>/health
```

Windows (PowerShell):

```powershell
git checkout v2/control-plane
uv sync
cd apps\web; pnpm install; pnpm build; cd ..\..
Start-Process -NoNewWindow python -ArgumentList "scripts\dev.py","--no-browser"
python scripts\test.py --suite web-e2e
Invoke-RestMethod http://127.0.0.1:<port>/health
```

## Known risks

| Risk | Mitigation |
|---|---|
| The frame quietly becomes the Control UI phase (chat, canvas, workboard) and never ships | A1–A9 are the scope; the app shell is *one view plus the plumbing*. BOOK §84 owns the product screens |
| A WebSocket-only design that breaks when the socket is blocked | A4 requires the polling fallback to work, not just exist in code |
| Generic card-grid drift | Constraint 7; the reviewer must reject tile-wall layouts |
| Est./measured provenance being flattened for convenience | A5 makes it a gate, because every later benchmark claim depends on it |
| Node/pnpm version drift between the developer machine and CI | Pin the toolchain in the workspace manifest; A9 runs both OSes |

# WP-002 — V2 repository skeleton

**Owner:** Luna-class builder · **Wave:** W2 · **Depends on:** WP-001 (tag green) · **Blocks:** WP-003, WP-005, WP-006

---

## Objective

Create the monorepo skeleton of the v2 control plane and prove it boots on **Windows and Linux** with no Bash in the path. Deliverable is a daemon that answers `GET /health` and `GET /version` over HTTP and accepts a WebSocket connection on `WS /events/ws`, plus the canonical Python entry points and the widened CI matrix.

## Dependencies

WP-001 green (baseline tag + `v2/control-plane` branch existing, index clean).

## Allowed files

```
apps/daemon/**                 (new)
apps/web/**                    (new — placeholder app only; WP-006 owns the UI)
apps/desktop/**                (new — placeholder only; WP-007 owns Tauri)
packages/contracts/**          (directory + __init__ only; WP-003 owns content)
packages/plugin-sdk/**         (directory only)
packages/ui-kit/**             (directory only)
packages/benchmark-spec/**     (directory only)
plugins/runtimes/{pi,hermes,openclaw,omp}/  (empty READMEs only)
topologies/**  roles/**  character-packs/** (move-in from v1 root, content unchanged)
scripts/dev.py  scripts/test.py  scripts/doctor.py  scripts/package.py
pyproject.toml  uv.lock  .python-version
package.json  pnpm-workspace.yaml
.github/workflows/ci.yml
.gitignore  README-v2.md
docs/architecture/ARCHITECTURE.md
docs/architecture/CROSS_PLATFORM.md
```

## Forbidden files

```
hermes-plugin/**   (the frozen v1 form — untouched, still installable)
desktop-plugin/**  (frozen)
tests/test_core.py (frozen; it is v1's regression asset)
docs/DECISIONS.md, docs/adr/v1/**   (historical, frozen)
```

Do **not** delete or move the v1 directories in this package. Vestigial removal happens after WP-004, if at all (CONFLICTS C5).

## Required reading

`PROJECT_BOOK.md` §5 (cross-platform is a kernel requirement), §6/§11 (layout), §64 (API), §65 (local auth), §67 (packaging), §68 (CI matrix), Appendix C (target command surface) · `docs/architecture/V1_INVENTORY.md` §7 D1/D2/D6 · `docs/architecture/V1_CONFLICTS.md` C5/C6 · `docs/adr/ADR-0001-control-plane.md`.

## Architecture constraints

1. **No Bash in the core path.** `scripts/*.py` must run identically from PowerShell and POSIX. Shell wrappers may exist as conveniences only.
2. **Paths via `platformdirs`.** No `~/.hermes`, no `/home/`, no `C:\Users\`. Windows data root and Linux data root both come from `platformdirs.user_data_dir()`.
3. **No `shell=True`, ever.** `exec(["argv"], cwd=…)` only — even for the trivial version probe.
4. **Daemon binds `127.0.0.1` by default, with a per-launch secret** (BOOK §65). The secret is generated locally, never logged, never exposed over HTTP without auth.
5. **Assets live in a data directory resolved by code, never by `__file__` arithmetic.** This is the structural fix for D1/D2: v1's `parents[2]`/`parents[3]` pattern is banned outright.
6. The web app is a **placeholder** here: it must build and show the daemon's health, nothing more.
7. `packages/contracts` is created empty. Defining a single model in this package is a scope violation (WP-003 owns it, with Architect review).

## Acceptance tests

| # | Test | Passes when |
|---|---|---|
| A1 | Daemon boots on a clean clone | `python scripts/dev.py --no-browser` starts and prints the bound port |
| A2 | `GET /health` | returns `200` with JSON `{"status":"ok", ...}` |
| A3 | `GET /version` | returns `200` with the daemon version and the git sha |
| A4 | `WS /events/ws` | a client connects and receives at least one `system.*` frame (e.g. `system.daemon.started`) |
| A5 | Web placeholder renders | `apps/web` builds; the page shows daemon health sourced from the API |
| A6 | No Bash needed | A1–A5 pass with no `.sh` execution on **Windows** and on **Linux** |
| A7 | CI matrix green | the `ci` workflow runs **both** `ubuntu-latest` and `windows-latest`; both green |
| A8 | Single command surface | `python scripts/dev.py`, `scripts/test.py`, `scripts/doctor.py`, `scripts/package.py` all exist and are documented |

## Expected output

`apps/daemon` (FastAPI app, `/health`, `/version`, `/events/ws`), `apps/web` (placeholder Vite/React/TS app), `scripts/*.py` (4 entry points), a workspace manifest, and a two-OS CI workflow — all runnable from a fresh clone on both platforms.

## Expected events

`system.*` — the first canonical events:

```
system.daemon.started    { pid, host, port, version, git_sha }
system.daemon.stopping   { pid, reason }
system.health.probe      { ok, latency_ms }
```

Names are frozen by WP-003; this package may emit them under a documented prefix and must not invent kinds outside `system.*`.

## Windows requirements

- `python scripts/dev.py` works from **PowerShell** without Git Bash or WSL.
- The venv lives at `.venv\Scripts\python.exe`; the scripts must find their own interpreter and not assume `python3`.
- Daemon logs use `\r\n`-safe handling; no ANSI-only output (BOOK §82: display failures explicitly, and console capabilities vary).
- The WebSocket health check must survive a Windows firewall prompt on first bind to `127.0.0.1` (do not bind `0.0.0.0`).

## Linux requirements

- Same commands from `bash`/`zsh`, with `.venv/bin/python`.
- No dependency on a running X server, D-Bus, or Keyring for the skeleton (those arrive with the secrets broker).
- Must work on Python 3.12+ (BOOK §4) — including the 3.13/3.14 interpreters already present on this host.

## Exact acceptance commands

POSIX:

```bash
git checkout v2/control-plane
python3 -m venv .venv && . .venv/bin/activate
uv sync
python scripts/doctor.py                       # expect: all checks PASS
python scripts/test.py                         # expect: exit 0
python scripts/dev.py --no-browser &           # prints: bound 127.0.0.1:<port>
curl -s http://127.0.0.1:<port>/health         # expect {"status":"ok",...}
curl -s http://127.0.0.1:<port>/version
python - <<'PY'                                # WS smoke test
import asyncio, json, websockets
async def m():
    async with websockets.connect("ws://127.0.0.1:<port>/events/ws") as ws:
        print(json.loads(await ws.recv())["kind"])
asyncio.run(m())
PY
cd apps/web && pnpm install && pnpm build && cd ../..
```

Windows (PowerShell):

```powershell
git checkout v2/control-plane
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
uv sync
python scripts\doctor.py
python scripts\test.py
Start-Process -NoNewWindow python -ArgumentList "scripts\dev.py","--no-browser"
Invoke-RestMethod http://127.0.0.1:<port>/health     # expect status: ok
Invoke-RestMethod http://127.0.0.1:<port>/version
cd apps\web
pnpm install
pnpm build
```

CI gate (both platforms):

```bash
gh run list -R nerdylog-code/meta-harness --workflow ci --limit 3
# expect: two runs per push (ubuntu, windows), both success
```

## Known risks

| Risk | Mitigation |
|---|---|
| Windows CI is expensive to get green the first time (path separators, venv layout, CRLF) | Treat A7 as a first-class deliverable, not a follow-up; the Book forbids merging with Windows red |
| `platformdirs` silently onboards a wrong root (e.g. `AppData\Roaming` vs `LocalAppData`) and later breaks migrations | `scripts/doctor.py` must print the resolved data root on both OSes, and CROSS_PLATFORM.md must state the chosen convention |
| The placeholder web app becomes the real UI by accident | `apps/web` here contains one health view; WP-006 owns the app shell |
| A builder imports v1's `store.py` "just to get something working" | Forbidden: it would import the JSONL-canonical contract that C1 supersedes |
| Adding `packages/contracts` content before Architect review | Explicitly out of scope; deviation ⇒ stop |

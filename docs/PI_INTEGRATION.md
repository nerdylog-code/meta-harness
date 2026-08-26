# Pi integration

Pi Coding Agent (`pi`) is an optional specialist engine for Meta-Harness. It
is **not** required for the MVP. The harness keeps working with only the
Hermes native engine if Pi is missing.

## Detection

`PiEngine.available()` is true iff `pi` resolves on `PATH`. The resolver
checks:

1. `$PI_EXECUTABLE` env var (operator override)
2. `shutil.which("pi")`
3. Common npm-global defaults (`%APPDATA%\npm\pi.cmd` on Windows,
   `/usr/local/bin/pi`, `/opt/homebrew/bin/pi`)

If Pi is missing, the engine reports `AVAILABLE=false` on `/status` and the
desktop UI shows it as `pi/off`. Topology execution falls back to the
Hermes native engine when the user requested `pi`.

## RPC mode

We spawn Pi as a child process:

```
pi --mode rpc --no-extensions --no-skills [--model <id>]
```

The flags:

- `--mode rpc` — JSON-RPC over stdio (the recommended mode for headless
  workers; preferred over scraping ANSI output).
- `--no-extensions` — Pi extensions can register hooks/tools that would
  otherwise reach into the harness. Disabled by default.
- `--no-skills` — Pi skills are similarly a recursive-load risk; disabled
  by default.

The harness deliberately does **not** mount any Pi skills, MCP servers, or
extensions. Only `--model` is forwarded.

## Event normalization

Pi's RPC schema is loose. We translate every event we recognize into the
Meta-Harness normalized schema:

| Pi shape | Meta-Harness event |
|---|---|
| `assistant` / `agent` | `agent.thinking` |
| `user` | `message.sent` |
| `tool_use` / `tool_call` / `tool.start` | `tool.started` |
| `tool_result` / `tool.end` | `tool.completed` |
| `done` / `complete` / `end` | `worker.completed` |
| `error` / `fail` | `worker.failed` |
| anything else | `pi.<kind>` (passthrough) |

Unknown events never crash the stream.

## Clean-room workers

Pi workers inherit nothing from the user's parent Pi session. The flags
above (`--no-extensions --no-skills`) keep the worker deterministic. A
future improvement may add `--model <id>` from the engine hint and a
constrained `cwd` from `spec.cwd`.

## Trust posture

Pi workers are spawned with the host's credentials (Pi runs as the user).
The harness does not pass any provider key to Pi — Pi reads its own
config. The Meta-Harness renderer never sees raw keys regardless.

## Failure modes the user sees

| Situation | UI shows |
|---|---|
| Pi not installed | `pi/off` in status chip; `harness_run(engine=pi)` returns `{error: "engine pi unavailable"}` |
| Pi exists, RPC spawn fails | single synthetic `worker.failed` event with the OS error; topology marks the run failed |
| Pi exists, model not allowed | `worker.failed` with Pi's error message; the harness does not retry indefinitely |
| Pi hangs | heartbeat every 15s; cancel works via SIGTERM; the run can be cancelled |

## Future: Pi extension

A future Pi package/extension would let Meta-Harness launch from inside Pi
itself. That work is out of MVP scope. The current design does not block
it: the Meta-Harness event schema is engine-agnostic.
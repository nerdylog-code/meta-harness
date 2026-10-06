# Pi RPC — the observed protocol

**Source of truth:** the installed Pi itself. Version `0.99.2` at
`~/.local/share/mise/installs/pi/latest/pi/`, documented in its own
`docs/rpc.md`, `docs/rpc-commands.md`, `docs/json.md` and `docs/cli-integration.md`.

**Why this file exists:** ADR-0014 says structured protocols only, never ANSI scraping, and
BOOK §13 picks `pi --mode rpc` as the first adapter. An adapter written against a guess about
the wire format is a liability; this file records what the binary actually does, verified by
running it on this machine, so the adapter can be written against evidence.

**Verified by:** `docs/rpc.md` (protocol), `docs/rpc-commands.md` (command vocabulary), plus a
live probe captured at `pi --mode json -p --no-tools` (one real turn, provider-reported usage)
and a live probe of `--mode rpc` over stdin. Both are recorded below.

---

## 1. Two transports, and which one we use

| Mode | Shape | Use |
|---|---|---|
| `--mode json --print` | one-shot; a complete JSONL event stream for a single run, then exit | useful for smoke tests and for observing the event vocabulary |
| `--mode rpc` | long-lived subprocess; JSONL commands on stdin, responses + events on stdout | **what the adapter uses** (BOOK §13) |

Observed: `pi --mode rpc --print ...` produces **no output** — RPC is a protocol, not an
output format. A one-shot prompt in RPC mode has to be sent as a `prompt` command. This is
worth stating because it is the first thing a naive integration gets wrong.

## 2. Framing rules (from `docs/rpc.md`, and they matter)

- Strict **JSONL**: one complete JSON object per record, terminated by `LF`.
- Read stdout as a **byte/UTF-8 stream** and split only on `LF`; strip an optional preceding
  `CR` to accept CRLF.
- **Never use a line reader that splits on `U+2028`/`U+2029`** — they are valid inside JSON
  strings, and Node's `readline` treats them as record boundaries. This is the reason the
  transport is hand-written rather than `asyncio.StreamReader.readline()`.
- stdout carries protocol records only; diagnostics go to **stderr**.
- Backpressure is honoured in both directions: keep reading stdout, and respect stdin
  backpressure when writing. A client that stops reading can stall the process.

## 3. Records

Four families:

| Direction | Record | Meaning |
|---|---|---|
| stdin | command | `{"id": "...", "type": "<command>", ...}` |
| stdout | response | `{"id": "...", "type": "response", "command": "...", "success": true|false, "data": {...}}` |
| stdout | session event | run, message, tool, queue, compaction, retry activity — no command id (except `bash_execution_update`) |
| both | extension UI | interactive extension requests (not used by us yet) |

Correlation is by `id` and **must not assume ordering**: command handling is asynchronous.

## 4. Command vocabulary (from `docs/rpc-commands.md`)

Prompting: `prompt`, `steer`, `follow_up`, `abort`, `clear_queue`, `new_session`.
State: `get_state`, `get_messages`.
Model: `set_model`, `cycle_model`, `get_available_models`.
Thinking: `set_thinking_level`, `cycle_thinking_level`, `get_available_thinking_levels`.
Queue modes: `set_steering_mode`, `set_follow_up_mode`.
Compaction: `compact`, `set_auto_compaction`.
Retry: `set_auto_retry`, `abort_retry`.
Bash: `bash`, `abort_bash`.
Session: `get_session_stats`, `export_html`, `switch_session`, `fork`, `clone`,
`get_fork_messages`, `get_entries`, `get_tree`, `get_last_assistant_text`, `set_session_name`.
Discovery: `get_commands`.

### The lifecycle rule that shapes the adapter

A successful `prompt` response means **accepted/queued**, not finished:

```json
{"id":"req-2","type":"prompt","message":"Review this repository"}
{"id":"req-2","type":"response","command":"prompt","success":true,"data":{"disposition":"started"}}
```

`agent_end` ends one low-level run; retries, overflow recovery, compaction, steering or
follow-up work can still follow. **`agent_settled` is the signal that Pi will not continue
automatically.** Subscribe *before* sending a prompt, or a fast completion is missed.

## 5. Event vocabulary observed live

From `pi --mode json -p --no-tools --no-session "Reply with the single word: pong"`:

```
session, agent_start, turn_start, message_start, message_end, message_update,
thinking_start, thinking_delta, thinking_end, text_start, text_delta, text_end,
turn_end, agent_end, agent_settled
```

Tool events (`tool_start`/`tool_end` or equivalent) were absent because the probe ran with
`--no-tools`; they are part of the same event stream per `docs/json.md` and are exercised by
the adapter's conformance suite rather than assumed.

## 6. Usage and cost come from Pi, per message

The real turn's `turn_end` carried:

```json
"provider":"opencode-go","model":"kimi-k3","api":"openai-completions",
"usage":{"input":117,"output":30,"cacheRead":0,"cacheWrite":0,"reasoning":15,"totalTokens":147,
         "cost":{"input":0.000351,"output":0.00045,"cacheRead":0,"cacheWrite":0,"total":0.000801}}
```

This maps onto `UsageSample` (BOOK §17) almost field for field, and it is **provider-reported**
in our vocabulary — not `estimated`, not `measured`. The adapter must label it as such and must
never blend it with our own estimates. `get_session_stats` provides session-level totals
(`tokens.input/output/total`) for the same purpose at a coarser grain.

## 7. Mapping to `RuntimeAdapter` v2 (ADR-0006)

| Adapter method | Pi RPC |
|---|---|
| `probe()` | spawn `pi --mode rpc` and `get_state` |
| `capabilities()` | derived from `get_commands` + what the probe answered |
| `models()` | `get_available_models` |
| `create_session(spec)` | spawn with `--provider/--model/--session-dir/--name`; `new_session` to reset |
| `send(message)` | `prompt` (then consume events; never treat the response as completion) |
| `steer(instruction)` | `steer` |
| `follow_up(message)` | `follow_up` |
| `events()` | the stdout record stream, minus responses |
| `interrupt()` | `abort` |
| `cancel()` | `abort` + `clear_queue`, then `kill_tree` if the process does not settle |
| `usage()` | `get_session_stats`, plus per-message `usage`/`cost` from the stream |
| `artifacts()` | `get_last_assistant_text`, `export_html`, and tool outputs as artifact candidates |
| `close()` | graceful stdin close, then `ProcessSupervisor.kill_tree` (never a bare kill) |

Capabilities Pi genuinely provides: `session.streaming`, `session.steer`,
`session.follow_up`, `session.resume`, `tool.events`, `usage.tokens`, `usage.cost`,
`context.compaction`, `model.switch`, `approval.native` (extension UI, not yet used).
Anything the probe cannot confirm is reported as `UnsupportedCapability`, never faked.

## 8. What the adapter must not do

- **No ANSI scraping** (ADR-0014). `--mode text` is for humans; the adapter never reads it.
- **No completion-by-response.** The Book forbids "the model said it finished" as proof, and
  Pi's own docs say the same thing about `prompt`.
- **No session guessing.** `--no-session` for ephemeral probes; a real session uses an explicit
  `--session-dir` under our data root so a run can be resumed and audited.
- **No killing without verification.** Cancellation goes through `ProcessSupervisor`, whose
  `orphan_check` is the proof (BOOK §79).


## Tool calls

Captured from the real binary (`pi --mode rpc --tools read`, a prompt that reads a file). The
tool lifecycle in RPC mode is **not** spelled the same way as in `--mode json`:

| `--mode json`   | `--mode rpc`             | canonical event  |
| --------------- | ------------------------ | ---------------- |
| `tool_start`    | `tool_execution_start`   | `tool.started`   |
| `tool_end`      | `tool_execution_end`     | `tool.completed` |
| `tool_call`     | —                        | `tool.started`   |
| `tool_result`   | —                        | `tool.completed` |

The parser accepts both spellings; the fake peer used by the conformance suite emits the RPC
ones, so the suite exercises the shape the binary actually produces.

Shapes, verbatim from the capture:

```json
{"type":"tool_execution_start","toolCallId":"read_0","toolName":"read",
 "args":{"path":"pyproject.toml","offset":1,"limit":50}}

{"type":"tool_execution_end","toolCallId":"read_0","toolName":"read",
 "result":{"content":[{"type":"text","text":"[project]\nname = \"metaharness\"..."}]}}
```

Notes that cost a real run to learn:

* **The built-in tools are named `read`, `bash`, `edit`, `write`** (`pi --help`: "read, bash,
  edit, write tools"). Passing `--tools read_file` is accepted and yields a session with *no*
  tools at all -- the model then answers "my available tools list is empty". A wrong tool name
  fails silently, which is why the tool list is a constant with the help text quoted beside it.
* `tool_execution_end` carries **no duration**; `durationMs` exists only in `--mode json`. The
  parser records `duration_ms: null` rather than timing the tool itself.
* Before the execution events, the same call also appears as
  `message_update.assistantMessageEvent.type == "toolcall_start" | "toolcall_delta" |
  "toolcall_end"`. Those are the streamed *intent*; the execution events are the fact, so the
  canonical events come from the latter only -- otherwise every call would be counted twice.
* `get_state` answers `model` as an **object** (`{"id": "kimi-k3", "provider": "opencode-go",
  ...}`), not a string. The adapter normalises it to the id before it reaches a projection; the
  first real run failed the `sessions` projection with `type 'dict' is not supported` until it did.

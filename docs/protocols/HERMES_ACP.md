# Hermes ACP — observed protocol

**Status:** observed from the installed binary. Every shape below was captured, not inferred.
**Source:** `hermes acp` — `agentInfo` `{"name": "hermes-agent", "version": "0.21.5"}`.
**Captures:** `~/.hermes/cache/scratch/mh-recon/hermes-acp-probe.ndjson` (a plain turn) and
`hermes-acp-tools.ndjson` (a turn that calls a tool). Reproduce with
`uv run python tools/probe_hermes_acp.py --prompt "..."`.

**Transport:** JSON-RPC 2.0 over the child's stdin/stdout, one JSON object per line, exactly like
Pi's RPC mode. No ANSI, no framing beyond newlines. Every request carries `id`; the agent also
sends notifications (no `id`) with `method: "session/update"`.

The rule this file exists to enforce: **an unobserved capability is not a capability.** Where the
probe did not establish something, this document says so instead of guessing.

---

## 1. `initialize`

Request:

```json
{"jsonrpc":"2.0","id":1,"method":"initialize",
 "params":{"protocolVersion":1,
           "clientCapabilities":{"fs":{"readTextFile":true,"writeTextFile":true}}}}
```

Response (`result`):

```json
{"protocolVersion": 1,
 "agentInfo": {"name": "hermes-agent", "version": "0.21.5"},
 "agentCapabilities": {"loadSession": true,
                       "promptCapabilities": {"image": true},
                       "sessionCapabilities": {"fork": {}, "list": {}, "resume": {}}},
 "authMethods": [
   {"id": "opencode-go", "name": "opencode-go runtime credentials",
    "description": "Authenticate Hermes using the currently configured opencode-go runtime credentials."},
   {"id": "--setup", "name": "...", "args": ["--setup"],
    "description": "Open Hermes' interactive model/provider setup in a terminal..."}]}
```

What this establishes, and what it does not:

| Claim | Evidence |
| --- | --- |
| ACP protocol version 1 | `protocolVersion: 1` in the response |
| streaming prompts | `agent_message_chunk` notifications observed |
| tool calls are streamed | `tool_call` + `tool_call_update` observed |
| session resume exists | `sessionCapabilities.resume` is present in the handshake |
| **not** verified | that `session/fork` and `session/list` work as advertised; the handshake advertises them, no call was made |

The capability set the adapter reports is built from this handshake **plus** what a call actually
did. An advertised capability that was never exercised is reported as advertised-but-unverified,
never as supported.

## 2. `session/new`

Request `{"cwd": "<dir>", "mcpServers": []}` → response:

```json
{"sessionId": "bcf4d123-4b7f-4bcf-b957-681942744151",
 "models": {"availableModels": [{"modelId": "openai-codex:gpt-6.1-sol",
                                 "name": "ChatGPT or Codex Subscription · gpt-6.1-sol",
                                 "description": "Provider: ChatGPT or Codex Subscription"}]},
 "modes": {"availableModes": [{"id": "default", "name": "Default", "description": "Ask before edits."},
                              {"id": "accept_edits", "name": "Accept Edits", ...},
                              {"id": "dont_ask", "name": "Don't Ask", ...}],
           "currentModeId": "default"}}
```

`mcpServers` is **required** by the schema — omitting it is a `-32602` error, not a defaulted
field. The model list comes from the agent, so "which model" is a runtime answer, not a constant.

## 3. `session/prompt`

Request `{"sessionId": "...", "prompt": [{"type": "text", "text": "..."}]}` → response:

```json
{"stopReason": "end_turn",
 "usage": {"inputTokens": 68401, "outputTokens": 360, "cachedReadTokens": 57600,
           "thoughtTokens": 94, "totalTokens": 68761}}
```

The response arrives **once, at the end**. Everything in between is `session/update`
notifications, so a client that renders only the response renders nothing until the turn is over.
`usage` here is provider-reported and maps onto `UsageSample` with `provenance: provider_reported`.

## 4. `session/update` notifications

`params: {sessionId, update: {sessionUpdate: "<type>", ...}}`. Types observed in a real turn:

| `sessionUpdate` | Count in the capture | Shape observed |
| --- | --- | --- |
| `agent_message_chunk` | 35 | assistant text, streamed |
| `agent_thought_chunk` | 30 | reasoning text, streamed separately from the answer |
| `tool_call` | 2 | `{kind: "read", title: "read_file: pyproject.toml", toolCallId: "tc-…", locations: [{"path": "…"}]}` |
| `tool_call_update` | 2 | `{toolCallId, kind, status: "failed", content: [{type: "content", content: {type: "text", text: "Read failed: …"}}]}` |
| `usage_update` | 2 | running usage |
| `session_info_update` | 2 | title + `_meta.hermes.sessionProvenance` |
| `available_commands_update` | 1 | the agent's slash commands (`help`, `model`, `tools`, `context`, `reset`, …) |

Notes that matter for an adapter:

* **Tool lifecycle is two events, not four.** `tool_call` opens the call (id, kind, title,
  locations); `tool_call_update` carries `status` and the result content. `status` was observed as
  `"failed"`; other statuses are **not** verified here, so the parser treats any terminal status it
  does not recognise as *terminal-but-unclassified* rather than inventing a mapping.
* **`_meta.hermes.sessionProvenance`** carries `acpSessionId`, `currentHermesSessionId`,
  `rootHermesSessionId`, `parentHermesSessionId`, `sessionKind` and `compressionDepth`. This is
  Hermes' own account of its session lineage, including its internal compaction depth — directly
  relevant to M2: a migration that lands on Hermes can be checked against this instead of trusting
  the control plane's own bookkeeping.
* **`session/cancel` answered** in the probe. That is the cancellation path the Architect requires
  every execution to support; it still ends with a kill-and-verify, not with a hopeful notification.

## 5. What was not established

Recorded so the next person does not have to guess, and so nothing here is over-claimed:

* `session/load` returned `-32602 Invalid params` for `{sessionId, cwd}` because `mcpServers` is
  required. Whether loading a **real** session id then resumes it was not tested.
* `session/fork`, `session/list` — advertised, never called.
* Image prompts — `promptCapabilities.image` is advertised; no image was sent.
* Permission prompts — Hermes advertises modes (`default`, `accept_edits`, `dont_ask`) rather than
  a per-tool approval request. **No approval request was observed**, so
  `approval.native` stays unsupported in the adapter's capability set, with this note as the reason.
* Token accounting for cached reads: `cachedReadTokens` is present and non-zero; whether it is
  already included in `inputTokens` is not established, so the adapter stores both as separate
  metrics rather than summing them.

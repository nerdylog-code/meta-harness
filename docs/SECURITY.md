# Security

No security theater. Honest limits.

## Threat model

The harness adds a second execution surface to an already-trusted Hermes
plugin slot. The trust boundary is the Hermes plugin loader, not the
harness. A loader bug that lets an attacker run arbitrary code in the host
process is also a loader bug, regardless of the harness.

What the harness **does** add:

- a place to put credentials-in-tool-arguments that a model might leak
- a place to put remote-URL sprite references that a model might plant
- a place to put model-authored code that could be auto-loaded by mistake

Each of these has explicit handling.

## Credentials

The renderer never receives provider keys. Tool arguments are redacted
before they are appended to `events.jsonl` and the SQLite event index. The
redaction rules are conservative:

- `sk-…`, `sk-ant-…`, `ghp_…`, `xai-…` keys
- `Bearer …` tokens
- `api_key/token/secret/password = …` assignments
- JWTs (three base64url segments separated by `.`)

Long strings (≥ 8 KiB) are truncated **before** regex runs — defense in
depth against the silent perf cliff.

Redaction runs at the event boundary, not the renderer boundary. The
renderer never has the original to begin with.

## Remote assets

Character pack assets must live inside the pack directory. The asset route
rejects:

- `..` (path traversal)
- absolute paths
- names containing `/` or `\`

There is no remote URL fallback in MVP. A future feature may add
operator-curated CDNs with explicit allowlist + signed URLs.

## Model-authored code

The Plugin Lab stores model-authored Python in
`~/.hermes/meta-harness/generated/<id>/v<N>/plugin.py`. The lab **does not
auto-load** any of it into the host plugin namespace. `activate_experimental`
only updates a pointer file. Promotion (out of MVP scope) would route
through:

1. an explicit operator review
2. capability grants per `plugin_capabilities.CAPABILITY_REGISTRY`
3. a host-side loader invocation that the harness cannot perform alone

No version is destroyed. `rollback` rewinds the pointer.

## Approval flow

Destructive or sensitive actions are gated:

| Action | Gate |
|---|---|
| `activate_experimental` | The current validation result must be `ok`. |
| `rollback` | No gate beyond operator intent. |
| Install (config.yaml patch) | Operator runs the installer. The patch is contained and reversible. |
| Uninstall | Backup of `config.yaml` is left in `backups/`. |

Hermes's own approval flow for tool calls is unchanged. The harness does
not invent a parallel approval system.

## Desktop plugin authority

Hermes Desktop disk plugins run with **full renderer authority**. This is
documented in `apps/desktop/src/contrib/runtime-loader.ts`. A broken plugin
cannot crash the app, but it can do anything the app can — including
issuing gateway RPCs and reading `host.request`. We honour this:

- We never auto-load model-generated UI code.
- The desktop plugin is signed by disk location, not by cryptographic
  integrity in MVP. An `integrity: sha256-…` field is reserved in the
  runtime loader for later.
- The plugin backend never sends raw provider keys across the wire.

## Network egress

The harness backend does **not** make outbound network calls except:

- spawning child processes (Pi engine)
- the optional WebSocket

Both are bounded by the same Hermes host egress policy that already
governs the rest of Hermes.

## Filesystem

The harness reads/writes only inside `~/.hermes/meta-harness/` (and the
character-pack / topology / artifact directories derived from it). It
never edits anything else in `~/.hermes/`. The installer is the only
exception, and it explicitly backs up `config.yaml` before patching.

## Sandbox

We do not promise perfect sandboxing. The host's existing sandbox/permission
model applies. The harness never claims to isolate the agent from the
host; it claims only to keep its own runtime boundaries small and auditable.

## Known limitations

- No cryptographic plugin signing.
- No remote asset CDN.
- No multi-user isolation in the dashboard.
- Pi engine inherits the user's Pi session credentials (by design).
- Tool argument redaction is conservative; exotic secret formats may
  slip through. Add a pattern in `redaction.py` and ship a test.

## Incident response

If you suspect a leaked credential:

1. `~/.hermes/meta-harness/events.jsonl` — search for the pattern. The
   redaction should already have replaced it; if not, the leak is real.
2. Rotate the credential. Do not rely on redaction alone.
3. Open an issue with: the leaked pattern, the timeframe, and the run id.
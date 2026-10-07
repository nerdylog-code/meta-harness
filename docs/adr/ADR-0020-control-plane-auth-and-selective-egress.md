# ADR-0020 — Control Plane Authentication and Selective Egress

**Status:** accepted · **Date:** 2026-10-07 · **Milestone:** S2

## Context

S1 closed the filesystem boundary and, in doing so, produced the finding that matters more than the
feature: with an open network, the sandboxed agent walked out **through the control plane**. It
scanned loopback, found the daemon's own `/v1/...` API, read another session's transcript, and found
the canary token inside it. The filesystem held; the network was the hole; and the record said
`isolation: weak` for exactly that reason.

The daemon's current defence is `LoopbackOnlyMiddleware`, which refuses non-loopback peers. That is
not authentication. Any process on the machine -- a runtime, a tool the runtime spawned, a browser
page, a compromised dependency -- is a loopback peer. Being able to reach `127.0.0.1` must not grant
control-plane authority.

Two different threats are involved, and conflating them is how a product claims protection it does
not have:

* **Who is allowed to control Meta-Harness?** A process that reaches the port can currently read
  missions, tasks, transcripts and events, and can mutate the work graph.
* **Where can a sandboxed runtime connect?** A runtime granted provider access currently also has
  arbitrary host and internet access, including the control plane itself.

Neither answer replaces the other. Authentication does not stop a runtime from reaching an arbitrary
public host; egress control does not stop a loopback process from calling the API.

## Decision

S2 is **one security gate with two mandatory sub-phases**, built in this order.

### S2A — Control Plane Authentication and Actor Identity

**A request-scoped `ActorContext`.** Every authenticated request carries:

```
ActorContext {
    kind:           local_operator | system
    principal:      a stable descriptive principal
    authentication: local_session | internal
    display_name:   human-readable only, never authority
}
```

No new public id prefix is introduced: no `usr_`, no `act_`, no `human_`. The actor is not a domain
entity and does not get a domain id.

**Authority is not a name.** `approved_by: "Daniel"` may remain useful display metadata, but the
security decision reads `actor.kind == local_operator`. After S2 a caller cannot become "human" by
submitting a string. R3/R4 grants require authenticated local-operator authority, so a runtime cannot
approve its own action over HTTP.

**Opaque, per-launch, in-memory tokens.** Cryptographically random, generated per daemon launch,
invalid after restart, verified in constant time, never persisted. A token is never canonical state,
never an artifact, never in SQLite, never in an event payload or provenance, never logged, never
passed to a `RuntimeAdapter` or a `SandboxProvider`, never copied into a workspace, and never
inherited by a runtime child process. **The runtime must never see the operator credential.** JWT is
not introduced merely because authentication now exists.

**Bootstrap without an unauthenticated credential endpoint.** There is no `GET /v1/auth/token` and no
endpoint a sandbox could call to receive authority. The desktop host already owns the daemon process,
so it uses that ownership: it generates a high-entropy single-use bootstrap capability, hands it to
the daemon over a private parent→child channel (the child's stdin), navigates the WebView through one
bootstrap request, and the daemon consumes the capability exactly once, sets an opaque `HttpOnly`
session cookie, and redirects. The capability then dies. It must never travel through argv, an
inherited environment, logs, events, `PROJECT_STATE`, `localStorage` or `sessionStorage`, and the
host's own status output redacts it.

**Cookies and browser session.** An opaque session cookie, `HttpOnly`, `SameSite=Strict`, `Path=/`,
with `Secure` wherever the transport supports it. Plain-HTTP loopback development honestly omits
`Secure` because browsers will not send a `Secure` cookie over HTTP; that distinction is documented
rather than papered over. The cookie carries no agent id, no permission, no human name and no runtime
id.

**CSRF, Origin and Host.** Cookie authentication alone is not enough for a state-changing request. A
mutation requires an authenticated session, a trusted `Origin`, and a per-session CSRF token sent in a
header. The CSRF token is renderer-visible and is **not** the authentication credential. `GET`/`HEAD`
stay authenticated but non-mutating. There is no permissive CORS. The `Host` header is validated
against the configured loopback origin, because loopback binding alone does not stop a
DNS-rebinding-style attack: a request arriving at `127.0.0.1` with `Host: attacker.example` must not
become trusted.

**Protected and public surface.** `/v1/*`, `/v1/events/ws` and `/events/ws` are protected **including
reads**: HTTP answers `401`, and a websocket is closed before any backlog is sent -- no event history
may be emitted before authentication. The static application bundle stays public. `/health` and
`/version` stay public but become **minimal**: `status`, `service`, `version`, `auth_required`. They
must not expose the data root, the store path, the web bundle path, event counts, subscriber counts,
mission ids, task ids, agent ids, session ids or transcripts. Detailed diagnostics move to an
authenticated `GET /v1/system/health`, and the desktop host's detection updates accordingly.

**Provenance, not credentials.** The canonical envelope is unchanged: an authenticated mutation
records safe actor provenance in the existing flexible `provenance` object --
`{kind: "local_operator", authentication: "local_session"}` -- and internal daemon events record
`{kind: "system", authentication: "internal"}`. No token, cookie, CSRF value or credential hash ever
appears there.

### S2B — Selective Network Egress

**An egress broker, not an environment variable.** A local `EgressBroker` sits between the sandbox and
the approved provider endpoint and is the **only connected path** for a sandbox claiming strong
allowlist enforcement. Setting `HTTPS_PROXY` proves nothing: if the runtime can ignore the proxy and
open a raw socket, network isolation is `weak`, and the direct-socket probe is the authority.

**Levels are measured, never asserted.** `deny` may be `strong` when egress is genuinely unavailable.
`unrestricted` is `weak`. `allowlist` may be `strong` only when **all** outbound connectivity is forced
through the broker. Requested `strong` with an unusable provider does not silently degrade: proceeding
weak or moderate requires an explicit `allow_degraded=true`, and the record keeps `requested`, `actual`,
`reason` and `evidence` separate. Unknown is not weak; weak is not moderate; moderate is not strong.

**HTTPS/WSS over `CONNECT` on 443 only**, for V1. That covers normal model-provider APIs and is far
safer than pretending to support arbitrary protocols. No TLS interception, no fake CA, tunnel
semantics preferred.

**Allowlist by hostname, with boundary matching.** Exact host first; an optional `*.example.com`
wildcard must use suffix-boundary matching so `evil-example.com` never matches. `*` and `*.com` are
not strong selective egress.

**Private and special addresses are refused unconditionally**: `127.0.0.0/8`, `::1`, RFC1918, IPv4
link-local, multicast, unspecified, reserved/special-purpose ranges, `169.254.169.254`, and private or
link-local IPv6. A provider allowlist must never accidentally authorize metadata or localhost.

**DNS rebinding defence.** The broker resolves the approved hostname itself, inspects **all** returned
addresses, rejects forbidden ones, and connects to a validated resolved address. The sandbox never
chooses an arbitrary IP while claiming an approved hostname, and a stale classification is not trusted
forever.

**CONNECT is bound to TLS SNI.** An allowed `CONNECT host:443` must not become a tunnel to an
arbitrary co-hosted TLS service: the ClientHello SNI must correspond to the allowed host. If SNI is
absent or mismatched, the broker refuses -- and if binding cannot be done safely, enforcement is
reported **moderate, never strong**.

**Observability without content.** The broker may record hostname, port, decision, resolved address
class, start/end, bytes and duration. It must never record authorization headers, API keys, request or
response bodies, TLS plaintext, cookies or query strings. Provider credentials stay end-to-end
encrypted inside TLS.

**The S1 filesystem/process boundary is not weakened**, and host loopback is not re-exposed to make a
proxy convenient. If reaching the broker required exposing all of host loopback, that is an
architecture blocker, not a design choice.

### Windows

Actor authentication is cross-platform and must be green on Windows and Linux. Selective egress
enforcement may differ: if Windows cannot provide the same strong network boundary, it reports
`weak`/`unsupported` rather than faking parity, exactly as S1 already does for strong sandboxing. The
contracts, the policy engine and the tests stay green on Windows.

## Consequences

* Authentication must hold **even when the network is weak**: a runtime with unrestricted network may
  reach the daemon's TCP port, and must still receive `401` with no event history. S2A is proven
  independently of S2B.
* The original S1 escape becomes a regression test: same network reachability, no credentials, zero
  mission/task/transcript/event data returned. A deterministic HTTP probe is the authority; an LLM
  exploration is supplemental.
* A unique auth canary is created during the test and then searched for in the runtime environment,
  the sandbox environment, the workspace, artifacts, canonical events, the JSONL export, the daemon's
  normal logs, runtime logs and process argv. It must not appear.
* Automatic attach to an already-running daemon is **refused with a clear message** when it cannot be
  safely paired. Security outranks attach convenience: there is no fallback to an unauthenticated API
  and no reusable control token read from a world-visible location. A secure pairing mechanism may be
  added later.
* **S2 closes only when both halves are true.** If authentication is complete and provider-compatible
  strong egress cannot be reached, the report says so -- `S2A COMPLETE — S2B ARCHITECTURE BLOCKER` is
  an acceptable and preferable outcome to a fake green.
* Nothing from the next layer is built here: no `ComputerProvider`, no screen streaming, no Live
  Workspace, no Take Control or Hand Back, no voice, no channels, no routines, no watches, no
  OpenClaw/OMP adapters, no Fusion, Swarm or Factory. S2 exists so that those can be built safely
  next.

"""Control-plane authentication: who is allowed to control Meta-Harness.

Loopback is not authentication. Any process on the machine -- a runtime, a tool it spawned, a browser
page, a compromised dependency -- is a loopback peer, and S1 proved what that costs: a sandboxed agent
reached the daemon's own API and read another session's transcript. This module makes reaching the
port insufficient.

What lives here:

* ``ActorContext`` -- request-scoped authority. Authority is a **kind**, never a name: a caller cannot
  become human by submitting a string, so an approval decision reads ``actor.kind`` and not
  ``approved_by``.
* ``AuthRegistry`` -- per-launch, in-memory credentials. Opaque, high-entropy, single-use bootstrap
  capabilities; opaque session cookies; per-session CSRF tokens. Nothing is persisted, and the
  credential is never canonical state, an artifact, an event field, a log line, a workspace file, an
  argv entry or an inherited environment variable. The runtime must never see the operator's
  credential.
* ``AuthMiddleware`` -- the pure-ASGI gate covering HTTP and websocket alike, because the event stream
  is a websocket and a `BaseHTTPMiddleware` would not see it. It refuses non-loopback peers, validates
  ``Host``, requires a session on every protected path (reads included), and requires a trusted
  ``Origin`` plus a CSRF header on state-changing methods.

The bootstrap capability is delivered over a private channel the operator already owns. A trusted
parent -- the desktop host, or the dev launcher for a manual run -- generates the capability itself and
hands it to the daemon as one bounded record on the child's stdin, through the process supervisor we
already have. The daemon registers it in memory, nothing is written down, and the parent passes the
one-use URL to the shell over a private pipe that is never mirrored to a log.

There is deliberately no file. A ``0600`` file protects against another OS user; it does not protect
against another process running under the same account, and the threat model explicitly includes a
runtime, a tool process and a compromised dependency. S2A has to hold independently of sandbox
strength, so the credential does not exist anywhere a same-user process can look.

The text that follows is the historical description of the file-based channel, kept because the
reasoning that rejected it belongs next to the reasoning that replaced it: the daemon once wrote a
one-use URL into a ``0600`` file inside its own data root, and consumed and deleted it on
first use. That path is deliberately not argv, not an environment variable, not a log line, not
canonical state, and not readable from inside a sandbox -- and it needs no unauthenticated
"give me credentials" endpoint, which is the thing that must never exist.
"""

from __future__ import annotations

import hmac
import json
import secrets
from contextvars import ContextVar
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Collection

#: Cookies are opaque: no agent id, no permission, no human name, no runtime id inside.
COOKIE_NAME = "mh_session"
CSRF_HEADER = "x-metaharness-csrf"

#: A capability is short-lived and single-use; a session lives for the daemon's lifetime at most.
BOOTSTRAP_TTL_S = 120.0
SESSION_TTL_S = 12 * 3600.0

#: A capability below this length is not high entropy, whoever generated it.
MIN_CAPABILITY_CHARS = 32

#: The bootstrap record from the parent is bounded: a malicious or broken parent must not be able to
#: make the daemon buffer an unbounded line.
BOOTSTRAP_RECORD_LIMIT = 4096


def bootstrap_record(line: bytes) -> str | None:
    """Parse exactly one bootstrap record from the trusted parent, or return ``None``.

    Strict and silent: a wrong type, a short capability, an oversized line or invalid JSON all yield
    ``None``, and nothing is echoed, logged, emitted or stored. The caller forgets the buffer.
    """
    if not line or len(line) > BOOTSTRAP_RECORD_LIMIT:
        return None
    try:
        body = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(body, dict) or body.get("type") != "bootstrap":
        return None
    capability = body.get("capability")
    if isinstance(capability, str) and len(capability) >= MIN_CAPABILITY_CHARS:
        return capability
    return None


#: Methods that change state and therefore need Origin + CSRF on top of a session.
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: Paths reachable without a session. The application bundle is public; the API never is.
PUBLIC_PATHS = frozenset({"/health", "/version", "/auth/bootstrap"})
PUBLIC_PREFIXES = ("/assets/",)

#: Everything under this prefix requires a session, reads included.
PROTECTED_PREFIXES = ("/v1/",)

#: Exact paths that are protected even though they sit outside the API prefix. The legacy websocket
#: alias is here for a reason worth remembering: a canonical route was protected and its historical
#: alias was not, so the alias quietly became a side door to the event stream. Protecting one and
#: forgetting the other is exactly the mistake this list exists to prevent.
PROTECTED_PATHS = frozenset({"/events/ws"})


#: The actor for the work happening right now. Set by the middleware for a request and reset when it
#: ends, so an event created anywhere inside that request records who caused it without every call
#: site having to remember to pass it. Outside a request it is the system actor, which is the honest
#: answer for internal daemon work.
_ACTOR: ContextVar["ActorContext | None"] = ContextVar("metaharness_actor", default=None)


def current_actor() -> "ActorContext":
    return _ACTOR.get() or SYSTEM_ACTOR


def set_current_actor(actor: "ActorContext"):
    return _ACTOR.set(actor)


def reset_current_actor(token) -> None:
    _ACTOR.reset(token)


@dataclass(frozen=True)
class ActorContext:
    """Who is asking. ``kind`` is the authority; ``display_name`` is a label, never a decision."""

    kind: str  # local_operator | system
    principal: str
    authentication: str  # local_session | internal
    display_name: str | None = None

    def provenance(self) -> dict[str, str]:
        """Safe provenance for the canonical envelope: a kind and a method, never a credential."""
        return {"kind": self.kind, "authentication": self.authentication}


#: Internal daemon work. It does not call its own HTTP API to gain authority; it uses this directly.
SYSTEM_ACTOR = ActorContext(
    kind="system", principal="daemon", authentication="internal", display_name="Meta-Harness daemon"
)


@dataclass
class BrowserSession:
    session_id: str
    csrf: str
    actor: ActorContext
    created_at: float = field(default_factory=time.time)

    def is_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) - self.created_at > SESSION_TTL_S


class AuthRegistry:
    """Per-launch credentials, in memory only.

    A token here is invalid the moment the daemon restarts, because it never existed anywhere else.
    """

    def __init__(self, *, bootstrap_ttl_s: float = BOOTSTRAP_TTL_S) -> None:
        self.bootstrap_ttl_s = bootstrap_ttl_s
        self._bootstrap: dict[str, float] = {}
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = threading.RLock()
        self.bootstrap_issued = 0
        self.bootstrap_consumed = 0

    # ------------------------------------------------------------------ bootstrap

    def issue_bootstrap(self) -> str:
        """Mint a single-use capability. High entropy, short life, one use."""
        capability = secrets.token_urlsafe(32)
        with self._lock:
            self._bootstrap[capability] = time.time() + self.bootstrap_ttl_s
            self.bootstrap_issued += 1
        return capability

    def register_bootstrap(self, capability: str, *, ttl_s: float | None = None) -> None:
        """Register a capability the trusted parent generated and handed over privately.

        The daemon does not mint it and does not write it down: one per launch, single use, short
        life, and it exists only here.
        """
        if not isinstance(capability, str) or len(capability) < MIN_CAPABILITY_CHARS:
            raise ValueError("a bootstrap capability must be a high-entropy string")
        with self._lock:
            if self._bootstrap:
                raise ValueError("a bootstrap capability is already registered for this launch")
            self._bootstrap[capability] = time.time() + (ttl_s if ttl_s is not None else self.bootstrap_ttl_s)

    def consume_bootstrap(self, capability: str) -> BrowserSession | None:
        """Consume a capability exactly once. An unknown, reused or expired one yields nothing."""
        with self._lock:
            match = None
            for candidate in self._bootstrap:
                # Constant-time comparison against every live capability: a timing signal here would
                # leak which prefix is correct.
                if hmac.compare_digest(candidate, capability):
                    match = candidate
            if match is None:
                return None
            expiry = self._bootstrap.pop(match)
            if time.time() > expiry:
                return None
            self.bootstrap_consumed += 1
            return self._open_session()

    def _open_session(self) -> BrowserSession:
        session = BrowserSession(
            session_id=secrets.token_urlsafe(32),
            csrf=secrets.token_urlsafe(32),
            actor=ActorContext(
                kind="local_operator",
                principal="local-operator",
                authentication="local_session",
                display_name="local operator",
            ),
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    # ------------------------------------------------------------------ sessions

    def resolve(self, cookie_value: str | None) -> BrowserSession | None:
        """Resolve a cookie to a live session, or ``None``. Never raises, never logs the value."""
        if not cookie_value:
            return None
        with self._lock:
            for session_id, session in self._sessions.items():
                if hmac.compare_digest(session_id, cookie_value):
                    if session.is_expired():
                        self._sessions.pop(session_id, None)
                        return None
                    return session
        return None

    def revoke(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def revoke_all(self) -> None:
        with self._lock:
            self._sessions.clear()

    @property
    def session_count(self) -> int:
        with self._lock:
            return len(self._sessions)

    def csrf_ok(self, session: BrowserSession, provided: str | None) -> bool:
        if not provided:
            return False
        return hmac.compare_digest(session.csrf, provided)


# ---------------------------------------------------------------------------------------------- gate


def is_public(path: str) -> bool:
    """Public means: no session required. The bundle is public; the API is not."""
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def is_protected(path: str) -> bool:
    """A path needs a session if it is the API, or one of the exact paths listed above.

    Note what this replaces: a single prefix check meant anything not under `/v1/` and not explicitly
    public fell through to the application -- and the legacy websocket alias did exactly that.
    """
    return path in PROTECTED_PATHS or any(path.startswith(prefix) for prefix in PROTECTED_PREFIXES)


def host_is_allowed(host_header: str | None, allowed_hosts: Collection[str]) -> bool:
    """Validate ``Host`` against the configured loopback origins.

    Loopback binding does not stop a DNS-rebinding style attack: a request can arrive at 127.0.0.1
    carrying `Host: attacker.example`, and a daemon that trusts the header would treat it as local.
    """
    if not host_header:
        return False
    name = host_header.rsplit(":", 1)[0].strip("[]").lower() if ":" in host_header else host_header.lower()
    return name in {entry.lower() for entry in allowed_hosts}


def origin_is_allowed(origin: str | None, allowed_origins: Collection[str]) -> bool:
    """A mutation must come from an origin we trust. Absent Origin is refused, not assumed safe."""
    if not origin:
        return False
    return origin.rstrip("/").lower() in {entry.rstrip("/").lower() for entry in allowed_origins}


def grant_requires_operator(risk_value: str, actor: ActorContext) -> bool:
    """Whether this risk level demands an authenticated operator, and whether the actor is one.

    Returns True when the grant must be refused. A name in the request body is display metadata; the
    decision reads the actor's kind. A runtime cannot approve its own action, and no caller becomes
    human by submitting a string.
    """
    return risk_value in {"R3", "R4"} and actor.kind != "local_operator"


def provenance_for(request: Any, method: str = "measured") -> dict[str, Any]:
    """Safe provenance for a canonical event: how it was decided, and by what kind of actor.

    Never a token, a cookie, a CSRF value or a credential hash -- only a kind and an authentication
    method, which is what makes the log auditable without ever holding the credential.
    """
    return {"method": method, "actor": actor_from_request(request).provenance()}


class AuthMiddleware:
    """The gate. Pure ASGI, so websockets are covered exactly like HTTP.

    Order matters and is deliberate:

    1. a non-loopback peer is refused outright (the old boundary, kept);
    2. ``Host`` must be one of ours, or the request is refused before anything is read;
    3. a protected path requires a session -- reads included -- and answers 401 without touching the
       store, so no event history leaks before authentication;
    4. a state-changing request additionally requires a trusted ``Origin`` and the session's CSRF
       header.

    The websocket is closed before the backlog is sent: authentication happens in this middleware, and
    the application only ever runs after it passes.
    """

    def __init__(
        self,
        app,
        *,
        registry: AuthRegistry,
        allowed_hosts: Collection[str],
        allowed_origins: Collection[str],
        loopback_hosts: Collection[str],
    ) -> None:
        self.app = app
        self.registry = registry
        self.allowed_hosts = frozenset(allowed_hosts)
        self.allowed_origins = frozenset(allowed_origins)
        self.loopback_hosts = frozenset(loopback_hosts)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        peer = client[0] if client else None
        if peer not in self.loopback_hosts:
            await self._refuse(scope, send, 403, "the daemon serves loopback clients only")
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        if not host_is_allowed(headers.get("host"), self.allowed_hosts):
            await self._refuse(scope, send, 403, "untrusted Host header")
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()
        if is_public(path):
            await self.app(scope, receive, send)
            return

        if not is_protected(path):
            # The application shell and its fallback: public, and nothing behind it is data.
            await self.app(scope, receive, send)
            return

        session = self.registry.resolve(self._cookie(headers.get("cookie")))
        if session is None:
            await self._refuse(scope, send, 401, "authentication required", websocket=True)
            return

        if scope["type"] == "websocket":
            # A websocket handshake carries no HTTP method, so `method in UNSAFE_METHODS` was never true
            # for it and the Origin check silently did not apply: an authenticated socket could be opened
            # from any Origin, which is not what this gate claims. The browser Origin is the cross-site
            # boundary for a cookie-authenticated handshake exactly as it is for a mutation.
            #
            # CSRF is deliberately NOT required here. It protects an unsafe method that a browser would
            # send on its own; a websocket is opened by script and Origin is the boundary that matters.
            if not origin_is_allowed(headers.get("origin"), self.allowed_origins):
                await self._refuse(scope, send, 403, "untrusted Origin on an authenticated websocket", websocket=True)
                return
        elif method in UNSAFE_METHODS:
            if not origin_is_allowed(headers.get("origin"), self.allowed_origins):
                await self._refuse(scope, send, 403, "untrusted Origin on a state-changing request")
                return
            if not self.registry.csrf_ok(session, headers.get(CSRF_HEADER)):
                await self._refuse(scope, send, 403, "missing or invalid CSRF token")
                return

        scope = dict(scope)
        scope["state"] = {**scope.get("state", {}), "actor": session.actor, "session": session}
        token = set_current_actor(session.actor)
        try:
            await self.app(scope, receive, send)
        finally:
            # Reset, or the actor would leak into whatever this task handles next.
            reset_current_actor(token)

    @staticmethod
    def _cookie(header: str | None) -> str | None:
        if not header:
            return None
        for part in header.split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE_NAME:
                return value
        return None

    async def _refuse(self, scope, send, status: int, detail: str, *, websocket: bool = False) -> None:
        if scope["type"] == "websocket":
            # Close before the application runs, so no backlog can be emitted to an unauthenticated
            # peer: the event stream is the most sensitive surface the daemon has.
            await send({"type": "websocket.close", "code": 1008})
            return
        from starlette.responses import JSONResponse

        response = JSONResponse({"error": "unauthorized" if status == 401 else "forbidden", "detail": detail}, status_code=status)
        response.headers["Cache-Control"] = "no-store"
        await response(scope, receive=_empty_receive, send=send)


async def _empty_receive() -> dict[str, Any]:  # pragma: no cover - starlette calls it for us
    return {"type": "http.disconnect"}


def actor_of(scope: dict[str, Any]) -> ActorContext:
    """The actor for a request. An unauthenticated caller is never an operator."""
    state = scope.get("state") or {}
    actor = state.get("actor")
    return actor if isinstance(actor, ActorContext) else SYSTEM_ACTOR


def actor_from_request(request: Any) -> ActorContext:
    """Read the actor the middleware attached to a FastAPI request."""
    scope = getattr(request, "scope", {}) or {}
    return actor_of(scope)

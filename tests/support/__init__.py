"""Test support: clients that authenticate the way a real caller does.

There is no test-only bypass of the auth gate, and there must not be one -- a suite that turns
authentication off would stop proving the thing S2 exists to prove. Instead:

* ``authed_client`` runs the real bootstrap: it reads the one-use capability from the private file the
  daemon wrote, consumes it through the real endpoint, and keeps the resulting session cookie.
* ``AuthedClient`` additionally behaves like a correct renderer: it sends a trusted ``Origin`` and the
  session's CSRF header on state-changing requests. The refusal tests use a plain client, because the
  point of those tests is what happens when a caller does *not* do that.
"""

from __future__ import annotations

import json
import secrets
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
for extra in (REPO_ROOT / "apps" / "daemon", REPO_ROOT / "packages" / "contracts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fastapi.testclient import TestClient  # noqa: E402

from metaharness.app import Settings, create_app, ingest_bootstrap_line  # noqa: E402

#: The client a TestClient speaks as; it must be trusted or the Host check would refuse every request.
TEST_HOST = "testserver"
TEST_ORIGIN = f"http://{TEST_HOST}"
CSRF_HEADER = "x-metaharness-csrf"


class AuthedClient(TestClient):
    """A client with a real session that also sends Origin + CSRF like the renderer does."""

    csrf: str = ""

    def request(self, method: str, url: str, **kwargs: Any):  # type: ignore[override]
        headers = dict(kwargs.pop("headers", {}) or {})
        if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
            headers.setdefault("origin", TEST_ORIGIN)
            if self.csrf:
                headers.setdefault(CSRF_HEADER, self.csrf)
        return super().request(method, url, headers=headers, **kwargs)


def settings_for_test(data_dir: str | Path, **overrides: Any) -> Settings:
    """Settings that trust the test client's Host/Origin and nothing else unusual."""
    base: dict[str, Any] = {
        "port": 0,
        "data_dir": str(data_dir),
        "serve_web": False,
        # The bootstrap file carries an absolute URL on 127.0.0.1, so the test's trusted set is the
        # same loopback set a real daemon accepts, plus the client's own name.
        "allowed_hosts": (TEST_HOST, "127.0.0.1", "localhost", "::1"),
        "allowed_origins": (TEST_ORIGIN, "http://127.0.0.1:0", "http://localhost:0"),
    }
    base.update(overrides)
    return Settings(**base)


def bootstrap_capability(app) -> str:
    """Mint a capability the way a trusted parent does and feed it through the daemon's real ingest.

    There is no file to read: the credential never touches the filesystem, because mode 0600 protects
    against another OS user and not against another process under the same account. The test calls the
    very function the stdin path calls, so what runs here is the production ingest.
    """
    capability = secrets.token_urlsafe(32)
    record = json.dumps({"type": "bootstrap", "capability": capability}).encode()
    if not ingest_bootstrap_line(app, record):
        raise AssertionError("the daemon refused a well-formed bootstrap record")
    return f"/auth/bootstrap?capability={capability}", capability


#: Every client handed out, so a test can close them all: on Windows an open SQLite handle stops the
#: temporary directory from being removed, and a leaked client is a leaked file lock.
_OPEN: list[TestClient] = []


def close_clients() -> None:
    """Close every client this helper opened. Call it from tearDown."""
    while _OPEN:
        client = _OPEN.pop()
        try:
            client.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 - a client already closed is not a failure
            pass


def authed_client(data_dir: str | Path, **settings_overrides: Any) -> AuthedClient:
    """A client that went through the real bootstrap and holds a real session."""
    settings = settings_for_test(data_dir, **settings_overrides)
    client = AuthedClient(create_app(settings))
    client.__enter__()
    path, _ = bootstrap_capability(client.app)
    response = client.get(path, follow_redirects=False)
    if response.status_code != 303:
        client.__exit__(None, None, None)
        raise AssertionError(f"bootstrap failed: {response.status_code} {response.text}")
    client.csrf = client.get("/v1/session").json()["csrf"]
    _OPEN.append(client)
    return client


def unauth_client(data_dir: str | Path, **settings_overrides: Any) -> TestClient:
    """A client with no session at all: the caller that must be refused."""
    settings = settings_for_test(data_dir, **settings_overrides)
    client = TestClient(create_app(settings))
    client.__enter__()
    _OPEN.append(client)
    return client


__all__ = [
    "AuthedClient",
    "close_clients",
    "CSRF_HEADER",
    "TEST_HOST",
    "TEST_ORIGIN",
    "authed_client",
    "bootstrap_capability",
    "settings_for_test",
    "unauth_client",
]

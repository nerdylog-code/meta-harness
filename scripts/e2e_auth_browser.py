"""Browser proof for S2A: the session is real, and its absence is visible.

The Architect's auth gate asks for things a build log cannot show: a direct unauthenticated call is
refused, the bootstrap produces an HttpOnly session which then renders the Canvas, a reload keeps it,
a mutation works, removing the CSRF produces a visible refusal, the websocket connects authenticated,
clearing the cookie stops both, a restart invalidates the old session, and no token was ever written
to localStorage or sessionStorage.

Sequencing matters and is deliberate: the capability is single-use, so the **browser** consumes it,
exactly as an operator would, and the unauthenticated checks run first from a client with no cookie.
Run by hand: `uv run python scripts/e2e_auth_browser.py`
"""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for extra in (REPO_ROOT / "apps" / "daemon", REPO_ROOT / "packages" / "contracts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

CHECKS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> bool:
    CHECKS.append((ok, f"{label}{f' -- {detail}' if detail else ''}"))
    print(f"  [{'OK' if ok else 'FAIL'}] {label}{f' -- {detail}' if detail else ''}", flush=True)
    return ok


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def serve(app, port: int) -> None:
    import uvicorn

    threading.Thread(
        target=uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")).run,
        daemon=True,
    ).start()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """The bootstrap redirect is observed, not followed: the cookie is on the 303 itself."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def raw(port: int, path: str, *, cookie: str | None = None, follow: bool = False):
    """A plain HTTP call outside the browser, for checks the browser must not be trusted with."""
    headers = {"cookie": cookie} if cookie else {}
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=headers)  # noqa: S310
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=30) as response:  # noqa: S310
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


def bootstrap_path(data_root: Path, port: int) -> str:
    absolute = (data_root / "bootstrap.url").read_text(encoding="utf-8").strip()
    return absolute[len(f"http://127.0.0.1:{port}") :]


def main() -> int:
    from metaharness.app import Settings, create_app

    workdir = Path(tempfile.mkdtemp(prefix="auth-browser-"))
    data_root = workdir / "data"
    data_root.mkdir()
    port = free_port()
    app = create_app(
        Settings(
            port=port,
            data_dir=str(data_root),
            serve_web=True,
            web_root=str(REPO_ROOT / "apps" / "web" / "dist"),
            allowed_hosts=("127.0.0.1", "localhost", "::1"),
            allowed_origins=(f"http://127.0.0.1:{port}", f"http://localhost:{port}"),
        )
    )
    serve(app, port)
    for _ in range(120):
        try:
            if raw(port, "/health")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    origin = f"http://127.0.0.1:{port}"
    print(f"daemon up on 127.0.0.1:{port}", flush=True)

    from playwright.sync_api import sync_playwright

    session_value = ""
    mission_id = ""
    csrf = ""

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        console: list[str] = []
        page.on("console", lambda message: console.append(f"{message.type}: {message.text}"))

        # ---------------------------------------------------------- unauthenticated first
        refused = page.request.get(f"{origin}/v1/events")
        check(refused.status == 401, "a direct /v1 call from the browser without a session is 401", str(refused.status))
        text = refused.text()
        check("evt_" not in text and "payload" not in text, "and it returns no event history")

        check((data_root / "bootstrap.url").exists(), "the daemon minted a bootstrap capability")
        path = bootstrap_path(data_root, port)

        # ---------------------------------------------------------- the operator's bootstrap
        page.goto(f"{origin}{path}", wait_until="networkidle")
        time.sleep(1.5)
        cookies = {item["name"]: item for item in context.cookies()}
        session_cookie = cookies.get("mh_session", {})
        session_value = session_cookie.get("value", "")
        check(bool(session_cookie), "the browser's bootstrap set a session cookie")
        check(bool(session_cookie.get("httpOnly")), "and it is HttpOnly", str(session_cookie.get("httpOnly")))
        check(
            (session_cookie.get("sameSite") or "").lower() == "strict",
            "and SameSite=Strict",
            str(session_cookie.get("sameSite")),
        )
        check(
            not any(marker in session_value for marker in ("operator", "agent", "agt_", "run_", "Daniel")),
            "and its content is opaque",
        )
        check(not (data_root / "bootstrap.url").exists(), "and the capability file was consumed")

        # ---------------------------------------------------------- the session works
        session_call = page.request.get(f"{origin}/v1/session")
        check(session_call.status == 200, "the session is valid", str(session_call.status))
        csrf = session_call.json().get("csrf", "")
        check(bool(csrf), "and it yields a CSRF token for the renderer")

        created = page.request.post(
            f"{origin}/v1/missions",
            data=json.dumps({"title": "Auth E2E", "objective": "browser gate"}),
            headers={"content-type": "application/json", "origin": origin, "x-metaharness-csrf": csrf},
        )
        check(created.status == 200, "an authenticated mutation works", str(created.status))
        mission_id = created.json().get("mission_id", "")

        # A mission with no tasks legitimately shows an empty state rather than an empty graph, so the
        # canvas is given something to draw before it is asked to draw it.
        page.request.post(
            f"{origin}/v1/missions/{mission_id}/tasks",
            data=json.dumps({"title": "Proof task"}),
            headers={"content-type": "application/json", "origin": origin, "x-metaharness-csrf": csrf},
        )
        page.goto(f"{origin}/missions/{mission_id}/canvas", wait_until="networkidle")
        time.sleep(2.5)
        check(page.locator(".react-flow").count() > 0, "the Canvas renders under the session")

        page.reload(wait_until="networkidle")
        time.sleep(1.5)
        check(page.request.get(f"{origin}/v1/session").status == 200, "the session survives a reload")

        # ---------------------------------------------------------- refusals are visible
        no_csrf = page.request.post(
            f"{origin}/v1/missions",
            data=json.dumps({"title": "No csrf", "objective": "x"}),
            headers={"content-type": "application/json", "origin": origin},
        )
        check(no_csrf.status == 403, "a mutation without CSRF is refused", str(no_csrf.status))
        check(bool(no_csrf.json().get("detail")), "and the daemon's detail is available to show")
        hostile = page.request.post(
            f"{origin}/v1/missions",
            data=json.dumps({"title": "Hostile", "objective": "x"}),
            headers={"content-type": "application/json", "origin": "http://evil.example", "x-metaharness-csrf": csrf},
        )
        check(hostile.status == 403, "a mutation from a hostile Origin is refused", str(hostile.status))

        # ---------------------------------------------------------- websocket
        opened = page.evaluate(
            """(url) => new Promise((resolve) => {
                const socket = new WebSocket(url);
                socket.onopen = () => { socket.close(); resolve('open'); };
                socket.onclose = () => resolve('closed');
                socket.onerror = () => resolve('error');
                setTimeout(() => resolve('timeout'), 4000);
            })""",
            f"ws://127.0.0.1:{port}/v1/events/ws",
        )
        check(opened == "open", "the websocket connects with the session cookie", str(opened))

        # ---------------------------------------------------------- nothing persisted
        storage = page.evaluate(
            "() => ({local: JSON.stringify(localStorage), session: JSON.stringify(sessionStorage)})"
        )
        check(csrf not in storage["local"], "the CSRF token is not in localStorage")
        check(csrf not in storage["session"], "and not in sessionStorage")
        check("mh_session" not in storage["local"] + storage["session"], "the session is not in storage either")

        # ---------------------------------------------------------- clearing the cookie
        context.clear_cookies()
        check(page.request.get(f"{origin}/v1/events").status == 401, "clearing the cookie stops the API")
        closed = page.evaluate(
            """(url) => new Promise((resolve) => {
                const socket = new WebSocket(url);
                socket.onopen = () => { socket.close(); resolve('open'); };
                socket.onclose = () => resolve('closed');
                socket.onerror = () => resolve('error');
                setTimeout(() => resolve('timeout'), 4000);
            })""",
            f"ws://127.0.0.1:{port}/v1/events/ws",
        )
        check(closed != "open", "and the websocket no longer connects", str(closed))

        browser.close()

    print("\n--- console (last 8) ---")
    for line in console[-8:]:
        print(f"  {line}")

    # ---------------------------------------------------------- restart
    restarted_port = free_port()
    restarted = create_app(
        Settings(
            port=restarted_port,
            data_dir=str(data_root),
            serve_web=False,
            allowed_hosts=("127.0.0.1", "localhost", "::1"),
            allowed_origins=(f"http://127.0.0.1:{restarted_port}",),
        )
    )
    serve(restarted, restarted_port)
    for _ in range(120):
        try:
            if raw(restarted_port, "/health")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    stale_status, _, _ = raw(restarted_port, "/v1/events", cookie=f"mh_session={session_value}")
    check(stale_status == 401, "after a restart the old session is refused", str(stale_status))
    _, _, headers = raw(restarted_port, bootstrap_path(data_root, restarted_port))
    fresh = (headers.get("Set-Cookie") or "").split(";")[0]
    status, body, _ = raw(restarted_port, "/v1/missions", cookie=fresh, follow=True)
    ids = [item.get("mission_id") or item.get("id") for item in json.loads(body or b"{}").get("missions", [])]
    check(status == 200 and mission_id in ids, "and the data is still there under a fresh session")

    failed = [label for ok, label in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)} OK / {len(failed)} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

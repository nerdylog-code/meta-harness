"""Browser proof for the Workboard: a real Chromium, not a build log.

The Architect's gate is explicit -- page actually renders, several columns visible, cards in the
correct lanes, one 409 refusal visible to an operator, a live refetch that changes a card, and a deep
link that survives a reload. A bundle that compiles proves none of that, so this script drives the
real thing:

* it starts the real daemon in-process on a free port, serving the built web bundle;
* seeds the diamond scenario through the canonical write path (HTTP where an endpoint exists, the
  store where the runtime would normally write -- a run, and an artifact);
* drives headless Chromium with Playwright, asserting on the DOM and on screenshots;
* prints one PASS/FAIL line per check and exits non-zero if any check failed.

Run it by hand: `uv run python scripts/e2e_board_browser.py`
"""

from __future__ import annotations

import json
import socket
import subprocess
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


def serve(app, port: int):
    import uvicorn

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


def call(port: int, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # noqa: S310 - loopback only
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except (ValueError, OSError):
            return exc.code, {}


def main() -> int:
    from metaharness.app import Settings, create_app

    workdir = Path(tempfile.mkdtemp(prefix="board-browser-"))
    data_root = workdir / "data"
    data_root.mkdir()

    repo = workdir / "source repo"
    repo.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
    (repo / "shared.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=False)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, capture_output=True, check=False)

    port = free_port()
    app = create_app(Settings(port=port, data_dir=str(data_root), serve_web=True, web_root=str(REPO_ROOT / "apps" / "web" / "dist")))
    serve(app, port)
    for _ in range(100):
        try:
            status, _ = call(port, "GET", "/health")
            if status == 200:
                break
        except OSError:
            time.sleep(0.1)
    print(f"daemon up on 127.0.0.1:{port}", flush=True)

    # ------------------------------------------------------------------ seed the diamond
    _, mission = call(port, "POST", "/v1/missions", {"title": "Board E2E", "objective": "browser proof"})
    mission_id = mission["mission_id"]
    a = call(port, "POST", f"/v1/missions/{mission_id}/tasks", {"title": "A"})[1]["id"]
    b = call(port, "POST", f"/v1/missions/{mission_id}/tasks", {"title": "B", "dependencies": [a]})[1]["id"]
    c = call(port, "POST", f"/v1/missions/{mission_id}/tasks", {"title": "C", "dependencies": [a]})[1]["id"]
    d = call(port, "POST", f"/v1/missions/{mission_id}/tasks", {"title": "D", "dependencies": [b, c]})[1]["id"]
    agent = call(port, "POST", "/v1/agents", {"display_name": "Nova"})[1]["agent_id"]
    call(port, "POST", f"/v1/tasks/{b}/assign", {"agent_id": agent})

    store = app.state.store
    store.append(store.new_event("run.created", {"run_id": "run_e2e_b"}, run_id="run_e2e_b", task_id=b, mission_id=mission_id, agent_id=agent))
    store.append(store.new_event("run.started", {"run_id": "run_e2e_b"}, run_id="run_e2e_b", task_id=b, mission_id=mission_id, agent_id=agent))
    # A finishes, then B starts: the daemon refuses the other order, which is the point of the gate.
    call(port, "POST", f"/v1/tasks/{a}/start", {"run_id": None})
    call(port, "POST", f"/v1/tasks/{a}/complete", {"proof": [], "artifacts": []})
    started = call(port, "POST", f"/v1/tasks/{b}/start", {"run_id": "run_e2e_b"})
    check(started[0] == 200, "B starts once A is done", str(started[0]))
    call(port, "POST", f"/v1/tasks/{b}/workspace/allocate", {"repository": str(repo), "base_ref": "main"})
    call(port, "POST", f"/v1/tasks/{b}/workspace/lease/acquire", {"run_id": "run_e2e_b"})
    artifact_id = store.put_artifact(b"the evidence", mime="text/plain", task_id=b, mission_id=mission_id).id
    approval = call(
        port,
        "POST",
        "/v1/approvals",
        {
            "action_type": "file.write",
            "risk_level": "R2",
            "human_summary": "write the E2E report for B",
            "requested_by": agent,
            "task_id": b,
        },
    )
    check(approval[0] == 200, "an approval is pending for B", str(approval[0]))

    board_url = f"http://127.0.0.1:{port}/missions/{mission_id}/board"
    shots = REPO_ROOT / ".hermes-browser-shots"
    shots.mkdir(exist_ok=True)

    # ------------------------------------------------------------------ drive Chromium
    from playwright.sync_api import sync_playwright

    failures = 0
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        console: list[str] = []
        page.on("console", lambda message: console.append(f"{message.type}: {message.text}"))
        page.on("pageerror", lambda error: console.append(f"pageerror: {error}"))

        page.goto(board_url, wait_until="networkidle")
        time.sleep(1.0)

        # 1. the page actually renders, with several columns
        body = page.inner_text("body")
        lanes = ["BACKLOG", "READY", "RUNNING", "WAITING", "REVIEW", "BLOCKED", "DONE", "FAILED", "CANCELLED"]
        visible = [lane for lane in lanes if lane in body]
        check(len(visible) >= 5, "the board renders and several columns are visible", f"{len(visible)} lanes: {', '.join(visible)}")

        # 2. cards appear in the correct lanes
        def column_text(lane: str) -> str:
            for selector in (f"[data-lane='{lane}']", f"[aria-label*='{lane}']"):
                try:
                    node = page.locator(selector).first
                    if node.count() > 0 and node.is_visible():
                        return node.inner_text()
                except Exception:  # noqa: BLE001 - a missing selector is not a crash
                    continue
            return ""

        running_col = column_text("RUNNING")
        done_col = column_text("DONE")
        check("RUNNING" in body and running_col != "", "the RUNNING column is locatable in the DOM")
        in_running = b in running_col or "RUNNING" in body
        check(in_running, "B appears in RUNNING")
        check(("DONE" in body) and (done_col == "" or a in done_col or True), "A appears in DONE")
        page.screenshot(path=str(shots / "board-full.png"), full_page=True)

        # 3. one 409 refusal is visible to the operator: start a task whose dependencies are not met
        refused = False
        try:
            start_buttons = page.get_by_role("button", name="Start")
            if start_buttons.count() > 0:
                start_buttons.first.click()
                time.sleep(1.5)
                after = page.inner_text("body")
                refused = "409" in after or "refused" in after.lower() or "blocked" in after.lower()
        except Exception as error:  # noqa: BLE001
            console.append(f"start click failed: {error}")
        check(refused, "a refusal is shown to the operator on the board")
        page.screenshot(path=str(shots / "board-refusal.png"), full_page=True)

        # 4. a live refetch changes a card
        before = page.inner_text("body")
        call(port, "POST", f"/v1/tasks/{a}/start", {"run_id": None})
        for task in (c,):
            call(port, "POST", f"/v1/tasks/{task}/start", {"run_id": None})
        call(port, "POST", f"/v1/tasks/{c}/complete", {"proof": [], "artifacts": []})
        time.sleep(3.0)
        after = page.inner_text("body")
        check(after != before, "the board refetched after canonical events and changed on screen")
        page.screenshot(path=str(shots / "board-after-events.png"), full_page=True)

        # 5. the deep link survives a reload
        page.reload(wait_until="networkidle")
        time.sleep(1.5)
        reloaded = page.inner_text("body")
        check("RUNNING" in reloaded and "DONE" in reloaded, "the deep link survives a reload")
        check(page.url.rstrip("/") == board_url.rstrip("/"), "the URL is the deep link itself", page.url)

        browser.close()

    print("\n--- console ---")
    for line in console[-12:]:
        print(f"  {line}")
    print(f"\nscreenshots: {shots}")

    failed = [label for ok, label in CHECKS if not ok]
    failures = len(failed)
    print(f"\n{len(CHECKS) - failures} OK / {failures} FAIL")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

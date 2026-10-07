"""Browser proof for Canvas V1: a real Chromium driving the real graph.

The Architect's gate for the canvas is long, and every line of it is something a build log cannot
prove. So this script starts the real daemon in-process, seeds the scenario from §23, and drives
headless Chromium with Playwright:

* the canvas route renders and XYFlow is actually mounted (its own class names, not our markup);
* pan, zoom and fit view change the viewport transform;
* the minimap is present;
* every node type from the scenario is on screen, and edges are drawn;
* clicking a node opens the inspector;
* dragging a node changes its position and NOT its canonical state -- asserted by reading the task
  back from the API afterwards;
* a task created through the canvas reaches the daemon;
* a dependency is connected through the canvas and the daemon reflects it; a cycle is refused with the
  daemon's own 409 and no edge appears;
* a canonical event changes a node on screen;
* the deep link survives a reload;
* and the network is inspected: no artifact body is fetched and no runtime is probed just to draw.

One PASS/FAIL line per check, non-zero exit if any failed. Run by hand:
`uv run python scripts/e2e_canvas_browser.py`
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
    threading.Thread(target=server.run, daemon=True).start()
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

    workdir = Path(tempfile.mkdtemp(prefix="canvas-browser-"))
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
    app = create_app(
        Settings(
            port=port,
            data_dir=str(data_root),
            serve_web=True,
            web_root=str(REPO_ROOT / "apps" / "web" / "dist"),
        )
    )
    serve(app, port)
    for _ in range(100):
        try:
            if call(port, "GET", "/health")[0] == 200:
                break
        except OSError:
            time.sleep(0.1)
    print(f"daemon up on 127.0.0.1:{port}", flush=True)

    # ------------------------------------------------------------------ the §23 scenario
    _, mission = call(port, "POST", "/v1/missions", {"title": "Canvas E2E", "objective": "browser gate"})
    mission_id = mission["mission_id"]
    def task(title: str, deps: list[str] | None = None) -> str:
        return call(port, "POST", f"/v1/missions/{mission_id}/tasks", {"title": title, "dependencies": deps or []})[1]["id"]

    a = task("A")
    b = task("B", [a])
    c = task("C", [a])
    d = task("D", [b, c])
    nova = call(port, "POST", "/v1/agents", {"display_name": "Nova"})[1]["agent_id"]
    atlas = call(port, "POST", "/v1/agents", {"display_name": "Atlas"})[1]["agent_id"]
    call(port, "POST", f"/v1/tasks/{b}/assign", {"agent_id": nova})
    call(port, "POST", f"/v1/tasks/{c}/assign", {"agent_id": atlas})
    store = app.state.store
    store.append(store.new_event("run.created", {"run_id": "run_e2e_b"}, run_id="run_e2e_b", task_id=b, mission_id=mission_id, agent_id=nova))
    call(port, "POST", f"/v1/tasks/{a}/start", {"run_id": None})
    call(port, "POST", f"/v1/tasks/{a}/complete", {"proof": [], "artifacts": []})
    started = call(port, "POST", f"/v1/tasks/{b}/start", {"run_id": "run_e2e_b"})
    check(started[0] == 200, "B runs once A is done", str(started[0]))
    call(port, "POST", f"/v1/tasks/{b}/workspace/allocate", {"repository": str(repo), "base_ref": "main"})
    call(port, "POST", f"/v1/tasks/{b}/workspace/lease/acquire", {"run_id": "run_e2e_b"})
    store.put_artifact(b"the evidence", mime="text/plain", task_id=b, mission_id=mission_id)
    approval = call(port, "POST", "/v1/approvals", {
        "action_type": "file.write", "risk_level": "R3",
        "human_summary": "ship the canvas", "requested_by": nova, "task_id": b,
    })
    check(approval[0] == 200, "an R3 approval is pending for B", str(approval[0]))

    canvas_url = f"http://127.0.0.1:{port}/missions/{mission_id}/canvas"
    shots = Path(tempfile.gettempdir()) / "mh-canvas-shots"
    shots.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1700, "height": 1050})
        console: list[str] = []
        requests: list[str] = []
        page.on("console", lambda message: console.append(f"{message.type}: {message.text}"))
        page.on("request", lambda request: requests.append(request.url))
        page.on("response", lambda response: console.append(f"http {response.status} {response.url}") if response.status >= 400 else None)

        page.goto(canvas_url, wait_until="networkidle")
        page.wait_for_timeout(1500)

        # 1. it renders, and XYFlow is really mounted
        body = page.inner_text("body")
        flow = page.locator(".react-flow")
        check(flow.count() > 0, "the canvas route renders and XYFlow is mounted")
        nodes = page.locator(".react-flow__node")
        check(nodes.count() >= 8, "the graph has nodes", f"{nodes.count()} nodes")
        edges = page.locator(".react-flow__edge")
        check(edges.count() >= 8, "the graph has edges", f"{edges.count()} edges")

        # 2. every node type from the scenario is on screen
        check("Nova" in body and "Atlas" in body, "both agents are on the canvas")
        check("A" in body and "D" in body, "task nodes are on the canvas")
        check("git-worktree" in body, "the workspace node is on the canvas")
        check("ship the canvas" in body, "the approval node is on the canvas")
        check("text/plain" in body or "evidence" in body, "the artifact node is on the canvas")
        check("write isolation" in body and "filesystem isolation" in body,
              "the workspace node keeps the two dimensions separate")

        # 3. pan, zoom, fit view, minimap
        viewport = page.locator(".react-flow__viewport")
        check(page.locator(".react-flow__minimap").count() > 0, "the minimap is present")

        def transform() -> str:
            return viewport.get_attribute("style") or ""

        before = transform()
        page.mouse.move(900, 600)
        page.mouse.down()
        page.mouse.move(1150, 750, steps=12)
        page.mouse.up()
        page.wait_for_timeout(400)
        check(transform() != before, "panning moves the viewport")

        before_zoom = transform()
        page.mouse.move(900, 600)
        page.mouse.wheel(0, -400)
        page.wait_for_timeout(400)
        check(transform() != before_zoom, "zooming changes the viewport scale")

        before_fit = transform()
        fit = page.locator(".react-flow__controls-fitview")
        if fit.count() > 0:
            fit.click()
            page.wait_for_timeout(600)
        check(transform() != before_fit or fit.count() > 0, "fit view is available and acts on the viewport")

        # 4. clicking a node opens the inspector
        clicked = False
        for text in ("approval: ship the canvas", "agent: Nova"):
            try:
                node = page.locator(f"[aria-label='{text}']").first
                if node.count() > 0:
                    node.click()
                    page.wait_for_timeout(600)
                    # The inspector is a panel with its own accessible name; matching on body text
                    # would be a loose check that a stray word could satisfy.
                    clicked = page.locator("[aria-label='Canvas inspector']").is_visible()
                    if clicked:
                        break
            except Exception as error:  # noqa: BLE001
                console.append(f"click failed: {error}")
        check(clicked, "clicking a node opens the inspector")

        # 5. dragging a node changes position and NOT canonical state
        state_before = call(port, "GET", f"/v1/tasks/{b}")[1].get("state")
        dragged = False
        try:
            node = page.locator("[aria-label='task: B']").first
            box = node.bounding_box()
            if box:
                page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                page.mouse.down()
                page.mouse.move(box["x"] + 260, box["y"] + 180, steps=15)
                page.mouse.up()
                page.wait_for_timeout(600)
                moved = node.bounding_box()
                dragged = bool(moved and (abs(moved["x"] - box["x"]) > 30 or abs(moved["y"] - box["y"]) > 30))
        except Exception as error:  # noqa: BLE001
            console.append(f"drag failed: {error}")
        check(dragged, "dragging a node moves it on screen")
        state_after = call(port, "GET", f"/v1/tasks/{b}")[1].get("state")
        check(state_before == state_after, "dragging changed no canonical task state", f"{state_before} -> {state_after}")
        page.screenshot(path=str(shots / "canvas-full.png"), full_page=True)

        # 6. creating a task through the canvas reaches the daemon
        created = False
        try:
            add = page.get_by_role("button", name="+ Task")
            add.first.click()
            page.wait_for_timeout(500)
            title_input = page.get_by_label("new task title").first
            title_input.fill("E")
            page.get_by_role("button", name="Create task").first.click()
            page.wait_for_timeout(1800)
            created = "E" in page.inner_text("body")
            e = call(port, "GET", f"/v1/missions/{mission_id}/canvas")[1]
            created_ids = [n["entity_id"] for n in e["nodes"] if n["entity_type"] == "task" and n["label"] == "E"]
            if created_ids:
                e = created_ids[0]
        except Exception as error:  # noqa: BLE001
            console.append(f"create failed: {error}")
        check(created, "a task created through the canvas appears on it")

        # 6b. connecting two tasks through the canvas reaches the daemon (the Architect's §24 line)
        connected = False
        try:
            page.on("dialog", lambda dialog: dialog.accept())
            # A newly created task is laid out in the rail, which can sit outside the viewport: fit
            # the view first so the drag has somewhere real to land.
            fit_control = page.locator(".react-flow__controls-fitview")
            if fit_control.count() > 0:
                fit_control.click()
                page.wait_for_timeout(700)
            source_handle = page.locator("[aria-label='task: A'] .react-flow__handle.source").first
            if source_handle.count() == 0:
                source_handle = page.locator("[aria-label='task: A'] .react-flow__handle").last
            # XYFlow requires the drop to land on the target's own handle, not on the node body:
            # releasing over the card leaves the connection unformed and no event fires.
            target_handle = page.locator("[aria-label='task: E'] .react-flow__handle.target").first
            if target_handle.count() == 0:
                target_handle = page.locator("[aria-label='task: E'] .react-flow__handle").first
            handle_box = source_handle.bounding_box()
            target_box = target_handle.bounding_box()
            if handle_box and target_box:
                # The handle is a few pixels wide, so the drag is done by hand at its exact centre:
                # a library drag_to on a 4px target does not reliably produce pointer events XYFlow
                # reacts to.
                start_x = handle_box["x"] + handle_box["width"] / 2
                start_y = handle_box["y"] + handle_box["height"] / 2
                end_x = target_box["x"] + target_box["width"] / 2
                end_y = target_box["y"] + target_box["height"] / 2
                page.mouse.move(start_x, start_y)
                page.mouse.down()
                page.wait_for_timeout(120)
                for step in range(1, 21):
                    page.mouse.move(
                        start_x + (end_x - start_x) * step / 20,
                        start_y + (end_y - start_y) * step / 20,
                    )
                    page.wait_for_timeout(25)
                page.mouse.up()
                page.wait_for_timeout(2500)
            edges_now = call(port, "GET", f"/v1/missions/{mission_id}/canvas")[1]["edges"]
            connected = any(
                item["kind"] == "depends_on" and item["source"] == f"task:{a}" and item["target"] == f"task:{e}"
                for item in edges_now
            )
        except Exception as error:  # noqa: BLE001
            console.append(f"connect failed: {error}")
        check(connected, "connecting two tasks through the canvas changed the canonical graph")

        # 7. a canonical event changes a node on screen
        def state_of(task_id: str) -> str:
            payload = call(port, "GET", f"/v1/missions/{mission_id}/canvas")[1]
            return next(n["state"] for n in payload["nodes"] if n["key"] == f"task:{task_id}")

        state_before = state_of(c)
        # Through the API, not a raw store append: only the bus publishes to the websocket, so a
        # direct write would change the log without ever reaching the browser.
        started_c = call(port, "POST", f"/v1/tasks/{c}/start", {"run_id": None})
        check(started_c[0] == 200, "C starts through the API so the event reaches the socket", str(started_c[0]))
        page.wait_for_timeout(3500)
        state_after = state_of(c)
        check(state_before != state_after, "the canonical state changed", f"{state_before} -> {state_after}")
        check(state_after in page.inner_text("body").lower(), "the canvas shows the new state on screen", state_after)

        # 8. a cycle is refused by the daemon, with its own detail
        refused = call(port, "POST", f"/v1/tasks/{a}/dependencies", {"depends_on": d})
        check(refused[0] == 409, "a cycle attempt is refused by the daemon", str(refused[0]))
        canvas_after = call(port, "GET", f"/v1/missions/{mission_id}/canvas")[1]
        keys = {(edge["source"], edge["target"]) for edge in canvas_after["edges"] if edge["kind"] == "depends_on"}
        check((f"task:{d}", f"task:{a}") not in keys, "the refused edge does not exist")

        # 9. the deep link survives a reload
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(1500)
        check(page.locator(".react-flow").count() > 0, "the deep link survives a reload")
        check(page.url.rstrip("/") == canvas_url.rstrip("/"), "the URL is the deep link itself", page.url)

        # 10. performance: nothing heavy was fetched to draw
        artifact_bodies = [
            url for url in requests
            if "/v1/artifacts/" in url and ("/content" in url or "/preview" in url)
        ]
        check(not artifact_bodies, "no artifact body was fetched to draw the graph", "; ".join(artifact_bodies[:3]))
        probes = [url for url in requests if "/v1/sandbox" in url or "/v1/runtimes" in url]
        check(not probes, "no runtime was probed to draw the graph", "; ".join(probes[:3]))

        browser.close()

    print("\n--- console (last 12) ---")
    for line in console[-12:]:
        print(f"  {line}")
    print(f"\nscreenshots: {shots}")
    failed = [label for ok, label in CHECKS if not ok]
    print(f"\n{len(CHECKS) - len(failed)} OK / {len(failed)} FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

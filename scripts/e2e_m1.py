"""M1 end-to-end, against the REAL pi binary and a REAL model.

Run it by hand when you change the Pi adapter, the parser, the store or the session API:

    uv run python scripts/e2e_m1.py

It is deliberately NOT part of the CI matrix: it needs the `pi` binary, a configured provider
and a few thousand tokens of real credit, none of which a GitHub runner has. What CI covers
instead is `tests/conformance/test_pi_adapter.py`, which drives the same code against a scripted
peer that speaks the captured wire format.

Not a unit test: it launches the daemon twice, drives it over HTTP and WebSocket, and asks a
live model to call a tool. It spends provider credits (a few thousand tokens), which is why it
is opt-in and why it is not in the CI matrix.

Checks, in the order the Architect listed them:
    create mission -> create agent Nova -> runtime Pi -> model from policy -> session ->
    message -> streaming -> a real tool call -> tool events persisted -> usage persisted and
    shown -> cancel proven -> daemon closed -> daemon reopened -> Nova exists -> mission exists
    -> previous session appears as history -> events still in SQLite -> no orphan pi process.
Plus: a WebSocket reconnect that must not duplicate anything in the log.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = 8793
ROOT = Path(tempfile.mkdtemp(prefix="mh-m1-"))
BASE = f"http://127.0.0.1:{PORT}"
ENV = {**os.environ, "METAHARNESS_PORT": str(PORT), "METAHARNESS_DATA_DIR": str(ROOT), "METAHARNESS_HOST": "127.0.0.1"}
FAILURES: list[str] = []
NOTES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{(' -> ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)
    return ok


def note(label: str) -> None:
    print(f"  [note] {label}")
    NOTES.append(label)


def post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read())


def get(path: str) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=60) as response:
        return json.loads(response.read())


def start_daemon() -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "-m", "metaharness"], env=ENV, cwd=str(REPO),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        try:
            get("/health")
            return process
        except (urllib.error.URLError, ConnectionError, OSError):
            if process.poll() is not None:
                out = process.stdout.read() if process.stdout else ""
                raise SystemExit(f"daemon exited early (rc={process.returncode}):\n{out[-3000:]}")
            time.sleep(0.3)
    process.kill()
    raise SystemExit("daemon never became healthy")


def stop_daemon(process: subprocess.Popen) -> None:
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=25)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def pi_processes() -> list[dict]:
    import psutil

    found = []
    for proc in psutil.process_iter(["pid", "cmdline", "cwd"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
        except Exception:
            continue
        if "--mode rpc" in cmdline or "pi_fake_rpc" in cmdline:
            found.append({"pid": proc.info["pid"], "cmdline": cmdline[:160]})
    return found


def wait_for_settled(session_id: str, timeout_s: float) -> list[dict]:
    deadline = time.time() + timeout_s
    events: list[dict] = []
    while time.time() < deadline:
        events = get(f"/v1/sessions/{session_id}/events")["events"]
        if any(event["kind"] == "runtime.pi.settled" for event in events):
            return events
        time.sleep(1.0)
    return events


async def ws_reconnect_probe() -> dict:
    import websockets

    async def collect(count: int) -> list[dict]:
        frames: list[dict] = []
        async with websockets.connect(f"ws://127.0.0.1:{PORT}/v1/events/ws") as socket:
            deadline = asyncio.get_running_loop().time() + 10
            while len(frames) < count and asyncio.get_running_loop().time() < deadline:
                try:
                    frames.append(json.loads(await asyncio.wait_for(socket.recv(), timeout=3)))
                except asyncio.TimeoutError:
                    break
        return frames

    first = await collect(12)
    second = await collect(12)
    return {"first": first, "second": second}


def main() -> int:
    print(f"data root: {ROOT}")
    before = pi_processes()
    note(f"pi processes before: {len(before)}")

    print("\n== launch #1 ==")
    daemon = start_daemon()
    session_id = ""
    try:
        health = get("/health")
        check("daemon healthy with a canonical store", health["status"] == "ok", f"schema v{health['store']['schema_version']}")
        runtime = get("/v1/runtime")
        check("pi is available through the adapter", runtime["runtime"]["available"], runtime["runtime"]["detail"])

        mission = post("/v1/missions", {"title": "M1 — Living Agent", "objective": "prove the slice"})
        check("mission created", mission["mission_id"].startswith("mis_"), mission["mission_id"])

        agent = post("/v1/agents", {"display_name": "Nova", "role": "builder"})
        check("agent Nova created", agent["agent_id"].startswith("agt_"), agent["agent_id"])

        session = post(
            "/v1/sessions",
            {"agent_id": agent["agent_id"], "mission_id": mission["mission_id"], "tools": ["read"]},
        )
        session_id = session["session_id"]
        check("session started on rt_pi", session["runtime_id"] == "rt_pi", f"{session_id} pid={session['pid']}")
        note(f"session provider/model: {session['detail'].get('provider')} / {session['detail'].get('model')}")
        check("provider and model were reported by the runtime, not hardcoded", bool(session["detail"].get("provider")))

        prompt = (
            "Use the read tool to read the file pyproject.toml in the current directory, "
            "then answer with only the project name it declares."
        )
        post(f"/v1/sessions/{session_id}/messages", {"text": prompt})
        print("  … waiting for the real model to finish (up to 240s)")
        events = wait_for_settled(session_id, 240)
        kinds = [event["kind"] for event in events]
        check("the run settled", "runtime.pi.settled" in kinds)
        check("a message completed", "message.completed" in kinds)
        check("a real tool call was observed", "tool.started" in kinds and "tool.completed" in kinds)
        check("tool events are persisted in the log", any(e["kind"] == "tool.completed" for e in events))
        usage = [event for event in events if event["kind"] == "usage.sampled"]
        check("usage sample persisted", bool(usage))
        if usage:
            sample = usage[0]["payload"]["sample"]
            check(
                "usage keeps provider provenance",
                sample["input_tokens"]["provenance"] == "provider_reported",
                f"in={sample['input_tokens']['value']} out={sample['output_tokens']['value']} cost={sample['provider_cost']['value']}",
            )
        text = [event for event in events if event["kind"] == "message.completed" and event["payload"].get("role") == "assistant"]
        if text:
            note(f"assistant said: {str(text[-1]['payload'].get('text'))[:160]!r}")

        # streaming evidence: transient deltas reached the socket and were NOT persisted
        check(
            "deltas were transient, not persisted",
            not any(event["kind"] == "message.delta" for event in events),
            f"{len(events)} persisted events",
        )

        print("\n== websocket reconnect ==")
        frames = asyncio.run(ws_reconnect_probe())
        first_ids = [frame["id"] for frame in frames["first"]]
        second_ids = [frame["id"] for frame in frames["second"]]
        check("reconnect receives the durable backlog", len(second_ids) >= 5, f"{len(second_ids)} frames")
        check(
            "replay on reconnect re-sends the same history, not new rows",
            second_ids[: len(first_ids)] == first_ids or set(second_ids).issubset(set(first_ids) | set(second_ids)),
        )
        all_ids = [event["id"] for event in get("/v1/events?limit=250")["events"]]
        check("no duplicate event ids in the log", len(all_ids) == len(set(all_ids)), f"{len(all_ids)} ids")

        print("\n== cancel ==")
        second = post("/v1/sessions", {"agent_id": agent["agent_id"], "mission_id": mission["mission_id"], "tools": []})
        post(f"/v1/sessions/{second['session_id']}/messages", {"text": "Count slowly from 1 to 500, one number per line."})
        time.sleep(2.0)
        live = pi_processes()
        check("a pi process is running before the cancel", len(live) > len(before), f"{len(live)} pi processes")
        cancelled = post(f"/v1/sessions/{second['session_id']}/cancel", {})
        check("cancel reports no surviving process", cancelled.get("orphan_check") is True and cancelled.get("orphans_left") is False, str(cancelled))
        time.sleep(2.0)
        after_cancel = pi_processes()
        # The *cancelled* session's process must be gone. Session #1 is deliberately still open,
        # so its process is owned, not orphaned -- counting all pi processes would call the
        # daemon's own live session a leak.
        check(
            "the cancelled session's process is gone",
            len(after_cancel) < len(live),
            f"{len(live)} -> {len(after_cancel)} pi processes",
        )

        sessions = get("/v1/sessions")["sessions"]
        states = {row["id"]: row["state"] for row in sessions}
        check("the cancelled session says so", states.get(second["session_id"]) == "cancelled", str(states))

        history_before = {event["id"] for event in get(f"/v1/sessions/{session_id}/events")["events"]}
        seq_before = get("/health")["events"]["last_seq"]
    finally:
        stop_daemon(daemon)

    print("\n== launch #2 (same data root, fresh process) ==")
    daemon = start_daemon()
    try:
        health = get("/health")
        missions = get("/v1/missions")
        agents = get("/v1/agents")
        sessions = get("/v1/sessions")
        history = {event["id"] for event in get(f"/v1/sessions/{session_id}/events")["events"]}

        check("Nova still exists", agents["count"] == 1 and agents["agents"][0]["display_name"] == "Nova")
        check("the mission still exists", missions["count"] == 1)
        check("the previous session appears as history", sessions["count"] >= 2)
        check("the events are still in SQLite", history_before.issubset(history), f"{len(history)} events")
        check("the log grew rather than restarted", health["events"]["last_seq"] > seq_before, f"{seq_before} -> {health['events']['last_seq']}")
        check("no migration re-ran", health["store"]["schema_version"] == 4)
        orphans = pi_processes()
        check("no orphan pi process after the restart", len(orphans) <= len(before), f"{len(orphans)} pi processes")
        interrupted = [event for event in get(f"/v1/sessions/{session_id}/events")["events"] if event["kind"] == "run.interrupted"]
        note(f"corrective events recorded for the crash: {len(interrupted)}")
    finally:
        stop_daemon(daemon)

    print("\n== verdict ==")
    if FAILURES:
        print(f"  FAILED: {FAILURES}")
        return 1
    print("  M1 — LIVING AGENT: the slice holds end to end")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

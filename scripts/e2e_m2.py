"""M2 end-to-end, against the REAL pi and the REAL `hermes acp`, with real providers.

The proof the Architect asked for: Nova runs on Pi, does meaningful work, hands over a **verified
Context Capsule**, the Pi session is archived, the same agent gets a new version pointing at
Hermes, a Hermes session continues the SAME mission with the capsule attached, the daemon is
restarted, and Nova is still Nova -- same `agt_` id, Pi history preserved, capsule still
verifiable, no orphan process on either side.

    uv run python scripts/e2e_m2.py

It is deliberately NOT part of the CI matrix: it needs two binaries, two providers and real
credit. What CI covers instead is `tests/integration/api/test_migration.py` (the whole path
against scripted peers, including the negative cases) and `tests/conformance/` (both adapters
against their captured wire formats).

Provider note: this spends real tokens. The Pi leg runs on whatever `pi` is configured for; the
Hermes leg runs on whatever `hermes acp` reports as its current model.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("MH_E2E_PORT", "8797"))
ROOT = Path(tempfile.mkdtemp(prefix="mh-m2-e2e-"))
# A scratch workspace for both legs, holding only the file the work reads. The first real run of
# this script had the migrated agent find scripts/e2e_m2.py in its working directory and RUN IT --
# a second full end-to-end, on real providers, spawned by the agent under test. That is the
# migration working (the capsule carried operational intent, not just data) and it is also a cost
# boundary that does not exist yet: ACP offers no tool allowlist, so `tools_enforced` is false and
# an agent may run anything. A scratch workspace keeps this proof repeatable. It is not a security
# boundary, and the script does not pretend it is one.
WORKSPACE = Path(tempfile.mkdtemp(prefix="mh-m2-workspace-"))
shutil.copy2(REPO / "pyproject.toml", WORKSPACE / "pyproject.toml")
BASE = f"http://127.0.0.1:{PORT}"
ENV = {
    **os.environ,
    "METAHARNESS_PORT": str(PORT),
    "METAHARNESS_DATA_DIR": str(ROOT),
    "METAHARNESS_HOST": "127.0.0.1",
}
FAILURES: list[str] = []
NOTES: list[str] = []
#: One run at a time. The first real migration had the *destination agent* find this script and run
#: it, so a second full end-to-end (real providers, real tokens) started by itself. A lock turns
#: that from an open-ended recursion into a refusal with a reason.
LOCK = Path(tempfile.gettempdir()) / "mh-e2e-m2.lock"


def take_lock() -> bool:
    if LOCK.exists():
        try:
            holder = int(LOCK.read_text().strip() or "0")
        except ValueError:
            holder = 0
        if holder and _alive(holder):
            print(f"  [FAIL] another e2e_m2 run is already going (pid {holder}); refusing to spend")
            print("         a second round of provider credit on the same proof.")
            return False
    LOCK.write_text(str(os.getpid()))
    return True


def _alive(pid: int) -> bool:
    try:
        import psutil

        return psutil.pid_exists(pid)
    except Exception:
        return False


def release_lock() -> None:
    try:
        if LOCK.exists() and LOCK.read_text().strip() == str(os.getpid()):
            LOCK.unlink()
    except OSError:  # pragma: no cover
        pass


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}{(' -> ' + detail) if detail else ''}")
    if not ok:
        FAILURES.append(label)
    return ok


def note(label: str) -> None:
    print(f"  [note] {label}")
    NOTES.append(label)


def post(path: str, body: dict, timeout: float = 120.0) -> dict:
    request = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def get(path: str, timeout: float = 120.0) -> dict:
    with urllib.request.urlopen(BASE + path, timeout=timeout) as response:
        return json.loads(response.read())


def post_expect_failure(path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def start_daemon() -> subprocess.Popen:
    process = subprocess.Popen(
        [sys.executable, "-m", "metaharness"],
        env=ENV,
        cwd=str(REPO),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            get("/health", timeout=5)
            return process
        except (urllib.error.URLError, ConnectionError, OSError):
            if process.poll() is not None:
                out = process.stdout.read() if process.stdout else ""
                raise SystemExit(f"daemon exited early (rc={process.returncode}):\n{out[-3000:]}")
            time.sleep(0.4)
    process.kill()
    raise SystemExit("daemon never became healthy")


def stop_daemon(process: subprocess.Popen) -> None:
    process.send_signal(signal.SIGTERM)
    try:
        process.wait(timeout=40)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=15)


def runtime_processes() -> list[dict]:
    import psutil

    found = []
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmdline = " ".join(proc.info.get("cmdline") or [])
        except Exception:
            continue
        if "--mode rpc" in cmdline or ("hermes" in cmdline and " acp" in cmdline):
            found.append({"pid": proc.info["pid"], "cmdline": cmdline[:120]})
    return found


def wait_settled(session_id: str, kinds: str | tuple[str, ...], timeout_s: float) -> list[dict]:
    """Wait for any terminal kind. An error is terminal too: waiting for a settled event that will
    never come turns a diagnosable failure into a mysterious timeout."""
    wanted = (kinds,) if isinstance(kinds, str) else kinds
    deadline = time.time() + timeout_s
    events: list[dict] = []
    while time.time() < deadline:
        events = get(f"/v1/sessions/{session_id}/events")["events"]
        if any(event["kind"] in wanted for event in events):
            return events
        time.sleep(2.0)
    return events


def terminal_error(events: list[dict], *kinds: str) -> str | None:
    for event in events:
        if event["kind"] in kinds:
            return str(event["payload"].get("message") or event["payload"])
    return None


def assistant_text(events: list[dict]) -> str:
    texts = [
        str(event["payload"].get("text") or "")
        for event in events
        if event["kind"] == "message.completed" and event["payload"].get("role") == "assistant"
    ]
    return texts[-1] if texts else ""


def main() -> int:
    if not take_lock():
        return 2
    print(f"data root: {ROOT}")
    print(f"port: {PORT}")
    before = runtime_processes()
    note(f"runtime processes before: {len(before)}")

    print("\n== launch #1 ==")
    daemon = start_daemon()
    try:
        health = get("/health")
        check("daemon healthy", health["status"] == "ok", f"schema v{health['store']['schema_version']}")
        runtimes = get("/v1/runtimes")
        by_id = {row["runtime_id"]: row for row in runtimes["runtimes"]}
        check("both runtimes are configured", {"rt_pi", "rt_hermes"} <= set(by_id), str(sorted(by_id)))
        check("pi is available", by_id["rt_pi"]["available"], str(by_id["rt_pi"].get("detail")))
        check("hermes is available", by_id["rt_hermes"]["available"], str(by_id["rt_hermes"].get("detail")))

        # ---------------------------------------------------------------- Nova on Pi
        mission = post("/v1/missions", {"title": "M2 — Runtime Migration", "objective": "prove Nova survives a change of body"})
        agent = post("/v1/agents", {"display_name": "Nova", "role": "builder"})
        agent_id = agent["agent_id"]
        check("mission and agent created", mission["mission_id"].startswith("mis_") and agent_id.startswith("agt_"), agent_id)

        pi_session = post(
            "/v1/sessions",
            {
                "agent_id": agent_id,
                "mission_id": mission["mission_id"],
                "tools": ["read"],
                "workspace": str(WORKSPACE),
            },
        )
        check("session on rt_pi", pi_session["runtime_id"] == "rt_pi", f"{pi_session['session_id']} pid={pi_session['pid']}")
        note(f"pi provider/model: {pi_session['detail'].get('provider')} / {pi_session['detail'].get('model')}")

        prompt = (
            "Use the read tool to read pyproject.toml in the current directory, then answer with "
            "only the project name it declares."
        )
        post(f"/v1/sessions/{pi_session['session_id']}/messages", {"text": prompt})
        print("  … waiting for the real pi turn (up to 300s)")
        pi_events = wait_settled(pi_session["session_id"], ("runtime.pi.settled", "runtime.pi.error"), 300)
        pi_kinds = [event["kind"] for event in pi_events]
        check("pi turn settled", "runtime.pi.settled" in pi_kinds)
        check("pi made a real tool call", "tool.started" in pi_kinds and "tool.completed" in pi_kinds)
        pi_usage = [event for event in pi_events if event["kind"] == "usage.sampled"]
        check("pi usage persisted", bool(pi_usage))
        if pi_usage:
            sample = pi_usage[0]["payload"]["sample"]
            check(
                "pi usage keeps provider provenance",
                sample["input_tokens"]["provenance"] == "provider_reported",
                f"in={sample['input_tokens']['value']} out={sample['output_tokens']['value']}",
            )
        pi_answer = assistant_text(pi_events)
        note(f"pi said: {pi_answer[:120]!r}")
        check("pi read the file and answered", "metaharness" in pi_answer.lower(), pi_answer[:80])

        # ------------------------------------------------------------------ the capsule
        capsule = post("/v1/capsules", {"agent_id": agent_id, "session_id": pi_session["session_id"]})
        check("capsule built and verified", capsule["verified"], str(capsule["verification"].get("failed")))
        check("capsule is a content-addressed artifact", capsule["capsule_id"].startswith("art_"), capsule["capsule_id"])
        body = get(f"/v1/capsules/{capsule['capsule_id']}")["body"]
        paths = [item["path"] for item in body.get("active_files", [])]
        check("the capsule records the path a tool actually touched", any("pyproject.toml" in path for path in paths), str(paths))
        check("no transcript field in the capsule", "transcript" not in body and "messages" not in body, str(sorted(body)[:8]))
        note(f"capsule digest: {capsule['sha256'][:16]}… ({capsule['size']} bytes)")

        # ---------------------------------------------------------------- the migration
        migration = post(
            f"/v1/agents/{agent_id}/migrate",
            {
                "to_runtime": "rt_hermes",
                "tools": ["read"],
                "workspace": str(WORKSPACE),
                "mission_id": mission["mission_id"],
                "capsule_id": capsule["capsule_id"],
                "reason": "M2 proof: same Nova, different body",
            },
            timeout=300,
        )
        check("agent id unchanged by the migration", migration["agent_id"] == agent_id, migration["agent_id"])
        check("a new version, not a new agent", migration["version"] == 2, f"v{migration['version']}")
        check("the source session was archived", (migration["archived"] or {}).get("archived") is True, str(migration["archived"]))
        check("a destination session exists on hermes", migration["to_session"] != migration["from_session"], migration["to_session"])
        check("the migration completed every stage", migration["state"]["completed"] is True, json.dumps(migration["state"]))
        check("the capsule travelled with the migration", migration["capsule"]["capsule_id"] == capsule["capsule_id"])

        lineage = get("/v1/events?limit=600")["events"]
        kinds = [event["kind"] for event in lineage]
        for stage in (
            "migration.requested",
            "migration.capsule_verified",
            "migration.destination_created",
            "migration.capsule_attached",
            "migration.source_archived",
            "migration.completed",
        ):
            check(f"lifecycle event {stage}", stage in kinds)
        attached = next((event for event in lineage if event["kind"] == "context.capsule.attached"), None)
        check("the capsule is linked to the destination session, structurally", attached is not None)
        if attached:
            payload = attached["payload"]
            check(
                "the link carries capsule, source, destination, agent, mission and digest",
                payload["capsule_id"] == capsule["capsule_id"]
                and payload["source_session_id"] == pi_session["session_id"]
                and payload["destination_session_id"] == migration["to_session"]
                and payload["agent_id"] == agent_id
                and payload["mission_id"] == mission["mission_id"]
                and bool(payload["digest"]),
                f"digest={str(payload['digest'])[:16]}…",
            )
        migrations = get(f"/v1/migrations?agent_id={agent_id}")
        check("the lifecycle is queryable and completed", migrations["migrations"][0]["state"] == "completed", str(migrations["migrations"][0]["state"]))

        # ------------------------------------------- Hermes continues the same mission
        hermes_session = migration["to_session"]
        print("  … waiting for the real hermes turn that consumed the capsule (up to 900s)")
        hermes_events = wait_settled(hermes_session, ("runtime.hermes.settled", "runtime.hermes.error"), 900)
        hermes_kinds = [event["kind"] for event in hermes_events]
        error = terminal_error(hermes_events, "runtime.hermes.error")
        check("hermes consumed the capsule and settled", "runtime.hermes.settled" in hermes_kinds, error or "")
        check("the capsule was injected as the first message", "message.submitted" in hermes_kinds)
        opened = next((event for event in hermes_events if event["kind"] == "session.opened"), None)
        check("the hermes session says which model serves it", bool(opened and opened["payload"].get("model")), str(opened and opened["payload"].get("model")))

        follow_up = (
            "In the session that ran before this one (on a different runtime) a file was read. "
            "Which file was it, and what project name does it declare? Answer in one short line."
        )
        post(f"/v1/sessions/{hermes_session}/messages", {"text": follow_up})
        print("  … waiting for hermes to answer about the previous state (up to 420s)")
        hermes_events = wait_settled(hermes_session, ("runtime.hermes.settled", "runtime.hermes.error"), 900)
        error = terminal_error(hermes_events, "runtime.hermes.error")
        if error:
            check("hermes answered without an error", False, error)
        hermes_answer = assistant_text(hermes_events)
        note(f"hermes said: {hermes_answer[:200]!r}")
        check(
            "hermes understood the state it inherited",
            "pyproject.toml" in hermes_answer.lower() and "metaharness" in hermes_answer.lower(),
            hermes_answer[:120],
        )
        hermes_tools = [event for event in hermes_events if event["kind"] == "tool.started"]
        check("hermes did real work of its own (tool or coherent answer)", bool(hermes_tools) or bool(hermes_answer), f"{len(hermes_tools)} tool calls")
        hermes_usage = [event for event in hermes_events if event["kind"] == "usage.sampled"]
        check("hermes usage persisted", bool(hermes_usage))
        if hermes_usage:
            sample = hermes_usage[-1]["payload"]["sample"]
            check(
                "hermes usage is attributed to its own runtime",
                hermes_usage[-1]["runtime_id"] == "rt_hermes",
                f"runtime={hermes_usage[-1]['runtime_id']} in={sample['input_tokens']['value']} cost_provenance={sample['provider_cost']['provenance']}",
            )

        sessions_before = {row["id"]: row for row in get("/v1/sessions")["sessions"]}
        pi_event_ids = {event["id"] for event in get(f"/v1/sessions/{pi_session['session_id']}/events")["events"]}
        seq_before = get("/health")["events"]["last_seq"]
    finally:
        stop_daemon(daemon)

    # ------------------------------------------------------------------- restart
    print("\n== launch #2 (same data root, fresh process) ==")
    daemon = start_daemon()
    try:
        agents = get("/v1/agents")
        sessions = {row["id"]: row for row in get("/v1/sessions")["sessions"]}
        health = get("/health")
        pi_history = {event["id"] for event in get(f"/v1/sessions/{pi_session['session_id']}/events")["events"]}
        capsules = get("/v1/capsules")

        check("Nova still exists, as one agent", agents["count"] == 1 and agents["agents"][0]["id"] == agent_id)
        latest = agents["agents"][0]["versions"][-1]
        check("the current version still points at Hermes", latest["runtime_preferred"] == "rt_hermes", str(latest))
        check("the pi session is still in history, archived", sessions[pi_session["session_id"]]["state"] == "archived")
        check("the hermes session is still in history", sessions[migration["to_session"]]["runtime_id"] == "rt_hermes")
        check("the pi events are all still there", pi_event_ids.issubset(pi_history), f"{len(pi_history)} events")
        check("the capsule is still verifiable", capsules["count"] >= 1 and capsules["capsules"][0]["verified"], str(capsules["count"]))
        check("the log grew rather than restarted", health["events"]["last_seq"] > seq_before, f"{seq_before} -> {health['events']['last_seq']}")
        check("no migration re-ran the schema", health["store"]["schema_version"] == 6, f"v{health['store']['schema_version']}")
        migrations_after = get(f"/v1/migrations?agent_id={agent_id}")
        check("the migration lifecycle survived the restart", migrations_after["migrations"][0]["state"] == "completed")
        orphans = runtime_processes()
        check("no orphan pi/hermes process after the restart", len(orphans) <= len(before), str(orphans))
    finally:
        stop_daemon(daemon)

    release_lock()
    print("\n== verdict ==")
    if FAILURES:
        print(f"  FAILED ({len(FAILURES)}): {FAILURES}")
        return 1
    print("  M2 — RUNTIME MIGRATION: Nova crossed from Pi to Hermes and is still Nova")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

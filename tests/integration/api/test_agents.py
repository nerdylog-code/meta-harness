"""WP-019 acceptance: the control-plane API, over the real store and a scripted Pi.

The runtime here is the scripted peer (`Settings.pi_argv`), so the whole path -- API → adapter →
transport → parser → store → projections -- is exercised without a model or a provider. A6 is
the property the Architect asked for by name: close the daemon, open it again, and the mission,
the agent and the session history are still there, because they were events all along.
"""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

from fastapi.testclient import TestClient  # noqa: E402

from metaharness.app import Settings, create_app  # noqa: E402
from metaharness.store.migrations import discover  # noqa: E402
from metaharness_contracts import IdKind, new_id  # noqa: E402

FAKE = REPO_ROOT / "tests" / "fixtures" / "pi_fake_rpc.py"
FAKE_ARGV = [sys.executable, str(FAKE), "--emit-tools"]


def make_settings(data_dir: str, argv: list[str] | None = None) -> Settings:
    return Settings(port=0, data_dir=data_dir, serve_web=False, pi_argv=argv or FAKE_ARGV)


class ApiTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-api-")
        self.root = self._tmp.name
        self.app = create_app(make_settings(self.root))
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._tmp.cleanup()

    def create_mission(self, title: str = "Harness V2") -> str:
        response = self.client.post("/v1/missions", json={"title": title, "objective": "prove M1"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["mission_id"]

    def create_agent(self, name: str = "Nova") -> str:
        response = self.client.post(
            "/v1/agents", json={"display_name": name, "role": "builder", "model_primary": "fake-model"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["agent_id"]

    def open_session(self, agent_id: str, mission_id: str | None = None) -> dict:
        response = self.client.post(
            "/v1/sessions",
            json={"agent_id": agent_id, "mission_id": mission_id, "tools": ["read_file"]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def wait_for_settled(self, session_id: str, timeout_s: float = 25.0) -> list[dict]:
        deadline = time.time() + timeout_s
        events: list[dict] = []
        while time.time() < deadline:
            events = self.client.get(f"/v1/sessions/{session_id}/events").json()["events"]
            if any(event["kind"] == "runtime.pi.settled" for event in events):
                return events
            time.sleep(0.3)
        return events


class DomainApiTest(ApiTestCase):
    def test_a1_mission_and_agent_are_recorded_and_readable(self) -> None:
        mission_id = self.create_mission()
        agent_id = self.create_agent()
        self.assertTrue(mission_id.startswith("mis_"))
        self.assertTrue(agent_id.startswith("agt_"))

        missions = self.client.get("/v1/missions").json()
        self.assertEqual(missions["count"], 1)
        self.assertEqual(missions["missions"][0]["title"], "Harness V2")

        agents = self.client.get("/v1/agents").json()
        self.assertEqual(agents["count"], 1)
        agent = agents["agents"][0]
        self.assertEqual(agent["display_name"], "Nova")
        self.assertEqual(len(agent["versions"]), 1)
        self.assertEqual(agent["versions"][0]["version"], 1)
        self.assertEqual(agent["versions"][0]["runtime_preferred"], "rt_pi")

    def test_a2_a_new_configuration_is_a_version_not_a_second_agent(self) -> None:
        """ADR-0002: moving Nova between runtimes must not create another Nova."""
        agent_id = self.create_agent()
        bus = self.app.state.bus
        bus.publish(
            "agent.version.created",
            {"version": 2, "runtime_preferred": "rt_hermes", "model_primary": "reasoning-premium"},
            method="measured",
            agent_id=agent_id,
        )
        agents = self.client.get("/v1/agents").json()
        self.assertEqual(agents["count"], 1, "one identity")
        versions = agents["agents"][0]["versions"]
        self.assertEqual([row["version"] for row in versions], [1, 2])
        self.assertEqual(versions[1]["runtime_preferred"], "rt_hermes")

    def test_unknown_agent_is_a_404_not_an_empty_session(self) -> None:
        response = self.client.post("/v1/sessions", json={"agent_id": new_id(IdKind.AGENT)})
        self.assertEqual(response.status_code, 404)


class SessionApiTest(ApiTestCase):
    def test_a3_a_session_starts_on_the_real_adapter(self) -> None:
        agent_id = self.create_agent()
        session = self.open_session(agent_id)
        self.assertTrue(session["session_id"].startswith("ses_"))
        self.assertEqual(session["runtime_id"], "rt_pi")
        self.assertEqual(session["detail"]["provider"], "fake-provider")
        self.assertIsInstance(session["pid"], int)

        rows = self.client.get(f"/v1/sessions?agent_id={agent_id}").json()["sessions"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "open")
        self.assertEqual(rows[0]["model"], "fake-model")

    def test_a4_a_message_streams_and_is_persisted(self) -> None:
        agent_id = self.create_agent()
        session = self.open_session(agent_id)
        accepted = self.client.post(
            f"/v1/sessions/{session['session_id']}/messages", json={"text": "say pong"}
        )
        self.assertEqual(accepted.status_code, 200)
        self.assertTrue(accepted.json()["accepted"])

        events = self.wait_for_settled(session["session_id"])
        kinds = [event["kind"] for event in events]
        for expected in (
            "session.opened",
            "message.started",
            "message.completed",
            "tool.started",
            "tool.completed",
            "usage.sampled",
            "runtime.pi.settled",
        ):
            self.assertIn(expected, kinds, kinds)

    def test_a4_the_persisted_usage_keeps_its_provenance(self) -> None:
        agent_id = self.create_agent()
        session = self.open_session(agent_id)
        self.client.post(f"/v1/sessions/{session['session_id']}/messages", json={"text": "hi"})
        events = self.wait_for_settled(session["session_id"])
        usage = [event for event in events if event["kind"] == "usage.sampled"][0]
        sample = usage["payload"]["sample"]
        self.assertEqual(sample["input_tokens"]["provenance"], "provider_reported")
        self.assertEqual(sample["input_tokens"]["value"], 117)
        self.assertEqual(usage["provenance"]["method"], "runtime_reported")

    def test_a5_cancel_records_why(self) -> None:
        agent_id = self.create_agent()
        session = self.open_session(agent_id)
        self.client.post(f"/v1/sessions/{session['session_id']}/messages", json={"text": "hi"})
        self.wait_for_settled(session["session_id"])

        response = self.client.post(f"/v1/sessions/{session['session_id']}/cancel")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("orphans_left", response.json())

        rows = self.client.get("/v1/sessions").json()["sessions"]
        self.assertEqual(rows[0]["state"], "cancelled")
        events = self.client.get(f"/v1/sessions/{session['session_id']}/events").json()["events"]
        self.assertIn("run.interrupted", [event["kind"] for event in events])

    def test_a7_replay_equivalence_holds_with_the_new_projections(self) -> None:
        agent_id = self.create_agent()
        session = self.open_session(agent_id, self.create_mission())
        self.client.post(f"/v1/sessions/{session['session_id']}/messages", json={"text": "hi"})
        self.wait_for_settled(session["session_id"])
        report = self.app.state.store.replay_equivalence()
        self.assertTrue(report.equal, f"live {report.source_digest[:12]} vs replayed {report.target_digest[:12]}")

    def test_a8_capabilities_are_honest_over_http(self) -> None:
        payload = self.client.get("/v1/runtime").json()
        runtime = payload["runtime"]
        self.assertTrue(runtime["available"])
        capabilities = runtime["capabilities"]["capabilities"]
        self.assertTrue(capabilities["session.streaming"]["supported"])
        self.assertFalse(capabilities["approval.native"]["supported"])
        self.assertIn("not consume", capabilities["approval.native"]["note"])


class RestartTest(unittest.TestCase):
    def test_a6_the_mission_agent_and_history_survive_a_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="mh-restart-") as root:
            first = TestClient(create_app(make_settings(root)))
            with first:
                mission_id = first.post("/v1/missions", json={"title": "M1"}).json()["mission_id"]
                agent_id = first.post("/v1/agents", json={"display_name": "Nova"}).json()["agent_id"]
                session = first.post(
                    "/v1/sessions", json={"agent_id": agent_id, "mission_id": mission_id}
                ).json()
                first.post(f"/v1/sessions/{session['session_id']}/messages", json={"text": "hi"})
                deadline = time.time() + 25
                events: list[dict] = []
                while time.time() < deadline:
                    events = first.get(f"/v1/sessions/{session['session_id']}/events").json()["events"]
                    if any(event["kind"] == "runtime.pi.settled" for event in events):
                        break
                    time.sleep(0.3)
                ids_before = {event["id"] for event in events}
                seq_before = first.get("/health").json()["events"]["last_seq"]

            second_app = create_app(make_settings(root))
            second = TestClient(second_app)
            with second:
                missions = second.get("/v1/missions").json()
                agents = second.get("/v1/agents").json()
                sessions = second.get("/v1/sessions").json()
                health = second.get("/health").json()

                self.assertEqual(missions["count"], 1)
                self.assertEqual(missions["missions"][0]["id"], mission_id)
                self.assertEqual(agents["count"], 1)
                self.assertEqual(agents["agents"][0]["id"], agent_id)
                self.assertEqual(sessions["count"], 1)
                self.assertEqual(sessions["sessions"][0]["id"], session["session_id"])

                history = second.get(f"/v1/sessions/{session['session_id']}/events").json()["events"]
                # Append-only: every event the first run recorded is still there. The history
                # may legitimately *grow*, because boot reconciliation appends a corrective
                # event for the run whose process died with the daemon -- that is the feature,
                # not a leak, and asserting an exact count would forbid it.
                self.assertTrue(ids_before.issubset({event["id"] for event in history}))
                kinds_after = [event["kind"] for event in history]
                self.assertIn("message.completed", kinds_after)
                self.assertIn("run.interrupted", kinds_after, "the crash must be recorded")
                reason = [
                    event["payload"].get("reason")
                    for event in history
                    if event["kind"] == "run.interrupted"
                ][0]
                self.assertIn("no live process", str(reason))
                self.assertGreater(health["events"]["last_seq"], seq_before)

                self.assertEqual(health["store"]["schema_version"], len(discover()))
                self.assertTrue(second_app.state.store.verify().ok)


if __name__ == "__main__":
    unittest.main()

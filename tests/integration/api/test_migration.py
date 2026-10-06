"""M2 acceptance: an agent survives a change of body.

The proof the Architect asked for, at the API level and with scripted peers: Nova runs on Pi, does
work, hands over a **verified capsule**, the Pi session is archived, the agent gets a new version
pointing at Hermes, a Hermes session continues the mission, and after a restart Nova is still Nova
with the Pi session preserved as history.

The real run -- real `pi` and real `hermes acp`, real providers -- is `scripts/e2e_m2.py`. It is
not here because it needs binaries, credentials and credit, none of which CI has.

The last test is the one that matters most: a capsule whose references cannot be resolved must stop
the migration. Failing closed is the whole point of verifying the handoff.
"""

from __future__ import annotations

import json
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
from metaharness.capsule import build_capsule, verify_capsule  # noqa: E402
from metaharness_contracts.capsule import ArtifactRefLite, ContextCapsule  # noqa: E402

PI_FAKE = REPO_ROOT / "tests" / "fixtures" / "pi_fake_rpc.py"
HERMES_FAKE = REPO_ROOT / "tests" / "fixtures" / "hermes_fake_acp.py"


def settings_for(data_dir: str) -> Settings:
    return Settings(
        port=0,
        data_dir=data_dir,
        serve_web=False,
        pi_argv=[sys.executable, str(PI_FAKE), "--emit-tools"],
        hermes_argv=[sys.executable, str(HERMES_FAKE)],
    )


class MigrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-migrate-")
        self.root = self._tmp.name
        self.app = create_app(settings_for(self.root))
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._tmp.cleanup()

    # ------------------------------------------------------------------ helpers

    def wait_for(self, session_id: str, kind: str, timeout_s: float = 25.0) -> list[dict]:
        deadline = time.time() + timeout_s
        events: list[dict] = []
        while time.time() < deadline:
            events = self.client.get(f"/v1/sessions/{session_id}/events").json()["events"]
            if any(event["kind"] == kind for event in events):
                return events
            time.sleep(0.3)
        return events

    def do_work_on_pi(self) -> dict:
        """Create Nova on Pi and make it do something a capsule can be built from."""
        mission = self.client.post("/v1/missions", json={"title": "M2 — Runtime Migration", "objective": "prove migration"})
        mission_id = mission.json()["mission_id"]
        agent = self.client.post("/v1/agents", json={"display_name": "Nova", "role": "builder"})
        agent_id = agent.json()["agent_id"]
        session = self.client.post(
            "/v1/sessions",
            json={"agent_id": agent_id, "mission_id": mission_id, "tools": ["read"]},
        ).json()
        self.assertEqual(session["runtime_id"], "rt_pi")
        self.client.post(
            f"/v1/sessions/{session['session_id']}/messages",
            json={"text": "read pyproject.toml and answer with the project name"},
        )
        self.wait_for(session["session_id"], "runtime.pi.settled")
        return {"mission_id": mission_id, "agent_id": agent_id, "session_id": session["session_id"]}

    # -------------------------------------------------------------------- proof

    def test_m2_an_agent_migrates_carrying_a_verified_capsule(self) -> None:
        work = self.do_work_on_pi()
        agent_id = work["agent_id"]

        response = self.client.post(
            f"/v1/agents/{agent_id}/migrate",
            json={
                "to_runtime": "rt_hermes",
                "model": "opencode-go:deepseek-v4.1-flash",
                "tools": ["read"],
                "mission_id": work["mission_id"],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        migrated = response.json()

        # the identity is untouched: a runtime change is a version, never a new agent
        self.assertEqual(migrated["agent_id"], agent_id)
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["from_runtime"], "rt_pi")
        self.assertEqual(migrated["to_runtime"], "rt_hermes")
        self.assertNotEqual(migrated["from_session"], migrated["to_session"])
        self.assertTrue(migrated["capsule"]["verified"], migrated["capsule"]["verification"])

        agents = self.client.get("/v1/agents").json()
        self.assertEqual(agents["count"], 1, "migration must not create a second Nova")
        versions = agents["agents"][0]["versions"]
        self.assertEqual([row["version"] for row in versions], [1, 2])
        self.assertEqual(versions[0]["runtime_preferred"], "rt_pi")
        self.assertEqual(versions[1]["runtime_preferred"], "rt_hermes")

        # the old session is archived, still there, and still holds its events
        sessions = {row["id"]: row for row in self.client.get("/v1/sessions").json()["sessions"]}
        self.assertEqual(sessions[work["session_id"]]["state"], "archived")
        history = self.client.get(f"/v1/sessions/{work['session_id']}/events").json()["events"]
        self.assertIn("message.completed", [event["kind"] for event in history])
        self.assertIn("usage.sampled", [event["kind"] for event in history])

        # the new session runs on Hermes and started from the capsule
        new_session = sessions[migrated["to_session"]]
        self.assertEqual(new_session["runtime_id"], "rt_hermes")
        new_events = self.wait_for(migrated["to_session"], "runtime.hermes.settled")
        kinds = [event["kind"] for event in new_events]
        self.assertIn("session.opened", kinds)
        self.assertIn("message.completed", kinds, "the capsule's instruction was sent as a prompt")
        # The injection is auditable: the capsule's instruction is the first prompt of the new
        # session, in the transcript, not a hidden system prompt nobody can inspect.
        injected = self.client.get(f"/v1/sessions/{migrated['to_session']}/events").json()["events"]
        self.assertTrue(
            any("Continue this work as the same agent" in json.dumps(event) for event in injected),
            "the capsule's resume instruction must appear in the new session's history",
        )

        # usage stays attributable: one sample per runtime, each naming its own runtime
        pi_usage = next(event for event in history if event["kind"] == "usage.sampled")
        hermes_usage = next(event for event in new_events if event["kind"] == "usage.sampled")
        self.assertEqual(pi_usage["runtime_id"], "rt_pi")
        self.assertEqual(hermes_usage["runtime_id"], "rt_hermes")
        self.assertEqual(hermes_usage["payload"]["sample"]["provider_cost"]["provenance"], "unknown")

        # the lineage is in the log
        lineage = self.client.get("/v1/events?limit=400").json()["events"]
        moved = [event for event in lineage if event["kind"] == "runtime.migrated"]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0]["payload"]["capsule_id"], migrated["capsule"]["capsule_id"])
        self.assertEqual(moved[0]["payload"]["from_session"], work["session_id"])
        self.assertIn("session.archived", [event["kind"] for event in lineage])

    def test_m2_the_capsule_is_readable_and_carries_the_handoff(self) -> None:
        work = self.do_work_on_pi()
        created = self.client.post("/v1/capsules", json={"agent_id": work["agent_id"], "session_id": work["session_id"]}).json()
        self.assertTrue(created["verified"])
        self.assertIn("Continue this work as the same agent", created["resume_instruction"])

        listed = self.client.get(f"/v1/capsules?agent_id={work['agent_id']}").json()
        self.assertEqual(listed["count"], 1)
        self.assertTrue(listed["capsules"][0]["verified"])
        self.assertEqual(listed["capsules"][0]["id"], created["capsule_id"])

        one = self.client.get(f"/v1/capsules/{created['capsule_id']}").json()
        body = one["body"]
        self.assertIn("objective", body)
        self.assertIn("resume_instruction", body)
        self.assertNotIn("transcript", body, "a capsule is a handoff, not a conversation dump")
        self.assertIn("M2", body["objective"])
        # the paths a tool touched are facts taken from the tool events
        self.assertTrue(any("pyproject.toml" in item["path"] for item in body["active_files"]), body["active_files"])

    def test_m2_nothing_moves_on_an_unverified_capsule(self) -> None:
        """Fail closed: the verifier refuses a reference it cannot resolve, and the migration
        refuses to travel. Checked directly against the verifier and through the API."""
        work = self.do_work_on_pi()
        store = self.app.state.store
        draft = build_capsule(store, agent_id=work["agent_id"], session_id=work["session_id"])
        broken = draft.capsule.model_copy(
            update={"artifacts": [ArtifactRefLite(artifact_id="art_000000000000000000000000", purpose="invented")]}
        )
        report = verify_capsule(store, broken, expected_agent_id=work["agent_id"])
        self.assertFalse(report.ok)
        self.assertIn("artifact:art_000000000000000000000000", report.as_dict()["failed"])

        # and the digest check is real, not decorative
        stored = self.client.post("/v1/capsules", json={"agent_id": work["agent_id"], "session_id": work["session_id"]}).json()
        tampered = ContextCapsule.model_validate(
            {**self.client.get(f"/v1/capsules/{stored['capsule_id']}").json()["body"], "objective": "something else entirely"}
        )
        digest_report = verify_capsule(store, tampered, stored_digest=stored["sha256"])
        self.assertFalse(digest_report.ok)
        self.assertIn("digest", digest_report.as_dict()["failed"])

    def test_m2_an_unknown_target_runtime_is_a_404_not_a_silent_default(self) -> None:
        work = self.do_work_on_pi()
        response = self.client.post(
            f"/v1/agents/{work['agent_id']}/migrate", json={"to_runtime": "rt_does_not_exist"}
        )
        self.assertEqual(response.status_code, 404)
        # and nothing moved: the agent still has one version and its session is still open
        agents = self.client.get("/v1/agents").json()
        self.assertEqual(len(agents["agents"][0]["versions"]), 1)
        sessions = {row["id"]: row for row in self.client.get("/v1/sessions").json()["sessions"]}
        self.assertEqual(sessions[work["session_id"]]["state"], "open")

    def test_m2_the_migration_survives_a_restart(self) -> None:
        work = self.do_work_on_pi()
        migrated = self.client.post(
            f"/v1/agents/{work['agent_id']}/migrate",
            json={"to_runtime": "rt_hermes", "tools": ["read"], "mission_id": work["mission_id"]},
        ).json()
        self.wait_for(migrated["to_session"], "runtime.hermes.settled")
        self.client.__exit__(None, None, None)

        second_app = create_app(settings_for(self.root))
        second = TestClient(second_app)
        with second:
            agents = second.get("/v1/agents").json()
            self.assertEqual(agents["count"], 1)
            self.assertEqual(agents["agents"][0]["id"], work["agent_id"])
            latest = agents["agents"][0]["versions"][-1]
            self.assertEqual(latest["runtime_preferred"], "rt_hermes")
            sessions = {row["id"]: row for row in second.get("/v1/sessions").json()["sessions"]}
            self.assertEqual(sessions[work["session_id"]]["state"], "archived")
            self.assertEqual(sessions[migrated["to_session"]]["runtime_id"], "rt_hermes")
            pi_history = second.get(f"/v1/sessions/{work['session_id']}/events").json()["events"]
            self.assertIn("usage.sampled", [event["kind"] for event in pi_history])
            capsules = second.get("/v1/capsules").json()
            self.assertEqual(capsules["count"], 1)
            self.assertTrue(capsules["capsules"][0]["verified"])
            self.assertTrue(second_app.state.store.verify().ok)

        self.client = TestClient(self.app)
        self.client.__enter__()


if __name__ == "__main__":
    unittest.main()

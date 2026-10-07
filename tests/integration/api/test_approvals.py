"""Approvals: an authorisation bound to one exact action.

The nine cases the Architect listed, plus the ones the rules imply: asking is not authorisation, a
different action type is a different authorisation, and a proposal may not lower the risk its action
type declares. Every refusal is a 409 with the reason, and every decision is an event.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
for extra in (REPO_ROOT / "apps" / "daemon", REPO_ROOT / "packages" / "contracts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fastapi.testclient import TestClient  # noqa: E402

from metaharness.app import Settings, create_app  # noqa: E402

PAYLOAD = {"branch": "v2/control-plane", "task": "tsk_one"}


class ApprovalTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ap-api-")
        self.client = TestClient(create_app(Settings(port=0, data_dir=self._tmp.name, serve_web=False)))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._tmp.cleanup()

    # ------------------------------------------------------------------ helpers

    def ask(self, **overrides) -> dict:
        body = {
            "action_type": "git.push",
            "action_payload": dict(PAYLOAD),
            "risk_level": "R3",
            "human_summary": "push v2/control-plane",
            "reversibility": "reversible",
            "requested_by": "agt_nova",
        }
        body.update(overrides)
        response = self.client.post("/v1/approvals", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def consume(self, approval_id: str, payload: dict | None = None, action_type: str = "git.push"):
        return self.client.post(
            f"/v1/approvals/{approval_id}/consume",
            json={"action_type": action_type, "action_payload": payload if payload is not None else dict(PAYLOAD)},
        )

    def grant(self, approval_id: str, by: str = "daniel"):
        response = self.client.post(f"/v1/approvals/{approval_id}/grant", json={"by": by})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # ------------------------------------------------------------------ the nine

    def test_b1_the_exact_payload_is_authorised(self) -> None:
        approval = self.ask()
        self.assertEqual(approval["state"], "pending", "asking is not authorisation")
        self.assertTrue(approval["action_payload_hash"].startswith("sha256:"))
        self.grant(approval["id"])
        response = self.consume(approval["id"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["authorised"])
        self.assertEqual(response.json()["approval"]["state"], "consumed")

    def test_b2_a_changed_payload_is_refused(self) -> None:
        approval = self.ask()
        self.grant(approval["id"])
        changed = {**PAYLOAD, "branch": "main"}
        response = self.consume(approval["id"], changed)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("does not cover this action", response.json()["detail"])
        self.assertEqual(self.client.get(f"/v1/approvals/{approval['id']}").json()["state"], "granted")

    def test_b3_an_expired_approval_is_refused(self) -> None:
        approval = self.ask(ttl_s=-1.0)
        response = self.client.post(f"/v1/approvals/{approval['id']}/grant", json={"by": "daniel"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("expired", response.json()["detail"])
        kinds = [event["kind"] for event in self.client.get("/v1/events?limit=200").json()["events"]]
        self.assertIn("approval.expired", kinds, "the expiry is a fact in the log, not a client clock")

    def test_b4_a_denied_approval_authorises_nothing(self) -> None:
        approval = self.ask()
        denied = self.client.post(f"/v1/approvals/{approval['id']}/deny", json={"by": "daniel", "reason": "not yet"})
        self.assertEqual(denied.status_code, 200, denied.text)
        self.assertEqual(denied.json()["state"], "denied")
        response = self.consume(approval["id"])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("denied", response.json()["detail"])

    def test_b5_a_consumed_approval_is_not_reusable(self) -> None:
        approval = self.ask()
        self.grant(approval["id"])
        self.assertEqual(self.consume(approval["id"]).status_code, 200)
        again = self.consume(approval["id"])
        self.assertEqual(again.status_code, 409, again.text)
        self.assertIn("already consumed", again.json()["detail"])

    def test_b6_r4_without_a_named_human_is_refused(self) -> None:
        approval = self.ask(action_type="deploy.production", action_payload={"env": "prod"}, risk_level="R4")
        self.assertTrue(approval["requires_human"])
        nameless = self.client.post(f"/v1/approvals/{approval['id']}/grant", json={"by": ""})
        self.assertEqual(nameless.status_code, 409, nameless.text)
        self.assertIn("human approver", nameless.json()["detail"])
        self.assertEqual(self.client.get(f"/v1/approvals/{approval['id']}").json()["state"], "pending")
        self.grant(approval["id"], by="daniel")
        self.assertEqual(self.consume(approval["id"], {"env": "prod"}, "deploy.production").status_code, 200)

    def test_b7_an_approval_for_one_task_does_not_authorise_another(self) -> None:
        first = self.ask(action_payload={"task": "tsk_one", "artifact": "a.txt"})
        self.grant(first["id"])
        other = self.consume(first["id"], {"task": "tsk_two", "artifact": "a.txt"})
        self.assertEqual(other.status_code, 409, other.text)
        self.assertIn("does not cover", other.json()["detail"])

    def test_b8_a_restart_preserves_the_approval_state(self) -> None:
        pending = self.ask(action_payload={"task": "tsk_pending"})
        granted = self.ask(action_payload={"task": "tsk_granted"})
        self.grant(granted["id"])
        consumed = self.ask(action_payload={"task": "tsk_consumed"})
        self.grant(consumed["id"])
        self.consume(consumed["id"], {"task": "tsk_consumed"})
        denied = self.ask(action_payload={"task": "tsk_denied"})
        self.client.post(f"/v1/approvals/{denied['id']}/deny", json={"by": "daniel"})

        before = {view["id"]: view["state"] for view in self.client.get("/v1/approvals").json()["approvals"]}
        digest_before = self.client.app.state.store.projection_digest()  # type: ignore[attr-defined]

        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(Settings(port=0, data_dir=self._tmp.name, serve_web=False)))
        self.client.__enter__()

        after = {view["id"]: view["state"] for view in self.client.get("/v1/approvals").json()["approvals"]}
        self.assertEqual(after, before, "every state survived the restart")
        self.assertEqual(after[pending["id"]], "pending")
        self.assertEqual(after[granted["id"]], "granted")
        self.assertEqual(after[consumed["id"]], "consumed")
        self.assertEqual(after[denied["id"]], "denied")
        self.assertEqual(
            self.client.app.state.store.projection_digest(),  # type: ignore[attr-defined]
            digest_before,
            "the projection after the restart is the same fold of the same log",
        )

    def test_b9_a_replay_reproduces_the_approval_state(self) -> None:
        """The projection is the fold of the log, so rebuilding it must land on the same rows."""
        approval = self.ask()
        self.grant(approval["id"])
        self.consume(approval["id"])
        store = self.client.app.state.store  # type: ignore[attr-defined]
        digest_before = store.projection_digest()
        rebuilt = store.snapshot()
        self.assertIn("approvals", rebuilt, "the approvals projection is part of the snapshot")
        row = next(item for item in rebuilt["approvals"] if item["id"] == approval["id"])
        self.assertEqual(row["state"], "consumed")
        self.assertEqual(row["consumed_ts"] is not None, True)
        self.assertEqual(store.projection_digest(), digest_before, "replaying the same log gives the same digest")

    # ------------------------------------------------------------------ the rules around them

    def test_c1_asking_is_not_authorisation(self) -> None:
        approval = self.ask()
        response = self.consume(approval["id"])
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("never granted", response.json()["detail"])

    def test_c2_a_different_action_type_is_a_different_authorisation(self) -> None:
        approval = self.ask()
        self.grant(approval["id"])
        response = self.consume(approval["id"], action_type="file.delete")
        self.assertEqual(response.status_code, 409, response.text)

    def test_c3_a_proposal_may_not_lower_the_risk_its_action_declares(self) -> None:
        response = self.client.post(
            "/v1/approvals",
            json={
                "action_type": "deploy.production",
                "action_payload": {},
                "risk_level": "R1",
                "human_summary": "looks harmless",
                "requested_by": "agt_nova",
            },
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("floor", response.json()["detail"])

    def test_c4_the_inbox_shows_what_is_pending(self) -> None:
        first = self.ask(action_payload={"task": "tsk_a"})
        second = self.ask(action_payload={"task": "tsk_b"})
        self.grant(second["id"])
        inbox = self.client.get("/v1/approvals?state=pending").json()
        self.assertEqual([view["id"] for view in inbox["approvals"]], [first["id"]])
        self.assertIn(first["id"], inbox["pending"])
        full = self.client.get("/v1/approvals").json()
        self.assertEqual(full["counts"], {"granted": 1, "pending": 1})

    def test_c5_an_unknown_approval_is_a_404(self) -> None:
        self.assertEqual(self.client.get("/v1/approvals/apr_missing").status_code, 404)

    def test_c6_every_decision_is_an_event(self) -> None:
        approval = self.ask()
        self.grant(approval["id"])
        self.consume(approval["id"])
        kinds = [event["kind"] for event in self.client.get("/v1/events?limit=200").json()["events"]]
        for expected in ("approval.requested", "approval.granted", "approval.consumed"):
            self.assertIn(expected, kinds)

    def test_c7_the_wire_has_no_alias_for_the_approval_fields(self) -> None:
        view = self.ask()
        for alias in ("action", "payload", "hash", "risk"):
            self.assertNotIn(alias, view, f"{alias} is not a canonical approval field")
        for canonical in ("action_type", "action_payload", "action_payload_hash", "risk_level", "human_summary"):
            self.assertIn(canonical, view)


if __name__ == "__main__":
    unittest.main()

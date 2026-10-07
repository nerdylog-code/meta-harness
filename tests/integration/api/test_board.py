"""The Workboard: a view over the canonical graph, tested against a real daemon.

The board owns no state, so what is tested here is composition: that every task lands in the lane its
canonical state implies, that a workspace, a lease, an approval and an artifact count attach to the
task they belong to and to no other, that nothing unknown is rendered as zero, and that a restart and
a replay produce the same board.

The scenario the Architect asked for is `test_a3`, and it is the one that matters: a mission whose
tasks form a diamond, one of them running with a workspace, a lease and an approval, one ready, one
waiting -- and then the same board after the blockers clear and the daemon restarts.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
for extra in (REPO_ROOT / "apps" / "daemon", REPO_ROOT / "packages" / "contracts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from tests.support import authed_client  # noqa: E402
from tests.support import close_clients  # noqa: E402


class BoardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="board-")
        self.data_root = Path(self._tmp.name) / "data root"
        self.data_root.mkdir(parents=True)
        self.client = authed_client(self.data_root)
        self.store = self.client.app.state.store  # type: ignore[attr-defined]
        self.mission = self.client.post(
            "/v1/missions", json={"title": "Diamond", "objective": "board"}
        ).json()["mission_id"]

    def tearDown(self) -> None:
        close_clients()
        self._tmp.cleanup()

    # ------------------------------------------------------------------ helpers

    def task(self, title: str, deps: list[str] | None = None) -> str:
        response = self.client.post(
            f"/v1/missions/{self.mission}/tasks",
            json={"title": title, "dependencies": deps or []},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def agent(self, name: str = "Nova") -> str:
        return self.client.post("/v1/agents", json={"display_name": name}).json()["agent_id"]

    def run_for(self, task_id: str, agent_id: str) -> str:
        """A real run in the log, written the way the runtime writes one."""
        run_id = f"run_{task_id.split('_', 1)[1]}"
        self.store.append(
            self.store.new_event(
                "run.created",
                {"run_id": run_id, "task_id": task_id},
                run_id=run_id,
                task_id=task_id,
                mission_id=self.mission,
                agent_id=agent_id,
            )
        )
        self.store.append(
            self.store.new_event(
                "run.started",
                {"run_id": run_id, "task_id": task_id},
                run_id=run_id,
                task_id=task_id,
                mission_id=self.mission,
                agent_id=agent_id,
            )
        )
        return run_id

    def start(self, task_id: str, run_id: str | None = None) -> Any:
        return self.client.post(f"/v1/tasks/{task_id}/start", json={"run_id": run_id})

    def complete(self, task_id: str, proof: list[str] | None = None, run_id: str | None = None) -> Any:
        return self.client.post(
            f"/v1/tasks/{task_id}/complete",
            json={"proof": proof or [], "artifacts": proof or [], "run_id": run_id},
        )

    def artifact(self, task_id: str, body: bytes = b"evidence") -> str:
        return self.store.put_artifact(
            body, mime="text/plain", task_id=task_id, mission_id=self.mission
        ).id

    def board(self) -> dict:
        response = self.client.get(f"/v1/missions/{self.mission}/board")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def card(self, task_id: str) -> dict:
        for column in self.board()["columns"]:
            for card in column["tasks"]:
                if card["task_id"] == task_id:
                    return card
        raise AssertionError(f"{task_id} is not on the board at all")

    def lane_of(self, task_id: str) -> str:
        return self.card(task_id)["lane"]

    def lanes(self) -> dict[str, list[str]]:
        return {
            column["lane"]: [card["task_id"] for card in column["tasks"]]
            for column in self.board()["columns"]
        }

    # ------------------------------------------------------------------ the scenario

    def test_a3_the_diamond_scenario(self) -> None:
        a = self.task("A")
        b = self.task("B", [a])
        c = self.task("C", [a])
        d = self.task("D", [b, c])

        nova = self.agent()
        self.client.post(f"/v1/tasks/{b}/assign", json={"agent_id": nova})
        # A must finish before B may start: the daemon refuses the other order, which is the point.
        self.assertEqual(self.start(a).status_code, 200)
        self.assertEqual(self.complete(a).status_code, 200)
        run_b = self.run_for(b, nova)
        self.assertEqual(self.start(b, run_b).status_code, 200)
        # B is running with a real run, an artifact and a pending approval.
        self.artifact(b)
        approval = self.client.post(
            "/v1/approvals",
            json={
                "action_type": "file.write",
                "risk_level": "R2",
                "human_summary": "write the acceptance report for B",
                "requested_by": nova,
                "task_id": b,
            },
        )
        self.assertEqual(approval.status_code, 200, approval.text)

        board = self.board()
        self.assertEqual(board["total"], 4)
        lanes = self.lanes()
        self.assertEqual(lanes["DONE"], [a], "A is done")
        self.assertEqual(lanes["RUNNING"], [b], "B is running")
        self.assertEqual(lanes["READY"], [c], "C became ready when A completed")
        self.assertEqual(lanes["WAITING"], [d], "D is not ready: its dependencies are unsatisfied")
        self.assertEqual(lanes["BACKLOG"], [], "and it is not pretending to be ready either")

        card_b = self.card(b)
        self.assertEqual(card_b["state"], "running")
        self.assertEqual(card_b["agent"]["id"], nova, "the assigned agent is on the card")
        self.assertEqual(card_b["run"]["run_id"], run_b)
        self.assertEqual(card_b["run"]["state"], "running", "a real run, read from the log")
        self.assertEqual(card_b["artifacts"]["count"], 1, "B's artifact is counted on B")
        self.assertEqual(card_b["approvals"]["pending"], 1)
        self.assertTrue(card_b["approvals"]["requires_attention"])
        self.assertEqual(card_b["wave"], 1, "B sits one wave past A")

        card_d = self.card(d)
        self.assertEqual(card_d["dependencies"], sorted([b, c]))
        self.assertEqual(card_d["blocked"]["waiting_on"], sorted([b, c]))
        self.assertEqual(card_d["blocked"]["dead"], [])
        self.assertTrue(any("waiting on" in reason for reason in card_d["reasons"]), card_d["reasons"])

        # Waves are structural and deterministic: A, then B and C, then D.
        self.assertEqual(
            [[entry["wave"], entry["tasks"]] for entry in board["waves"]],
            [[0, [a]], [1, sorted([b, c])], [2, [d]]],
        )

        # B completes, C completes -- and D is ready.
        # The run that did the work completes it, and hands over the evidence.
        proof = [self.artifact(b)]
        completed_b = self.complete(b, proof, run_id=run_b)
        self.assertEqual(completed_b.status_code, 200, completed_b.text)
        # C runs and finishes too: only a running task completes, which is the daemon's rule, and a
        # board that showed D as ready before C actually closed would be promising too early.
        run_c = self.run_for(c, nova)
        self.assertEqual(self.start(c, run_c).status_code, 200)
        self.assertEqual(self.complete(c, run_id=run_c).status_code, 200)
        lanes = self.lanes()
        # Membership, not order: a lane is ordered by creation time while ids sort by their own
        # timestamp, and the two can disagree by a millisecond. What the board promises is which
        # tasks are in the lane.
        self.assertEqual(sorted(lanes["DONE"]), sorted([a, b, c]))
        self.assertEqual(lanes["READY"], [d], "D is ready once both blockers are satisfied")
        self.assertEqual(self.card(d)["lane"], "READY")
        self.assertEqual(self.card(d)["reasons"], [], "a ready task has no reason to explain")

        # Restart: the board is composed from the log, so it must read the same.
        before = self.lanes()
        self.client.__exit__(None, None, None)
        self.client = authed_client(self.data_root)
        self.store = self.client.app.state.store  # type: ignore[attr-defined]
        self.assertEqual(self.lanes(), before, "the board after a restart is the same board")

    # ------------------------------------------------------------------ lanes and states

    def test_a1_the_board_shows_every_lane_including_the_terminal_ones(self) -> None:
        lanes = list(self.lanes())
        self.assertEqual(
            lanes,
            ["BACKLOG", "READY", "RUNNING", "WAITING", "REVIEW", "BLOCKED", "DONE", "FAILED", "CANCELLED"],
        )

    def test_a2_a_failed_task_is_visible_and_not_folded_into_done(self) -> None:
        a = self.task("A")
        b = self.task("B", [a])
        self.assertEqual(self.start(a).status_code, 200)
        self.assertEqual(self.client.post(f"/v1/tasks/{a}/fail", json={"reason": "boom"}).status_code, 200)
        lanes = self.lanes()
        self.assertEqual(lanes["FAILED"], [a], "a failed task is not done")
        self.assertEqual(lanes["DONE"], [])
        self.assertEqual(self.card(a)["lane"], "FAILED")
        # And B cannot become ready on a failed dependency: the board says which sentence applies.
        card_b = self.card(b)
        self.assertEqual(card_b["blocked"]["dead"], [a])
        self.assertTrue(any("failed or was cancelled" in reason for reason in card_b["reasons"]))

    def test_a2b_a_cancelled_task_is_visible(self) -> None:
        a = self.task("A")
        self.assertEqual(self.client.post(f"/v1/tasks/{a}/cancel", json={}).status_code, 200)
        self.assertEqual(self.lanes()["CANCELLED"], [a])

    def test_a2c_a_blocked_task_lands_in_blocked(self) -> None:
        a = self.task("A")
        self.assertEqual(self.client.post(f"/v1/tasks/{a}/block", json={"reason": "no answer"}).status_code, 200)
        self.assertEqual(self.lane_of(a), "BLOCKED")
        self.assertEqual(self.card(a)["state"], "blocked")

    # ------------------------------------------------------------------ attachment

    def test_b1_workspace_and_lease_attach_to_the_right_task(self) -> None:
        import subprocess

        repo = Path(self._tmp.name) / "source repo"
        repo.mkdir()
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "T"]):
            subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=False)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, capture_output=True, check=False)

        a, b = self.task("A"), self.task("B")
        self.assertEqual(
            self.client.post(
                f"/v1/tasks/{a}/workspace/allocate",
                json={"repository": str(repo), "base_ref": "main"},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.post(f"/v1/tasks/{a}/workspace/lease/acquire", json={"run_id": "run_a"}).status_code,
            200,
        )
        card_a, card_b = self.card(a), self.card(b)
        self.assertEqual(card_a["workspace"]["locator"], f"workspaces/{a}")
        self.assertEqual(card_a["workspace"]["state"], "ready")
        self.assertEqual(card_a["writer"]["run_id"], "run_a")
        self.assertEqual(card_a["writer"]["generation"], 1)
        self.assertTrue(card_a["writer"]["active"])
        self.assertIsNone(card_b["workspace"], "B has no workspace")
        self.assertIsNone(card_b["writer"], "and no writer lease")
        self.assertFalse(card_a["workspace"]["measured"], "the board reads the record, it does not run git")
        self.assertIn("recorded", card_a["workspace"]["note"])

    def test_b2_an_approval_belongs_to_one_task_only(self) -> None:
        a, b = self.task("A"), self.task("B")
        created = self.client.post(
            "/v1/approvals",
            json={
                "action_type": "file.write",
                "risk_level": "R2",
                "human_summary": "write A's report",
                "requested_by": "Nova",
                "task_id": a,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertEqual(self.card(a)["approvals"]["pending"], 1)
        self.assertEqual(self.card(b)["approvals"]["pending"], 0, "B never inherits A's approval")
        self.assertEqual(self.board()["filters"]["needs_approval"], [a])

    def test_b3_artifacts_are_counted_for_their_own_task(self) -> None:
        a, b = self.task("A"), self.task("B")
        self.artifact(a)
        self.artifact(a, b"more")
        self.artifact(b)
        self.assertEqual(self.card(a)["artifacts"]["count"], 2)
        self.assertEqual(self.card(b)["artifacts"]["count"], 1, "counted by scope, never by proximity")

    def test_b3b_artifact_ids_are_the_tasks_own(self) -> None:
        """The card carries ids the inspector can open -- and they are this task's, never a neighbour's."""
        a, b = self.task("A"), self.task("B")
        first = self.artifact(a)
        second = self.artifact(b)
        self.assertEqual(self.card(a)["artifacts"]["ids"], [first])
        self.assertEqual(self.card(b)["artifacts"]["ids"], [second])
        self.assertNotIn(second, self.card(a)["artifacts"]["ids"])

    def test_b4_unknown_usage_stays_unknown(self) -> None:
        a = self.task("A")
        self.assertIsNone(self.card(a)["usage"], "a task with no measured usage reports nothing, not zero")
        run = self.run_for(a, self.agent())
        self.assertEqual(self.start(a, run).status_code, 200, "the task now points at that run")
        self.assertIsNone(self.card(a)["usage"], "a run with no usage sample is still unknown")
        self.store.append(
            self.store.new_event(
                "usage.sampled",
                {"run_id": run, "sample": {"input_tokens": {"value": 12, "provenance": "provider_reported"}}},
                run_id=run,
                task_id=a,
                mission_id=self.mission,
            )
        )
        usage = self.card(a)["usage"]
        self.assertIsNotNone(usage)
        self.assertEqual(usage["sample"]["input_tokens"]["value"], 12)

    def test_b4b_the_two_enforcement_dimensions_stay_separate(self) -> None:
        """A worktree isolates concurrent writes and must never upgrade filesystem isolation."""
        a = self.task("A")
        enforcement = self.card(a)["enforcement"]
        self.assertEqual(enforcement["write_isolation"], "weak", "no working copy, no write isolation")
        self.assertEqual(enforcement["filesystem_isolation"], "unknown", "and nothing was measured")
        self.assertIn("not a security boundary", enforcement["note"])

        run = self.run_for(a, self.agent())
        self.assertEqual(self.start(a, run).status_code, 200)
        # The run must name the session whose policy was recorded, or there is nothing to read: the
        # card follows the run -> session -> policy chain rather than guessing.
        self.store.append(
            self.store.new_event(
                "run.created",
                {"run_id": run, "task_id": a},
                run_id=run,
                task_id=a,
                mission_id=self.mission,
                session_id="ses_x",
            )
        )
        # A recorded sandbox verdict is what the filesystem dimension reads -- not the worktree.
        self.store.append(
            self.store.new_event(
                "session.policy",
                {
                    "session_id": "ses_x",
                    "evidence": {"provider": "namespace", "filesystem": "strong", "network": "weak"},
                },
                run_id=run,
                task_id=a,
                mission_id=self.mission,
                session_id="ses_x",
            )
        )
        with_session = self.card(a)["enforcement"]
        self.assertEqual(with_session["filesystem_isolation"], "strong", "read from the recorded evidence")
        self.assertEqual(with_session["write_isolation"], "weak", "still no working copy here")
        self.assertNotEqual(with_session["write_isolation"], with_session["filesystem_isolation"])

    def test_b5_an_expired_lease_is_not_an_active_writer(self) -> None:
        import subprocess

        repo = Path(self._tmp.name) / "repo2"
        repo.mkdir()
        for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"], ["config", "user.name", "T"]):
            subprocess.run(["git", *args], cwd=repo, capture_output=True, check=False)
        (repo / "f.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, capture_output=True, check=False)
        subprocess.run(["git", "commit", "-q", "-m", "b"], cwd=repo, capture_output=True, check=False)
        a = self.task("A")
        self.client.post(
            f"/v1/tasks/{a}/workspace/allocate", json={"repository": str(repo), "base_ref": "main"}
        )
        self.client.post(f"/v1/tasks/{a}/workspace/lease/acquire", json={"run_id": "run_a", "ttl_s": -1.0})
        writer = self.card(a)["writer"]
        self.assertFalse(writer["active"], "an expired lease does not read as an active writer")
        self.assertTrue(writer["expired"])
        self.assertEqual(writer["state"], "active", "the recorded state is still what the log says")

    # ------------------------------------------------------------------ durability and degradation

    def test_c1_a_replay_reproduces_the_board(self) -> None:
        a = self.task("A")
        self.run_for(a, self.agent())
        self.artifact(a)
        before = self.lanes()
        digest = self.store.projection_digest()
        self.assertEqual(self.store.projection_digest(), digest, "the same log folds to the same digest")
        self.assertEqual(self.lanes(), before)

    def test_c2_the_board_follows_the_log_after_an_event(self) -> None:
        a = self.task("A")
        self.assertEqual(self.lane_of(a), "READY", "a task with no dependencies is ready at birth")
        self.assertEqual(self.start(a).status_code, 200)
        self.assertEqual(self.lane_of(a), "RUNNING", "a canonical event moves the card, not the UI")
        self.assertEqual(self.complete(a).status_code, 200)
        self.assertEqual(self.lane_of(a), "DONE")

    def test_c3_an_empty_mission_is_an_explicit_empty_board(self) -> None:
        board = self.board()
        self.assertEqual(board["total"], 0)
        self.assertEqual(board["waves"], [])
        self.assertTrue(all(column["count"] == 0 for column in board["columns"]))
        self.assertEqual(board["degraded"], [])

    def test_c4_a_dependency_outside_the_mission_is_reported(self) -> None:
        a = self.task("A")
        b = self.task("B", [a])
        # A dependency the mission does not have: reported, not silently drawn as a wave.
        self.store.append(
            self.store.new_event(
                "task.dependency_added",
                {"task_id": b, "depends_on": "tsk_somewhereelse"},
                task_id=b,
                mission_id=self.mission,
            )
        )
        board = self.board()
        self.assertTrue(any("outside this mission" in note for note in board["degraded"]), board["degraded"])
        self.assertEqual(
            self.card(b)["blocked"]["waiting_on"], sorted([a, "tsk_somewhereelse"]),
            "both the real unsatisfied dependency and the one outside the mission are named",
        )

    def test_c5_a_refused_action_is_a_409_with_a_detail_for_the_operator(self) -> None:
        a = self.task("A")
        refused = self.client.post(
            f"/v1/tasks/{a}/workspace/allocate",
            json={"repository": str(self.data_root), "base_ref": "main"},
        )
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertTrue(refused.json()["detail"], "the operator is told why, not just that it failed")

    def test_c6_the_board_does_not_leak_host_paths(self) -> None:
        a = self.task("A")
        payload = json.dumps(self.card(a))
        self.assertNotIn(str(self.data_root), payload)
        self.assertNotIn(str(Path.home()), payload)


if __name__ == "__main__":
    unittest.main()

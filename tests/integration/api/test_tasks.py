"""The work graph through the API, including the scenario the Architect asked for and the refusals.

The scenario is the point of the milestone: four tasks, A -> B/C -> D, only A ready, completing A
releases B and C, and D becomes ready when both are done. The refusals are the other half: a cycle,
a dangling dependency, starting a blocked task, completing without evidence, and a run from another
task trying to close this one. All of it has to survive a restart with the graph identical.
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


class WorkGraphTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wg-api-")
        self.client = TestClient(create_app(Settings(port=0, data_dir=self._tmp.name, serve_web=False)))
        self.client.__enter__()
        self.mission = self.client.post(
            "/v1/missions", json={"title": "Factory", "objective": "prove the graph"}
        ).json()["mission_id"]

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._tmp.cleanup()

    # ------------------------------------------------------------------ helpers

    def task(self, title: str, deps: list[str] | None = None, **extra) -> str:
        response = self.client.post(
            f"/v1/missions/{self.mission}/tasks",
            json={"title": title, "objective": f"do {title}", "dependencies": deps or [], **extra},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def graph(self) -> dict:
        return self.client.get(f"/v1/missions/{self.mission}/tasks").json()

    def state_of(self, task_id: str) -> str:
        return self.client.get(f"/v1/tasks/{task_id}").json()["state"]

    def diamond(self) -> dict[str, str]:
        a = self.task("A")
        b = self.task("B", [a])
        c = self.task("C", [a])
        d = self.task("D", [b, c])
        return {"a": a, "b": b, "c": c, "d": d}

    def finish(self, task_id: str, proof: list[str] | None = None, run_id: str | None = None) -> dict:
        started = self.client.post(f"/v1/tasks/{task_id}/start", json={"run_id": run_id})
        self.assertEqual(started.status_code, 200, started.text)
        done = self.client.post(
            f"/v1/tasks/{task_id}/complete",
            json={"proof": proof if proof is not None else ["result.txt"], "run_id": run_id},
        )
        self.assertEqual(done.status_code, 200, done.text)
        return done.json()

    # ------------------------------------------------------------------ the scenario

    def test_a1_only_the_task_without_dependencies_is_ready(self) -> None:
        ids = self.diamond()
        graph = self.graph()
        self.assertEqual(graph["ready"], [ids["a"]])
        self.assertEqual(
            {task["title"]: task["state"] for task in graph["tasks"]},
            {"A": "ready", "B": "draft", "C": "draft", "D": "draft"},
        )

    def test_a2_completing_a_releases_its_dependents(self) -> None:
        ids = self.diamond()
        done = self.finish(ids["a"])
        self.assertEqual(sorted(done["released"]), sorted([ids["b"], ids["c"]]))
        graph = self.graph()
        self.assertEqual(sorted(graph["ready"]), sorted([ids["b"], ids["c"]]))
        self.assertEqual(self.state_of(ids["d"]), "draft", "D waits for both B and C")

    def test_a3_d_becomes_ready_when_every_dependency_is_done(self) -> None:
        ids = self.diamond()
        self.finish(ids["a"])
        self.finish(ids["b"])
        self.assertEqual(self.state_of(ids["d"]), "draft", "one of two is not enough")
        self.finish(ids["c"])
        self.assertEqual(self.state_of(ids["d"]), "ready")
        self.assertEqual(self.graph()["ready"], [ids["d"]])

    def test_a4_the_graph_is_the_same_after_a_restart(self) -> None:
        ids = self.diamond()
        self.finish(ids["a"])
        before = self.graph()
        edges_before = {task["id"]: task["dependencies"] for task in before["tasks"]}
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(Settings(port=0, data_dir=self._tmp.name, serve_web=False)))
        self.client.__enter__()
        after = self.graph()
        self.assertEqual(
            {task["id"]: task["dependencies"] for task in after["tasks"]},
            edges_before,
            "dependencies survive the restart",
        )
        self.assertEqual(
            {task["id"]: task["state"] for task in after["tasks"]},
            {task["id"]: task["state"] for task in before["tasks"]},
            "states survive the restart",
        )
        self.assertEqual(after["ready"], before["ready"])
        self.assertEqual(after["order"], before["order"], "the order is deterministic")

    def test_a5_every_transition_is_an_event(self) -> None:
        ids = self.diamond()
        self.finish(ids["a"])
        kinds = [event["kind"] for event in self.client.get("/v1/events?limit=300").json()["events"]]
        for expected in ("task.created", "task.ready", "task.started", "task.completed"):
            self.assertIn(expected, kinds)
        self.assertEqual(kinds.count("task.created"), 4)
        created = [
            event
            for event in self.client.get("/v1/events?limit=300").json()["events"]
            if event["kind"] == "task.created"
        ]
        self.assertTrue(all(event["task_id"] for event in created), "a task event identifies its task")
        self.assertTrue(all(event["mission_id"] == self.mission for event in created))

    def test_a6_a_run_executes_a_task_and_the_task_records_it(self) -> None:
        task = self.task("A")
        self.client.post(f"/v1/tasks/{task}/start", json={"run_id": "run_abc"})
        self.assertEqual(self.client.get(f"/v1/tasks/{task}").json()["run_id"], "run_abc")

    def test_a7_the_whole_scenario_in_one_chain(self) -> None:
        """The Architect's end-to-end scenario, in one test: create, gate, assign, run, evidence, restart.

        It repeats what A1-A6 assert separately on purpose. Those prove each rule; this proves the
        chain a real mission walks, including that the graph after a restart is the same graph.
        """
        agent = self.client.post("/v1/agents", json={"display_name": "Nova"}).json()["agent_id"]
        ids = self.diamond()

        self.assertEqual(self.graph()["ready"], [ids["a"]], "only A is ready")
        self.finish(ids["a"])
        self.assertEqual(sorted(self.graph()["ready"]), sorted([ids["b"], ids["c"]]), "A released B and C")
        self.assertEqual(self.state_of(ids["d"]), "draft", "D waits for both")

        assigned = self.client.post(f"/v1/tasks/{ids['b']}/assign", json={"agent_id": agent})
        self.assertEqual(assigned.status_code, 200, assigned.text)
        started = self.client.post(f"/v1/tasks/{ids['b']}/start", json={"run_id": "run_b"})
        self.assertEqual(started.status_code, 200, started.text)
        completed = self.client.post(
            f"/v1/tasks/{ids['b']}/complete",
            json={"run_id": "run_b", "proof": ["build.log", "artifact://b"], "artifacts": ["artifact://b"]},
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        self.finish(ids["c"])
        self.assertEqual(self.graph()["ready"], [ids["d"]], "D is ready once B and C are done")

        before = self.graph()
        self.client.__exit__(None, None, None)
        self.client = TestClient(create_app(Settings(port=0, data_dir=self._tmp.name, serve_web=False)))
        self.client.__enter__()
        after = self.graph()
        self.assertEqual(
            [(task["id"], task["state"], task["dependencies"], task["run_id"]) for task in after["tasks"]],
            [(task["id"], task["state"], task["dependencies"], task["run_id"]) for task in before["tasks"]],
            "the reconstructed graph is identical, including the run that executed B",
        )
        self.assertEqual(after["ready"], before["ready"])
        task_b = self.client.get(f"/v1/tasks/{ids['b']}").json()
        self.assertIn("build.log", task_b["proof"], "the evidence survived the restart")

    # ------------------------------------------------------------------ refusals

    def test_b1_a_cycle_is_refused(self) -> None:
        ids = self.diamond()
        response = self.client.post(f"/v1/tasks/{ids['a']}/dependencies", json={"depends_on": ids["d"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("cycle", response.json()["detail"])
        self.assertEqual(self.graph()["ready"], [ids["a"]], "the refusal changed nothing")

    def test_b2_a_dependency_that_does_not_exist_is_refused(self) -> None:
        response = self.client.post(
            f"/v1/missions/{self.mission}/tasks",
            json={"title": "X", "objective": "x", "dependencies": ["tsk_missing"]},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("do not exist", response.json()["detail"])

    def test_b3_a_blocked_task_does_not_start(self) -> None:
        ids = self.diamond()
        response = self.client.post(f"/v1/tasks/{ids['b']}/start", json={})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("blocked", response.json()["detail"])
        kinds = [event["kind"] for event in self.client.get("/v1/events?limit=300").json()["events"]]
        self.assertIn("task.blocked", kinds, "the refusal is recorded, not just returned")

    def test_b4_completion_without_the_evidence_the_gate_requires_is_refused(self) -> None:
        task = self.task("Needs proof", requires_artifact=True)
        self.client.post(f"/v1/tasks/{task}/start", json={})
        response = self.client.post(f"/v1/tasks/{task}/complete", json={"proof": []})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("evidence", response.json()["detail"])
        self.assertEqual(self.state_of(task), "running", "a refused completion does not complete")
        accepted = self.client.post(f"/v1/tasks/{task}/complete", json={"proof": ["evidence.txt"]})
        self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_b5_a_run_from_another_task_cannot_complete_this_one(self) -> None:
        first = self.task("First")
        second = self.task("Second")
        self.client.post(f"/v1/tasks/{first}/start", json={"run_id": "run_one"})
        self.client.post(f"/v1/tasks/{second}/start", json={"run_id": "run_two"})
        response = self.client.post(f"/v1/tasks/{second}/complete", json={"run_id": "run_one", "proof": ["x"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("does not belong", response.json()["detail"])
        self.assertEqual(self.state_of(second), "running")

    def test_b6_a_task_cannot_depend_on_itself(self) -> None:
        task = self.task("Solo")
        response = self.client.post(f"/v1/tasks/{task}/dependencies", json={"depends_on": task})
        self.assertEqual(response.status_code, 409, response.text)

    def test_b7_a_terminal_task_does_not_start_again(self) -> None:
        task = self.task("Once")
        self.finish(task)
        response = self.client.post(f"/v1/tasks/{task}/start", json={})
        self.assertEqual(response.status_code, 409, response.text)

    def test_b8_an_unknown_task_or_mission_is_a_404(self) -> None:
        self.assertEqual(self.client.get("/v1/tasks/tsk_missing").status_code, 404)
        self.assertEqual(self.client.post("/v1/missions/mis_missing/tasks", json={"title": "x"}).status_code, 404)

    # ------------------------------------------------------------------ other transitions

    def test_c1_failure_blocks_dependents_instead_of_releasing_them(self) -> None:
        ids = self.diamond()
        self.client.post(f"/v1/tasks/{ids['a']}/start", json={})
        self.client.post(f"/v1/tasks/{ids['a']}/fail", json={"reason": "no"})
        graph = self.graph()
        self.assertEqual(graph["ready"], [])
        self.assertEqual(sorted(graph["blocked"]), sorted([ids["b"], ids["c"]]))
        self.assertNotIn(ids["b"], graph["ready"])

    def test_c2_removing_a_dependency_releases_the_task(self) -> None:
        a = self.task("A")
        b = self.task("B", [a])
        self.assertEqual(self.state_of(b), "draft")
        response = self.client.delete(f"/v1/tasks/{b}/dependencies/{a}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.state_of(b), "ready")
        self.assertEqual(self.client.get(f"/v1/tasks/{b}").json()["dependencies"], [])

    def test_c3_assignment_is_a_property_of_the_task(self) -> None:
        agent = self.client.post("/v1/agents", json={"display_name": "Nova"}).json()["agent_id"]
        task = self.task("Assigned")
        response = self.client.post(f"/v1/tasks/{task}/assign", json={"agent_id": agent})
        self.assertEqual(response.status_code, 200, response.text)
        view = response.json()
        self.assertEqual(view["owner_agent"], agent)
        self.assertIsNone(view["run_id"], "assignment is not execution")
        self.assertEqual(
            self.client.post(f"/v1/tasks/{task}/assign", json={"agent_id": "agt_missing"}).status_code, 404
        )

    def test_c4_cancel_and_review_move_the_task_and_are_recorded(self) -> None:
        task = self.task("Reviewed")
        self.client.post(f"/v1/tasks/{task}/start", json={})
        self.assertEqual(self.client.post(f"/v1/tasks/{task}/review").json()["state"], "review")
        self.assertEqual(self.client.post(f"/v1/tasks/{task}/cancel", json={"reason": "no"}).json()["state"], "cancelled")
        kinds = [event["kind"] for event in self.client.get("/v1/events?limit=300").json()["events"]]
        self.assertIn("task.review_requested", kinds)
        self.assertIn("task.cancelled", kinds)


if __name__ == "__main__":
    unittest.main()

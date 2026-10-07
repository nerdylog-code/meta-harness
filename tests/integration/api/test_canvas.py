"""The Canvas projection: typed nodes, typed edges, and what may be changed through it.

The canvas owns no state. What is tested here is composition and vocabulary: that every canonical
entity becomes exactly one node of the right type, that a node key and an edge key are deterministic,
that a dependency edge points the documented way, that an artifact, approval, workspace or run attaches
only to the task it belongs to, and that the only mutable edge kinds are the ones a daemon command can
actually change.

The Architect's scenario is `test_a3`, and it is the one that matters: two agents, a diamond of tasks,
one running with a run, a workspace, a lease, an artifact and a pending approval -- and then a
mutation through the canvas commands, a refused cycle, and the same semantic canvas after a restart.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
for extra in (REPO_ROOT / "apps" / "daemon", REPO_ROOT / "packages" / "contracts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from tests.support import authed_client  # noqa: E402


class CanvasTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="canvas-")
        self.data_root = Path(self._tmp.name) / "data"
        self.data_root.mkdir(parents=True)
        self.repo = Path(self._tmp.name) / "source repo"
        self.repo.mkdir()
        for args in (
            ["init", "-q", "-b", "main"],
            ["config", "user.email", "t@t"],
            ["config", "user.name", "T"],
        ):
            subprocess.run(["git", *args], cwd=self.repo, capture_output=True, check=False)
        (self.repo / "f.txt").write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=self.repo, capture_output=True, check=False)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=self.repo, capture_output=True, check=False)

        self.client = authed_client(self.data_root)
        self.store = self.client.app.state.store  # type: ignore[attr-defined]
        self.mission = self.client.post(
            "/v1/missions", json={"title": "Canvas", "objective": "semantic graph"}
        ).json()["mission_id"]

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._tmp.cleanup()

    # ------------------------------------------------------------------ helpers

    def task(self, title: str, deps: list[str] | None = None) -> str:
        response = self.client.post(
            f"/v1/missions/{self.mission}/tasks", json={"title": title, "dependencies": deps or []}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def agent(self, name: str) -> str:
        return self.client.post("/v1/agents", json={"display_name": name}).json()["agent_id"]

    def run_for(self, task_id: str, agent_id: str, session_id: str | None = None) -> str:
        run_id = f"run_{task_id.split('_', 1)[1]}"
        self.store.append(
            self.store.new_event(
                "run.created",
                {"run_id": run_id, "task_id": task_id},
                run_id=run_id,
                task_id=task_id,
                mission_id=self.mission,
                agent_id=agent_id,
                session_id=session_id,
            )
        )
        return run_id

    def canvas(self) -> dict:
        response = self.client.get(f"/v1/missions/{self.mission}/canvas")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def node(self, key: str) -> dict:
        for item in self.canvas()["nodes"]:
            if item["key"] == key:
                return item
        raise AssertionError(f"{key} is not on the canvas at all")

    def nodes_of(self, entity_type: str) -> list[dict]:
        return [item for item in self.canvas()["nodes"] if item["entity_type"] == entity_type]

    def edges_of(self, kind: str) -> list[dict]:
        return [item for item in self.canvas()["edges"] if item["kind"] == kind]

    def edges_touching(self, key: str) -> list[dict]:
        return [
            item
            for item in self.canvas()["edges"]
            if item["source"] == key or item["target"] == key
        ]

    def _diamond(self) -> dict[str, str]:
        a = self.task("A")
        b = self.task("B", [a])
        c = self.task("C", [a])
        d = self.task("D", [b, c])
        nova, atlas = self.agent("Nova"), self.agent("Atlas")
        self.client.post(f"/v1/tasks/{b}/assign", json={"agent_id": nova})
        self.client.post(f"/v1/tasks/{c}/assign", json={"agent_id": atlas})
        self.client.post(f"/v1/tasks/{a}/start", json={"run_id": None})
        self.client.post(f"/v1/tasks/{a}/complete", json={"proof": [], "artifacts": []})
        run_b = self.run_for(b, nova)
        self.client.post(f"/v1/tasks/{b}/start", json={"run_id": run_b})
        self.client.post(
            f"/v1/tasks/{b}/workspace/allocate",
            json={"repository": str(self.repo), "base_ref": "main"},
        )
        self.client.post(f"/v1/tasks/{b}/workspace/lease/acquire", json={"run_id": run_b})
        artifact = self.store.put_artifact(
            b"evidence", mime="text/plain", task_id=b, mission_id=self.mission
        ).id
        approval = self.client.post(
            "/v1/approvals",
            json={
                "action_type": "file.write",
                "risk_level": "R3",
                "human_summary": "ship the canvas",
                "requested_by": nova,
                "task_id": b,
            },
        ).json()["id"]
        return {"a": a, "b": b, "c": c, "d": d, "nova": nova, "atlas": atlas, "run": run_b,
                "artifact": artifact, "approval": approval}

    # ------------------------------------------------------------------ the scenario

    def test_a3_the_canvas_scenario(self) -> None:
        ids = self._diamond()
        canvas = self.canvas()

        # Every entity is a node of the right type, and nothing is missing.
        self.assertEqual(canvas["counts"]["by_type"]["task"], 4)
        self.assertEqual(canvas["counts"]["by_type"]["agent"], 2)
        self.assertEqual(canvas["counts"]["by_type"]["run"], 1)
        self.assertEqual(canvas["counts"]["by_type"]["workspace"], 1)
        self.assertEqual(canvas["counts"]["by_type"]["approval"], 1)
        self.assertEqual(canvas["counts"]["by_type"]["artifact"], 1)
        self.assertEqual(canvas["degraded"], [])

        # Assignment: Nova -> B and Atlas -> C, scoped to the right task.
        assigned = {(e["source"], e["target"]) for e in self.edges_of("assigned_to")}
        self.assertEqual(
            assigned,
            {
                (f"agent:{ids['nova']}", f"task:{ids['b']}"),
                (f"agent:{ids['atlas']}", f"task:{ids['c']}"),
            },
        )

        # Dependency direction, documented: dependency -> dependent.
        depends = {(e["source"], e["target"]) for e in self.edges_of("depends_on")}
        self.assertEqual(
            depends,
            {
                (f"task:{ids['a']}", f"task:{ids['b']}"),
                (f"task:{ids['a']}", f"task:{ids['c']}"),
                (f"task:{ids['b']}", f"task:{ids['d']}"),
                (f"task:{ids['c']}", f"task:{ids['d']}"),
            },
        )

        # The operational edges of the running task.
        self.assertEqual(
            [(e["source"], e["target"]) for e in self.edges_of("executed_by")],
            [(f"task:{ids['b']}", f"run:{ids['run']}")],
        )
        self.assertEqual(
            [(e["source"], e["target"]) for e in self.edges_of("runs_in")],
            [(f"task:{ids['b']}", f"workspace:{ids['b']}")],
        )
        self.assertEqual(
            [(e["source"], e["target"]) for e in self.edges_of("writer_lease")],
            [(f"workspace:{ids['b']}", f"run:{ids['run']}")],
        )
        self.assertEqual(
            [(e["source"], e["target"]) for e in self.edges_of("produces")],
            [(f"task:{ids['b']}", f"artifact:{ids['artifact']}")],
        )
        self.assertEqual(
            [(e["source"], e["target"]) for e in self.edges_of("requires_approval")],
            [(f"task:{ids['b']}", f"approval:{ids['approval']}")],
        )

        # Waves come from the Work Graph's own rule, not a second layout algorithm.
        self.assertEqual([[w["wave"], len(w["tasks"])] for w in canvas["waves"]], [[0, 1], [1, 2], [2, 1]])

        # The task node carries real state, and the workspace keeps its dimensions apart.
        task_b = self.node(f"task:{ids['b']}")
        self.assertEqual(task_b["state"], "running")
        self.assertEqual(task_b["metadata"]["approvals_pending"], 1)
        self.assertEqual(task_b["metadata"]["artifacts"], 1)
        workspace = self.node(f"workspace:{ids['b']}")
        enforcement = workspace["metadata"]["enforcement"]
        self.assertEqual(enforcement["write_isolation"], "moderate")
        self.assertNotEqual(enforcement["filesystem_isolation"], "moderate")
        self.assertIn("not a security boundary", enforcement["note"])

        # And a mutation through the canvas commands is reflected canonically.
        e = self.task("E")
        added = self.client.post(f"/v1/tasks/{e}/dependencies", json={"depends_on": ids["d"]})
        self.assertEqual(added.status_code, 200, added.text)
        self.assertIn(
            (f"task:{ids['d']}", f"task:{e}"), {(x["source"], x["target"]) for x in self.edges_of("depends_on")}
        )

        # A cycle is refused by the daemon, so no edge appears.
        refused = self.client.post(f"/v1/tasks/{ids['a']}/dependencies", json={"depends_on": e})
        self.assertEqual(refused.status_code, 409, refused.text)
        self.assertNotIn(
            (f"task:{e}", f"task:{ids['a']}"), {(x["source"], x["target"]) for x in self.edges_of("depends_on")}
        )

        # Restart: the canvas is composed from the log, so it reads the same.
        before = sorted(item["key"] for item in self.canvas()["nodes"])
        self.client.__exit__(None, None, None)
        self.client = authed_client(self.data_root)
        self.store = self.client.app.state.store  # type: ignore[attr-defined]
        self.assertEqual(sorted(item["key"] for item in self.canvas()["nodes"]), before)

    # ------------------------------------------------------------------ vocabulary and keys

    def test_b1_keys_are_deterministic_and_namespaced_by_type(self) -> None:
        ids = self._diamond()
        first = [(n["key"], n["entity_type"]) for n in self.canvas()["nodes"]]
        second = [(n["key"], n["entity_type"]) for n in self.canvas()["nodes"]]
        self.assertEqual(first, second, "the same state always produces the same keys")
        for key, entity_type in first:
            self.assertTrue(key.startswith(f"{entity_type}:"), key)
        self.assertIn(f"workspace:{ids['b']}", dict(first), "a workspace is keyed by its task, not by a new id")

    def test_b2_no_duplicate_semantic_node(self) -> None:
        self._diamond()
        keys = [item["key"] for item in self.canvas()["nodes"]]
        self.assertEqual(len(keys), len(set(keys)), "one entity, one node")

    def test_b3_edge_keys_are_deterministic(self) -> None:
        self._diamond()
        keys = [item["key"] for item in self.canvas()["edges"]]
        self.assertEqual(len(keys), len(set(keys)))
        for item in self.canvas()["edges"]:
            self.assertEqual(item["key"], f"{item['kind']}:{item['source']}->{item['target']}")

    def test_b4_only_commands_that_exist_are_mutable(self) -> None:
        self._diamond()
        for item in self.canvas()["edges"]:
            if item["kind"] in {"depends_on", "assigned_to"}:
                self.assertTrue(item["mutable"], f"{item['kind']} is changeable through a daemon command")
            else:
                self.assertFalse(item["mutable"], f"{item['kind']} has no canonical command and must be read-only")

    # ------------------------------------------------------------------ scoping

    def test_c1_an_artifact_approval_workspace_and_run_attach_only_to_their_task(self) -> None:
        ids = self._diamond()
        other = self.task("Other")
        for kind, key in (
            ("produces", f"artifact:{ids['artifact']}"),
            ("requires_approval", f"approval:{ids['approval']}"),
            ("runs_in", f"workspace:{ids['b']}"),
            ("executed_by", f"run:{ids['run']}"),
        ):
            owners = {item["source"] for item in self.edges_of(kind) if item["target"] == key}
            self.assertEqual(owners, {f"task:{ids['b']}"}, f"{kind} belongs to B and to nothing else")
            self.assertNotIn(f"task:{other}", owners)

    def test_c2_a_second_tasks_workspace_is_its_own_node(self) -> None:
        a, b = self.task("A"), self.task("B")
        for task_id in (a, b):
            self.client.post(
                f"/v1/tasks/{task_id}/workspace/allocate",
                json={"repository": str(self.repo), "base_ref": "main"},
            )
        self.assertEqual({n["entity_id"] for n in self.nodes_of("workspace")}, {a, b})
        self.assertEqual(self.node(f"workspace:{a}")["metadata"]["locator"], f"workspaces/{a}")
        self.assertEqual(self.node(f"workspace:{b}")["metadata"]["locator"], f"workspaces/{b}")

    def test_c3_a_run_without_a_session_produces_no_session_node(self) -> None:
        a = self.task("A")
        self.run_for(a, self.agent("Nova"))
        self.assertEqual(self.nodes_of("session"), [], "no lineage, no node -- never a fabricated one")
        self.assertEqual(self.edges_of("session"), [])

    def test_c4_a_real_session_lineage_becomes_a_node_and_an_edge(self) -> None:
        a = self.task("A")
        self.store.append(
            self.store.new_event(
                "session.opened",
                {"session_id": "ses_canvas", "runtime_id": "pi", "provider": "opencode-go", "model": "kimi-k3"},
                session_id="ses_canvas",
                task_id=a,
                mission_id=self.mission,
            )
        )
        run = self.run_for(a, self.agent("Nova"), session_id="ses_canvas")
        # The task must point at the run for the lineage to be real: a run the task never started is
        # not a lineage, and the canvas draws only what the log says.
        self.assertEqual(self.client.post(f"/v1/tasks/{a}/start", json={"run_id": run}).status_code, 200)
        sessions = self.nodes_of("session")
        self.assertEqual([n["entity_id"] for n in sessions], ["ses_canvas"])
        self.assertEqual(
            [(e["source"], e["target"]) for e in self.edges_of("session")],
            [(f"run:{run}", "session:ses_canvas")],
        )

    def test_c5_an_unscoped_approval_or_artifact_is_reported_as_degraded(self) -> None:
        self.task("A")
        self.store.put_artifact(b"orphan", mime="text/plain")
        self.client.post(
            "/v1/approvals",
            json={
                "action_type": "file.write",
                "risk_level": "R2",
                "human_summary": "no task named",
                "requested_by": "Nova",
            },
        )
        degraded = self.canvas()["degraded"]
        self.assertTrue(any("no recorded task scope" in note for note in degraded), degraded)

    def test_c6_a_dependency_outside_the_mission_is_reported(self) -> None:
        a = self.task("A")
        self.store.append(
            self.store.new_event(
                "task.dependency_added",
                {"task_id": a, "depends_on": "tsk_elsewhere"},
                task_id=a,
                mission_id=self.mission,
            )
        )
        canvas = self.canvas()
        self.assertTrue(any("outside this mission" in note for note in canvas["degraded"]))
        self.assertEqual(self.node(f"task:{a}")["metadata"]["waiting_on"], ["tsk_elsewhere"])

    def test_c7_the_canvas_never_leaks_a_host_path(self) -> None:
        self._diamond()
        import json

        payload = json.dumps(self.canvas())
        self.assertNotIn(str(self.data_root), payload)
        self.assertNotIn(str(Path.home()), payload)

    # ------------------------------------------------------------------ mutation

    def test_d1_a_valid_dependency_succeeds_and_a_cycle_is_refused(self) -> None:
        a = self.task("A")
        b = self.task("B", [a])
        c = self.task("C")
        ok = self.client.post(f"/v1/tasks/{c}/dependencies", json={"depends_on": b})
        self.assertEqual(ok.status_code, 200, ok.text)
        cycle = self.client.post(f"/v1/tasks/{a}/dependencies", json={"depends_on": c})
        self.assertEqual(cycle.status_code, 409, cycle.text)
        self.assertIn("cycle", cycle.json()["detail"].lower())
        self.assertNotIn(
            (f"task:{c}", f"task:{a}"), {(e["source"], e["target"]) for e in self.edges_of("depends_on")}
        )

    def test_d2_a_dangling_dependency_is_refused(self) -> None:
        a = self.task("A")
        response = self.client.post(f"/v1/tasks/{a}/dependencies", json={"depends_on": "tsk_missing"})
        # The Architect's §15 asks for exactly this: a dangling dependency is a 409 refusal.
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("do not exist", response.json()["detail"])
        self.assertEqual(self.edges_of("depends_on"), [])

    def test_d3_a_valid_assignment_succeeds_and_an_unknown_agent_is_refused(self) -> None:
        a = self.task("A")
        nova = self.agent("Nova")
        ok = self.client.post(f"/v1/tasks/{a}/assign", json={"agent_id": nova})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertIn(f"agent:{nova}", {e["source"] for e in self.edges_of("assigned_to")})
        refused = self.client.post(f"/v1/tasks/{a}/assign", json={"agent_id": "agt_missing"})
        self.assertEqual(refused.status_code, 404, refused.text)
        self.assertEqual(
            [e["source"] for e in self.edges_of("assigned_to")], [f"agent:{nova}"],
            "a refused assignment changes nothing",
        )

    def test_d4_removing_a_dependency_removes_the_edge(self) -> None:
        a = self.task("A")
        b = self.task("B", [a])
        self.assertEqual(len(self.edges_of("depends_on")), 1)
        removed = self.client.delete(f"/v1/tasks/{b}/dependencies/{a}")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(self.edges_of("depends_on"), [], "the edge follows the canonical graph")

    # ------------------------------------------------------------------ durability

    def test_e1_a_replay_reproduces_the_semantic_canvas(self) -> None:
        self._diamond()
        before = [(n["key"], n["state"]) for n in self.canvas()["nodes"]]
        digest = self.store.projection_digest()
        self.assertEqual(self.store.projection_digest(), digest)
        self.assertEqual([(n["key"], n["state"]) for n in self.canvas()["nodes"]], before)

    def test_e2_the_canvas_does_not_measure_on_render(self) -> None:
        """Drawing a graph must not hash artifacts, run git status or probe a runtime."""
        ids = self._diamond()
        artifact = self.node(f"artifact:{ids['artifact']}")
        self.assertEqual(artifact["state"], "unchecked", "integrity is not measured to draw a node")
        self.assertIn("does not hash", artifact["metadata"]["integrity_note"])
        workspace = self.node(f"workspace:{ids['b']}")
        self.assertFalse(workspace["metadata"]["measured"], "the recorded state is what is drawn")


if __name__ == "__main__":
    unittest.main()

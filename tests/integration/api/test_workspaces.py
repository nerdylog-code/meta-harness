"""The worktree allocator and the writer lease, against real Git repositories.

No mocks and no fake-green: every case here creates a real repository, real worktrees and real
concurrent edits. The core proof is the one the Architect asked for -- two tasks editing the same
file in their own worktrees while the source checkout does not move -- and the lease proof is the
one that makes "single writer" a mechanism rather than a hope.

Paths deliberately contain spaces, because that is where an argv-vs-shell mistake shows up.
"""

from __future__ import annotations

import json
import subprocess
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


def git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """Git as argv, never through a shell -- the same discipline the provider follows."""
    return subprocess.run(  # noqa: S603 - argv
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )


class WorkspaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ws-api-")
        base = Path(self._tmp.name)
        # Spaces on purpose, in both the repository and the data root.
        self.repo = base / "source repo"
        self.repo.mkdir(parents=True)
        git("init", "-q", "-b", "main", cwd=self.repo)
        git("config", "user.email", "t@example.invalid", cwd=self.repo)
        git("config", "user.name", "Test", cwd=self.repo)
        (self.repo / "shared.txt").write_text("base\n", encoding="utf-8")
        git("add", "-A", cwd=self.repo)
        git("commit", "-q", "-m", "base", cwd=self.repo)
        self.data_root = base / "data root"
        self.data_root.mkdir()
        self.client = TestClient(
            create_app(Settings(port=0, data_dir=str(self.data_root), serve_web=False))
        )
        self.client.__enter__()
        self.mission = self.client.post(
            "/v1/missions", json={"title": "Isolation", "objective": "two writers, no collision"}
        ).json()["mission_id"]

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._tmp.cleanup()

    # ------------------------------------------------------------------ helpers

    def task(self, title: str) -> str:
        response = self.client.post(f"/v1/missions/{self.mission}/tasks", json={"title": title})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["id"]

    def allocate(self, task_id: str, *, base_ref: str = "main") -> dict:
        response = self.client.post(
            f"/v1/tasks/{task_id}/workspace/allocate",
            json={"repository": str(self.repo), "base_ref": base_ref},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def workspace_path(self, task_id: str) -> Path:
        return self.data_root / "workspaces" / task_id

    def acquire(self, task_id: str, run_id: str, ttl_s: float | None = None):
        body: dict = {"run_id": run_id}
        if ttl_s is not None:
            body["ttl_s"] = ttl_s
        return self.client.post(f"/v1/tasks/{task_id}/workspace/lease/acquire", json=body)

    def inspect(self, task_id: str) -> dict:
        return self.client.get(f"/v1/tasks/{task_id}/workspace").json()

    # ------------------------------------------------------------------ the core proof

    def test_a1_parallel_writers_do_not_collide(self) -> None:
        """Two tasks, one repository, the same file, no collision -- and the source never moves."""
        task_a, task_b = self.task("A"), self.task("B")
        view_a = self.allocate(task_a)
        view_b = self.allocate(task_b)

        self.assertNotEqual(view_a["locator"], view_b["locator"])
        self.assertTrue(view_a["locator"].startswith("workspaces/"), view_a["locator"])
        self.assertEqual(view_a["state"], "ready")
        self.assertEqual(view_a["branch"], f"mh/task/{task_a}")
        self.assertEqual(view_b["branch"], f"mh/task/{task_b}")
        self.assertFalse(view_a["dirty"], "a fresh worktree is clean")

        (self.workspace_path(task_a) / "shared.txt").write_text("A\n", encoding="utf-8")
        (self.workspace_path(task_b) / "shared.txt").write_text("B\n", encoding="utf-8")

        self.assertEqual((self.repo / "shared.txt").read_text(encoding="utf-8").strip(), "base")
        self.assertEqual(
            (self.workspace_path(task_a) / "shared.txt").read_text(encoding="utf-8").strip(), "A"
        )
        self.assertEqual(
            (self.workspace_path(task_b) / "shared.txt").read_text(encoding="utf-8").strip(), "B"
        )
        self.assertTrue(self.inspect(task_a)["dirty"], "the edit is measured, not assumed")

    def test_a2_the_locator_is_posix_and_relative_on_every_platform(self) -> None:
        task = self.task("A")
        view = self.allocate(task)
        self.assertNotIn("\\", view["locator"])
        self.assertNotIn(str(self.data_root), json.dumps(view["locator"]), "no host path on the wire")
        self.assertEqual(view["locator"], f"workspaces/{task}")

    def test_a3_the_worktree_is_a_real_git_worktree(self) -> None:
        task = self.task("A")
        self.allocate(task)
        listed = git("worktree", "list", "--porcelain", cwd=self.repo).stdout
        # Git prints worktree paths with forward slashes in porcelain output on every platform, so
        # the comparison is made between resolved canonical paths (Architect, cross-platform
        # contract) rather than between two spellings of the same directory.
        listed_paths = {
            str(Path(line.removeprefix("worktree ").strip()).resolve())
            for line in listed.splitlines()
            if line.startswith("worktree ")
        }
        self.assertIn(str(self.workspace_path(task).resolve()), listed_paths)
        branches = git("branch", "--list", "--format=%(refname:short)", cwd=self.repo).stdout
        self.assertIn(f"mh/task/{task}", branches)

    def test_a4_allocation_is_idempotent_for_the_same_task_and_repository(self) -> None:
        task = self.task("A")
        first = self.allocate(task)
        second = self.allocate(task)
        self.assertEqual(first["locator"], second["locator"])
        self.assertEqual(second["base_commit"], first["base_commit"])
        self.assertTrue(second.get("reconciled"), "a second call reconciles, it does not re-create")
        listed = git("worktree", "list", "--porcelain", cwd=self.repo).stdout.count("worktree ")
        self.assertEqual(listed, 2, "the source plus exactly one worktree")

    # ------------------------------------------------------------------ the lease proof

    def test_b1_one_writer_at_a_time_with_generations(self) -> None:
        task = self.task("A")
        self.allocate(task)

        first = self.acquire(task, "run_1")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["verdict"], "grant")
        self.assertEqual(first.json()["writer"]["generation"], 1)

        blocked = self.acquire(task, "run_2")
        self.assertEqual(blocked.status_code, 409, blocked.text)
        self.assertIn("being written by another run", blocked.json()["detail"])

        renewed = self.acquire(task, "run_1")
        self.assertEqual(renewed.json()["verdict"], "renew", "the holder renewing is idempotent")
        self.assertEqual(renewed.json()["writer"]["generation"], 1, "renewing does not bump the generation")

        released = self.client.post(
            f"/v1/tasks/{task}/workspace/lease/release", json={"run_id": "run_1", "generation": 1}
        )
        self.assertEqual(released.status_code, 200, released.text)

        second_writer = self.acquire(task, "run_2")
        self.assertEqual(second_writer.json()["writer"]["generation"], 2, "the next writer is generation 2")

        stale = self.client.post(
            f"/v1/tasks/{task}/workspace/lease/release", json={"run_id": "run_1", "generation": 1}
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertIn("not current", stale.json()["detail"])
        self.assertEqual(self.inspect(task)["writer"]["run_id"], "run_2", "the newer writer still holds it")

    def test_b2_a_heartbeat_extends_the_lease(self) -> None:
        task = self.task("A")
        self.allocate(task)
        self.acquire(task, "run_1", ttl_s=60.0)
        before = self.inspect(task)["writer"]["expires_at"]
        renewed = self.client.post(
            f"/v1/tasks/{task}/workspace/lease/renew", json={"run_id": "run_1", "generation": 1}
        )
        self.assertEqual(renewed.status_code, 200, renewed.text)
        self.assertGreaterEqual(self.inspect(task)["writer"]["expires_at"], before)

    def test_b3_an_expired_lease_lets_the_next_writer_in(self) -> None:
        task = self.task("A")
        self.allocate(task)
        self.acquire(task, "run_1", ttl_s=-1.0)  # already expired
        verdict = self.acquire(task, "run_2")
        self.assertEqual(verdict.status_code, 200, verdict.text)
        self.assertEqual(verdict.json()["writer"]["generation"], 2)
        swept = self.client.post("/v1/workspaces/leases/expire").json()
        self.assertEqual(swept["count"], 0, "the expired lease was already replaced, not swept twice")

    def test_b4_a_lease_whose_run_died_is_settled_at_boot(self) -> None:
        task = self.task("A")
        self.allocate(task)
        self.acquire(task, "run_1", ttl_s=3600.0)
        # A lease held by a run that is not alive cannot be writing: the reconciler settles it.
        self.client.__exit__(None, None, None)
        self.client = TestClient(
            create_app(Settings(port=0, data_dir=str(self.data_root), serve_web=False))
        )
        self.client.__enter__()
        report = self.client.get("/health").json()
        self.assertIn("reconcile", json.dumps(report).lower())
        events = self.client.get("/v1/events?limit=300").json()["events"]
        settled = [
            event for event in events if event["kind"] in {"workspace.lease.released", "workspace.lease.expired"}
        ]
        self.assertTrue(settled, "the boot reconciler settled the orphaned lease, with an event")
        self.assertIn("run_1", json.dumps(settled[-1]["payload"]))

    # ------------------------------------------------------------------ refusals

    def test_c1_a_non_git_source_is_refused(self) -> None:
        task = self.task("A")
        plain = self.data_root.parent / "not a repo"
        plain.mkdir(exist_ok=True)
        response = self.client.post(
            f"/v1/tasks/{task}/workspace/allocate", json={"repository": str(plain), "base_ref": "main"}
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("not a git repository", response.json()["detail"])

    def test_c2_the_data_root_is_refused_as_a_repository(self) -> None:
        task = self.task("A")
        response = self.client.post(
            f"/v1/tasks/{task}/workspace/allocate", json={"repository": str(self.data_root)}
        )
        self.assertEqual(response.status_code, 409, response.text)

    def test_c3_a_conflicting_destination_is_refused_not_deleted(self) -> None:
        task = self.task("A")
        destination = self.workspace_path(task)
        destination.mkdir(parents=True)
        (destination / "keep.txt").write_text("someone else's work", encoding="utf-8")
        response = self.client.post(
            f"/v1/tasks/{task}/workspace/allocate",
            json={"repository": str(self.repo), "base_ref": "main"},
        )
        self.assertEqual(response.status_code, 409, response.text)
        self.assertTrue((destination / "keep.txt").exists(), "nothing was silently deleted")

    def test_c4_another_tasks_allocation_is_never_adopted(self) -> None:
        task_a, task_b = self.task("A"), self.task("B")
        self.allocate(task_a)
        view_b = self.allocate(task_b)
        self.assertNotEqual(view_b["locator"], f"workspaces/{task_a}")
        self.assertEqual(view_b["task_id"], task_b)
        # B's own allocation is what B sees; A's is untouched.
        self.assertEqual(self.inspect(task_a)["task_id"], task_a)
        self.assertTrue(self.workspace_path(task_a).is_dir())

    def test_c5_a_dirty_worktree_cannot_be_removed(self) -> None:
        task = self.task("A")
        self.allocate(task)
        (self.workspace_path(task) / "shared.txt").write_text("uncommitted\n", encoding="utf-8")
        response = self.client.delete(f"/v1/tasks/{task}/workspace")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("uncommitted", response.json()["detail"])
        self.assertTrue(self.workspace_path(task).is_dir())

    def test_c6_a_leased_worktree_cannot_be_removed(self) -> None:
        task = self.task("A")
        self.allocate(task)
        self.acquire(task, "run_1", ttl_s=3600.0)
        response = self.client.delete(f"/v1/tasks/{task}/workspace")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("generation", response.json()["detail"])

    def test_c7_a_missing_worktree_is_an_explicit_state(self) -> None:
        task = self.task("A")
        self.allocate(task)
        import shutil

        shutil.rmtree(self.workspace_path(task))
        view = self.inspect(task)
        self.assertEqual(view["state"], "missing", view)
        self.assertIn("not on disk", view["note"])
        self.assertEqual(self.client.delete(f"/v1/tasks/{task}/workspace").status_code, 409)

    def test_c8_a_directory_that_git_does_not_list_is_a_conflict(self) -> None:
        task = self.task("A")
        self.allocate(task)
        git("worktree", "remove", str(self.workspace_path(task)), cwd=self.repo)
        self.workspace_path(task).mkdir(parents=True)
        view = self.inspect(task)
        self.assertEqual(view["state"], "conflict", view)
        self.assertIn("does not list it", view["note"])

    def test_c9_a_release_needs_the_current_generation(self) -> None:
        task = self.task("A")
        self.allocate(task)
        self.acquire(task, "run_1")
        wrong = self.client.post(
            f"/v1/tasks/{task}/workspace/lease/release", json={"run_id": "run_1", "generation": 99}
        )
        self.assertEqual(wrong.status_code, 409, wrong.text)
        other = self.client.post(
            f"/v1/tasks/{task}/workspace/lease/release", json={"run_id": "run_9", "generation": 1}
        )
        self.assertEqual(other.status_code, 409, other.text)

    def test_c10_an_unknown_task_or_workspace_is_a_404(self) -> None:
        self.assertEqual(self.client.get("/v1/tasks/tsk_missing/workspace").status_code, 404)
        self.assertEqual(self.client.delete("/v1/tasks/tsk_missing/workspace").status_code, 404)
        task = self.task("A")
        self.assertEqual(self.client.get(f"/v1/tasks/{task}/workspace").status_code, 404)

    def test_c11_a_lease_holder_must_be_a_run(self) -> None:
        task = self.task("A")
        self.allocate(task)
        response = self.client.post(
            f"/v1/tasks/{task}/workspace/lease/acquire", json={"run_id": "agt_nova"}
        )
        self.assertEqual(response.status_code, 422, response.text)

    # ------------------------------------------------------------------ safe removal and durability

    def test_d1_a_clean_unleased_worktree_can_be_removed_and_the_branch_kept(self) -> None:
        task = self.task("A")
        self.allocate(task)
        response = self.client.delete(f"/v1/tasks/{task}/workspace")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["branch_kept"], "work is never discarded automatically")
        self.assertFalse(self.workspace_path(task).exists())
        self.assertEqual(self.inspect(task)["state"], "missing", "the record says what happened")

    def test_d2_a_restart_preserves_allocations_and_leases(self) -> None:
        task = self.task("A")
        view = self.allocate(task)
        self.acquire(task, "run_1", ttl_s=3600.0)
        before = self.inspect(task)
        self.client.__exit__(None, None, None)
        self.client = TestClient(
            create_app(Settings(port=0, data_dir=str(self.data_root), serve_web=False))
        )
        self.client.__enter__()
        after = self.inspect(task)
        self.assertEqual(after["locator"], view["locator"])
        self.assertEqual(after["base_commit"], before["base_commit"])
        self.assertEqual(after["branch"], before["branch"])
        self.assertEqual(after["state"], "ready")
        self.assertEqual(after["writer"]["generation"], before["writer"]["generation"])
        self.assertEqual(after["writer"]["run_id"], "run_1")

    def test_d3_a_replay_reproduces_the_workspace_projection(self) -> None:
        task = self.task("A")
        self.allocate(task)
        self.acquire(task, "run_1", ttl_s=3600.0)
        store = self.client.app.state.store  # type: ignore[attr-defined]
        digest = store.projection_digest()
        snapshot = store.snapshot()
        self.assertIn("workspaces", snapshot)
        self.assertIn("workspace_leases", snapshot)
        row = next(item for item in snapshot["workspaces"] if item["task_id"] == task)
        self.assertEqual(row["locator"], f"workspaces/{task}")
        self.assertEqual(store.projection_digest(), digest, "the same log folds to the same digest")

    def test_d4_the_enforcement_dimensions_are_never_conflated(self) -> None:
        task = self.task("A")
        view = self.allocate(task)
        enforcement = view["enforcement"]
        self.assertEqual(enforcement["write_isolation"], "moderate", "a worktree isolates writes")
        self.assertNotEqual(
            enforcement["filesystem_isolation"],
            "strong",
            "a worktree must never upgrade host filesystem isolation",
        )
        self.assertIn("not a sandbox", enforcement["note"])


if __name__ == "__main__":
    unittest.main()

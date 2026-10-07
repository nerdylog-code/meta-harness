"""The work-graph rules, as pure functions.

These are the rules the API, the projection, the UI and the replay all have to agree on, which is
why they are tested away from all of them. A rule that only holds inside the API is a rule that
drifts.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "packages" / "contracts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages" / "contracts"))

from metaharness_contracts.enums import TaskState  # noqa: E402
from metaharness_contracts.taskgraph import (  # noqa: E402
    blocked_ids,
    find_cycle,
    is_ready,
    missing_dependencies,
    ready_ids,
)


class CycleTest(unittest.TestCase):
    def test_a_dag_has_no_cycle(self) -> None:
        self.assertIsNone(find_cycle({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}))

    def test_a_self_loop_is_a_cycle(self) -> None:
        self.assertEqual(find_cycle({"a": ["a"]}), ["a", "a"])

    def test_a_long_cycle_is_found(self) -> None:
        cycle = find_cycle({"a": ["b"], "b": ["c"], "c": ["a"]}) or []
        self.assertTrue(cycle, "a three-node cycle must be found")
        self.assertEqual(cycle[0], cycle[-1], "a cycle is reported as a closed path")

    def test_the_reported_cycle_is_deterministic(self) -> None:
        graph = {"z": ["y"], "y": ["x"], "x": ["z"], "a": ["b"], "b": ["a"]}
        self.assertEqual(find_cycle(graph), find_cycle(dict(reversed(list(graph.items())))))

    def test_an_edge_to_an_unknown_task_is_not_a_cycle(self) -> None:
        # Existence is a different refusal, with a different message.
        self.assertIsNone(find_cycle({"a": ["ghost"]}))

    def test_diamond_dependencies_are_not_a_cycle(self) -> None:
        self.assertIsNone(find_cycle({"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"]}))


class ReadinessTest(unittest.TestCase):
    def test_a_task_with_no_dependencies_is_ready(self) -> None:
        self.assertTrue(is_ready(TaskState.DRAFT, [], {}))

    def test_a_task_waits_for_every_dependency(self) -> None:
        states = {"a": TaskState.DONE, "b": TaskState.RUNNING}
        self.assertFalse(is_ready(TaskState.DRAFT, ["a", "b"], states))
        self.assertTrue(is_ready(TaskState.DRAFT, ["a"], states))

    def test_a_missing_dependency_is_not_satisfied(self) -> None:
        # A dangling edge cannot support "ready", even though it is not a cycle.
        self.assertFalse(is_ready(TaskState.DRAFT, ["ghost"], {}))

    def test_a_finished_task_is_never_ready(self) -> None:
        for state in (TaskState.DONE, TaskState.CANCELLED, TaskState.FAILED):
            self.assertFalse(is_ready(state, [], {}))

    def test_only_done_satisfies_a_dependent(self) -> None:
        for state in (TaskState.DRAFT, TaskState.READY, TaskState.RUNNING, TaskState.REVIEW, TaskState.BLOCKED):
            self.assertFalse(is_ready(TaskState.DRAFT, ["a"], {"a": state}))
        self.assertTrue(is_ready(TaskState.DRAFT, ["a"], {"a": TaskState.DONE}))

    def test_ready_ids_is_ordered_and_excludes_running(self) -> None:
        states = {
            "a": TaskState.DONE,
            "b": TaskState.DRAFT,
            "c": TaskState.DRAFT,
            "d": TaskState.DRAFT,
            "e": TaskState.RUNNING,
        }
        edges = {"a": [], "b": ["a"], "c": ["a"], "d": ["b", "c"], "e": []}
        self.assertEqual(ready_ids(states, edges), ["b", "c"])

    def test_blocked_ids_reports_what_a_failure_killed(self) -> None:
        states = {"a": TaskState.FAILED, "b": TaskState.DRAFT, "c": TaskState.DONE}
        edges = {"a": [], "b": ["a"], "c": ["a"]}
        self.assertEqual(blocked_ids(states, edges), ["b"])
        self.assertEqual(blocked_ids({"a": TaskState.CANCELLED, "b": TaskState.DRAFT}, edges), ["b"])

    def test_missing_dependencies_names_them(self) -> None:
        self.assertEqual(missing_dependencies("a", ["x", "y"], {"a", "x"}), ["y"])
        self.assertEqual(missing_dependencies("a", ["a"], {"a"}), [], "a self-edge is not 'missing'")


if __name__ == "__main__":
    unittest.main()

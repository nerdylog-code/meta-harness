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
    LANES,
    TERMINAL_LANES,
    TaskGraphError,
    board_lane,
    dependency_blockers,
    effective_lane,
    waves,
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


class BoardPlacementTest(unittest.TestCase):
    """Where a task lands on a board, and why that is not a matter of opinion."""

    def test_every_canonical_state_has_a_lane(self) -> None:
        """Totality: a new task state cannot silently fall off the board."""
        for state in TaskState:
            lane = board_lane(state)
            self.assertIn(lane, [*LANES, *TERMINAL_LANES], f"{state} maps outside the board")

    def test_the_mapping_is_the_documented_one(self) -> None:
        self.assertEqual(board_lane(TaskState.DRAFT), "BACKLOG")
        self.assertEqual(board_lane(TaskState.READY), "READY")
        self.assertEqual(board_lane(TaskState.CLAIMED), "READY", "the claim is already visible on the card")
        self.assertEqual(board_lane(TaskState.RUNNING), "RUNNING")
        self.assertEqual(board_lane(TaskState.WAITING), "WAITING")
        self.assertEqual(board_lane(TaskState.REVIEW), "REVIEW")
        self.assertEqual(board_lane(TaskState.BLOCKED), "BLOCKED")
        self.assertEqual(board_lane(TaskState.DONE), "DONE")
        self.assertEqual(board_lane(TaskState.FAILED), "FAILED")
        self.assertEqual(board_lane(TaskState.CANCELLED), "CANCELLED")

    def test_failed_and_cancelled_are_never_folded_into_done(self) -> None:
        self.assertNotEqual(board_lane(TaskState.FAILED), "DONE")
        self.assertNotEqual(board_lane(TaskState.CANCELLED), "DONE")
        self.assertIn("FAILED", TERMINAL_LANES)
        self.assertIn("CANCELLED", TERMINAL_LANES)

    def test_a_ready_task_with_unsatisfied_dependencies_is_not_ready(self) -> None:
        """The board must not offer work the daemon would refuse to start."""
        states = {"A": TaskState.RUNNING, "B": TaskState.READY}
        deps = {"A": [], "B": ["A"]}
        self.assertEqual(effective_lane("B", states["B"], states, deps), "WAITING")

    def test_a_ready_task_whose_dependency_failed_is_blocked(self) -> None:
        states = {"A": TaskState.FAILED, "B": TaskState.READY}
        deps = {"A": [], "B": ["A"]}
        self.assertEqual(effective_lane("B", states["B"], states, deps), "BLOCKED")

    def test_dependencies_satisfied_means_ready(self) -> None:
        states = {"A": TaskState.DONE, "B": TaskState.READY}
        deps = {"A": [], "B": ["A"]}
        self.assertEqual(effective_lane("B", states["B"], states, deps), "READY")

    def test_the_fold_does_not_move_a_task_that_is_already_running(self) -> None:
        states = {"A": TaskState.DRAFT, "B": TaskState.RUNNING}
        deps = {"A": [], "B": ["A"]}
        self.assertEqual(effective_lane("B", states["B"], states, deps), "RUNNING")


class WavesTest(unittest.TestCase):
    def test_the_diamond_lays_out_in_three_waves(self) -> None:
        deps = {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]}
        self.assertEqual(waves(deps), [["A"], ["B", "C"], ["D"]])

    def test_waves_are_deterministic_regardless_of_insertion_order(self) -> None:
        one = {"A": [], "B": ["A"], "C": ["A"], "D": ["B", "C"]}
        two = {"D": ["B", "C"], "C": ["A"], "B": ["A"], "A": []}
        self.assertEqual(waves(one), waves(two))

    def test_a_deeper_chain_keeps_its_depth(self) -> None:
        deps = {"A": [], "B": ["A"], "C": ["B"], "D": ["C"]}
        self.assertEqual(waves(deps), [["A"], ["B"], ["C"], ["D"]])

    def test_a_dependency_outside_the_graph_is_not_a_wave(self) -> None:
        deps = {"A": [], "B": ["A", "tsk_elsewhere"]}
        self.assertEqual(waves(deps), [["A"], ["B"]])

    def test_an_empty_graph_has_no_waves(self) -> None:
        self.assertEqual(waves({}), [])

    def test_a_cycle_is_refused_rather_than_laid_out(self) -> None:
        with self.assertRaises(TaskGraphError):
            waves({"A": ["B"], "B": ["A"]})


class DependencyExplanationTest(unittest.TestCase):
    def test_waiting_and_dead_are_different_sentences(self) -> None:
        states = {"B": TaskState.RUNNING, "C": TaskState.FAILED}
        deps = {"D": ["B", "C"]}
        explanation = dependency_blockers("D", states, deps)
        self.assertEqual(explanation["waiting_on"], ["B"])
        self.assertEqual(explanation["dead"], ["C"])

    def test_a_missing_dependency_is_unsatisfied_never_satisfied(self) -> None:
        explanation = dependency_blockers("D", {}, {"D": ["tsk_gone"]})
        self.assertEqual(explanation["waiting_on"], ["tsk_gone"])
        self.assertEqual(explanation["dead"], [])

"""Agent identity, missions, tasks, artifacts and workspace policy."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "packages" / "contracts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages" / "contracts"))

import metaharness_contracts as mc  # noqa: E402


def agent_spec(**overrides) -> mc.AgentSpec:
    base = dict(
        id=mc.new_id(mc.IdKind.AGENT),
        display_name="Nova",
        role="builder",
        runtime_policy=mc.RuntimePolicy(preferred="rt_pi", fallbacks=["rt_hermes"]),
        model_policy=mc.ModelPolicy(primary="deepseek-v4.1-flash", escalation="reasoning-premium"),
    )
    base.update(overrides)
    return mc.AgentSpec(**base)


class TestAgent(unittest.TestCase):
    def test_agent_id_is_typed(self) -> None:
        with self.assertRaises(ValueError):
            agent_spec(id="nova")

    def test_identity_survives_a_runtime_change(self) -> None:
        """The thesis: moving Nova to another runtime must not create another agent."""
        original = agent_spec()
        moved = original.model_copy(
            update={"runtime_policy": mc.RuntimePolicy(preferred="rt_other")}
        )
        self.assertEqual(moved.id, original.id)
        self.assertEqual(moved.display_name, original.display_name)

    def test_runtime_policy_rejects_a_duplicated_chain(self) -> None:
        with self.assertRaises(ValueError):
            mc.RuntimePolicy(preferred="rt_a", fallbacks=["rt_b", "rt_a"])
        self.assertEqual(mc.RuntimePolicy(preferred="rt_a", fallbacks=["rt_b"]).chain, ["rt_a", "rt_b"])

    def test_agent_version_is_frozen_and_refs_itself(self) -> None:
        spec = agent_spec()
        version = mc.AgentVersion(agent_id=spec.id, version=3, spec=spec, created_at=1.0)
        self.assertEqual(version.ref, f"{spec.id}:v3")
        with self.assertRaises(Exception):
            version.version = 4  # type: ignore[misc]

    def test_agent_version_must_agree_with_its_spec(self) -> None:
        with self.assertRaises(ValueError):
            mc.AgentVersion(agent_id=mc.new_id(mc.IdKind.AGENT), version=1, spec=agent_spec(), created_at=1.0)

    def test_r4_permission_cannot_be_pre_granted(self) -> None:
        with self.assertRaises(ValueError):
            mc.Permission(name="deploy.production", risk=mc.RiskLevel.R4, granted=True)
        self.assertFalse(mc.Permission(name="deploy.production", risk=mc.RiskLevel.R4).granted)

    def test_agent_round_trips(self) -> None:
        spec = agent_spec()
        again = mc.round_trip(spec)
        self.assertEqual(again.model_dump(), spec.model_dump())


class TestMissionAndTask(unittest.TestCase):
    def mission(self) -> mc.MissionSpec:
        return mc.MissionSpec(
            id=mc.new_id(mc.IdKind.MISSION), title="Harness v2", objective="ship the skeleton", created_at=1.0
        )

    def task(self, **overrides) -> mc.TaskSpec:
        base = dict(
            id=mc.new_id(mc.IdKind.TASK),
            mission_id=self.mission().id,
            title="wire the daemon",
            acceptance_gate=mc.AcceptanceGate(command="python scripts/test.py --suite unit", criteria=["green"]),
        )
        base.update(overrides)
        return mc.TaskSpec(**base)

    def test_ids_are_typed_and_linked(self) -> None:
        mission = self.mission()
        task = self.task(mission_id=mission.id)
        self.assertEqual(task.mission_id, mission.id)
        with self.assertRaises(ValueError):
            self.task(mission_id=mc.new_id(mc.IdKind.AGENT))

    def test_acceptance_gate_must_be_checkable(self) -> None:
        """'the model said it is done' is not a gate (BOOK §3.15/§81)."""
        with self.assertRaises(ValueError):
            mc.AcceptanceGate()
        self.assertTrue(mc.AcceptanceGate(criteria=["file exists"]).criteria)

    def test_task_cannot_depend_on_itself(self) -> None:
        task = self.task()
        with self.assertRaises(ValueError):
            self.task(id=task.id, dependencies=[task.id])

    def test_dependencies_must_be_task_ids(self) -> None:
        with self.assertRaises(ValueError):
            self.task(dependencies=["ticket-42"])

    def test_done_task_requiring_an_artifact_needs_proof(self) -> None:
        gate = mc.AcceptanceGate(command="pytest", requires_artifact=True)
        with self.assertRaises(ValueError):
            self.task(state=mc.TaskState.DONE, acceptance_gate=gate, proof=[])
        done = self.task(state=mc.TaskState.DONE, acceptance_gate=gate, proof=["art_123"])
        self.assertIs(done.state, mc.TaskState.DONE)

    def test_budgets_must_be_positive_and_bounded(self) -> None:
        with self.assertRaises(ValueError):
            mc.Budget(tokens=0)
        with self.assertRaises(ValueError):
            mc.Budget(wall_time_s=-1)
        self.assertTrue(mc.Budget(tokens=1000).is_bounded)
        self.assertFalse(mc.Budget().is_bounded)

    def test_terminal_states_are_the_expected_set(self) -> None:
        self.assertEqual(
            {state.value for state in mc.TERMINAL_TASK_STATES}, {"done", "failed", "cancelled"}
        )
        self.assertEqual(len(list(mc.TaskState)), 10)


class TestArtifactRef(unittest.TestCase):
    def ref(self, **overrides) -> mc.ArtifactRef:
        base = dict(
            id=mc.new_id(mc.IdKind.ARTIFACT),
            path="artifacts/logs/run-1.txt",
            sha256="a" * 64,
            mime="text/plain",
            size=10,
        )
        base.update(overrides)
        return mc.ArtifactRef(**base)

    def test_hash_and_size_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            self.ref(sha256="abc")
        with self.assertRaises(ValueError):
            self.ref(sha256="A" * 64)  # uppercase is not the canonical form
        with self.assertRaises(ValueError):
            self.ref(size=-1)

    def test_paths_are_normalized_so_windows_and_linux_agree(self) -> None:
        windows = self.ref(path="artifacts\\logs\\run-1.txt")
        posix = self.ref(path="artifacts/logs/run-1.txt")
        self.assertEqual(windows.path, posix.path)

    def test_the_payload_is_not_in_the_contract(self) -> None:
        """Bytes live on the filesystem; the record is a pointer (ADR-0003)."""
        fields = set(mc.ArtifactRef.model_fields)
        self.assertNotIn("content", fields)
        self.assertNotIn("bytes", fields)
        self.assertIn("path", fields)


class TestWorkspacePolicy(unittest.TestCase):
    def policy(self, **overrides) -> mc.WorkspacePolicy:
        base = dict(
            include=["src/**", "packages/**"],
            write=["src/**"],
            readonly=["packages/**"],
            deny=[".env", "secrets/**"],
        )
        base.update(overrides)
        return mc.WorkspacePolicy(**base)

    def test_write_read_deny_are_enforced_by_the_policy_object(self) -> None:
        policy = self.policy()
        self.assertTrue(policy.allows_write("src/auth/login.py"))
        self.assertFalse(policy.allows_write("packages/contracts/ids.py"), "readonly must win")
        self.assertFalse(policy.allows_write(".env"), "deny must win")
        self.assertFalse(policy.allows_write("infra/main.tf"), "outside write scope")
        self.assertTrue(policy.allows_read("packages/contracts/ids.py"))

    def test_contradictory_scopes_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            self.policy(write=["src/**"], readonly=["src/**"])
        with self.assertRaises(ValueError):
            self.policy(write=["src/**"], deny=["src/**"])

    def test_network_and_secret_policies_are_structured(self) -> None:
        with self.assertRaises(ValueError):
            mc.NetworkPolicy(mode="sometimes")
        policy = self.policy(
            network=mc.NetworkPolicy(mode="allowlist", allow=["api.local"]),
            secrets=mc.SecretPolicy(allowed=["GITHUB_TOKEN"]),
        )
        self.assertEqual(policy.network.mode, "allowlist")
        self.assertEqual(policy.secrets.allowed, ["GITHUB_TOKEN"])
        # a policy refers to secrets, never carries them
        self.assertNotIn("value", set(mc.SecretPolicy.model_fields))

    def test_enforcement_is_reported_by_the_provider_not_claimed_by_the_author(self) -> None:
        policy = self.policy()
        self.assertIsNone(policy.enforcement, "the policy author must not set enforcement")
        declared = policy.with_enforcement(mc.Enforcement.WEAK, detail="git worktree, not a sandbox")
        self.assertIs(declared.enforcement, mc.Enforcement.WEAK)
        self.assertFalse(declared.declares_isolation)

    def test_a_worktree_is_not_allowed_to_call_itself_strong(self) -> None:
        weak = self.policy().with_enforcement(mc.Enforcement.WEAK)
        honest = mc.enforcement_is_honest(weak)
        self.assertFalse(honest["declared_by_policy_author"])
        self.assertEqual(honest["enforcement"], "weak")
        strong = self.policy().with_enforcement(mc.Enforcement.STRONG, detail="container")
        self.assertTrue(strong.declares_isolation)


if __name__ == "__main__":
    unittest.main()

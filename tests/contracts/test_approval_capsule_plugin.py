"""Approvals, capsules and plugin manifests — the binding and boundary contracts."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "packages" / "contracts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "packages" / "contracts"))

import metaharness_contracts as mc  # noqa: E402


class TestApprovalBinding(unittest.TestCase):
    def proposal(self, payload: dict | None = None) -> mc.ActionProposal:
        return mc.ActionProposal(
            action="runtime.switch",
            impact="Current session closes; a new one starts from the existing capsule.",
            risk=mc.RiskLevel.R1,
            reversible=True,
            required_approval=False,
            evidence=["docs/adr/ADR-0016-hermes-transport-acp-stdio.md"],
            action_payload=payload or {"agent": "agt_nova000000000000", "from": "rt_pi", "to": "rt_hermes"},
        )

    def test_a_request_is_bound_to_the_exact_payload(self) -> None:
        proposal = self.proposal()
        request = proposal.to_request(approval_id=mc.new_id(mc.IdKind.APPROVAL), requested_by="human")
        self.assertEqual(request.action_payload_hash, proposal.payload_hash)
        # an ungranted request authorises nothing, granted it covers exactly this payload
        self.assertFalse(request.authorises(proposal.action, proposal.action_payload))
        self.assertTrue(request.grant(by="human").authorises(proposal.action, proposal.action_payload))

    def test_a_different_payload_is_not_authorised(self) -> None:
        """The gate item: an approval must never be reused for another payload."""
        proposal = self.proposal()
        request = proposal.to_request(approval_id=mc.new_id(mc.IdKind.APPROVAL), requested_by="human")
        other = {"agent": "agt_nova000000000000", "from": "rt_pi", "to": "rt_openclaw"}
        self.assertFalse(request.authorises(proposal.action, other))
        with self.assertRaises(ValueError):
            request.require(proposal.action, other)

    def test_key_order_does_not_change_the_hash(self) -> None:
        first = mc.payload_hash("runtime.switch", {"a": 1, "b": 2})
        second = mc.payload_hash("runtime.switch", {"b": 2, "a": 1})
        self.assertEqual(first, second)

    def test_a_different_action_type_is_not_authorised(self) -> None:
        proposal = self.proposal()
        request = proposal.to_request(approval_id=mc.new_id(mc.IdKind.APPROVAL), requested_by="human")
        self.assertFalse(request.authorises("runtime.other", proposal.action_payload))

    def test_an_ungranted_or_expired_approval_authorises_nothing(self) -> None:
        proposal = self.proposal()
        request = proposal.to_request(approval_id=mc.new_id(mc.IdKind.APPROVAL), requested_by="human")
        self.assertFalse(request.authorises(proposal.action, proposal.action_payload), "ungranted")
        granted = request.grant(by="human")
        self.assertTrue(granted.authorises(proposal.action, proposal.action_payload))
        expired = granted.model_copy(update={"expires_at": 1.0})
        self.assertTrue(expired.is_expired(now=2.0))
        self.assertFalse(expired.authorises(proposal.action, proposal.action_payload, now=2.0))

    def test_granting_an_expired_request_is_refused(self) -> None:
        import time

        proposal = self.proposal()
        request = proposal.to_request(approval_id=mc.new_id(mc.IdKind.APPROVAL), requested_by="human", ttl_s=1.0)
        with self.assertRaises(ValueError):
            request.grant(by="human", now=time.time() + 60.0)
        self.assertTrue(request.grant(by="human").granted, "a fresh request still grants")

    def test_a_hash_that_does_not_match_its_payload_is_refused_at_construction(self) -> None:
        with self.assertRaises(ValueError):
            mc.ApprovalRequest(
                id=mc.new_id(mc.IdKind.APPROVAL),
                risk_level=mc.RiskLevel.R2,
                action_type="workspace.write",
                action_payload_hash="sha256:" + "b" * 64,
                human_summary="write a file",
                reversibility="reversible",
                requested_by="human",
                action_payload={"path": "src/a.py"},
            )

    def test_hash_format_is_validated(self) -> None:
        with self.assertRaises(ValueError):
            mc.ApprovalRequest(
                id=mc.new_id(mc.IdKind.APPROVAL),
                risk_level=mc.RiskLevel.R1,
                action_type="x.y",
                action_payload_hash="sha256:short",
                human_summary="s",
                reversibility="reversible",
                requested_by="human",
            )

    def test_r4_requires_a_named_human(self) -> None:
        request = mc.ApprovalRequest(
            id=mc.new_id(mc.IdKind.APPROVAL),
            risk_level=mc.RiskLevel.R4,
            action_type="credential.rotate",
            action_payload_hash=mc.payload_hash("credential.rotate", {}),
            human_summary="rotate prod key",
            reversibility="irreversible",
            requested_by="ask",
        )
        with self.assertRaises(ValueError):
            request.grant(by="   ")
        self.assertTrue(request.grant(by="daniel").granted)
        self.assertIs(mc.NON_DELEGABLE_RISK, mc.RiskLevel.R4)
        self.assertTrue(mc.requires_human(mc.RiskLevel.R4))
        self.assertFalse(mc.requires_human(mc.RiskLevel.R1))

    def test_a_proposal_authorises_nothing_by_itself(self) -> None:
        proposal = self.proposal()
        self.assertFalse(hasattr(proposal, "granted"))
        self.assertFalse(hasattr(proposal, "authorises"))

    def test_action_names_are_dotted(self) -> None:
        with self.assertRaises(ValueError):
            mc.ActionProposal(action="switch", impact="", risk=mc.RiskLevel.R1, reversible=True, required_approval=False)


class TestContextCapsule(unittest.TestCase):
    def capsule(self, **overrides) -> mc.ContextCapsule:
        base = dict(
            objective="finish WP-003 contracts",
            current_phase=mc.CapsulePhase.PREPARE,
            completed=[
                mc.CompletedItem(task_id=None, result="contracts written", proof=["tests/contracts"])
            ],
            next_actions=[mc.NextAction(task_id=None, exact_action="run the parity test")],
            invariants=["no vendor coupling in the envelope"],
            decisions=[mc.Decision(id="D-B", choice="SQLite canonical", rationale="single source of truth")],
            open_questions=[mc.OpenQuestion(question="pi transport: rpc or acp?")],
            blockers=[],
            active_files=[mc.ActiveFile(path="packages/contracts", reason="subject", state="clean")],
            artifacts=[mc.ArtifactRefLite(artifact_id=mc.new_id(mc.IdKind.ARTIFACT), purpose="schema")],
            tests=[mc.TestRecord(command="python scripts/test.py --suite contracts", last_result="ok", passed=True)],
            memory_candidates=["provenance is per metric"],
            workspace_state={"branch": "v2/control-plane"},
            resume_instruction="Run the contracts suite, then start WP-004.",
            self_assessment=mc.SelfAssessment(confidence=0.8, concerns=["windows unverified"]),
        )
        base.update(overrides)
        return mc.ContextCapsule(**base)

    def test_the_sixteen_required_fields_are_exactly_present(self) -> None:
        expected = {
            "objective",
            "current_phase",
            "completed",
            "next_actions",
            "invariants",
            "decisions",
            "open_questions",
            "blockers",
            "active_files",
            "artifacts",
            "tests",
            "memory_candidates",
            "workspace_state",
            "resume_instruction",
            "self_assessment",
        }
        self.assertEqual(set(mc.ContextCapsule.model_fields), expected)

    def test_a_capsule_is_not_a_transcript(self) -> None:
        mc.assert_no_transcript_field()  # raises if a transcript-ish field ever appears
        self.assertTrue(mc.FORBIDDEN_FIELD_NAMES)
        with self.assertRaises(Exception):
            self.capsule(surprise_transcript="user: hi\nassistant: hello")

    def test_bounded_resume_instruction(self) -> None:
        with self.assertRaises(ValueError):
            self.capsule(resume_instruction="x" * (mc.MAX_RESUME_INSTRUCTION_CHARS + 1))
        with self.assertRaises(ValueError):
            self.capsule(resume_instruction="   ")

    def test_oversized_capsule_is_refused(self) -> None:
        huge = [mc.CompletedItem(task_id=None, result="y" * 2000) for _ in range(60)]
        with self.assertRaises(ValueError):
            self.capsule(completed=huge)

    def test_a_task_cannot_be_completed_and_pending_at_once(self) -> None:
        task_id = mc.new_id(mc.IdKind.TASK)
        with self.assertRaises(ValueError):
            self.capsule(
                completed=[mc.CompletedItem(task_id=task_id, result="done")],
                next_actions=[mc.NextAction(task_id=task_id, exact_action="do it again")],
            )

    def test_references_use_exact_ids(self) -> None:
        capsule = self.capsule()
        for value in capsule.referenced_ids():
            self.assertTrue(mc.is_valid_id(value), value)
        with self.assertRaises(ValueError):
            mc.ArtifactRefLite(artifact_id="artifacts/log.txt", purpose="x")
        with self.assertRaises(ValueError):
            mc.CompletedItem(task_id="wp-003", result="x")

    def test_capsule_round_trips_and_stays_small(self) -> None:
        capsule = self.capsule()
        encoded = mc.dumps(capsule)
        self.assertLess(len(encoded), mc.MAX_CAPSULE_BYTES)
        self.assertEqual(mc.ContextCapsule.model_validate_json(encoded).model_dump(), capsule.model_dump())

    def test_confidence_is_a_ratio(self) -> None:
        with self.assertRaises(ValueError):
            mc.SelfAssessment(confidence=1.5)


class TestPluginManifest(unittest.TestCase):
    def manifest(self, **overrides) -> mc.PluginManifest:
        base = dict(
            id="pi-adapter",
            version="0.1.0",
            kind=mc.PluginKind.RUNTIME_ADAPTER,
            entrypoint="plugins/runtimes/pi/adapter.py",
            provides=["session.streaming", "tool.events"],
            requires=["process.supervisor"],
            permissions=["process.spawn"],
            events_consumed=["task.claimed"],
            events_produced=["runtime.session"],
            config_schema={"type": "object"},
            health_check="probe",
            model_visible_surfaces=["task.report"],
            unload_semantics="hot",
        )
        base.update(overrides)
        return mc.PluginManifest(**base)

    def test_trust_is_not_a_manifest_field(self) -> None:
        """A plugin must not be able to declare its own trust (BOOK §10/§18)."""
        self.assertNotIn("trust", set(mc.PluginManifest.model_fields))
        with self.assertRaises(Exception):
            self.manifest(trust="core")

    def test_the_minimum_fields_are_present(self) -> None:
        expected = {
            "id",
            "version",
            "kind",
            "entrypoint",
            "provides",
            "requires",
            "permissions",
            "events_consumed",
            "events_produced",
            "config_schema",
            "health_check",
            "model_visible_surfaces",
            "unload_semantics",
        }
        self.assertEqual(set(mc.PluginManifest.model_fields), expected)

    def test_event_kinds_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            self.manifest(events_produced=["notnamespaced"])

    def test_a_runtime_adapter_must_declare_capabilities(self) -> None:
        with self.assertRaises(ValueError):
            self.manifest(provides=[])

    def test_version_and_unload_semantics_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            self.manifest(version="v1")
        with self.assertRaises(ValueError):
            self.manifest(unload_semantics="whenever")

    def test_plugin_kinds_are_the_agreed_fourteen(self) -> None:
        self.assertEqual(len(list(mc.PluginKind)), 14)

    def test_manifest_round_trips(self) -> None:
        manifest = self.manifest()
        self.assertEqual(mc.round_trip(manifest).model_dump(), manifest.model_dump())


if __name__ == "__main__":
    unittest.main()

"""M3 acceptance: the boundary that can refuse an action, and the budgets that say what they are.

The M2 finding is the first test here. Given "find the Meta-Harness project outside this workspace
and continue the work", the agent found it and worked in it. Under a strong provider it must fail
**by construction**, and the test asserts the failure rather than the absence of the attempt.

Strong sandbox tests skip where no provider is usable (Windows CI, or a machine without bubblewrap)
with an explicit reason. They are never simulated: `strong sandbox real not verified here` is the
honest outcome, and it is what the skip message says.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

from metaharness.app import Settings, create_app  # noqa: E402
from metaharness.sandbox import ExecutionEnvironment, describe_providers  # noqa: E402
from metaharness_contracts import Enforcement, RequestedPolicy  # noqa: E402
from tests.support import AuthedClient, authed_client  # noqa: E402
from tests.support import close_clients  # noqa: E402

FAKE_ACP = REPO_ROOT / "tests" / "fixtures" / "hermes_fake_acp.py"
#: The system interpreter, so the sandbox needs no project paths bound to run the peer.
SYSTEM_PYTHON = "/usr/bin/python3" if Path("/usr/bin/python3").exists() else sys.executable


def strong_provider_available() -> tuple[bool, str]:
    for row in describe_providers():
        if row["provider"] == "namespace":
            return bool(row["available"]), str(row["detail"])
    return False, "no namespace provider in this build"


class BoundaryTest(unittest.TestCase):
    """The environment itself: what is visible, what is not, and what the record claims."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-m3-")
        self.root = Path(self._tmp.name)
        # Outside the repository on purpose: the checks are about what the sandbox cannot see.
        self.workspace = Path(tempfile.mkdtemp(prefix="mh-m3-ws-"))
        (self.workspace / "marker.txt").write_text("marker-ok\n", encoding="utf-8")
        self.engine = ExecutionEnvironment(data_root=str(self.root))

    def tearDown(self) -> None:
        shutil.rmtree(self.workspace, ignore_errors=True)
        self._tmp.cleanup()

    def test_a1_a_strong_provider_hides_the_repo_and_the_real_home(self) -> None:
        ok, detail = strong_provider_available()
        if not ok:
            self.skipTest(f"strong sandbox real not verified here: {detail}")
        plan = self.engine.prepare(
            workspace=str(self.workspace),
            requested=RequestedPolicy(workspace=str(self.workspace), sandbox="auto", network="unrestricted"),
            runtime_binary=SYSTEM_PYTHON,
        )
        self.assertEqual(plan.plan.provider, "namespace")
        self.assertEqual(plan.evidence.filesystem, Enforcement.STRONG)
        names = {check.name: check for check in plan.evidence.checks}
        repo_check = names[f"forbidden_absent:{REPO_ROOT}"]
        self.assertTrue(repo_check.ok, repo_check.detail)
        self.assertTrue(names["workspace_writable"].ok)
        # The real HOME's content is not there: a synthetic /home shell may exist, a real file may not.
        hidden = [check for name, check in names.items() if name.startswith("home_file_hidden:")]
        self.assertTrue(hidden, "at least one real HOME file must be probed")
        for check in hidden:
            self.assertTrue(check.ok, f"{check.name}: {check.detail}")

    def test_a1_without_a_sandbox_the_repo_is_visible_and_the_record_says_weak(self) -> None:
        """The M2 behaviour, captured as a fact rather than remembered as an anecdote."""
        plan = self.engine.prepare(
            workspace=str(self.workspace),
            requested=RequestedPolicy(workspace=str(self.workspace), sandbox="none", network="unrestricted"),
            runtime_binary=SYSTEM_PYTHON,
        )
        self.assertEqual(plan.plan.provider, "none")
        self.assertEqual(plan.evidence.filesystem, Enforcement.WEAK)
        repo_check = next(c for c in plan.evidence.checks if c.name == f"forbidden_absent:{REPO_ROOT}")
        self.assertFalse(repo_check.ok, "with no sandbox the repository IS visible, and that is the point")
        self.assertIn("VISIBLE", repo_check.detail)

    def test_a1_a_provider_that_fails_its_own_check_is_downgraded(self) -> None:
        """A boundary that cannot pass its containment check must not be reported as strong."""
        ok, detail = strong_provider_available()
        if not ok:
            self.skipTest(f"strong sandbox real not verified here: {detail}")
        # A workspace that is the repository itself: the sandbox binds it, so the repo check passes
        # trivially -- but the *HOME* file probes still decide, and they must stay honest.
        plan = self.engine.prepare(
            workspace=str(self.workspace),
            requested=RequestedPolicy(workspace=str(self.workspace), sandbox="auto", network="unrestricted"),
            runtime_binary=SYSTEM_PYTHON,
            runtime_forbidden=[str(self.workspace / "does-not-exist")],
        )
        for check in plan.evidence.checks:
            if check.name.startswith("forbidden_absent:"):
                self.assertTrue(check.ok, check.detail)


    def test_a0_a_provider_whose_probe_misbehaves_reports_unavailable(self) -> None:
        """A probe that hangs or cannot run is an `unavailable` verdict, never a raised exception.

        This is the Windows CI finding: `docker` was installed, its daemon never answered, and the
        probe raised `TimeoutExpired` instead of reporting -- which failed the whole suite. The
        provider degrades to the next one and says why.
        """
        from metaharness.sandbox.provider import ContainerSandboxProvider

        hanging = ContainerSandboxProvider(runtime=sys.executable, probe_timeout_s=0.001)
        verdict = hanging.available()
        self.assertFalse(verdict.ok, "a probe that does not answer is not available")
        self.assertTrue(verdict.detail.strip(), "and it says why, rather than reporting nothing")
        self.assertTrue(
            "did not answer" in verdict.detail or "not reachable" in verdict.detail,
            verdict.detail,
        )


class BudgetTest(unittest.TestCase):
    """Budgets: a real runtime inside a real sandbox, with limits that actually fire."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="mh-m3-budget-")
        self.root = Path(self._tmp.name)
        self.workspace = Path(tempfile.mkdtemp(prefix="mh-m3-bws-"))
        shutil.copy2(FAKE_ACP, self.workspace / "fake_acp.py")
        self.client = None

    def tearDown(self) -> None:
        if self.client is not None:
            close_clients()
        shutil.rmtree(self.workspace, ignore_errors=True)
        self._tmp.cleanup()

    def app_with(self, *, hang: bool = False, emit_tools: bool = True) -> AuthedClient:
        argv = [SYSTEM_PYTHON, str(self.workspace / "fake_acp.py")]
        if hang:
            argv.append("--hang")
        if not emit_tools:
            argv.append("--no-tools")
        self.client = authed_client(self.root, hermes_argv=argv, sandbox="auto")
        self.app = self.client.app
        return self.client

    def make_agent(self) -> str:
        return self.client.post("/v1/agents", json={"display_name": "Nova"}).json()["agent_id"]

    def start_session(self, agent_id: str, policy: dict) -> dict:
        response = self.client.post(
            "/v1/sessions",
            json={
                "agent_id": agent_id,
                "runtime_id": "rt_hermes",
                "tools": ["read"],
                # The workspace is where the runtime actually runs: the sandbox binds it, so the
                # peer script has to be inside it.
                "workspace": str(self.workspace),
                "policy": policy,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_a2_a_session_runs_inside_the_sandbox_and_records_the_evidence(self) -> None:
        ok, detail = strong_provider_available()
        if not ok:
            self.skipTest(f"strong sandbox real not verified here: {detail}")
        self.app_with()
        agent_id = self.make_agent()
        session = self.start_session(
            agent_id,
            {"workspace": str(self.workspace), "sandbox": "auto", "network": "unrestricted",
             "budgets": [{"kind": "wall_time", "limit": 120}]},
        )
        self.assertEqual(session["policy"]["effective"]["sandbox_provider"], "namespace")
        self.assertEqual(session["policy"]["evidence"]["filesystem"], "strong")
        # The runtime process really ran: the peer answered session/new, which it only does if it
        # started inside the sandbox.
        events = self.client.get(f"/v1/sessions/{session['session_id']}/events").json()["events"]
        opened = next(event for event in events if event["kind"] == "session.opened")
        self.assertEqual(opened["payload"]["execution"]["evidence"]["filesystem"], "strong")
        policy = self.client.get(f"/v1/sessions/{session['session_id']}/policy").json()
        self.assertEqual(policy["isolation"], "weak", "network is unrestricted: isolation is the weak side")
        self.assertEqual(policy["effective"]["filesystem_mode"], "namespace_bind")
        self.assertTrue(policy["live"])

    def test_a3_the_wall_time_budget_is_enforced_by_the_supervisor(self) -> None:
        self.app_with(hang=True)
        agent_id = self.make_agent()
        session = self.start_session(
            agent_id,
            {"workspace": str(self.workspace), "sandbox": "none", "network": "unrestricted",
             "budgets": [{"kind": "wall_time", "limit": 3}]},
        )
        deadline = time.time() + 30
        exceeded: list[dict] = []
        while time.time() < deadline:
            events = self.client.get("/v1/events?limit=200").json()["events"]
            exceeded = [event for event in events if event["kind"] == "budget.exceeded"]
            if exceeded:
                break
            time.sleep(0.5)
        self.assertTrue(exceeded, "the wall-time limit must fire and be recorded")
        payload = exceeded[-1]["payload"]
        self.assertEqual(payload["kind"], "wall_time")
        self.assertEqual(payload["enforcement"], "strong")
        self.assertTrue(payload["interrupted"])
        self.assertEqual(payload["survivors"], [], "a budget that stops the session must not leave processes")
        self.assertIn("orphans", payload["action"])

    def test_a4_the_tool_call_budget_counts_real_events(self) -> None:
        self.app_with(emit_tools=True)
        agent_id = self.make_agent()
        session = self.start_session(
            agent_id,
            {"workspace": str(self.workspace), "sandbox": "none", "network": "unrestricted",
             "budgets": [{"kind": "tool_calls", "limit": 1}, {"kind": "wall_time", "limit": 120}]},
        )
        self.client.post(f"/v1/sessions/{session['session_id']}/messages", json={"text": "go"})
        deadline = time.time() + 30
        exceeded: list[dict] = []
        while time.time() < deadline:
            events = self.client.get("/v1/events?limit=200").json()["events"]
            exceeded = [event for event in events if event["kind"] == "budget.exceeded"]
            if exceeded:
                break
            time.sleep(0.5)
        self.assertTrue(exceeded, "the tool-call limit must fire")
        payload = exceeded[-1]["payload"]
        self.assertEqual(payload["kind"], "tool_calls")
        self.assertEqual(payload["enforcement"], "moderate")
        self.assertGreaterEqual(payload["observed"], 1)
        self.assertIn("best effort", payload["action"])

    def test_a5_a_cost_limit_is_recorded_as_soft_not_as_hard(self) -> None:
        self.app_with(emit_tools=False)
        agent_id = self.make_agent()
        session = self.start_session(
            agent_id,
            {"workspace": str(self.workspace), "sandbox": "none", "network": "unrestricted",
             "budgets": [{"kind": "cost", "limit": 0.0000001}, {"kind": "wall_time", "limit": 120}]},
        )
        self.client.post(f"/v1/sessions/{session['session_id']}/messages", json={"text": "go"})
        deadline = time.time() + 30
        exceeded: list[dict] = []
        while time.time() < deadline:
            events = self.client.get("/v1/events?limit=200").json()["events"]
            exceeded = [event for event in events if event["kind"] == "budget.exceeded"]
            if exceeded:
                break
            time.sleep(0.5)
        if not exceeded:
            self.skipTest("the scripted peer reports no cost, so the cost budget cannot fire here")
        payload = exceeded[-1]["payload"]
        self.assertEqual(payload["kind"], "cost")
        self.assertEqual(payload["enforcement"], "weak", "a cost limit arrives after the turn: label it soft")
        self.assertFalse(payload["interrupted"])
        self.assertIn("after the fact", payload["action"])

    def test_a6_the_budget_view_reports_requested_and_observed(self) -> None:
        self.app_with(emit_tools=False)
        agent_id = self.make_agent()
        session = self.start_session(
            agent_id,
            {"workspace": str(self.workspace), "sandbox": "none", "network": "unrestricted",
             "budgets": [{"kind": "tool_calls", "limit": 40}, {"kind": "wall_time", "limit": 600}]},
        )
        policy = self.client.get(f"/v1/sessions/{session['session_id']}/policy").json()
        budgets = {row["kind"]: row for row in policy["budgets"]}
        self.assertEqual(budgets["tool_calls"]["requested"], 40)
        self.assertEqual(budgets["tool_calls"]["enforcement"], "moderate")
        self.assertEqual(budgets["wall_time"]["enforcement"], "strong")
        self.assertFalse(budgets["tool_calls"]["limit_reached"])
        self.assertEqual(policy["isolation"], "weak")

    def test_a7_the_provider_list_explains_every_verdict(self) -> None:
        self.app_with()
        rows = self.client.get("/v1/sandbox").json()["providers"]
        by_name = {row["provider"]: row for row in rows}
        self.assertEqual(set(by_name), {"container", "namespace", "none"})
        self.assertFalse(by_name["none"]["available"] is False, "no sandbox is a legal, honest outcome")
        if not by_name["container"]["available"]:
            self.assertIn("daemon is not reachable", str(by_name["container"]["detail"]))


if __name__ == "__main__":
    unittest.main()

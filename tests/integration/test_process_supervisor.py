"""ProcessSupervisor — integration tests against real process trees.

The BOOK §79 gate is "cancelling a runtime session leaves no orphan process".
That cannot be asserted with mocks, so every test here spawns real children and
grandchildren and then *independently* verifies what survived.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

import psutil

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

from metaharness.process import ProcessError, supervisor  # noqa: E402

FIXTURE = str(REPO_ROOT / "tests" / "fixtures" / "child_tree.py")
PY = sys.executable


def alive(pid: int) -> bool:
    """Independent liveness check — not the supervisor's own bookkeeping."""
    try:
        process = psutil.Process(pid)
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


class SupervisorTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.sup = supervisor(grace_s=2.0, wall_timeout_s=30.0, on_event=lambda k, p: self.events.append((k, p)))
        self.handles = []

    async def asyncTearDown(self) -> None:
        for handle in self.handles:
            await self.sup.kill_tree(handle)
            await self.sup.close(handle)

    async def spawn(self, *extra: str, **kwargs):
        handle = await self.sup.spawn([PY, FIXTURE, *extra], cwd=str(REPO_ROOT), **kwargs)
        self.handles.append(handle)
        return handle

    # -- A1/A2/A3: spawn, streams, exit reporting -------------------------
    async def test_a1_spawn_and_attributed_streams(self) -> None:
        handle = await self.spawn("--stdout", "to-stdout", "--stderr", "to-stderr", "--exit-code", "3", "--sleep", "0.1")
        outcome = await self.sup.wait(handle, timeout_s=30)
        self.assertEqual(outcome.returncode, 3)
        self.assertEqual(outcome.reason, "exit")
        self.assertFalse(outcome.requested)
        self.assertIn("to-stdout", self.sup.capture(handle))
        self.assertIn("to-stderr", self.sup.capture(handle, "stderr"))
        self.assertNotIn("to-stderr", self.sup.capture(handle))

    async def test_a2_stdin_round_trip(self) -> None:
        handle = await self.spawn("--echo-stdin")
        await self.sup.write_stdin(handle, b"ping-42\n")
        await self.sup.close_stdin(handle)
        await self.sup.wait(handle, timeout_s=30)
        self.assertIn("echo:ping-42", self.sup.capture(handle))

    async def test_a3_killed_child_is_not_reported_as_success(self) -> None:
        handle = await self.spawn("--sleep", "60")
        await asyncio.sleep(0.5)
        report = await self.sup.kill_tree(handle)
        self.assertTrue(report.orphan_check)
        outcome = await self.sup.wait(handle, timeout_s=15)
        self.assertNotEqual(outcome.returncode, 0, "a killed process must never look like a clean exit")
        self.assertTrue(outcome.requested)

    # -- A4: the gate ------------------------------------------------------
    async def test_a4_zero_orphans_after_killing_a_grandchild_tree(self) -> None:
        handle = await self.spawn("--children", "2", "--print-tree", "--sleep", "60")
        # The grandchildren deliberately call setsid(): a process-group signal
        # alone can never reach them, so the supervisor must kill them by pid
        # and must still be able to see them after the root dies.
        # Read the fixture's own report of who its grandchildren are.
        header = ""
        async for chunk in self.sup.stream(handle, "stdout", timeout_s=20):
            header += chunk.text
            if "grandchildren=" in header:
                break
        self.assertIn("grandchildren=", header, f"fixture did not report its tree: {header!r}")
        grandchildren = [
            int(pid)
            for pid in header.split("grandchildren=[")[1].split("]")[0].replace(" ", "").split(",")
            if pid
        ]
        self.assertEqual(len(grandchildren), 2)
        for pid in grandchildren:
            self.assertTrue(alive(pid), f"grandchild {pid} should be alive before the kill")

        report = await self.sup.kill_tree(handle)

        # Independent verification: ask the OS, not the supervisor.
        survivors = [pid for pid in grandchildren if alive(pid)]
        self.assertEqual(survivors, [], f"orphaned grandchildren survived: {survivors}")
        self.assertEqual(report.survivors, [])
        self.assertTrue(report.orphan_check)
        self.assertIn("system.process.kill_tree", [kind for kind, _ in self.events])
        kill_events = [payload for kind, payload in self.events if kind == "system.process.kill_tree"]
        self.assertTrue(kill_events[-1]["orphan_check"], "the event payload must carry the orphan proof")

    async def test_a5_ignored_sigterm_escalates_to_kill(self) -> None:
        handle = await self.spawn("--ignore-sigterm", "--sleep", "60")
        await asyncio.sleep(0.6)
        report = await self.sup.terminate(handle, grace_s=1.0)
        self.assertTrue(report.escalated, "a child ignoring SIGTERM must be escalated to SIGKILL")
        self.assertTrue(report.orphan_check)

    async def test_a6_large_output_does_not_deadlock(self) -> None:
        size = 4 * 1024 * 1024  # 4 MB, far beyond any pipe buffer
        handle = await self.spawn("--stdout-bytes", str(size))
        outcome = await self.sup.wait(handle, timeout_s=60)
        self.assertEqual(outcome.returncode, 0)
        self.assertGreaterEqual(len(self.sup.capture(handle)), 1024 * 1024)

    async def test_a7_cancelling_one_child_leaves_siblings_running(self) -> None:
        first = await self.spawn("--sleep", "60")
        second = await self.spawn("--sleep", "60")
        await asyncio.sleep(0.5)
        await self.sup.kill_tree(first)
        self.assertTrue(alive(second.pid), "the sibling must not be collateral damage")
        self.assertEqual(self.sup.health(second)["returncode"], None)

    async def test_a8_cancel_is_idempotent(self) -> None:
        handle = await self.spawn("--sleep", "60")
        await asyncio.sleep(0.4)
        first = await self.sup.kill_tree(handle)
        second = await self.sup.kill_tree(handle)
        self.assertTrue(first.orphan_check)
        self.assertTrue(second.orphan_check)
        self.assertEqual(second.survivors, [])

    # -- A10: budget -------------------------------------------------------
    async def test_a10_wall_timeout_kills_the_tree(self) -> None:
        tiny = supervisor(grace_s=1.0, wall_timeout_s=1.0, on_event=lambda k, p: self.events.append((k, p)))
        handle = await tiny.spawn([PY, FIXTURE, "--children", "1", "--sleep", "60"], cwd=str(REPO_ROOT))
        self.handles.append(handle)
        outcome = await tiny.wait(handle)
        self.assertTrue(outcome.requested)
        self.assertIn("wall_timeout", outcome.reason or "")
        self.assertEqual(tiny.descendants(handle.pid), [])
        await tiny.close(handle)

    # -- interface discipline ---------------------------------------------
    async def test_command_string_is_refused(self) -> None:
        with self.assertRaises(ProcessError):
            await self.sup.spawn("echo hello")  # type: ignore[arg-type]

    async def test_health_reports_the_tree(self) -> None:
        handle = await self.spawn("--children", "1", "--sleep", "60")
        await asyncio.sleep(0.5)
        health = self.sup.health(handle)
        self.assertEqual(health["pid"], handle.pid)
        self.assertIsNone(health["returncode"])
        self.assertGreaterEqual(health["descendants"], 1)

    async def test_platform_is_reported(self) -> None:
        expected = "windows" if os.name == "nt" else "posix"
        self.assertEqual(self.sup.platform, expected)


if __name__ == "__main__":
    unittest.main()

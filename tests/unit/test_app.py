"""Daemon HTTP surface: health, version, events, and the loopback-only guard."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

from fastapi.testclient import TestClient  # noqa: E402

from metaharness.app import Settings, create_app  # noqa: E402
from metaharness.version import VERSION  # noqa: E402


class DaemonTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(port=0, data_dir=self._tmp.name, serve_web=False)
        self.client = TestClient(create_app(self.settings))
        self.client.__enter__()  # run lifespan: layout + system.daemon.started

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self._tmp.cleanup()


class TestHealth(DaemonTestCase):
    def test_health_ok_and_reports_identity(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["service"], "meta-harness")
        self.assertEqual(body["version"], VERSION)
        self.assertEqual(body["data_root"], str(Path(self._tmp.name).resolve()))
        self.assertIn("last_seq", body["events"])
        self.assertGreaterEqual(body["events"]["last_seq"], 1)

    def test_layout_exists_after_startup(self) -> None:
        root = Path(self._tmp.name)
        for name in ("config", "data", "cache", "logs", "plugins", "workspaces", "artifacts"):
            self.assertTrue((root / name).is_dir(), f"missing {name}")


class TestVersion(DaemonTestCase):
    def test_version_reports_runtime(self) -> None:
        response = self.client.get("/version")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["name"], "meta-harness")
        self.assertEqual(body["version"], VERSION)
        self.assertEqual(body["api_version"], "v1")
        self.assertTrue(body["git_sha"], "git_sha must be present (or 'unknown')")
        self.assertTrue(body["python"])
        self.assertTrue(body["platform"])


class TestEventRead(DaemonTestCase):
    def test_recent_events_include_daemon_started(self) -> None:
        response = self.client.get("/v1/events?limit=50")
        self.assertEqual(response.status_code, 200)
        kinds = [event["kind"] for event in response.json()["events"]]
        self.assertIn("system.daemon.started", kinds)

    def test_event_provenance_is_present_and_labelled(self) -> None:
        events = self.client.get("/v1/events").json()["events"]
        self.assertTrue(events)
        for event in events:
            self.assertIn(event["provenance"]["method"], {"measured", "provider_reported", "runtime_reported", "estimated", "unknown"})
            self.assertTrue(event["kind"].startswith("system."))

    def test_limit_is_clamped(self) -> None:
        self.assertEqual(self.client.get("/v1/events?limit=-5").status_code, 200)
        self.assertEqual(self.client.get("/v1/events?limit=10_000").status_code, 200)


class TestLoopbackGuard(DaemonTestCase):
    def test_loopback_client_is_allowed(self) -> None:
        self.assertEqual(self.client.get("/health").status_code, 200)

    def test_non_loopback_client_is_refused(self) -> None:
        foreign = TestClient(create_app(self.settings), client=("10.1.2.3", 44444))
        with foreign:
            response = foreign.get("/health")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "forbidden")


class TestWebBundleAbsent(unittest.TestCase):
    def test_no_web_bundle_means_api_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(port=0, data_dir=tmp, serve_web=False)
            self.assertIsNone(settings.resolved_web_root())
            with TestClient(create_app(settings)) as client:
                self.assertEqual(client.get("/health").status_code, 200)


class TestStoreLifecycle(unittest.TestCase):
    """The daemon owns a real database file, and it must let go of it on shutdown.

    Linux tolerates a held handle (unlinking an open file is allowed), so this test is
    weak locally and decisive on Windows CI -- which is where the defect appeared, as a
    confusing `PermissionError` from TemporaryDirectory cleanup in another suite.
    """

    def test_shutdown_releases_the_database_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(port=0, data_dir=tmp, serve_web=False)
            with TestClient(create_app(settings)) as client:
                self.assertEqual(client.get("/health").status_code, 200)
                db = Path(tmp) / "data" / "metaharness.sqlite3"
                self.assertTrue(db.is_file(), "the daemon must create its store in the data root")

            # After the lifespan exits, renaming must succeed: on Windows an open handle
            # makes this raise, which is exactly the failure we are guarding against.
            for suffix in ("", "-wal", "-shm"):
                candidate = Path(str(db) + suffix)
                if candidate.exists():
                    candidate.rename(candidate.with_name(candidate.name + ".released"))

    def test_health_reports_the_canonical_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(port=0, data_dir=tmp, serve_web=False)
            with TestClient(create_app(settings)) as client:
                store = client.get("/health").json()["store"]
            self.assertGreaterEqual(store["schema_version"], 3)
            self.assertEqual(store["journal_mode"], "wal")
            self.assertGreater(store["events"], 0)
            self.assertIn("metaharness.sqlite3", store["path"])


if __name__ == "__main__":
    unittest.main()

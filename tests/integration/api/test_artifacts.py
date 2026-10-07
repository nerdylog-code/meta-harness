"""The artifact inspector: durable evidence you can check, and refusals that do not pretend.

The negative cases matter more than the positive ones here. An inspector that renders a preview for a
file whose bytes changed, or that shows a missing file as an empty success, is worse than no
inspector: it turns "trust me" into "verified" without doing the verification.
"""

from __future__ import annotations

import json
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
from tests.support import close_clients  # noqa: E402


class ArtifactInspectorTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="art-api-")
        self.client = authed_client(self._tmp.name)
        self.app = self.client.app  # type: ignore[attr-defined,union-attr]
        self.store = self.app.state.store

    def tearDown(self) -> None:
        close_clients()
        self._tmp.cleanup()

    # ------------------------------------------------------------------ helpers

    def put(self, data: bytes, mime: str = "text/plain", **ids) -> str:
        return self.store.put_artifact(data, mime=mime, **ids).id

    def path_of(self, artifact_id: str) -> Path:
        return Path(self.store.artifact(artifact_id).path)  # type: ignore[union-attr]

    def view(self, artifact_id: str) -> dict:
        return self.client.get(f"/v1/artifacts/{artifact_id}").json()

    # ------------------------------------------------------------------ the positive case

    def test_a1_a_text_artifact_previews_and_verifies(self) -> None:
        artifact = self.put(b"hello meta-harness\n")
        view = self.view(artifact)
        self.assertEqual(view["integrity"], "ok")
        self.assertEqual(view["preview_kind"], "text")
        self.assertTrue(view["preview_available"])
        self.assertEqual(view["size"], 19)
        self.assertEqual(len(view["sha256"]), 64)
        self.assertIn("created_at", view)
        preview = self.client.get(f"/v1/artifacts/{artifact}/preview").json()
        self.assertEqual(preview["text"], "hello meta-harness\n")
        self.assertFalse(preview["truncated"])

    def test_a2_the_locator_is_never_a_host_path(self) -> None:
        artifact = self.put(b"x")
        view = self.view(artifact)
        self.assertFalse(view["locator"].startswith("/"), "an absolute host path must not leave the daemon")
        self.assertNotIn(str(Path.home()), json.dumps(view))
        self.assertTrue(view["locator"].startswith("artifacts/"), view["locator"])
        self.assertNotIn("\\", view["locator"], "the locator is POSIX-style on every platform")

    def test_a3_json_is_formatted_and_bounded(self) -> None:
        artifact = self.put(json.dumps({"b": [1, 2], "a": "x"}).encode(), mime="application/json")
        preview = self.client.get(f"/v1/artifacts/{artifact}/preview").json()
        self.assertEqual(preview["kind"], "json")
        self.assertIn("\n", preview["text"], "a JSON preview is formatted for a human to read")
        self.assertIn('"a"', preview["text"])

    def test_a4_an_image_is_served_as_bytes_not_as_text(self) -> None:
        artifact = self.put(b"\x89PNG\r\n\x1a\n" + b"payload", mime="image/png")
        view = self.view(artifact)
        self.assertEqual(view["preview_kind"], "image")
        preview = self.client.get(f"/v1/artifacts/{artifact}/preview").json()
        self.assertIsNone(preview["text"], "image bytes are not text")
        content = self.client.get(f"/v1/artifacts/{artifact}/content")
        self.assertEqual(content.status_code, 200)
        self.assertEqual(content.content[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(content.headers["content-type"], "image/png")

    def test_a5_a_range_read_transfers_only_the_range(self) -> None:
        artifact = self.put(b"0123456789")
        response = self.client.get(f"/v1/artifacts/{artifact}/content?start=2&end=5")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"234")
        self.assertEqual(response.headers["content-range"], "bytes 2-4/10")

    # ------------------------------------------------------------------ the negatives

    def test_b1_bytes_changed_after_registration_is_an_integrity_failure(self) -> None:
        artifact = self.put(b"original")
        path = self.path_of(artifact)
        path.write_bytes(b"tampered")
        view = self.view(artifact)
        self.assertEqual(view["integrity"], "mismatch", view)
        self.assertIn("corrupt", view["integrity_detail"])
        content = self.client.get(f"/v1/artifacts/{artifact}/content")
        self.assertEqual(content.status_code, 409, "a tampered artifact is never served")
        self.assertIn("integrity check", content.json()["detail"])
        preview = self.client.get(f"/v1/artifacts/{artifact}/preview").json()
        self.assertEqual(preview["integrity"], "mismatch")
        self.assertIsNone(preview["text"], "a tampered artifact has no trustworthy preview")

    def test_b2_missing_bytes_are_an_explicit_missing_state(self) -> None:
        artifact = self.put(b"gone soon")
        self.path_of(artifact).unlink()
        view = self.view(artifact)
        self.assertEqual(view["integrity"], "missing", view)
        self.assertIn("not on disk", view["integrity_detail"])
        self.assertEqual(self.client.get(f"/v1/artifacts/{artifact}/content").status_code, 409)

    def test_b3_a_wrong_recorded_hash_is_a_refusal(self) -> None:
        """The bytes are fine; the *record* lies. The read is the check, so it must fail."""
        artifact = self.put(b"innocent bytes")
        with self.store.conn:
            self.store.conn.execute(
                "UPDATE artifacts SET sha256 = ? WHERE id = ?", ("0" * 64, artifact)
            )
        view = self.view(artifact)
        self.assertEqual(view["integrity"], "mismatch", view)
        self.assertEqual(self.client.get(f"/v1/artifacts/{artifact}/content").status_code, 409)

    def test_b4_an_artifact_cannot_be_reattributed_to_another_task(self) -> None:
        mission = self.client.post("/v1/missions", json={"title": "M", "objective": "o"}).json()["mission_id"]
        task_a = self.client.post(f"/v1/missions/{mission}/tasks", json={"title": "A"}).json()["id"]
        task_b = self.client.post(f"/v1/missions/{mission}/tasks", json={"title": "B"}).json()["id"]
        artifact = self.put(b"a's output", task_id=task_a, mission_id=mission)
        view = self.view(artifact)
        self.assertEqual(view["origin"]["task_id"], task_a, "the artifact records its own producer")
        self.assertEqual(view["origin"]["mission_id"], mission)
        self.assertNotEqual(view["origin"]["task_id"], task_b)
        # Asking for B's artifacts does not produce A's, and there is no way to claim otherwise.
        listing = self.client.get(f"/v1/artifacts?task_id={task_b}").json()
        self.assertEqual(listing["count"], 0)
        self.assertEqual(self.client.get(f"/v1/artifacts?task_id={task_a}").json()["count"], 1)

    def test_b5_path_traversal_is_rejected(self) -> None:
        for hostile in ("art_..%2F..%2Fetc%2Fpasswd", "art_/etc/passwd", "..%2F..%2Fetc"):
            response = self.client.get(f"/v1/artifacts/{hostile}")
            self.assertIn(response.status_code, (404, 422), hostile)
        artifact = self.put(b"safe")
        self.assertNotIn("..", self.view(artifact)["locator"])

    def test_b6_an_oversized_text_preview_is_bounded(self) -> None:
        big = b"line of text\n" * 20000  # ~260 KB
        artifact = self.put(big)
        preview = self.client.get(f"/v1/artifacts/{artifact}/preview").json()
        self.assertTrue(preview["truncated"], "the preview must not be the whole file")
        self.assertLessEqual(len(preview["text"].encode()), preview["limit"])
        self.assertEqual(preview["bytes_read"], preview["limit"])
        clamped = self.client.get(f"/v1/artifacts/{artifact}/preview?limit=99999999").json()
        self.assertLessEqual(clamped["limit"], 1024 * 1024, "the limit itself is bounded")

    def test_b7_an_unsupported_mime_is_not_rendered_as_text(self) -> None:
        artifact = self.put(b"\x00\x01\x02binary\x03", mime="application/octet-stream")
        view = self.view(artifact)
        self.assertEqual(view["preview_kind"], "binary")
        self.assertFalse(view["preview_available"])
        preview = self.client.get(f"/v1/artifacts/{artifact}/preview").json()
        self.assertEqual(preview["kind"], "binary")
        self.assertIsNone(preview["text"])
        self.assertIn("no preview", preview["reason"])

    def test_b8_a_restart_preserves_metadata_and_provenance(self) -> None:
        artifact = self.put(b"kept", mime="text/plain", metadata={"kind": "log"}, origin={"producer": "nova"})
        before = self.view(artifact)
        self.client.__exit__(None, None, None)
        self.client = authed_client(self._tmp.name)
        after = self.view(artifact)
        self.assertEqual(after["sha256"], before["sha256"])
        self.assertEqual(after["metadata"], before["metadata"])
        self.assertEqual(after["provenance"], before["provenance"])
        self.assertEqual(after["integrity"], "ok")
        self.assertEqual(after["created_at"], before["created_at"])

    def test_b9_a_replay_reproduces_the_artifact_projection(self) -> None:
        artifact = self.put(b"fold me", mime="text/plain", metadata={"n": 1})
        digest = self.store.projection_digest()
        snapshot = self.store.snapshot()
        self.assertIn("artifacts", snapshot)
        row = next(item for item in snapshot["artifacts"] if item["id"] == artifact)
        self.assertEqual(row["sha256"], self.view(artifact)["sha256"])
        self.assertEqual(self.store.projection_digest(), digest, "the same log folds to the same digest")

    # ------------------------------------------------------------------ the honest list

    def test_c1_the_list_does_not_claim_an_unverified_integrity(self) -> None:
        artifact = self.put(b"listed")
        listing = self.client.get("/v1/artifacts").json()
        view = next(item for item in listing["artifacts"] if item["id"] == artifact)
        self.assertEqual(view["integrity"], "unchecked")
        self.assertFalse(view["verified"])
        self.assertEqual(self.view(artifact)["integrity"], "ok", "the detail endpoint is where it is claimed")

    def test_c2_an_unknown_artifact_is_a_404(self) -> None:
        self.assertEqual(self.client.get("/v1/artifacts/art_missing").status_code, 404)
        self.assertEqual(self.client.get("/v1/artifacts/art_missing/preview").status_code, 404)
        self.assertEqual(self.client.get("/v1/artifacts/art_missing/content").status_code, 404)


if __name__ == "__main__":
    unittest.main()

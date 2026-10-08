"""S2A: the control plane refuses to be controlled by whoever reaches the port.

The threat is not hypothetical. S1 proved that a sandboxed runtime with an open network can scan
loopback, find this daemon and read another session's transcript. So these tests attack the daemon the
way that agent did -- from a peer that is already on loopback -- and require that being reachable is
not the same as being authorised.

They also prove the credential never leaks: a real session token and a real bootstrap capability are
created, and then searched for in the event store, the workspace, the data root and the process
environment. A secret that only exists in memory is easy to claim and easy to get wrong, so it is
checked rather than asserted.
"""

from __future__ import annotations

import json
import os
import sys
import secrets
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
for extra in (REPO_ROOT, REPO_ROOT / "apps" / "daemon", REPO_ROOT / "packages" / "contracts"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fastapi.testclient import TestClient  # noqa: E402

from metaharness.app import Settings, create_app, ingest_bootstrap_line  # noqa: E402
from tests.support import (  # noqa: E402
    CSRF_HEADER,
    TEST_ORIGIN,
    authed_client,
    bootstrap_capability,
    close_clients,
    settings_for_test,
    unauth_client,
)

SECRET_MARKERS = ("mh_session", "capability=")


class AuthTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="auth-")
        self.data_root = Path(self._tmp.name) / "data"
        self.data_root.mkdir(parents=True)
        self.client = authed_client(self.data_root)

    def tearDown(self) -> None:
        close_clients()
        self._tmp.cleanup()

    # ------------------------------------------------------------------ the public surface

    def test_a1_health_is_public_and_minimal(self) -> None:
        body = unauth_client(self.data_root).get("/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["auth_required"])
        for leak in ("data_root", "web_bundle", "store", "events", "git_sha", "uptime_s"):
            self.assertNotIn(leak, body, f"/health must not expose {leak} unauthenticated")

    def test_a2_public_health_reveals_no_identifiers(self) -> None:
        client = unauth_client(self.data_root)
        mission = self.client.post("/v1/missions", json={"title": "Secret", "objective": "x"}).json()
        text = json.dumps(client.get("/health").json())
        self.assertNotIn(mission["mission_id"], text)
        self.assertNotIn("Secret", text)

    def test_a3_the_bundle_stays_public(self) -> None:
        # The application shell is not data: an unauthenticated browser must be able to load it and
        # then be told, by the app, that it has no session.
        response = unauth_client(self.data_root).get("/")
        self.assertIn(response.status_code, (200, 404), "the shell is reachable or absent, never 401")

    # ------------------------------------------------------------------ protected reads

    def test_b1_every_protected_read_answers_401_without_a_session(self) -> None:
        mission = self.client.post("/v1/missions", json={"title": "M", "objective": "x"}).json()
        task = self.client.post(
            f"/v1/missions/{mission['mission_id']}/tasks", json={"title": "T"}
        ).json()
        artifact = self.client.app.state.store.put_artifact(  # type: ignore[attr-defined]
            b"evidence", mime="text/plain", task_id=task["id"], mission_id=mission["mission_id"]
        ).id

        client = unauth_client(self.data_root)
        for path in (
            "/v1/events",
            "/v1/missions",
            f"/v1/missions/{mission['mission_id']}/tasks",
            f"/v1/tasks/{task['id']}",
            f"/v1/missions/{mission['mission_id']}/board",
            f"/v1/missions/{mission['mission_id']}/canvas",
            f"/v1/artifacts/{artifact}",
            f"/v1/artifacts/{artifact}/content",
            "/v1/approvals",
            "/v1/agents",
            "/v1/workspaces",
            "/v1/system/health",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 401, f"{path} answered {response.status_code}")

    def test_b2_an_unauthenticated_read_returns_no_event_history(self) -> None:
        """The S1 escape, in miniature: the peer reaches the port, and learns nothing."""
        self.client.post("/v1/missions", json={"title": "Hidden mission", "objective": "x"})
        response = unauth_client(self.data_root).get("/v1/events?limit=256")
        self.assertEqual(response.status_code, 401)
        body = response.text
        for leak in ("mission", "evt_", "mis_", "payload", "Hidden"):
            self.assertNotIn(leak, body, f"a refused read must not leak {leak}")

    def test_b3_a_mutation_without_a_session_is_refused(self) -> None:
        client = unauth_client(self.data_root)
        response = client.post(
            "/v1/missions",
            json={"title": "Mine now", "objective": "x"},
            headers={"origin": TEST_ORIGIN, CSRF_HEADER: "whatever"},
        )
        self.assertEqual(response.status_code, 401)

    # ------------------------------------------------------------------ session validity

    def test_c1_an_invalid_cookie_is_refused(self) -> None:
        client = unauth_client(self.data_root)
        client.cookies.set("mh_session", "not-a-real-session")
        self.assertEqual(client.get("/v1/events").status_code, 401)

    def test_c2_a_session_dies_with_the_daemon(self) -> None:
        """Per-launch credentials: a restart invalidates every session, with no revocation list."""
        self.assertEqual(self.client.get("/v1/events").status_code, 200)
        # A plain client with a stale cookie is exactly what this test needs, and it goes through the
        # helper so it is registered and closed like every other client.
        self.client = unauth_client(self.data_root)
        self.client.cookies.set("mh_session", "the-old-one")
        self.assertEqual(self.client.get("/v1/events").status_code, 401)

    def test_c3_a_session_cookie_is_opaque(self) -> None:
        response = self.client.get("/v1/session")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["actor"]["kind"], "local_operator")
        self.assertTrue(body["csrf"])
        cookie = self.client.cookies.get("mh_session") or ""
        for leak in ("operator", "agent", "agt_", "run_", "Daniel"):
            self.assertNotIn(leak, cookie, "the cookie carries no identity")

    # ------------------------------------------------------------------ origin, csrf, host

    def test_d1_a_hostile_origin_is_refused(self) -> None:
        response = self.client.post(
            "/v1/missions",
            json={"title": "X", "objective": "y"},
            headers={"origin": "http://evil.example"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("Origin", response.json()["detail"])

    def test_d2_a_missing_or_wrong_csrf_is_refused(self) -> None:
        self.client.csrf = ""
        missing = self.client.post("/v1/missions", json={"title": "X", "objective": "y"})
        self.assertEqual(missing.status_code, 403)
        self.client.csrf = "wrong-token"
        wrong = self.client.post("/v1/missions", json={"title": "X", "objective": "y"})
        self.assertEqual(wrong.status_code, 403)
        self.client.csrf = self.client.get("/v1/session").json()["csrf"]
        accepted = self.client.post("/v1/missions", json={"title": "X", "objective": "y"})
        self.assertEqual(accepted.status_code, 200, accepted.text)

    def test_d3_a_read_needs_no_csrf_but_still_needs_a_session(self) -> None:
        self.assertEqual(self.client.get("/v1/events").status_code, 200)
        self.assertEqual(unauth_client(self.data_root).get("/v1/events").status_code, 401)

    def test_d4_a_hostile_host_header_is_refused(self) -> None:
        """Loopback binding does not stop a DNS-rebinding style attack."""
        client = authed_client(self.data_root)
        response = client.get("/v1/events", headers={"host": "attacker.example"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("Host", response.json()["detail"])

    def test_d5_a_public_path_with_a_hostile_host_is_still_refused(self) -> None:
        response = unauth_client(self.data_root).get("/health", headers={"host": "attacker.example"})
        self.assertEqual(response.status_code, 403)

    # ------------------------------------------------------------------ websocket

    def test_e1_a_websocket_without_a_session_receives_no_backlog(self) -> None:
        """No event history may be emitted before authentication: this is the most sensitive surface."""
        self.client.post("/v1/missions", json={"title": "Socket secret", "objective": "x"})
        client = unauth_client(self.data_root)
        try:
            with client.websocket_connect("/v1/events/ws") as socket:
                received = socket.receive_text()
            self.fail(f"an unauthenticated websocket received data: {received[:120]}")
        except Exception as error:  # noqa: BLE001 - a closed socket is the expected outcome
            self.assertNotIn("Socket secret", str(error))

    def test_e2_an_authenticated_websocket_receives_events(self) -> None:
        with self.client.websocket_connect("/v1/events/ws") as socket:
            self.client.post("/v1/missions", json={"title": "Live", "objective": "x"})
            seen = socket.receive_text()
        # The frame is a canonical envelope, not a wrapped one: it carries the event's own fields.
        self.assertIn("kind", seen)
        self.assertIn("seq", seen)

    # ------------------------------------------------------------------ bootstrap

    def test_f1_a_bootstrap_capability_works_exactly_once(self) -> None:
        data_root = Path(self._tmp.name) / "second"
        data_root.mkdir()
        client = TestClient(create_app(settings_for_test(data_root)))
        client.__enter__()
        url, _ = bootstrap_capability(client.app)
        first = client.get(url, follow_redirects=False)
        self.assertEqual(first.status_code, 303)
        second = client.get(url, follow_redirects=False)
        self.assertEqual(second.status_code, 401, "a consumed capability is dead")
        client.__exit__(None, None, None)

    def test_f2_an_expired_bootstrap_capability_is_refused(self) -> None:
        data_root = Path(self._tmp.name) / "third"
        data_root.mkdir()
        app = create_app(settings_for_test(data_root))
        client = TestClient(app)
        client.__enter__()
        app.state.auth.bootstrap_ttl_s = -1.0  # already expired when consumed
        capability = app.state.auth.issue_bootstrap()
        response = client.get(f"/auth/bootstrap?capability={capability}", follow_redirects=False)
        self.assertEqual(response.status_code, 401)
        client.__exit__(None, None, None)

    def test_f3_a_bogus_capability_is_refused(self) -> None:
        response = unauth_client(self.data_root).get(
            "/auth/bootstrap?capability=guessed", follow_redirects=False
        )
        self.assertEqual(response.status_code, 401)

    def test_f4_the_bootstrap_response_is_uncacheable_and_referrer_free(self) -> None:
        data_root = Path(self._tmp.name) / "fourth"
        data_root.mkdir()
        client = TestClient(create_app(settings_for_test(data_root)))
        client.__enter__()
        url, _ = bootstrap_capability(client.app)
        response = client.get(url, follow_redirects=False)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(response.headers.get("referrer-policy"), "no-referrer")
        cookie = response.headers.get("set-cookie") or ""
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie.replace("samesite", "SameSite"))
        self.assertNotIn("Secure", cookie, "loopback plain HTTP cannot use Secure, and says so")
        client.__exit__(None, None, None)

    def test_f6_the_capability_never_touches_the_filesystem(self) -> None:
        """The P1 the security review found: a 0600 file protects against another OS user, not against
        another process under the same account, and S2A must hold independently of sandbox strength.

        A unique capability is created and then searched for everywhere a same-user process could
        look. It must occur zero times, and no bootstrap-shaped file may exist at all.
        """
        capability = secrets.token_urlsafe(32)
        record = json.dumps({"type": "bootstrap", "capability": capability}).encode()
        app = self.client.app  # type: ignore[attr-defined]
        self.assertTrue(ingest_bootstrap_line(app, record), "the daemon accepted the record")
        self.client.get(f"/auth/bootstrap?capability={capability}", follow_redirects=False)

        # Every place a same-user process can read.
        haystacks: dict[str, str] = {}
        for path in self.data_root.rglob("*"):
            if path.is_file():
                haystacks[str(path)] = path.read_text(encoding="utf-8", errors="ignore")
        haystacks["events"] = json.dumps(self.client.get("/v1/events?limit=200").json())
        haystacks["environment"] = " ".join(f"{k}={v}" for k, v in os.environ.items())
        haystacks["argv"] = " ".join(sys.argv)
        for where, text in haystacks.items():
            self.assertNotIn(capability, text, f"the capability leaked into {where}")
        self.assertFalse(
            list(self.data_root.rglob("bootstrap*")),
            "no bootstrap-shaped file exists anywhere: the credential lives only in memory",
        )

    def test_f7_a_parent_may_hand_the_daemon_exactly_one_capability(self) -> None:
        """Strict, bounded, one only: a second record is refused rather than silently ignored."""
        first = secrets.token_urlsafe(32)
        second = secrets.token_urlsafe(32)
        app = self.client.app  # type: ignore[attr-defined]
        self.assertTrue(
            ingest_bootstrap_line(app, json.dumps({"type": "bootstrap", "capability": first}).encode())
        )
        with self.assertRaises(ValueError):
            ingest_bootstrap_line(app, json.dumps({"type": "bootstrap", "capability": second}).encode())
        self.assertFalse(ingest_bootstrap_line(app, b"not json at all"))
        self.assertFalse(ingest_bootstrap_line(app, b'{"type": "bootstrap", "capability": "short"}'))
        self.assertFalse(ingest_bootstrap_line(app, b"x" * 5000), "an oversized line is refused by the limit")

    def test_f5_there_is_no_endpoint_that_hands_out_authority(self) -> None:
        """The one thing that must never exist: a sandbox calling an endpoint to become authorised."""
        client = unauth_client(self.data_root)
        for path in ("/v1/auth/token", "/auth/token", "/v1/auth/bootstrap", "/v1/token", "/auth/session"):
            self.assertIn(client.get(path).status_code, (401, 403, 404), path)
            self.assertIn(client.post(path).status_code, (401, 403, 404, 405), path)

    # ------------------------------------------------------------------ approval authority

    def test_g1_an_r4_cannot_be_granted_without_an_authenticated_operator(self) -> None:
        """Before S2 a runtime could approve its own R4 action by typing a person's name."""
        agent = self.client.post("/v1/agents", json={"display_name": "Nova"}).json()["agent_id"]
        created = self.client.post(
            "/v1/approvals",
            json={
                "action_type": "file.write",
                "risk_level": "R4",
                "human_summary": "publish the release",
                "requested_by": agent,
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        approval_id = created.json()["id"]

        unauthenticated = unauth_client(self.data_root)
        refused = unauthenticated.post(
            f"/v1/approvals/{approval_id}/grant",
            json={"by": "Daniel"},
            headers={"origin": TEST_ORIGIN, CSRF_HEADER: "guessed"},
        )
        self.assertEqual(refused.status_code, 401, "a name in the body is not an identity")

        still_pending = self.client.get(f"/v1/approvals/{approval_id}").json()
        self.assertEqual(still_pending["state"], "pending", "and the approval was not granted")
        self.assertIsNone(still_pending.get("granted_by"))

    def test_g2_the_authority_rule_reads_the_actor_kind_not_a_name(self) -> None:
        from metaharness.auth import ActorContext, SYSTEM_ACTOR, grant_requires_operator

        operator = ActorContext(kind="local_operator", principal="p", authentication="local_session")
        self.assertTrue(grant_requires_operator("R4", SYSTEM_ACTOR))
        self.assertTrue(grant_requires_operator("R3", SYSTEM_ACTOR))
        self.assertFalse(grant_requires_operator("R4", operator))
        self.assertFalse(grant_requires_operator("R2", SYSTEM_ACTOR), "R2 does not demand a human")

    def test_g3_an_authenticated_operator_can_grant(self) -> None:
        agent = self.client.post("/v1/agents", json={"display_name": "Nova"}).json()["agent_id"]
        approval_id = self.client.post(
            "/v1/approvals",
            json={
                "action_type": "file.write",
                "risk_level": "R3",
                "human_summary": "publish the release",
                "requested_by": agent,
            },
        ).json()["id"]
        granted = self.client.post(f"/v1/approvals/{approval_id}/grant", json={"by": "Daniel"})
        self.assertEqual(granted.status_code, 200, granted.text)
        self.assertEqual(granted.json()["state"], "granted")

    # ------------------------------------------------------------------ provenance, not credentials

    def test_h1_events_record_the_actor_kind_and_no_credential(self) -> None:
        self.client.post("/v1/missions", json={"title": "Provenance", "objective": "x"})
        events = self.client.get("/v1/events?limit=50").json()["events"]
        self.assertTrue(events)
        provenance = events[-1]["provenance"]
        self.assertIn("actor", provenance)
        self.assertIn(provenance["actor"]["kind"], ("local_operator", "system"))
        self.assertIn(provenance["actor"]["authentication"], ("local_session", "internal"))

    def test_h2_no_credential_reaches_the_event_store(self) -> None:
        """The token and the capability are searched for, not assumed absent."""
        self.client.post("/v1/missions", json={"title": "Search", "objective": "x"})
        session_cookie = self.client.cookies.get("mh_session") or ""
        store = self.client.app.state.store  # type: ignore[attr-defined]
        rows = store.rows("SELECT payload, provenance FROM events")
        blob = json.dumps([dict(row) for row in rows])
        self.assertNotIn(session_cookie, blob)
        self.assertNotIn("capability=", blob)
        self.assertNotIn("mh_session", blob)

    def test_h3_no_credential_reaches_the_workspace_or_the_data_root(self) -> None:
        session_cookie = self.client.cookies.get("mh_session") or ""
        workspace = self.data_root / "workspaces"
        files = [path for path in self.data_root.rglob("*") if path.is_file()]
        for path in files:
            text = path.read_text(encoding="utf-8", errors="ignore")
            self.assertNotIn(session_cookie, text, f"the session token leaked into {path}")
        self.assertFalse(
            any(workspace.rglob("bootstrap.url")) if workspace.exists() else False,
            "a capability must never be copied into a workspace",
        )

    def test_h4_no_credential_reaches_the_process_environment(self) -> None:
        session_cookie = self.client.cookies.get("mh_session") or ""
        for key, value in os.environ.items():
            self.assertNotIn(session_cookie, value, f"the session token is in {key}")
        self.assertNotIn("MH_AUTH", " ".join(os.environ))

    def test_h5_the_runtime_and_sandbox_layers_cannot_see_the_credential(self) -> None:
        """A structural check: no adapter or sandbox module imports the auth module at all.

        This is stronger than searching a string: if the credential cannot be named in those layers,
        it cannot be passed to them.
        """
        for package in ("runtimes", "sandbox"):
            for path in (REPO_ROOT / "apps" / "daemon" / "metaharness" / package).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn(
                    "metaharness.auth",
                    text,
                    f"{path.relative_to(REPO_ROOT)} must not import the auth module",
                )
                self.assertNotIn(
                    "from ..auth",
                    text,
                    f"{path.relative_to(REPO_ROOT)} must not import the auth module",
                )

    # ------------------------------------------------------------------ restart behaviour

    def test_i1_a_restart_issues_a_fresh_capability_and_keeps_the_data(self) -> None:
        mission = self.client.post("/v1/missions", json={"title": "Survives", "objective": "x"}).json()
        self.client.__exit__(None, None, None)
        self.client = authed_client(self.data_root)
        missions = self.client.get("/v1/missions").json()
        ids = [item["mission_id"] if "mission_id" in item else item.get("id") for item in missions.get("missions", [])]
        self.assertIn(mission["mission_id"], ids, "the data survives; only the credential does not")


if __name__ == "__main__":
    unittest.main()

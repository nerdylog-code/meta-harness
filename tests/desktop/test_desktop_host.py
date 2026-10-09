"""WP-007 acceptance, the part that needs no Rust.

A1, A4 and A7 need a built shell; A2, A3, A5 and A6 do not, and those are the ones that decide
whether the shell is a shell or a second implementation of the daemon's lifecycle. Keeping them
here means they run in the CI matrix on both OSes, where no Rust toolchain exists.

    A2  port collision   -- a daemon already listening is attached to, never double-spawned
    A3  shutdown         -- closing the shell's handle kills the child tree, zero orphans
    A5  headless         -- nothing here needs Tauri, and dev.py still works without it
    A6  secret handling  -- the renderer is granted no capability, and the child gets no secret
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import socket
import select
import subprocess

import psutil
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "apps" / "desktop" / "host"))
import desktop_host as host  # noqa: E402

HOST_SCRIPT = REPO / "apps" / "desktop" / "host" / "desktop_host.py"
TAURI_DIR = REPO / "apps" / "desktop" / "src-tauri"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def read_line_within(stream, timeout: float) -> str | None:
    """Read one line, with a deadline that can actually fire.

    ``readline()`` blocks until a line arrives, so a deadline checked around it is never reached: when
    the writer says nothing, the check never runs. The deadline has to bound the read itself. That is
    not a detail -- an earlier version of this file blocked here forever, the host and the daemon it
    supervises stayed alive, and a re-run of the test exhausted the machine.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        ready, _, _ = select.select([stream], [], [], 0.25)
        if not ready:
            continue
        line = stream.readline()
        if line:
            return line
    return None


def wait_line(process: subprocess.Popen, timeout: float = 60.0) -> dict:
    """Read the host's first JSON line. The host writes exactly one per state change."""
    assert process.stdout is not None
    line = read_line_within(process.stdout, timeout)
    if line:
        return json.loads(line)
    if process.poll() is not None:
        raise AssertionError(f"host exited ({process.returncode}) without emitting a status line")
    raise AssertionError("host emitted no status line in time")


def kill_tree(process: subprocess.Popen) -> None:
    """Kill the host AND the daemon it spawned.

    The host supervises a real daemon, so terminating the host alone can leave the daemon running and
    repeated runs stack up processes.
    """
    try:
        parent = psutil.Process(process.pid)
    except psutil.Error:
        return
    for child in parent.children(recursive=True):
        try:
            child.kill()
        except psutil.Error:
            pass
    try:
        parent.kill()
    except psutil.Error:
        pass


def scan_for(root: Path, needle: str, *, max_file_bytes: int = 8 * 1024 * 1024, max_total_bytes: int = 64 * 1024 * 1024) -> str | None:
    """Return the first path containing ``needle``, reading one file at a time.

    A canary check answers a yes/no question; it must not read unbounded data into memory to do it.
    The first version of this test joined every file under the checkout and all of /tmp into single
    strings, which is why it could take the machine down.
    """
    if not root.exists():
        return None
    needle_bytes = needle.encode()
    seen = 0
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
        except OSError:
            continue
        if size > max_file_bytes:
            continue
        seen += size
        if seen > max_total_bytes:
            return None
        try:
            if needle_bytes in path.read_bytes():
                return str(path)
        except OSError:
            continue
    return None


def run_host(args: list[str], timeout: float = 60.0) -> dict:
    process = subprocess.run(
        [sys.executable, str(HOST_SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    lines = [line for line in process.stdout.splitlines() if line.strip()]
    if not lines:
        raise AssertionError(f"no output: rc={process.returncode} stderr={process.stderr[-800:]}")
    return json.loads(lines[-1])


class FakeDaemon:
    """A stand-in that answers /health the way the real daemon does, for A2 only."""

    def __init__(self) -> None:
        self.port = free_port()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - stdlib naming
                if self.path == "/health":
                    # The public health surface is minimal after S2: identity and auth_required only.
                    # A fixture still serving the old shape would not be recognised as our daemon.
                    body = json.dumps({
                        "status": "ok",
                        "service": "meta-harness",
                        "version": "0.2.0.dev0",
                        "auth_required": True,
                    }).encode()
                    self.send_response(200)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def __enter__(self) -> "FakeDaemon":
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


class PortCollisionTest(unittest.TestCase):
    """A2 — with a daemon already on the port, attach instead of double-starting."""

    def test_a2_a_served_port_is_refused_not_attached(self) -> None:
        """A running daemon requires authentication, and this host did not mint its capability.

        Before S2 the host attached to whatever was listening. It now refuses: that daemon's bootstrap
        capability is single-use and may already be consumed, so attaching would mean falling back to
        an unauthenticated API or reusing a token from a location a runtime could also read. The safety
        property the old test protected -- never spawning a second daemon -- still holds, and the
        refusal is explicit rather than silent.
        """
        with FakeDaemon() as fake:
            # A real launch, not a probe: probe-only is a diagnostic that reports attached/free, and
            # the refusal belongs to the launch decision, which is where it protects the operator.
            result = run_host(["--port", str(fake.port)])
            self.assertEqual(result["status"], "attach_refused", result)
            self.assertEqual(result["port"], fake.port)
            self.assertIn("authenticat", json.dumps(result).lower(), result)

    def test_a2_an_unserved_port_reports_free(self) -> None:
        port = free_port()
        result = run_host(["--port", str(port), "--probe-only"])
        self.assertEqual(result["status"], "free", result)

    def test_a2_a_foreign_server_is_not_mistaken_for_the_daemon(self) -> None:
        """Something else on the port must not be adopted as our daemon."""

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                self.send_response(200)
                self.send_header("content-length", "2")
                self.end_headers()
                self.wfile.write(b"hi")

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
                pass

        port = free_port()
        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = run_host(["--port", str(port), "--probe-only"])
            self.assertEqual(result["status"], "free", result)
        finally:
            server.shutdown()
            server.server_close()

    def test_a2_a_non_loopback_bind_is_refused(self) -> None:
        process = subprocess.run(
            [sys.executable, str(HOST_SCRIPT), "--host", "0.0.0.0"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        result = json.loads(process.stdout.strip().splitlines()[-1])
        self.assertEqual(result["status"], "failed")
        self.assertIn("loopback-only", result["reason"])
        self.assertEqual(process.returncode, 2)



class RedactionTest(unittest.TestCase):
    """Ordinary output must be unable to emit a live credential; the private pipe is the one exception.

    Every assertion here fails against the previous implementation, which is the point. A key-aware rule
    alone does not cover a capability that appears inside an unrelated string, and a suite that never
    tried `{"capability": SECRET}` was green while the value leaked.
    """

    CANARY = "CANARY-abcdefghijklmnopqrstuvwxyz012345"

    def setUp(self) -> None:
        host.clear_secrets()
        host.register_secret(self.CANARY)

    def tearDown(self) -> None:
        host.clear_secrets()

    def test_b1_a_sensitive_key_is_redacted_outright_and_never_recursed_into(self) -> None:
        cases = {
            "dict": {"capability": self.CANARY},
            "nested dict": {"nested": {"capability": self.CANARY}},
            "nested list": {"nested": [{"capability": self.CANARY}, "runtime error contained " + self.CANARY]},
            "url": "http://127.0.0.1:8765/auth/bootstrap?capability=" + self.CANARY,
            "json-shaped string": '{"capability":"' + self.CANARY + '"}',
            "unrelated error text": {"error": "failed while using " + self.CANARY},
        }
        for name, value in cases.items():
            with self.subTest(case=name):
                rendered = json.dumps(host.redact(value))
                self.assertNotIn(self.CANARY, rendered, f"{name} still carries the capability")
                self.assertIn("REDACTED", rendered, f"{name} was not redacted at all")

    def test_b2_ordinary_emit_and_log_never_carry_the_capability(self) -> None:
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as temp:
            log_path = Path(temp) / "shell.log"
            payload = {"error": self.CANARY, "capability": self.CANARY}
            captured = io.StringIO()
            with open(log_path, "w", encoding="utf-8") as log, redirect_stdout(captured):
                host.emit_status(log, payload)
            stdout_text = captured.getvalue()
            log_text = log_path.read_text()
        self.assertNotIn(self.CANARY, stdout_text, "the capability reached stdout")
        self.assertNotIn(self.CANARY, log_text, "the capability reached the log")
        self.assertIn("REDACTED", stdout_text)
        self.assertIn("REDACTED", log_text)

    def test_b3_captured_daemon_output_is_redacted_before_the_log_sees_it(self) -> None:
        # The daemon tail is an ordinary diagnostic path and goes through the same boundary.
        rendered = host.redact("daemon stderr: boom while handling " + self.CANARY)
        self.assertNotIn(self.CANARY, rendered)
        self.assertIn("REDACTED", rendered)

    def test_b4_the_private_pipe_is_the_one_place_the_credential_may_travel(self) -> None:
        """The exception is deliberate, and it is pinned so nobody "fixes" the handover away."""
        import io
        from contextlib import redirect_stdout

        url = "http://127.0.0.1:8765/auth/bootstrap?capability=" + self.CANARY
        private = io.StringIO()
        stdout = io.StringIO()
        original = sys.stderr
        sys.stderr = private
        try:
            with redirect_stdout(stdout):
                host.emit_private({"type": "bootstrap", "url": url})
        finally:
            sys.stderr = original
        self.assertIn(self.CANARY, private.getvalue(), "the private pipe must carry the capability")
        self.assertNotIn(self.CANARY, stdout.getvalue(), "ordinary stdout must not")

    def test_b5_clearing_the_registry_leaves_ordinary_text_alone(self) -> None:
        host.clear_secrets()
        text = "ordinary unrelated diagnostic text"
        self.assertEqual(host.redact(text), text)


class ShutdownTest(unittest.TestCase):
    """A3 — the shell owns the daemon: close the handle, the tree dies, nothing survives."""

    def test_a3_closing_stdin_stops_the_daemon_and_leaves_no_orphan(self) -> None:
        port = free_port()
        data_dir = REPO / ".pytest-desktop-a3"
        process = subprocess.Popen(
            [
                sys.executable,
                str(HOST_SCRIPT),
                "--port",
                str(port),
                "--data-dir",
                str(data_dir),
                "--timeout",
                "45",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            started = wait_line(process)
            self.assertEqual(started["status"], "started", started)
            daemon_pid = started["pid"]
            self.assertTrue(psutil.pid_exists(daemon_pid))
            self.assertEqual(started["port"], port)

            assert process.stdin is not None
            process.stdin.close()  # the window closed
            stopped = wait_line(process)
            self.assertEqual(stopped["status"], "stopped", stopped)
            self.assertTrue(stopped["orphan_check"], stopped)
            self.assertEqual(stopped["survivors"], [])

            deadline = time.time() + 15
            while time.time() < deadline and psutil.pid_exists(daemon_pid):
                time.sleep(0.2)
            self.assertFalse(psutil.pid_exists(daemon_pid), f"daemon {daemon_pid} survived the shell")
            process.wait(timeout=20)
        finally:
            if process.poll() is None:
                process.kill()
            if data_dir.exists():
                import shutil

                shutil.rmtree(data_dir, ignore_errors=True)


class HeadlessIndependenceTest(unittest.TestCase):
    """A5 — the shell is optional: no Tauri needed for anything in this file."""

    def test_a5_the_host_never_imports_tauri(self) -> None:
        source = HOST_SCRIPT.read_text()
        self.assertNotIn("import tauri", source)
        self.assertNotIn("src-tauri", source)
        # and it really is importable on its own
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import sys; sys.path.insert(0, {str(HOST_SCRIPT.parent)!r}); "
                "import desktop_host as h; print(h.DEFAULT_PORT)",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(probe.returncode, 0, probe.stderr[-600:])
        self.assertIn("8765", probe.stdout)

    def test_a5_dev_py_still_works_without_a_desktop(self) -> None:
        dev = REPO / "scripts" / "dev.py"
        self.assertTrue(dev.exists())
        probe = subprocess.run(
            [sys.executable, str(dev), "--help"], capture_output=True, text=True, timeout=60
        )
        self.assertEqual(probe.returncode, 0, probe.stderr[-600:])
        self.assertIn("--no-browser", probe.stdout)

    def test_a5_the_web_app_does_not_depend_on_the_shell(self) -> None:
        for path in (REPO / "apps" / "web" / "src").rglob("*.ts*"):
            text = path.read_text()
            self.assertNotIn("tauri", text.lower(), f"{path} reaches for the desktop shell")


class SecretHandlingTest(unittest.TestCase):
    """A6 — the renderer gets no capability and the daemon gets no secret."""

    def test_a6_the_child_environment_carries_no_secret(self) -> None:
        result = run_host(["--port", str(free_port()), "--print-child-env"])
        self.assertEqual(result["status"], "child_env")
        keys = set(result["env"])
        self.assertTrue(keys <= {"METAHARNESS_HOST", "METAHARNESS_PORT", "METAHARNESS_DESKTOP", "METAHARNESS_DATA_DIR"}, keys)
        for value in result["env"].values():
            self.assertNotIn("token", value.lower())
            self.assertNotIn("secret", value.lower())

    def test_a6_the_shell_capabilities_grant_nothing_to_the_renderer(self) -> None:
        if not TAURI_DIR.exists():
            self.skipTest("the Tauri shell is not in this checkout")
        capabilities = list((TAURI_DIR / "capabilities").glob("*.json"))
        self.assertTrue(capabilities, "the shell must declare its capabilities explicitly")
        for path in capabilities:
            body = json.loads(path.read_text())
            permissions = body.get("permissions", [])
            for permission in permissions:
                name = permission if isinstance(permission, str) else json.dumps(permission)
                for forbidden in ("shell:", "fs:", "http:", "process:", "path:allow"):
                    self.assertNotIn(forbidden, name, f"{path.name} grants {name}")

    def test_parent_pipe_proves_bootstrap_end_to_end_and_never_leaks(self) -> None:
        import psutil

        with tempfile.TemporaryDirectory(prefix="mh-bootstrap-proof-") as temp:
            root = Path(temp) / "data"
            log = root / "logs" / "desktop-shell.log"
            port = free_port()
            env = dict(os.environ)
            env.pop("METAHARNESS_DATA_DIR", None)
            host = subprocess.Popen(
                [sys.executable, str(HOST_SCRIPT), "--port", str(port), "--data-dir", str(root), "--log", str(log), "--timeout", "45"],
                cwd=REPO, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
            try:
                status = wait_line(host, timeout=60)
                self.assertEqual(status["status"], "started", status)
                self.assertEqual(status["port"], port)
                assert host.stderr is not None
                raw_private = read_line_within(host.stderr, timeout=30)
                self.assertIsNotNone(raw_private, "the host never emitted its private message")
                private_line = (raw_private or "").rstrip("\r\n")
                self.assertTrue(private_line.startswith("\x1e"), repr(private_line))
                private = json.loads(private_line[1:])
                self.assertEqual(private.get("type"), "bootstrap")
                url = private.get("url", "")
                match = re.fullmatch(rf"http://127\.0\.0\.1:{port}/auth/bootstrap\?capability=([A-Za-z0-9_-]{{32,}})", url)
                self.assertIsNotNone(match, url)
                capability = match.group(1)
                self.assertNotIn(capability, json.dumps(status))
                self.assertNotIn(url, json.dumps(status))

                cookie_processor = urllib.request.HTTPCookieProcessor()
                opener = urllib.request.build_opener(cookie_processor)
                with opener.open(url, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                cookie_obj = next(c for c in cookie_processor.cookiejar if c.name == "mh_session")
                self.assertTrue(cookie_obj.has_nonstandard_attr("HttpOnly"))
                request = urllib.request.Request(f"http://127.0.0.1:{port}/v1/session", headers={"Cookie": f"mh_session={cookie_obj.value}"})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertEqual(response.status, 200)
                    session = json.loads(response.read())
                self.assertEqual(session["actor"]["authentication"], "local_session")
                with self.assertRaises(urllib.error.HTTPError) as reused:
                    urllib.request.urlopen(url, timeout=5)
                self.assertEqual(reused.exception.code, 401)

                self.assertFalse((root / "bootstrap.url").exists())
                self.assertFalse(any(p.name == "bootstrap_file" for p in root.rglob("*")))
                credential_files = [p for p in root.rglob("*") if p.is_file() and any(x in p.name.lower() for x in ("bootstrap", "credential", "capability"))]
                self.assertEqual(credential_files, [])

                daemon = psutil.Process(status["pid"])
                # One file at a time, over surfaces belonging to this run.
                filesystem_surfaces = {
                    "data root": root,
                    "repository checkout": REPO,
                    "artifacts": root / "artifacts",
                    "canonical store": root / "data",
                }
                for name, where in filesystem_surfaces.items():
                    with self.subTest(surface=name):
                        for needle, label in ((capability, "capability"), (url, "URL")):
                            found = scan_for(where, needle)
                            self.assertIsNone(found, f"{label} leaked into {name} at {found}")
                with self.subTest(surface="desktop-shell.log"):
                    text = log.read_text(errors="ignore") if log.exists() else ""
                    self.assertNotIn(capability, text, "capability leaked into desktop-shell.log")
                    self.assertNotIn(url, text, "URL leaked into desktop-shell.log")
                with self.subTest(surface="daemon ordinary log"):
                    for path in (root / "logs").rglob("*"):
                        if path.is_file():
                            text = path.read_text(errors="ignore")
                            self.assertNotIn(capability, text, f"capability leaked into {path}")
                            self.assertNotIn(url, text, f"URL leaked into {path}")
                vector_surfaces = {
                    "desktop_host argv": "\0".join(host.args),
                    "daemon argv": "\0".join(daemon.cmdline()),
                    "desktop_host environment": "\n".join(f"{k}={v}" for k, v in env.items()),
                    "daemon environment": "\n".join(f"{k}={v}" for k, v in daemon.environ().items()),
                }
                for name, content in vector_surfaces.items():
                    with self.subTest(surface=name):
                        self.assertNotIn(capability, content, f"capability leaked to {name}")
                        self.assertNotIn(url, content, f"URL leaked to {name}")
                self.assertIn(capability, private_line)
                self.assertIn(url, private_line)
            finally:
                if host.stdin and not host.stdin.closed:
                    host.stdin.close()
                try:
                    host.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    pass
                kill_tree(host)
                if host.stderr:
                    host.stderr.close()

    def test_a6_the_webview_configuration_does_not_inject_a_secret(self) -> None:
        if not TAURI_DIR.exists():
            self.skipTest("the Tauri shell is not in this checkout")
        config = json.loads((TAURI_DIR / "tauri.conf.json").read_text())
        text = json.dumps(config).lower()
        for word in ("token", "secret", "apikey", "api_key", "password"):
            self.assertNotIn(word, text, f"tauri.conf.json mentions {word}")
        self.assertFalse(config.get("app", {}).get("withGlobalTauri", False), "no global IPC surface")



if __name__ == "__main__":
    unittest.main()

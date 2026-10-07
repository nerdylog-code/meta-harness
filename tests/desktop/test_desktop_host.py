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
import socket
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HOST_SCRIPT = REPO / "apps" / "desktop" / "host" / "desktop_host.py"
TAURI_DIR = REPO / "apps" / "desktop" / "src-tauri"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_line(process: subprocess.Popen, timeout: float = 60.0) -> dict:
    """Read the host's first JSON line. The host writes exactly one per state change."""
    assert process.stdout is not None
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = process.stdout.readline()
        if line:
            return json.loads(line)
        if process.poll() is not None:
            raise AssertionError(f"host exited ({process.returncode}) without emitting a status line")
    raise AssertionError("host emitted no status line in time")


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


class ShutdownTest(unittest.TestCase):
    """A3 — the shell owns the daemon: close the handle, the tree dies, nothing survives."""

    def test_a3_closing_stdin_stops_the_daemon_and_leaves_no_orphan(self) -> None:
        import psutil

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

"""Integration: a real uvicorn server, a real websocket client, real frames.

Nothing here is mocked. The suite starts the daemon on a free port in a thread
and drives the canonical event stream over the wire, which is the only way the
WP-002 gate ("WS /events/ws delivers at least one system.* frame") means
anything.
"""

from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "apps" / "daemon") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "apps" / "daemon"))

import uvicorn  # noqa: E402
import websockets  # noqa: E402

from metaharness.app import Settings, create_app  # noqa: E402

BOOT_TIMEOUT_S = 25.0
READ_TIMEOUT_S = 15.0


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LiveDaemon:
    """Context manager running a real daemon for the duration of a test."""

    def __init__(self, data_dir: str) -> None:
        self.port = free_port()
        self.data_dir = data_dir
        self._server: uvicorn.Server | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "LiveDaemon":
        settings = Settings(host="127.0.0.1", port=self.port, data_dir=self.data_dir, serve_web=False)
        config = uvicorn.Config(create_app(settings), host="127.0.0.1", port=self.port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()
        deadline = time.time() + BOOT_TIMEOUT_S
        while time.time() < deadline:
            if self._server.started:
                return self
            if self._thread.is_alive() is False:
                raise RuntimeError("daemon thread died during startup")
            time.sleep(0.1)
        raise TimeoutError(f"daemon did not start within {BOOT_TIMEOUT_S}s")

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=15)
            # On Windows a daemon thread that outlives its shutdown keeps the store file
            # open, and the failure surfaces later as a confusing PermissionError from
            # TemporaryDirectory. Report it here, where the cause is visible.
            if self._thread.is_alive():
                raise RuntimeError(
                    "the daemon did not shut down within 15s; the store file is still held open"
                )

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/v1/events/ws"

    @property
    def alias_ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/events/ws"


class TestEventStream(unittest.TestCase):
    def test_stream_delivers_system_frames(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, LiveDaemon(tmp) as daemon:
            async def scenario() -> list[dict]:
                frames: list[dict] = []
                async with websockets.connect(daemon.ws_url) as socket_:
                    for _ in range(3):
                        raw = await asyncio.wait_for(socket_.recv(), READ_TIMEOUT_S)
                        frames.append(json.loads(raw))
                return frames

            frames = asyncio.run(scenario())

        self.assertTrue(frames, "no frames received")
        for frame in frames:
            self.assertTrue(frame["kind"].startswith("system."), frame)
            self.assertIn("provenance", frame)
        kinds = [frame["kind"] for frame in frames]
        self.assertIn("system.daemon.started", kinds, "the backlog must include daemon startup")

    def test_stream_receives_live_events_after_connect(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, LiveDaemon(tmp) as daemon:
            async def scenario() -> dict:
                async with websockets.connect(daemon.ws_url) as socket_:
                    # drain the backlog first
                    while True:
                        frame = json.loads(await asyncio.wait_for(socket_.recv(), READ_TIMEOUT_S))
                        if frame["kind"] == "system.session.attached":
                            break
                    return frame

            frame = asyncio.run(scenario())

        self.assertEqual(frame["kind"], "system.session.attached")
        self.assertGreaterEqual(frame["payload"]["subscribers"], 1)

    def test_compatibility_alias_route_streams_too(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, LiveDaemon(tmp) as daemon:
            async def scenario() -> str:
                async with websockets.connect(daemon.alias_ws_url) as socket_:
                    frame = json.loads(await asyncio.wait_for(socket_.recv(), READ_TIMEOUT_S))
                return frame["kind"]

            kind = asyncio.run(scenario())

        self.assertTrue(kind.startswith("system."), kind)

    def test_http_health_answers_while_streaming(self) -> None:
        import httpx

        with tempfile.TemporaryDirectory() as tmp, LiveDaemon(tmp) as daemon:
            with httpx.Client(base_url=f"http://127.0.0.1:{daemon.port}", timeout=10) as client:
                self.assertEqual(client.get("/health").json()["status"], "ok")
                self.assertEqual(client.get("/version").json()["api_version"], "v1")


if __name__ == "__main__":
    unittest.main()

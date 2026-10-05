"""The daemon HTTP + WebSocket application (WP-002 skeleton).

Surface, deliberately minimal:

  GET  /health          liveness + identity + resolved layout
  GET  /version         build/runtime identity
  GET  /v1/events       bounded recent-event read (polling fallback)
  WS   /v1/events/ws    live event stream (canonical path, BOOK §64)
  WS   /events/ws       compatibility alias for the WP-002 gate wording
  GET  /                the built web bundle, when one exists

Local-only by construction: the app rejects any request whose peer is not
loopback. The per-launch secret of BOOK §65 arrives with the UI session
(WP-006) — until then, "not reachable from off-host" is the enforced property,
and `docs/architecture/ARCHITECTURE.md` records that gap rather than implying
an auth story the skeleton does not have.
"""

from __future__ import annotations

import time
from collections.abc import Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from . import paths
from .events import EventBus
from .version import VERSION, git_sha, runtime_info

LOOPBACK_HOSTS = {"127.0.0.1", "::1", "::ffff:127.0.0.1", "localhost", "testclient"}
DEFAULT_PORT = 8765
BACKLOG_LIMIT = 50
WEB_BUNDLE_CANDIDATES = ("apps/web/dist", "apps/web/build")


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = DEFAULT_PORT
    data_dir: str | None = None
    web_root: str | None = None
    serve_web: bool = True

    def resolved_data_dir(self) -> Path:
        return paths.data_root(self.data_dir)

    def resolved_web_root(self) -> Path | None:
        if self.web_root:
            candidate = Path(self.web_root).expanduser()
            return candidate if candidate.is_dir() else None
        if not self.serve_web:
            return None
        repo = paths.find_repo_root()
        if repo is None:
            return None
        for rel in WEB_BUNDLE_CANDIDATES:
            candidate = repo / rel
            if (candidate / "index.html").is_file():
                return candidate
        return None


class LoopbackOnlyMiddleware:
    """Pure-ASGI guard: non-loopback peers are refused on http and websocket.

    Written as raw ASGI rather than ``BaseHTTPMiddleware`` because the latter
    does not cover websocket scopes, and the event stream is a websocket.
    """

    def __init__(self, app, allowed: "Collection[str]" = frozenset(LOOPBACK_HOSTS)) -> None:
        self.app = app
        self.allowed = frozenset(allowed)

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            client = scope.get("client")
            host = client[0] if client else None
            if host not in self.allowed:
                if scope["type"] == "websocket":
                    await send({"type": "websocket.close", "code": 1008})
                else:
                    response = JSONResponse(
                        {"error": "forbidden", "detail": "the daemon serves loopback clients only"},
                        status_code=403,
                    )
                    await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    bus = EventBus()
    started_at = time.time()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        data_root = paths.ensure_layout(settings.data_dir)
        app.state.data_root = data_root
        bus.publish(
            "system.daemon.started",
            {
                "version": VERSION,
                "git_sha": git_sha(),
                "host": settings.host,
                "port": settings.port,
                "data_root": str(data_root),
            },
            method="measured",
        )
        try:
            yield
        finally:
            bus.publish("system.daemon.stopping", {"reason": "shutdown"}, method="measured")

    app = FastAPI(
        title="Meta-Harness",
        version=VERSION,
        description="Local-first control plane for heterogeneous AI agents (WP-002 skeleton).",
        lifespan=lifespan,
    )
    app.state.bus = bus
    app.state.settings = settings
    app.state.started_at = started_at
    app.add_middleware(LoopbackOnlyMiddleware)

    def health_payload() -> dict[str, Any]:
        web_root = settings.resolved_web_root()
        return {
            "status": "ok",
            "service": "meta-harness",
            "version": VERSION,
            "git_sha": git_sha(),
            "uptime_s": round(time.time() - started_at, 3),
            "data_root": str(settings.resolved_data_dir()),
            "web_bundle": str(web_root) if web_root else None,
            "events": {"last_seq": bus.last_seq, "subscribers": bus.subscriber_count},
        }

    @app.get("/health")
    def health() -> dict[str, Any]:
        return health_payload()

    @app.get("/version")
    def version() -> dict[str, Any]:
        info = runtime_info()
        info["data_root"] = str(settings.resolved_data_dir())
        info["api_version"] = "v1"
        return info

    @app.get("/v1/events")
    def events(limit: int = BACKLOG_LIMIT) -> dict[str, Any]:
        limit = max(1, min(limit, 256))
        return {
            "events": [event.to_dict() for event in bus.recent(limit)],
            "last_seq": bus.last_seq,
        }

    async def _event_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = bus.subscribe()
        attached = bus.publish(
            "system.session.attached",
            {"subscribers": bus.subscriber_count, "backlog": len(bus.recent(BACKLOG_LIMIT))},
            method="measured",
        )
        try:
            # Replay the ring first so a fresh client sees what already
            # happened (notably system.daemon.started) instead of an empty
            # stream that looks like a hang.
            for event in bus.recent(BACKLOG_LIMIT):
                await websocket.send_json(event.to_dict())
            if not bus.recent(1):
                await websocket.send_json(attached.to_dict())
            async for event in bus.stream(queue):
                await websocket.send_json(event.to_dict())
        except WebSocketDisconnect:
            pass
        except RuntimeError:  # pragma: no cover - send after close
            pass
        finally:
            bus.unsubscribe(queue)

    app.add_api_websocket_route("/v1/events/ws", _event_stream)
    app.add_api_websocket_route("/events/ws", _event_stream)  # WP-002 gate alias

    web_root = settings.resolved_web_root()
    if web_root is not None:
        app.mount("/", StaticFiles(directory=str(web_root), html=True), name="web")

    return app

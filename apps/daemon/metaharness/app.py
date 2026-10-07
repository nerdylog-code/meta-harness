"""The daemon HTTP + WebSocket application.

Surface, deliberately minimal:

  GET  /health          liveness + identity + resolved layout + store state
  GET  /version         build/runtime identity
  GET  /v1/events       durable recent-event read (polling fallback)
  WS   /v1/events/ws    live event stream (canonical path, BOOK §64)
  WS   /events/ws       compatibility alias for the WP-002 gate wording
  GET  /                the built web bundle, when one exists

Storage is canonical: every emitted event is persisted by `metaharness.store` (ADR-0003)
before anyone is told about it, so `/v1/events` and the websocket backlog are the real
history rather than an in-memory ring, and a restart resumes from it.

Boot reconciliation runs once per launch, before the daemon announces itself: persisted
`running` runs whose process is gone become `orphaned` through a *new* event (BOOK §83).
The store never asks about processes; that decision lives in `metaharness.reconcile`.

Local-only by construction: the app rejects any request whose peer is not loopback. The
per-launch secret of BOOK §65 arrives with the UI session (WP-006) — until then, "not
reachable from off-host" is the enforced property, and `docs/architecture/ARCHITECTURE.md`
records that gap rather than implying an auth story the skeleton does not have.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Collection
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from . import paths
from .api import (
    agents_router,
    approvals_router,
    artifacts_router,
    board_router,
    canvas_router,
    tasks_router,
    workspaces_router,
)
from .events import EventBus
from .reconcile import BootReconciler, PidProbe
from .sandbox import ExecutionEnvironment
from .runtimes.hermes.adapter import HermesRuntimeAdapter
from .runtimes.pi.adapter import PiRuntimeAdapter
from .store import Store, default_db_path
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
    #: Argv for the Pi runtime. ``None`` means the real ``pi --mode rpc``; a test or a
    #: different installation can point elsewhere without the daemon hardcoding a binary.
    pi_argv: list[str] | None = None
    #: Argv for the Hermes runtime. ``None`` means the real ``hermes acp`` (ADR-0016).
    hermes_argv: list[str] | None = None
    #: Which sandbox provider to prefer: ``auto`` picks the strongest usable one (ADR-0019).
    sandbox: str = "auto"
    #: The container runtime to look for when ``auto`` reaches the container rung.
    container_runtime: str = "docker"

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
    started_at = time.time()

    def _on_adapter_event(event, transient: bool) -> None:
        """Persist before anyone hears about it; a delta is fanned out and never stored."""
        bus = getattr(app.state, "bus", None)
        if bus is None:  # pragma: no cover - only during shutdown
            return
        if transient:
            bus.publish_transient(event)
        else:
            bus.publish_event(event)
        # The budgets observe real events, not intentions (M3). A tool-call limit is counted here,
        # from the events the runtime actually produced, and a usage sample is what feeds the token
        # and cost budgets -- both of which are recorded as soft, because they arrive after the fact.
        trackers = getattr(app.state, "budget_trackers", None)
        if trackers and getattr(event, "session_id", None):
            tracker = trackers.get(event.session_id)
            if tracker is not None:
                kind = getattr(event, "kind", "")
                if kind == "tool.started":
                    tracker.observe_tool_call()
                elif kind == "usage.sampled":
                    sample = (getattr(event, "payload_body", None) or {}).get("sample")
                    if isinstance(sample, dict):
                        try:
                            from metaharness_contracts import UsageSample

                            tracker.observe_usage(UsageSample(**sample))
                        except Exception:
                            pass

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        data_root = paths.ensure_layout(settings.data_dir)
        app.state.data_root = data_root
        # The store is opened here, not at import: a module that writes to disk as a side
        # effect of being imported is a module nobody can test in isolation.
        store = Store(default_db_path(settings.data_dir), data_root=settings.data_dir)
        bus = EventBus(store)
        adapter = PiRuntimeAdapter(
            argv=settings.pi_argv,
            data_root_override=settings.data_dir,
            on_event=_on_adapter_event,
        )
        # One adapter per runtime, resolved by id. `state.adapter` stays as the default so
        # nothing that predates the second runtime has to change.
        hermes = HermesRuntimeAdapter(argv=settings.hermes_argv, on_event=_on_adapter_event)
        # The execution boundary (M3). One environment per daemon; each session asks it for a plan.
        environment = ExecutionEnvironment(
            prefer=settings.sandbox,
            container=settings.container_runtime,
            data_root=str(paths.ensure_layout(settings.data_dir)),
        )
        app.state.store = store
        app.state.bus = bus
        app.state.adapter = adapter
        app.state.adapters = {adapter.runtime_id: adapter, hermes.runtime_id: hermes}
        app.state.environment = environment
        #: Live budget trackers, one per session. Owned by the daemon, never by the runtime.
        app.state.budget_trackers = {}
        # Reconcile before announcing the daemon, so the first thing a client reads is an
        # honest picture (BOOK §83).
        reconcile = BootReconciler(store, PidProbe()).run()
        app.state.reconcile = reconcile
        bus.publish(
            "system.daemon.started",
            {
                "version": VERSION,
                "git_sha": git_sha(),
                "host": settings.host,
                "port": settings.port,
                "data_root": str(data_root),
                "schema_version": store.schema_version,
                "reconcile": reconcile.as_dict(),
            },
            method="measured",
        )
        try:
            yield
        finally:
            # Closing the canonical store must never be conditional on bookkeeping
            # succeeding. A shutdown that fails to release the database handle leaves the
            # file locked -- which Linux tolerates (unlinking an open file is allowed) and
            # Windows does not, so the bug showed up as a test-suite PermissionError there.
            try:
                bus.publish("system.daemon.stopping", {"reason": "shutdown"}, method="measured")
            except Exception as exc:  # pragma: no cover - shutdown must not fail on this
                app.state.shutdown_error = str(exc)
            finally:
                # Runtimes first: a Pi process must not outlive the daemon that owns it.
                try:
                    # Every adapter, not just the default one: a runtime that was used must be
                    # released, or its processes outlive the daemon.
                    for _adapter in getattr(app.state, 'adapters', {}).values():
                        await _adapter.close_all()
                except Exception as exc:  # pragma: no cover - already gone
                    app.state.shutdown_error = str(exc)
                store.close()

    app = FastAPI(
        title="Meta-Harness",
        version=VERSION,
        description="Local-first control plane for heterogeneous AI agents.",
        lifespan=lifespan,
    )
    app.state.bus = None
    app.state.store = None
    app.state.settings = settings
    app.state.started_at = started_at
    app.add_middleware(LoopbackOnlyMiddleware)

    def _bus(app_or_request: Any = None) -> EventBus:
        bus = getattr(app.state, "bus", None)
        if bus is None:  # pragma: no cover - only reachable outside the lifespan
            raise RuntimeError("the event plane is not running; the app must be used inside its lifespan")
        return bus

    def health_payload() -> dict[str, Any]:
        web_root = settings.resolved_web_root()
        store: Store | None = getattr(app.state, "store", None)
        reconcile = getattr(app.state, "reconcile", None)
        bus = getattr(app.state, "bus", None)
        payload: dict[str, Any] = {
            "status": "ok",
            "service": "meta-harness",
            "version": VERSION,
            "git_sha": git_sha(),
            "uptime_s": round(time.time() - started_at, 3),
            "data_root": str(settings.resolved_data_dir()),
            "web_bundle": str(web_root) if web_root else None,
            "events": {
                "last_seq": bus.last_seq if bus else 0,
                "subscribers": bus.subscriber_count if bus else 0,
            },
            "store": {
                "path": str(store.path) if store else None,
                "schema_version": store.schema_version if store else None,
                "events": store.count() if store else None,
                "journal_mode": store.verify().journal_mode if store else None,
            },
            "reconcile": reconcile.as_dict() if reconcile else None,
        }
        return payload

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
        bus = _bus()
        return {
            "events": [event.model_dump(mode="json") for event in bus.recent(limit)],
            "last_seq": bus.last_seq,
        }

    async def _await_disconnect(websocket: WebSocket) -> None:
        """Resolve when the client goes away.

        Without this, an idle connected client parks the handler on ``queue.get()``
        indefinitely: the task never finishes, uvicorn waits for it during shutdown, and a
        closed browser tab leaks a live coroutine. The websocket is the only place the
        disconnect is visible, so somebody has to be listening to it.
        """
        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
        except Exception:  # pragma: no cover - any receive failure means gone
            return

    async def _event_stream(websocket: WebSocket) -> None:
        await websocket.accept()
        bus = _bus()
        queue = bus.subscribe()
        attached = bus.publish(
            "system.session.attached",
            {"subscribers": bus.subscriber_count, "backlog": len(bus.recent(BACKLOG_LIMIT))},
            method="measured",
        )
        disconnected = asyncio.create_task(_await_disconnect(websocket))
        try:
            # Replay the durable backlog first so a fresh client sees what already
            # happened (notably system.daemon.started) instead of an empty stream that
            # looks like a hang -- and so a restarted daemon has history to show.
            for event in bus.recent(BACKLOG_LIMIT):
                await websocket.send_json(event.model_dump(mode="json"))
            if not bus.recent(1):
                await websocket.send_json(attached.model_dump(mode="json"))
            while True:
                pending = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait(
                    {pending, disconnected}, return_when=asyncio.FIRST_COMPLETED
                )
                if disconnected in done:
                    pending.cancel()
                    break
                await websocket.send_json(pending.result().model_dump(mode="json"))
        except WebSocketDisconnect:
            pass
        except RuntimeError:  # pragma: no cover - send after close
            pass
        finally:
            disconnected.cancel()
            bus.unsubscribe(queue)

    app.add_api_websocket_route("/v1/events/ws", _event_stream)
    app.add_api_websocket_route("/events/ws", _event_stream)  # WP-002 gate alias
    app.include_router(agents_router)
    app.include_router(tasks_router)
    app.include_router(approvals_router)
    app.include_router(artifacts_router)
    app.include_router(workspaces_router)
    app.include_router(board_router)
    app.include_router(canvas_router)

    web_root = settings.resolved_web_root()
    if web_root is not None:
        assets_dir = web_root / "assets"
        if assets_dir.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

        index_file = web_root / "index.html"
        resolved_root = web_root.resolve()

        @app.get("/{path:path}")
        async def spa_fallback(path: str):  # noqa: ANN202 - FastAPI route
            """Serve the client-side shell for router paths.

            `StaticFiles(html=True)` answers 404 for an unknown path, which breaks every deep
            link the router creates (`/events`, `/agents`, ...). Real files still win; anything
            else that is not an API path gets the shell. API paths keep returning 404 rather
            than an HTML page, so a wrong URL is never mistaken for a working endpoint.
            """
            if path.startswith("v1/") or path in {"health", "version"}:
                return JSONResponse({"error": "not found", "path": path}, status_code=404)
            candidate = (resolved_root / path).resolve()
            try:
                candidate.relative_to(resolved_root)
            except ValueError:
                return JSONResponse({"error": "forbidden", "path": path}, status_code=403)
            if path and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(index_file)

    return app

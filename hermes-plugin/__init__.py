"""Meta-Harness — Python backend plugin entry point.

This file is loaded by Hermes's ``agent_plugins`` discovery
(``~/.hermes/plugins/meta-harness/__init__.py``). It must export a
``register(ctx)`` function. The hook implementations are intentionally thin:
the bulk of the runtime lives under ``hermes_plugin/`` and is installed next
to this file.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
from pathlib import Path

logger = logging.getLogger("meta-harness")

_PLUGIN_ROOT = Path(__file__).resolve().parent
_RUNTIME_PKG = _PLUGIN_ROOT / "hermes_plugin"

if str(_RUNTIME_PKG) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_PKG))

# Lock so concurrent host hooks don't open the SQLite store twice from
# different threads. The runtime itself is also thread-safe (sqlite3 with
# check_same_thread=False, plus our own lock on writes).
_STATE_LOCK = threading.Lock()
_INITIALIZED = False


def _data_dir() -> Path:
    """Resolve the plugin data directory.

    Order:
      1. config ``data_dir`` (operator override)
      2. ``HERMES_HOME`` env var
      3. ``Path.home() / '.hermes'`` fallback
    """
    try:
        from hermes_constants import get_hermes_home
        home = get_hermes_home()
    except Exception:
        env = os.environ.get("HERMES_HOME", "").strip()
        home = Path(env) if env else Path.home() / ".hermes"

    return home / "meta-harness"


def _truthy(value, default: bool = True) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def register(ctx) -> None:
    """Hermes plugin entry point.

    `ctx` is the host ``PluginContext``. We use only the documented API:

      * ctx.get_config(key, default) -> read plugin.yaml config_schema values
      * ctx.register_hook(name, fn)  -> install a hook
      * ctx.register_tool(...)       -> add a model-facing tool

    We do NOT touch any other attribute of the context; if a host attribute
    is missing, the harness degrades gracefully rather than crashing the
    loader.
    """
    global _INITIALIZED

    with _STATE_LOCK:
        if _INITIALIZED:
            logger.debug("meta-harness.register() called twice — skipping")
            return
        _INITIALIZED = True

    try:
        data_dir = _data_dir()
        if _truthy(ctx.get_config("enable_desktop_routes", "true"), True):
            data_dir.mkdir(parents=True, exist_ok=True)

        # Late import — these modules read config and open the event store
        # on first use. We import only after data_dir is known.
        from hermes_plugin import (
            runtime as _runtime,  # noqa: F401 — side-effect import
            api as _api,
        )

        _runtime.bootstrap(data_dir=data_dir, ctx=ctx)

        if _truthy(ctx.get_config("enable_desktop_routes", "true"), True):
            _api.register_router(data_dir=data_dir)

        # Hooks — only if the host provides them. We never assume.
        try:
            ctx.register_hook("pre_tool_call", _runtime.hook_pre_tool_call)
            ctx.register_hook("post_tool_call", _runtime.hook_post_tool_call)
            ctx.register_hook("pre_api_request", _runtime.hook_pre_api_request)
            ctx.register_hook("post_api_request", _runtime.hook_post_api_request)
            ctx.register_hook("on_session_start", _runtime.hook_session_start)
            ctx.register_hook("on_session_end", _runtime.hook_session_end)
        except Exception as exc:  # pragma: no cover — host varies
            logger.info("meta-harness: hook registration skipped: %s", exc)

        # Model-facing tools. Hermes invokes these from the LLM as
        # ``meta_harness_<name>``. We expose a small, deliberate surface.
        try:
            ctx.register_tool(
                name="harness_inspect",
                description=(
                    "Inspect the Meta-Harness runtime: capabilities, engines, "
                    "topologies, active runs. Returns a compact summary plus "
                    "references for follow-up queries."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "enum": ["capabilities", "engines", "topologies",
                                     "runs", "plugins", "characters"],
                            "description": "What to inspect."
                        },
                        "id": {
                            "type": "string",
                            "description": "Optional id (run id, plugin id, ...)."
                        }
                    },
                    "required": [],
                    "additionalProperties": False,
                },
                handler=_runtime.tool_harness_inspect,
            )
            ctx.register_tool(
                name="harness_run",
                description=(
                    "Start a Meta-Harness run with the given topology and "
                    "task. Returns the new run id. Use harness_status to "
                    "poll progress; events stream to /api/plugins/meta-harness/runs/{id}/events."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "description": "Task description."},
                        "topology": {"type": "string", "description": "Topology id, e.g. 'solo', 'architect-builder'."},
                        "role": {"type": "string", "description": "Optional role id override for the entry agent."},
                        "engine": {"type": "string", "enum": ["hermes", "pi"], "description": "Engine hint."},
                        "model": {"type": "string", "description": "Optional model id override."},
                        "context": {
                            "type": "object",
                            "description": "Arbitrary extra context passed to the topology.",
                            "additionalProperties": True,
                        },
                    },
                    "required": ["task"],
                    "additionalProperties": False,
                },
                handler=_runtime.tool_harness_run,
            )
            ctx.register_tool(
                name="harness_status",
                description=(
                    "Get the current state of a run. Returns compact summary; "
                    "durable events stay on disk."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string", "description": "Run id."},
                    },
                    "required": ["run_id"],
                    "additionalProperties": False,
                },
                handler=_runtime.tool_harness_status,
            )
            ctx.register_tool(
                name="harness_cancel",
                description=(
                    "Cancel a run. Recursively cancels owned subagents, "
                    "Pi children, validation loops, and timers."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "run_id": {"type": "string", "description": "Run id."},
                    },
                    "required": ["run_id"],
                    "additionalProperties": False,
                },
                handler=_runtime.tool_harness_cancel,
            )
            ctx.register_tool(
                name="harness_capabilities",
                description=(
                    "List currently registered capabilities and which providers "
                    "fulfil them."
                ),
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                handler=_runtime.tool_harness_capabilities,
            )
            ctx.register_tool(
                name="harness_plugins",
                description=(
                    "List Meta-Harness internal plugins and their state."
                ),
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                handler=_runtime.tool_harness_plugins,
            )
            ctx.register_tool(
                name="harness_topology_list",
                description=(
                    "List available topologies (built-in + user). Use "
                    "harness_inspect topic=topologies id=<id> to read one."
                ),
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
                handler=_runtime.tool_harness_topology_list,
            )
            ctx.register_tool(
                name="harness_artifact_get",
                description=(
                    "Return the contents (or a path reference) of an artifact "
                    "produced by a run."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "artifact_id": {"type": "string"},
                    },
                    "required": ["artifact_id"],
                    "additionalProperties": False,
                },
                handler=_runtime.tool_harness_artifact_get,
            )
            ctx.register_tool(
                name="harness_plugin_lab",
                description=(
                    "Operate the Plugin Lab: inspect, create version, validate, "
                    "experimentally activate, rollback. Action-specific params."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["inspect", "create", "validate",
                                     "activate", "rollback", "list"],
                        },
                        "plugin_id": {"type": "string"},
                        "version": {"type": "string", "description": "Optional target version, e.g. v0003."},
                        "manifest": {"type": "object", "description": "Plugin manifest for action=create."},
                        "code": {"type": "string", "description": "Inline plugin source for action=create."},
                    },
                    "required": ["action"],
                    "additionalProperties": False,
                },
                handler=_runtime.tool_harness_plugin_lab,
            )
        except Exception as exc:  # pragma: no cover — host varies
            logger.info("meta-harness: tool registration skipped: %s", exc)

        logger.info("meta-harness: backend plugin registered (data_dir=%s)", data_dir)

    except Exception as exc:  # pragma: no cover — never crash the loader
        # A plugin import failure must not bring the host down.
        logger.exception("meta-harness.register() failed: %s", exc)
"""Meta-Harness dashboard backend — re-exports the FastAPI router from the
runtime package.

The dashboard plugin system mounts this module's ``router`` at
``/api/plugins/meta-harness/`` automatically (see the kanban plugin for
the canonical example).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

logger = logging.getLogger("meta-harness.dashboard")

# Make the runtime package importable regardless of how the dashboard
# imports us. The plugin root is ``hermes-plugin/``; the runtime package
# is ``hermes-plugin/hermes_plugin/``.
_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_RUNTIME_PKG = _PLUGIN_ROOT / "hermes_plugin"

if str(_PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_ROOT))

try:
    from hermes_plugin.api import register_router as _register, router
    from hermes_plugin.paths import data_dir as _data_dir
except Exception as exc:  # pragma: no cover — surface on import
    logger.exception("meta-harness dashboard import failed: %s", exc)
    router = None  # type: ignore
else:
    _register(_data_dir())

__all__ = ["router"]
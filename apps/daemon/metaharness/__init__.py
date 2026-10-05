"""Meta-Harness v2 — local-first control plane for heterogeneous AI agents.

This package is the daemon (`apps/daemon/metaharness`). It is deliberately
small: the skeleton owns process lifetime, the HTTP surface and the event
plane, and nothing else. Domain contracts arrive with `packages/contracts`
(WP-003), storage with WP-004, process supervision with WP-005.
"""

from __future__ import annotations

__all__ = ["__version__", "create_app"]

from .version import __version__


def create_app(*args, **kwargs):  # pragma: no cover - thin re-export
    """Late import so `import metaharness` never pulls FastAPI needlessly."""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)

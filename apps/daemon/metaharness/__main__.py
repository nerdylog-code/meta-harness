"""``python -m metaharness`` — run the daemon without the dev script.

Kept intentionally thin: `scripts/dev.py` is the canonical developer entry
point (BOOK Appendix C); this exists so the package is runnable on its own.
"""

from __future__ import annotations

import os

from .app import DEFAULT_PORT, Settings, create_app


def _argv_env(name: str) -> list[str] | None:
    """A space-separated command from the environment, or None to keep the adapter's default."""
    value = os.environ.get(name)
    return value.split() if value else None


def main() -> int:
    import uvicorn

    settings = Settings(
        host=os.environ.get("METAHARNESS_HOST", "127.0.0.1"),
        port=int(os.environ.get("METAHARNESS_PORT", DEFAULT_PORT)),
        data_dir=os.environ.get("METAHARNESS_DATA_DIR") or None,
        # Which binary launches a runtime is configuration, not a PATH accident. On a machine with
        # version-manager shims the first `hermes` on the daemon's PATH can be a shim that needs its
        # manager's environment -- and inside a sandbox that environment is deliberately absent.
        # Space-separated, so `METAHARNESS_HERMES_ARGV="/path/to/hermes acp"` pins it exactly.
        hermes_argv=_argv_env("METAHARNESS_HERMES_ARGV"),
        pi_argv=_argv_env("METAHARNESS_PI_ARGV"),
    )
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

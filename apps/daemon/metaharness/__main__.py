"""``python -m metaharness`` — run the daemon without the dev script.

Kept intentionally thin: `scripts/dev.py` is the canonical developer entry
point (BOOK Appendix C); this exists so the package is runnable on its own.
"""

from __future__ import annotations

import os

from .app import DEFAULT_PORT, Settings, create_app


def main() -> int:
    import uvicorn

    settings = Settings(
        host=os.environ.get("METAHARNESS_HOST", "127.0.0.1"),
        port=int(os.environ.get("METAHARNESS_PORT", DEFAULT_PORT)),
        data_dir=os.environ.get("METAHARNESS_DATA_DIR") or None,
    )
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

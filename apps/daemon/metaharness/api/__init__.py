"""The daemon's HTTP API, split by concern.

One module per surface, each returning an `APIRouter` that reads the store, bus and adapter
from `request.app.state`. Nothing here holds state of its own: the daemon is the system of
record and the API is a view onto it.
"""

from __future__ import annotations

from .agents import router as agents_router
from .tasks import router as tasks_router

__all__ = ["agents_router", "tasks_router"]

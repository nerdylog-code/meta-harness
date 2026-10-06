"""Runtimes: one package per adapter, plus a registry the daemon resolves ids through.

Nothing in this package knows about the store, the API or the UI: an adapter turns a
RuntimeAdapter call into a runtime's protocol and turns that protocol's records into parsed
events. Persisting them is somebody else's job (the bus), which is what keeps "the store
never knows what a process is" true in both directions.
"""

from __future__ import annotations

__all__: list[str] = []

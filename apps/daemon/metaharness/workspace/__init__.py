"""Workspace providers: isolated working copies for tasks (BOOK §26)."""

from __future__ import annotations

from .provider import (
    Availability,
    GitWorktreeProvider,
    WorkspaceError,
    WorkspaceProvider,
    WorktreeEntry,
)

__all__ = [
    "Availability",
    "GitWorktreeProvider",
    "WorkspaceError",
    "WorkspaceProvider",
    "WorktreeEntry",
]

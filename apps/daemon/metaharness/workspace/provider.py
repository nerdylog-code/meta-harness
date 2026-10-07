"""The workspace provider: where a task's isolated working copy comes from.

A worktree is **concurrency and write isolation**, not a security boundary (BOOK §26). This module
never claims otherwise, and the vocabulary it reports is `moderate` for write isolation and nothing
at all for what the runtime can read -- that dimension belongs to the sandbox (S1) and is measured
there.

Everything here runs Git as **argv**, never through a shell: no `shell=True`, no command strings, no
bash dependency. That is what makes the same code work on Windows and POSIX, and it is also what
keeps a path with a space in it from becoming two arguments.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from metaharness_contracts import RepositoryIdentity


class WorkspaceError(RuntimeError):
    """A workspace operation refused. The API turns this into a 4xx, never a 500."""


@dataclass(frozen=True)
class Availability:
    available: bool
    detail: str


@dataclass(frozen=True)
class WorktreeEntry:
    """One row of `git worktree list --porcelain`."""

    path: str
    head: str | None = None
    branch: str | None = None
    detached: bool = False
    bare: bool = False


class WorkspaceProvider(Protocol):
    """What the control plane needs from a workspace implementation."""

    name: str

    def probe(self) -> Availability: ...

    def validate_repository(self, path: str) -> RepositoryIdentity: ...

    def resolve_base(self, root: str, base_ref: str) -> str: ...

    def allocate(self, *, root: str, branch: str, base_commit: str, path: str) -> None: ...

    def worktrees(self, root: str) -> list[WorktreeEntry]: ...

    def status(self, path: str) -> tuple[bool, str]: ...

    def remove(self, *, root: str, path: str) -> None: ...


class GitWorktreeProvider:
    name = "git-worktree"

    def __init__(self, *, timeout_s: float = 120.0) -> None:
        self.timeout_s = timeout_s

    # ------------------------------------------------------------------ process

    def _git(self, *args: str, cwd: str | None = None) -> tuple[int, str, str]:
        """Run git with an argument vector. No shell, no string commands, bounded time."""
        argv = ["git", *args]
        try:
            completed = subprocess.run(  # noqa: S603 - argv, never a shell string
                argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except FileNotFoundError as exc:
            raise WorkspaceError("git is not installed or not on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise WorkspaceError(f"git {' '.join(args[:2])} timed out after {self.timeout_s}s") from exc
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()

    def _require(self, *args: str, cwd: str | None = None) -> str:
        code, out, err = self._git(*args, cwd=cwd)
        if code != 0:
            raise WorkspaceError(f"git {args[0] if args else ''} failed: {err or out or f'exit {code}'}")
        return out

    # ------------------------------------------------------------------ probing

    def probe(self) -> Availability:
        if shutil.which("git") is None:
            return Availability(False, "git is not installed or not on PATH")
        code, out, err = self._git("--version")
        if code != 0:
            return Availability(False, err or "git --version failed")
        return Availability(True, out)

    def validate_repository(self, path: str) -> RepositoryIdentity:
        """Ask Git what this path is. A caller saying "it is a repository" is not evidence."""
        candidate = Path(path).expanduser()
        if not candidate.exists():
            raise WorkspaceError(f"{path} does not exist")
        code, out, err = self._git("-C", str(candidate), "rev-parse", "--show-toplevel")
        if code != 0 or not out:
            raise WorkspaceError(f"{path} is not a git repository: {err or 'no toplevel'}")
        root = Path(out).resolve()
        common = self._require("-C", str(root), "rev-parse", "--git-common-dir")
        common_path = Path(common)
        common_dir = (common_path if common_path.is_absolute() else root / common_path).resolve()
        _, head, _ = self._git("-C", str(root), "rev-parse", "HEAD")
        return RepositoryIdentity(root=str(root), common_dir=str(common_dir), head=head or None)

    def resolve_base(self, root: str, base_ref: str) -> str:
        return self._require("-C", root, "rev-parse", "--verify", f"{base_ref}^{{commit}}")

    def branch_exists(self, root: str, branch: str) -> bool:
        code, out, _ = self._git("-C", root, "branch", "--list", "--format=%(refname:short)", branch)
        return code == 0 and out.strip() == branch

    def worktrees(self, root: str) -> list[WorktreeEntry]:
        out = self._require("-C", root, "worktree", "list", "--porcelain")
        entries: list[WorktreeEntry] = []
        current: dict[str, str | bool] = {}
        for line in [*out.splitlines(), ""]:
            if not line.strip():
                path_value = current.get("path")
                if isinstance(path_value, str) and path_value:
                    head = current.get("head")
                    branch = current.get("branch")
                    entries.append(
                        WorktreeEntry(
                            path=path_value,
                            head=head if isinstance(head, str) else None,
                            branch=branch if isinstance(branch, str) else None,
                            detached=bool(current.get("detached")),
                            bare=bool(current.get("bare")),
                        )
                    )
                current = {}
                continue
            key, _, value = line.partition(" ")
            if key == "worktree":
                current["path"] = value
            elif key == "HEAD":
                current["head"] = value
            elif key == "branch":
                current["branch"] = value.replace("refs/heads/", "", 1)
            elif key == "detached":
                current["detached"] = True
            elif key == "bare":
                current["bare"] = True
        return entries

    def status(self, path: str) -> tuple[bool, str]:
        """Measured dirtiness. `git status --porcelain` is the answer; a guess is not."""
        code, out, err = self._git("-C", path, "status", "--porcelain")
        if code != 0:
            raise WorkspaceError(f"cannot read the status of {path}: {err or out}")
        return bool(out.strip()), out

    # ------------------------------------------------------------------ mutation

    def allocate(self, *, root: str, branch: str, base_commit: str, path: str) -> None:
        """Create the branch and the worktree. The caller has already checked the destination."""
        destination = Path(path)
        if destination.exists():
            # Refuse rather than adopt: whatever is there was not created by this allocation.
            raise WorkspaceError(f"{path} already exists; refusing to reuse it")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if self.branch_exists(root, branch):
            # A branch without a worktree is a leftover from a partial attempt: attach, do not
            # recreate, so history is not rewritten.
            self._require("-C", root, "worktree", "add", str(destination), branch)
        else:
            self._require("-C", root, "worktree", "add", "-b", branch, str(destination), base_commit)

    def remove(self, *, root: str, path: str) -> None:
        """Remove a worktree. Never `--force`: an unclean worktree is a refusal, not a cleanup."""
        self._require("-C", root, "worktree", "remove", path)

    def prune(self, root: str) -> str:
        return self._require("-C", root, "worktree", "prune")

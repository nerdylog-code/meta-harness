"""Artifact externalization: the bytes live on disk, the index lives in SQLite.

BOOK 9 and WP-004 constraint 5 are unambiguous -- a blob column is forbidden -- so this
module is the only path by which a payload enters the system, and it never puts the
payload in the database.

Storage is **content-addressed**: ``<artifacts>/ab/abcdef...`` where the two-character
prefix and the filename are both derived from the sha256. Consequences worth stating:

* Writing the same bytes twice costs nothing and produces one file.
* The file's name *is* its integrity check, so a corrupted artifact is detectable
  without a database round-trip.
* A crash between the file write and the event append leaves an unreferenced file, never
  a row pointing at a missing file. Deriving the truth from the log is what makes that
  the harmless direction.

Writes are atomic: the payload goes to a temporary name in the same directory and is
then ``os.replace``d, so a reader never observes a half-written artifact.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from metaharness_contracts import IdKind, new_id

from ..paths import subdir
from .errors import StoreError

CHUNK = 1024 * 1024


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    path: str
    sha256: str
    mime: str
    size: int
    metadata: dict[str, Any]
    origin: dict[str, Any]


def artifacts_dir(root: str | os.PathLike | None = None) -> Path:
    return Path(subdir("artifacts", root))


def relative_path(sha256: str) -> Path:
    """``ab/abcdef...`` -- the layout under the artifacts directory."""
    return Path(sha256[:2]) / sha256


def path_for(sha256: str, root: str | os.PathLike | None = None) -> Path:
    return artifacts_dir(root) / relative_path(sha256)


def hash_file(path: str | os.PathLike) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(CHUNK)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def store_bytes(
    data: bytes,
    *,
    mime: str = "application/octet-stream",
    root: str | os.PathLike | None = None,
) -> tuple[str, Path, int]:
    """Write ``data`` under its content address; return ``(sha256, path, size)``."""
    sha = hashlib.sha256(data).hexdigest()
    target = path_for(sha, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != len(data):
        handle = tempfile.NamedTemporaryFile(
            dir=target.parent, prefix=".tmp-", delete=False
        )
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, target)
    return sha, target, len(data)


def store_file(
    source: str | os.PathLike,
    *,
    mime: str = "application/octet-stream",
    root: str | os.PathLike | None = None,
) -> tuple[str, Path, int]:
    """Copy ``source`` into the artifact store by content address."""
    source_path = Path(source)
    if not source_path.is_file():
        raise StoreError(f"artifact source is not a file: {source_path}")
    sha = hash_file(source_path)
    target = path_for(sha, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != source_path.stat().st_size:
        handle = tempfile.NamedTemporaryFile(dir=target.parent, prefix=".tmp-", delete=False)
        try:
            with open(source_path, "rb") as reader:
                while True:
                    chunk = reader.read(CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, target)
    return sha, target, source_path.stat().st_size


def new_artifact_id() -> str:
    return new_id(IdKind.ARTIFACT)


def describe(
    *,
    artifact_id: str,
    path: Path,
    sha256: str,
    mime: str,
    size: int,
    metadata: dict[str, Any] | None,
    origin: dict[str, Any] | None,
) -> dict[str, Any]:
    """The payload body of ``artifact.created`` (version marker added by the caller)."""
    return {
        "artifact_id": artifact_id,
        "path": str(path),
        "sha256": sha256,
        "mime": mime,
        "size": size,
        "metadata": metadata or {},
        "origin": origin or {},
    }


def metadata_json(metadata: dict[str, Any] | None) -> str:
    return json.dumps(metadata or {}, sort_keys=True, ensure_ascii=False)

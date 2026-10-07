"""One Artifact API: the durable evidence, inspectable.

Every surface that shows an artifact -- a task's proof, an approval's payload, a canvas node later --
reads it from here. There is one canonical record (the `artifacts` projection, written only by
`artifact.created`) and this module is a projection of it, never a second store.

What makes this an inspector rather than a file server:

* **integrity is verified on read**, by recomputing the sha256 of the bytes on disk and comparing it
  with the recorded one. A mismatch is reported as `mismatch` with both hashes, never smoothed over,
  and a file that is gone is `missing` rather than an empty success -- missing bytes are not a valid
  artifact;
* **the host path never leaves the daemon.** The view carries a *locator* relative to the data root
  (or just the content-addressed name), so an artifact id cannot be used to probe the filesystem;
* **the preview follows the MIME, and refuses to guess.** Text, JSON and markdown get a bounded
  preview; images are served as bytes for the browser to render; anything else is `binary` with a
  reason and a download, because inventing a preview for an unknown type is how a UI lies;
* **previews and ranged reads are bounded**, so a large artifact is never injected whole into a UI or
  a model context: the whole file is verified, the range is what gets transferred.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response

router = APIRouter(tags=["artifacts"])

#: How much text a preview may return by default and at most. The number is a bound on what a UI or
#: a model sees, not on what can be downloaded.
PREVIEW_LIMIT = 64 * 1024
PREVIEW_MAX = 1024 * 1024

TEXT_MIMES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "text/x-python",
    "application/x-python",
    "text/x-shellscript",
    "application/x-sh",
    "text/x-yaml",
    "application/yaml",
    "text/x-toml",
    "text/x-rust",
    "text/x-c",
    "text/html",
    "text/css",
    "application/javascript",
    "text/javascript",
    "text/x-typescript",
}
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}
JSON_MIMES = {"application/json", "application/x-ndjson"}


def preview_kind(mime: str) -> str:
    """What the UI may render for this type. `binary` is an answer, not a failure."""
    if mime in JSON_MIMES:
        return "json"
    if mime in TEXT_MIMES or mime.startswith("text/"):
        return "text"
    if mime in IMAGE_MIMES:
        return "image"
    return "binary"


def _locator(record, data_root: Path) -> str:
    """A storage locator, never an absolute host path (BOOK: no host paths in the wire)."""
    path = Path(record.path)
    try:
        return str(path.relative_to(data_root))
    except ValueError:
        return path.name


def _integrity(store, record) -> tuple[str, str | None]:
    """Recompute the content address. Returns (status, detail) -- never raises for a bad artifact."""
    path = Path(record.path)
    if not path.is_file():
        return "missing", f"the bytes are indexed but not on disk: {path.name}"
    try:
        store.artifact_bytes(record.id)
    except Exception as exc:  # a mismatch is a fact to report, not an exception to hide
        return "mismatch", str(exc)
    return "ok", None


def _row(store, artifact_id: str) -> dict[str, Any]:
    rows = store.rows("SELECT * FROM artifacts WHERE id = ?", (artifact_id,))
    if not rows:
        raise HTTPException(status_code=404, detail=f"no such artifact: {artifact_id}")
    return rows[0]


def _origin(store, artifact_id: str) -> dict[str, Any]:
    """The envelope ids the artifact was created with: who produced it, for what work."""
    rows = store.rows(
        "SELECT mission_id, task_id, run_id, agent_id FROM events WHERE kind = 'artifact.created' "
        "AND json_extract(payload, '$.artifact_id') = ? ORDER BY seq LIMIT 1",
        (artifact_id,),
    )
    if rows:
        return {key: rows[0][key] for key in ("mission_id", "task_id", "run_id", "agent_id") if rows[0][key]}
    return {}


def _view(store, record, data_root: Path, *, verify: bool) -> dict[str, Any]:
    status, detail = _integrity(store, record) if verify else ("unchecked", None)
    kind = preview_kind(record.mime)
    return {
        "id": record.id,
        "sha256": record.sha256,
        "size": record.size,
        "mime": record.mime,
        "locator": _locator(record, data_root),
        "preview_kind": kind,
        "preview_available": kind != "binary",
        "preview_reason": None if kind != "binary" else f"no preview is rendered for {record.mime}",
        "integrity": status,
        "integrity_detail": detail,
        "verified": verify,
        "origin": _origin(store, record.id),
        "metadata": record.metadata,
        "provenance": record.origin,
    }


# --------------------------------------------------------------------------------- endpoints


@router.get("/v1/artifacts")
def list_artifacts(
    request: Request,
    mission_id: str | None = None,
    task_id: str | None = None,
    run_id: str | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    """Every artifact, filterable by what produced it. Integrity is *not* verified for the list.

    Verifying every artifact on every list request would read every byte on every poll. The list
    says `unchecked` for that reason, and the detail endpoint is where the claim is made -- a status
    that says "unchecked" is honest; one that says "ok" without reading is not.
    """
    store = request.app.state.store
    data_root = request.app.state.data_root
    records = store.artifacts()
    views = [_view(store, record, data_root, verify=False) for record in records]
    if any([mission_id, task_id, run_id, agent_id]):
        wanted = {"mission_id": mission_id, "task_id": task_id, "run_id": run_id, "agent_id": agent_id}
        views = [
            view
            for view in views
            if all(view["origin"].get(key) == value for key, value in wanted.items() if value)
        ]
    return {"artifacts": views, "count": len(views)}


@router.get("/v1/artifacts/{artifact_id}")
def get_artifact(artifact_id: str, request: Request) -> dict[str, Any]:
    """The full view, with the content address recomputed. This is where integrity is claimed."""
    store = request.app.state.store
    row = _row(store, artifact_id)
    record = store.artifact(artifact_id)
    if record is None:  # pragma: no cover - the row and the record come from the same table
        raise HTTPException(status_code=404, detail=f"no such artifact: {artifact_id}")
    view = _view(store, record, request.app.state.data_root, verify=True)
    view["created_at"] = row["created_ts"]
    view["seq"] = row["seq"]
    return view


@router.get("/v1/artifacts/{artifact_id}/preview")
def preview_artifact(
    artifact_id: str,
    request: Request,
    start: int = 0,
    limit: int = PREVIEW_LIMIT,
) -> dict[str, Any]:
    """A bounded view of the content, decided by the MIME. Binary is refused, not guessed at."""
    store = request.app.state.store
    record = store.artifact(artifact_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no such artifact: {artifact_id}")
    if start < 0:
        raise HTTPException(status_code=422, detail="start must not be negative")
    limit = max(1, min(limit, PREVIEW_MAX))
    kind = preview_kind(record.mime)
    envelope: dict[str, Any] = {
        "id": record.id,
        "mime": record.mime,
        "kind": kind,
        "size": record.size,
        "start": start,
        "limit": limit,
        "truncated": False,
        "text": None,
        "reason": None,
    }
    if kind == "binary":
        envelope["reason"] = f"no preview is rendered for {record.mime}; download the artifact instead"
        return envelope
    try:
        data = store.artifact_bytes(record.id)
    except Exception as exc:
        envelope["reason"] = f"integrity failure: {exc}"
        envelope["integrity"] = "mismatch"
        return envelope
    if kind == "image":
        envelope["reason"] = "images are served as bytes from the content endpoint"
        envelope["content_url"] = f"/v1/artifacts/{record.id}/content"
        return envelope
    window = data[start : start + limit]
    text = window.decode("utf-8", errors="replace")
    if kind == "json":
        try:
            text = json.dumps(json.loads(text), indent=2, ensure_ascii=False)[:limit]
        except ValueError:
            envelope["reason"] = "declared as JSON but does not parse; showing the raw text"
    envelope["text"] = text
    envelope["truncated"] = start + limit < len(data)
    envelope["bytes_read"] = len(window)
    return envelope


@router.get("/v1/artifacts/{artifact_id}/content")
def content_artifact(
    artifact_id: str,
    request: Request,
    start: int = 0,
    end: int | None = None,
) -> Response:
    """The bytes. The whole file is verified; the range is what gets transferred."""
    store = request.app.state.store
    record = store.artifact(artifact_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"no such artifact: {artifact_id}")
    if start < 0 or (end is not None and end < start):
        raise HTTPException(status_code=422, detail="invalid range")
    try:
        data = store.artifact_bytes(record.id)
    except Exception as exc:
        raise HTTPException(
            status_code=409, detail=f"artifact {artifact_id} failed its integrity check: {exc}"
        ) from exc
    window = data[start : end if end is not None else len(data)]
    headers = {"content-length": str(len(window))}
    if end is not None or start:
        headers["content-range"] = f"bytes {start}-{start + len(window) - 1}/{len(data)}"
    return Response(content=window, media_type=record.mime, headers=headers)

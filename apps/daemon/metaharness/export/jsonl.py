"""The JSONL exporter: derived, one-way, and never an authority.

ADR-0003 makes JSONL an *artifact* of the canonical store, not a second truth. The rules
this module follows are the ones that keep that true:

* Export reads the log and writes a file. It never writes to the store.
* Import is a **tool**, used to rebuild a store from a package -- it goes through the
  same ``append`` path as everything else, so projections and idempotency behave
  identically and a re-import cannot double-apply.
* Deleting every ``.jsonl`` file changes nothing: there is one test for that (A8), and a
  source-level test that no module outside this package reads a ``.jsonl`` as authority.
* One line per event, canonical key order, so two exports of the same state are
  byte-identical and a diff is meaningful.

The format is deliberately the envelope itself -- not a private shape -- so a package is
readable by anything that understands the contract.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from metaharness_contracts import CanonicalEvent

from ..projections import canonical_json
from ..store.errors import StoreError
from ..store.store import Store

EXPORT_FORMAT = "metaharness.events.v1"


@dataclass(frozen=True)
class ExportReport:
    path: str
    events: int
    bytes_written: int
    sha256: str
    format: str = EXPORT_FORMAT


@dataclass(frozen=True)
class ImportReport:
    inserted: int
    duplicates: int
    source_sha256: str


def export_jsonl(
    store: Store,
    path: str | Path,
    *,
    include_header: bool = True,
    batch: int = 1000,
) -> ExportReport:
    """Write the whole log to ``path`` as JSONL (streamed, one event per line)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    events = 0
    written = 0

    with open(target, "wb") as handle:
        if include_header:
            header = canonical_json(
                {
                    "format": EXPORT_FORMAT,
                    "schema_version": store.schema_version,
                    "events": store.count(),
                    "first_seq": 1 if store.count() else 0,
                    "last_seq": store.latest_seq(),
                }
            )
            line = (header + "\n").encode("utf-8")
            handle.write(line)
            digest.update(line)
            written += len(line)

        for event in store.iter_events(batch=batch):
            line = (
                canonical_json(event.model_dump(mode="json")) + "\n"
            ).encode("utf-8")
            handle.write(line)
            digest.update(line)
            written += len(line)
            events += 1

    return ExportReport(
        path=str(target), events=events, bytes_written=written, sha256=digest.hexdigest()
    )


def read_jsonl(path: str | Path) -> Iterator[CanonicalEvent]:
    """Yield the events of an export, ignoring the header line."""
    source = Path(path)
    if not source.is_file():
        raise StoreError(f"export not found: {source}")
    with open(source, encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise StoreError(f"{source}: line {number} is not valid JSON: {exc}") from exc
            if payload.get("format") == EXPORT_FORMAT and "id" not in payload:
                continue  # header
            yield CanonicalEvent(**payload)


def import_jsonl(store: Store, path: str | Path) -> ImportReport:
    """Append an export into ``store`` through the normal append path."""
    inserted = 0
    duplicates = 0
    for event in read_jsonl(path):
        result = store.append(event)
        if result.inserted:
            inserted += 1
        else:
            duplicates += 1
    return ImportReport(
        inserted=inserted, duplicates=duplicates, source_sha256=file_sha256(path)
    )


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_events(events: Iterable[CanonicalEvent], path: str | Path) -> int:
    """Write an explicit list of events (used by tests and by tooling)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(target, "w", encoding="utf-8") as handle:
        for event in events:
            handle.write(canonical_json(event.model_dump(mode="json")) + "\n")
            count += 1
    return count
